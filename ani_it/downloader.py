"""Motore di download per episodi singoli o in batch con architettura Dual-Engine (aria2c e yt-dlp)."""

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from ani_it.config import Config
from ani_it.constants import Colors, FORMAT_HLS, FORMAT_MP4, Icons
from ani_it.resolver import StreamResolver
from ani_it.scraper import AnimeUnityScraper, EpisodesNotFoundError
from ani_it.utils import (
    EpisodeNumber,
    SignalHandler,
    check_binary,
    get_download_dir,
    normalize_episode_number,
    parse_episode_range,
    quality_height,
    sanitize_filename,
)

# Sotto questa dimensione un file non è considerato un episodio completo
MIN_COMPLETE_SIZE = 1024 * 1024


def _format_numbers(numbers: list[EpisodeNumber], limit: int = 20) -> str:
    """Elenco compatto di numeri di episodio per i messaggi all'utente."""
    shown = ", ".join(str(n) for n in numbers[:limit])
    return shown + (f" … (+{len(numbers) - limit})" if len(numbers) > limit else "")


class DownloaderManager:
    """Gestisce il download segmentato degli episodi con architettura Dual-Engine:
    - aria2c diretto per file statici .mp4/.mkv (massima velocità, bypass scraping webpage).
    - yt-dlp per flussi adattivi HLS .m3u8 (con aria2c come external-downloader).
    """

    def __init__(
        self,
        config: Config,
        scraper: Optional[AnimeUnityScraper] = None,
        resolver: Optional[StreamResolver] = None,
    ) -> None:
        self.config = config
        base_url = config.general.base_url
        self.scraper = scraper or AnimeUnityScraper(base_url=base_url)
        self.resolver = resolver or StreamResolver(base_url=base_url)
        self.download_base = get_download_dir()

    def episode_path(
        self,
        anime_title: str,
        episode_number: Any,
        output_dir: Optional[Path] = None,
    ) -> Path:
        """Percorso del file di un episodio scaricato (o in corso di download)."""
        dest_dir = output_dir or (self.download_base / sanitize_filename(anime_title))
        return dest_dir / f"Episodio_{episode_number}.mp4"

    def download_episode(
        self,
        anime_title: str,
        episode_number: Any,
        episode_link: str,
        output_dir: Optional[Path] = None,
        episode_id: Optional[str | int] = None,
    ) -> bool:
        """Scarica un singolo episodio risolvendo lo stream ed eseguendo aria2c o yt-dlp.

        Args:
            anime_title: Titolo della serie anime.
            episode_number: Numero o label dell'episodio.
            episode_link: Link sorgente o endpoint embed dell'episodio.
            output_dir: Cartella di destinazione personalizzata (opzionale).
            episode_id: ID univoco dell'episodio nel DB AnimeUnity per fallback automatico.

        Returns:
            True se il download è andato a buon fine, False altrimenti.
        """
        target_file = self.episode_path(anime_title, episode_number, output_dir)
        dest_dir = target_file.parent
        dest_dir.mkdir(parents=True, exist_ok=True)

        part_file = target_file.with_name(target_file.name + ".part")
        aria2_file = target_file.with_name(target_file.name + ".aria2")

        # aria2c scrive direttamente sul file finale (spesso preallocato): un file grande
        # con accanto il suo file di controllo è un download interrotto, non completo.
        if (
            target_file.exists()
            and target_file.stat().st_size > MIN_COMPLETE_SIZE
            and not aria2_file.exists()
            and not part_file.exists()
        ):
            sys.stdout.write(
                f"{Colors.BRIGHT_GREEN}{Icons.CHECK} Episodio {episode_number} già presente: "
                f"{target_file}{Colors.RESET}\n"
            )
            return True

        sys.stdout.write(
            f"\n{Colors.BRIGHT_CYAN}{Icons.DOWNLOAD} Risoluzione stream per '{anime_title}' - Episodio {episode_number}...{Colors.RESET}\n"
        )

        try:
            stream_info = self.resolver.resolve(
                episode_link,
                quality=self.config.general.quality,
                episode_id=episode_id,
            )
        except Exception as exc:
            sys.stderr.write(
                f"{Colors.BRIGHT_RED}{Icons.ERROR} Impossibile risolvere lo stream: {exc}{Colors.RESET}\n"
            )
            return False

        stream_url = stream_info["stream_url"]
        headers = stream_info.get("headers", {})
        stream_fmt = stream_info.get("format", FORMAT_MP4)

        has_aria2 = check_binary(self.config.downloader.binary)
        is_direct_video = (
            stream_fmt == FORMAT_MP4
            or stream_url.endswith((".mp4", ".mkv"))
            or ".mp4?" in stream_url
            or ".mkv?" in stream_url
        )

        # In caso di errore o Ctrl+C i file parziali (e il file di controllo .aria2 /
        # .part) vengono conservati: servono a `-c` per riprendere dal punto raggiunto.

        if not (is_direct_video and has_aria2) and not check_binary("yt-dlp"):
            sys.stderr.write(
                f"{Colors.BRIGHT_RED}{Icons.ERROR} yt-dlp non è installato: impossibile scaricare "
                f"l'episodio {episode_number}.{Colors.RESET}\n"
                f"  Installalo con: {Colors.BRIGHT_GREEN}sudo pacman -S yt-dlp{Colors.RESET} "
                f"{Colors.DIM}(per gli MP4 diretti basta anche aria2c){Colors.RESET}\n"
            )
            return False

        if is_direct_video and has_aria2:
            # ==================================================================
            # STRATEGIA A: Invocazione diretta di aria2c per MP4 statici
            # Bypassa completamente yt-dlp evitando il parsing webpage generico
            # ==================================================================
            sys.stdout.write(
                f"{Colors.BRIGHT_GREEN}{Icons.ROCKET} Avvio download diretto ad alta velocità con aria2c...{Colors.RESET}\n"
            )
            cmd = [
                self.config.downloader.binary,
                "-c",
                *self.config.downloader.args,
                "--auto-file-renaming=false",
                "--allow-overwrite=true",
                "--summary-interval=1",
                *[f"--header={name}: {headers[name]}" for name in ("User-Agent", "Referer") if headers.get(name)],
                "-d", str(dest_dir),
                "-o", target_file.name,
                stream_url,
            ]
        else:
            # ==================================================================
            # STRATEGIA B: Invocazione yt-dlp per flussi adattivi HLS .m3u8
            # ==================================================================
            cmd = [
                "yt-dlp",
                "--no-warnings",
                "--no-playlist",
                "--user-agent", headers.get("User-Agent", ""),
                "--referer", headers.get("Referer", ""),
                "-o", str(target_file),
            ]

            # Qualità: sul master HLS yt-dlp sceglie video+audio con il filtro di altezza.
            # Non si applica ai file diretti, che non hanno varianti da filtrare.
            height = quality_height(self.config.general.quality)
            if stream_fmt == FORMAT_HLS and height is not None:
                cmd.extend(["-f", f"bv*[height<={height}]+ba/b[height<={height}]"])
            if stream_fmt == FORMAT_HLS:
                cmd.extend(["--merge-output-format", "mp4"])

            if has_aria2:
                aria2_args = shlex.join(["-c", *self.config.downloader.args])
                cmd.extend([
                    "--external-downloader",
                    self.config.downloader.binary,
                    "--external-downloader-args",
                    f"{self.config.downloader.binary}:{aria2_args}",
                ])
            else:
                sys.stdout.write(
                    f"{Colors.DIM}(aria2c non rilevato, utilizzo downloader interno di yt-dlp){Colors.RESET}\n"
                )
            cmd.append(stream_url)

        proc: Optional[subprocess.Popen[bytes]] = None
        try:
            proc = subprocess.Popen(cmd)
            SignalHandler.register_process(proc)
            returncode = proc.wait()
        except FileNotFoundError:
            sys.stderr.write(
                f"{Colors.BRIGHT_RED}{Icons.ERROR} Impossibile avviare '{cmd[0]}': programma non trovato "
                f"nel sistema.{Colors.RESET}\n"
            )
            return False
        except KeyboardInterrupt:
            if proc is not None and proc.poll() is None:
                proc.terminate()
            sys.stderr.write(
                f"\n{Colors.BRIGHT_YELLOW}{Icons.WARNING} Download interrotto: i dati parziali sono "
                f"conservati e il download riprenderà da dove si è fermato.{Colors.RESET}\n"
            )
            return False
        finally:
            if proc is not None:
                SignalHandler.unregister_process(proc)

        if returncode == 0 and target_file.exists():
            # Pulizia file di controllo aria2 se rimasti
            if aria2_file.exists():
                try:
                    aria2_file.unlink()
                except OSError:
                    pass
            sys.stdout.write(
                f"\n{Colors.BRIGHT_GREEN}{Icons.CHECK} Download completato con successo:{Colors.RESET} {target_file}\n"
            )
            return True

        sys.stderr.write(
            f"\n{Colors.BRIGHT_RED}{Icons.CROSS} Errore durante il download dell'episodio {episode_number}.{Colors.RESET}\n"
        )
        if aria2_file.exists() or part_file.exists():
            sys.stderr.write(
                f"{Colors.DIM}I dati parziali sono conservati: rilancia il download per riprenderlo.{Colors.RESET}\n"
            )
        return False

    def download_range(
        self,
        anime_id: str | int,
        slug: str,
        range_str: str,
        details: Optional[dict[str, Any]] = None,
        anime_title: Optional[str] = None,
    ) -> None:
        """Scarica un intervallo specificato di episodi (es. '1-12', '1,3,5', 'all').

        Args:
            details: Dettagli già caricati dal chiamante (evita di scaricare la scheda due volte).
            anime_title: Titolo da usare per la cartella. Il chiamante passa quello mostrato nei
                menu, così lo stesso anime finisce sempre nella stessa cartella; in assenza si usa
                il titolo estratto dalla scheda.
        """
        if details is None:
            sys.stdout.write(
                f"{Colors.BRIGHT_CYAN}{Icons.SEARCH} Recupero lista episodi per il download...{Colors.RESET}\n"
            )
            try:
                details = self.scraper.get_anime_details(anime_id, slug)
            except EpisodesNotFoundError:
                sys.stderr.write(
                    f"{Colors.BRIGHT_RED}{Icons.ERROR} Nessun episodio disponibile per questa serie.{Colors.RESET}\n"
                )
                return
            except Exception as exc:
                sys.stderr.write(
                    f"{Colors.BRIGHT_RED}{Icons.ERROR} Impossibile recuperare la lista episodi: {exc}{Colors.RESET}\n"
                )
                return

        episodes = details.get("episodes", [])
        if not episodes:
            sys.stderr.write(
                f"{Colors.BRIGHT_RED}{Icons.ERROR} Nessun episodio disponibile per questa serie.{Colors.RESET}\n"
            )
            return

        # Mappa numero reale -> episodio (12 e 12.5 restano distinti; l'episodio 0 è valido)
        ep_map: dict[EpisodeNumber, dict[str, Any]] = {}
        for ep in episodes:
            number = normalize_episode_number(ep.get("number"))
            if number is not None:
                ep_map.setdefault(number, ep)

        if not ep_map:
            sys.stderr.write(
                f"{Colors.BRIGHT_RED}{Icons.ERROR} Gli episodi di questa serie non hanno una numerazione valida.{Colors.RESET}\n"
            )
            return

        try:
            target_numbers, missing_numbers = parse_episode_range(range_str, ep_map.keys())
        except ValueError as exc:
            sys.stderr.write(f"{Colors.BRIGHT_RED}{Icons.ERROR} {exc}{Colors.RESET}\n")
            return

        if missing_numbers:
            sys.stderr.write(
                f"{Colors.BRIGHT_YELLOW}{Icons.WARNING} Episodi non presenti nella serie (saltati): "
                f"{_format_numbers(missing_numbers)}{Colors.RESET}\n"
            )

        if not target_numbers:
            ordered = sorted(ep_map)
            sys.stderr.write(
                f"{Colors.BRIGHT_RED}{Icons.ERROR} Nessun episodio valido nell'intervallo specificato: '{range_str}'. "
                f"Episodi disponibili: da {ordered[0]} a {ordered[-1]} ({len(ordered)} in totale).{Colors.RESET}\n"
            )
            return

        anime_title = anime_title or details.get("title") or f"Anime_{anime_id}"
        clean_title = sanitize_filename(anime_title)
        dest_dir = self.download_base / clean_title
        dest_dir.mkdir(parents=True, exist_ok=True)

        sys.stdout.write(
            f"{Colors.BRIGHT_GREEN}{Icons.FOLDER} Cartella di destinazione:{Colors.RESET} {dest_dir}\n"
            f"{Colors.BRIGHT_YELLOW}{Icons.FILM} Download in coda: {len(target_numbers)} episodi {target_numbers}{Colors.RESET}\n\n"
        )

        successful = 0
        failed: list[EpisodeNumber] = []
        for num in target_numbers:
            ep = ep_map[num]
            ok = self.download_episode(
                anime_title=anime_title,
                episode_number=num,
                episode_link=ep["link"],
                output_dir=dest_dir,
                episode_id=ep.get("id"),
            )
            if ok:
                successful += 1
            else:
                failed.append(num)

        sys.stdout.write(
            f"\n{Colors.BRIGHT_GREEN}{Icons.SPARKLE} Riepilogo download: {successful}/{len(target_numbers)} episodi scaricati.{Colors.RESET}\n"
        )
        if failed:
            sys.stderr.write(
                f"{Colors.BRIGHT_RED}{Icons.CROSS} Episodi non scaricati: {_format_numbers(failed)}{Colors.RESET}\n"
            )
