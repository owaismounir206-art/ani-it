"""Funzioni di utilità per ani-it: conformità XDG, deoffuscamento JS, rilevamento display e gestione segnali."""

import os
import re
import sys
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Callable, Any, Optional

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


def get_runtime_dir() -> Path:
    """Restituisce il percorso per socket e runtime IPC conforme a XDG."""
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        path = Path(xdg_runtime) / APP_NAME
    else:
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        path = Path(f"/tmp/ani-it-{uid}")
    path.mkdir(parents=True, exist_ok=True)
    return path


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

    fallback = Path.home() / "Downloads" / "Anime"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


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


def check_required_dependencies() -> tuple[bool, list[str]]:
    """Controlla la presenza delle dipendenze di sistema minime (fzf, mpv)."""
    missing: list[str] = []
    for dep in ("fzf", "mpv"):
        if not check_binary(dep):
            missing.append(dep)
    return len(missing) == 0, missing


def sanitize_filename(name: str) -> str:
    """Rimuove caratteri non validi nei filesystem POSIX/Linux."""
    # Rimuovi slash, caratteri di controllo e simboli rischiosi
    cleaned = re.sub(r'[/\\?%*:|"<>]', "_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
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


def parse_episode_range(range_str: str, max_ep: int) -> list[int]:
    """Interpreta stringhe di range come '1-12', '1,3,5', 'all', o '5'."""
    s = range_str.strip().lower()
    if s in ("all", "*"):
        return list(range(1, max_ep + 1))

    episodes: set[int] = set()
    parts = s.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            sub = part.split("-", 1)
            try:
                start = int(sub[0])
                end = int(sub[1])
                for ep in range(min(start, end), max(start, end) + 1):
                    if 1 <= ep <= max_ep:
                        episodes.add(ep)
            except ValueError:
                continue
        else:
            try:
                ep = int(part)
                if 1 <= ep <= max_ep:
                    episodes.add(ep)
            except ValueError:
                continue

    return sorted(list(episodes))


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
        """Registra una funzione di pulizia (es. rimozione socket temporanei)."""
        cls._cleanup_callbacks.append(callback)

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

    payload_raw, radix_str, count_str, words_raw = match.groups()

    # Pulisci payload
    payload = payload_raw[1:-1]  # Rimuovi apici esterni
    # Gestisci escape
    payload = payload.replace(r"\'", "'").replace(r'\"', '"').replace(r"\\", "\\")

    try:
        radix = int(radix_str)
        count = int(count_str)
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
