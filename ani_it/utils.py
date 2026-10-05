"""Funzioni di utilità per ani-it: conformità XDG, deoffuscamento JS, rilevamento display e gestione segnali."""

import math
import os
import re
import sys
import shutil
import signal
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Any, Iterable, Optional

from ani_it.constants import APP_NAME, Colors


def get_config_dir() -> Path:
    """Restituisce il percorso della directory di configurazione conforme a XDG."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_state_dir() -> Path:
    """Restituisce il percorso della directory di stato/dati conforme a XDG."""
    xdg_state = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_cache_dir() -> Path:
    """Restituisce il percorso della directory di cache temporanea conforme a XDG."""
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


# Base del percorso di ripiego quando XDG_RUNTIME_DIR non è definita
FALLBACK_RUNTIME_BASE = Path("/tmp")


def _private_dir(path: Path) -> Path:
    """Crea `path` con permessi 0700 e verifica che sia una directory dell'utente corrente.

    Solleva OSError se `path` è un symlink, non è una directory o appartiene a un altro
    utente (un percorso prevedibile in /tmp può essere stato predisposto da qualcun altro).
    Una directory propria con permessi troppo larghi viene ristretta a 0700.
    """
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()  # lstat: non segue i symlink
    if not stat.S_ISDIR(info.st_mode):
        raise PermissionError(f"{path} non è una directory (o è un symlink)")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise PermissionError(f"{path} appartiene a un altro utente")
    if stat.S_IMODE(info.st_mode) & 0o077:
        path.chmod(0o700)
    return path


def get_runtime_dir() -> Path:
    """Restituisce una directory privata (0700) per socket e runtime IPC, conforme a XDG."""
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        candidate = Path(xdg_runtime) / APP_NAME
    else:
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        candidate = FALLBACK_RUNTIME_BASE / f"ani-it-{uid}"

    try:
        return _private_dir(candidate)
    except OSError:
        # Percorso non utilizzabile in modo sicuro: directory temporanea nuova (0700)
        return Path(tempfile.mkdtemp(prefix=f"{APP_NAME}-"))


def get_download_dir() -> Path:
    """Recupera la directory Download dell'utente tramite XDG o fallback su ~/Downloads."""
    xdg_download = os.environ.get("XDG_DOWNLOAD_DIR")
    if xdg_download and Path(xdg_download).is_dir():
        return Path(xdg_download) / "Anime"

    # Tentativo con xdg-user-dir
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

    # Nessuna creazione qui: la cartella viene creata al momento del download
    return Path.home() / "Downloads" / "Anime"


def detect_display_server() -> str:
    """Rileva se l'ambiente grafico corrente è Wayland, X11 o TTY pura."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "tty"


def is_tty() -> bool:
    """Verifica se l'output standard è collegato a un terminale interattivo."""
    return sys.stdout.isatty()


def check_binary(binary_name: str) -> bool:
    """Verifica se un comando o binario è presente nel PATH del sistema."""
    return shutil.which(binary_name) is not None


def check_required_dependencies(player_binary: str = "mpv") -> tuple[bool, list[str]]:
    """Controlla i binari senza i quali il programma non può funzionare: fzf e il player configurato."""
    missing: list[str] = []
    for dep in ("fzf", player_binary):
        if not check_binary(dep):
            missing.append(dep)
    return len(missing) == 0, missing


def check_optional_dependencies() -> list[str]:
    """Binari che servono solo per i download (yt-dlp): la loro assenza non blocca lo streaming."""
    return [dep for dep in ("yt-dlp",) if not check_binary(dep)]


# Lunghezza massima in byte del nome di una cartella/file (il limite dei filesystem Linux è 255)
MAX_FILENAME_BYTES = 200


def sanitize_filename(name: str) -> str:
    """Rende sicuro per il filesystem un titolo che arriva dall'API del sito.

    Sostituisce i caratteri non validi e i caratteri di controllo, elimina punti e spazi
    ai bordi (così `..` non può risalire di cartella e il nome non diventa un file nascosto)
    e limita la lunghezza in byte.
    """
    cleaned = re.sub(r'[/\\?%*:|"<>]', "_", name)
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned.encode("utf-8")) > MAX_FILENAME_BYTES:
        cleaned = cleaned.encode("utf-8")[:MAX_FILENAME_BYTES].decode("utf-8", errors="ignore").strip(" .")
    return cleaned or "anime"


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
    """Altezza in pixel di una qualità come '720p' o '720'; None per 'best' o valori non validi."""
    match = re.search(r"\d+", str(quality))
    return int(match.group()) if match else None


