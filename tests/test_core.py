"""Config, key store and hotkey parsing."""

import pytest

import eecc_redact.config
from eecc_redact import keystore
from eecc_redact.config import Config
from eecc_redact.hotkeys import parse_hotkey


def test_config_round_trips_and_ignores_unknown_keys():
    config = Config(
        hotkey="alt+f9",
        box_padding=3,
        setup_complete=True,
        base_url='https://self-hosted.example/api "quoted"',
    )
    path = config.save()
    assert path == eecc_redact.config.config_path()
    text = path.read_text()
    assert "hotkey = " in text
    path.write_text(text + 'update_url = "gone"\n')
    loaded = Config.load()
    assert loaded == config


def test_missing_config_gives_defaults():
    assert Config.load() == Config()


def test_the_environment_wins_over_the_keychain(monkeypatch, isolated):
    isolated["key"] = "shr_live_from_keychain"
    assert keystore.key_source() == ("shr_live_from_keychain", "keychain")
    monkeypatch.setenv(keystore.ENV_VAR, " shr_test_from_env ")
    assert keystore.key_source() == ("shr_test_from_env", "environment")


def test_no_key_anywhere():
    assert keystore.key_source() == ("", "")
    assert keystore.get_key() == ""


def test_key_environment_and_scrubbing():
    assert keystore.environment("shr_live_abc") == "production"
    assert keystore.environment("shr_test_abc") == "sandbox"
    assert keystore.environment("nope") == "unknown"
    assert keystore.looks_like_key("--key=shr_live_ABC-123")
    assert not keystore.looks_like_key("doctor")
    out = keystore.scrub("failed with shr_live_ABC123def456 calling /v1/models")
    assert out == "failed with shr_live_*** calling /v1/models"


def test_the_default_hotkey_parses_for_every_platform():
    hotkey = parse_hotkey("ctrl+shift+print")
    assert hotkey.text == "Ctrl+Shift+Print"
    assert hotkey.qt_text == "Ctrl+Shift+Print"
    assert hotkey.win_modifiers == 0x0002 | 0x0004
    assert hotkey.win_vk == 0x2C  # VK_SNAPSHOT
    assert hotkey.portal == "CTRL+SHIFT+Print"
    assert hotkey.config_value == "ctrl+shift+print"


def test_hotkey_order_and_case_are_normalized():
    hotkey = parse_hotkey(" Shift + CTRL + s ")
    assert hotkey.text == "Ctrl+Shift+S"
    assert hotkey.win_vk == ord("S")
    assert hotkey.portal == "CTRL+SHIFT+s"
    assert parse_hotkey("meta+f9").qt_text == "Meta+F9"
    assert parse_hotkey("alt+f9").win_vk == 0x78
    assert parse_hotkey("print").win_modifiers == 0


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("", "No hotkey"),
        ("s", "every time you type it"),
        ("ctrl+shift", "exactly one key"),
        ("ctrl+a+b", "exactly one key"),
        ("ctrl+banana", "Unknown key"),
    ],
)
def test_bad_hotkeys_explain_themselves(text, fragment):
    with pytest.raises(ValueError, match=fragment):
        parse_hotkey(text)
