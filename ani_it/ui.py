"""Interfaccia utente TUI interattiva basata su fzf, chafa e grafica terminale moderna."""

import json
import re
import shlex
import subprocess
import sys
import unicodedata
from typing import Any, Optional

from ani_it.config import Config
from ani_it.constants import APP_NAME, APP_VERSION, Colors, Icons
from ani_it.history import HistoryManager
from ani_it.preview import is_valid_anime_id, render_preview
from ani_it.utils import (
    check_optional_dependencies,
    check_required_dependencies,
    get_cache_dir,
)


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

# Larghezza interna dei box dei menu (in colonne visibili)
MENU_BOX_WIDTH = 65

# Pacchetti Arch per i binari noti (per gli altri si rimanda alla configurazione)
KNOWN_PACMAN_PACKAGES = frozenset({"fzf", "mpv", "yt-dlp"})

# Colonna del titolo nel menu di selezione anime (colonne visibili)
TITLE_COLUMN_WIDTH = 65
EPISODE_TITLE_WIDTH = 38


def _single_line(value: Any) -> str:
    """Testo su una sola riga: tab e a capo dell'API romperebbero le colonne separate da tab di fzf."""
    return re.sub(r"[\t\r\n]+", " ", str(value or "")).strip()


def char_width(char: str) -> int:
    """Colonne occupate da un carattere: 0 per i combinanti, 2 per CJK/emoji larghi, altrimenti 1."""
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def display_width(text: str) -> int:
    """Larghezza in colonne del testo (senza interpretare i codici ANSI)."""
    return sum(char_width(c) for c in text)


def visible_len(text: str) -> int:
    """Colonne occupate dal testo senza i codici di colore ANSI."""
    return display_width(ANSI_ESCAPE_RE.sub("", text))


def pad_visible(text: str, width: int) -> str:
    """Allinea a `width` colonne visibili: i codici ANSI non contano nella larghezza."""
    return text + " " * max(0, width - visible_len(text))


def fit_plain(text: str, width: int) -> str:
    """Tronca (con '…') e riempie testo semplice a esattamente `width` colonne."""
    if display_width(text) <= width:
        return text + " " * (width - display_width(text))
    out, used = [], 0
    for char in text:
        w = char_width(char)
        if used + w > width - 1:
            break
        out.append(char)
        used += w
    return "".join(out) + "…" + " " * (width - used - 1)


def fit_segments(segments: list[tuple[str, str]], width: int) -> str:
    """Compone segmenti (colore, testo) in esattamente `width` colonne visibili.

    Il troncamento e il riempimento si calcolano sul testo semplice e i colori si applicano
    dopo, così nessuna sequenza ANSI viene tagliata a metà e le colonne restano allineate.
    """
    out: list[str] = []
    used = 0
    for color, text in segments:
        room = width - used
        if room <= 0:
            break
        if display_width(text) > room:
            text = fit_plain(text, room).rstrip(" ") if room > 1 else "…"
        out.append(f"{color}{text}{Colors.RESET}" if color else text)
        used += display_width(text)
    out.append(" " * max(0, width - used))
    return "".join(out)


