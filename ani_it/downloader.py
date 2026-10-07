"""Motore di download per episodi singoli o in batch con architettura Dual-Engine e fallback streaming (v2.0)."""

import gc
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import requests

from ani_it.config import Config
from ani_it.constants import Colors, FORMAT_HLS, FORMAT_MP4, HTTP_TIMEOUT, Icons
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

# Sotto questa dimensione un file non è considerato un episodio completo (1 MB)
MIN_COMPLETE_SIZE = 1024 * 1024
CHUNK_SIZE_BYTES = 65536  # 64 KB per streaming a basso consumo RAM


def _format_numbers(numbers: list[EpisodeNumber], limit: int = 20) -> str:
    """Elenco compatto di numeri di episodio per i messaggi all'utente."""
    shown = ", ".join(str(n) for n in numbers[:limit])
    return shown + (f" … (+{len(numbers) - limit})" if len(numbers) > limit else "")


class DownloaderManager:
    """Gestisce il download segmentato degli episodi con architettura Dual-Engine a basso consumo RAM."""

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
        """Percorso del file di un episodio scaricato (sanitizzato contro path traversal)."""
        clean_title = sanitize_filename(anime_title)
        dest_dir = output_dir or (self.download_base / clean_title)
        clean_ep = sanitize_filename(f"Episodio_{episode_number}.mp4")
        return dest_dir / clean_ep

    def download_episode(
        self,
        anime_title: str,
        episode_number: Any,
        episode_link: str,
        output_dir: Optional[Path] = None,
        episode_id: Optional[str | int] = None,
    ) -> bool:
        """Scarica un singolo episodio risolvendo lo stream ed eseguendo aria2c o yt-dlp."""
        target_file = self.episode_path(anime_title, episode_number, output_dir)
        dest_dir = target_file.parent
        dest_dir.mkdir(parents=True, exist_ok=True)

        part_file = target_file.with_name(target_file.name + ".part")
        aria2_file = target_file.with_name(target_file.name + ".aria2")

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
        has_ytdlp = check_binary("yt-dlp")
        is_direct_video = (
            stream_fmt == FORMAT_MP4
            or stream_url.endswith((".mp4", ".mkv"))
            or ".mp4?" in stream_url
            or ".mkv?" in stream_url
        )

        # Se nessun binario esterno è presente ma il video è diretto HTTP, usa il fallback streaming nativo
        if is_direct_video and not has_aria2 and not has_ytdlp:
            return self._download_via_python_stream(stream_url, headers, target_file, part_file, episode_number)

        if not (is_direct_video and has_aria2) and not has_ytdlp:
            sys.stderr.write(
                f"{Colors.BRIGHT_RED}{Icons.ERROR} yt-dlp non è installato: impossibile scaricare "
                f"l'episodio {episode_number}.{Colors.RESET}\n"
                f"  Installalo con il gestore pacchetti del tuo sistema (es. pacman -S yt-dlp o brew install yt-dlp).\n"
            )
            return False

        if is_direct_video and has_aria2:
            # STRATEGIA A: Invocazione diretta di aria2c per MP4 statici con disk-cache limitata a 16M
            sys.stdout.write(
                f"{Colors.BRIGHT_GREEN}{Icons.ROCKET} Avvio download diretto ad alta velocità con aria2c...{Colors.RESET}\n"
            )
            cmd = [
                self.config.downloader.binary,
                "-c",
                *self.config.downloader.args,
                "--disk-cache=16M",
                "--auto-file-renaming=false",
                "--allow-overwrite=true",
                "--summary-interval=1",
                *[f"--header={name}: {headers[name]}" for name in ("User-Agent", "Referer") if headers.get(name)],
                "-d", str(dest_dir),
                "-o", target_file.name,
                stream_url,
            ]
        else:
            # STRATEGIA B: Invocazione yt-dlp per flussi adattivi HLS .m3u8
            cmd = [
                "yt-dlp",
                "--no-warnings",
                "--no-playlist",
                "--user-agent", headers.get("User-Agent", ""),
                "--referer", headers.get("Referer", ""),
                "-o", str(target_file),
            ]

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
        returncode = -1
        try:
            proc = subprocess.Popen(cmd)
            SignalHandler.register_process(proc)
            returncode = proc.wait()
        except FileNotFoundError:
            sys.stderr.write(
                f"{Colors.BRIGHT_RED}{Icons.ERROR} Impossibile avviare '{cmd[0]}': programma non trovato nel sistema.{Colors.RESET}\n"
            )
            return False
        except KeyboardInterrupt:
            if proc is not None and proc.poll() is None:
                proc.terminate()
            sys.stderr.write(
                f"\n{Colors.BRIGHT_YELLOW}{Icons.WARNING} Download interrotto: dati parziali conservati per la ripresa.{Colors.RESET}\n"
            )
            return False
        finally:
            if proc is not None:
                SignalHandler.unregister_process(proc)
            gc.collect()

        if returncode == 0 and target_file.exists():
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

    def _download_via_python_stream(
        self,
        stream_url: str,
        headers: dict[str, str],
        target_file: Path,
        part_file: Path,
        episode_number: Any,
    ) -> bool:
        """Download in streaming via Python requests a blocchi da 64 KB (nessun buffering cumulativo in RAM)."""
        sys.stdout.write(
            f"{Colors.BRIGHT_GREEN}{Icons.DOWNLOAD} Download in streaming HTTP diretto (blocchi 64 KB)...{Colors.RESET}\n"
        )
        try:
            req_headers = {"User-Agent": headers.get("User-Agent", ""), "Referer": headers.get("Referer", "")}
            with requests.get(stream_url, headers=req_headers, stream=True, timeout=HTTP_TIMEOUT) as resp:
                resp.raise_for_status()
                with open(part_file, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE_BYTES):
                        if chunk:
                            f.write(chunk)
            part_file.replace(target_file)
            sys.stdout.write(
                f"\n{Colors.BRIGHT_GREEN}{Icons.CHECK} Download completato con successo:{Colors.RESET} {target_file}\n"
            )
            return True
        except Exception as exc:
            sys.stderr.write(
                f"\n{Colors.BRIGHT_RED}{Icons.CROSS} Errore streaming download episodio {episode_number}: {exc}{Colors.RESET}\n"
            )
            return False
        finally:
            gc.collect()

    def download_range(
        self,
        anime_id: str | int,
        slug: str,
        range_str: str,
        details: Optional[dict[str, Any]] = None,
        anime_title: Optional[str] = None,
    ) -> None:
        """Scarica un intervallo specificato di episodi (es. '1-12', '1,3,5', 'all')."""
        if details is None:
            sys.stdout.write(
                f"{Colors.BRIGHT_CYAN}{Icons.PROGRESS} Caricamento lista episodi da AnimeUnity...{Colors.RESET}\n"
            )
            try:
                details = self.scraper.get_anime_details(anime_id, slug)
            except EpisodesNotFoundError as exc:
                sys.stderr.write(f"{Colors.BRIGHT_YELLOW}{Icons.WARNING} Nessun episodio disponibile: {exc}{Colors.RESET}\n")
                return
            except Exception as exc:
                sys.stderr.write(f"{Colors.BRIGHT_RED}{Icons.ERROR} Impossibile ottenere i dettagli: {exc}{Colors.RESET}\n")
                return

        episodes = details.get("episodes", [])
        if not episodes:
            sys.stdout.write(f"{Colors.BRIGHT_YELLOW}{Icons.WARNING} Nessun episodio disponibile per questa serie.{Colors.RESET}\n")
            return

        title = anime_title or details.get("title") or slug
        target_folder = self.download_base / sanitize_filename(title)

        available_numbers = [ep.get("number") for ep in episodes if ep.get("number") is not None]
        try:
            selected_numbers, missing = parse_episode_range(range_str, available_numbers)
        except ValueError as exc:
            sys.stderr.write(f"{Colors.BRIGHT_RED}{Icons.ERROR} {exc}{Colors.RESET}\n")
            return

        if missing:
            sys.stdout.write(
                f"{Colors.BRIGHT_YELLOW}{Icons.WARNING} Attenzione: i seguenti episodi non presenti nella serie saranno saltati:\n"
                f"  {_format_numbers(missing)}{Colors.RESET}\n\n"
            )

        if not selected_numbers:
            sys.stdout.write(
                f"{Colors.BRIGHT_YELLOW}{Icons.WARNING} Nessun episodio dell'intervallo '{range_str}' è presente nella serie.\n"
                f"  Episodi disponibili: {_format_numbers(sorted(available_numbers))}{Colors.RESET}\n"
            )
            return

        selected_set = set(selected_numbers)
        to_download = [ep for ep in episodes if ep.get("number") in selected_set]

        total = len(to_download)
        sys.stdout.write(
            f"\n{Colors.BOLD}{Colors.BRIGHT_MAGENTA}{Icons.DOWNLOAD} Inizio download di {total} episodi per '{title}'...{Colors.RESET}\n"
        )

        completed = 0
        failed: list[EpisodeNumber] = []

        try:
            for i, ep in enumerate(to_download, 1):
                ep_num = ep.get("number")
                sys.stdout.write(
                    f"\n{Colors.BOLD}{Colors.WHITE}[{i}/{total}] Download Episodio {ep_num}...{Colors.RESET}\n"
                )
                success = self.download_episode(
                    anime_title=title,
                    episode_number=ep_num,
                    episode_link=ep.get("link", ""),
                    output_dir=target_folder,
                    episode_id=ep.get("id"),
                )
                if success:
                    completed += 1
                else:
                    failed.append(ep_num)
        except KeyboardInterrupt:
            sys.stdout.write(f"\n{Colors.BRIGHT_YELLOW}{Icons.WARNING} Download batch interrotto dall'utente.{Colors.RESET}\n")
        finally:
            gc.collect()

        sys.stdout.write(
            f"\n{Colors.BOLD}{Colors.BRIGHT_GREEN}{Icons.CHECK} Download completati: {completed}/{total}{Colors.RESET}\n"
        )
        if failed:
            sys.stdout.write(
                f"{Colors.BRIGHT_RED}{Icons.ERROR} Episodi non riusciti: {_format_numbers(failed)}{Colors.RESET}\n"
            )
