import asyncio

import pytest

import shell_command_queue as queue


def test_fifo_is_per_device_and_release_wakes_next_ticket():
    async def scenario():
        first = queue.enqueue("device-a", "request-1")
        second = queue.enqueue("device-a", "request-2")
        other_device = queue.enqueue("device-b", "request-3")
        assert (first.position, second.position, other_device.position) == (1, 2, 1)
        assert await queue.wait_turn(first, 0.1)
        assert await queue.wait_turn(other_device, 0.1)
        assert not await queue.wait_turn(second, 0.01)
        queue.release(first)
        assert await queue.wait_turn(second, 0.1)
        queue.release(second)
        queue.release(other_device)
        queue.release(second)

    asyncio.run(scenario())


def test_expired_waiter_is_removed_and_does_not_block_following_ticket():
    async def scenario():
        first = queue.enqueue("device-a", "request-4")
        second = queue.enqueue("device-a", "request-5")
        assert await queue.wait_turn(first, 0.1)
        assert not await queue.wait_turn(second, 0.001)
        queue.release(first)
        assert await queue.wait_turn(second, 0.1)
        queue.release(second)

    asyncio.run(scenario())


@pytest.mark.parametrize("device,request_id", [("", "r"), ("d", ""), ("d", "x" * 129)])
def test_invalid_ticket_metadata_is_rejected(device, request_id):
    with pytest.raises(ValueError):
        queue.enqueue(device, request_id)


def test_duplicate_request_id_is_rejected_until_released():
    ticket = queue.enqueue("device-a", "request-6")
    with pytest.raises(ValueError):
        queue.enqueue("device-b", "request-6")
    queue.release(ticket)
    replacement = queue.enqueue("device-b", "request-6")
    queue.release(replacement)


def test_global_task_cap_fairly_queues_across_devices_and_releases_capacity():
    async def scenario():
        first = queue.enqueue("device-a", "request-7")
        second = queue.enqueue("device-b", "request-8")
        third = queue.enqueue("device-c", "request-9")
        assert await queue.acquire_capacity(first, limit=2)
        assert await queue.acquire_capacity(second, limit=2)
        waiting = asyncio.create_task(queue.acquire_capacity(third, limit=2))
        for _ in range(20):
            if third.capacity_position:
                break
            await asyncio.sleep(0)
        assert not waiting.done()
        assert third.capacity_position == 1
        queue.release_capacity(first)
        assert await asyncio.wait_for(waiting, timeout=0.1)
        queue.release_capacity(second)
        assert await queue.acquire_capacity(first, limit=2)
        queue.release_capacity(first)
        queue.release_capacity(third)
        queue.release(first)
        queue.release(second)
        queue.release(third)

    asyncio.run(scenario())
