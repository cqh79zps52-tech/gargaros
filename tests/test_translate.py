from __future__ import annotations

import pytest

from gargaros import translate


def test_click_args_default():
    assert translate.click_args(x=10, y=20) == {"loc": [10, 20], "button": "left", "clicks": 1}


def test_click_args_double():
    assert translate.click_args(x=10, y=20, double=True)["clicks"] == 2


def test_click_args_invalid_button():
    with pytest.raises(ValueError):
        translate.click_args(x=0, y=0, button="middle-right")


def test_move_args():
    assert translate.move_args(x=1, y=2) == {"loc": [1, 2], "drag": False}


def test_type_args_no_loc():
    assert translate.type_args(text="hi") == {"text": "hi", "press_enter": False}


def test_type_args_with_loc():
    out = translate.type_args(text="x", loc=[5, 6])
    assert out["loc"] == [5, 6]


def test_scroll_vertical_up():
    out = translate.scroll_args(dy=3)
    assert out == {"type": "vertical", "direction": "up", "wheel_times": 3}


def test_scroll_horizontal_left():
    out = translate.scroll_args(dx=-2)
    assert out == {"type": "horizontal", "direction": "left", "wheel_times": 2}


def test_scroll_zero_raises():
    with pytest.raises(ValueError):
        translate.scroll_args(dx=0, dy=0)


def test_shortcut_string_simple():
    assert translate.shortcut_string(name="c", modifiers=["ctrl"]) == "ctrl+c"


def test_shortcut_string_complex():
    assert translate.shortcut_string(name="A", modifiers=["Ctrl", "Shift"]) == "ctrl+shift+a"


def test_shortcut_string_invalid_modifier():
    with pytest.raises(ValueError):
        translate.shortcut_string(name="x", modifiers=["meta"])


def test_snapshot_args_defaults():
    out = translate.snapshot_args()
    assert out == {
        "use_vision": False,
        "use_dom": False,
        "use_annotation": True,
        "use_ui_tree": True,
    }


def test_snapshot_args_with_display():
    out = translate.snapshot_args(use_vision=True, display=[0, 1])
    assert out["use_vision"] is True
    assert out["display"] == [0, 1]


def test_click_label_args():
    assert translate.click_label_args(label=7) == {"label": 7, "button": "left", "clicks": 1}


def test_click_label_args_double():
    assert translate.click_label_args(label=7, double=True)["clicks"] == 2


def test_click_label_args_invalid_button():
    with pytest.raises(ValueError):
        translate.click_label_args(label=1, button="bogus")


def test_type_label_args():
    out = translate.type_label_args(label=3, text="hi", press_enter=True)
    assert out == {"label": 3, "text": "hi", "clear": False, "press_enter": True}


def test_scrape_args_minimal():
    assert translate.scrape_args(url="https://example.com") == {
        "url": "https://example.com",
        "use_dom": False,
        "use_sampling": True,
    }


def test_scrape_args_with_query():
    out = translate.scrape_args(url="https://example.com", query="hi")
    assert out["query"] == "hi"


def test_scrape_args_empty_url():
    with pytest.raises(ValueError):
        translate.scrape_args(url="")


def test_app_args_launch():
    assert translate.app_args(name="notepad") == {"mode": "launch", "name": "notepad"}


def test_app_args_resize():
    out = translate.app_args(name="notepad", mode="resize", window_loc=[100, 100], window_size=[800, 600])
    assert out == {
        "mode": "resize",
        "name": "notepad",
        "window_loc": [100, 100],
        "window_size": [800, 600],
    }


def test_app_args_invalid_mode():
    with pytest.raises(ValueError):
        translate.app_args(name="notepad", mode="kill")
