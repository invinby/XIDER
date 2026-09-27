import json

import ui_cards


def test_remembers_card_per_chat_and_user(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_cards, "FILE", tmp_path / "cards.json")
    assert ui_cards.get(10, 20) is None
    ui_cards.set_card(10, 20, 7)
    ui_cards.set_card(10, 21, 8)
    ui_cards.set_card(11, 20, 9)
    assert ui_cards.get(10, 20) == 7
    assert ui_cards.get(10, 21) == 8
    assert ui_cards.get(11, 20) == 9
    assert json.loads(ui_cards.FILE.read_text())["10:20"] == 7


def test_invalid_card_falls_back_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_cards, "FILE", tmp_path / "cards.json")
    ui_cards.FILE.write_text("{broken", encoding="utf-8")
    assert ui_cards.get(1, 1) is None
    ui_cards.set_card(1, 1, 42)
    assert ui_cards.get(1, 1) == 42
