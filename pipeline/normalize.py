"""Map each ATS response to the shared schema:
{company, ats, title, location, url, posted, ext_id}
"""

from datetime import datetime, timezone

FIELDS = ["company", "ats", "title", "location", "url", "posted", "ext_id"]


def _iso(value) -> str:
    """Return a UTC ISO 8601 timestamp, or "" if the value is missing."""
    if not value:
        return ""
    try:
        if isinstance(value, (int, float)):  # ms epoch (Lever)
            parsed = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        else:
            # Recruitee sends "2026-09-18 13:27:28 UTC"
            parsed = datetime.fromisoformat(str(value).replace(" UTC", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _join(*parts) -> str:
    return ", ".join(p.strip() for p in parts if p and p.strip())


def _job(company, ats, title, location, url, posted, ext_id) -> dict:
    return {
        "company": company,
        "ats": ats,
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url or "",
        "posted": _iso(posted),
        "ext_id": str(ext_id),
    }


def normalize_greenhouse(data, company: str, slug: str, ats: str = "greenhouse") -> list[dict]:
    return [
        _job(
            company,
            ats,
            j.get("title"),
            (j.get("location") or {}).get("name"),
            j.get("absolute_url"),
            j.get("first_published") or j.get("updated_at"),
            j.get("id"),
        )
        for j in data.get("jobs", [])
    ]


def normalize_lever(data, company: str, slug: str, ats: str = "lever") -> list[dict]:
    return [
        _job(
            company,
            ats,
            j.get("text"),
            (j.get("categories") or {}).get("location"),
            j.get("hostedUrl"),
            j.get("createdAt"),
            j.get("id"),
        )
        for j in data
    ]


def normalize_ashby(data, company: str, slug: str, ats: str = "ashby") -> list[dict]:
    return [
        _job(
            company,
            ats,
            j.get("title"),
            j.get("location"),
            j.get("jobUrl") or j.get("applyUrl"),
            j.get("publishedAt"),
            j.get("id"),
        )
        for j in data.get("jobs", [])
        if j.get("isListed", True)
    ]


def normalize_workable(data, company: str, slug: str, ats: str = "workable") -> list[dict]:
    # city and country are top-level fields; there is no "location" object.
    return [
        _job(
            company,
            ats,
            j.get("title"),
            _join(j.get("city"), j.get("country"), "Remote" if j.get("telecommuting") else ""),
            j.get("url") or j.get("application_url"),
            j.get("published_on"),
            j.get("shortcode"),
        )
        for j in data.get("jobs", [])
    ]


def normalize_recruitee(data, company: str, slug: str, ats: str = "recruitee") -> list[dict]:
    return [
        _job(
            company,
            ats,
            j.get("title"),
            _join(j.get("location"), "Remote" if j.get("remote") else ""),
            j.get("careers_url"),
            j.get("published_at"),
            j.get("id"),
        )
        for j in data.get("offers", [])
    ]


def normalize_smartrecruiters(data, company: str, slug: str, ats: str = "smartrecruiters") -> list[dict]:
    jobs = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        # country is a two-letter code; "gb" would never match the "uk" location filter
        country = "United Kingdom" if (loc.get("country") or "").lower() == "gb" else (loc.get("country") or "").upper()
        jobs.append(
            _job(
                company,
                ats,
                j.get("name"),
                _join(loc.get("city"), country, "Remote" if loc.get("remote") else ""),
                f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                j.get("releasedDate"),
                j.get("id"),
            )
        )
    return jobs


def normalize_bamboohr(data, company: str, slug: str, ats: str = "bamboohr") -> list[dict]:
    jobs = []
    for j in data.get("result", []):
        detail = j.get("detail") or {}
        office = detail.get("location") or j.get("location") or {}
        other = detail.get("atsLocation") or j.get("atsLocation") or {}
        jobs.append(
            _job(
                company,
                ats,
                j.get("jobOpeningName"),
                _join(
                    office.get("city") or other.get("city"),
                    office.get("addressCountry") or other.get("country"),
                    "Remote" if j.get("isRemote") else "",
                ),
                f"https://{slug}.bamboohr.com/careers/{j.get('id')}",
                detail.get("datePosted"),
                j.get("id"),
            )
        )
    return jobs


NORMALIZERS = {
    "bamboohr": normalize_bamboohr,
    "greenhouse": normalize_greenhouse,
    "greenhouse-eu": normalize_greenhouse,
    "lever": normalize_lever,
    "ashby": normalize_ashby,
    "workable": normalize_workable,
    "recruitee": normalize_recruitee,
    "smartrecruiters": normalize_smartrecruiters,
}


def normalize(ats: str, data, company: str, slug: str) -> list[dict]:
    return NORMALIZERS[ats](data, company, slug, ats)
