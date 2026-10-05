"""Motore di download per episodi singoli o in batch con architettura Dual-Engine (aria2c e yt-dlp)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from ani_it.config import Config
from ani_it.constants import Colors, FORMAT_HLS, FORMAT_MP4, Icons
from ani_it.resolver import StreamResolver
from ani_it.scraper import AnimeUnityScraper
from ani_it.utils import (
    SignalHandler,
    check_binary,
    get_download_dir,
    parse_episode_range,
    sanitize_filename,
)


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
        self.scraper = scraper or AnimeUnityScraper()
        self.resolver = resolver or StreamResolver()
        self.download_base = get_download_dir()

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
        clean_title = sanitize_filename(anime_title)
        dest_dir = output_dir or (self.download_base / clean_title)
        dest_dir.mkdir(parents=True, exist_ok=True)

        target_file = dest_dir / f"Episodio_{episode_number}.mp4"
        part_file = dest_dir / f"Episodio_{episode_number}.mp4.part"
        aria2_file = dest_dir / f"Episodio_{episode_number}.mp4.aria2"

        if target_file.exists() and target_file.stat().st_size > 1024 * 1024:
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

        # Pulizia di sicurezza in caso di interruzione Ctrl+C
        def cleanup_partial() -> None:
            for p in (part_file, aria2_file):
                if p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        pass

        SignalHandler.register_cleanup(cleanup_partial)

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
                "-x", "16",
                "-s", "16",
                "-k", "1M",
                "-j", "4",
                "--auto-file-renaming=false",
                "--allow-overwrite=true",
                "--check-certificate=false",
                "--summary-interval=1",
                f"--header=User-Agent: {headers.get('User-Agent', '')}",
                f"--header=Referer: {headers.get('Referer', '')}",
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
                "--no-check-certificates",
                "--no-playlist",
                "--user-agent", headers.get("User-Agent", ""),
                "--referer", headers.get("Referer", ""),
                "-o", str(target_file),
            ]
            if has_aria2:
                cmd.extend([
                    "--external-downloader",
                    self.config.downloader.binary,
                    "--external-downloader-args",
                    f"{self.config.downloader.binary}:-c -x 16 -s 16 -k 1M -j 4",
                ])
            else:
                sys.stdout.write(
                    f"{Colors.DIM}(aria2c non rilevato, utilizzo downloader interno di yt-dlp){Colors.RESET}\n"
                )
            cmd.append(stream_url)

        try:
            proc = subprocess.Popen(cmd)
            SignalHandler.register_process(proc)
            returncode = proc.wait()
        except KeyboardInterrupt:
            cleanup_partial()
            return False
        finally:
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

        cleanup_partial()
        sys.stderr.write(
            f"\n{Colors.BRIGHT_RED}{Icons.CROSS} Errore durante il download dell'episodio {episode_number}.{Colors.RESET}\n"
        )
        return False

    def download_range(
        self,
        anime_id: str | int,
        slug: str,
        range_str: str,
    ) -> None:
        """Scarica un intervallo specificato di episodi (es. '1-12', '1,3,5', 'all')."""
        sys.stdout.write(
            f"{Colors.BRIGHT_CYAN}{Icons.SEARCH} Recupero lista episodi per il download...{Colors.RESET}\n"
        )
        details = self.scraper.get_anime_details(anime_id, slug)
        episodes = details.get("episodes", [])
        if not episodes:
            sys.stderr.write(
                f"{Colors.BRIGHT_RED}{Icons.ERROR} Nessun episodio disponibile per questa serie.{Colors.RESET}\n"
            )
            return

        total_eps = len(episodes)
        target_numbers = parse_episode_range(range_str, max_ep=total_eps)
        if not target_numbers:
            sys.stderr.write(
                f"{Colors.BRIGHT_RED}{Icons.ERROR} Nessun episodio valido nell'intervallo specificato: '{range_str}'. "
                f"La serie ha {total_eps} episodi.{Colors.RESET}\n"
            )
            return

        anime_title = details.get("title", f"Anime_{anime_id}")
        clean_title = sanitize_filename(anime_title)
        dest_dir = self.download_base / clean_title
        dest_dir.mkdir(parents=True, exist_ok=True)

        sys.stdout.write(
            f"{Colors.BRIGHT_GREEN}{Icons.FOLDER} Cartella di destinazione:{Colors.RESET} {dest_dir}\n"
            f"{Colors.BRIGHT_YELLOW}{Icons.FILM} Download in coda: {len(target_numbers)} episodi {target_numbers}{Colors.RESET}\n\n"
        )

        # Mappa numero -> episodio
        ep_map = {int(float(ep["number"])): ep for ep in episodes if str(ep.get("number", "")).replace(".", "").isdigit()}

        successful = 0
        for num in target_numbers:
            ep = ep_map.get(num)
            if not ep:
                continue
            ok = self.download_episode(
                anime_title=anime_title,
                episode_number=num,
                episode_link=ep["link"],
                output_dir=dest_dir,
                episode_id=ep.get("id"),
            )
            if ok:
                successful += 1

        sys.stdout.write(
            f"\n{Colors.BRIGHT_GREEN}{Icons.SPARKLE} Riepilogo download: {successful}/{len(target_numbers)} episodi scaricati.{Colors.RESET}\n"
        )
