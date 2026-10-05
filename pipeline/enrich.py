"""Columns derived from a normalized job: level (from the title) and age_days (from posted)."""

from datetime import date, datetime

from pipeline.filters import term_pattern


class LevelDetector:
    def __init__(self, config: dict):
        self.default = config["default"]
        self.seniority_map = config.get("seniority_map") or {}
        self.levels = []
        for level, terms in config["keywords"].items():
            terms = [str(t) for t in terms]
            # Capitalised terms are roman numerals: "I" must not match the word "i".
            exact = [t for t in terms if t.isupper()]
            loose = [t for t in terms if not t.isupper()]
            self.levels.append((level, term_pattern(loose), term_pattern(exact, ignore_case=False)))

    def level(self, title: str, seniority: str = "") -> str:
        if seniority in self.seniority_map:
            return self.seniority_map[seniority]
        for level, loose, exact in self.levels:
            if loose.search(title) or exact.search(title):
                return level
        return self.default


def age_days(posted: str, today: date):
    """Whole days since the posted timestamp, or "" if there is none."""
    try:
        return max((today - datetime.fromisoformat(posted).date()).days, 0)
    except ValueError:
        return ""


def enrich(jobs: list[dict], levels_config: dict, today: date) -> list[dict]:
    detector = LevelDetector(levels_config)
    return [
        {**job, "level": detector.level(job["title"], job.get("seniority", "")), "age_days": age_days(job["posted"], today)}
        for job in jobs
    ]
