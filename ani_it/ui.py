"""Interfaccia utente TUI interattiva basata su fzf, chafa e grafica terminale moderna."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Optional

from ani_it.config import Config
from ani_it.constants import APP_NAME, APP_VERSION, Colors, Icons
from ani_it.history import HistoryManager
from ani_it.utils import (
    check_binary,
    check_required_dependencies,
    get_cache_dir,
)


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
        """Verifica la presenza dei binari essenziali per Arch Linux prima dell'avvio."""
        ok, missing = check_required_dependencies()
        if not ok:
            missing_str = " ".join(missing)
            sys.stderr.write(
                f"\n{Colors.BRIGHT_RED}{Icons.ERROR} ERRORE DIPENDENZE ARCH LINUX:{Colors.RESET}\n"
                f"I seguenti componenti obbligatori non sono installati nel sistema: {Colors.BOLD}{missing_str}{Colors.RESET}\n\n"
                f"Puoi installarli su Arch Linux con il comando:\n"
                f"  {Colors.BRIGHT_GREEN}sudo pacman -S {missing_str}{Colors.RESET}\n\n"
            )
            sys.exit(1)

    def select_anime(self, anime_list: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """Apre un menu interattivo fzf con preview grafica chafa e palette colori curata."""
        if not anime_list:
            return None

        if len(anime_list) == 1:
            return anime_list[0]

        # Salva i dettagli di ogni anime nella cache per la preview dinamica di fzf
        for item in anime_list:
            aid = str(item["id"])
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

            title = str(item.get("title") or "").strip()
            if not title or title.lower() in ("senza titolo", "anime senza titolo", "null", "none"):
                title = str(item.get("title_eng") or item.get("slug") or f"Anime {item.get('id')}").replace("-", " ").title()

            is_ita = "(ita)" in title.lower() or "-ita" in str(item.get("slug", "")).lower()
            lang_tag = f"{Colors.BRIGHT_GREEN}[ITA]{Colors.RESET} " if is_ita else f"{Colors.DIM}[SUB]{Colors.RESET} "

            eng = item.get("title_eng")
            if eng and eng.lower() != title.lower() and not is_ita:
                display_title = f"{lang_tag}{title} {Colors.DIM}({eng[:30]}){Colors.RESET}"
            else:
                display_title = f"{lang_tag}{title}"
            display_title = display_title[:75]

            year = str(item.get("year", "N/D"))
            eps_val = str(item.get("episodes_count", "?"))
            eps = f"{eps_val} ep."

            aid = str(item["id"])
            line = f"{type_badge}\t{display_title:<65}\t{year:<6}\t{eps:<8}\t{aid}"
            fzf_lines.append(line)

        input_data = "\n".join(fzf_lines)

        py_bin = sys.executable
        preview_cmd = f"{py_bin} -m ani_it --preview-anime {{5}}"

        # Palette stile Tokyo Night / Catppuccin per fzf
        fzf_args = [
            "fzf",
            "--ansi",
            "--layout=reverse",
            "--height=85%",
            "--border=rounded",
            "--delimiter=\t",
            "--with-nth=1..4",
            f"--prompt= 󰍉 Cerca Anime > ",
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
            res = subprocess.run(
                fzf_args,
                input=input_data,
                text=True,
                capture_output=True,
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

        fzf_lines: list[str] = []
        for ep in episodes:
            num = ep.get("number", 0)
            try:
                num_val = float(num)
            except (ValueError, TypeError):
                num_val = 0.0

            if last_watched_ep > 0:
                if num_val < last_watched_ep:
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

            line = f"{badge}  EP. {num_str:<4} │ {clean_title:<38}\t{created}\t{ep.get('id')}"
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
            f"--prompt= 󰐊 Seleziona Episodio > ",
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
                capture_output=True,
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

    def post_watch_menu(self, current_episode_num: Any, has_next: bool, has_prev: bool) -> str:
        """Mostra il menu istantaneo post-visione nel terminale racchiuso in un box ordinato."""
        self.clear_screen()
        menu_box = f"""
{Colors.BRIGHT_BLUE}  ╭─── {Colors.BOLD}{Colors.BRIGHT_CYAN}{Icons.TV} Riproduzione terminata: Episodio {current_episode_num}{Colors.RESET}{Colors.BRIGHT_BLUE} ────────────────────────╮
  │                                                                 │
  │   {Colors.BRIGHT_GREEN}󰐊 [Invio] / [n]{Colors.RESET}  Riproduci prossimo episodio                     │
  │   {Colors.BRIGHT_YELLOW}󰁍 [p]{Colors.RESET}            Riproduci episodio precedente                   │
  │   {Colors.BRIGHT_CYAN}󰑐 [r]{Colors.RESET}            Riavvia questo episodio                         │
  │   {Colors.WHITE}󰍉 [s]{Colors.RESET}            Torna alla selezione episodi                    │
  │   {Colors.BRIGHT_MAGENTA}󰇚 [d]{Colors.RESET}            Scarica questo episodio                         │
  │   {Colors.BRIGHT_RED}󰅚 [q]{Colors.RESET}            Esci dal programma                              │
  │                                                                 │
  ╰─────────────────────────────────────────────────────────────────╯{Colors.RESET}
"""
        sys.stdout.write(menu_box)
        sys.stdout.write(f"  {Colors.BOLD}{Colors.BRIGHT_CYAN}{Icons.ARROW_RIGHT} Azione [n]: {Colors.RESET}")
        sys.stdout.flush()

        try:
            choice = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "quit"

        if not choice or choice in ("n", "next", "prossimo"):
            return "next"
        if choice in ("p", "prev", "precedente"):
            return "prev"
        if choice in ("r", "replay", "restart"):
            return "replay"
        if choice in ("s", "select", "scegli"):
            return "select"
        if choice in ("d", "download", "scarica"):
            return "download"
        if choice in ("q", "quit", "esci"):
            return "quit"

        return "next"

    def post_download_menu(
        self,
        current_episode_num: Any,
        has_next: bool = True,
        has_prev: bool = False,
    ) -> str:
        """Mostra il menu dedicato post-download per evitare riproduzioni accidentali o sovrapposizioni."""
        self.clear_screen()
        menu_box = f"""
{Colors.BRIGHT_MAGENTA}  ╭─── {Colors.BOLD}{Colors.BRIGHT_CYAN}{Icons.DOWNLOAD} Download completato: Episodio {current_episode_num}{Colors.RESET}{Colors.BRIGHT_MAGENTA} ────────────────────────╮
  │                                                                 │
  │   {Colors.BRIGHT_GREEN}󰐊 [Invio] / [p]{Colors.RESET}  Riproduci episodio scaricato con MPV            │
  │   {Colors.BRIGHT_CYAN}󰑐 [n]{Colors.RESET}            Scarica prossimo episodio                       │
  │   {Colors.WHITE}󰍉 [s]{Colors.RESET}            Torna alla selezione episodi                    │
  │   {Colors.BRIGHT_RED}󰅚 [q]{Colors.RESET}            Esci dal programma                              │
  │                                                                 │
  ╰─────────────────────────────────────────────────────────────────╯{Colors.RESET}
"""
        sys.stdout.write(menu_box)
        sys.stdout.write(f"  {Colors.BOLD}{Colors.BRIGHT_CYAN}{Icons.ARROW_RIGHT} Azione [p]: {Colors.RESET}")
        sys.stdout.flush()

        try:
            choice = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "quit"

        if not choice or choice in ("p", "play", "riproduci"):
            return "play"
        if choice in ("n", "next", "prossimo"):
            return "next"
        if choice in ("s", "select", "scegli"):
            return "select"
        if choice in ("q", "quit", "esci"):
            return "quit"

        return "play"

    def render_anime_preview(self, anime_id: str) -> None:
        """Stampa la preview formattata con chafa (locandina grafica) e sinossi per fzf."""
        cache_file = self.data_cache_dir / f"{anime_id}.json"
        if not cache_file.is_file():
            sys.stdout.write(f"{Colors.DIM}Nessuna informazione disponibile per l'anime {anime_id}{Colors.RESET}\n")
            return

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
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
        if self.config.general.preview_art and check_binary("chafa") and img_url:
            cover_path = self.covers_dir / f"{anime_id}.jpg"
            if not cover_path.exists():
                try:
                    import requests
                    r = requests.get(img_url, timeout=5)
                    if r.status_code == 200:
                        cover_path.write_bytes(r.content)
                except Exception:
                    pass

            if cover_path.exists():
                try:
                    subprocess.run(
                        ["chafa", "--size=36x20", "--clear", "--symbols=vhalf,braille", str(cover_path)],
                        check=False,
                    )
                except Exception:
                    pass

        # Scheda descrittiva elegante
        sys.stdout.write(f"\n{Colors.BOLD}{Colors.BRIGHT_WHITE}╭───────────────────────────────────────────────────╮{Colors.RESET}\n")
        sys.stdout.write(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}  {title[:47]}{Colors.RESET}\n")
        if title_eng and title_eng.lower() != title.lower():
            sys.stdout.write(f"{Colors.DIM}  {title_eng[:47]}{Colors.RESET}\n")
        sys.stdout.write(f"{Colors.BOLD}{Colors.BRIGHT_WHITE}╰───────────────────────────────────────────────────╯{Colors.RESET}\n")

        # Metadati a icone
        score_str = f"  {Colors.BRIGHT_YELLOW}⭐ Voto:{Colors.RESET} {score}" if score else ""
        sys.stdout.write(f"{Colors.BRIGHT_CYAN}󰎁 Tipo:{Colors.RESET} {anime_type}   {Colors.BRIGHT_GREEN}󰃭 Anno:{Colors.RESET} {year}   {Colors.BRIGHT_MAGENTA}󰐊 Episodi:{Colors.RESET} {eps}{score_str}\n")
        sys.stdout.write(f"{Colors.BRIGHT_BLUE}󰅚 Stato:{Colors.RESET} {status}\n")

        if genres:
            genre_line = ", ".join(genres[:4])
            sys.stdout.write(f"{Colors.BRIGHT_YELLOW}🎭 Generi:{Colors.RESET} {genre_line}\n")

        # Trama formattata con a capo automatico
        if plot:
            sys.stdout.write(f"\n{Colors.BOLD}{Colors.BRIGHT_WHITE}󰋽 TRAMA:{Colors.RESET}\n")
            wrapped = textwrap.fill(plot, width=54)
            sys.stdout.write(f"{Colors.WHITE}{wrapped}{Colors.RESET}\n")
