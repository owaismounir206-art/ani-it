"""Interfaccia a riga di comando (CLI) e punto di ingresso dell'applicazione ani-it."""

import argparse
import shutil
import sys
from typing import Any, Optional

import requests

from ani_it.config import Config, load_config
from ani_it.constants import (
    APP_NAME,
    APP_VERSION,
    Colors,
    Icons,
)
from ani_it.downloader import DownloaderManager
from ani_it.history import HistoryManager
from ani_it.player import MpvController
from ani_it.resolver import StreamResolver
from ani_it.scraper import AnimeUnityScraper
from ani_it.ui import FzfUI
from ani_it.utils import (
    SignalHandler,
    get_cache_dir,
    sanitize_filename,
)


def create_parser() -> argparse.ArgumentParser:
    """Crea il parser degli argomenti CLI con tutti i flag supportati."""
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=f"{Colors.BOLD}{Colors.BRIGHT_CYAN}ani-it v{APP_VERSION}{Colors.RESET} - "
        f"Client CLI/TUI nativo per AnimeUnity su Arch Linux.",
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False,
    )

    parser.add_argument(
        "--help",
        action="help",
        help="Mostra questo messaggio di aiuto ed esce.",
    )

    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Titolo dell'anime da cercare su AnimeUnity.",
    )

    parser.add_argument(
        "-c",
        "--continue",
        dest="resume",
        action="store_true",
        help="Riprendi immediatamente la visione dell'ultimo anime ed episodio.",
    )

    parser.add_argument(
        "-h",
        "-H",
        "--history",
        dest="history",
        action="store_true",
        help="Mostra la cronologia degli anime visti con selezione rapida.",
    )

    parser.add_argument(
        "-d",
        "--download",
        dest="download",
        action="store_true",
        help="Attiva la modalità download con yt-dlp e aria2c.",
    )

    parser.add_argument(
        "-e",
        "--episode",
        dest="episode",
        type=str,
        default=None,
        help="Seleziona direttamente un numero di episodio o un intervallo (es. 1 o 1-12).",
    )

    parser.add_argument(
        "-q",
        "--quality",
        dest="quality",
        type=str,
        choices=["1080", "1080p", "720", "720p", "480", "480p", "best"],
        default=None,
        help="Forza una qualità video specifica (1080p, 720p, 480p, best).",
    )

    parser.add_argument(
        "--clear-history",
        action="store_true",
        help="Svuota completamente il database della cronologia locale.",
    )

    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Pulisce la cache delle locandine e dei metadati.",
    )

    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {APP_VERSION}",
        help="Stampa la versione del programma ed esce.",
    )

    # Flag interni per integrazione con fzf e shell completion
    parser.add_argument(
        "--preview-anime",
        dest="preview_anime_id",
        type=str,
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--list-history-titles",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    return parser


def main() -> None:
    """Punto di ingresso principale della CLI."""
    SignalHandler.install()
    config: Config = load_config()
    history = HistoryManager()
    ui = FzfUI(config, history)

    parser = create_parser()
    args = parser.parse_args()

    # 1. Gestione comando interno di preview per fzf
    if args.preview_anime_id:
        ui.render_anime_preview(args.preview_anime_id)
        sys.exit(0)

    # 2. Gestione comando interno per completamento shell
    if args.list_history_titles:
        for title in history.list_titles():
            sys.stdout.write(f"{title}\n")
        sys.exit(0)

    # 3. Operazioni di pulizia dati
    if args.clear_history:
        history.clear()
        sys.stdout.write(f"{Colors.BRIGHT_GREEN}{Icons.CHECK} Cronologia cancellata con successo.{Colors.RESET}\n")
        sys.exit(0)

    if args.clear_cache:
        cache_dir = get_cache_dir()
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
        sys.stdout.write(f"{Colors.BRIGHT_GREEN}{Icons.CHECK} Cache svuotata con successo.{Colors.RESET}\n")
        sys.exit(0)

    # 4. Verifica dipendenze di sistema minime (fzf, mpv)
    ui.verify_system_requirements()

    # Sovrascrittura qualità se passata da CLI
    if args.quality:
        q_val = args.quality if (args.quality.endswith("p") or args.quality == "best") else f"{args.quality}p"
        config.general.quality = q_val

    # Banner introduttivo moderno
    ui.print_banner()

    scraper = AnimeUnityScraper()
    resolver = StreamResolver(session=scraper.session)
    player = MpvController(config)
    downloader = DownloaderManager(config, scraper=scraper, resolver=resolver)

    selected_anime: Optional[dict[str, Any]] = None
    target_episode_num: Optional[Any] = args.episode

    # 5. Modalità Riprendi (--continue)
    if args.resume:
        last = history.get_last_played()
        if not last:
            sys.stdout.write(
                f"\n  {Colors.BRIGHT_YELLOW}{Icons.WARNING} Nessuna riproduzione precedente trovata in cronologia.{Colors.RESET}\n"
            )
        else:
            selected_anime = {
                "id": last["id"],
                "title": last["title"],
                "slug": last["slug"],
            }
            last_ep = last.get("last_episode", 1)
            if last.get("completed", False):
                target_episode_num = str(last_ep + 1)
            else:
                target_episode_num = str(last_ep)

    # 6. Modalità Cronologia (--history)
    elif args.history:
        items = history.list_history()
        if not items:
            sys.stdout.write(
                f"\n  {Colors.BRIGHT_YELLOW}{Icons.WARNING} La cronologia è attualmente vuota.{Colors.RESET}\n"
            )
            sys.exit(0)

        anime_items: list[dict[str, Any]] = []
        for h in items:
            anime_items.append({
                "id": h["id"],
                "title": h["title"],
                "slug": h["slug"],
                "type": "HIST",
                "year": "Recente",
                "episodes_count": str(h.get("total_episodes", "?")),
                "status": "Completato" if h.get("completed") else f"Ep. {h.get('last_episode')}",
                "plot": f"Ultima visione: Episodio {h.get('last_episode')}",
            })
        selected_anime = ui.select_anime(anime_items)
        if not selected_anime:
            sys.exit(0)

    # 7. Ricerca standard per Query o Prompt Interattivo con Loop Continuo
    query: Optional[str] = args.query
    while not selected_anime:
        if not query:
            sys.stdout.write(f"\n  {Colors.BOLD}{Colors.BRIGHT_CYAN}󰍉 Inserisci il nome dell'anime da cercare:{Colors.RESET} ")
            sys.stdout.flush()
            try:
                query = input().strip()
            except (EOFError, KeyboardInterrupt):
                sys.exit(0)

        if not query:
            sys.exit(0)

        sys.stdout.write(
            f"\n  {Colors.BRIGHT_BLUE}󰍉 Ricerca su AnimeUnity per '{Colors.BOLD}{Colors.BRIGHT_WHITE}{query}{Colors.RESET}{Colors.BRIGHT_BLUE}'...{Colors.RESET}\n"
        )
        try:
            results = scraper.search_anime(query)
        except requests.exceptions.ConnectionError as exc:
            sys.stderr.write(
                f"\n  {Colors.BRIGHT_RED}{Icons.ERROR} Errore di connessione ad AnimeUnity.{Colors.RESET}\n"
                f"  {Colors.DIM}Verifica la connessione internet o lo stato dei server (https://www.animeunity.so){Colors.RESET}\n"
            )
            query = None
            continue
        except Exception as exc:
            sys.stderr.write(f"  {Colors.BRIGHT_RED}{Icons.ERROR} Errore durante la ricerca: {exc}{Colors.RESET}\n")
            results = []

        if not results:
            sys.stdout.write(
                f"\n  {Colors.BRIGHT_YELLOW}╭── 󰀪 Nessun risultato trovato per '{query}' ────────────────╮{Colors.RESET}\n"
                f"  {Colors.BRIGHT_YELLOW}│{Colors.RESET} {Colors.DIM}Suggerimento: prova con un termine più breve (es. 'uma')    {Colors.RESET}{Colors.BRIGHT_YELLOW}│{Colors.RESET}\n"
                f"  {Colors.BRIGHT_YELLOW}╰──────────────────────────────────────────────────────────╯{Colors.RESET}\n"
            )
            query = None
            continue

        selected_anime = ui.select_anime(results)
        if not selected_anime:
            query = None
            continue

    # 8. Recupero dettagli completi ed episodi della serie selezionata
    aid = selected_anime["id"]
    slug = selected_anime["slug"]
    title = selected_anime.get("title", f"Anime_{aid}")

    sys.stdout.write(f"{Colors.BRIGHT_CYAN}{Icons.FILM} Caricamento episodi per '{title}'...{Colors.RESET}\n")
    try:
        details = scraper.get_anime_details(aid, slug)
    except Exception as exc:
        sys.stderr.write(f"{Colors.BRIGHT_RED}{Icons.ERROR} Errore nel caricamento della scheda anime: {exc}{Colors.RESET}\n")
        sys.exit(1)

    episodes = details.get("episodes", [])
    if not episodes:
        sys.stderr.write(f"{Colors.BRIGHT_RED}{Icons.ERROR} Nessun episodio disponibile per questa serie.{Colors.RESET}\n")
        sys.exit(1)

    total_episodes = len(episodes)

    # 9. Modalità Download (-d / --download)
    if args.download:
        if target_episode_num:
            range_val = str(target_episode_num)
        else:
            sys.stdout.write(
                f"\n{Colors.BOLD}Totale episodi disponibili: {total_episodes}{Colors.RESET}\n"
                f"Inserisci l'intervallo da scaricare (es. '1-12', '1,3,5', 'all' o singolo numero) [all]: "
            )
            sys.stdout.flush()
            try:
                user_range = input().strip()
            except (EOFError, KeyboardInterrupt):
                sys.exit(0)
            range_val = user_range if user_range else "all"

        downloader.download_range(aid, slug, range_val)
        sys.exit(0)

    # 10. Modalità Streaming / Riproduzione (Loop interattivo)
    # Trova l'indice dell'episodio di partenza
    current_idx = 0
    if target_episode_num is not None:
        matched = False
        target_str = str(target_episode_num).strip()
        for idx, ep in enumerate(episodes):
            if str(ep.get("number")).strip() == target_str or str(idx + 1) == target_str:
                current_idx = idx
                matched = True
                break
        if not matched:
            sys.stdout.write(
                f"{Colors.BRIGHT_YELLOW}{Icons.WARNING} Episodio {target_episode_num} non trovato, selezione manuale...{Colors.RESET}\n"
            )
            selected_ep = ui.select_episode(title, episodes, aid)
            if not selected_ep:
                sys.exit(0)
            current_idx = episodes.index(selected_ep)
    else:
        selected_ep = ui.select_episode(title, episodes, aid)
        if not selected_ep:
            sys.exit(0)
        current_idx = episodes.index(selected_ep)

    # Loop continuo di riproduzione
    while 0 <= current_idx < len(episodes):
        current_ep = episodes[current_idx]
        ep_num = current_ep["number"]
        ep_link = current_ep["link"]
        ep_id = current_ep.get("id")

        sys.stdout.write(
            f"\n{Colors.BRIGHT_CYAN}{Icons.PLAY} Risoluzione stream: {title} - Episodio {ep_num}...{Colors.RESET}\n"
        )
        try:
            stream_data = resolver.resolve(
                ep_link,
                quality=config.general.quality,
                episode_id=ep_id,
            )
        except Exception as exc:
            sys.stderr.write(f"{Colors.BRIGHT_RED}{Icons.ERROR} Impossibile risolvere lo stream: {exc}{Colors.RESET}\n")
            action = ui.post_watch_menu(ep_num, has_next=(current_idx + 1 < len(episodes)), has_prev=(current_idx > 0))
            if action == "next":
                current_idx += 1
                continue
            elif action == "prev":
                current_idx -= 1
                continue
            elif action == "select":
                new_ep = ui.select_episode(title, episodes, aid)
                if new_ep:
                    current_idx = episodes.index(new_ep)
                    continue
                break
            else:
                break

        # Avvio di MPV
        playback = player.play(
            stream_data=stream_data,
            anime_title=title,
            episode_number=ep_num,
        )

        # Aggiornamento cronologia atomica
        is_completed = (playback.status == "completed")
        history.update_progress(
            anime_id=aid,
            title=title,
            slug=slug,
            episode=int(float(ep_num)) if str(ep_num).replace(".", "").isdigit() else (current_idx + 1),
            total_episodes=total_episodes,
            completed=is_completed,
        )

        has_next = current_idx + 1 < len(episodes)
        has_prev = current_idx > 0

        # Se auto_next è abilitato e la visione è stata completata
        if config.general.auto_next and is_completed and has_next:
            sys.stdout.write(f"\n{Colors.BRIGHT_GREEN}{Icons.ARROW_RIGHT} Riproduzione automatica prossimo episodio...{Colors.RESET}\n")
            current_idx += 1
            continue

        # Menu interattivo post-watch
        action = ui.post_watch_menu(ep_num, has_next=has_next, has_prev=has_prev)

        if action == "next":
            if has_next:
                current_idx += 1
            else:
                sys.stdout.write(f"{Colors.BRIGHT_YELLOW}{Icons.WARNING} Sei all'ultimo episodio della serie!{Colors.RESET}\n")
                break
        elif action == "prev":
            if has_prev:
                current_idx -= 1
            else:
                sys.stdout.write(f"{Colors.BRIGHT_YELLOW}{Icons.WARNING} Sei al primo episodio della serie!{Colors.RESET}\n")
        elif action == "replay":
            continue
        elif action == "select":
            new_ep = ui.select_episode(title, episodes, aid)
            if new_ep:
                current_idx = episodes.index(new_ep)
            else:
                break
        elif action == "download":
            downloader.download_episode(
                title,
                ep_num,
                ep_link,
                episode_id=ep_id,
            )
            dl_action = ui.post_download_menu(ep_num, has_next=has_next, has_prev=has_prev)
            if dl_action == "play":
                target_file = downloader.download_base / sanitize_filename(title) / f"Episodio_{ep_num}.mp4"
                local_stream_data = {
                    "stream_url": str(target_file) if target_file.exists() else stream_data["stream_url"],
                    "headers": stream_data.get("headers", {}),
                    "format": "mp4",
                    "subtitles": stream_data.get("subtitles", []),
                }
                player.play(
                    stream_data=local_stream_data,
                    anime_title=title,
                    episode_number=ep_num,
                )
                continue
            elif dl_action == "next":
                if has_next:
                    current_idx += 1
                    next_ep = episodes[current_idx]
                    downloader.download_episode(
                        title,
                        next_ep["number"],
                        next_ep["link"],
                        episode_id=next_ep.get("id"),
                    )
                else:
                    sys.stdout.write(f"{Colors.BRIGHT_YELLOW}{Icons.WARNING} Sei all'ultimo episodio della serie!{Colors.RESET}\n")
                continue
            elif dl_action == "select":
                new_ep = ui.select_episode(title, episodes, aid)
                if new_ep:
                    current_idx = episodes.index(new_ep)
                else:
                    break
            elif dl_action == "quit":
                break
        elif action == "quit":
            break


if __name__ == "__main__":
    main()
