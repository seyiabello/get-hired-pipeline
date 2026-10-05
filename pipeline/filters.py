"""Role, seniority and location rules. All term lists live in config.yaml."""

import re

_NEVER = re.compile(r"(?!)")


def term_pattern(terms: list[str], ignore_case: bool = True) -> re.Pattern:
    """Whole-word match for any term, so "ai" does not match "maintain".

    A trailing "s" is allowed ("Engineers"), and a space in a term also matches
    a hyphen ("front end" matches "front-end").
    """
    if not terms:
        return _NEVER
    alternatives = [r"[\s-]+".join(re.escape(word) for word in str(term).split()) for term in terms]
    body = r"(?<![A-Za-z0-9])(?:" + "|".join(alternatives) + r")s?(?![A-Za-z0-9])"
    return re.compile(body, re.IGNORECASE if ignore_case else 0)


class JobFilter:
    def __init__(self, config: dict, tier: str):
        self.require_role_term = config["tiers"][tier]["require_role_term"]
        self.role = term_pattern(config["role_terms"])
        self.must = term_pattern(config["title_must_contain"])
        self.exclude = term_pattern(config["title_exclude_seniority"] + config["title_exclude_roles"])
        self.location_uk = term_pattern(config["locations"]["uk"])
        self.location_broad = term_pattern(config["locations"]["broad"])
        self.location_exclude = term_pattern(config["locations"]["exclude"])

    def location_ok(self, location: str) -> bool:
        if not location.strip() or self.location_uk.search(location):
            return True
        return bool(self.location_broad.search(location)) and not self.location_exclude.search(location)

    def keep(self, job: dict) -> bool:
        title = job["title"]
        if not self.must.search(title) or self.exclude.search(title):
            return False
        if self.require_role_term and not self.role.search(title):
            return False
        return self.location_ok(job["location"])


def apply_filters(jobs: list[dict], config: dict, tier: str) -> list[dict]:
    job_filter = JobFilter(config, tier)
    return [job for job in jobs if job_filter.keep(job)]
