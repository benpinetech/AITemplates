"""Settings — Qt persistence + secure keyring storage.

Two stores:

  - QSettings (platform-native config files / registry): non-sensitive
    preferences like the chosen org, model name, base URL, last-opened
    folder, and window geometry.
  - keyring (OS keychain): the OpenAI API key. Never written to a
    plaintext settings file.
"""

from __future__ import annotations

from typing import Optional

import keyring
from PySide6.QtCore import QSettings


SETTINGS_ORG = "JDA-Pine"
SETTINGS_APP = "Converter"

KEYRING_SERVICE = "JDA-Pine-Converter"
KEYRING_API_KEY = "openai_api_key"


def app_settings() -> QSettings:
    """One settings instance shared by the whole app."""
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


# ── API key (sensitive) ───────────────────────────────────────────────────────
#
# Storage strategy: try the OS keychain first (Keychain / Credential
# Manager / Secret Service). If no keychain backend is available — common
# on Linux WMs like Hyprland that don't ship gnome-keyring — fall back
# to an encrypted file under ~/.config/jda-pine-converter/ with 0600
# perms. Either way the key never lives in a project file, so it cannot
# be accidentally committed.

STORAGE_KEYCHAIN = "keychain"
STORAGE_FALLBACK = "encrypted-file"


def storage_kind() -> str:
    """Return whichever storage backend will be used right now. The GUI
    surfaces this so the mapper knows where their key actually lives."""
    try:
        keyring.get_password(KEYRING_SERVICE, "_probe_unused_entry")
        return STORAGE_KEYCHAIN
    except keyring.errors.NoKeyringError:
        return STORAGE_FALLBACK
    except Exception:
        return STORAGE_KEYCHAIN


def load_api_key() -> Optional[str]:
    """Return the stored OpenAI API key, or None if not set."""
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_API_KEY)
    except keyring.errors.NoKeyringError:
        from . import secrets_storage
        return secrets_storage.get_secret(KEYRING_API_KEY)
    except Exception:
        return None


def save_api_key(key: str) -> None:
    """Store the OpenAI API key. Keychain first; fall back to an
    encrypted file when no keychain backend is installed."""
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_API_KEY, key)
    except keyring.errors.NoKeyringError:
        from . import secrets_storage
        secrets_storage.set_secret(KEYRING_API_KEY, key)


def clear_api_key() -> None:
    """Remove the stored API key from whichever backend holds it."""
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_API_KEY)
    except keyring.errors.NoKeyringError:
        from . import secrets_storage
        secrets_storage.delete_secret(KEYRING_API_KEY)
    except keyring.errors.PasswordDeleteError:
        pass


# ── preferences (non-sensitive) ───────────────────────────────────────────────

KEY_ORG = "org"
KEY_MODEL = "openai_model"
KEY_BASE_URL = "openai_base_url"
KEY_LAST_DIR = "last_dir"
KEY_GEOMETRY = "geometry"
KEY_WINDOW_STATE = "window_state"

DEFAULT_MODEL = "gpt-4o-mini"


def get_org(default: str = "oba") -> str:
    return str(app_settings().value(KEY_ORG, default))


def set_org(org: str) -> None:
    app_settings().setValue(KEY_ORG, org)


def get_model(default: str = DEFAULT_MODEL) -> str:
    return str(app_settings().value(KEY_MODEL, default))


def set_model(model: str) -> None:
    app_settings().setValue(KEY_MODEL, model.strip() or DEFAULT_MODEL)


def get_base_url(default: str = "") -> str:
    """OpenAI-compatible endpoint override. Empty string = use the
    SDK's default (api.openai.com)."""
    return str(app_settings().value(KEY_BASE_URL, default))


def set_base_url(url: str) -> None:
    app_settings().setValue(KEY_BASE_URL, url.strip())


def get_last_dir(default: str = "") -> str:
    return str(app_settings().value(KEY_LAST_DIR, default))


def set_last_dir(path: str) -> None:
    app_settings().setValue(KEY_LAST_DIR, path)
