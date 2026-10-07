"""The guard that picks which trigger does the day's work, and the workflow file around it."""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

from pipeline import schedule_guard
from pipeline.schedule_guard import PIPELINE_STEP, WORKFLOW_FILE, decide, did_the_work, london_date, runs_that_worked

ROOT = Path(__file__).parent.parent
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / WORKFLOW_FILE).read_text(encoding="utf-8"))


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


# --- decide ---

@pytest.mark.parametrize("now, expected", [
    ("2026-10-07T06:07", True),    # summer: 06:07 UTC is 07:07 in London
    ("2026-10-07T05:59", False),   # 06:59 in London, too early
    ("2026-10-07T09:40", True),    # a trigger that arrives hours late still goes ahead
    ("2026-10-07T22:30", True),    # even very late
    ("2026-12-01T06:07", False),   # winter: 06:07 UTC is 06:07 in London
    ("2026-12-01T07:07", True),    # winter: 07:07 in London
])
def test_first_trigger_at_or_after_seven_london_runs(now, expected):
    assert decide("schedule", False, utc(now), [])[0] is expected


def test_later_triggers_skip_once_a_run_has_done_the_work_today():
    go, reason = decide("schedule", False, utc("2026-10-07T07:07"), ["2026-10-07T06:07:31Z"])
    assert go is False and "already run today" in reason


def test_yesterdays_run_does_not_count():
    assert decide("schedule", False, utc("2026-10-07T06:07"), ["2026-10-06T06:07:31Z"])[0] is True


def test_today_means_the_london_date_not_the_utc_date():
    # 23:30 UTC on the 6th is 00:30 on the 7th in London (summer time).
    assert london_date("2026-10-06T23:30:00Z") == date(2026, 10, 7)
    assert decide("schedule", False, utc("2026-10-07T08:07"), ["2026-10-06T23:30:00Z"])[0] is False
    # 22:30 UTC on the 6th is still the 6th in London.
    assert decide("schedule", False, utc("2026-10-07T08:07"), ["2026-10-06T22:30:00Z"])[0] is True


@pytest.mark.parametrize("now, worked, expected", [
    ("2026-10-07T06:20", [], True),                           # backup trigger, GitHub's schedule never fired
    ("2026-10-07T06:20", ["2026-10-07T06:07:31Z"], False),    # GitHub's schedule already did it
    ("2026-10-07T05:30", [], False),                          # too early
])
def test_unforced_dispatch_is_treated_like_a_scheduled_trigger(now, worked, expected):
    assert decide("workflow_dispatch", False, utc(now), worked) == decide("schedule", False, utc(now), worked)
    assert decide("workflow_dispatch", False, utc(now), worked)[0] is expected


@pytest.mark.parametrize("now, worked", [
    ("2026-10-07T03:00", []),                                 # before 07:00
    ("2026-10-07T14:00", ["2026-10-07T06:07:31Z"]),           # already ran today
])
def test_force_always_runs(now, worked):
    assert decide("workflow_dispatch", True, utc(now), worked) == (True, "force is set, running")


def test_other_events_are_not_guarded():
    assert decide("push", False, utc("2026-10-07T03:00"), ["2026-10-07T01:00:00Z"])[0] is True


# --- which earlier runs count ---

def jobs(pipeline_conclusion):
    return [{"steps": [
        {"name": "Decide whether this trigger should run", "conclusion": "success"},
        {"name": PIPELINE_STEP, "conclusion": pipeline_conclusion},
    ]}]


def test_a_run_that_skipped_does_not_count_as_having_done_the_work():
    assert did_the_work(jobs("success")) is True
    assert did_the_work(jobs("skipped")) is False
    assert did_the_work(jobs("failure")) is False
    assert did_the_work([{"steps": None}]) is False
    assert did_the_work([]) is False


def test_runs_that_worked_looks_only_at_todays_other_runs():
    calls = []
    runs = {"workflow_runs": [
        {"id": 5, "run_started_at": "2026-10-07T07:07:10Z"},   # this run itself
        {"id": 4, "run_started_at": "2026-10-07T06:07:31Z"},   # worked
        {"id": 3, "run_started_at": "2026-10-07T05:07:02Z"},   # skipped (too early)
        {"id": 2, "run_started_at": "2026-10-06T06:07:44Z"},   # yesterday, never fetched
        {"id": 1, "created_at": "2026-10-07T06:30:00Z"},       # no run_started_at: falls back to created_at
    ]}
    job_lists = {4: jobs("success"), 3: jobs("skipped"), 1: jobs("success")}

    def get(path, token):
        calls.append(path)
        assert token == "tok"
        if path.endswith("/jobs"):
            return {"jobs": job_lists[int(path.split("/")[-2])]}
        return runs

    worked = runs_that_worked("me/repo", "tok", date(2026, 10, 7), "5", get=get)

    assert worked == ["2026-10-07T06:07:31Z", "2026-10-07T06:30:00Z"]
    assert calls[0] == f"/repos/me/repo/actions/workflows/{WORKFLOW_FILE}/runs?status=success&per_page=30"
    assert calls[1:] == ["/repos/me/repo/actions/runs/4/jobs", "/repos/me/repo/actions/runs/3/jobs", "/repos/me/repo/actions/runs/1/jobs"]


