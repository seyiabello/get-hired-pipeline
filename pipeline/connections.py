"""Tier C: tag each job Warm (you know someone at the company) or Cold."""

import csv
import io
import logging
import re
import sys
from pathlib import Path

log = logging.getLogger("get-hired")


def load_connections(path: Path) -> list[dict] | None:
    """Read a LinkedIn connections export. Returns None if the file is missing."""
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    # LinkedIn puts a few note lines above the real header.
    start = next((i for i, line in enumerate(lines) if line.lstrip('"').startswith("First Name")), None)
    if start is None:
        log.warning("%s has no header row starting with 'First Name'", path.name)
        return []
    return list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))


def company_keys(name: str, ignore_words: list[str]) -> set[str]:
    """The forms a company name is compared by.

    Two forms, so that "Eleven Labs" matches "ElevenLabs" and "Poly AI" matches
    "PolyAI": one with the ignored words removed and one with them kept, both
    with spaces removed.
    """
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    kept = [word for word in words if word not in ignore_words]
    return {"".join(kept), "".join(words)} - {""}


def tag_jobs(jobs: list[dict], connections: list[dict] | None, ignore_words: list[str]) -> list[dict]:
    """Add lead_type, connection and connection_title to every job."""
    if connections is None:
        log.info("Tier C: no connections file, tagging every job Cold")
        connections = []

    first_at = {}  # company key -> (row number, first connection there)
    for row, person in enumerate(connections):
        for key in company_keys(person.get("Company"), ignore_words):
            first_at.setdefault(key, (row, person))

    tagged = []
    for job in jobs:
        matches = [first_at[key] for key in company_keys(job["company"], ignore_words) if key in first_at]
        if matches:
            person = min(matches, key=lambda match: match[0])[1]
            name = f"{(person.get('First Name') or '').strip()} {(person.get('Last Name') or '').strip()}".strip()
            lead = {"lead_type": "Warm", "connection": name, "connection_title": (person.get("Position") or "").strip()}
        else:
            lead = {"lead_type": "Cold", "connection": "", "connection_title": ""}
        tagged.append({**job, **lead})
    return tagged


SLIM_COLUMNS = ["First Name", "Last Name", "Company", "Position"]


def _slim() -> int:
    """Write connections.slim.csv: only the four columns the pipeline reads, and only
    people with a company. Small enough to store as the CONNECTIONS_CSV GitHub secret."""
    root = Path(__file__).parent.parent
    connections = load_connections(root / "connections.csv")
    if not connections:
        print("connections.csv is missing or has no header row")
        return 1
    out = root / "connections.slim.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SLIM_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(person for person in connections if (person.get("Company") or "").strip())
    print(f"wrote {out.name}: {out.stat().st_size / 1024:.0f} KB (GitHub secrets hold up to 48 KB)")
    return 0


if __name__ == "__main__":
    sys.exit(_slim())
