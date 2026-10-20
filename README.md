# College Finder for Tennessee Students

A searchable site listing every U.S. college that takes part in federal student aid (about 6,000 schools), with links to apply, financial aid and scholarships, and net-price calculators, plus application fees and tuition for Tennessee residents. Tennessee schools carry extra hand-checked details: scholarship deadlines, free application periods, TN Promise and TN Direct Admissions.

## How it works

| File | What it does |
| --- | --- |
| `index.html` | The page: search, filters, Tennessee deadline timeline, aid info |
| `site-config.js` | Tennessee key dates and statewide aid programs. Update yearly |
| `data/tn-overlay.json` | Hand-checked Tennessee details. Edit this to fix or add Tennessee info |
| `data/colleges-data.js` | The generated school list. Don't edit by hand; it's rebuilt automatically |
| `scripts/build_data.py` | Downloads federal data (IPEDS) and merges in the Tennessee details |
| `.github/workflows/deploy.yml` | Builds the data and publishes the site on every push and monthly |
| `.github/workflows/check-links.yml` | Weekly check of the hand-entered links; opens an issue if any break |

The repo ships with a Tennessee-only `data/colleges-data.js`, so the page works right away. The first time the deploy workflow runs on GitHub, it downloads the national list and replaces that file.

## Put it on GitHub

1. Create a new **public** repository, for example `college-finder`.
2. Upload everything in this folder, including the hidden `.github` folder. If drag-and-drop skips it, create the two workflow files by hand with **Add file → Create new file** and paste in their contents.
3. Go to **Settings → Pages**. Under **Build and deployment → Source**, choose **GitHub Actions**. (Not "Deploy from a branch".)
4. Go to **Settings → Actions → General → Workflow permissions**, choose **Read and write permissions**, and save. This lets the workflow save the refreshed data file.
5. Open the **Actions** tab, choose **Build data and publish site**, and click **Run workflow**. It takes two to five minutes.
6. When it finishes, the site is at `https://YOUR-USERNAME.github.io/college-finder/`. The link also appears on the finished workflow run.

From then on, the site rebuilds whenever you change a file and on the 1st of every month.

## Updating Tennessee details

Edit `data/tn-overlay.json` in GitHub (pencil icon) and commit. Each school looks like this:

```json
{
  "name": "Middle Tennessee State University",
  "short": "MTSU",
  "type": "public",
  "region": "Middle",
  "city": "Murfreesboro",
  "domain": "mtsu.edu",
  "da": true,
  "apply": "https://www.mtsu.edu/how-to-apply/",
  "scholarships": "https://www.mtsu.edu/financial-aid/incoming-freshmen/",
  "fee": "toolkit2025",
  "freeApp": "Listed in THEC's Sept. 2025 fee-waiver toolkit.",
  "dates": [["2026-12-01", "Complete admission file for guaranteed scholarships"]],
  "notes": "..."
}
```

The build matches each Tennessee entry to its federal record by name. The workflow log lists any that didn't match. To force a match, add `"unitid": "220978"` (the school's IPEDS ID, shown in its College Navigator link) or `"ipedsName": "exact federal name"`.

Update `site-config.js` each August with the new TN Promise deadline, key dates and free-application month details.

## Troubleshooting

Every build writes a **Data build report**. To read it, open the **Actions** tab, click the newest **Build data and publish site** run, and scroll down the Summary page. It says which source was used, or exactly why each one failed.

The build tries these sources in order and uses the first that works:

1. **IPEDS files you uploaded.** Put `HD20xx`, `IC20xx` and `IC20xx_AY` from https://nces.ed.gov/ipeds/datacenter/DataFiles.aspx anywhere in the repo. Zipped or unzipped both work, and names like `HD2024 (1).zip` are fine.
2. **IPEDS download** from nces.ed.gov.
3. **Urban Institute Education Data API**, a free copy of IPEDS that needs no key.
4. **College Scorecard API**, if a `SCORECARD_API_KEY` repository secret is set.

If none work, the site still publishes with the last saved list and the page says "Showing Tennessee schools only."

**Testing on your own computer:** double-click `index.html`. To rebuild data locally, run `python3 scripts/build_data.py` (needs internet) or `python3 scripts/build_data.py --tn-only`.

## Accuracy

Federal links are reported by each college and can go stale between yearly updates. Fee waivers and deadlines change every year. The page says so and links to official sources.
