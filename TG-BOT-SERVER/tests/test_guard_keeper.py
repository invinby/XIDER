import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.guard_keeper import Keeper


def test_keeper_requires_an_agent_command(tmp_path):
    with pytest.raises(ValueError, match="command must not be empty"):
        Keeper([], tmp_path / "keeper.log")


def test_keeper_clamps_retry_backoff(tmp_path):
    keeper = Keeper(["python", "agent.py"], tmp_path / "keeper.log", max_backoff=0)
    assert keeper.max_backoff == 1


def test_stop_interrupts_retry_wait_and_terminates_child(tmp_path):
    keeper = Keeper(["python", "agent.py"], tmp_path / "keeper.log")
    child = Mock()
    child.poll.return_value = None
    child.pid = 123
    keeper.child = child

    keeper.stop()

    assert keeper.stop_requested is True
    assert keeper._stop_event.is_set()
    child.terminate.assert_called_once_with()


def test_stop_does_not_terminate_an_exited_child(tmp_path):
    keeper = Keeper(["python", "agent.py"], tmp_path / "keeper.log")
    child = Mock()
    child.poll.return_value = 0
    keeper.child = child

    keeper.stop()

    child.terminate.assert_not_called()
