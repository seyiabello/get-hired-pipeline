"""Decide whether this GitHub Actions run should do the day's work.

The workflow is triggered several times each morning (GitHub's own schedule, plus an
optional outside trigger), because any single trigger can arrive late or not at all.
The first one at or after 07:00 London time does the work. Later ones see that a run
has already done it today and skip. A run started with the "force" input always goes.

Used as the first step of .github/workflows/daily.yml. Standard library only, so it
runs before dependencies are installed.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
START_HOUR = 7
WORKFLOW_FILE = "daily.yml"
PIPELINE_STEP = "Run pipeline"  # must match the step name in the workflow
API = "https://api.github.com"


def london_date(timestamp: str):
    """London calendar date of a GitHub API timestamp such as 2026-10-06T23:30:00Z."""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(LONDON).date()


def did_the_work(jobs: list[dict]) -> bool:
    """True if a run got through the pipeline step. A run that skipped also ends in
    "success", so the run's own conclusion is not enough."""
    return any(
        step.get("name") == PIPELINE_STEP and step.get("conclusion") == "success"
        for job in jobs
        for step in job.get("steps") or []
    )


def decide(event: str, force: bool, now: datetime, worked_at: list[str]) -> tuple[bool, str]:
    """Return (go, reason).

    worked_at holds the start times of earlier runs that completed the pipeline step.
    Scheduled runs and unforced dispatches are treated the same way.
    """
    if force:
        return True, "force is set, running"
    if event not in ("schedule", "workflow_dispatch"):
        return True, f"event {event!r} is not guarded, running"
    local = now.astimezone(LONDON)
    if local.hour < START_HOUR:
        return False, f"it is {local:%H:%M} in London, before {START_HOUR:02d}:00, skipping"
    if any(london_date(started) == local.date() for started in worked_at):
        return False, f"the pipeline has already run today ({local.date()} in London), skipping"
    return True, f"no pipeline run yet today ({local.date()} in London), running"


def _get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        f"{API}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def runs_that_worked(repo: str, token: str, today, current_run_id: str, get=_get) -> list[str]:
    """Start times of today's successful runs of this workflow that ran the pipeline step."""
    runs = get(f"/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs?status=success&per_page=30", token)
    worked = []
    for run in runs.get("workflow_runs", []):
        started = run.get("run_started_at") or run.get("created_at")
        if str(run.get("id")) == str(current_run_id) or london_date(started) != today:
            continue
        if did_the_work(get(f"/repos/{repo}/actions/runs/{run['id']}/jobs", token).get("jobs", [])):
            worked.append(started)
    return worked


def main() -> int:
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    force = os.environ.get("FORCE", "").strip().lower() == "true"
    now = datetime.now(timezone.utc)
    worked = []
    if not force:
        try:
            worked = runs_that_worked(
                os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"],
                now.astimezone(LONDON).date(), os.environ.get("GITHUB_RUN_ID", ""),
            )
        except Exception as exc:
            # A second run in a day is cheap. A missed day is not. So when in doubt, run.
            print(f"::warning::could not check earlier runs ({type(exc).__name__}), assuming none")
    go, reason = decide(event, force, now, worked)
    print(f"Guard: {reason}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"go={'true' if go else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
