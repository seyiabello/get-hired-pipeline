"""Get Hired pipeline: pull job listings into a Google Sheet.

Usage:
    python main.py              (needs SHEET_ID and Google credentials, see README)
    python main.py --dry-run
    python main.py --dry-run --company Cleo
"""

import argparse
import csv
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from pipeline.connections import load_connections, tag_jobs
from pipeline.dedupe import dedupe
from pipeline.digest import send_digest
from pipeline.enrich import enrich
from pipeline.fetchers import FETCHERS, detect_from_careers_url
from pipeline.filters import apply_filters
from pipeline.market import apply_market_rules, estimate_cost, fetch_market, normalize_market
from pipeline.normalize import normalize
from pipeline.sheets import JOB_COLUMNS, open_worksheet, upsert

ROOT = Path(__file__).parent
log = logging.getLogger("get-hired")


def load_targets(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def resolve_target(client: httpx.Client, target: dict) -> tuple[str, str]:
    """Return (ats, slug), detecting them from the Careers URL if either is blank."""
    ats, slug = target.get("ATS", "").lower(), target.get("Slug", "")
    if not (ats and slug):
        url = target.get("Careers URL", "")
        if not url:
            raise ValueError("no ATS/Slug and no Careers URL")
        found = detect_from_careers_url(client, url)
        if not found:
            raise ValueError(f"no supported ATS found at {url}")
        ats, slug = found
        log.info("%s: detected %s/%s", target["Company"], ats, slug)
    if ats not in FETCHERS:
        raise ValueError(f"unsupported ATS {ats!r}")
    return ats, slug


def run_tier_a(client: httpx.Client, targets: list[dict]) -> list[dict]:
    jobs = []
    for target in targets:
        company = target["Company"]
        try:
            ats, slug = resolve_target(client, target)
            found = normalize(ats, FETCHERS[ats](client, slug), company, slug)
        except Exception as exc:  # one bad company must never stop the run
            log.warning("%s failed, skipping: %s", company, exc)
            continue
        log.info("%s: %d jobs fetched", company, len(found))
        jobs.extend(found)
    return jobs


def run_tier_b(client: httpx.Client, config: dict) -> tuple[list[dict], dict[str, int]]:
    """Market search. Returns (jobs, results returned per query that ran).

    Both are empty when Tier B is switched off or there is no token. A query that
    fails is logged and left out.
    """
    if not config.get("enabled"):
        log.info("Tier B: skipped (market.enabled is false)")
        return [], {}
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        log.warning("Tier B: skipped, APIFY_TOKEN is not set")
        return [], {}
    found, counts = [], {}
    for query in config["queries"]:
        try:
            items = fetch_market(client, config, token, query)
        except Exception as exc:  # one bad query must never stop the run
            log.warning("Tier B: %r failed, continuing without it: %s", query, exc)
            continue
        log.info("Tier B: %r returned %d jobs (asked for up to %d)", query, len(items), config["max_items"])
        counts[query] = len(items)
        found.extend(normalize_market(items))
    return found, counts


def log_cost(counts: dict[str, int], config: dict) -> None:
    if not counts:
        return
    total, per_query = estimate_cost(counts, config)
    log.info("Apify estimated cost: $%.3f for %d queries, %d results", total, len(counts), sum(counts.values()))
    for query, cost in per_query.items():
        log.info("  $%.3f  %-28s %2d results", cost, query, counts[query])


def write_csv(jobs: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=JOB_COLUMNS, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(jobs)


def print_jobs(jobs: list[dict]) -> None:
    for job in jobs:
        print(
            f"{job['company'][:14]:<14} {job['title'][:48]:<48} {job['level']:<8} "
            f"{job['location'][:24]:<24} {job['age_days']:>4}d  {job['lead_type']}"
        )
        contact = f"   via {job['connection']} ({job['connection_title']})" if job["connection"] else ""
        print(f"{'':<14} {job['url']}{contact}")
    print(f"\n{len(jobs)} jobs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print results and write jobs.csv only, do not touch Sheets")
    parser.add_argument("--company", help="only run this company from targets.csv")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc).date()

    targets = load_targets(ROOT / "targets.csv")
    if args.company:
        targets = [t for t in targets if t["Company"].lower() == args.company.lower()]
        if not targets:
            log.error("no company named %r in targets.csv", args.company)
            return 1

    headers = {"User-Agent": "get-hired-pipeline/1.0"}
    with httpx.Client(timeout=config["http"]["timeout_seconds"], headers=headers, follow_redirects=True) as client:
        fetched = run_tier_a(client, targets)
        market, counts = ([], {}) if args.company else run_tier_b(client, config["market"])
    tier_a = apply_filters(fetched, config["filters"], tier="a")
    log.info("Tier A: kept %d of %d jobs after filters", len(tier_a), len(fetched))
    tier_b = apply_market_rules(apply_filters(market, config["filters"], tier="b"), config["market"])
    if market:
        log.info("Tier B: kept %d of %d jobs after filters", len(tier_b), len(market))

    ignore_words = config["connections"]["ignore_words"]
    jobs = dedupe(tier_a, tier_b, ignore_words)
    log.info("Dedupe: %d jobs, %d repeats dropped", len(jobs), len(tier_a) + len(tier_b) - len(jobs))
    jobs = enrich(jobs, config["levels"], today)
    connections = load_connections(ROOT / config["connections"]["file"])
    jobs = tag_jobs(jobs, connections, ignore_words)
    log.info("Tier C: %d Warm, %d Cold", sum(j["lead_type"] == "Warm" for j in jobs), sum(j["lead_type"] == "Cold" for j in jobs))

    write_csv(jobs, ROOT / "jobs.csv")
    log.info("wrote jobs.csv")
    if args.dry_run:
        print_jobs(jobs)
        log_cost(counts, config["market"])
        return 0

    try:
        stats = upsert(open_worksheet(config["sheets"]["tab"]), jobs, today)
        log.info("Sheets: %d new rows, %d updated", stats["new"], stats["updated"])
    except Exception as exc:
        log.error("Sheets update failed: %s", exc)
        return 1
    finally:
        log_cost(counts, config["market"])

    if not config["digest"]["enabled"]:
        return 0
    new_today = set(stats["new_today"])
    try:
        send_digest([job for job in jobs if job["url"] in new_today], today)
    except RuntimeError as exc:
        log.error("Digest: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
