from datetime import date
from pathlib import Path

import pytest
import yaml

from pipeline.enrich import LevelDetector, age_days, enrich

ROOT = Path(__file__).parent.parent
TODAY = date(2026, 10, 2)


@pytest.fixture(scope="module")
def levels_config():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["levels"]


@pytest.mark.parametrize("title, expected", [
    ("Associate Software Engineer - Intern", "intern"),   # intern beats associate
    ("Machine Learning Internship", "intern"),
    ("Graduate Software Engineer", "graduate"),
    ("New Grad ML Engineer", "graduate"),
    ("Junior Data Scientist", "junior"),
    ("Jr. Platform Engineer", "junior"),
    ("Associate Data Scientist", "junior"),
    ("Entry-Level AI Engineer", "junior"),
    ("Software Engineer I", "junior"),
    ("Backend Engineer I, Payments", "junior"),
    ("Software Engineer II", "mid"),
    ("Backend Engineer III", "senior"),
    ("Senior Machine Learning Engineer", "senior"),
    ("Sr. AI Engineer", "senior"),
    ("Machine Learning Engineer", "mid"),
    ("AI Engineer", "mid"),                                # the I in AI is not a numeral
    ("Engineer, i18n", "mid"),                             # lowercase i is not a numeral
    ("Internal Tools Engineer", "mid"),                    # "internal" is not "intern"
    ("Postgraduate Research Scientist", "mid"),
    ("Seniority Systems Engineer", "mid"),
])
def test_level_from_title(levels_config, title, expected):
    assert LevelDetector(levels_config).level(title) == expected


def test_level_keywords_are_configurable():
    config = {"default": "unknown", "keywords": {"early": ["trainee", "I"], "late": ["veteran"]}}
    detector = LevelDetector(config)
    assert detector.level("Trainee Engineer") == "early"
    assert detector.level("Engineer I") == "early"
    assert detector.level("Veteran Engineer") == "late"
    assert detector.level("Senior Engineer") == "unknown"


@pytest.mark.parametrize("posted, expected", [
    ("2026-10-02T09:00:00+00:00", 0),
    ("2026-10-01T23:59:59+00:00", 1),
    ("2026-09-02T00:00:00+00:00", 30),
    ("2025-10-02T00:00:00+00:00", 365),
    ("2026-10-03T00:00:00+00:00", 0),   # a future date never goes negative
    ("", ""),
    ("not a date", ""),
])
def test_age_days(posted, expected):
    assert age_days(posted, TODAY) == expected


def test_enrich_adds_both_columns_without_changing_the_input(levels_config):
    job = {"title": "Senior AI Engineer", "posted": "2026-09-22T12:00:00+00:00"}
    assert enrich([job], levels_config, TODAY) == [{**job, "level": "senior", "age_days": 10}]
    assert "level" not in job


@pytest.mark.parametrize("seniority, title, expected", [
    ("Entry Level", "Senior AI Engineer", "junior"),          # the label wins over the title
    ("No Prior Experience Required", "AI Engineer", "graduate"),
    ("Mid Level", "Graduate AI Engineer", "mid"),
    ("Senior Level", "AI Engineer", "senior"),
    ("", "Senior AI Engineer", "senior"),                     # no label: fall back to the title
    ("Executive", "Junior AI Engineer", "junior"),            # label not in the map: fall back
])
def test_level_from_seniority_label(levels_config, seniority, title, expected):
    assert LevelDetector(levels_config).level(title, seniority) == expected


def test_enrich_uses_the_seniority_label_when_present(levels_config):
    job = {"title": "AI Engineer", "posted": "", "seniority": "Entry Level"}
    assert enrich([job], levels_config, TODAY)[0]["level"] == "junior"
