"""Test unitari per DownloaderManager (Dual-Engine: aria2c e yt-dlp)."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ani_it.config import Config
from ani_it.downloader import DownloaderManager


class TestDownloaderManager(unittest.TestCase):
    """Verifica il comportamento del motore di download con aria2c e yt-dlp."""

    def setUp(self) -> None:
        # I messaggi di avanzamento del downloader non devono sporcare l'output della suite
        quiet = contextlib.ExitStack()
        quiet.enter_context(contextlib.redirect_stdout(io.StringIO()))
        quiet.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.addCleanup(quiet.close)

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


MB = 1024 * 1024


def make_sparse(path: Path, size: int) -> None:
    """Crea un file della dimensione indicata senza scrivere davvero i byte."""
    with open(path, "wb") as f:
        f.truncate(size)


class TestDownloadRobustness(unittest.TestCase):
    """Binari mancanti e download interrotti (aria2c prealloca il file finale)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output_dir = Path(self.temp_dir.name)
        self.mock_resolver = MagicMock()
        self.manager = DownloaderManager(
            config=Config(),
            scraper=MagicMock(),
            resolver=self.mock_resolver,
        )
        self.target = self.output_dir / "Episodio_1.mp4"
        self.control = self.output_dir / "Episodio_1.mp4.aria2"
        self.set_stream("https://vix-content.net/video.mp4", "mp4")

    def set_stream(self, url: str, fmt: str) -> None:
        self.mock_resolver.resolve.return_value = {
            "stream_url": url,
            "format": fmt,
            "headers": {"User-Agent": "TestUA", "Referer": "https://vixcloud.co"},
        }

    def download(self) -> bool:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return self.manager.download_episode(
                anime_title="Test Anime",
                episode_number=1,
                episode_link="https://vixcloud.co/embed/1",
                output_dir=self.output_dir,
            )

    # --- binari mancanti (punto 2) ----------------------------------------

    @patch("ani_it.downloader.check_binary")
    @patch("ani_it.downloader.subprocess.Popen")
    def test_missing_aria2c_binary_does_not_crash(self, mock_popen: MagicMock, mock_check: MagicMock) -> None:
        """Popen -> FileNotFoundError non deve diventare UnboundLocalError nel finally."""
        mock_check.return_value = True
        mock_popen.side_effect = FileNotFoundError(2, "No such file", "aria2c")
        self.assertFalse(self.download())

    @patch("ani_it.downloader.check_binary")
    @patch("ani_it.downloader.subprocess.Popen")
    def test_missing_ytdlp_is_reported_before_launching(self, mock_popen: MagicMock, mock_check: MagicMock) -> None:
        """Senza yt-dlp i flussi HLS falliscono subito con un messaggio, senza avviare nulla."""
        mock_check.side_effect = lambda name: name != "yt-dlp"
        self.set_stream("https://vix-content.net/master.m3u8", "hls")

        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            ok = self.manager.download_episode("Test Anime", 1, "https://vixcloud.co/embed/1", self.output_dir)

        self.assertFalse(ok)
        mock_popen.assert_not_called()
        self.assertIn("yt-dlp", err.getvalue())

    @patch("ani_it.downloader.check_binary")
    @patch("ani_it.downloader.subprocess.Popen")
    def test_missing_ytdlp_blocks_mp4_when_aria2c_also_missing(self, mock_popen: MagicMock, mock_check: MagicMock) -> None:
        mock_check.return_value = False
        self.assertFalse(self.download())
        mock_popen.assert_not_called()

    # --- download interrotti (punto 10) -----------------------------------

    @patch("ani_it.downloader.check_binary", return_value=True)
    @patch("ani_it.downloader.subprocess.Popen")
    def test_complete_file_is_skipped(self, mock_popen: MagicMock, _check: MagicMock) -> None:
        make_sparse(self.target, 2 * MB)
        self.assertTrue(self.download())
        mock_popen.assert_not_called()

    @patch("ani_it.downloader.check_binary", return_value=True)
    @patch("ani_it.downloader.subprocess.Popen")
    def test_file_with_aria2_control_file_is_not_complete(self, mock_popen: MagicMock, _check: MagicMock) -> None:
        """Un file grande ma con il suo .aria2 accanto è un download interrotto: va ripreso."""
        make_sparse(self.target, 2 * MB)
        self.control.write_bytes(b"control")
        mock_popen.return_value.wait.return_value = 0

        self.assertTrue(self.download())
        mock_popen.assert_called_once()
        self.assertIn("-c", mock_popen.call_args[0][0])

    @patch("ani_it.downloader.check_binary", return_value=True)
    @patch("ani_it.downloader.subprocess.Popen")
    def test_failed_download_keeps_control_file_and_partial_data(self, mock_popen: MagicMock, _check: MagicMock) -> None:
        """Dopo un errore .aria2 e file parziale restano, così '-c' può riprendere."""
        make_sparse(self.target, 2 * MB)
        self.control.write_bytes(b"control")
        mock_popen.return_value.wait.return_value = 1

        self.assertFalse(self.download())
        self.assertTrue(self.control.exists())
        self.assertTrue(self.target.exists())

    @patch("ani_it.downloader.check_binary", return_value=True)
    @patch("ani_it.downloader.subprocess.Popen")
    def test_interrupted_download_keeps_control_file(self, mock_popen: MagicMock, _check: MagicMock) -> None:
        make_sparse(self.target, 2 * MB)
        self.control.write_bytes(b"control")
        mock_popen.return_value.wait.side_effect = KeyboardInterrupt

        self.assertFalse(self.download())
        self.assertTrue(self.control.exists())
        self.assertTrue(self.target.exists())


