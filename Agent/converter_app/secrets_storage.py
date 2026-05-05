"""Fallback for OS-keychain-less environments.

Most desktops have a Secret Service (Keychain on macOS, Credential
Manager on Windows, gnome-keyring or KWallet on Linux GNOME/KDE) and
the ``keyring`` library uses it transparently. But minimal Linux WMs
like Hyprland / sway / i3 don't ship a Secret Service by default, so
``keyring`` raises ``NoKeyringError`` when you try to save anything.

This module is the fallback the GUI swaps in when that happens. It
stores secrets in an **encrypted file** under
``$XDG_CONFIG_HOME/jda-pine-converter/`` (typically
``~/.config/jda-pine-converter/``) with ``0600`` permissions. The
encryption key sits next to it, also ``0600``. So:

  * The secrets file isn't trivially grep-able for ``sk-…``.
  * File permissions keep it out of other users' reach on shared boxes.
  * Nothing lives in the project repo, so nothing is at risk of
    accidental ``git add``.

Threat model: if an attacker has read access to the user's home
directory, they can decrypt the file (they hold both files). That's
the same threat model as Keychain when the user is logged in. This
isn't trying to defeat a determined local attacker — it's trying to
prevent accidental disclosure (logs, backups, careless screen shares,
git commits).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Dict, Optional

from cryptography.fernet import Fernet, InvalidToken


_CONFIG_SUBDIR = "jda-pine-converter"
_KEY_FILENAME = ".secrets.key"
_STORE_FILENAME = "secrets.enc"


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / _CONFIG_SUBDIR


def _key_path() -> Path:
    return _config_dir() / _KEY_FILENAME


def _store_path() -> Path:
    return _config_dir() / _STORE_FILENAME


# ─── filesystem helpers ───────────────────────────────────────────────────────


def _ensure_dir() -> Path:
    d = _config_dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    # If the directory pre-existed with looser perms, tighten them.
    try:
        os.chmod(d, 0o700)
    except OSError:
        # e.g. on Windows, where chmod is mostly a no-op. Live with it.
        pass
    return d


def _write_locked(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` and immediately mark it ``0600``.
    On Windows, ``chmod`` is largely informational; the file ends up
    in the user's profile which is already user-only by default."""
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ─── encryption key ──────────────────────────────────────────────────────────


def _load_or_create_key() -> Fernet:
    _ensure_dir()
    kp = _key_path()
    if kp.exists():
        # Sanity-check perms — if someone widened them, tighten back.
        try:
            mode = stat.S_IMODE(kp.stat().st_mode)
            if mode & 0o077:
                os.chmod(kp, 0o600)
        except OSError:
            pass
        return Fernet(kp.read_bytes())
    key = Fernet.generate_key()
    _write_locked(kp, key)
    return Fernet(key)


# ─── store ───────────────────────────────────────────────────────────────────


def _load_store() -> Dict[str, str]:
    fernet = _load_or_create_key()
    sp = _store_path()
    if not sp.exists():
        return {}
    try:
        decrypted = fernet.decrypt(sp.read_bytes())
        return json.loads(decrypted.decode("utf-8"))
    except (InvalidToken, json.JSONDecodeError, ValueError):
        # Corrupt or rotated key — start fresh rather than crash. The
        # user can re-enter the key in Settings.
        return {}


def _save_store(data: Dict[str, str]) -> None:
    fernet = _load_or_create_key()
    encrypted = fernet.encrypt(json.dumps(data).encode("utf-8"))
    _write_locked(_store_path(), encrypted)


# ─── public API ──────────────────────────────────────────────────────────────


def get_secret(name: str) -> Optional[str]:
    """Read one stored secret. Returns None if unset."""
    return _load_store().get(name)


def set_secret(name: str, value: str) -> None:
    data = _load_store()
    data[name] = value
    _save_store(data)


def delete_secret(name: str) -> None:
    data = _load_store()
    if name in data:
        del data[name]
        _save_store(data)


def store_location() -> Path:
    """Path of the encrypted secrets file. Useful for showing the user
    where their key lives when the OS keychain isn't available."""
    return _store_path()
