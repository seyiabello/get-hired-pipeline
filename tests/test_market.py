"""Tier B: request shape, normalizing, market rules and the skip rules. No real network calls."""

import json

import httpx
import pytest

import main
from conftest import FIXTURES
from pipeline.filters import apply_filters
from pipeline.market import apply_market_rules, clean_title, fetch_market, needs_active_clearance, normalize_market
from pipeline.normalize import FIELDS

CONFIG = {
    "enabled": True,
    "actor": "blackfalcondata~hiringcafe-scraper",
    "queries": ["AI Engineer", "MLOps Engineer"],
    "country": "GB",
    "seniority_levels": ["Entry Level", "Mid Level"],
    "posted_within_days": 14,
    "sort_by": "date",
    "max_items": 10,
    "timeout_seconds": 300,
    "max_min_yoe": 3,
    "exclude_active_clearance": True,
}

# Field names as in the saved live response, cut down to the ones we read.
ITEM = {
    "jobId": "2c348e5768d733e3",
    "title": "AI Engineer",
    "company": "Acme Robotics",
    "companyName": "Acme Robotics Ltd",
    "location": "Leeds",
    "workplaceCountries": ["GB"],
    "isWorkplaceWorldwideOk": False,
    "applyUrl": "https://jobs.example.com/acme/43740",
    "portalUrl": "https://hiring.cafe/job/kac5xufceyhyds01",
    "postedDate": "2026-03-16T22:04:00.000Z",
    "seniorityLevel": "Entry Level",
    "minYearsExperience": 2,
    "salaryMin": 45000,
    "salaryMax": 60000,
    "salaryCurrency": "GBP",
    "securityClearance": None,
    "clearanceRequired": False,
}


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def normalized(**fields) -> dict:
    return normalize_market([{**ITEM, **fields}])[0]


# --- request ---

def test_request_shape():
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(201, json=[ITEM])

    assert fetch_market(client_for(handler), CONFIG, "secret-token", "AI Engineer") == [ITEM]

    request = seen["request"]
    assert request.method == "POST"
    assert request.url.path == "/v2/acts/blackfalcondata~hiringcafe-scraper/run-sync-get-dataset-items"
    assert "secret-token" not in str(request.url)
    assert request.headers["authorization"] == "Bearer secret-token"
    assert "maxItems" not in request.url.params  # it would cap spend below what 10 results cost
    assert json.loads(request.content) == {
        "jobDescriptionQuery": '"AI Engineer"',
        "country": "GB",
        "seniorityLevels": ["Entry Level", "Mid Level"],
        "postedWithinDays": 14,
        "sortBy": "date",
        "maxResults": 10,
        "includeDetails": False,
    }


# --- normalizing ---

def test_normalize_item():
    assert normalized() == {
        "company": "Acme Robotics",
        "ats": "hiring.cafe",
        "title": "AI Engineer",
        "location": "Leeds, United Kingdom",
        "url": "https://jobs.example.com/acme/43740",
        "posted": "2026-03-16T22:04:00+00:00",
        "ext_id": "2c348e5768d733e3",
        "seniority": "Entry Level",
        "min_yoe": 2,
        "salary_min": 45000,
        "salary_max": 60000,
        "salary_currency": "GBP",
        "clearance": "",
        "raw_title": "AI Engineer",
    }


def test_normalize_falls_back_when_fields_are_missing():
    item = {k: v for k, v in ITEM.items() if k not in ("company", "applyUrl")}
    job = normalize_market([item])[0]
    assert job["company"] == "Acme Robotics Ltd"
    assert job["url"] == "https://hiring.cafe/job/kac5xufceyhyds01"
    assert list(normalize_market([{}])[0])[:len(FIELDS)] == FIELDS


def test_currency_is_blank_when_no_salary_is_given():
    job = normalized(salaryMin=None, salaryMax=None)
    assert (job["salary_min"], job["salary_max"], job["salary_currency"]) == ("", "", "")
    assert normalized(salaryMin=None)["salary_currency"] == "GBP"


