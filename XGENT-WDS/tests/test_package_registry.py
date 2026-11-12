import importlib
import pkgutil
import threading

import pytest

from xg_commands import HANDLERS


@pytest.fixture
def client(monkeypatch):
    import xgent_wds as wds

    instance = wds.XgentClient()
    monkeypatch.setattr(instance, "_client", type("Publisher", (), {"publish": lambda *_args, **_kwargs: None})())
    return instance


def test_registered_handler_keys_match_supported_commands(client):
    import xgent_wds as wds

    assert set(HANDLERS) == set(wds.SUPPORTED_COMMANDS)
    assert all(callable(handler) for handler in HANDLERS.values())


def test_each_xg_util_module_imports_without_starting_threads_or_registering_commands():
    import xg_util
    from xg_commands import HANDLERS

    before_threads = {thread.ident for thread in threading.enumerate()}
    before_handlers = dict(HANDLERS)
    for module in pkgutil.iter_modules(xg_util.__path__, prefix="xg_util."):
        importlib.import_module(module.name)

    assert {thread.ident for thread in threading.enumerate()} == before_threads
    assert dict(HANDLERS) == before_handlers