class FzfUI:
    """Interfaccia interattiva da terminale con styling moderno (Tokyo Night/Catppuccin), fzf e chafa."""

    def __init__(self, config: Config, history: Optional[HistoryManager] = None) -> None:
        self.config = config
        self.history = history or HistoryManager()
        self.cache_dir = get_cache_dir()
        self.covers_dir = self.cache_dir / "covers"
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.data_cache_dir = self.cache_dir / "anime_cache"
        self.data_cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def clear_screen() -> None:
        """Pulisce la schermata del terminale in modo conforme agli standard ANSI."""
        sys.stdout.write(Colors.CLEAR_SCREEN)
        sys.stdout.flush()

    @staticmethod
    def wait_for_enter(message: str = "Premi Invio per continuare...") -> None:
        """Mette in pausa il flusso finché l'utente non preme Invio (per leggere un errore)."""
        sys.stdout.write(f"\n  {Colors.DIM}{message}{Colors.RESET} ")
        sys.stdout.flush()
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

    @staticmethod
    def print_banner() -> None:
        """Stampa un banner ASCII/Nerd Font accattivante con colori vivaci."""
        banner = f"""
{Colors.BRIGHT_CYAN}  ╭─────────────────────────────────────────────────────────────╮
  │  {Colors.BOLD}{Colors.BRIGHT_MAGENTA}🌸 {APP_NAME}{Colors.RESET}{Colors.BRIGHT_CYAN} v{APP_VERSION}  •  {Colors.BOLD}{Colors.BRIGHT_WHITE}AnimeUnity TUI Client (Arch Linux){Colors.RESET}{Colors.BRIGHT_CYAN}          │
  │  {Colors.DIM}󰣇 Arch Linux  •  󰓃 PipeWire  •  󰐊 MPV IPC  •  󰍉 FZF{Colors.RESET}{Colors.BRIGHT_CYAN}            │
  ╰─────────────────────────────────────────────────────────────╯{Colors.RESET}
"""
        sys.stdout.write(banner)
        sys.stdout.flush()

    def verify_system_requirements(self) -> None:
        """Verifica i binari essenziali (fzf e il player configurato) e segnala yt-dlp se manca."""
        ok, missing = check_required_dependencies(self.config.player.binary)
        if not ok:
            known = [m for m in missing if m in KNOWN_PACMAN_PACKAGES]
            custom = [m for m in missing if m not in KNOWN_PACMAN_PACKAGES]
            message = (
                f"\n{Colors.BRIGHT_RED}{Icons.ERROR} ERRORE DIPENDENZE:{Colors.RESET}\n"
                f"I seguenti componenti obbligatori non sono installati nel sistema: "
                f"{Colors.BOLD}{' '.join(missing)}{Colors.RESET}\n\n"
            )
            if known:
                message += (
                    f"Puoi installarli su Arch Linux con il comando:\n"
                    f"  {Colors.BRIGHT_GREEN}sudo pacman -S {' '.join(known)}{Colors.RESET}\n\n"
                )
            if custom:
                message += (
                    f"Installa {' '.join(repr(c) for c in custom)} oppure correggi "
                    f"{Colors.BOLD}player.binary{Colors.RESET} in config.toml.\n\n"
                )
            sys.stderr.write(message)
            sys.exit(1)

        # yt-dlp serve solo per i download: lo streaming funziona anche senza
        for dep in check_optional_dependencies():
            sys.stderr.write(
                f"{Colors.BRIGHT_YELLOW}{Icons.WARNING} {dep} non trovato: i download non saranno disponibili "
                f"(sudo pacman -S {dep}).{Colors.RESET}\n"
            )

    def select_anime(self, anime_list: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """Apre un menu interattivo fzf con preview grafica chafa e palette colori curata."""
        if not anime_list:
            return None

        if len(anime_list) == 1:
            return anime_list[0]

        # Salva i dettagli di ogni anime nella cache per la preview dinamica di fzf
        for item in anime_list:
            aid = str(item["id"])
            if not is_valid_anime_id(aid):
                continue  # l'ID diventa un nome di file: solo numerici
            cache_file = self.data_cache_dir / f"{aid}.json"
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(item, f, ensure_ascii=False)
            except OSError:
                pass

        # Prepara le righe per fzf formattate con badge colorati
        fzf_lines: list[str] = []
        for item in anime_list:
            raw_type = item.get("type", "TV").upper()
            if "MOVIE" in raw_type or "FILM" in raw_type:
                type_badge = f"{Colors.BRIGHT_MAGENTA}󰎁 [{raw_type[:5]}]{Colors.RESET}"
            elif "OVA" in raw_type or "ONA" in raw_type:
                type_badge = f"{Colors.BRIGHT_YELLOW}󰵔 [{raw_type[:5]}]{Colors.RESET}"
            elif "HIST" in raw_type:
                type_badge = f"{Colors.BRIGHT_BLUE}󰋽 [{raw_type[:5]}]{Colors.RESET}"
            else:
                type_badge = f"{Colors.BRIGHT_CYAN}󰎁 [{raw_type[:5]}]{Colors.RESET}"

            title = _single_line(item.get("title"))
            if not title or title.lower() in ("senza titolo", "anime senza titolo", "null", "none"):
                title = _single_line(
                    item.get("title_eng") or item.get("slug") or f"Anime {item.get('id')}"
                ).replace("-", " ").title()

            # Doppiaggio dal campo `dub` dell'API; solo per le voci senza il campo (cronologia)
            # si ripiega sul suffisso "-ita" dello slug (mai su una sottostringa).
            if "dub" in item:
                is_ita = bool(item["dub"])
            else:
                is_ita = str(item.get("slug", "")).lower().endswith("-ita")

            # Segmenti (colore, testo semplice): troncamento e padding sul testo visibile,
            # colori applicati dopo (niente sequenze ANSI tagliate né colonne sfalsate)
            segments = [
                (Colors.BRIGHT_GREEN, "[ITA]") if is_ita else (Colors.DIM, "[SUB]"),
                ("", " "),
                ("", title),
            ]
            eng = _single_line(item.get("title_eng"))
            if eng and eng.lower() != title.lower() and not is_ita:
                segments += [("", " "), (Colors.DIM, f"({eng[:30]})")]
            display_title = fit_segments(segments, TITLE_COLUMN_WIDTH)

            year = _single_line(item.get("year", "N/D"))
            eps_val = _single_line(item.get("episodes_count", "?"))
            eps = f"{eps_val} ep."

            aid = str(item["id"])
            line = f"{type_badge}\t{display_title}\t{year:<6}\t{eps:<8}\t{aid}"
            fzf_lines.append(line)

        input_data = "\n".join(fzf_lines)

        # Ogni preview è un nuovo processo: modulo leggero e percorso di Python tra virgolette
        preview_cmd = f"{shlex.quote(sys.executable)} -m ani_it.preview {{5}}"

        # Palette stile Tokyo Night / Catppuccin per fzf
        fzf_args = [
            "fzf",
            "--ansi",
            "--layout=reverse",
            "--height=85%",
            "--border=rounded",
            "--delimiter=\t",
            "--with-nth=1..4",
            "--prompt= 󰍉 Cerca Anime > ",
            "--pointer=󰐊",
            "--marker=󰄬",
            "--header=TIPO      TITOLO                                                           ANNO   EPISODI",
            f"--preview={preview_cmd}",
            "--preview-window=right:52%:wrap:border-rounded",
            "--color=bg+:#283457,bg:#1a1b26,spinner:#7dcfff,hl:#bb9af7",
            "--color=fg:#c0caf5,header:#7aa2f7,info:#7dcfff,pointer:#f7768e",
            "--color=marker:#9ece6a,fg+:#ffffff,prompt:#7dcfff,hl+:#7dcfff",
            "--color=border:#3d59a1",
        ]

        try:
            # Si cattura solo stdout: fzf disegna l'interfaccia su stderr in alcune versioni
            res = subprocess.run(
                fzf_args,
                input=input_data,
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return None

            selected_line = res.stdout.strip()
            parts = selected_line.split("\t")
            if len(parts) >= 5:
                selected_id = parts[4].strip()
                for item in anime_list:
                    if str(item["id"]) == selected_id:
                        return item

        except Exception as exc:
            sys.stderr.write(f"{Colors.BRIGHT_RED}{Icons.ERROR} Errore durante l'esecuzione di fzf: {exc}{Colors.RESET}\n")

        return None

    def select_episode(
        self,
        anime_title: str,
        episodes: list[dict[str, Any]],
        anime_id: str | int,
    ) -> Optional[dict[str, Any]]:
        """Apre un menu fzf con badge visivi per episodi (Visto, In Corso, Non Visto)."""
        if not episodes:
            sys.stdout.write(f"{Colors.BRIGHT_YELLOW}{Icons.WARNING} Nessun episodio disponibile.{Colors.RESET}\n")
            return None

        hist_entry = self.history.get_anime_history(anime_id)
        last_watched_ep = hist_entry.get("last_episode", 0) if hist_entry else 0
        last_ep_completed = bool(hist_entry.get("episode_completed")) if hist_entry else False

        fzf_lines: list[str] = []
        for ep in episodes:
            num = ep.get("number", 0)
            try:
                num_val = float(num)
            except (ValueError, TypeError):
                num_val = 0.0

            if last_watched_ep > 0:
                if num_val < last_watched_ep or (num_val == last_watched_ep and last_ep_completed):
                    badge = f"{Colors.BRIGHT_GREEN}󰄬 [VISTO]{Colors.RESET}    "
                elif num_val == last_watched_ep:
                    badge = f"{Colors.BRIGHT_YELLOW}󰐊 [IN CORSO]{Colors.RESET} "
                else:
                    badge = f"{Colors.DIM}󰓛 [NON VISTO]{Colors.RESET}"
            else:
                badge = f"{Colors.DIM}󰓛 [NON VISTO]{Colors.RESET}"

            # Format numero episodio sempre allineato (es. 01, 02 o 12)
            try:
                num_str = f"{int(num_val):02d}" if float(num_val).is_integer() else f"{num_val}"
            except Exception:
                num_str = f"{num}"

            # Pulizia del titolo per evitare duplicazioni del tipo 'Episodio 01 │ Episodio 1'
            raw_title = str(ep.get("title") or "").strip()
            num_clean = str(num).strip()
            if (
                not raw_title
                or raw_title.lower() == f"episodio {num_clean}".lower()
                or raw_title.lower() == f"episodio {num_str}".lower()
                or re.match(r"^episodio\s+\d+(\.\d+)?$", raw_title.lower())
            ):
                clean_title = "-"
            else:
                clean_title = raw_title

            created = ep.get("created_at", "")

            line = (
                f"{badge}  EP. {num_str:<4} │ {fit_plain(_single_line(clean_title), EPISODE_TITLE_WIDTH)}"
                f"\t{created}\t{ep.get('id')}"
            )
            fzf_lines.append(line)

        input_data = "\n".join(fzf_lines)

        fzf_args = [
            "fzf",
            "--ansi",
            "--layout=reverse",
            "--height=75%",
            "--border=rounded",
            "--delimiter=\t",
            "--with-nth=1..2",
            "--prompt= 󰐊 Seleziona Episodio > ",
            "--pointer=󰐊",
            "--marker=󰄬",
            f"--header=Serie: {anime_title} (Totale: {len(episodes)} ep.)\nSTATO        EPISODIO   TITOLO                                  DATA RILASCIO",
            "--color=bg+:#283457,bg:#1a1b26,spinner:#7dcfff,hl:#bb9af7",
            "--color=fg:#c0caf5,header:#7aa2f7,info:#7dcfff,pointer:#f7768e",
            "--color=marker:#9ece6a,fg+:#ffffff,prompt:#7dcfff,hl+:#7dcfff",
            "--color=border:#3d59a1",
        ]

        try:
            res = subprocess.run(
                fzf_args,
                input=input_data,
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return None

            selected_line = res.stdout.strip()
            parts = selected_line.split("\t")
            if len(parts) >= 3:
                selected_id = parts[2].strip()
                for ep in episodes:
                    if str(ep.get("id")) == selected_id:
                        return ep

        except Exception as exc:
            sys.stderr.write(f"{Colors.BRIGHT_RED}{Icons.ERROR} Errore fzf episodi: {exc}{Colors.RESET}\n")

        return None

    @staticmethod
    def _render_box(title: str, rows: list[str], border: str) -> str:
        """Box con bordi allineati: la larghezza si calcola sul testo visibile (senza ANSI)."""
        inner = MENU_BOX_WIDTH
        top_fill = "─" * max(1, inner - visible_len(title) - 5)
        lines = [
            f"{border}  ╭─── {Colors.BOLD}{Colors.BRIGHT_CYAN}{title}{Colors.RESET}{border} {top_fill}╮",
            f"  │{' ' * inner}│",
        ]
        lines += [f"  │{pad_visible('   ' + row, inner)}│" for row in rows]
        lines += [f"  │{' ' * inner}│", f"  ╰{'─' * inner}╯{Colors.RESET}"]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _prompt_choice(valid: dict[str, str], default: Optional[str], prompt: str) -> str:
        """Chiede una scelta finché non è valida. Invio = default; EOF/Ctrl+C = 'quit'.

        `valid` associa ogni input accettato (minuscolo) all'azione restituita: le voci non
        disponibili non vi compaiono, così una scelta non offerta viene richiesta di nuovo
        invece di essere interpretata come un'altra azione.
        """
        while True:
            sys.stdout.write(f"  {Colors.BOLD}{Colors.BRIGHT_CYAN}{Icons.ARROW_RIGHT} {prompt}{Colors.RESET}")
            sys.stdout.flush()
            try:
                choice = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                return "quit"
            if not choice and default is not None:
                return default
            if choice in valid:
                return valid[choice]
            sys.stdout.write(f"  {Colors.BRIGHT_YELLOW}{Icons.WARNING} Scelta non valida.{Colors.RESET}\n")

    def post_watch_menu(self, current_episode_num: Any, has_next: bool, has_prev: bool) -> str:
        """Menu post-visione: mostra e accetta solo le azioni disponibili per questo episodio."""
        self.clear_screen()
        valid = {"r": "replay", "replay": "replay", "restart": "replay",
                 "s": "select", "select": "select", "scegli": "select",
                 "d": "download", "download": "download", "scarica": "download",
                 "q": "quit", "quit": "quit", "esci": "quit"}
        rows: list[str] = []
        if has_next:
            valid.update({"n": "next", "next": "next", "prossimo": "next"})
            rows.append(f"{Colors.BRIGHT_GREEN}󰐊 [Invio] / [n]{Colors.RESET}  Riproduci prossimo episodio")
        if has_prev:
            valid.update({"p": "prev", "prev": "prev", "precedente": "prev"})
            rows.append(f"{Colors.BRIGHT_YELLOW}󰁍 [p]{Colors.RESET}            Riproduci episodio precedente")
        rows += [
            f"{Colors.BRIGHT_CYAN}󰑐 [r]{Colors.RESET}            Riavvia questo episodio",
            f"{Colors.WHITE}󰍉 [s]{Colors.RESET}            Torna alla selezione episodi",
            f"{Colors.BRIGHT_MAGENTA}󰇚 [d]{Colors.RESET}            Scarica questo episodio",
            f"{Colors.BRIGHT_RED}󰅚 [q]{Colors.RESET}            Esci dal programma",
        ]
        sys.stdout.write("\n" + self._render_box(
            f"{Icons.TV} Riproduzione terminata: Episodio {current_episode_num}", rows, Colors.BRIGHT_BLUE
        ))
        default = "next" if has_next else "select"
        return self._prompt_choice(valid, default, f"Azione [{'n' if has_next else 's'}]: ")

    def error_menu(
        self,
        current_episode_num: Any,
        message: str,
        has_next: bool,
        has_prev: bool,
    ) -> str:
        """Menu mostrato dopo un errore (risoluzione stream, riproduzione, download).

        Non pulisce lo schermo, così il messaggio resta leggibile. Restituisce
        'retry', 'next', 'prev', 'select', 'download' o 'quit'.
        """
        sys.stdout.write(f"\n  {Colors.BRIGHT_RED}{Icons.ERROR} {message}{Colors.RESET}\n")
        valid = {"r": "retry", "retry": "retry", "riprova": "retry",
                 "s": "select", "select": "select", "scegli": "select",
                 "d": "download", "download": "download", "scarica": "download",
                 "q": "quit", "quit": "quit", "esci": "quit"}
        rows = [f"{Colors.BRIGHT_GREEN}󰑐 [Invio] / [r]{Colors.RESET}  Riprova questo episodio"]
        if has_next:
            valid.update({"n": "next", "next": "next", "prossimo": "next"})
            rows.append(f"{Colors.BRIGHT_CYAN}󰐊 [n]{Colors.RESET}            Passa al prossimo episodio")
        if has_prev:
            valid.update({"p": "prev", "prev": "prev", "precedente": "prev"})
            rows.append(f"{Colors.BRIGHT_YELLOW}󰁍 [p]{Colors.RESET}            Passa all'episodio precedente")
        rows += [
            f"{Colors.WHITE}󰍉 [s]{Colors.RESET}            Torna alla selezione episodi",
            f"{Colors.BRIGHT_MAGENTA}󰇚 [d]{Colors.RESET}            Prova a scaricare l'episodio",
            f"{Colors.BRIGHT_RED}󰅚 [q]{Colors.RESET}            Esci dal programma",
        ]
        sys.stdout.write("\n" + self._render_box(
            f"{Icons.ERROR} Errore: Episodio {current_episode_num}", rows, Colors.BRIGHT_RED
        ))
        return self._prompt_choice(valid, "retry", "Azione [r]: ")

    def post_download_menu(
        self,
        current_episode_num: Any,
        has_next: bool = True,
        has_prev: bool = False,
    ) -> str:
        """Menu dedicato post-download (mostrato solo se il download è riuscito)."""
        self.clear_screen()
        valid = {"p": "play", "play": "play", "riproduci": "play",
                 "s": "select", "select": "select", "scegli": "select",
                 "q": "quit", "quit": "quit", "esci": "quit"}
        rows = [f"{Colors.BRIGHT_GREEN}󰐊 [Invio] / [p]{Colors.RESET}  Riproduci episodio scaricato con MPV"]
        if has_next:
            valid.update({"n": "next", "next": "next", "prossimo": "next"})
            rows.append(f"{Colors.BRIGHT_CYAN}󰑐 [n]{Colors.RESET}            Scarica prossimo episodio")
        rows += [
            f"{Colors.WHITE}󰍉 [s]{Colors.RESET}            Torna alla selezione episodi",
            f"{Colors.BRIGHT_RED}󰅚 [q]{Colors.RESET}            Esci dal programma",
        ]
        sys.stdout.write("\n" + self._render_box(
            f"{Icons.DOWNLOAD} Download completato: Episodio {current_episode_num}", rows, Colors.BRIGHT_MAGENTA
        ))
        return self._prompt_choice(valid, "play", "Azione [p]: ")

    def render_anime_preview(self, anime_id: str) -> None:
        """Stampa la scheda dell'anime (locandina con chafa + sinossi) letta dalla cache."""
        render_preview(anime_id, self.config.general.preview_art, cache_dir=self.cache_dir)
