"""Bounded in-memory runbook plans for owner-approved shell steps."""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import threading


MAX_STEPS = 8
MAX_PLAN_BYTES = 3000
PLAN_TTL_SECONDS = 600
MAX_PLANS = 100
MAX_DEVICE_ID_BYTES = 256


class RunbookStoreFull(ValueError):
    """Raised when the bounded process-local runbook store is at capacity."""


@dataclass(frozen=True)
class StepResult:
    step_index: int
    outcome: str
    duration_seconds: int | None
    exit_code: int | None


@dataclass(frozen=True)
class Runbook:
    plan_id: str
    requester_id: int
    owner_id: int
    device_id: str
    steps: tuple[str, ...]
    created_at: int
    expires_at: int
    state: str
    current_index: int
    active_request_id: str | None
    step_states: tuple[str, ...]
    step_started_at: tuple[int | None, ...]
    step_results: tuple[StepResult | None, ...]


_PLANS: dict[str, Runbook] = {}
_REQUESTS: dict[str, str] = {}
_LOCK = threading.RLock()


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _timestamp(value: object) -> bool:
    return type(value) is int and value >= 0


def parse_steps(text: str) -> tuple[str, ...]:
    """Parse 2-8 exact single-line shell commands within one preview card."""
    if not isinstance(text, str) or not text or "\x00" in text:
        raise ValueError("runbook text must be non-empty UTF-8 text")
    try:
        encoded_size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("runbook text must be valid UTF-8") from exc
    if encoded_size > MAX_PLAN_BYTES:
        raise ValueError("runbook exceeds the preview byte limit")
    steps = tuple(text.splitlines())
    if not 2 <= len(steps) <= MAX_STEPS or any(not step.strip() for step in steps):
        raise ValueError("runbook must contain 2-8 non-empty lines")
    return steps


def _expire(plan: Runbook, now: int) -> Runbook:
    if now >= plan.expires_at and plan.state not in {"completed", "failed", "cancelled", "expired"}:
        return Runbook(**{**plan.__dict__, "state": "expired"})
    return plan


def _store(plan: Runbook) -> Runbook:
    _PLANS[plan.plan_id] = plan
    if plan.active_request_id:
        _REQUESTS[plan.active_request_id] = plan.plan_id
    return plan


def create_plan(
    requester_id: int,
    owner_id: int,
    device_id: str,
    text: str,
    *,
    now: int,
) -> Runbook:
    if not _positive_int(requester_id) or not _positive_int(owner_id) or not _timestamp(now):
        raise ValueError("invalid requester, owner, or timestamp")
    try:
        device_size = len(device_id.encode("utf-8")) if isinstance(device_id, str) else 0
    except UnicodeEncodeError as exc:
        raise ValueError("invalid device id") from exc
    if not isinstance(device_id, str) or not device_id or device_size > MAX_DEVICE_ID_BYTES:
        raise ValueError("invalid device id")
    steps = parse_steps(text)
    with _LOCK:
        expired_terminal = [
            plan_id for plan_id, plan in _PLANS.items()
            if now >= plan.expires_at and plan.state in {"completed", "failed", "cancelled", "expired"}
        ]
        for plan_id in expired_terminal:
            old = _PLANS.pop(plan_id)
            if old.active_request_id:
                _REQUESTS.pop(old.active_request_id, None)
        expired_active = [
            plan_id for plan_id, plan in _PLANS.items()
            if now >= plan.expires_at and plan.state not in {"completed", "failed", "cancelled", "expired"}
        ]
        for plan_id in expired_active:
            _store(_expire(_PLANS[plan_id], now))
        if len(_PLANS) >= MAX_PLANS:
            raise RunbookStoreFull("runbook store is full")
        plan_id = secrets.token_urlsafe(12)
        while plan_id in _PLANS:
            plan_id = secrets.token_urlsafe(12)
        plan = Runbook(
            plan_id=plan_id, requester_id=requester_id, owner_id=owner_id,
            device_id=device_id, steps=steps, created_at=now,
            expires_at=now + PLAN_TTL_SECONDS, state="draft", current_index=0,
            active_request_id=None, step_states=tuple("pending" for _ in steps),
            step_started_at=tuple(None for _ in steps),
            step_results=tuple(None for _ in steps),
        )
        _PLANS[plan_id] = plan
        return plan


