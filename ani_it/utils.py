"""Funzioni di utilità cross-platform per ani-it v2.0.

Gestione percorsi conforme agli standard di sistema (Linux/XDG, macOS, Windows),
sanitizzazione di sicurezza contro injection/path-traversal, deoffuscamento JS,
rilevamento dipendenze e console ANSI.
"""

import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Final, Iterable, Optional
from urllib.parse import urlparse

from ani_it.constants import APP_NAME, Colors


# ==============================================================================
# RILEVAMENTO SISTEMA OPERATIVO & DISPLAY
# ==============================================================================

def is_windows() -> bool:
    """True se l'applicazione è in esecuzione su Windows."""
    return sys.platform == "win32"


def is_macos() -> bool:
    """True se l'applicazione è in esecuzione su macOS (Darwin)."""
    return sys.platform == "darwin"


def is_linux() -> bool:
    """True se l'applicazione è in esecuzione su Linux."""
    return sys.platform.startswith("linux")


def detect_display_server() -> str:
    """Rileva l'ambiente grafico corrente: wayland, x11, darwin, win32 o tty."""
    if is_windows():
        return "win32"
    if is_macos():
        return "darwin"
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "tty"


def is_tty() -> bool:
    """Verifica se stdout è collegato a un terminale interattivo."""
    return sys.stdout.isatty()


def enable_windows_ansi() -> bool:
    """Abilita il supporto sequenze di controllo ANSI su Windows 10/11."""
    if not is_windows():
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        for handle_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            handle = kernel32.GetStdHandle(handle_id)
            if handle and handle != -1:
                mode = ctypes.c_ulong()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                    kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        return True
    except Exception:
        return False


# ==============================================================================
# GESTIONE PERCORSI FILESYSTEM CROSS-PLATFORM
# ==============================================================================

def get_config_dir() -> Path:
    """Restituisce il percorso della directory di configurazione conforme al SO."""
    if is_windows():
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    elif is_macos():
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg_config) if xdg_config else Path.home() / ".config"

    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_state_dir() -> Path:
    """Restituisce il percorso della directory di stato/dati conforme al SO."""
    if is_windows():
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif is_macos():
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg_state = os.environ.get("XDG_STATE_HOME")
        base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"

    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_cache_dir() -> Path:
    """Restituisce il percorso della directory di cache persistente conforme al SO."""
    if is_windows():
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / APP_NAME / "cache"
        base.mkdir(parents=True, exist_ok=True)
        return base
    elif is_macos():
        base = Path.home() / "Library" / "Caches" / APP_NAME
        base.mkdir(parents=True, exist_ok=True)
        return base
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
        path = base / APP_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path


FALLBACK_RUNTIME_BASE: Final[Path] = Path("/tmp")


