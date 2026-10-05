"""ani-it: Client CLI/TUI nativo per lo streaming di anime da AnimeUnity in italiano.

Ottimizzato per Arch Linux (Wayland/X11, PipeWire, MPV IPC).
"""

__version__ = "1.0.0"
__author__ = "ani-it contributors"
__license__ = "GPL-3.0-or-later"

from ani_it.constants import (
    APP_NAME,
    BASE_URL,
    DEFAULT_USER_AGENT,
)
from ani_it.config import Config, load_config
from ani_it.history import HistoryManager
from ani_it.scraper import AnimeUnityScraper
from ani_it.resolver import StreamResolver
from ani_it.player import MpvController
from ani_it.downloader import DownloaderManager

__all__ = [
    "__version__",
    "__author__",
    "__license__",
    "APP_NAME",
    "BASE_URL",
    "DEFAULT_USER_AGENT",
    "Config",
    "load_config",
    "HistoryManager",
    "AnimeUnityScraper",
    "StreamResolver",
    "MpvController",
    "DownloaderManager",
]
