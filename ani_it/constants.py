"""Costanti globali, endpoint di AnimeUnity, template, regex compilate e codici ANSI per ani-it v2.0."""

import re
from dataclasses import dataclass
from typing import Final

from ani_it._version import __version__

# Informazioni applicazione
APP_NAME: Final[str] = "ani-it"
APP_VERSION: Final[str] = __version__

# Endpoint AnimeUnity. Il dominio cambia spesso: quello predefinito è sovrascrivibile da
# `general.base_url` in config.toml, e gli endpoint si derivano con Endpoints(base_url).
DEFAULT_BASE_URL: Final[str] = "https://www.animeunity.so"
BASE_URL: Final[str] = DEFAULT_BASE_URL

# Timeout HTTP rigidi: (connessione 3.5s, lettura 15s)
HTTP_TIMEOUT: Final[tuple[float, float]] = (3.5, 15.0)


@dataclass(frozen=True)
class Endpoints:
    """Endpoint di AnimeUnity derivati da un dominio base."""

    base_url: str

    @property
    def archive(self) -> str:
        return f"{self.base_url}/archivio"

    @property
    def info(self) -> str:
        return f"{self.base_url}/anime"

    @property
    def embed_url(self) -> str:
        return f"{self.base_url}/embed-url"

    @property
    def embed_pc(self) -> str:
        return f"{self.base_url}/embed-pc"

    @property
    def livesearch(self) -> str:
        return f"{self.base_url}/livesearch"

    @property
    def get_animes(self) -> str:
        return f"{self.base_url}/archivio/get-animes"

    @property
    def info_api(self) -> str:
        return f"{self.base_url}/info_api"


