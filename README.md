# Get Hired pipeline

A daily job that pulls relevant UK AI and ML engineering roles into a Google Sheet and tags each one Warm (you know someone at the company) or Cold.

## Overview

The pipeline has three tiers:

- **Tier A, company career pages.** Reads `targets.csv` and fetches every open role straight from each company's applicant tracking system (ATS). Free and exact.
- **Tier B, market search.** Runs an Apify actor that searches Hiring.cafe for UK roles at companies you have not listed. Costs a few pence per run and can be switched off.
- **Tier C, connections.** Matches each job's company against your LinkedIn connections export and tags it Warm or Cold.

Jobs are filtered by title, seniority and location, de-duplicated, then upserted into the "Jobs" tab of your sheet. Columns you add to the sheet yourself (notes, status) are never touched.

## Architecture

```mermaid
flowchart TD
    T[targets.csv] --> A[Tier A: fetch from each ATS]
    A --> N[Normalize to shared schema]
    N --> FA[Filter: title, seniority, location]

    Q[config.yaml queries] --> B[Tier B: Apify Hiring.cafe search]
    B --> NB[Normalize and clean titles]
    NB --> FB[Filter: Tier A rules plus role terms, experience, clearance]

    FA --> D[Dedupe]
    FB --> D
    D --> E[Add level and age_days]
    C[connections.csv] --> W[Tier C: tag Warm or Cold]
    E --> W
    W --> CSV[jobs.csv]
    W --> S[Google Sheet: upsert by url]
    S --> M[Email digest of jobs first seen today]
```

## Repo structure

```
get-hired-pipeline/
├── config.yaml              # filters, levels, Tier B settings, connection matching
├── targets.csv              # Company,ATS,Slug,Careers URL
├── connections.csv          # your LinkedIn export (gitignored, optional)
├── main.py                  # entry point
├── pipeline/
│   ├── fetchers.py          # one fetch function per ATS, plus ATS detection
│   ├── normalize.py         # map each ATS response to the shared schema
│   ├── filters.py           # title, seniority and location rules
│   ├── market.py            # Tier B: Apify Hiring.cafe search
│   ├── dedupe.py            # drop repeats
│   ├── enrich.py            # level and age_days
│   ├── connections.py       # Tier C: Warm/Cold matching
│   ├── digest.py            # email of today's new jobs
│   ├── setup_sheet.py       # one-off formatting of the Jobs tab
│   ├── schedule_guard.py    # decides which daily trigger does the work
│   └── sheets.py            # Google Sheets upsert
├── tests/
│   └── fixtures/            # saved real API responses, one per source
└── .github/workflows/daily.yml
```

Every job from every source is mapped to the same fields: `company, ats, title, location, url, posted, ext_id`.

The sheet columns are:

| Column | Meaning |
|---|---|
| company, ats, title, location, url, posted, ext_id | the shared fields above |
| level | intern, graduate, junior, mid or senior |
| age_days | days since `posted`, refreshed every run |
| min_yoe, salary_min, salary_max, salary_currency, clearance | Tier B only, blank when the listing does not say |
| lead_type, connection, connection_title | Warm or Cold, and who you know there |
| first_seen, last_seen | date the pipeline first and most recently saw the job |

A job that disappears from a company's board keeps its row. Its `last_seen` simply stops moving, which tells you it has closed.

## Setup

Requires Python 3.11.

