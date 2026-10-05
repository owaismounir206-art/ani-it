"""Test unitari per DownloaderManager (Dual-Engine: aria2c e yt-dlp)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ani_it.config import Config
from ani_it.downloader import DownloaderManager


class TestDownloaderManager(unittest.TestCase):
    """Verifica il comportamento del motore di download con aria2c e yt-dlp."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)
        self.config = Config()
        self.mock_resolver = MagicMock()
        self.mock_scraper = MagicMock()
        self.manager = DownloaderManager(
            config=self.config,
            scraper=self.mock_scraper,
            resolver=self.mock_resolver,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @patch("ani_it.downloader.check_binary")
    @patch("ani_it.downloader.subprocess.Popen")
    def test_direct_mp4_downloads_with_aria2c(self, mock_popen: MagicMock, mock_check: MagicMock) -> None:
        """Verifica che un flusso MP4 con aria2c installato invochi direttamente aria2c senza yt-dlp."""
        mock_check.return_value = True  # aria2c presente
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        self.mock_resolver.resolve.return_value = {
            "stream_url": "https://vix-content.net/video.mp4",
            "format": "mp4",
            "headers": {"User-Agent": "TestUA", "Referer": "https://vixcloud.co"},
        }

        # Simula creazione del file dopo il download
        target_file = self.output_dir / "Episodio_1.mp4"
        target_file.write_bytes(b"DATA" * 1024)

        success = self.manager.download_episode(
            anime_title="Test Anime",
            episode_number=1,
            episode_link="https://vixcloud.co/embed/1",
            output_dir=self.output_dir,
        )

        self.assertTrue(success)
        # Verifica che il comando eseguito sia aria2c e non yt-dlp
        cmd_called = mock_popen.call_args[0][0]
        self.assertEqual(cmd_called[0], "aria2c")
        self.assertIn("-c", cmd_called)
        self.assertIn("-x", cmd_called)
        self.assertIn("https://vix-content.net/video.mp4", cmd_called)

    @patch("ani_it.downloader.check_binary")
    @patch("ani_it.downloader.subprocess.Popen")
    def test_hls_stream_downloads_with_ytdlp(self, mock_popen: MagicMock, mock_check: MagicMock) -> None:
        """Verifica che un flusso HLS (.m3u8) invochi yt-dlp con --no-playlist."""
        mock_check.return_value = True
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        self.mock_resolver.resolve.return_value = {
            "stream_url": "https://vix-content.net/master.m3u8",
            "format": "hls",
            "headers": {"User-Agent": "TestUA", "Referer": "https://vixcloud.co"},
        }

        target_file = self.output_dir / "Episodio_2.mp4"
        target_file.write_bytes(b"DATA" * 1024)

        success = self.manager.download_episode(
            anime_title="Test Anime",
            episode_number=2,
            episode_link="https://vixcloud.co/embed/2",
            output_dir=self.output_dir,
        )

        self.assertTrue(success)
        cmd_called = mock_popen.call_args[0][0]
        self.assertEqual(cmd_called[0], "yt-dlp")
        self.assertIn("--no-playlist", cmd_called)
        self.assertIn("--external-downloader", cmd_called)


if __name__ == "__main__":
    unittest.main()