# Header di navigazione realistici
DEFAULT_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def site_headers(base_url: str = DEFAULT_BASE_URL) -> dict[str, str]:
    """Header di navigazione per le richieste verso AnimeUnity (Referer/Origin dal dominio base)."""
    return {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": base_url,
        "Origin": base_url,
        "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Linux"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


DEFAULT_HEADERS: Final[dict[str, str]] = site_headers(DEFAULT_BASE_URL)


# ==============================================================================
# REGEX COMPILATE STATICHE AD ALTE PRESTAZIONI (ZERO-OVERHEAD SCRAPING)
# ==============================================================================

RE_ANIMES: Final[re.Pattern[str]] = re.compile(
    r':animes=["\'](\[\{.*?\}\])["\']', re.DOTALL
)
RE_EPISODES: Final[re.Pattern[str]] = re.compile(
    r':episodes=["\'](\[\{.*?\}\])["\']', re.DOTALL
)
RE_ANIME: Final[re.Pattern[str]] = re.compile(
    r':anime=["\'](\{.*?\})["\']', re.DOTALL
)
RE_EPISODE: Final[re.Pattern[str]] = re.compile(
    r':episode=["\'](\{.*?\})["\']', re.DOTALL
)

RE_CSRF: Final[re.Pattern[str]] = re.compile(
    r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']|'
    r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']csrf-token["\']',
    re.IGNORECASE,
)

RE_H1_TITLE: Final[re.Pattern[str]] = re.compile(r'<h1[^>]*>(.*?)</h1>', re.IGNORECASE | re.DOTALL)
RE_TITLE_TAG: Final[re.Pattern[str]] = re.compile(r'<title[^>]*>(.*?)</title>', re.IGNORECASE | re.DOTALL)
RE_PLOT: Final[re.Pattern[str]] = re.compile(
    r'<p[^>]*class=["\'][^"\']*(?:plot|description|trama)[^"\']*["\'][^>]*>(.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
RE_COVER_IMG: Final[re.Pattern[str]] = re.compile(
    r'<img[^>]*class=["\'][^"\']*(?:poster|cover|anime-image)[^"\']*["\'][^>]*src=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
RE_COVER_IMG_ALT: Final[re.Pattern[str]] = re.compile(
    r'<img[^>]*src=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*(?:poster|cover|anime-image)[^"\']*["\']',
    re.IGNORECASE,
)
RE_SCORE: Final[re.Pattern[str]] = re.compile(
    r'class=["\'][^"\']*(?:score|rating|vote)[^"\']*["\'][^>]*>([^<]+)<',
    re.IGNORECASE,
)
RE_GENRES: Final[re.Pattern[str]] = re.compile(
    r'<a[^>]*href=["\'][^"\']*/archivio\?[^"\']*genre[^"\']*["\'][^>]*>([^<]+)</a>',
    re.IGNORECASE,
)
RE_SCRIPT_EPISODES: Final[re.Pattern[str]] = re.compile(
    r'(?:var|let)?\s*episodes\s*[:=]\s*(\[\{.*?\}\])',
    re.DOTALL,
)


# Codici Colori ANSI ed Escape Sequences
class Colors:
    """Codici di formattazione ANSI per il terminale."""
    RESET: Final[str] = "\033[0m"
    BOLD: Final[str] = "\033[1m"
    DIM: Final[str] = "\033[2m"
    ITALIC: Final[str] = "\033[3m"
    UNDERLINE: Final[str] = "\033[4m"

    # Colori base testo
    BLACK: Final[str] = "\033[30m"
    RED: Final[str] = "\033[31m"
    GREEN: Final[str] = "\033[32m"
    YELLOW: Final[str] = "\033[33m"
    BLUE: Final[str] = "\033[34m"
    MAGENTA: Final[str] = "\033[35m"
    CYAN: Final[str] = "\033[36m"
    WHITE: Final[str] = "\033[37m"

    # Colori chiari/intensi testo
    BRIGHT_BLACK: Final[str] = "\033[90m"
    BRIGHT_RED: Final[str] = "\033[91m"
    BRIGHT_GREEN: Final[str] = "\033[92m"
    BRIGHT_YELLOW: Final[str] = "\033[93m"
    BRIGHT_BLUE: Final[str] = "\033[94m"
    BRIGHT_MAGENTA: Final[str] = "\033[95m"
    BRIGHT_CYAN: Final[str] = "\033[96m"
    BRIGHT_WHITE: Final[str] = "\033[97m"

    # Sfondi
    BG_BLACK: Final[str] = "\033[40m"
    BG_RED: Final[str] = "\033[41m"
    BG_GREEN: Final[str] = "\033[42m"
    BG_YELLOW: Final[str] = "\033[43m"
    BG_BLUE: Final[str] = "\033[44m"
    BG_MAGENTA: Final[str] = "\033[45m"
    BG_CYAN: Final[str] = "\033[46m"
    BG_WHITE: Final[str] = "\033[47m"

    # Controllo cursore e schermo
    CLEAR_LINE: Final[str] = "\033[2K"
    CLEAR_SCREEN: Final[str] = "\033[2J\033[H"
    HIDE_CURSOR: Final[str] = "\033[?25l"
    SHOW_CURSOR: Final[str] = "\033[?25h"


# Icone e simboli (Nerd Fonts v3.x con fallback Unicode pulito)
class Icons:
    """Icone con simboli Nerd Font e caratteri grafici per la TUI."""
    PLAY: Final[str] = "󰐊"         # nf-md-play
    PAUSE: Final[str] = "󰏤"        # nf-md-pause
    STOP: Final[str] = "󰓛"         # nf-md-stop
    DOWNLOAD: Final[str] = "󰇚"     # nf-md-download
    CHECK: Final[str] = "󰄬"        # nf-md-check
    CROSS: Final[str] = "󰅖"        # nf-md-close
    FILM: Final[str] = "󰎁"         # nf-md-filmstrip
    TV: Final[str] = "󰵔"           # nf-md-television_classic
    CLOCK: Final[str] = "󰥔"        # nf-md-clock
    STAR: Final[str] = "󰓎"         # nf-md-star
    EYE: Final[str] = "󰈈"          # nf-md-eye
    PROGRESS: Final[str] = "󰔟"     # nf-md-progress_clock
    SEARCH: Final[str] = "󰍉"       # nf-md-magnify
    FOLDER: Final[str] = "󰉋"       # nf-md-folder
    LINK: Final[str] = "󰌷"         # nf-md-link
    ARROW_RIGHT: Final[str] = "󰁕"  # nf-md-arrow_right
    BACK: Final[str] = "󰁍"         # nf-md-arrow_left
    REFRESH: Final[str] = "󰑐"      # nf-md-refresh
    SPARKLE: Final[str] = "󰄵"      # nf-md-sparkles
    INFO: Final[str] = "󰋽"         # nf-md-information
    WARNING: Final[str] = "󰀪"      # nf-md-alert
    ERROR: Final[str] = "󰅚"        # nf-md-alert_circle
    ROCKET: Final[str] = "󰛡"       # nf-md-rocket


# Nome del socket IPC di mpv
SOCKET_NAME_TEMPLATE: Final[str] = "ani-it-mpv-{pid}.sock"
PIPE_NAME_TEMPLATE: Final[str] = r"\\.\pipe\ani-it-mpv-{uuid}"

# Formati stream riconosciuti
FORMAT_HLS: Final[str] = "hls"
FORMAT_MP4: Final[str] = "mp4"

# Risoluzioni supportate
RESOLUTIONS: Final[tuple[str, ...]] = ("1080p", "720p", "480p", "best")

# Stati formali della macchina a stati della TUI
STATE_SEARCHING: Final[str] = "SEARCHING"
STATE_ANIME_SELECT: Final[str] = "ANIME_SELECT"
STATE_EPISODE_SELECT: Final[str] = "EPISODE_SELECT"
STATE_PLAYING: Final[str] = "PLAYING"
STATE_POST_WATCH: Final[str] = "POST_WATCH"
STATE_DOWNLOADING: Final[str] = "DOWNLOADING"
STATE_IDLE: Final[str] = "IDLE"