def _private_dir(path: Path) -> Path:
    """Crea una directory con permessi 0700 e verifica che sia dell'utente corrente."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise PermissionError(f"{path} non è una directory (o è un symlink)")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise PermissionError(f"{path} appartiene a un altro utente")
    if hasattr(os, "chmod") and not is_windows():
        if stat.S_IMODE(info.st_mode) & 0o077:
            path.chmod(0o700)
    return path


def get_runtime_dir() -> Path:
    """Restituisce una directory privata (0700) per socket IPC."""
    if is_windows():
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / APP_NAME / "runtime"
        base.mkdir(parents=True, exist_ok=True)
        return base

    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        candidate = Path(xdg_runtime) / APP_NAME
    else:
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        candidate = FALLBACK_RUNTIME_BASE / f"ani-it-{uid}"

    try:
        return _private_dir(candidate)
    except OSError:
        return Path(tempfile.mkdtemp(prefix=f"{APP_NAME}-"))


def get_download_dir() -> Path:
    """Recupera la directory Anime nei Download dell'utente in modo agnostico rispetto all'OS."""
    if is_windows() or is_macos():
        return Path.home() / "Downloads" / "Anime"

    xdg_download = os.environ.get("XDG_DOWNLOAD_DIR")
    if xdg_download and Path(xdg_download).is_dir():
        return Path(xdg_download) / "Anime"

    try:
        res = subprocess.run(
            ["xdg-user-dir", "DOWNLOAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            candidate = Path(res.stdout.strip())
            if candidate.is_dir():
                return candidate / "Anime"
    except Exception:
        pass

    return Path.home() / "Downloads" / "Anime"


# ==============================================================================
# SICUREZZA, SANITIZZAZIONE E VALIDAZIONE
# ==============================================================================

MAX_FILENAME_BYTES: Final[int] = 200

# Caratteri illegali su filesystem Windows/Unix o pericolosi per l'interprete dei comandi
ILLEGAL_FILENAME_CHARS_RE: Final[re.Pattern[str]] = re.compile(r'[/\\?%*:|"<>$&;]')
CONTROL_CHARS_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_filename(name: str) -> str:
    """Sanitizza un nome di file o cartella per prevenire directory traversal e injection.

    Rimuove caratteri non validi su Unix e Windows (/ \\ : * ? " < > | \0 $ & ;),
    blocca sequenze di Directory Traversal (..), converte caratteri di controllo in spazi,
    rimuove punti e spazi ai bordi e limita la dimensione a 200 byte.
    """
    if not name:
        return "anime"

    # 1. Rimuovi sequenze di directory traversal (..)
    cleaned = re.sub(r"\.{2,}", "_", str(name))

    # 2. Sostituisci caratteri proibiti con underscore
    cleaned = ILLEGAL_FILENAME_CHARS_RE.sub("_", cleaned)

    # 3. Sostituisci caratteri di controllo con spazi
    cleaned = CONTROL_CHARS_RE.sub(" ", cleaned)

    # 4. Collassa spazi multipli ed elimina spazi/punti ai bordi
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")

    # 5. Troncamento a livello di byte mantenendo caratteri UTF-8 validi
    encoded = cleaned.encode("utf-8")
    if len(encoded) > MAX_FILENAME_BYTES:
        cleaned = encoded[:MAX_FILENAME_BYTES].decode("utf-8", errors="ignore").strip(" .")

    return cleaned or "anime"


def is_safe_url(url: str) -> bool:
    """Verifica che l'URL utilizzi unicamente schemi sicuri http o https."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def check_binary(binary_name: str) -> bool:
    """Verifica se un comando o binario è presente nel PATH del sistema."""
    return shutil.which(binary_name) is not None


def check_required_dependencies(player_binary: str = "mpv") -> tuple[bool, list[str]]:
    """Controlla i binari obbligatori: fzf e il player configurato."""
    missing: list[str] = []
    for dep in ("fzf", player_binary):
        if not check_binary(dep):
            missing.append(dep)
    return len(missing) == 0, missing


def check_optional_dependencies() -> list[str]:
    """Binari opzionali per download ad alta velocità o anteprime."""
    return [dep for dep in ("yt-dlp",) if not check_binary(dep)]


# ==============================================================================
# FORMATTAZIONE E NUMERAZIONE EPISODI
# ==============================================================================

def format_duration(seconds: float) -> str:
    """Formatta i secondi in formato leggibile mm:ss o hh:mm:ss."""
    sec = int(seconds)
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    secs = sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def quality_height(quality: str) -> Optional[int]:
    """Altezza in pixel di una qualità come '720p' o '720'; None per 'best'."""
    match = re.search(r"\d+", str(quality))
    return int(match.group()) if match else None


EpisodeNumber = int | float

_EPISODE_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"^\d+(?:\.\d+)?$")


