"""Tier B: market search through an Apify Hiring.cafe actor.

Run `python -m pipeline.market` to make one live call for the first query, save the
raw response to tests/fixtures/hiring_cafe.json and print how it normalizes.
"""

import json
import os
import re
import sys
from pathlib import Path

import httpx

from pipeline.normalize import _job

ATS = "hiring.cafe"
FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "hiring_cafe.json"

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]+")
_LEADING_NUMBER = re.compile(r"^\s*[(\[]\d+[)\]]\s*")
# " in Leeds, West Yorkshire, United Kingdom" at the end of a title
_LOCATION_SUFFIX = re.compile(r"\s+in\s+[A-Z][^,|]*(?:,\s*[^,|]+)*,\s*(?:United Kingdom|UK)\s*$")
# "Active eSC/eDV Required", "existing DV clearance", "SC Cleared". Not "SC eligible" or "SC Clearance".
_CLEARANCE = r"(?:e?SC|e?DV|NPPV\d?|clearance|cleared)"
_ACTIVE_CLEARANCE = re.compile(
    rf"\b(?:active|existing|current|currently|holds?|holding|held)\b[^|,;]{{0,25}}?\b{_CLEARANCE}\b"
    r"|\b(?:e?SC|e?DV)[\s/-]+(?:e?SC[\s/-]+|e?DV[\s/-]+)?cleared\b",
    re.IGNORECASE,
)


def clean_title(title: str) -> str:
    title = _EMOJI.sub("", title or "")
    title = _LEADING_NUMBER.sub("", title)
    title = _LOCATION_SUFFIX.sub("", title)
    return " ".join(title.split()).strip(" -|,")


def fetch_market(client: httpx.Client, config: dict, token: str, query: str) -> list[dict]:
    """Run the actor for one search query and wait for its dataset items. Raises on any failure."""
    url = f"https://api.apify.com/v2/acts/{config['actor']}/run-sync-get-dataset-items"
    # The actor has no proxy input, so no proxy is requested on this account.
    actor_input = {
        # The actor's plain "query" field matches loosely ("LLM Engineer" returns civil
        # engineers), so the query is sent as an exact phrase to find in the description.
        "jobDescriptionQuery": f'"{query}"',
        "country": config["country"],
        "seniorityLevels": config["seniority_levels"],
        "postedWithinDays": config["posted_within_days"],
        "sortBy": config["sort_by"],
        "maxResults": config["max_items"],
        "includeDetails": False,  # descriptions are not used; core fields are always returned
    }
    response = client.post(
        url,
        json=actor_input,
        # No maxItems query parameter: Apify turns it into a spending cap that also has to
        # cover the actor's start fee, which cut every run to half its results.
        params={"timeout": config["timeout_seconds"]},
        # The token goes in a header, not in ?token=, so it can never show up in a logged URL.
        headers={"Authorization": f"Bearer {token}"},
        timeout=config["timeout_seconds"],
    )
    response.raise_for_status()
    items = response.json()
    if not isinstance(items, list):
        raise ValueError(f"expected a list of jobs, got {type(items).__name__}")
    return items


def _location(item: dict) -> str:
    # Work-from-anywhere jobs arrive as "North America or South America or Europe or ...",
    # which the location filter would read as a list of non-UK places.
    if item.get("isWorkplaceWorldwideOk"):
        return "Remote (Worldwide)"
    location = (item.get("location") or "").strip()
    # A bare city ("Leeds") would fail the location filter even though the job is in the UK.
    if "GB" in (item.get("workplaceCountries") or []) and "united kingdom" not in location.lower():
        location = f"{location}, United Kingdom".lstrip(", ")
    return location


def _blank_if_none(value):
    return "" if value is None else value


def normalize_market(items: list[dict]) -> list[dict]:
    """Shared schema plus the extra columns only this source can fill."""
    jobs = []
    for item in items:
        job = _job(
            (item.get("company") or item.get("companyName") or "").strip(),
            ATS,
            clean_title(item.get("title")),
            _location(item),
            item.get("applyUrl") or item.get("portalUrl"),
            item.get("postedDate"),
            item.get("jobId") or "",
        )
        has_salary = item.get("salaryMin") is not None or item.get("salaryMax") is not None
        job.update(
            seniority=item.get("seniorityLevel") or "",
            min_yoe=_blank_if_none(item.get("minYearsExperience")),
            salary_min=_blank_if_none(item.get("salaryMin")),
            salary_max=_blank_if_none(item.get("salaryMax")),
            salary_currency=(item.get("salaryCurrency") or "") if has_salary else "",
            clearance=item.get("securityClearance") or ("Required" if item.get("clearanceRequired") else ""),
            raw_title=item.get("title") or "",
        )
        jobs.append(job)
    return jobs


def needs_active_clearance(job: dict) -> bool:
    """True when the title or clearance field asks for a clearance you must already hold."""
    return any(_ACTIVE_CLEARANCE.search(str(job.get(field) or "")) for field in ("raw_title", "title", "clearance"))


def apply_market_rules(jobs: list[dict], config: dict) -> list[dict]:
    """Experience and clearance rules. Jobs that state no experience figure are kept."""
    kept = []
    for job in jobs:
        years = job.get("min_yoe")
        if isinstance(years, (int, float)) and years > config["max_min_yoe"]:
            continue
        if config["exclude_active_clearance"] and needs_active_clearance(job):
            continue
        kept.append(job)
    return kept


def _inspect() -> int:
    """Make one live call, save it as the test fixture and print a summary."""
    import yaml

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("APIFY_TOKEN is not set")
        return 1
    config = yaml.safe_load((FIXTURE.parents[2] / "config.yaml").read_text(encoding="utf-8"))["market"]
    with httpx.Client() as client:
        try:
            items = fetch_market(client, config, token, config["queries"][0])
        except httpx.HTTPStatusError as exc:
            print(f"Apify returned {exc.response.status_code}: {exc.response.text[:500]}")
            return 1
    FIXTURE.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(items)} items saved to {FIXTURE}")
    if items:
        print("keys:", sorted(items[0]))
    for job in normalize_market(items):
        print(json.dumps(job, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(_inspect())