def get_plan(plan_id: str, *, owner_id: int, now: int) -> Runbook | None:
    if not isinstance(plan_id, str) or not plan_id or not _positive_int(owner_id) or not _timestamp(now):
        return None
    with _LOCK:
        plan = _PLANS.get(plan_id)
        if plan is None or plan.owner_id != owner_id:
            return None
        plan = _expire(plan, now)
        return _store(plan)


def confirm_plan(plan_id: str, *, owner_id: int, now: int) -> Runbook | None:
    with _LOCK:
        plan = get_plan(plan_id, owner_id=owner_id, now=now)
        if plan is None or plan.state != "draft":
            return None
        return _store(Runbook(**{**plan.__dict__, "state": "confirmed"}))


def bind_pending(plan_id: str, request_id: str, *, step_index: int, now: int) -> bool:
    if not isinstance(request_id, str) or not request_id or type(step_index) is not int or not _timestamp(now):
        return False
    with _LOCK:
        plan = _PLANS.get(plan_id)
        if plan is None:
            return False
        plan = _expire(plan, now)
        if plan.state not in {"confirmed", "ready_next"} or plan.active_request_id is not None:
            _store(plan)
            return False
        if step_index != plan.current_index or not 0 <= step_index < len(plan.steps):
            return False
        replacement = Runbook(**{
            **plan.__dict__, "state": "awaiting_step", "active_request_id": request_id,
        })
        _store(replacement)
        return True


def find_by_request(request_id: str, *, now: int) -> Runbook | None:
    if not isinstance(request_id, str) or not request_id or not _timestamp(now):
        return None
    with _LOCK:
        plan_id = _REQUESTS.get(request_id)
        plan = _PLANS.get(plan_id) if plan_id else None
        if plan is None:
            return None
        return _store(_expire(plan, now))


def mark_started(request_id: str, *, now: int) -> bool:
    if not isinstance(request_id, str) or not request_id or not _timestamp(now):
        return False
    with _LOCK:
        plan_id = _REQUESTS.get(request_id)
        plan = _PLANS.get(plan_id) if plan_id else None
        if plan is None or plan.active_request_id != request_id or plan.state != "awaiting_step":
            return False
        started = list(plan.step_started_at)
        if started[plan.current_index] is not None:
            return False
        started[plan.current_index] = now
        _store(Runbook(**{**plan.__dict__, "step_started_at": tuple(started)}))
        return True


def _step_result(plan: Runbook, *, outcome: str, now: int, exit_code: int | None) -> StepResult:
    started_at = plan.step_started_at[plan.current_index]
    duration = max(0, now - started_at) if started_at is not None else None
    return StepResult(plan.current_index, outcome, duration, exit_code)


def complete_step(
    request_id: str,
    *,
    success: bool,
    now: int,
    exit_code: int | None = None,
    outcome: str | None = None,
) -> Runbook | None:
    if (not isinstance(request_id, str) or not request_id or type(success) is not bool
            or not _timestamp(now) or (exit_code is not None and type(exit_code) is not int)):
        return None
    terminal_outcome = outcome or ("completed" if success else "failed")
    if not isinstance(terminal_outcome, str) or not terminal_outcome or len(terminal_outcome) > 32:
        return None
    with _LOCK:
        plan_id = _REQUESTS.get(request_id)
        plan = _PLANS.get(plan_id) if plan_id else None
        if plan is None or plan.active_request_id != request_id or plan.state != "awaiting_step":
            return None
        plan = _expire(plan, now)
        if plan.state == "expired":
            return _store(plan)
        states = list(plan.step_states)
        states[plan.current_index] = "completed" if success else "failed"
        results = list(plan.step_results)
        results[plan.current_index] = _step_result(
            plan, outcome=terminal_outcome, now=now, exit_code=exit_code,
        )
        if not success:
            updated = Runbook(**{**plan.__dict__, "state": "failed", "step_states": tuple(states),
                                 "step_results": tuple(results)})
        elif plan.current_index + 1 >= len(plan.steps):
            updated = Runbook(**{**plan.__dict__, "state": "completed", "active_request_id": None,
                                 "step_states": tuple(states), "step_results": tuple(results)})
            _REQUESTS.pop(request_id, None)
        else:
            updated = Runbook(**{**plan.__dict__, "state": "ready_next", "active_request_id": None,
                                 "current_index": plan.current_index + 1, "step_states": tuple(states),
                                 "step_results": tuple(results)})
            _REQUESTS.pop(request_id, None)
        return _store(updated)