```powershell
git clone https://github.com/<you>/get-hired-pipeline.git
cd get-hired-pipeline
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

On macOS or Linux, activate with `source .venv/bin/activate`.

### 1. Google service account

The pipeline writes to your sheet as a service account, which is a robot Google account with its own email address.

1. Go to https://console.cloud.google.com and create a project (any name).
2. Open "APIs & Services", then "Library". Search for **Google Sheets API** and click Enable.
3. Open "IAM & Admin", then "Service Accounts". Click "Create service account", give it a name and click Done. You can skip the optional role steps.
4. Click the new service account, open the "Keys" tab, then "Add key", "Create new key", JSON. A key file downloads. Store it outside this repo.
5. Copy the service account's email address. It ends in `iam.gserviceaccount.com`.

### 2. Share the sheet with the service account

1. Create a Google Sheet, or open the one you want to use.
2. Click Share, paste the service account email and give it Editor access.
3. Copy the sheet ID from the URL. It is the long string between `/d/` and `/edit`.

You do not need to create the "Jobs" tab or any headers. The first run does both.

### 3. Environment variables

| Variable | Used for | Notes |
|---|---|---|
| `SHEET_ID` | Google Sheets | the ID from step 2 |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Google Sheets, local runs | path to the key file |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google Sheets, GitHub Actions | the full contents of the key file. Used when the file variable is not set |
| `APIFY_TOKEN` | Tier B | from https://console.apify.com/settings/integrations. Without it Tier B is skipped |
| `GMAIL_ADDRESS` | Email digest | the Gmail account that sends the digest. Without it no email is sent |
| `GMAIL_APP_PASSWORD` | Email digest | a Gmail app password, see step 6 |
| `DIGEST_TO` | Email digest | optional. Where to send the digest. Defaults to `GMAIL_ADDRESS` |

In PowerShell, for the current session:

```powershell
$env:SHEET_ID = "your-sheet-id"
$env:GOOGLE_SERVICE_ACCOUNT_FILE = "C:\path\to\key.json"
$env:APIFY_TOKEN = "your-apify-token"
```

The pipeline never prints or logs any of these values.

### 4. GitHub secrets

In your GitHub repo open "Settings", "Secrets and variables", "Actions" and add:

| Secret | Value |
|---|---|
| `SHEET_ID` | the sheet ID |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | open the key file in a text editor and paste the whole thing |
| `APIFY_TOKEN` | your Apify token. Leave it out to run Tier A only |
| `CONNECTIONS_CSV` | optional, see below |
| `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` | optional, for the email digest, see step 6 |
| `DIGEST_TO` | optional, if the digest should go to a different address |

`connections.csv` is not committed, so GitHub Actions cannot see it. Without the `CONNECTIONS_CSV` secret the daily run tags every job Cold and overwrites the Warm tags from your local runs. To keep Warm tags in the daily run:

```powershell
python -m pipeline.connections
```

This writes `connections.slim.csv` with only the four columns the pipeline reads (first name, last name, company, position). Paste its contents into the `CONNECTIONS_CSV` secret. GitHub secrets hold up to 48 KB and the command prints the file size.

The workflow in `.github/workflows/daily.yml` is meant to run once each morning, shortly after 07:00 UK time. It runs the tests first, then the pipeline, and uploads `jobs.csv` as an artifact.

How the daily run is chosen:

- GitHub is asked to trigger the workflow four times each morning (06:07, 07:07, 08:07 and 09:07 UTC), because its scheduler can be late or skip a trigger altogether.
- The first step of every run is a guard. The first trigger that arrives at or after 07:00 in London, on a day when the pipeline has not yet completed, does the work. Every later trigger that day finishes in a few seconds with "the pipeline has already run today, skipping".
- A late trigger still runs. What matters is whether the pipeline has run today (London date), not the exact time.
- If a run fails, the next trigger tries again, because only a run that completed the pipeline counts.

To start a run by hand, open the Actions tab, choose "Daily job pipeline", click "Run workflow" and tick **force**. Or from a terminal:

```powershell
gh workflow run daily.yml -f force=true
```

With force ticked the run always goes ahead. Without it, a hand-started run follows the same rule as a scheduled one, so it skips if the pipeline has already run today. That default is what makes the backup trigger in step 8 safe.

### 5. Connections (optional)

1. In LinkedIn open "Settings & Privacy", "Data privacy", "Get a copy of your data".
2. Tick "Connections" only and request the archive. It arrives by email within a few minutes.
3. Save `Connections.csv` from the archive as `connections.csv` in the repo root.

If the file is missing, every job is tagged Cold and the run says so.

Company names are compared after removing the words in `connections.ignore_words` in `config.yaml` (ltd, inc, ai, bank and so on). The match is exact, so "Cleo AI Ltd" matches "Cleo" but "Cleopatra" does not. If a company you know people at shows as Cold, check how LinkedIn spells the company and add the extra word to that list.

### 6. Email digest (optional)

After each run the pipeline emails you the jobs whose `first_seen` is today, grouped by level, with company, title, location, salary if the listing gave one, and the link. No email is sent when nothing is new, on `--dry-run`, or when the Gmail variables are not set.

It sends through Gmail with an app password, which is a 16-letter password for one app that you can revoke at any time. Your normal Google password is never used.

1. Turn on 2-Step Verification for the Google account at https://myaccount.google.com/security. App passwords are not available without it.
2. Go to https://myaccount.google.com/apppasswords and sign in again if asked.
3. Type a name such as "Get Hired pipeline" and click Create.
4. Copy the 16-letter password shown. Google shows it once. The spaces do not matter.
5. Add GitHub secrets `GMAIL_ADDRESS` (the full Gmail address) and `GMAIL_APP_PASSWORD` (the 16 letters). Add `DIGEST_TO` only if the digest should go somewhere other than that Gmail address.

To test it locally, set the same variables in PowerShell and run `python main.py`:

```powershell
$env:GMAIL_ADDRESS = "you@gmail.com"
$env:GMAIL_APP_PASSWORD = "abcd efgh ijkl mnop"
```

If you run the pipeline twice in one day, the second run sends the same day's new jobs again, plus anything added since. To stop the emails, set `digest.enabled: false` in `config.yaml` or delete the two secrets. To revoke access, delete the app password on the same Google page.

If the apppasswords page says the setting is not available, the account is a work or school account whose administrator has disabled app passwords, or it uses Advanced Protection. Use a personal Gmail account as the sender instead.

### 7. Format the sheet (optional)

Once the pipeline has run at least once, so the "Jobs" tab has its header row, you can format the tab with one command. It uses the same `SHEET_ID` and Google credentials as the pipeline:

```powershell
python -m pipeline.setup_sheet
```

What it does:

- Freezes the header row and the first three columns (company, ats, title), and makes the header bold with a light fill.
- Hides the `ats`, `posted`, `ext_id` and `salary_currency` columns. To see them again, select the columns either side, right-click and choose "Unhide columns".
- Shows `salary_min` and `salary_max` as whole pounds.
- Adds `status`, `notes` and `applied_on` columns at the end if they are missing. `status` is a dropdown: To review, Applying, Applied, Interviewing, Offer, Rejected, Not interested.
- Adds four conditional formatting rules over all data rows, in this priority order:

  | Rule | Format |
  |---|---|
  | No longer listed: `last_seen` is more than 2 days ago | grey text, strikethrough |
  | New: `first_seen` is within the last 2 days | light green fill |
  | `level` is intern, graduate or junior | bold |
  | `lead_type` is Warm | light orange fill |

  Where two rules set the same thing, the higher one wins, so a new Warm job is green, not orange.
- Creates a filter view called "To review" that shows rows with a blank status, newest `first_seen` first. Open it from "Data", "Filter views". A filter view changes only what you see; the rows themselves are not reordered.

It is safe to run again. It finds columns by header name, so it still works after you move columns around. It replaces its own rules and filter view and leaves any you made yourself alone, and it adds no duplicate columns. It never changes cell values. If you later rename one of the pipeline's headers, the step that needs it is skipped with a warning.

The frozen columns are only set when company, ats and title are the first three columns. The dropdown rejects values outside the list for new entries; anything already typed in the status column is left as it is.

### 8. Backup trigger from outside GitHub (optional, recommended)

GitHub does not guarantee scheduled workflows. On some repositories the schedule fires late, and on some it does not fire at all for days. A free outside scheduler that starts the workflow through the GitHub API removes that dependency. The guard makes it safe: if GitHub's own schedule has already run that day, the backup trigger skips.

**A. Create a token that can only start this repo's workflows**

1. On GitHub, click your profile picture, then "Settings", "Developer settings", "Personal access tokens", "Fine-grained tokens", "Generate new token".
2. Token name: `cron-job.org get-hired-pipeline`.
3. Expiration: pick the longest you are comfortable with, up to one year. Put the expiry date in your calendar, because the backup stops when the token expires.
4. Repository access: "Only select repositories", then choose `get-hired-pipeline`.
5. Permissions, "Repository permissions": set **Actions** to "Read and write". Leave everything else at "No access". GitHub adds "Metadata: Read-only" by itself.
6. Click "Generate token" and copy it. It starts with `github_pat_` and is shown once.

This token can start, cancel and re-run workflows in this one repo and read their logs. It cannot read your secrets, push code or touch any other repo.

**B. Check the token from your own machine**

```powershell
curl.exe -i -X POST "https://api.github.com/repos/seyiabello/get-hired-pipeline/actions/workflows/daily.yml/dispatches" `
  -H "Accept: application/vnd.github+json" `
  -H "Authorization: Bearer github_pat_YOUR_TOKEN" `
  -H "X-GitHub-Api-Version: 2022-11-28" `
  -d '{\"ref\":\"main\"}'