def normalize_episode_number(value: Any) -> Optional[EpisodeNumber]:
    """Restituisce il numero reale di un episodio (int se intero, float se decimale es. 12.5)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not _EPISODE_NUMBER_RE.match(text):
            return None
        number = float(text)
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _parse_range_bound(text: str, token: str) -> EpisodeNumber:
    number = normalize_episode_number(text.strip())
    if number is None:
        raise ValueError(f"Intervallo episodi non valido: {token!r}")
    return number


def parse_episode_range(
    range_str: str,
    available: Iterable[Any],
) -> tuple[list[EpisodeNumber], list[EpisodeNumber]]:
    """Interpreta '1-12', '1,3,5', '12.5', 'all' contro i numeri di episodio realmente presenti."""
    numbers = sorted({n for n in map(normalize_episode_number, available) if n is not None})
    spec = range_str.strip().lower()
    if spec in ("all", "*"):
        return numbers, []

    present = set(numbers)
    selected: set[EpisodeNumber] = set()
    missing: set[EpisodeNumber] = set()

    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue

        if "-" in token:
            left, _, right = token.partition("-")
            first_bound = _parse_range_bound(left, token)
            second_bound = _parse_range_bound(right, token)
            low, high = min(first_bound, second_bound), max(first_bound, second_bound)
            selected.update(n for n in numbers if low <= n <= high)

            if numbers:
                candidate = math.ceil(max(low, numbers[0]))
                last = min(high, numbers[-1])
                while candidate <= last:
                    if candidate not in present:
                        missing.add(candidate)
                    candidate += 1
        else:
            number = _parse_range_bound(token, token)
            if number in present:
                selected.add(number)
            else:
                missing.add(number)

    return sorted(selected), sorted(missing)


# ==============================================================================
# GESTIONE SEGNALI E PROCESSI FIGLI
# ==============================================================================

class SignalHandler:
    """Gestore unificato per segnali di terminazione e ripristino del terminale."""

    _active_processes: list[subprocess.Popen[Any]] = []
    _cleanup_callbacks: list[Callable[[], None]] = []
    _installed: bool = False

    @classmethod
    def register_process(cls, proc: subprocess.Popen[Any]) -> None:
        cls._active_processes.append(proc)

    @classmethod
    def unregister_process(cls, proc: subprocess.Popen[Any]) -> None:
        if proc in cls._active_processes:
            cls._active_processes.remove(proc)

    @classmethod
    def register_cleanup(cls, callback: Callable[[], None]) -> None:
        if callback not in cls._cleanup_callbacks:
            cls._cleanup_callbacks.append(callback)

    @classmethod
    def unregister_cleanup(cls, callback: Callable[[], None]) -> None:
        if callback in cls._cleanup_callbacks:
            cls._cleanup_callbacks.remove(callback)

    @classmethod
    def install(cls) -> None:
        """Installa hook di terminazione per interruzione da tastiera o segnali di sistema."""
        if cls._installed:
            return
        cls._installed = True

        def _handler(signum: int, frame: Any) -> None:
            for proc in list(cls._active_processes):
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

            for cb in cls._cleanup_callbacks:
                try:
                    cb()
                except Exception:
                    pass

            sys.stderr.write(Colors.SHOW_CURSOR + Colors.RESET + "\n")
            sys.stderr.flush()
            sys.exit(130 if signum == signal.SIGINT else 143)

        signal.signal(signal.SIGINT, _handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handler)


# ==============================================================================
# DEOFFUSCAMENTO JAVASCRIPT PACKER (DEAN EDWARDS)
# ==============================================================================

def _baseN_to_int(token: str, radix: int) -> int:
    val = 0
    for char in token:
        if "0" <= char <= "9":
            digit = ord(char) - ord("0")
        elif "a" <= char <= "z":
            digit = ord(char) - ord("a") + 10
        elif "A" <= char <= "Z":
            digit = ord(char) - ord("A") + 36
        else:
            return -1
        if digit >= radix:
            return -1
        val = val * radix + digit
    return val


def unpack_js(packed_code: str) -> str:
    """Estrae e deoffusca codice JavaScript impacchettato con Dean Edwards Packer."""
    pattern = re.compile(
        r"\}\s*\(\s*(['\"].*?['\"])\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(['\"].*?['\"])\.split\(\s*['\"]\|['\"]\s*\)",
        re.DOTALL,
    )

    match = pattern.search(packed_code)
    if not match:
        alt_pattern = re.compile(
            r"return\s+p\}\s*\(\s*(['\"].*?['\"])\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\[[^\]]*\]|['\"].*?['\"])",
            re.DOTALL,
        )
        match = alt_pattern.search(packed_code)
        if not match:
            return packed_code

    payload_raw, radix_str, _count, words_raw = match.groups()
    payload = payload_raw[1:-1]
    payload = payload.replace(r"\'", "'").replace(r'\"', '"').replace(r"\\", "\\")

    try:
        radix = int(radix_str)
    except ValueError:
        return packed_code

    if words_raw.startswith("["):
        words_cleaned = words_raw[1:-1].split(",")
        words = [w.strip().strip("'\"") for w in words_cleaned]
    else:
        words = words_raw[1:-1].split("|")

    def replace_token(m: re.Match[str]) -> str:
        word = m.group(0)
        idx = _baseN_to_int(word, radix)
        if 0 <= idx < len(words) and words[idx]:
            return words[idx]
        return word

    unpacked = re.sub(r"\b\w+\b", replace_token, payload)
    return unpacked
