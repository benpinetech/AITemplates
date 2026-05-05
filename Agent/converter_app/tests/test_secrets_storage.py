"""Tests for the encrypted-file fallback storage.

The fallback only fires when the OS keychain isn't available. We
exercise it directly here against a tmp directory to avoid touching
the real user config.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

_AGENT_DIR = Path(__file__).resolve().parent.parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from converter_app import secrets_storage


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Force the storage to write into a tmp dir for each test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield tmp_path


# ─── round-trip ───────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_set_then_get(self, isolated_config):
        secrets_storage.set_secret("openai_api_key", "sk-test-AAAAAAAAAA")
        assert secrets_storage.get_secret("openai_api_key") == "sk-test-AAAAAAAAAA"

    def test_get_missing_returns_none(self, isolated_config):
        assert secrets_storage.get_secret("not_set") is None

    def test_overwrite(self, isolated_config):
        secrets_storage.set_secret("k", "v1")
        secrets_storage.set_secret("k", "v2")
        assert secrets_storage.get_secret("k") == "v2"

    def test_delete(self, isolated_config):
        secrets_storage.set_secret("k", "v")
        secrets_storage.delete_secret("k")
        assert secrets_storage.get_secret("k") is None

    def test_delete_missing_is_noop(self, isolated_config):
        secrets_storage.delete_secret("never_existed")   # no exception


# ─── on-disk shape ────────────────────────────────────────────────────────

class TestOnDisk:
    def test_files_land_in_expected_location(self, isolated_config):
        secrets_storage.set_secret("openai_api_key", "sk-test")
        assert (isolated_config / "jda-pine-converter" / ".secrets.key").exists()
        assert (isolated_config / "jda-pine-converter" / "secrets.enc").exists()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
    def test_permissions_are_0600(self, isolated_config):
        secrets_storage.set_secret("openai_api_key", "sk-test")
        for p in (
            isolated_config / "jda-pine-converter" / ".secrets.key",
            isolated_config / "jda-pine-converter" / "secrets.enc",
        ):
            mode = stat.S_IMODE(p.stat().st_mode)
            assert mode == 0o600, f"{p} is {oct(mode)}, expected 0600"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
    def test_directory_permissions_are_0700(self, isolated_config):
        secrets_storage.set_secret("openai_api_key", "sk-test")
        d = isolated_config / "jda-pine-converter"
        mode = stat.S_IMODE(d.stat().st_mode)
        assert mode == 0o700, f"{d} is {oct(mode)}, expected 0700"

    def test_secret_value_not_visible_in_file(self, isolated_config):
        # The encrypted file must NOT contain the plaintext API key.
        secret = "sk-PLEASE_DO_NOT_LEAK_ME_AAAAAAAAAA"
        secrets_storage.set_secret("openai_api_key", secret)
        encrypted_path = isolated_config / "jda-pine-converter" / "secrets.enc"
        assert encrypted_path.exists()
        contents = encrypted_path.read_bytes()
        # Searching for the key text in the encrypted bytes — Fernet output
        # is base64'd encrypted bytes, so the plaintext can't appear.
        assert secret.encode() not in contents
        assert b"sk-" not in contents


# ─── corruption recovery ──────────────────────────────────────────────────

class TestCorruption:
    def test_corrupt_store_returns_empty(self, isolated_config):
        # Plant a valid key but a junk store file.
        secrets_storage.set_secret("k", "v")
        (isolated_config / "jda-pine-converter" / "secrets.enc").write_bytes(
            b"this is not valid Fernet output"
        )
        # Reading should not crash; should treat as empty.
        assert secrets_storage.get_secret("k") is None

    def test_missing_key_file_regenerates(self, isolated_config):
        secrets_storage.set_secret("k", "v")
        kp = isolated_config / "jda-pine-converter" / ".secrets.key"
        kp.unlink()
        # Old store can no longer be decrypted with the new key. Read
        # is empty; we don't crash. New writes work fine.
        assert secrets_storage.get_secret("k") is None
        secrets_storage.set_secret("k2", "v2")
        assert secrets_storage.get_secret("k2") == "v2"


# ─── settings.py integration ──────────────────────────────────────────────

class TestSettingsIntegration:
    """The settings.load_api_key / save_api_key / clear_api_key path
    should fall back to secrets_storage when keyring raises
    NoKeyringError. We force that by monkeypatching keyring."""

    def test_save_then_load_via_fallback(self, isolated_config, monkeypatch):
        from converter_app import settings as cfg
        import keyring
        from keyring.errors import NoKeyringError

        def boom_set(*_a, **_kw):
            raise NoKeyringError("test: no backend")
        def boom_get(*_a, **_kw):
            raise NoKeyringError("test: no backend")
        def boom_del(*_a, **_kw):
            raise NoKeyringError("test: no backend")

        monkeypatch.setattr(keyring, "set_password", boom_set)
        monkeypatch.setattr(keyring, "get_password", boom_get)
        monkeypatch.setattr(keyring, "delete_password", boom_del)

        cfg.save_api_key("sk-fallback-AAAAAAAAAA")
        assert cfg.load_api_key() == "sk-fallback-AAAAAAAAAA"
        assert cfg.storage_kind() == cfg.STORAGE_FALLBACK
        cfg.clear_api_key()
        assert cfg.load_api_key() is None

    def test_storage_kind_reports_keychain_when_keyring_works(self, monkeypatch):
        from converter_app import settings as cfg
        import keyring

        # Pretend keyring works and returns nothing.
        monkeypatch.setattr(keyring, "get_password", lambda *_a, **_kw: None)
        assert cfg.storage_kind() == cfg.STORAGE_KEYCHAIN