EpisodeNumber = int | float

_EPISODE_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")


def normalize_episode_number(value: Any) -> Optional[EpisodeNumber]:
    """Restituisce il numero reale di un episodio: int se intero, float se decimale (12.5).

    Restituisce None se il valore non è un numero valido (None, '', 'abc', '1.2.3', 'nan').
    """
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
    """Interpreta '1-12', '1,3,5', '12.5', 'all' contro i numeri di episodio realmente presenti.

    Args:
        range_str: Intervallo richiesto dall'utente.
        available: Numeri degli episodi esistenti (anche con buchi, decimali o partenza da 0).

    Returns:
        (selezionati, mancanti): gli episodi presenti da scaricare e i numeri richiesti
        che non esistono. Per gli intervalli si segnalano solo i buchi interni alla
        numerazione, non i numeri oltre l'ultimo episodio.

    Raises:
        ValueError: se un elemento dell'intervallo non è un numero valido.
    """
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


class SignalHandler:
    """Gestore unificato per SIGINT/SIGTERM per terminare sottoprocessi e ripristinare il terminale."""

    _active_processes: list[subprocess.Popen[Any]] = []
    _cleanup_callbacks: list[Callable[[], None]] = []
    _installed: bool = False

    @classmethod
    def register_process(cls, proc: subprocess.Popen[Any]) -> None:
        """Registra un processo attivo da monitorare e terminare in uscita."""
        cls._active_processes.append(proc)

    @classmethod
    def unregister_process(cls, proc: subprocess.Popen[Any]) -> None:
        """Rimuove un processo completato dall'elenco attivo."""
        if proc in cls._active_processes:
            cls._active_processes.remove(proc)

    @classmethod
    def register_cleanup(cls, callback: Callable[[], None]) -> None:
        """Registra una funzione di pulizia (es. rimozione socket temporanei). Senza duplicati."""
        if callback not in cls._cleanup_callbacks:
            cls._cleanup_callbacks.append(callback)

    @classmethod
    def unregister_cleanup(cls, callback: Callable[[], None]) -> None:
        """Rimuove una funzione di pulizia registrata (non fa nulla se assente)."""
        if callback in cls._cleanup_callbacks:
            cls._cleanup_callbacks.remove(callback)

    @classmethod
    def install(cls) -> None:
        """Installa gli hook di cattura dei segnali POSIX."""
        if cls._installed:
            return
        cls._installed = True

        def _handler(signum: int, frame: Any) -> None:
            # Termina i processi figli attivi
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

            # Esegui pulizie registrate
            for cb in cls._cleanup_callbacks:
                try:
                    cb()
                except Exception:
                    pass

            # Ripristina visibilità cursore
            sys.stderr.write(Colors.SHOW_CURSOR + Colors.RESET + "\n")
            sys.stderr.flush()
            sys.exit(130 if signum == signal.SIGINT else 143)

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)


# ==============================================================================
# DEOFFUSCAMENTO JAVASCRIPT PACKER (DEAN EDWARDS)
# ==============================================================================

def _baseN_to_int(token: str, radix: int) -> int:
    """Converte un token alfabetico in base N in intero (supporta fino a base 62)."""
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
    """Estrae e deoffusca codice JavaScript impacchettato con Dean Edwards Packer.

    Cerca pattern del tipo:
    eval(function(p,a,c,k,e,d){...}('payload', a, c, 'k1|k2|k3'.split('|'), 0, {}))
    """
    pattern = re.compile(
        r"\}\s*\(\s*(['\"].*?['\"])\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(['\"].*?['\"])\.split\(\s*['\"]\|['\"]\s*\)",
        re.DOTALL,
    )

    match = pattern.search(packed_code)
    if not match:
        # Pattern alternativo con argomenti su righe separate
        alt_pattern = re.compile(
            r"return\s+p\}\s*\(\s*(['\"].*?['\"])\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\[[^\]]*\]|['\"].*?['\"])",
            re.DOTALL,
        )
        match = alt_pattern.search(packed_code)
        if not match:
            return packed_code

    payload_raw, radix_str, _count, words_raw = match.groups()

    # Pulisci payload
    payload = payload_raw[1:-1]  # Rimuovi apici esterni
    # Gestisci escape
    payload = payload.replace(r"\'", "'").replace(r'\"', '"').replace(r"\\", "\\")

    try:
        radix = int(radix_str)
    except ValueError:
        return packed_code

    if words_raw.startswith("["):
        # Parsing array JSON o Python
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
