from datetime import datetime

import pytest

from conftest import load_fixture
from pipeline.normalize import FIELDS, NORMALIZERS, _iso, normalize

# Fixtures are real responses, cut down to the first 8 jobs (BambooHR: all jobs, short descriptions).
SLUGS = {
    "bamboohr": "holisticai",
    "greenhouse": "monzo",
    "greenhouse-eu": "polyai",
    "ashby": "cleo-2",
    "lever": "spotify",
    "workable": "starling-bank",
    "recruitee": "channable",
    "smartrecruiters": "Wise",
}


def test_every_ats_has_a_fixture():
    assert set(SLUGS) == set(NORMALIZERS)


@pytest.mark.parametrize("ats", sorted(SLUGS))
def test_fixture_maps_to_shared_schema(ats):
    jobs = normalize(ats, load_fixture(ats), "Acme", SLUGS[ats])
    assert jobs
    for job in jobs:
        assert list(job) == FIELDS
        assert job["company"] == "Acme"
        assert job["ats"] == ats
        assert job["title"] and job["title"] == job["title"].strip()
        assert job["url"].startswith("https://")
        assert job["ext_id"] and job["ext_id"] != "None"
        assert job["location"]  # every fixture job has one; blank is only allowed in principle
        assert datetime.fromisoformat(job["posted"]).utcoffset().total_seconds() == 0


def test_greenhouse_prefers_first_published():
    raw = load_fixture("greenhouse")["jobs"][0]
    job = normalize("greenhouse", {"jobs": [raw]}, "Monzo", "monzo")[0]
    assert job["posted"] == _iso(raw["first_published"])
    assert job["location"] == raw["location"]["name"].strip()
    assert job["url"] == raw["absolute_url"]


def test_greenhouse_falls_back_to_updated_at():
    raw = dict(load_fixture("greenhouse")["jobs"][0], first_published=None)
    job = normalize("greenhouse", {"jobs": [raw]}, "Monzo", "monzo")[0]
    assert job["posted"] == _iso(raw["updated_at"])


def test_lever_converts_ms_epoch():
    raw = load_fixture("lever")[0]
    job = normalize("lever", [raw], "Spotify", "spotify")[0]
    assert datetime.fromisoformat(job["posted"]).timestamp() == raw["createdAt"] // 1000
    assert job["title"] == raw["text"].strip()
    assert job["url"] == raw["hostedUrl"]


def test_ashby_skips_unlisted_and_falls_back_to_apply_url():
    raw = load_fixture("ashby")["jobs"][0]
    hidden = dict(raw, isListed=False)
    no_job_url = dict(raw, jobUrl=None)
    jobs = normalize("ashby", {"jobs": [hidden, no_job_url]}, "Cleo", "cleo-2")
    assert [j["url"] for j in jobs] == [raw["applyUrl"]]


def test_workable_location_from_city_and_country():
    raw = load_fixture("workable")["jobs"][0]
    job = normalize("workable", {"jobs": [raw]}, "Starling", "starling-bank")[0]
    assert job["location"].startswith(f"{raw['city']}, {raw['country']}")
    assert job["ext_id"] == raw["shortcode"]


def test_recruitee_date_format():
    assert _iso("2026-09-18 13:27:28 UTC") == "2026-09-18T13:27:28+00:00"


def test_smartrecruiters_builds_url():
    raw = load_fixture("smartrecruiters")["content"][0]
    job = normalize("smartrecruiters", {"content": [raw]}, "Wise", "Wise")[0]
    assert job["url"] == f"https://jobs.smartrecruiters.com/Wise/{raw['id']}"


def test_smartrecruiters_gb_becomes_united_kingdom():
    raw = {"id": "1", "name": "AI Engineer", "location": {"city": "Leeds", "country": "gb"}}
    job = normalize("smartrecruiters", {"content": [raw]}, "Wise", "Wise")[0]
    assert job["location"] == "Leeds, United Kingdom"


def test_missing_optional_fields_do_not_crash():
    job = normalize("greenhouse", {"jobs": [{"id": 1, "title": "Engineer"}]}, "Acme", "acme")[0]
    assert job["location"] == "" and job["posted"] == "" and job["url"] == ""


def test_bamboohr_uses_detail_for_date_and_country():
    data = load_fixture("bamboohr")
    raw = next(j for j in data["result"] if j["location"]["city"] == "London")
    job = normalize("bamboohr", {"result": [raw]}, "Holistic AI", "holisticai")[0]
    assert job["location"] == "London, United Kingdom"
    assert job["posted"] == _iso(raw["detail"]["datePosted"])
    assert job["url"] == f"https://holisticai.bamboohr.com/careers/{raw['id']}"


def test_bamboohr_without_detail_still_maps():
    raw = dict(load_fixture("bamboohr")["result"][0], detail=None)
    job = normalize("bamboohr", {"result": [raw]}, "Holistic AI", "holisticai")[0]
    assert job["title"] and job["posted"] == "" and job["location"] == "London"