# --- main: outputs, and failing safe ---

@pytest.fixture
def actions_env(monkeypatch, tmp_path):
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_REPOSITORY", "me/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_RUN_ID", "5")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.delenv("FORCE", raising=False)
    return output


class Clock(datetime):
    fixed = utc("2026-10-07T06:07")

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


@pytest.mark.parametrize("worked, expected", [([], "go=true\n"), (["2026-10-07T05:10:00Z"], "go=false\n")])
def test_main_writes_the_output(actions_env, monkeypatch, capsys, worked, expected):
    monkeypatch.setattr(schedule_guard, "datetime", Clock)
    monkeypatch.setattr(schedule_guard, "runs_that_worked", lambda *args: worked)
    assert schedule_guard.main() == 0
    assert actions_env.read_text() == expected
    assert "Guard:" in capsys.readouterr().out


def test_main_runs_when_the_lookup_fails_and_does_not_print_the_token(actions_env, monkeypatch, capsys):
    def broken(*args):
        raise OSError("secret-token leaked in a url")

    monkeypatch.setattr(schedule_guard, "datetime", Clock)
    monkeypatch.setattr(schedule_guard, "runs_that_worked", broken)
    assert schedule_guard.main() == 0
    assert actions_env.read_text() == "go=true\n"
    out = capsys.readouterr().out
    assert "::warning::could not check earlier runs (OSError)" in out
    assert "secret-token" not in out


@pytest.mark.parametrize("value, forced", [("true", True), ("True", True), ("false", False), ("", False)])
def test_main_reads_the_force_input(actions_env, monkeypatch, value, forced):
    monkeypatch.setattr(schedule_guard, "datetime", Clock)
    monkeypatch.setenv("FORCE", value)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    lookups = []
    monkeypatch.setattr(schedule_guard, "runs_that_worked", lambda *args: lookups.append(1) or ["2026-10-07T05:10:00Z"])
    schedule_guard.main()
    assert actions_env.read_text() == ("go=true\n" if forced else "go=false\n")
    assert lookups == ([] if forced else [1])  # a forced run does not need the lookup


def test_guard_imports_only_the_standard_library():
    # It runs before `pip install`, so it must not need anything from requirements.txt.
    source = (ROOT / "pipeline" / "schedule_guard.py").read_text(encoding="utf-8")
    imported = {line.split()[1].split(".")[0] for line in source.splitlines() if line.startswith(("import ", "from "))}
    assert imported == {"json", "os", "sys", "urllib", "datetime", "zoneinfo"}


# --- the workflow file agrees with the guard ---

STEPS = WORKFLOW["jobs"]["run"]["steps"]
ON = WORKFLOW[True]  # YAML reads the key "on" as the boolean true


def test_workflow_keeps_the_four_crons():
    assert [entry["cron"] for entry in ON["schedule"]] == ["7 6 * * *", "7 7 * * *", "7 8 * * *", "7 9 * * *"]


def test_workflow_has_a_force_input_that_is_off_by_default():
    force = ON["workflow_dispatch"]["inputs"]["force"]
    assert force["type"] == "boolean" and force["default"] is False


def test_guard_step_runs_right_after_checkout_and_gets_what_it_needs():
    assert STEPS[0] == {"uses": "actions/checkout@v4"}
    guard = STEPS[1]
    assert guard["id"] == "clock"
    assert guard["run"] == "python3 -m pipeline.schedule_guard"
    assert guard["env"] == {"GITHUB_TOKEN": "${{ github.token }}", "FORCE": "${{ inputs.force }}"}
    assert WORKFLOW["permissions"]["actions"] == "read"


def test_every_later_step_waits_for_the_guard():
    for step in STEPS[2:]:
        assert "steps.clock.outputs.go == 'true'" in step["if"], step


def test_the_step_name_the_guard_looks_for_exists_in_the_workflow():
    assert [step.get("name") for step in STEPS].count(PIPELINE_STEP) == 1