def test_zero_years_is_kept_as_zero_not_blank():
    assert normalized(minYearsExperience=0)["min_yoe"] == 0
    assert normalized(minYearsExperience=None)["min_yoe"] == ""


def test_clearance_column():
    assert normalized(securityClearance="Secret", clearanceRequired=True)["clearance"] == "Secret"
    assert normalized(securityClearance=None, clearanceRequired=True)["clearance"] == "Required"


@pytest.mark.parametrize("fields, expected", [
    ({"location": "Leeds", "workplaceCountries": ["GB"]}, "Leeds, United Kingdom"),
    ({"location": "London, United Kingdom", "workplaceCountries": ["GB"]}, "London, United Kingdom"),
    ({"location": "", "workplaceCountries": ["GB"]}, "United Kingdom"),
    ({"location": "Berlin", "workplaceCountries": ["DE"]}, "Berlin"),
    ({"location": "North America or Europe or Asia", "isWorkplaceWorldwideOk": True}, "Remote (Worldwide)"),
])
def test_location(fields, expected):
    assert normalized(**fields)["location"] == expected


def test_uk_and_worldwide_jobs_pass_the_tier_b_filter(filter_config):
    items = [
        ITEM,
        dict(ITEM, location="North America or Europe or Asia", workplaceCountries=[], isWorkplaceWorldwideOk=True),
        dict(ITEM, location="Berlin", workplaceCountries=["DE"]),
    ]
    kept = apply_filters(normalize_market(items), filter_config, "b")
    assert [j["location"] for j in kept] == ["Leeds, United Kingdom", "Remote (Worldwide)"]


@pytest.mark.parametrize("raw, expected", [
    ("Senior Civil Engineer – Water Engineering in Leeds, West Yorkshire, United Kingdom", "Senior Civil Engineer – Water Engineering"),
    ("AI Engineer in London, United Kingdom", "AI Engineer"),
    ("ML Engineer in Bristol, City of Bristol, UK", "ML Engineer"),
    ("🌎 AI Engineer, Remote - Contract", "AI Engineer, Remote - Contract"),
    ("(1556) Lead Data and Insight Engineer ", "Lead Data and Insight Engineer"),
    ("[204] MLOps  Engineer 🚀", "MLOps Engineer"),
    ("AI Engineer | Public Sector Consulting", "AI Engineer | Public Sector Consulting"),
    ("Engineer in Residence", "Engineer in Residence"),               # no location, left alone
    ("Engineer (3) - Platform", "Engineer (3) - Platform"),           # only a leading number goes
    ("Specialist in machine learning, United Kingdom", "Specialist in machine learning, United Kingdom"),
    (None, ""),
])
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


# --- market rules ---

@pytest.mark.parametrize("title", [
    "Data Engineer - Advanced Analytics | Active eSC/eDV Required",
    "DevOps Engineer - Public Sector | Active eSC/eDV Clearance Required",
    "AI Engineer (active SC clearance)",
    "ML Engineer - Existing DV Clearance",
    "Platform Engineer, must hold DV",
    "SC Cleared DevOps Engineer",
    "DevOps Engineer - DV Cleared",
    "Cloud Engineer (SC/DV Cleared)",
])
def test_active_clearance_is_detected(title):
    assert needs_active_clearance({"title": title, "clearance": "Secret"})


@pytest.mark.parametrize("title", [
    "DevOps Engineer - SC Clearance",            # names the level, does not say you must hold it
    "AI Engineer - SC Eligible",
    "ML Engineer (eligible for SC clearance)",
    "AI Engineer | Public Sector Consulting",
    "Active Directory Security Engineer",
    "Current Account Platform Engineer",
    "Machine Learning Engineer",
])
def test_eligibility_and_ordinary_titles_are_kept(title):
    assert not needs_active_clearance({"title": title, "clearance": "Secret"})


