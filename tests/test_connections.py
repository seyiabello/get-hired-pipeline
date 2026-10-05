from pathlib import Path

import pytest
import yaml

from conftest import FIXTURES
from pipeline.connections import company_keys, load_connections, tag_jobs

ROOT = Path(__file__).parent.parent
IGNORE = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["connections"]["ignore_words"]


@pytest.fixture(scope="module")
def connections():
    return load_connections(FIXTURES / "connections.csv")  # fake people, real export layout


def tag(company, connections):
    return tag_jobs([{"company": company, "title": "AI Engineer"}], connections, IGNORE)[0]


def test_note_lines_before_the_header_are_skipped(connections):
    assert len(connections) == 8
    assert connections[0]["First Name"] == "Ada"
    assert connections[7]["Position"] == "Engineer, Dialogue"  # quoted comma survives


def test_missing_file_returns_none(tmp_path):
    assert load_connections(tmp_path / "connections.csv") is None


def test_file_without_a_header_gives_no_connections(tmp_path):
    path = tmp_path / "connections.csv"
    path.write_text("Notes:\nnothing useful here\n", encoding="utf-8")
    assert load_connections(path) == []


@pytest.mark.parametrize("a, b", [
    ("Cleo", "Cleo AI, Ltd."),
    ("Cleo", "The Cleo Company"),
    ("Faculty", "The Faculty Group PLC"),
    ("ElevenLabs", "Eleven Labs"),
    ("PolyAI", "Poly AI"),
    ("Holistic AI", "holistic"),
    ("Wayve", "Wayve Technologies Limited"),
    ("Acme", "ACME, Inc."),
    ("Acme", "Acme Corp"),
    ("Acme", "Acme LLC"),
    ("Monzo", "Monzo Bank"),
])
def test_company_names_that_match(a, b):
    assert company_keys(a, IGNORE) & company_keys(b, IGNORE)


@pytest.mark.parametrize("a, b", [
    ("Synthesia", "Synthesia Studios"),
    ("Cleo", "Cleopatra Ltd"),          # no partial matches
    ("Faculty", "Facultative"),
    ("AI", "The AI Company"),           # names made only of ignored words differ
    ("", "Ltd"),
])
def test_company_names_that_do_not_match(a, b):
    assert not company_keys(a, IGNORE) & company_keys(b, IGNORE)


def test_blank_company_has_no_keys():
    assert company_keys("", IGNORE) == set()
    assert company_keys(None, IGNORE) == set()


def test_warm_uses_the_first_matching_connection(connections):
    job = tag("Cleo", connections)
    # Ben (row 2) and Dana (row 4) are both at Cleo; the first in the file wins.
    assert (job["lead_type"], job["connection"], job["connection_title"]) == ("Warm", "Ben Hughes", "Machine Learning Engineer")


@pytest.mark.parametrize("company, name", [
    ("ElevenLabs", "Chidi Evans"),
    ("Faculty", "Femi Clarke"),
    ("PolyAI", "Hari Lowe"),
    ("Monzo", "Ada Okafor"),
])
def test_warm_matches(connections, company, name):
    job = tag(company, connections)
    assert (job["lead_type"], job["connection"]) == ("Warm", name)


@pytest.mark.parametrize("company", ["Synthesia", "Wayve", ""])
def test_cold_when_nobody_matches(connections, company):
    job = tag(company, connections)
    assert (job["lead_type"], job["connection"], job["connection_title"]) == ("Cold", "", "")


def test_connection_without_a_company_never_matches(connections):
    assert tag("Freelance", connections)["lead_type"] == "Cold"


def test_no_connections_file_tags_everything_cold_and_logs(caplog):
    jobs = [{"company": "Cleo", "title": "AI Engineer"}, {"company": "Monzo", "title": "ML Engineer"}]
    with caplog.at_level("INFO"):
        tagged = tag_jobs(jobs, None, IGNORE)
    assert [j["lead_type"] for j in tagged] == ["Cold", "Cold"]
    assert "no connections file" in caplog.text


def test_ignore_words_are_configurable(connections):
    job = {"company": "Synthesia", "title": "x"}
    assert tag_jobs([job], connections, IGNORE)[0]["lead_type"] == "Cold"
    assert tag_jobs([job], connections, IGNORE + ["studios"])[0]["connection"] == "Gwen Marsh"


def test_input_jobs_are_not_modified(connections):
    job = {"company": "Cleo", "title": "AI Engineer"}
    tag_jobs([job], connections, IGNORE)
    assert "lead_type" not in job
