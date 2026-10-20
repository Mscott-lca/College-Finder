#!/usr/bin/env python3
"""
Build data/colleges-data.js for the College Finder.

The build tries these sources in order and uses the first one that works:
  1. IPEDS files you uploaded to the repo (HD, IC and IC_AY; .zip or unzipped .csv,
     in any folder, any capitalization; "HD2024 (1).zip" is fine too).
  2. IPEDS files downloaded from nces.ed.gov.
  3. The Urban Institute Education Data API (a free mirror of IPEDS; no key needed).
  4. The College Scorecard API (needs a SCORECARD_API_KEY repository secret).
Then data/tn-overlay.json (hand-checked Tennessee details) is merged on top.

Every run writes a plain-English report. On GitHub it appears on the workflow run's
Summary page under "Data build report".

Usage:
  python scripts/build_data.py            # national build
  python scripts/build_data.py --tn-only  # Tennessee details only (no downloads)

Standard library only. Exit code 2 means no national source worked.
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / "data" / "tn-overlay.json"
OUT = ROOT / "data" / "colleges-data.js"
IPEDS_URL = "https://nces.ed.gov/ipeds/datacenter/data/{name}.zip"
URBAN_URL = "https://educationdata.urban.org/api/v1/college-university/ipeds/directory/{year}/"
SCORECARD_URL = "https://api.data.gov/ed/collegescorecard/v1/schools"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
OVERLAY_SECTOR = {"public": 1, "cc": 4, "tcat": 7, "private": 2, "health": 1}
csv.field_size_limit(10_000_000)

REPORT = []


def log(msg):
    print(msg, flush=True)


def report(msg):
    """A line for the human-readable report (also printed to the log)."""
    REPORT.append(msg)
    log(msg)


def write_report(ok):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    head = "## Data build report\n\n" + ("**Result: national list built.**\n\n" if ok else
            "**Result: no national data source worked. The site was published with the last saved list.**\n\n")
    body = "\n".join(f"- {line}" for line in REPORT)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(head + body + "\n")


# ---------------------------------------------------------------- network
class FetchError(Exception):
    pass


def http_get(url, timeout=60, tries=2, label=None):
    """Return bytes, None for a 404, or raise FetchError with a readable reason."""
    last = "unknown error"
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            body = ""
            try:
                body = e.read(400).decode("utf-8", "replace").strip().replace("\n", " ")
            except Exception:
                pass
            last = f"HTTP {e.code} {e.reason}" + (f": {body[:300]}" if body else "")
            if e.code in (400, 401, 403):
                break           # retrying won't help
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if attempt < tries:
            time.sleep(3 * attempt)
    raise FetchError(last)


# ---------------------------------------------------------------- reading IPEDS files
def decode(raw):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def table_from_text(text):
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        clean = {}
        for k, v in row.items():
            key = (k or "").replace("\ufeff", "").replace("ï»¿", "").strip().strip('"').upper()
            clean[key] = (v or "").strip() if isinstance(v, str) else ""
        uid = clean.get("UNITID")
        if uid:
            out[uid] = clean
    return out


def table_from_zip(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    members = [m for m in z.namelist() if m.lower().endswith(".csv")]
    if not members:
        return {}
    members.sort(key=lambda m: (not m.lower().endswith("_rv.csv"), m))
    return table_from_text(decode(z.read(members[0])))


FILE_RE = re.compile(r"^(hd|ic)(\d{4})(_ay)?(_rv)?(_data_stata)?(\s*\(\d+\))?\.(zip|csv)$", re.I)


def find_local_ipeds():
    """Find uploaded IPEDS files anywhere in the repo. Returns {(kind, year): path}."""
    found = {}
    for p in ROOT.rglob("*"):
        if not p.is_file() or ".git" in p.parts or "node_modules" in p.parts:
            continue
        m = FILE_RE.match(p.name)
        if not m:
            continue
        kind = m.group(1).upper() + ("_AY" if m.group(3) else "")
        key = (kind, int(m.group(2)))
        # prefer revised (_rv) and zips over duplicates
        if key not in found or "_rv" in p.name.lower():
            found[key] = p
    return found


def load_local(path):
    data = path.read_bytes()
    if path.suffix.lower() == ".zip":
        if data[:2] != b"PK":
            raise ValueError("not a real zip file (it may be a web page saved by mistake)")
        return table_from_zip(data)
    return table_from_text(decode(data))


# ---------------------------------------------------------------- helpers
def clean_url(u):
    u = (str(u) if u is not None else "").strip().replace(" ", "")
    if not u or u in {".", "-1", "-2", "-3", "None", "null"}:
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u.lstrip("/")
    return u


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f < 0 else f


def norm_name(s):
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"^tcat\b", "tennessee college of applied technology", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    words = [w for w in s.split() if w not in {"the", "at", "of", "in"}]
    return " ".join(words)


def compact(d):
    return {k: v for k, v in d.items() if v is not None and v != "" and v != [] and v is not False}


def finish_row(row, fee=None, ug=1, dg=1):
    """compact() keeps zeros now, but make these fields explicit."""
    row = compact(row)
    if fee is not None:
        row["fee"] = int(fee)
    row["ug"] = ug
    row["dg"] = dg
    return row


# ---------------------------------------------------------------- source 1 & 2: IPEDS files
def rows_from_ipeds_tables(hd, ic, ay):
    rows = []
    for uid, r in hd.items():
        if r.get("CYACTIVE", "1") not in ("1", ""):
            continue
        if r.get("PSET4FLG", "1") not in ("1", ""):
            continue
        try:
            sector = int(float(r.get("SECTOR", "0")))
        except ValueError:
            continue
        if not 1 <= sector <= 9 or not r.get("INSTNM"):
            continue
        state = r.get("STABBR", "")
        fee = num(ic.get(uid, {}).get("APPLFEEU")) if ic else None

        tuition, rate = None, ("in" if state == "TN" else "out")
        a = ay.get(uid) if ay else None
        if a:
            k = 2 if rate == "in" else 3
            tuition = num(a.get(f"CHG{k}AY3"))
            if tuition is None:
                t, f = num(a.get(f"TUITION{k}")), num(a.get(f"FEE{k}"))
                if t is not None:
                    tuition = t + (f or 0)

        alias = r.get("IALIAS")
        rows.append(finish_row({
            "id": uid, "n": r.get("INSTNM"),
            "al": alias if alias not in ("", ".", "-2", "-1") else None,
            "c": r.get("CITY"), "s": state, "t": sector,
            "w": clean_url(r.get("WEBADDR")),
            "a": clean_url(r.get("APPLURL")) or clean_url(r.get("ADMINURL")),
            "f": clean_url(r.get("FAIDURL")), "np": clean_url(r.get("NPRICURL")),
            "tu": int(round(tuition)) if tuition else None, "tus": rate if tuition else None,
            "h": 1 if r.get("HBCU") == "1" else None, "tr": 1 if r.get("TRIBAL") == "1" else None,
        }, fee=fee, ug=0 if r.get("UGOFFER") == "2" else 1, dg=1 if r.get("DEGGRANT") == "1" else 0))
    return rows


def build_from_local_files():
    found = find_local_ipeds()
    hd_years = sorted([y for (k, y) in found if k == "HD"], reverse=True)
    if not hd_years:
        others = [p.name for p in found.values()]
        report("Uploaded IPEDS files: no HD (directory) file found in the repo"
               + (f" (found only: {', '.join(others)})" if others else "") + ".")
        return None, None
    for y in hd_years:
        try:
            hd = load_local(found[("HD", y)])
        except Exception as e:
            report(f"Uploaded file {found[('HD', y)].relative_to(ROOT)} could not be read: {e}.")
            continue
        if not hd:
            report(f"Uploaded file {found[('HD', y)].relative_to(ROOT)} had no rows with a UNITID column.")
            continue
        ic, ay, ic_y, ay_y = {}, {}, None, None
        for yy in sorted([yy for (k, yy) in found if k == "IC" and yy <= y], reverse=True)[:1]:
            try:
                ic, ic_y = load_local(found[("IC", yy)]), yy
            except Exception as e:
                report(f"Uploaded IC file could not be read: {e}. Continuing without application fees.")
        for yy in sorted([yy for (k, yy) in found if k == "IC_AY" and yy <= y], reverse=True)[:1]:
            try:
                ay, ay_y = load_local(found[("IC_AY", yy)]), yy
            except Exception as e:
                report(f"Uploaded IC_AY file could not be read: {e}. Continuing without tuition.")
        rows = rows_from_ipeds_tables(hd, ic, ay)
        report(f"Used uploaded IPEDS files: HD{y} ({len(rows)} active schools)"
               + (f", IC{ic_y} (application fees)" if ic_y else ", no IC file (application fees skipped)")
               + (f", IC{ay_y}_AY (tuition)" if ay_y else ", no IC_AY file (tuition skipped)") + ".")
        return rows, {"source": "IPEDS", "hdYear": y, "feeYear": ic_y,
                      "tuitionYear": f"{ay_y}\u2013{str(ay_y + 1)[-2:]}" if ay_y else None}
    return None, None


def build_from_nces_download():
    this_year = dt.date.today().year
    errors = []
    for y in range(this_year, this_year - 5, -1):
        try:
            blob = http_get(IPEDS_URL.format(name=f"HD{y}"))
        except FetchError as e:
            errors.append(f"HD{y}: {e}")
            if len(errors) >= 2:
                break
            continue
        if not blob:
            continue                       # 404: that year isn't published yet
        if blob[:2] != b"PK":
            errors.append(f"HD{y}: the server sent a web page instead of a zip file")
            continue
        hd = table_from_zip(blob)
        ic, ay, ic_y, ay_y = {}, {}, None, None
        for name, target in ((f"IC{y}", "ic"), (f"IC{y}_AY", "ay")):
            try:
                b = http_get(IPEDS_URL.format(name=name))
                if b and b[:2] == b"PK":
                    if target == "ic":
                        ic, ic_y = table_from_zip(b), y
                    else:
                        ay, ay_y = table_from_zip(b), y
            except FetchError:
                pass
        rows = rows_from_ipeds_tables(hd, ic, ay)
        report(f"Downloaded IPEDS HD{y} from nces.ed.gov ({len(rows)} active schools).")
        return rows, {"source": "IPEDS", "hdYear": y, "feeYear": ic_y,
                      "tuitionYear": f"{ay_y}\u2013{str(ay_y + 1)[-2:]}" if ay_y else None}
    report("Download from nces.ed.gov failed" + (f" ({'; '.join(errors)})" if errors else " (no recent files found)") + ".")
    return None, None


# ---------------------------------------------------------------- source 3: Urban Institute (no key)
def pick(rec, *names):
    for n in names:
        if n in rec and rec[n] not in (None, "", -1, -2, -3):
            return rec[n]
    return None


def build_from_urban():
    this_year = dt.date.today().year
    errors = []
    for y in range(this_year - 1, this_year - 6, -1):
        url, recs = URBAN_URL.format(year=y), []
        try:
            pages = 0
            while url and pages < 50:
                blob = http_get(url, timeout=120)
                if blob is None:
                    break
                data = json.loads(blob)
                recs.extend(data.get("results", []))
                url = data.get("next")
                pages += 1
        except (FetchError, ValueError) as e:
            errors.append(f"{y}: {e}")
            if len(errors) >= 2:
                break
            continue
        if not recs:
            continue
        keys = sorted(recs[0].keys())
        levels = {r.get("institution_level") for r in recs[:2000]}
        urban_levels = 4 in levels           # Urban codes 4/2/1; raw IPEDS codes 1/2/3
        rows = []
        for r in recs:
            if pick(r, "currently_active_ipeds", "cyactive") in (0, "0"):
                continue
            name, state = pick(r, "inst_name", "instnm", "institution_name"), pick(r, "state_abbr", "stabbr", "state")
            if not name or not state:
                continue
            sector = pick(r, "sector")
            try:
                sector = int(sector) if sector is not None else None
            except (TypeError, ValueError):
                sector = None
            if sector is None:
                control, level = pick(r, "inst_control", "control"), pick(r, "institution_level", "iclevel")
                if control in (1, 2, 3) and level is not None:
                    tier = {4: 0, 2: 1, 1: 2}.get(level) if urban_levels else {1: 0, 2: 1, 3: 2}.get(level)
                    sector = tier * 3 + control if tier is not None else None
            if not sector or not 1 <= sector <= 9:
                continue
            uid = pick(r, "unitid")
            rows.append(finish_row({
                "id": str(uid) if uid else None, "n": name, "al": pick(r, "inst_alias", "ialias"),
                "c": pick(r, "city"), "s": state, "t": sector,
                "w": clean_url(pick(r, "url_school", "webaddr", "url")),
                "a": clean_url(pick(r, "url_application", "applurl")) or clean_url(pick(r, "url_admin", "adminurl")),
                "f": clean_url(pick(r, "url_fin_aid", "url_finaid", "faidurl")),
                "np": clean_url(pick(r, "url_net_price", "npricurl")),
                "h": 1 if pick(r, "hbcu") in (1, "1") else None,
                "tr": 1 if pick(r, "tribal_college", "tribal") in (1, "1") else None,
            }, ug=0 if pick(r, "offering_undergrad", "ugoffer") in (0, 2, "2") else 1,
               dg=0 if pick(r, "degree_granting", "deggrant") in (0, 2, "2") else 1))
        rows = [r for r in rows if r.get("id")]
        if len(rows) < 1000:
            report(f"Urban Institute API {y}: got {len(recs)} records but only {len(rows)} could be read. "
                   f"Fields it sent: {', '.join(keys[:60])}.")
            continue
        report(f"Used the Urban Institute Education Data API (IPEDS {y} directory, {len(rows)} schools). "
               "Application fees and tuition are not included from this source.")
        return rows, {"source": "IPEDS (via Urban Institute)", "hdYear": y}
    report("Urban Institute API failed" + (f" ({'; '.join(errors)})" if errors else " (no recent data found)") + ".")
    return None, None


# ---------------------------------------------------------------- source 4: College Scorecard (needs key)
def dig(rec, path):
    """Read 'a.b.c' from either flat dotted keys or nested objects; also try under 'latest.'."""
    for p in (path, "latest." + path):
        if p in rec:
            return rec[p]
        cur = rec
        for part in p.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if cur is not None:
            return cur
    return None


def build_from_scorecard(key):
    variants = ["id,school,latest.cost.tuition", "id,latest.school,latest.cost.tuition"]
    last_err = None
    for fields in variants:
        rows, page, total = [], 0, None
        try:
            while True:
                q = urllib.parse.urlencode({"api_key": key, "fields": fields, "per_page": 100,
                                            "page": page, "keys_nested": "true"})
                blob = http_get(f"{SCORECARD_URL}?{q}", timeout=90)
                if not blob:
                    raise FetchError("empty response")
                data = json.loads(blob)
                if "error" in data or "errors" in data:
                    raise FetchError(json.dumps(data.get("error") or data.get("errors"))[:300])
                results = data.get("results", [])
                total = data.get("metadata", {}).get("total", 0)
                for r in results:
                    if dig(r, "school.operating") in (0, "0"):
                        continue
                    own = dig(r, "school.ownership")
                    lvl = dig(r, "school.institutional_characteristics.level")
                    if lvl not in (1, 2, 3):
                        hi = dig(r, "school.degrees_awarded.highest")
                        lvl = 1 if (hi or 0) >= 3 else 2 if hi == 2 else 3 if hi is not None else None
                    if own not in (1, 2, 3) or lvl not in (1, 2, 3):
                        continue
                    state = dig(r, "school.state")
                    rate = "in" if state == "TN" else "out"
                    tu = dig(r, "cost.tuition.in_state" if rate == "in" else "cost.tuition.out_of_state")
                    rows.append(finish_row({
                        "id": str(r.get("id")), "n": dig(r, "school.name"), "al": dig(r, "school.alias"),
                        "c": dig(r, "school.city"), "s": state, "t": (lvl - 1) * 3 + own,
                        "w": clean_url(dig(r, "school.school_url")),
                        "np": clean_url(dig(r, "school.price_calculator_url")),
                        "tu": int(tu) if isinstance(tu, (int, float)) and tu > 0 else None,
                        "tus": rate if isinstance(tu, (int, float)) and tu > 0 else None,
                        "h": 1 if dig(r, "school.minority_serving.historically_black") == 1 else None,
                        "tr": 1 if dig(r, "school.minority_serving.tribal") == 1 else None,
                    }, dg=1 if (dig(r, "school.degrees_awarded.highest") or 0) >= 2 else 0))
                page += 1
                if not results or page * 100 >= total:
                    break
                time.sleep(0.25)
        except (FetchError, ValueError) as e:
            last_err = str(e)
            continue
        rows = [r for r in rows if r.get("n") and r.get("s")]
        if len(rows) >= 1000:
            report(f"Used the College Scorecard API ({len(rows)} schools). Application fees and apply links are not included from this source.")
            return rows, {"source": "College Scorecard", "tuitionYear": "latest"}
        last_err = f"only {len(rows)} usable schools came back (total reported: {total})"
    hint = ""
    if last_err and ("API_KEY" in last_err or "403" in last_err or "401" in last_err):
        hint = " The key was rejected: check that the secret holds only the key itself, with no spaces or quotes."
    report(f"College Scorecard API failed: {last_err}.{hint}")
    return None, None


# ---------------------------------------------------------------- Tennessee overlay
def overlay_row(o):
    return compact({
        "sh": o.get("short"),
        "r": o.get("region"),
        "sc": o.get("scholarships"),
        "a": o.get("apply"),
        "fs": o.get("fee"),
        "fa": o.get("freeApp"),
        "no": o.get("notes"),
        "dt": o.get("dates"),
        "da": 1 if o.get("da") else None,
        "pr": 1 if o.get("promise") else None,
        "h": 1 if o.get("hbcu") else None,
    })


def merge_overlay(rows, quiet=False):
    data = json.loads(OVERLAY.read_text(encoding="utf-8"))
    schools = data["schools"]
    tn = [r for r in rows if r.get("s") == "TN"]
    by_norm = {}
    for r in tn:
        for nm in [r.get("n")] + [a.strip() for a in re.split(r"[|,;]", r.get("al") or "") if a.strip()]:
            by_norm.setdefault(norm_name(nm), []).append(r)
    used, unmatched = set(), []

    for o in schools:
        target = None
        if o.get("unitid"):
            target = next((r for r in tn if r["id"] == str(o["unitid"])), None)
        if not target and o.get("ipedsName"):
            target = next(iter(by_norm.get(norm_name(o["ipedsName"]), [])), None)
        if not target:
            cands = [r for r in by_norm.get(norm_name(o["name"]), []) if r["id"] not in used]
            target = cands[0] if len(cands) == 1 else None
        if not target:
            ot = set(norm_name(o["name"]).split())
            cands = [r for r in tn if r["id"] not in used and
                     (set(norm_name(r["n"]).split()) <= ot or ot <= set(norm_name(r["n"]).split()))]
            target = cands[0] if len(cands) == 1 else None

        extra = overlay_row(o)
        if target:
            used.add(target["id"])
            target.update(extra)
            target["dn"] = o["name"]          # friendlier display name
        else:
            unmatched.append(o["name"])
            row = {
                "id": "tn-" + re.sub(r"[^a-z0-9]+", "-", o["name"].lower()).strip("-"),
                "n": o["name"], "c": o.get("city"), "s": "TN",
                "t": o.get("sector") or OVERLAY_SECTOR.get(o.get("type"), 2),
                "w": f"https://{o['domain']}" if o.get("domain") else None,
                "ug": 0 if o.get("type") == "health" else 1,
                "dg": 0 if o.get("type") == "tcat" else 1,
            }
            row.update(extra)
            if o.get("fee") == "none":
                row["fee"] = 0
            fixed = {"ug": row["ug"], "dg": row["dg"]}
            if o.get("fee") == "none":
                fixed["fee"] = 0
            rows.append(compact(row) | fixed)

    (log if quiet else report)(f"Tennessee details: {len(schools) - len(unmatched)} schools matched to the national list"
                               + (f"; {len(unmatched)} kept as separate entries (for example {', '.join(unmatched[:5])})" if unmatched and not quiet else "") + ".")
    return rows, data.get("updated")


# ---------------------------------------------------------------- main
def write(rows, meta):
    rows.sort(key=lambda r: (r.get("dn") or r.get("n") or "").lower())
    meta = {**meta, "built": dt.date.today().isoformat(), "count": len(rows)}
    payload = json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, separators=(",", ":"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("/* Generated by scripts/build_data.py. Do not edit by hand; edit data/tn-overlay.json instead. */\n"
                   f"window.COLLEGES_DATA = {payload};\n", encoding="utf-8")
    log(f"Wrote {OUT.relative_to(ROOT)}: {len(rows)} schools, {OUT.stat().st_size // 1024} KB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tn-only", action="store_true", help="skip downloads; Tennessee overlay only")
    args = ap.parse_args()

    if args.tn_only:
        rows, meta = [], {"source": "Tennessee overlay", "mode": "tn-only"}
    else:
        rows, meta = None, None
        key = (os.environ.get("SCORECARD_API_KEY") or "").strip().strip('"').strip("'")
        sources = [build_from_local_files, build_from_nces_download, build_from_urban]
        for source in sources:
            try:
                rows, meta = source()
            except Exception as e:  # never let one source crash the build
                report(f"{source.__name__} stopped with an unexpected error: {type(e).__name__}: {e}")
                rows = None
            if rows:
                break
        if not rows:
            if key:
                rows, meta = build_from_scorecard(key)
            else:
                report("College Scorecard: skipped, because no SCORECARD_API_KEY secret is set "
                       "(Settings > Secrets and variables > Actions; the name must match exactly).")
        if not rows:
            report("No source worked, so data/colleges-data.js was left unchanged.")
            write_report(False)
            sys.exit(2)
        meta["mode"] = "national"

    rows, overlay_date = merge_overlay(rows, quiet=args.tn_only)
    meta["overlayUpdated"] = overlay_date
    write(rows, meta)
    if not args.tn_only:
        report(f"Wrote {len(rows)} schools to data/colleges-data.js.")
        write_report(True)


if __name__ == "__main__":
    main()
