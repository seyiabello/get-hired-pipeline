"""Tier A: one fetch function per ATS. Each returns the parsed JSON body as is.

Also detects the ATS and slug from a careers URL when targets.csv leaves them blank.
"""

import re

import httpx


def _get_json(client: httpx.Client, url: str, **params):
    response = client.get(url, params=params or None)
    response.raise_for_status()
    return response.json()


def fetch_greenhouse(client: httpx.Client, slug: str):
    # EU-hosted boards (job-boards.eu.greenhouse.io) are served by this same API host.
    # boards-api.eu.greenhouse.io does not exist.
    return _get_json(client, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")


def fetch_lever(client: httpx.Client, slug: str):
    return _get_json(client, f"https://api.lever.co/v0/postings/{slug}?mode=json")


def fetch_ashby(client: httpx.Client, slug: str):
    return _get_json(client, f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")


def fetch_workable(client: httpx.Client, slug: str):
    return _get_json(client, f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")


def fetch_recruitee(client: httpx.Client, slug: str):
    return _get_json(client, f"https://{slug}.recruitee.com/api/offers/")


def fetch_smartrecruiters(client: httpx.Client, slug: str):
    # Paginated, 100 per page. Returns all pages merged under "content".
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    content = []
    while True:
        page = _get_json(client, url, limit=100, offset=len(content))
        content.extend(page.get("content", []))
        if not page.get("content") or len(content) >= page.get("totalFound", 0):
            return {"content": content}


def fetch_bamboohr(client: httpx.Client, slug: str):
    # The list has no posted date or country, so each job's detail is fetched too
    # and attached under "detail". A failed detail call leaves "detail" as None.
    base = f"https://{slug}.bamboohr.com/careers"
    data = _get_json(client, f"{base}/list")
    for job in data.get("result", []):
        try:
            job["detail"] = _get_json(client, f"{base}/{job['id']}/detail")["result"]["jobOpening"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            job["detail"] = None
    return data


FETCHERS = {
    "bamboohr": fetch_bamboohr,
    "greenhouse": fetch_greenhouse,
    "greenhouse-eu": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workable": fetch_workable,
    "recruitee": fetch_recruitee,
    "smartrecruiters": fetch_smartrecruiters,
}

# Order matters: the EU Greenhouse pattern must be tried before the plain one.
_ATS_PATTERNS = [
    ("greenhouse-eu", r"(?:job-boards|boards)\.eu\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([\w-]+)"),
    ("greenhouse", r"(?:job-boards|boards)\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([\w-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"),
    ("lever", r"jobs\.lever\.co/([\w-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.%-]+)"),
    ("workable", r"apply\.workable\.com/(?!api/|j/)([\w-]+)"),
    ("recruitee", r"([\w-]+)\.recruitee\.com"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"),
    ("bamboohr", r"([\w-]+)\.bamboohr\.com"),
]


def detect_ats(text: str) -> tuple[str, str] | None:
    """Find the first known ATS link in a URL or a page of HTML. Returns (ats, slug)."""
    for ats, pattern in _ATS_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return ats, match.group(1)
    return None


def detect_from_careers_url(client: httpx.Client, url: str) -> tuple[str, str] | None:
    """Try the URL itself, then where it redirects to, then the links in the page."""
    found = detect_ats(url)
    if found:
        return found
    response = client.get(url)
    response.raise_for_status()
    return detect_ats(str(response.url)) or detect_ats(response.text)
