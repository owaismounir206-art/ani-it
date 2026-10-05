"""Gestione della configurazione TOML conforme a XDG per ani-it."""

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ani_it.constants import DEFAULT_BASE_URL, RESOLUTIONS
from ani_it.utils import get_config_dir

CONFIG_FILE_NAME = "config.toml"

DEFAULT_CONFIG_TEMPLATE = """# ==============================================================================
# File di configurazione per ani-it
# Percorso: ~/.config/ani-it/config.toml
# ==============================================================================

[player]
# Percorso del binario o nome comando del player video (predefinito: "mpv")
binary = "mpv"

# Argomenti addizionali passati a mpv
args = ["--hwdec=auto-safe", "--geometry=1280x720"]

[downloader]
# Binario per accelerazione download segmentati (predefinito: "aria2c")
binary = "aria2c"

# Argomenti passati a aria2c (connessioni simultanee, split e chunk)
args = ["-x", "16", "-s", "16", "-k", "1M", "-j", "4"]

[general]
# Dominio di AnimeUnity: cambia spesso, aggiornalo qui se il sito si sposta
base_url = "https://www.animeunity.so"

# Risoluzione preferita: "1080p", "720p", "480p", "best"
quality = "best"

# Mostra anteprima locandina tramite chafa nel riquadro di destra di fzf
preview_art = true

# Passa automaticamente all'episodio successivo al termine di mpv
auto_next = false
"""


@dataclass
class PlayerConfig:
    """Configurazione del video player."""
    binary: str = "mpv"
    args: list[str] = field(default_factory=lambda: ["--hwdec=auto-safe", "--geometry=1280x720"])


@dataclass
class DownloaderConfig:
    """Configurazione del motore di download."""
    binary: str = "aria2c"
    args: list[str] = field(default_factory=lambda: ["-x", "16", "-s", "16", "-k", "1M", "-j", "4"])


@dataclass
class GeneralConfig:
    """Impostazioni generali del programma."""
    base_url: str = DEFAULT_BASE_URL
    quality: str = "best"
    preview_art: bool = True
    auto_next: bool = False


@dataclass
class Config:
    """Oggetto contenitore principale della configurazione dell'applicazione."""
    player: PlayerConfig = field(default_factory=PlayerConfig)
    downloader: DownloaderConfig = field(default_factory=DownloaderConfig)
    general: GeneralConfig = field(default_factory=GeneralConfig)

    def to_toml(self) -> str:
        """Serializza la configurazione corrente in formato TOML leggibile."""
        def string_list(items: list[str]) -> str:
            return f"[{', '.join(_toml_string(x) for x in items)}]"

        return f"""[player]
binary = {_toml_string(self.player.binary)}
args = {string_list(self.player.args)}

[downloader]
binary = {_toml_string(self.downloader.binary)}
args = {string_list(self.downloader.args)}

[general]
base_url = {_toml_string(self.general.base_url)}
quality = {_toml_string(self.general.quality)}
preview_art = {'true' if self.general.preview_art else 'false'}
auto_next = {'true' if self.general.auto_next else 'false'}
"""


_TOML_ESCAPES = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _toml_string(value: str) -> str:
    """Stringa TOML tra virgolette con escape di backslash, virgolette e caratteri di controllo."""
    out = []
    for char in value:
        if char in _TOML_ESCAPES:
            out.append(_TOML_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def get_config_path() -> Path:
    """Restituisce il path completo del file di configurazione config.toml."""
    return get_config_dir() / CONFIG_FILE_NAME


def _stderr_warning(message: str) -> None:
    sys.stderr.write(f"⚠ config.toml: {message}\n")


def normalize_quality(value: str) -> Optional[str]:
    """'720' -> '720p', 'BEST' -> 'best'; None se non è una risoluzione supportata."""
    text = value.strip().lower()
    if text in RESOLUTIONS:
        return text
    if text.isdigit() and f"{text}p" in RESOLUTIONS:
        return f"{text}p"
    return None


class _Reader:
    """Legge i valori di una sezione verificandone il tipo e segnalando quelli non validi."""

    def __init__(self, name: str, data: Any, warn: Callable[[str], None]) -> None:
        self.name = name
        self.warn = warn
        if data is None:
            data = {}
        elif not isinstance(data, dict):
            warn(f"la sezione [{name}] non è una tabella: uso i valori predefiniti")
            data = {}
        self.data: dict[str, Any] = data

    def _invalid(self, key: str, expected: str, value: Any, default: Any) -> Any:
        self.warn(f"{self.name}.{key} deve essere {expected} (trovato {value!r}): uso il valore predefinito {default!r}")
        return default

    def text(self, key: str, default: str) -> str:
        if key not in self.data:
            return default
        value = self.data[key]
        if not isinstance(value, str) or not value.strip():
            return self._invalid(key, "una stringa non vuota", value, default)
        return value

    def boolean(self, key: str, default: bool) -> bool:
        if key not in self.data:
            return default
        value = self.data[key]
        if not isinstance(value, bool):
            return self._invalid(key, "true o false", value, default)
        return value

    def string_list(self, key: str, default: list[str]) -> list[str]:
        if key not in self.data:
            return list(default)
        value = self.data[key]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return self._invalid(key, "un elenco di stringhe", value, list(default))
        return list(value)

    def quality(self, default: str) -> str:
        if "quality" not in self.data:
            return default
        value = self.data["quality"]
        normalized = normalize_quality(value) if isinstance(value, str) else None
        if normalized is None:
            return self._invalid("quality", f"uno tra {', '.join(RESOLUTIONS)}", value, default)
        return normalized

    def base_url(self, default: str) -> str:
        if "base_url" not in self.data:
            return default
        value = self.data["base_url"]
        if not isinstance(value, str) or not value.strip().lower().startswith(("http://", "https://")):
            return self._invalid("base_url", "un URL che inizia con http:// o https://", value, default)
        return value.strip().rstrip("/")


def load_config(
    custom_path: Path | None = None,
    warn: Optional[Callable[[str], None]] = None,
) -> Config:
    """Carica la configurazione da file. Se assente, genera il file predefinito.

    I valori non validi non interrompono l'avvio: vengono sostituiti dal predefinito e
    segnalati con `warn` (di default su stderr), così non si ignorano in silenzio.
    """
    warn = warn or _stderr_warning
    path = custom_path or get_config_path()
    if not path.is_file():
        # Genera il file predefinito con commenti esplicativi
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
        except OSError:
            pass
        return Config()

    try:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as exc:
        warn(f"impossibile leggere {path}: {exc}. Uso la configurazione predefinita.")
        return Config()

    defaults = Config()
    player = _Reader("player", data.get("player"), warn)
    downloader = _Reader("downloader", data.get("downloader"), warn)
    general = _Reader("general", data.get("general"), warn)

    return Config(
        player=PlayerConfig(
            binary=player.text("binary", defaults.player.binary),
            args=player.string_list("args", defaults.player.args),
        ),
        downloader=DownloaderConfig(
            binary=downloader.text("binary", defaults.downloader.binary),
            args=downloader.string_list("args", defaults.downloader.args),
        ),
        general=GeneralConfig(
            base_url=general.base_url(defaults.general.base_url),
            quality=general.quality(defaults.general.quality),
            preview_art=general.boolean("preview_art", defaults.general.preview_art),
            auto_next=general.boolean("auto_next", defaults.general.auto_next),
        ),
    )
