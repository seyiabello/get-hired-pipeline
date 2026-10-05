"""Fetching, ATS detection and failure isolation, with no real network calls."""

import httpx
import pytest

from conftest import load_fixture
from main import resolve_target, run_tier_a
from pipeline.fetchers import detect_ats, fetch_bamboohr, fetch_smartrecruiters


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.mark.parametrize("text, expected", [
    ("https://job-boards.greenhouse.io/monzo/jobs/8143930", ("greenhouse", "monzo")),
    ("https://boards.greenhouse.io/monzo", ("greenhouse", "monzo")),
    ("https://boards.greenhouse.io/embed/job_board?for=monzo", ("greenhouse", "monzo")),
    ("https://job-boards.eu.greenhouse.io/polyai/jobs/4965884101", ("greenhouse-eu", "polyai")),
    ("https://jobs.lever.co/spotify/2193db3f", ("lever", "spotify")),
    ("https://jobs.ashbyhq.com/cleo-2", ("ashby", "cleo-2")),
    ("https://apply.workable.com/starling-bank/", ("workable", "starling-bank")),
    ("https://channable.recruitee.com/o/some-job", ("recruitee", "channable")),
    ("https://jobs.smartrecruiters.com/Wise/744000153202878", ("smartrecruiters", "Wise")),
    ("https://holisticai.bamboohr.com/careers", ("bamboohr", "holisticai")),
    ('<a href="https://jobs.ashbyhq.com/cleo-2/abc">Apply</a>', ("ashby", "cleo-2")),
    ("https://apply.workable.com/j/0DA49B0B28", None),  # a job link carries no account slug
    ("https://example.com/careers", None),
])
def test_detect_ats(text, expected):
    assert detect_ats(text) == expected


def test_resolve_uses_csv_values_without_fetching():
    def handler(request):
        raise AssertionError("should not fetch")

    target = {"Company": "Monzo", "ATS": "Greenhouse", "Slug": "monzo", "Careers URL": ""}
    assert resolve_target(client_for(handler), target) == ("greenhouse", "monzo")


def test_resolve_detects_from_page_links():
    def handler(request):
        return httpx.Response(200, text='<a href="https://jobs.ashbyhq.com/cleo-2/abc">role</a>')

    target = {"Company": "Cleo", "ATS": "", "Slug": "", "Careers URL": "https://web.meetcleo.com/careers"}
    assert resolve_target(client_for(handler), target) == ("ashby", "cleo-2")


def test_resolve_detects_from_redirect():
    def handler(request):
        if request.url.host == "wayve.ai":
            return httpx.Response(302, headers={"location": "https://jobs.ashbyhq.com/wayve"})
        return httpx.Response(200, text="<html></html>")

    target = {"Company": "Wayve", "ATS": "", "Slug": "", "Careers URL": "https://wayve.ai/careers/join-us/"}
    assert resolve_target(client_for(handler), target) == ("ashby", "wayve")


def test_smartrecruiters_follows_pagination():
    postings = [{"id": str(i), "name": f"Job {i}"} for i in range(250)]

    def handler(request):
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json={"totalFound": 250, "content": postings[offset:offset + 100]})

    assert fetch_smartrecruiters(client_for(handler), "Wise")["content"] == postings


def test_bamboohr_attaches_detail_and_survives_a_failed_detail_call():
    def handler(request):
        if request.url.path == "/careers/list":
            return httpx.Response(200, json={"result": [{"id": "1"}, {"id": "2"}]})
        if request.url.path == "/careers/1/detail":
            return httpx.Response(200, json={"result": {"jobOpening": {"datePosted": "2026-09-17"}}})
        return httpx.Response(500)

    result = fetch_bamboohr(client_for(handler), "acme")["result"]
    assert result == [{"id": "1", "detail": {"datePosted": "2026-09-17"}}, {"id": "2", "detail": None}]


def test_one_failing_company_does_not_stop_the_run(caplog):
    def handler(request):
        url = str(request.url)
        if "badslug" in url:
            return httpx.Response(404, json={"error": "not found"})
        if "timeout" in url:
            raise httpx.ReadTimeout("timed out", request=request)
        if "nonjson" in url:
            return httpx.Response(200, text="<html>Just a moment...</html>")
        if "wrongshape" in url:
            return httpx.Response(200, json=["unexpected"])
        return httpx.Response(200, json=load_fixture("greenhouse"))

    targets = [
        {"Company": "Bad Slug", "ATS": "greenhouse", "Slug": "badslug", "Careers URL": ""},
        {"Company": "Timeout", "ATS": "greenhouse", "Slug": "timeout", "Careers URL": ""},
        {"Company": "Non JSON", "ATS": "greenhouse", "Slug": "nonjson", "Careers URL": ""},
        {"Company": "Wrong Shape", "ATS": "greenhouse", "Slug": "wrongshape", "Careers URL": ""},
        {"Company": "Unknown ATS", "ATS": "taleo", "Slug": "x", "Careers URL": ""},
        {"Company": "Nothing", "ATS": "", "Slug": "", "Careers URL": ""},
        {"Company": "Monzo", "ATS": "greenhouse", "Slug": "monzo", "Careers URL": ""},
    ]
    with caplog.at_level("WARNING"):
        jobs = run_tier_a(client_for(handler), targets)

    assert {j["company"] for j in jobs} == {"Monzo"}
    assert len(jobs) == len(load_fixture("greenhouse")["jobs"])
    warned = " ".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    for name in ["Bad Slug", "Timeout", "Non JSON", "Wrong Shape", "Unknown ATS", "Nothing"]:
        assert name in warned
