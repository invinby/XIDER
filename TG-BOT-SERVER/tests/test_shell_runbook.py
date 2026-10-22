import importlib

import pytest

import shell_runbook as runbooks


@pytest.fixture(autouse=True)
def fresh_runbook_store():
    importlib.reload(runbooks)


def test_runbook_starts_only_after_owner_confirmation_and_tracks_exact_order():
    plan = runbooks.create_plan(
        requester_id=20, owner_id=10, device_id="device-1",
        text="printf first\nprintf second\nprintf third", now=1_800_000_000,
    )

    assert plan.steps == ("printf first", "printf second", "printf third")
    assert plan.state == "draft"
    assert runbooks.find_by_request("step-1", now=1_800_000_001) is None
    assert runbooks.confirm_plan(plan.plan_id, owner_id=9, now=1_800_000_001) is None

    confirmed = runbooks.confirm_plan(plan.plan_id, owner_id=10, now=1_800_000_001)
    assert confirmed.state == "confirmed"
    assert runbooks.bind_pending(plan.plan_id, "step-1", step_index=0, now=1_800_000_001)
    assert runbooks.find_by_request("step-1", now=1_800_000_002).steps == plan.steps

    next_step = runbooks.complete_step("step-1", success=True, now=1_800_000_003)
    assert (next_step.state, next_step.current_index, next_step.active_request_id) == ("ready_next", 1, None)
    assert runbooks.bind_pending(plan.plan_id, "step-2", step_index=1, now=1_800_000_004)
    stopped = runbooks.complete_step("step-2", success=False, now=1_800_000_005)
    assert stopped.state == "failed"
    assert stopped.current_index == 1
    assert runbooks.bind_pending(plan.plan_id, "step-3", step_index=2, now=1_800_000_006) is False


@pytest.mark.parametrize("text", [
    "", "one", "one\n\ntwo", "\none", "one\n", "\n".join(str(i) for i in range(9)),
    "я" * 3001,
])
def test_runbook_rejects_empty_unbounded_or_oversized_plans(text):
    with pytest.raises(ValueError):
        runbooks.parse_steps(text)


def test_runbook_expiry_rejects_confirmation_and_pending_step():
    plan = runbooks.create_plan(10, 10, "device-1", "echo one\necho two", now=100)

    assert runbooks.confirm_plan(plan.plan_id, owner_id=10, now=plan.expires_at) is None
    assert runbooks.find_by_request("step-1", now=plan.expires_at) is None


def test_runbook_report_records_terminal_outcome_duration_and_exit_code_without_commands():
    plan = runbooks.create_plan(10, 10, "device-1", "printf secret-one\nprintf secret-two", now=200)
    assert runbooks.confirm_plan(plan.plan_id, owner_id=10, now=201)
    assert runbooks.bind_pending(plan.plan_id, "step-1", step_index=0, now=202)
    assert runbooks.mark_started("step-1", now=205)

    finished = runbooks.complete_step("step-1", success=True, now=211, exit_code=0, outcome="completed")
    report = runbooks.format_report(finished)

    assert finished.step_results[0].duration_seconds == 6
    assert finished.step_results[0].outcome == "completed"
    assert finished.step_results[0].exit_code == 0
    assert "шаг 1" in report.lower()
    assert "6 с" in report
    assert "exit=0" in report
    assert "secret-one" not in report and "secret-two" not in report


def test_runbook_report_marks_unknown_result_and_unstarted_step_truthfully():
    plan = runbooks.create_plan(10, 10, "device-1", "echo one\necho two", now=300)
    runbooks.confirm_plan(plan.plan_id, owner_id=10, now=301)
    runbooks.bind_pending(plan.plan_id, "step-1", step_index=0, now=301)
    runbooks.mark_started("step-1", now=302)
    finished = runbooks.complete_step(
        "step-1", success=False, now=312, exit_code=None, outcome="result_timeout",
    )

    report = runbooks.format_report(finished)
    assert finished.step_results[0].duration_seconds == 10
    assert "result_timeout" in report
    assert "exit=н/д" in report
    assert "шаг 2" in report.lower()
    assert "не запускался" in report.lower()
