"""Anteprima di un anime per il pannello di fzf: scheda testuale e locandina (chafa) con cache LRU (v2.0).

Ogni anteprima di fzf è un nuovo processo (`python -m ani_it.preview ID`): il modulo importa
solo il necessario (niente requests all'avvio) per restare veloce a ogni cambio di selezione.
"""

import gc
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Optional

from ani_it.constants import Colors
from ani_it.utils import check_binary, get_cache_dir

MAX_COVERS_LRU = 15


def is_valid_anime_id(anime_id: object) -> bool:
    """Solo ID numerici: l'ID diventa un nome di file in cache e non deve poter uscire dalla cartella."""
    return str(anime_id).isdigit()


def _prune_cover_cache(covers_dir: Path, max_items: int = MAX_COVERS_LRU) -> None:
    """Mantiene una cache LRU delle locandine su disco: conserva al massimo 15 thumbnail recenti."""
    try:
        if not covers_dir.is_dir():
            return
        files = [p for p in covers_dir.iterdir() if p.is_file()]
        if len(files) <= max_items:
            return
        # Ordina per timestamp di modifica decrescente (i più recenti all'inizio)
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old_file in files[max_items:]:
            try:
                old_file.unlink()
            except OSError:
                pass
    except Exception:
        pass


def render_preview(anime_id: str, preview_art: bool = True, cache_dir: Optional[Path] = None) -> None:
    """Stampa la scheda dell'anime salvata in cache, con la locandina se richiesta e possibile."""
    if not is_valid_anime_id(anime_id):
        return

    cache_dir = cache_dir or get_cache_dir()
    cache_file = cache_dir / "anime_cache" / f"{anime_id}.json"
    if not cache_file.is_file():
        sys.stdout.write(f"{Colors.DIM}Nessuna informazione disponibile per l'anime {anime_id}{Colors.RESET}\n")
        return

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return

    title = data.get("title", "")
    title_eng = data.get("title_eng", "")
    year = data.get("year", "N/D")
    anime_type = data.get("type", "TV")
    status = data.get("status", "Terminato")
    eps = data.get("episodes_count", "?")
    plot = data.get("plot", "Nessuna trama fornita.")
    img_url = data.get("image_url", "")
    genres = data.get("genres", [])
    score = data.get("score", "")

    # Rendering locandina con chafa se disponibile e abilitato
    if preview_art and check_binary("chafa") and img_url:
        covers_dir = cache_dir / "covers"
        cover_path = covers_dir / f"{anime_id}.jpg"
        if not cover_path.exists():
            try:
                import requests  # import locale on-demand per non rallentare l'avvio

                covers_dir.mkdir(parents=True, exist_ok=True)
                r = requests.get(img_url, timeout=5, stream=True)
                if r.status_code == 200:
                    with open(cover_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)
                gc.collect()
            except Exception:
                pass

        if cover_path.exists():
            try:
                cover_path.touch()
            except OSError:
                pass
            _prune_cover_cache(covers_dir, max_items=MAX_COVERS_LRU)
            try:
                subprocess.run(
                    ["chafa", "--size=36x20", "--clear", "--symbols=vhalf,braille", str(cover_path)],
                    check=False,
                )
            except Exception:
                pass

    # Scheda descrittiva elegante (degrada con grazia se chafa non è presente)
    sys.stdout.write(f"\n{Colors.BOLD}{Colors.BRIGHT_WHITE}╭───────────────────────────────────────────────────╮{Colors.RESET}\n")
    sys.stdout.write(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}  {title[:47]}{Colors.RESET}\n")
    if title_eng and title_eng.lower() != title.lower():
        sys.stdout.write(f"{Colors.DIM}  {title_eng[:47]}{Colors.RESET}\n")
    sys.stdout.write(f"{Colors.BOLD}{Colors.BRIGHT_WHITE}╰───────────────────────────────────────────────────╯{Colors.RESET}\n")

    score_str = f"  {Colors.BRIGHT_YELLOW}⭐ Voto:{Colors.RESET} {score}" if score else ""
    sys.stdout.write(f"{Colors.BRIGHT_CYAN}󰎁 Tipo:{Colors.RESET} {anime_type}   {Colors.BRIGHT_GREEN}󰃭 Anno:{Colors.RESET} {year}   {Colors.BRIGHT_MAGENTA}󰐊 Episodi:{Colors.RESET} {eps}{score_str}\n")
    sys.stdout.write(f"{Colors.BRIGHT_BLUE}󰅚 Stato:{Colors.RESET} {status}\n")

    if genres:
        genre_line = ", ".join(genres[:4])
        sys.stdout.write(f"{Colors.BRIGHT_YELLOW}🎭 Generi:{Colors.RESET} {genre_line}\n")

    if plot:
        sys.stdout.write(f"\n{Colors.BOLD}{Colors.BRIGHT_WHITE}󰋽 TRAMA:{Colors.RESET}\n")
        wrapped = textwrap.fill(plot, width=54)
        sys.stdout.write(f"{Colors.WHITE}{wrapped}{Colors.RESET}\n")


def main(argv: Optional[list[str]] = None) -> int:
    """Punto di ingresso di `python -m ani_it.preview ID`."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.stderr.write("uso: python -m ani_it.preview ID_ANIME\n")
        return 2

    from ani_it.config import load_config

    config = load_config(warn=lambda message: None)
    render_preview(args[0], preview_art=config.general.preview_art)
    return 0


if __name__ == "__main__":
    sys.exit(main())
