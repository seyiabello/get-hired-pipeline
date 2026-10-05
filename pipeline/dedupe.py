"""Drop repeated jobs.

Within a tier, a job is a repeat only if its url was already seen, so separate
postings that share a title all stay. A Tier B job is also dropped when Tier A
already has the same company and title.
"""

from pipeline.connections import company_keys


def title_keys(job: dict, ignore_words: list[str]) -> set[str]:
    """company|title forms to compare by. The company is normalised the same way as
    for connections, so "Faculty AI" and "Faculty" count as the same company."""
    title = " ".join(job["title"].lower().split())
    return {f"{company}|{title}" for company in company_keys(job["company"], ignore_words)}


def _unique_by_url(jobs: list[dict]) -> list[dict]:
    seen, unique = set(), []
    for job in jobs:
        if job["url"] not in seen:
            seen.add(job["url"])
            unique.append(job)
    return unique


def dedupe(tier_a: list[dict], tier_b: list[dict], ignore_words: list[str]) -> list[dict]:
    """Return Tier A followed by the Tier B jobs that Tier A does not already cover."""
    tier_a = _unique_by_url(tier_a)
    urls = {job["url"] for job in tier_a}
    keys = set().union(*(title_keys(job, ignore_words) for job in tier_a))
    extra = [
        job
        for job in _unique_by_url(tier_b)
        if job["url"] not in urls and not title_keys(job, ignore_words) & keys
    ]
    return tier_a + extra
