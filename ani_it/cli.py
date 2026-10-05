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
    FORMAT_MP4,
    Colors,
    Icons,
)
from ani_it.downloader import DownloaderManager
from ani_it.history import HistoryManager
from ani_it.player import MpvController
from ani_it.preview import render_preview
from ani_it.resolver import StreamResolver
from ani_it.scraper import AnimeUnityScraper, EpisodesNotFoundError
from ani_it.ui import FzfUI
from ani_it.utils import (
    SignalHandler,
    format_duration,
    get_cache_dir,
    normalize_episode_number,
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
        "-t",
        "--terminal",
        dest="terminal_vo",
        nargs="?",
        const="tct",
        choices=["tct", "kitty", "sixel", "caca"],
        default=None,
        help="Riproduci il video direttamente nel terminale ('tct', 'kitty', 'sixel', 'caca').",
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


# Sotto questa soglia (secondi) una posizione non vale la pena di essere ripresa
MIN_SAVED_POSITION = 5.0


def find_episode_index(episodes: list[dict[str, Any]], target: Any, allow_position: bool = True) -> Optional[int]:
    """Indice dell'episodio richiesto: prima per numero reale su tutta la lista, poi per posizione.

    La posizione (1-based) è solo un ripiego: con un episodio 0 o numerazioni che non
    partono da 1 confonderla con il numero farebbe avviare l'episodio sbagliato.
    """
    wanted = normalize_episode_number(target)
    if wanted is None:
        return None
    for idx, episode in enumerate(episodes):
        if normalize_episode_number(episode.get("number")) == wanted:
            return idx
    if allow_position and isinstance(wanted, int) and 1 <= wanted <= len(episodes):
        return wanted - 1
    return None


def record_progress(
    history: HistoryManager,
    anime_id: Any,
    title: str,
    slug: str,
    episodes: list[dict[str, Any]],
    idx: int,
    playback: Any,
) -> None:
    """Salva in cronologia l'esito di una visione: numero reale, fine episodio/serie, posizione."""
    number = normalize_episode_number(episodes[idx].get("number"))
    if number is None:
        number = idx + 1
    finished = playback.status == "completed"
    position = 0.0 if finished or playback.time_pos < MIN_SAVED_POSITION else playback.time_pos
    history.update_progress(
        anime_id=anime_id,
        title=title,
        slug=slug,
        episode=number,
        total_episodes=len(episodes),
        episode_completed=finished,
        series_completed=finished and idx == len(episodes) - 1,
        position=position,
    )


def resume_position(entry: Optional[dict[str, Any]], episode: dict[str, Any]) -> float:
    """Posizione da cui riprendere `episode`: solo se è l'episodio interrotto in cronologia."""
    if not entry or entry.get("episode_completed"):
        return 0.0
    number = normalize_episode_number(episode.get("number"))
    saved = normalize_episode_number(entry.get("last_episode"))
    if number is None or saved is None or number != saved:
        return 0.0
    try:
        position = float(entry.get("last_position") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return position if position >= MIN_SAVED_POSITION else 0.0


def _warn(message: str) -> None:
    sys.stdout.write(f"{Colors.BRIGHT_YELLOW}{Icons.WARNING} {message}{Colors.RESET}\n")


def _step(idx: int, delta: int, total: int) -> Optional[int]:
    """Nuovo indice dopo uno spostamento; None (con avviso) se esce dalla lista."""
    new_idx = idx + delta
    if 0 <= new_idx < total:
        return new_idx
    _warn("Sei all'ultimo episodio della serie!" if delta > 0 else "Sei al primo episodio della serie!")
    return None


def _choose_episode(ui: FzfUI, title: str, episodes: list[dict[str, Any]], anime_id: Any) -> Optional[int]:
    picked = ui.select_episode(title, episodes, anime_id)
    return episodes.index(picked) if picked else None


def _run_player(
    player: MpvController,
    stream_data: dict[str, Any],
    title: str,
    ep_num: Any,
    start_at: float,
    quit_on_eof: bool,
) -> Any:
    """Avvia mpv; se il player non è installato mostra il messaggio ed esce con codice 1."""
    try:
        return player.play(
            stream_data=stream_data,
            anime_title=title,
            episode_number=ep_num,
            start_time=start_at or None,
            quit_on_eof=quit_on_eof,
        )
    except FileNotFoundError as exc:
        sys.stderr.write(f"{Colors.BRIGHT_RED}{Icons.ERROR} {exc}{Colors.RESET}\n")
        sys.exit(1)


def _playback_error_message(title: str, ep_num: Any, playback: Any) -> str:
    message = f"mpv non è riuscito a riprodurre '{title}' - Episodio {ep_num}."
    if playback.error_output:
        message += f"\n{Colors.DIM}{playback.error_output}{Colors.RESET}"
    return message


def play_episodes(
    *,
    config: Config,
    ui: FzfUI,
    history: HistoryManager,
    resolver: StreamResolver,
    player: MpvController,
    downloader: DownloaderManager,
    title: str,
    anime_id: Any,
    slug: str,
    episodes: list[dict[str, Any]],
    start_idx: int,
) -> None:
    """Loop interattivo di riproduzione e download, come piccola macchina a stati.

    Stati: play -> post_watch | error; post_watch -> play | download | uscita;
    download -> post_download | error; post_download -> play_local | download | play;
    play_local -> post_watch | error; error -> (stato da ritentare) | play | download.
    """
    total = len(episodes)
    idx = start_idx
    state = "play"
    error_message = ""
    retry_state = "play"   # stato a cui torna "Riprova" dal menu di errore
    skip_resume = False

    while True:
        episode = episodes[idx]
        ep_num = episode["number"]
        has_next = idx + 1 < total
        has_prev = idx > 0

        if state == "play":
            sys.stdout.write(
                f"\n{Colors.BRIGHT_CYAN}{Icons.PLAY} Risoluzione stream: {title} - Episodio {ep_num}...{Colors.RESET}\n"
            )
            try:
                stream_data = resolver.resolve(
                    episode["link"],
                    quality=config.general.quality,
                    episode_id=episode.get("id"),
                )
            except Exception as exc:
                error_message, retry_state, state = f"Impossibile risolvere lo stream: {exc}", "play", "error"
                continue

            start_at = 0.0 if skip_resume else resume_position(history.get_anime_history(anime_id), episode)
            skip_resume = False
            if start_at > 0:
                sys.stdout.write(f"{Colors.DIM}Riprendo da {format_duration(start_at)}{Colors.RESET}\n")

            playback = _run_player(player, stream_data, title, ep_num, start_at, config.general.auto_next)
            if playback.status == "error":
                error_message = _playback_error_message(title, ep_num, playback)
                retry_state, state = "play", "error"
                continue

            record_progress(history, anime_id, title, slug, episodes, idx, playback)
            if config.general.auto_next and playback.status == "completed" and has_next:
                sys.stdout.write(
                    f"\n{Colors.BRIGHT_GREEN}{Icons.ARROW_RIGHT} Riproduzione automatica prossimo episodio...{Colors.RESET}\n"
                )
                idx += 1
                continue
            state = "post_watch"

        elif state in ("post_watch", "error"):
            if state == "post_watch":
                action = ui.post_watch_menu(ep_num, has_next=has_next, has_prev=has_prev)
            else:
                action = ui.error_menu(ep_num, error_message, has_next=has_next, has_prev=has_prev)

            if action == "retry":
                state = retry_state
            elif action == "replay":
                skip_resume, state = True, "play"
            elif action in ("next", "prev"):
                new_idx = _step(idx, 1 if action == "next" else -1, total)
                if new_idx is not None:
                    idx, state = new_idx, "play"
            elif action == "select":
                new_idx = _choose_episode(ui, title, episodes, anime_id)
                if new_idx is not None:
                    idx, state = new_idx, "play"
            elif action == "download":
                state = "download"
            else:  # quit
                return

        elif state == "download":
            ok = downloader.download_episode(title, ep_num, episode["link"], episode_id=episode.get("id"))
            if ok:
                state = "post_download"
            else:
                error_message = f"Download dell'episodio {ep_num} non riuscito."
                retry_state, state = "download", "error"

        elif state == "post_download":
            action = ui.post_download_menu(ep_num, has_next=has_next, has_prev=has_prev)
            if action == "play":
                state = "play_local"
            elif action == "next":
                new_idx = _step(idx, 1, total)
                if new_idx is not None:
                    idx, state = new_idx, "download"
            elif action == "select":
                new_idx = _choose_episode(ui, title, episodes, anime_id)
                if new_idx is not None:
                    idx, state = new_idx, "play"
            else:  # quit
                return

        elif state == "play_local":
            path = downloader.episode_path(title, ep_num)
            if not path.exists():
                error_message = f"File scaricato non trovato: {path}"
                retry_state, state = "download", "error"
                continue
            local_stream = {"stream_url": str(path), "headers": {}, "format": FORMAT_MP4, "subtitles": []}
            playback = _run_player(player, local_stream, title, ep_num, 0.0, False)
            if playback.status == "error":
                error_message = _playback_error_message(title, ep_num, playback)
                retry_state, state = "play_local", "error"
                continue
            record_progress(history, anime_id, title, slug, episodes, idx, playback)
            state = "post_watch"


def main() -> None:
    """Punto di ingresso principale della CLI."""
    SignalHandler.install()

    # Il parsing viene prima di tutto: --help, --version e gli errori di sintassi escono qui
    # senza aver creato config, cronologia o cache.
    parser = create_parser()
    args = parser.parse_args()

    # 1. Gestione comando interno di preview per fzf (leggero: niente cronologia né UI)
    if args.preview_anime_id:
        render_preview(args.preview_anime_id, load_config(warn=lambda message: None).general.preview_art)
        sys.exit(0)

    config: Config = load_config()
    history = HistoryManager()
    ui = FzfUI(config, history)

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

    # Sovrascrittura output video da terminale (-t / --terminal)
    if args.terminal_vo:
        config.player.args = [f"--vo={args.terminal_vo}", "--force-window=no"] + config.player.args

    # Banner introduttivo moderno
    ui.print_banner()

    base_url = config.general.base_url
    scraper = AnimeUnityScraper(base_url=base_url)
    resolver = StreamResolver(session=scraper.session, base_url=base_url)
    player = MpvController(config)
    downloader = DownloaderManager(config, scraper=scraper, resolver=resolver)

    selected_anime: Optional[dict[str, Any]] = None
    target_episode_num: Optional[Any] = args.episode
    resume_info: Optional[dict[str, Any]] = None

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
            resume_info = last
            target_episode_num = None  # l'episodio si ricava dalla cronologia dopo aver letto la lista

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
                "status": "Completato" if h.get("series_completed") else f"Ep. {h.get('last_episode')}",
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
                f"\n  {Colors.BRIGHT_RED}{Icons.ERROR} Errore di connessione ad AnimeUnity: {exc}{Colors.RESET}\n"
                f"  {Colors.DIM}Verifica la connessione internet o lo stato dei server ({base_url}){Colors.RESET}\n"
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
    except EpisodesNotFoundError:
        sys.stderr.write(f"{Colors.BRIGHT_RED}{Icons.ERROR} Nessun episodio disponibile per questa serie.{Colors.RESET}\n")
        sys.exit(1)
    except Exception as exc:
        sys.stderr.write(f"{Colors.BRIGHT_RED}{Icons.ERROR} Errore nel caricamento della scheda anime: {exc}{Colors.RESET}\n")
        sys.exit(1)

    episodes = details.get("episodes", [])
    if not episodes:
        sys.stderr.write(f"{Colors.BRIGHT_RED}{Icons.ERROR} Nessun episodio disponibile per questa serie.{Colors.RESET}\n")
        sys.exit(1)

    if details.get("partial"):
        reason = details.get("partial_reason") or "errore di rete"
        _warn(f"Lista episodi incompleta ({reason}): alcuni episodi potrebbero mancare.")

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

        # I dettagli già caricati e il titolo mostrato nei menu vengono riusati: niente doppio
        # download della scheda e stessa cartella dei download lanciati dal menu.
        downloader.download_range(aid, slug, range_val, details=details, anime_title=title)
        sys.exit(0)

    # 10. Modalità Streaming / Riproduzione: scelta dell'episodio di partenza
    start_idx: Optional[int] = None
    if resume_info is not None:
        last_num = resume_info.get("last_episode")
        found = find_episode_index(episodes, last_num, allow_position=False)
        if found is None:
            _warn(f"Ultimo episodio visto ({last_num}) non trovato nella lista, selezione manuale...")
        elif resume_info.get("episode_completed"):
            if found + 1 < total_episodes:
                start_idx = found + 1
            else:
                _warn(f"Hai già completato l'ultimo episodio ({last_num}): serie terminata. Scegli cosa rivedere.")
        else:
            start_idx = found
    elif target_episode_num is not None:
        start_idx = find_episode_index(episodes, target_episode_num)
        if start_idx is None:
            _warn(f"Episodio {target_episode_num} non trovato, selezione manuale...")

    if start_idx is None:
        start_idx = _choose_episode(ui, title, episodes, aid)
        if start_idx is None:
            sys.exit(0)

    play_episodes(
        config=config,
        ui=ui,
        history=history,
        resolver=resolver,
        player=player,
        downloader=downloader,
        title=title,
        anime_id=aid,
        slug=slug,
        episodes=episodes,
        start_idx=start_idx,
    )


if __name__ == "__main__":
    main()