def cancel_request(request_id: str, *, owner_id: int, now: int) -> Runbook | None:
    if not isinstance(request_id, str) or not request_id or not _positive_int(owner_id) or not _timestamp(now):
        return None
    with _LOCK:
        plan_id = _REQUESTS.get(request_id)
        plan = _PLANS.get(plan_id) if plan_id else None
        if plan is None or plan.owner_id != owner_id or plan.active_request_id != request_id:
            return None
        states = list(plan.step_states)
        results = list(plan.step_results)
        states[plan.current_index] = "cancelled"
        results[plan.current_index] = _step_result(plan, outcome="cancelled", now=now, exit_code=None)
        updated = Runbook(**{**plan.__dict__, "state": "cancelled", "step_states": tuple(states),
                             "step_results": tuple(results)})
        return _store(updated)


def cancel_plan(plan_id: str, *, owner_id: int, now: int) -> Runbook | None:
    with _LOCK:
        plan = get_plan(plan_id, owner_id=owner_id, now=now)
        if plan is None or plan.state != "draft":
            return None
        return _store(Runbook(**{**plan.__dict__, "state": "cancelled"}))


def fail_plan(
    plan_id: str,
    *,
    owner_id: int,
    now: int,
    outcome: str = "failed",
    exit_code: int | None = None,
) -> Runbook | None:
    """Stop a confirmed/active plan without authorizing or dispatching another step."""
    with _LOCK:
        plan = get_plan(plan_id, owner_id=owner_id, now=now)
        if plan is None or plan.state not in {"confirmed", "awaiting_step", "ready_next"}:
            return None
        states = list(plan.step_states)
        results = list(plan.step_results)
        if plan.state == "awaiting_step" and plan.current_index < len(states):
            states[plan.current_index] = "failed"
            if results[plan.current_index] is None:
                results[plan.current_index] = _step_result(
                    plan, outcome=outcome, now=now, exit_code=exit_code,
                )
        if plan.active_request_id:
            _REQUESTS.pop(plan.active_request_id, None)
        return _store(Runbook(**{
            **plan.__dict__, "state": "failed", "active_request_id": None,
            "step_states": tuple(states), "step_results": tuple(results),
        }))


def format_report(plan: Runbook) -> str:
    """Render owner-visible step metadata without command text or captured output."""
    if not isinstance(plan, Runbook):
        return "Отчёт runbook недоступен."
    lines = ["Отчёт runbook:"]
    for index, command in enumerate(plan.steps):
        result = plan.step_results[index]
        if result is not None:
            duration = f"{result.duration_seconds} с" if result.duration_seconds is not None else "н/д"
            exit_code = str(result.exit_code) if result.exit_code is not None else "н/д"
            lines.append(f"Шаг {index + 1}: {result.outcome} · {duration} · exit={exit_code}")
        elif plan.active_request_id and index == plan.current_index:
            state = "выполняется" if plan.step_started_at[index] is not None else "ожидает подтверждения"
            lines.append(f"Шаг {index + 1}: {state} · длительность пока н/д · exit=н/д")
        else:
            lines.append(f"Шаг {index + 1}: не запускался · длительность н/д · exit=н/д")
    return "\n".join(lines)
