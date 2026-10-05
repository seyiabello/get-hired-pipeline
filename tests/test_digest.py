"""Email digest: content, when it is sent, and what reaches the logs. No real SMTP."""

import smtplib
from datetime import date

import pytest

from pipeline import digest
from pipeline.digest import build_digest, group_by_level, send_digest
from pipeline.sheets import COLUMNS, plan_upsert

TODAY = date(2026, 10, 5)


def job(company, title, level="mid", **fields):
    base = {"company": company, "title": title, "level": level, "location": "London, United Kingdom",
            "url": f"https://jobs.example.com/{company}/{title}".replace(" ", "-").lower()}
    return {**base, **fields}


class FakeSMTP:
    sent = []
    logins = []
    error = None

    def __init__(self, host, port, timeout=None):
        self.address = (host, port)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def login(self, user, password):
        if FakeSMTP.error:
            raise FakeSMTP.error
        FakeSMTP.logins.append((self.address, user, password))

    def send_message(self, message):
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.error = [], [], None
    monkeypatch.setattr(digest.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.delenv("DIGEST_TO", raising=False)
    return FakeSMTP


# --- content ---

def test_groups_are_in_career_order_and_sorted_inside():
    jobs = [
        job("Zeta", "AI Engineer", "senior"),
        job("Beta", "ML Engineer", "mid"),
        job("alpha", "ML Engineer", "mid"),
        job("Alpha", "AI Engineer", "mid"),
        job("Gamma", "ML Intern", "intern"),
        job("Delta", "Junior ML Engineer", "junior"),
        job("Eta", "Graduate Engineer", "graduate"),
        job("Theta", "Odd One", "executive"),
    ]
    groups = group_by_level(jobs)
    assert [level for level, _ in groups] == ["intern", "graduate", "junior", "mid", "senior", "executive"]
    mid = dict(groups)["mid"]
    assert [(j["company"], j["title"]) for j in mid] == [("Alpha", "AI Engineer"), ("alpha", "ML Engineer"), ("Beta", "ML Engineer")]


def test_text_body_shows_company_title_location_salary_and_url():
    jobs = [
        job("ClaimSorted", "Product Engineer // AI", salary_min=80000, salary_max=150000, salary_currency="GBP"),
        job("Trainline", "Junior Machine Learning Engineer", "junior", salary_min="", salary_max="", salary_currency=""),
    ]
    subject, text, _ = build_digest(jobs, TODAY)
    assert subject == "Get Hired: 2 new jobs (2026-10-05)"
    assert text == (
        "Junior (1)\n"
        "----------\n"
        "Trainline: Junior Machine Learning Engineer\n"
        "  London, United Kingdom\n"
        "  https://jobs.example.com/trainline/junior-machine-learning-engineer\n"
        "\n"
        "Mid (1)\n"
        "-------\n"
        "ClaimSorted: Product Engineer // AI\n"
        "  London, United Kingdom | GBP 80,000 to 150,000\n"
        "  https://jobs.example.com/claimsorted/product-engineer-//-ai\n"
    )


def test_subject_for_one_job():
    assert build_digest([job("Acme", "AI Engineer")], TODAY)[0] == "Get Hired: 1 new job (2026-10-05)"


@pytest.mark.parametrize("fields, expected", [
    ({"salary_min": 60000, "salary_max": 60000, "salary_currency": "GBP"}, "GBP 60,000"),
    ({"salary_min": 45000, "salary_max": "", "salary_currency": "GBP"}, "GBP from 45,000"),
    ({"salary_min": "", "salary_max": 70000, "salary_currency": "GBP"}, "GBP up to 70,000"),
    ({"salary_min": 50000, "salary_max": 65000, "salary_currency": ""}, "50,000 to 65,000"),
    ({"salary_min": "", "salary_max": "", "salary_currency": "GBP"}, ""),
    ({}, ""),                                                    # Tier A jobs have no salary keys
])
def test_salary(fields, expected):
    assert digest._salary(job("Acme", "AI Engineer", **fields)) == expected


def test_blank_location_and_no_salary_leave_no_empty_line():
    _, text, page = build_digest([job("Acme", "AI Engineer", location="")], TODAY)
    assert "Acme: AI Engineer\n  https://" in text
    assert "<br>" not in page


def test_html_is_escaped_and_links_to_the_job():
    risky = job("A&B <Labs>", 'Engineer "<script>alert(1)</script>"', url="https://jobs.example.com/a?x=1&y=\"2\"")
    _, _, page = build_digest([risky], TODAY)
    assert "<script>" not in page
    assert "A&amp;B &lt;Labs&gt;" in page
    assert 'href="https://jobs.example.com/a?x=1&amp;y=&quot;2&quot;"' in page
    assert "<h3>Mid (1)</h3>" in page


# --- sending ---

def test_sends_one_email_to_yourself_by_default(smtp, caplog):
    with caplog.at_level("INFO"):
        assert send_digest([job("Acme", "AI Engineer")], TODAY) is True

    assert smtp.logins == [(("smtp.gmail.com", 465), "me@gmail.com", "abcdefghijklmnop")]  # spaces removed
    [message] = smtp.sent
    assert message["From"] == "me@gmail.com" and message["To"] == "me@gmail.com"
    assert message["Subject"] == "Get Hired: 1 new job (2026-10-05)"
    plain, page = message.iter_parts()
    assert plain.get_content_type() == "text/plain" and "Acme: AI Engineer" in plain.get_content()
    assert page.get_content_type() == "text/html" and "<b>Acme</b>" in page.get_content()
    assert "emailed 1 new jobs" in caplog.text


def test_digest_to_overrides_the_recipient(smtp, monkeypatch):
    monkeypatch.setenv("DIGEST_TO", "other@example.com")
    send_digest([job("Acme", "AI Engineer")], TODAY)
    assert smtp.sent[0]["To"] == "other@example.com"


def test_nothing_is_sent_when_there_are_no_new_jobs(smtp, caplog):
    with caplog.at_level("INFO"):
        assert send_digest([], TODAY) is False
    assert smtp.sent == [] and smtp.logins == []
    assert "no new jobs today" in caplog.text


@pytest.mark.parametrize("missing", ["GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"])
def test_skipped_when_a_gmail_variable_is_missing(smtp, monkeypatch, caplog, missing):
    monkeypatch.delenv(missing)
    with caplog.at_level("INFO"):
        assert send_digest([job("Acme", "AI Engineer")], TODAY) is False
    assert smtp.sent == []
    assert "skipped" in caplog.text


@pytest.mark.parametrize("error", [
    smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted abcd efgh ijkl mnop"),
    TimeoutError("timed out"),
])
def test_failure_raises_without_leaking_the_password(smtp, caplog, error):
    smtp.error = error
    with caplog.at_level("INFO"), pytest.raises(RuntimeError) as raised:
        send_digest([job("Acme", "AI Engineer")], TODAY)
    message = str(raised.value)
    assert type(error).__name__ in message
    for secret in ("abcd", "mnop", "abcdefghijklmnop"):
        assert secret not in message and secret not in caplog.text
    assert raised.value.__cause__ is None


# --- which jobs count as new today ---

def sheet_job(n):
    return {"company": "Acme", "title": f"AI Engineer {n}", "url": f"https://jobs.example.com/{n}"}


def test_new_today_is_rows_added_now_plus_rows_first_seen_earlier_today():
    values = [
        COLUMNS,
        [""] * len(COLUMNS),
        [""] * len(COLUMNS),
    ]
    url, first_seen = COLUMNS.index("url"), COLUMNS.index("first_seen")
    values[1][url], values[1][first_seen] = "https://jobs.example.com/1", "2026-10-04"   # yesterday
    values[2][url], values[2][first_seen] = "https://jobs.example.com/2", "2026-10-05"   # earlier today

    _, stats = plan_upsert(values, [sheet_job(1), sheet_job(2), sheet_job(3)], TODAY)

    assert stats["new"] == 1
    assert stats["new_today"] == ["https://jobs.example.com/2", "https://jobs.example.com/3"]


def test_first_run_on_an_empty_sheet_counts_everything_as_new_today():
    _, stats = plan_upsert([[]], [sheet_job(1), sheet_job(2)], TODAY)
    assert stats["new_today"] == ["https://jobs.example.com/1", "https://jobs.example.com/2"]


def test_rows_without_a_first_seen_value_are_not_new():
    values = [["url"], ["https://jobs.example.com/1"]]  # a sheet from before the column existed
    _, stats = plan_upsert(values, [sheet_job(1)], TODAY)
    assert stats["new_today"] == []
