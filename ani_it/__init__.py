"""ani-it: Client CLI/TUI nativo per lo streaming di anime da AnimeUnity in italiano.

Ottimizzato per Arch Linux (Wayland/X11, PipeWire, MPV IPC).

Gli oggetti principali si importano su richiesta: `import ani_it` (e quindi ogni
`python -m ani_it.preview` lanciato da fzf) non carica requests, bs4 e il resto
dell'applicazione finché non servono.
"""

import importlib
from typing import Any

from ani_it._version import __version__

__author__ = "ani-it contributors"
__license__ = "GPL-3.0-or-later"

_LAZY_EXPORTS = {
    "APP_NAME": "ani_it.constants",
    "BASE_URL": "ani_it.constants",
    "DEFAULT_BASE_URL": "ani_it.constants",
    "DEFAULT_USER_AGENT": "ani_it.constants",
    "Config": "ani_it.config",
    "load_config": "ani_it.config",
    "HistoryManager": "ani_it.history",
    "AnimeUnityScraper": "ani_it.scraper",
    "StreamResolver": "ani_it.resolver",
    "MpvController": "ani_it.player",
    "DownloaderManager": "ani_it.downloader",
}

__all__ = [
    "__version__",
    "__author__",
    "__license__",
    *_LAZY_EXPORTS,
]


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'ani_it' has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value  # le richieste successive non passano più di qui
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