```

`HTTP/2 204` with an empty body means it worked, and a new run appears in the Actions tab within a few seconds. If the pipeline has already run today, that run skips, which is correct.

**C. Create the job on cron-job.org**

1. Sign up at https://cron-job.org (free) and click "Create cronjob".
2. On the "Common" tab:
   - Title: `Get Hired daily backup`
   - URL: `https://api.github.com/repos/seyiabello/get-hired-pipeline/actions/workflows/daily.yml/dispatches`
   - Execution schedule: "Custom", every day at 07:20. Set the time zone to "Europe/London" so it follows UK clock changes.
3. On the "Advanced" tab:
   - Request method: `POST`
   - Headers, add these four:

     | Key | Value |
     |---|---|
     | `Accept` | `application/vnd.github+json` |
     | `Authorization` | `Bearer github_pat_YOUR_TOKEN` |
     | `X-GitHub-Api-Version` | `2022-11-28` |
     | `Content-Type` | `application/json` |

   - Request body: `{"ref":"main"}`
4. Turn on "Notify me when execution fails" so you hear about an expired token.
5. Click "Create", then open the job and use "Test run". The result should be `204 No Content`.

07:20 is after GitHub's own 07:07 trigger has normally finished its guard, so on a good day the backup simply skips. For a second safety net, clone the job and set the copy to 08:20.

