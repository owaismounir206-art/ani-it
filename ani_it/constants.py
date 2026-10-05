"""Costanti globali, endpoint di AnimeUnity, template e codici ANSI per ani-it."""

from typing import Final

# Informazioni applicazione
APP_NAME: Final[str] = "ani-it"
APP_VERSION: Final[str] = "1.0.0"

# Endpoint AnimeUnity
BASE_URL: Final[str] = "https://www.animeunity.so"
ARCHIVE_ENDPOINT: Final[str] = f"{BASE_URL}/archivio"
INFO_ENDPOINT: Final[str] = f"{BASE_URL}/anime"
EMBED_ENDPOINT: Final[str] = f"{BASE_URL}/embed-pc"
LIVESEARCH_ENDPOINT: Final[str] = f"{BASE_URL}/livesearch"
GET_ANIMES_ENDPOINT: Final[str] = f"{BASE_URL}/archivio/get-animes"
INFO_API_ENDPOINT: Final[str] = f"{BASE_URL}/info_api"

# Header di navigazione realistici
DEFAULT_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS: Final[dict[str, str]] = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": BASE_URL,
    "Origin": BASE_URL,
    "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Linux"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

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


# Nome socket predefinito
DEFAULT_SOCKET_NAME: Final[str] = "ani-it-mpv.sock"

# Formati stream riconosciuti
FORMAT_HLS: Final[str] = "hls"
FORMAT_MP4: Final[str] = "mp4"

# Risoluzioni supportate
RESOLUTIONS: Final[tuple[str, ...]] = ("1080p", "720p", "480p", "best")
