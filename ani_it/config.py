"""Gestione della configurazione TOML conforme a XDG per ani-it."""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ani_it.utils import get_config_dir

# tomllib è integrato nella standard library a partire da Python 3.11
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore

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
# Risoluzione preferita: "1080p", "720p", "480p", "best"
quality = "best"

# Lingua predefinita ("it" per doppiaggio italiano o "sub-it" per sottotitoli)
language = "it"

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
    quality: str = "best"
    language: str = "it"
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
        def format_list(items: list[str]) -> str:
            escaped = [f'"{x}"' for x in items]
            return f"[{', '.join(escaped)}]"

        return f"""[player]
binary = "{self.player.binary}"
args = {format_list(self.player.args)}

[downloader]
binary = "{self.downloader.binary}"
args = {format_list(self.downloader.args)}

[general]
quality = "{self.general.quality}"
language = "{self.general.language}"
preview_art = {'true' if self.general.preview_art else 'false'}
auto_next = {'true' if self.general.auto_next else 'false'}
"""


def get_config_path() -> Path:
    """Restituisce il path completo del file di configurazione config.toml."""
    return get_config_dir() / CONFIG_FILE_NAME


def load_config(custom_path: Path | None = None) -> Config:
    """Carica la configurazione da file. Se assente, genera il file predefinito."""
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
        content = path.read_text(encoding="utf-8")
        data: dict[str, Any] = tomllib.loads(content)
    except Exception:
        # Fallback sicuro sui default in caso di file TOML corrotto
        return Config()

    player_data = data.get("player", {})
    downloader_data = data.get("downloader", {})
    general_data = data.get("general", {})

    player_cfg = PlayerConfig(
        binary=str(player_data.get("binary", "mpv")),
        args=list(player_data.get("args", ["--hwdec=auto-safe", "--geometry=1280x720"])),
    )

    downloader_cfg = DownloaderConfig(
        binary=str(downloader_data.get("binary", "aria2c")),
        args=list(downloader_data.get("args", ["-x", "16", "-s", "16", "-k", "1M", "-j", "4"])),
    )

    general_cfg = GeneralConfig(
        quality=str(general_data.get("quality", "best")),
        language=str(general_data.get("language", "it")),
        preview_art=bool(general_data.get("preview_art", True)),
        auto_next=bool(general_data.get("auto_next", False)),
    )

    return Config(player=player_cfg, downloader=downloader_cfg, general=general_cfg)


def save_config(config: Config, custom_path: Path | None = None) -> None:
    """Salva la configurazione su disco."""
    path = custom_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.to_toml(), encoding="utf-8")
