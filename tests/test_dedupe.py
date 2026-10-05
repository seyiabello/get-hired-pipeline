from pipeline.dedupe import dedupe as _dedupe
from pipeline.dedupe import title_keys

IGNORE = ["ltd", "ai", "labs", "the"]


def dedupe(tier_a, tier_b):
    return _dedupe(tier_a, tier_b, IGNORE)


def job(company, title, url, ats="ashby"):
    return {"company": company, "title": title, "url": url, "ats": ats}


def test_title_keys_ignore_case_and_extra_whitespace():
    assert title_keys(job("  Faculty ", "Machine  Learning Engineer ", "u"), IGNORE) == {"faculty|machine learning engineer"}


def test_tier_b_copy_is_dropped_when_the_company_is_written_differently():
    tier_a = [
        job("Faculty", "Machine Learning Engineer", "https://jobs.ashbyhq.com/faculty/1"),
        job("ElevenLabs", "Data Engineer", "https://jobs.ashbyhq.com/elevenlabs/1"),
    ]
    tier_b = [
        job("Faculty AI Ltd", "Machine Learning Engineer", "https://hiring.cafe/viewjob/1", "hiring.cafe"),
        job("Eleven Labs", "Data Engineer", "https://hiring.cafe/viewjob/2", "hiring.cafe"),
        job("Facultative", "Machine Learning Engineer", "https://hiring.cafe/viewjob/3", "hiring.cafe"),
    ]
    assert [j["url"] for j in dedupe(tier_a, tier_b)[2:]] == ["https://hiring.cafe/viewjob/3"]


def test_tier_a_keeps_separate_postings_with_the_same_title():
    tier_a = [
        job("Faculty", "Machine Learning Engineer", "https://jobs.example.com/1"),
        job("Faculty", "Machine Learning Engineer", "https://jobs.example.com/2"),
    ]
    assert dedupe(tier_a, []) == tier_a


def test_tier_a_drops_a_repeated_url_and_keeps_the_first():
    first = job("Faculty", "Machine Learning Engineer", "https://jobs.example.com/1")
    again = job("Faculty", "ML Engineer (edited)", "https://jobs.example.com/1")
    assert dedupe([first, again], []) == [first]


def test_tier_b_copy_of_a_tier_a_job_is_dropped():
    tier_a = [job("Faculty", "Machine Learning Engineer", "https://jobs.ashbyhq.com/faculty/1")]
    tier_b = [
        job("faculty", "Machine  learning engineer", "https://hiring.cafe/viewjob/abc", "hiring.cafe"),
        job("Faculty", "Data Engineer", "https://hiring.cafe/viewjob/def", "hiring.cafe"),
        job("Other Co", "Machine Learning Engineer", "https://hiring.cafe/viewjob/ghi", "hiring.cafe"),
    ]
    result = dedupe(tier_a, tier_b)
    assert [j["url"] for j in result] == [
        "https://jobs.ashbyhq.com/faculty/1",
        "https://hiring.cafe/viewjob/def",
        "https://hiring.cafe/viewjob/ghi",
    ]
    assert result[0]["ats"] == "ashby"  # the Tier A copy is the one kept


def test_tier_b_job_with_the_same_url_as_tier_a_is_dropped():
    tier_a = [job("Faculty", "Machine Learning Engineer", "https://jobs.ashbyhq.com/faculty/1")]
    tier_b = [job("Faculty AI", "ML Engineer", "https://jobs.ashbyhq.com/faculty/1", "hiring.cafe")]
    assert dedupe(tier_a, tier_b) == tier_a


def test_tier_b_keeps_same_title_postings_with_different_urls():
    tier_b = [
        job("Other Co", "AI Engineer", "https://hiring.cafe/viewjob/1", "hiring.cafe"),
        job("Other Co", "AI Engineer", "https://hiring.cafe/viewjob/2", "hiring.cafe"),
        job("Other Co", "AI Engineer", "https://hiring.cafe/viewjob/2", "hiring.cafe"),
    ]
    assert dedupe([], tier_b) == tier_b[:2]


def test_empty_inputs():
    assert dedupe([], []) == []