Do not add `"inputs":{"force":"true"}` to the body. Without force, the backup is treated exactly like a scheduled trigger. With it, the pipeline would run a second time every day, doubling the Apify cost and the email.

| Response from GitHub | Meaning |
|---|---|
| 204 | Started |
| 401 | The token is wrong or has expired. Create a new one and update the Authorization header |
| 403 or 404 | The token does not cover this repo, or lacks "Actions: Read and write" |
| 422 | The body is wrong, or the branch in `ref` does not exist |

## Running locally

```powershell
python main.py
```

This fetches all tiers, writes `jobs.csv` and upserts the sheet. Expect output like:

```
INFO Tier A: kept 63 of 435 jobs after filters
INFO Tier B: kept 18 of 24 jobs after filters
INFO Dedupe: 80 jobs, 1 repeats dropped
INFO Tier C: 12 Warm, 68 Cold
INFO Sheets: 80 new rows, 0 updated
```

Run the tests with:

```powershell
python -m pytest -q
```

The tests use saved responses in `tests/fixtures/` and make no network calls.

## Running with --dry-run

```powershell
python main.py --dry-run
python main.py --dry-run --company Cleo
```

`--dry-run` prints the jobs and writes `jobs.csv` without touching Google Sheets, so it needs no Google credentials. `--company` limits the run to one row of `targets.csv` and skips Tier B, which is the quickest way to check a new company.

## Adding a company to targets.csv

Add one row:

```
Company,ATS,Slug,Careers URL
Acme,ashby,acme,
```

Supported ATS values and where to find the slug:

| ATS | Slug is the part shown as `{slug}` |
|---|---|
| `greenhouse` | `job-boards.greenhouse.io/{slug}` |
| `greenhouse-eu` | `job-boards.eu.greenhouse.io/{slug}` |
| `lever` | `jobs.lever.co/{slug}` |
| `ashby` | `jobs.ashbyhq.com/{slug}` |
| `workable` | `apply.workable.com/{slug}` |
| `recruitee` | `{slug}.recruitee.com` |
| `smartrecruiters` | `jobs.smartrecruiters.com/{slug}` |
| `bamboohr` | `{slug}.bamboohr.com/careers` |

If you do not know the ATS, leave ATS and Slug blank and fill in the Careers URL. The pipeline looks at the URL, where it redirects to, and the links on the page:

```
Acme,,,https://acme.com/careers
```

Then check it:

```powershell
python main.py --dry-run --company Acme
```

If the log says "no supported ATS found", the company uses something else (Workday, Teamtailor, its own site) and cannot be added yet.

Two things to watch for:

- A slug can belong to a different company with a similar name. Check that the jobs printed are the ones you expect.
- Companies change ATS. If a company that used to work starts failing with a 404, find its new board and update the row.

## Tuning the filters

Everything is in `config.yaml`:

- `filters.title_must_contain`, `title_exclude_seniority`, `title_exclude_roles`: which titles are kept. Terms match whole words, so "ai" does not match "maintain".
- `filters.locations`: `uk` terms always pass. `broad` terms (remote, europe, emea) pass unless the location also names something in `exclude`, so "Remote" passes and "Remote - USA" does not.
- `filters.tiers`: Tier B must also contain one of `role_terms`. Tier A does not, because every company in `targets.csv` is already relevant.
- `levels`: the title keywords behind the `level` column.
- `market`: Tier B search queries, country, seniority, how recent, results per query, the experience limit (`max_min_yoe`) and `exclude_active_clearance`. Set `enabled: false` to run without Apify.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `WARNING <Company> failed, skipping: ... 404 Not Found` | Wrong slug, or the company moved ATS. The run carries on without it. Fix the row in `targets.csv`. |
| `WARNING <Company> failed, skipping: no supported ATS found` | The careers page does not link to a supported ATS. |
| A company shows `0 jobs fetched` | Its board is empty right now, or the slug belongs to an empty board. Open the board in a browser to check. |
| `Sheets update failed: set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON` | Neither variable is set in this terminal session. |
| `Sheets update failed: could not load credentials from ...` | The path is wrong, or the JSON was pasted incompletely. The message never shows the key itself. |
| `Sheets update failed` with a 403 or `PermissionError` | The sheet is not shared with the service account email, or the Sheets API is not enabled in the Google Cloud project. |
| `Sheets update failed` with a 404 | `SHEET_ID` is wrong. Use only the part between `/d/` and `/edit`. |
| `Tier B: skipped, APIFY_TOKEN is not set` | Expected if you have no token. Tier A still runs. |
| `Tier B: '<query>' failed` with 401 or 402 | The token is wrong, or the Apify account is out of credit. |
| Tier B returns jobs but few are kept | Normal. The role, experience and clearance rules are strict. Loosen them in `config.yaml`. |
| `Digest: skipped, GMAIL_ADDRESS and GMAIL_APP_PASSWORD are not both set` | Expected if you have not set up the digest. |
| `Digest: could not send the digest (SMTPAuthenticationError)` | The app password is wrong or was revoked, or `GMAIL_ADDRESS` is not the account that created it. Your normal Google password does not work here. |
| `Digest: could not send the digest (TimeoutError)` or another network error | The network blocks outgoing mail on port 465. GitHub Actions allows it. |
| No digest email arrived | Nothing was new today (the log says so), or the email is in spam. |
| `Sheet setup failed: the tab has no header row yet` | Run `python main.py` once before `python -m pipeline.setup_sheet`. |
| Everything is tagged Cold | `connections.csv` is missing, or in GitHub Actions the `CONNECTIONS_CSV` secret is not set. |
| A company you know people at is Cold | LinkedIn spells the company differently. Add the extra word to `connections.ignore_words`. |
| The scheduled run did not start | Check the Actions tab. Runs that say "skipping" in the first step are normal: only one trigger a day does the work. If there are no scheduled runs at all, GitHub's scheduler is not firing for this repo; set up the backup trigger in setup step 8. Turning the workflow off and on again (`gh workflow disable daily.yml`, then `gh workflow enable daily.yml`) sometimes makes GitHub pick the schedule up. GitHub also pauses schedules after 60 days without repo activity. |
| A run I started by hand skipped | The pipeline had already run today, or it was before 07:00 in London. Start it again with the force box ticked, or `gh workflow run daily.yml -f force=true`. |
| Rows I sorted or edited by hand | Safe. The pipeline finds rows by `url` and columns by header name. Do not rename the pipeline's own column headers. |

## Git push instructions

First push of a new local folder:

```powershell
git init -b main
git add .
git status
```

Check the `git status` list before committing. `connections.csv`, `jobs.csv`, `.venv/` and any key file must not appear in it.

```powershell
git commit -m "Get Hired pipeline"
```

Create an empty repo on GitHub (no README, no .gitignore), then:

```powershell
git remote add origin https://github.com/<you>/get-hired-pipeline.git
git push -u origin main
```

Make the repo private if you would rather not publish your target list. After pushing, add the secrets from the setup section and start the workflow once from the Actions tab to confirm it works.

Later changes:

```powershell
git add .
git commit -m "Describe the change"
git push
```