def test_clearance_is_read_from_the_title_before_cleaning_and_from_the_field():
    assert needs_active_clearance({"title": "AI Engineer", "raw_title": "AI Engineer, Active SC", "clearance": ""})
    assert needs_active_clearance({"title": "AI Engineer", "clearance": "Active DV clearance"})


def test_market_rules():
    jobs = [
        {"title": "AI Engineer", "min_yoe": "", "clearance": ""},                 # no figure stated: kept
        {"title": "AI Engineer", "min_yoe": 0, "clearance": ""},
        {"title": "AI Engineer", "min_yoe": 3, "clearance": ""},                  # at the limit: kept
        {"title": "AI Engineer", "min_yoe": 4, "clearance": ""},
        {"title": "AI Engineer - Active SC Required", "min_yoe": 1, "clearance": "Secret"},
        {"title": "AI Engineer - SC Clearance", "min_yoe": 1, "clearance": "Secret"},
    ]
    assert apply_market_rules(jobs, CONFIG) == [jobs[0], jobs[1], jobs[2], jobs[5]]
    relaxed = dict(CONFIG, max_min_yoe=5, exclude_active_clearance=False)
    assert apply_market_rules(jobs, relaxed) == jobs


def test_saved_live_response_normalizes():
    path = FIXTURES / "hiring_cafe.json"
    if not path.exists():
        pytest.skip("run `python -m pipeline.market` once to save a live response")
    jobs = normalize_market(json.loads(path.read_text(encoding="utf-8")))
    assert jobs
    for job in jobs:
        assert job["ats"] == "hiring.cafe"
        assert job["company"] and job["title"] and job["ext_id"]
        assert job["url"].startswith("http")
        assert "united kingdom" in job["location"].lower()


# --- running the tier ---

def test_one_run_per_query_and_a_failed_query_does_not_stop_the_rest(monkeypatch, caplog):
    monkeypatch.setenv("APIFY_TOKEN", "secret-token")
    queries = []

    def handler(request):
        query = json.loads(request.content)["jobDescriptionQuery"].strip('"')
        queries.append(query)
        if query == "AI Engineer":
            return httpx.Response(402, json={"error": {"message": "limit reached"}})
        return httpx.Response(201, json=[ITEM])

    with caplog.at_level("INFO"):
        jobs = main.run_tier_b(client_for(handler), CONFIG)

    assert queries == ["AI Engineer", "MLOps Engineer"]
    assert [j["title"] for j in jobs] == ["AI Engineer"]
    assert "'AI Engineer' failed" in caplog.text
    assert "'MLOps Engineer' returned 1 jobs (asked for up to 10)" in caplog.text
    assert "secret-token" not in caplog.text


def test_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "secret-token")

    def handler(request):
        raise AssertionError("should not call Apify")

    assert main.run_tier_b(client_for(handler), dict(CONFIG, enabled=False)) == []


def test_skipped_without_token(monkeypatch, caplog):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)

    def handler(request):
        raise AssertionError("should not call Apify")

    with caplog.at_level("WARNING"):
        assert main.run_tier_b(client_for(handler), CONFIG) == []
    assert "APIFY_TOKEN is not set" in caplog.text


@pytest.mark.parametrize("response", [
    httpx.Response(402, json={"error": {"type": "not-enough-usage", "message": "limit reached"}}),
    httpx.Response(201, json={"error": "not a list"}),
    httpx.Response(201, text="<html>"),
])
def test_failure_logs_a_warning_and_never_leaks_the_token(monkeypatch, caplog, response):
    monkeypatch.setenv("APIFY_TOKEN", "secret-token")
    with caplog.at_level("INFO"):
        assert main.run_tier_b(client_for(lambda request: response), CONFIG) == []
    assert "failed, continuing without it" in caplog.text
    assert "secret-token" not in caplog.text


def test_timeout_does_not_stop_the_run(monkeypatch, caplog):
    monkeypatch.setenv("APIFY_TOKEN", "secret-token")

    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with caplog.at_level("WARNING"):
        assert main.run_tier_b(client_for(handler), CONFIG) == []
    assert "secret-token" not in caplog.text
