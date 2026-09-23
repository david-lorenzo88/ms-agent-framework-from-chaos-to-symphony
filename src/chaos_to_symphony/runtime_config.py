"""Provider settings that can be changed while the app is running.

The provider is normally an environment variable, which is fine until you are
holding a laptop in front of an audience, or you have just cloned this repo and
want to point it at your own Foundry project without learning the deploy
script first. Both of those want a box to type in, so this module keeps a small
set of overrides that win over the environment, and writes them to a file so
they survive a restart.

Deliberately narrow:

* **Three keys, allow-listed.** ``CHAOS_PROVIDER``, ``FOUNDRY_PROJECT_ENDPOINT``
  and ``FOUNDRY_MODEL``. The endpoint this is exposed through is unauthenticated
  on a public deploy, so it must not be a way to set arbitrary process settings.
* **No secrets.** Foundry authenticates with ``DefaultAzureCredential`` - a
  managed identity in a container, your ``az login`` locally - so nothing here
  is a credential. Azure OpenAI and OpenAI stay environment-only precisely
  because they need a key, and a key typed into a public page is a key leaked.

Overrides beat the environment rather than the other way round: someone who
just typed a value into the page means it, and "Reset" clears the file and
hands control back to whatever the process was started with.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The only settings this module will ever read or write.
ALLOWED: tuple[str, ...] = ("CHAOS_PROVIDER", "FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL")

#: Providers the UI may select. Azure OpenAI and OpenAI are absent on purpose:
#: they need an API key, which does not belong in a form on a public page.
SELECTABLE_PROVIDERS: tuple[str, ...] = ("offline", "foundry")

_overrides: dict[str, str] | None = None
_listeners: list[Callable[[], None]] = []


def config_path() -> Path:
    """Where the overrides are kept. Override with ``CHAOS_CONFIG_FILE``."""
    return Path(os.getenv("CHAOS_CONFIG_FILE", ".chaos-config.json")).expanduser()


def on_change(callback: Callable[[], None]) -> Callable[[], None]:
    """Register a callback for when the overrides change.

    Used as a decorator by modules that cache something derived from these
    settings, so nothing has to import this module *and* be imported by it.
    """
    _listeners.append(callback)
    return callback


def _load() -> dict[str, str]:
    """Read the file once, tolerating anything that is not a usable config."""
    global _overrides
    if _overrides is not None:
        return _overrides

    _overrides = {}
    path = config_path()
    try:
        raw = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return _overrides
    except Exception as exc:
        # A corrupt config must not stop the app booting; the environment is
        # still there, and the demo runs offline by default anyway.
        logger.warning("Ignoring unreadable config at %s: %s", path, exc)
        return _overrides

    if isinstance(raw, dict):
        _overrides = {k: str(v) for k, v in raw.items() if k in ALLOWED and str(v).strip()}
    return _overrides


def value(name: str, default: str | None = None) -> str | None:
    """One setting: the saved override if there is one, else the environment."""
    if name in ALLOWED:
        override = _load().get(name)
        if override:
            return override
    return os.getenv(name, default)


def overrides() -> dict[str, str]:
    """A copy of what is currently overridden."""
    return dict(_load())


def snapshot() -> dict[str, Any]:
    """What the settings panel needs: values, and where each one came from."""
    saved = _load()
    return {
        "values": {name: value(name) or "" for name in ALLOWED},
        "sources": {name: ("saved" if saved.get(name) else "environment") for name in ALLOWED},
        "selectableProviders": list(SELECTABLE_PROVIDERS),
        "configPath": str(config_path()),
        "persisted": bool(saved),
    }


def save(values: dict[str, str]) -> bool:
    """Apply overrides now and try to write them. Returns whether they persisted.

    Applying and persisting are separate on purpose: a read-only filesystem
    should still let you switch provider for this process rather than refusing
    outright, and the caller can say plainly that the change will not survive a
    restart.
    """
    global _overrides
    _overrides = {k: str(v).strip() for k, v in values.items() if k in ALLOWED and str(v).strip()}
    _notify()

    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_overrides, indent=2) + "\n", "utf-8")
    except Exception as exc:
        logger.warning("Applied settings but could not write %s: %s", path, exc)
        return False
    return True


def clear() -> bool:
    """Drop the overrides and hand control back to the environment."""
    global _overrides
    _overrides = {}
    _notify()
    try:
        config_path().unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Cleared settings but could not remove %s: %s", config_path(), exc)
        return False
    return True


def _notify() -> None:
    for callback in _listeners:
        try:
            callback()
        except Exception:  # pragma: no cover - a listener must not break a save
            logger.exception("A settings listener failed")