class TestDownloadCommands(unittest.TestCase):
    """Comandi costruiti per aria2c e yt-dlp: TLS, argomenti da config, qualità."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output_dir = Path(self.temp_dir.name)
        self.config = Config()
        self.resolver = MagicMock()
        self.manager = DownloaderManager(config=self.config, scraper=MagicMock(), resolver=self.resolver)

    def run_download(self, url: str, fmt: str, aria2: bool = True) -> list[str]:
        self.resolver.resolve.return_value = {
            "stream_url": url, "format": fmt,
            "headers": {"User-Agent": "TestUA", "Referer": "https://vixcloud.co"},
        }
        installed = lambda name: name == "yt-dlp" or (name == "aria2c" and aria2)  # noqa: E731
        with patch("ani_it.downloader.check_binary", side_effect=installed), \
                patch("ani_it.downloader.subprocess.Popen") as popen, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            popen.return_value.wait.return_value = 1
            self.manager.download_episode("Serie", 1, "link", self.output_dir)
        return popen.call_args[0][0]

    def test_aria2c_keeps_tls_verification_on(self) -> None:
        cmd = self.run_download("https://cdn.example.com/v.mp4", "mp4")
        self.assertEqual(cmd[0], "aria2c")
        self.assertFalse([a for a in cmd if "check-certificate" in a], cmd)

    def test_ytdlp_keeps_tls_verification_on(self) -> None:
        cmd = self.run_download("https://cdn.example.com/master.m3u8", "hls")
        self.assertEqual(cmd[0], "yt-dlp")
        self.assertNotIn("--no-check-certificates", cmd)

    def test_aria2c_arguments_come_from_config(self) -> None:
        self.config.downloader.args = ["-x", "4", "-s", "4", "-k", "2M"]
        cmd = self.run_download("https://cdn.example.com/v.mp4", "mp4")
        self.assertIn("-c", cmd)
        i = cmd.index("-x")
        self.assertEqual(cmd[i:i + 2], ["-x", "4"])
        self.assertNotIn("16", cmd)

    def test_ytdlp_external_downloader_args_come_from_config(self) -> None:
        self.config.downloader.args = ["-x", "4", "-s", "4"]
        cmd = self.run_download("https://cdn.example.com/master.m3u8", "hls")
        i = cmd.index("--external-downloader-args")
        self.assertEqual(cmd[i + 1], "aria2c:-c -x 4 -s 4")

    def test_hls_quality_is_passed_to_ytdlp(self) -> None:
        self.config.general.quality = "720p"
        cmd = self.run_download("https://cdn.example.com/master.m3u8", "hls")
        i = cmd.index("-f")
        self.assertEqual(cmd[i + 1], "bv*[height<=720]+ba/b[height<=720]")
        self.assertIn("--merge-output-format", cmd)

    def test_best_quality_leaves_ytdlp_default_format(self) -> None:
        self.config.general.quality = "best"
        cmd = self.run_download("https://cdn.example.com/master.m3u8", "hls")
        self.assertNotIn("-f", cmd)

    def test_quality_filter_is_not_applied_to_plain_mp4(self) -> None:
        """Un MP4 diretto servito da yt-dlp non ha varianti: un filtro di altezza lo farebbe fallire."""
        self.config.general.quality = "720p"
        cmd = self.run_download("https://cdn.example.com/v.mp4", "mp4", aria2=False)
        self.assertEqual(cmd[0], "yt-dlp")
        self.assertNotIn("-f", cmd)

    def test_episode_path_matches_download_target(self) -> None:
        path = self.manager.episode_path("Serie: Test?", 12.5, output_dir=self.output_dir)
        self.assertEqual(path, self.output_dir / "Episodio_12.5.mp4")
        default = self.manager.episode_path("Serie: Test?", 3)
        self.assertEqual(default, self.manager.download_base / "Serie_ Test_" / "Episodio_3.mp4")


class TestDownloadRange(unittest.TestCase):
    """download_range deve ragionare sui numeri reali degli episodi."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.scraper = MagicMock()
        self.manager = DownloaderManager(config=Config(), scraper=self.scraper, resolver=MagicMock())
        self.manager.download_base = Path(self.temp_dir.name)
        self.manager.download_episode = MagicMock(return_value=True)  # type: ignore[method-assign]

    def run_range(self, numbers: list, range_str: str) -> str:
        episodes = [
            {"id": 1000 + i, "number": n, "title": "", "link": f"link-{n}"}
            for i, n in enumerate(numbers)
        ]
        self.scraper.get_anime_details.return_value = {"title": "Serie", "episodes": episodes}
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.manager.download_range("500", "500-serie", range_str)
        return out.getvalue()

    def downloaded(self) -> list:
        return [c.kwargs["episode_number"] for c in self.manager.download_episode.call_args_list]

    def links(self) -> list:
        return [c.kwargs["episode_link"] for c in self.manager.download_episode.call_args_list]

    def test_decimal_episode_does_not_overwrite_integer_one(self) -> None:
        """12.5 non deve sostituire il 12: '-e 12' scarica il link del 12."""
        self.run_range([11, 12, 12.5, 13], "12")
        self.assertEqual(self.downloaded(), [12])
        self.assertEqual(self.links(), ["link-12"])

    def test_decimal_episode_can_be_downloaded_explicitly(self) -> None:
        self.run_range([11, 12, 12.5, 13], "12.5")
        self.assertEqual(self.links(), ["link-12.5"])

    def test_series_numbered_from_13_accepts_its_real_range(self) -> None:
        self.run_range(list(range(13, 25)), "13-24")
        self.assertEqual(self.downloaded(), list(range(13, 25)))

    def test_all_includes_episode_zero_and_specials(self) -> None:
        self.run_range([0, 1, 2, 2.5], "all")
        self.assertEqual(self.downloaded(), [0, 1, 2, 2.5])

    def test_missing_episodes_are_reported(self) -> None:
        output = self.run_range([1, 2, 4], "1-4")
        self.assertEqual(self.downloaded(), [1, 2, 4])
        self.assertIn("3", output)
        self.assertRegex(output.lower(), r"non (presenti|trovat)")

    def test_requested_numbers_not_present_are_reported_not_silent(self) -> None:
        output = self.run_range([1, 2, 3], "2,9")
        self.assertEqual(self.downloaded(), [2])
        self.assertIn("9", output)

    def test_invalid_range_text_is_an_error_not_a_crash(self) -> None:
        output = self.run_range([1, 2, 3], "abc")
        self.manager.download_episode.assert_not_called()
        self.assertIn("abc", output)

    def test_range_outside_numbering_tells_available_numbers(self) -> None:
        output = self.run_range([13, 14, 15], "1-5")
        self.manager.download_episode.assert_not_called()
        self.assertIn("13", output)

    def test_details_passed_in_are_not_fetched_again(self) -> None:
        details = {"title": "Titolo HTML", "episodes": [{"id": 1, "number": 1, "link": "l1"}]}
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.manager.download_range("500", "500-serie", "1", details=details, anime_title="Titolo Ricerca")
        self.scraper.get_anime_details.assert_not_called()
        self.assertEqual(self.manager.download_episode.call_args.kwargs["anime_title"], "Titolo Ricerca")

    def test_folder_uses_given_title_not_the_html_one(self) -> None:
        details = {"title": "Titolo HTML", "episodes": [{"id": 1, "number": 1, "link": "l1"}]}
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.manager.download_range("500", "500-serie", "1", details=details, anime_title="Titolo Ricerca")
        folder = self.manager.download_episode.call_args.kwargs["output_dir"]
        self.assertEqual(folder.name, "Titolo Ricerca")

    def test_title_falls_back_to_details_when_not_given(self) -> None:
        self.run_range([1], "1")
        folder = self.manager.download_episode.call_args.kwargs["output_dir"]
        self.assertEqual(folder.name, "Serie")

    def test_manager_builds_scraper_and_resolver_with_configured_domain(self) -> None:
        config = Config()
        config.general.base_url = "https://www.animeunity.to"
        manager = DownloaderManager(config=config)
        self.assertEqual(manager.scraper.base_url, "https://www.animeunity.to")
        self.assertEqual(manager.resolver.base_url, "https://www.animeunity.to")

    def test_no_episodes_error_is_handled(self) -> None:
        from ani_it.scraper import EpisodesNotFoundError

        self.scraper.get_anime_details.side_effect = EpisodesNotFoundError("nessuno")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.manager.download_range("500", "500-serie", "all")
        self.manager.download_episode.assert_not_called()
        self.assertIn("Nessun episodio", out.getvalue())


if __name__ == "__main__":
    unittest.main()
