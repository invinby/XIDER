"""Process-local FIFO serialization for owner-approved commands per device."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field


MAX_CONCURRENT_OWNER_TASKS = 2


@dataclass(eq=False)
class Ticket:
    device_id: str
    request_id: str
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    capacity_ready: asyncio.Event = field(default_factory=asyncio.Event)
    position: int = 0
    capacity_position: int = 0
    owns_capacity: bool = False
    released: bool = False


_queues: dict[str, list[Ticket]] = {}
_tickets: dict[str, Ticket] = {}
_capacity_waiters: list[Ticket] = []
_active_capacity = 0
_lock = threading.RLock()


def enqueue(device_id: str, request_id: str) -> Ticket:
    """Add one consumed approval to its device FIFO; never stores command text."""
    if not isinstance(device_id, str) or not device_id or len(device_id.encode("utf-8")) > 256:
        raise ValueError("invalid device_id")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise ValueError("invalid request_id")
    with _lock:
        if request_id in _tickets:
            raise ValueError("duplicate queue request_id")
        queue = _queues.setdefault(device_id, [])
        ticket = Ticket(device_id, request_id, position=len(queue) + 1)
        queue.append(ticket)
        _tickets[request_id] = ticket
        if len(queue) == 1:
            ticket.ready.set()
        return ticket


async def wait_turn(ticket: Ticket, timeout: float) -> bool:
    """Wait only within the caller's original approval lifetime."""
    if not isinstance(ticket, Ticket) or type(timeout) not in (int, float) or timeout < 0:
        raise ValueError("invalid queue wait")
    if ticket.released:
        return False
    try:
        await asyncio.wait_for(ticket.ready.wait(), timeout=float(timeout))
    except asyncio.TimeoutError:
        return False
    return not ticket.released


async def acquire_capacity(ticket: Ticket, *, limit: int, timeout: float | None = None) -> bool:
    """Acquire a fair process-wide owner-task slot without storing command text."""
    global _active_capacity
    if (not isinstance(ticket, Ticket) or type(limit) is not int or not 1 <= limit <= 16
            or (timeout is not None and (type(timeout) not in (int, float) or timeout < 0))):
        raise ValueError("invalid capacity request")
    with _lock:
        if ticket.released:
            return False
        if ticket.owns_capacity:
            return True
        if _active_capacity < limit and not _capacity_waiters:
            _active_capacity += 1
            ticket.owns_capacity = True
            ticket.capacity_position = 0
            return True
        if ticket not in _capacity_waiters:
            _capacity_waiters.append(ticket)
            _refresh_capacity_positions()
    try:
        if timeout is None:
            await ticket.capacity_ready.wait()
        else:
            await asyncio.wait_for(ticket.capacity_ready.wait(), timeout=float(timeout))
    except asyncio.TimeoutError:
        with _lock:
            if ticket in _capacity_waiters:
                _capacity_waiters.remove(ticket)
                _refresh_capacity_positions()
        return False
    except asyncio.CancelledError:
        with _lock:
            if ticket in _capacity_waiters:
                _capacity_waiters.remove(ticket)
                _refresh_capacity_positions()
        raise
    with _lock:
        return ticket.owns_capacity and not ticket.released


def _refresh_capacity_positions() -> None:
    for index, waiting in enumerate(_capacity_waiters, start=1):
        waiting.capacity_position = index


def _release_capacity_locked(ticket: Ticket) -> None:
    global _active_capacity
    if not ticket.owns_capacity:
        if ticket in _capacity_waiters:
            _capacity_waiters.remove(ticket)
            _refresh_capacity_positions()
        return
    ticket.owns_capacity = False
    if _capacity_waiters:
        next_ticket = _capacity_waiters.pop(0)
        next_ticket.capacity_position = 0
        next_ticket.owns_capacity = True
        next_ticket.capacity_ready.set()
        _refresh_capacity_positions()
    else:
        _active_capacity = max(0, _active_capacity - 1)


def release_capacity(ticket: Ticket) -> None:
    """Release one global slot and transfer it directly to the FIFO waiter."""
    if not isinstance(ticket, Ticket):
        return
    with _lock:
        _release_capacity_locked(ticket)


def release(ticket: Ticket) -> None:
    """Remove a ticket exactly once and wake the next request for its device."""
    if not isinstance(ticket, Ticket):
        return
    with _lock:
        if ticket.released:
            return
        ticket.released = True
        _release_capacity_locked(ticket)
        _tickets.pop(ticket.request_id, None)
        queue = _queues.get(ticket.device_id, [])
        try:
            queue.remove(ticket)
        except ValueError:
            pass
        if queue:
            queue[0].position = 1
            queue[0].ready.set()
            for index, waiting in enumerate(queue[1:], start=2):
                waiting.position = index
        else:
            _queues.pop(ticket.device_id, None)
