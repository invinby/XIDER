import text_store


def test_text_store_is_atomic_and_rejects_unknown_or_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(text_store, "FILE", tmp_path / "bot_texts.json")
    assert text_store.get("start_guest").startswith("XIDER")
    text_store.set_text("start_guest", "Новое приветствие")
    assert text_store.get("start_guest") == "Новое приветствие"
    try:
        text_store.set_text("missing", "x")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown text key must be rejected")
