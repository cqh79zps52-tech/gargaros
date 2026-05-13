from __future__ import annotations

import pytest

from gargaros import input_driver


@pytest.fixture
def captured(monkeypatch):
    sent: list[list[input_driver.INPUT]] = []

    def fake(inputs):
        sent.append(list(inputs))
        return len(inputs)

    monkeypatch.setattr(input_driver, "_send_input", fake)
    return sent


def test_virtual_key_code_letters():
    assert input_driver.virtual_key_code("w") == 0x57
    assert input_driver.virtual_key_code("W") == 0x57
    assert input_driver.virtual_key_code("a") == 0x41


def test_virtual_key_code_named():
    assert input_driver.virtual_key_code("space") == 0x20
    assert input_driver.virtual_key_code("shift") == 0x10
    assert input_driver.virtual_key_code("ctrl") == 0x11
    assert input_driver.virtual_key_code("enter") == 0x0D
    assert input_driver.virtual_key_code("escape") == 0x1B


def test_virtual_key_code_digits():
    assert input_driver.virtual_key_code("0") == 0x30
    assert input_driver.virtual_key_code("9") == 0x39


def test_virtual_key_code_function_keys():
    assert input_driver.virtual_key_code("f1") == 0x70
    assert input_driver.virtual_key_code("F12") == 0x7B


def test_virtual_key_code_rejects_unknown():
    with pytest.raises(ValueError):
        input_driver.virtual_key_code("plonk")


def test_key_down_sends_keyboard_input(captured):
    input_driver.key_down("w")
    assert len(captured) == 1
    inputs = captured[0]
    assert len(inputs) == 1
    inp = inputs[0]
    assert inp.type == input_driver.INPUT_KEYBOARD
    assert inp.ki.wVk == 0x57
    assert inp.ki.dwFlags == 0  # no KEYUP flag = key down


def test_key_up_sets_keyup_flag(captured):
    input_driver.key_up("w")
    inp = captured[0][0]
    assert inp.type == input_driver.INPUT_KEYBOARD
    assert inp.ki.wVk == 0x57
    assert inp.ki.dwFlags & input_driver.KEYEVENTF_KEYUP


def test_mouse_move_relative_sends_mouse_input(captured):
    input_driver.mouse_move_relative(50, -10)
    inp = captured[0][0]
    assert inp.type == input_driver.INPUT_MOUSE
    assert inp.mi.dx == 50
    assert inp.mi.dy == -10
    assert inp.mi.dwFlags == input_driver.MOUSEEVENTF_MOVE


def test_mouse_button_left_down(captured):
    input_driver.mouse_button("left", "down")
    inp = captured[0][0]
    assert inp.type == input_driver.INPUT_MOUSE
    assert inp.mi.dwFlags == input_driver.MOUSEEVENTF_LEFTDOWN


def test_mouse_button_right_up(captured):
    input_driver.mouse_button("right", "up")
    inp = captured[0][0]
    assert inp.mi.dwFlags == input_driver.MOUSEEVENTF_RIGHTUP


def test_mouse_button_rejects_unknown(captured):
    with pytest.raises(ValueError):
        input_driver.mouse_button("foot", "down")
