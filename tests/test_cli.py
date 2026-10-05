"""Test del flusso principale di cli.main() con tutte le dipendenze simulate."""

import contextlib
import io
import itertools
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Iterable, Optional
from unittest.mock import MagicMock, patch

from ani_it import cli
from ani_it.config import Config
from ani_it.history import HistoryManager
from ani_it.player import PlaybackResult

ANIME = {"id": "500", "title": "Serie Test", "slug": "500-serie-test"}
STREAM = {"stream_url": "https://cdn.example.com/1.m3u8", "headers": {}, "format": "hls", "subtitles": []}


def make_episodes(numbers: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {"id": 1000 + i, "number": n, "title": f"Episodio {n}", "link": f"https://x/{n}", "created_at": ""}
        for i, n in enumerate(numbers)
    ]


def forever(actions: Iterable[str], then: str = "quit") -> Iterable[str]:
    """Sequenza di scelte dei menu che poi continua con `then`, così i loop terminano sempre."""
    return itertools.chain(actions, itertools.repeat(then))


class CliHarness(unittest.TestCase):
    """Esegue main() con scraper, resolver, player, ui e downloader sostituiti da mock."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.real_history = HistoryManager(custom_path=self.root / "history.json")

    def run_main(
        self,
        argv: Optional[list[str]] = None,
        episodes: Optional[list[dict[str, Any]]] = None,
        play_results: Optional[list[Any]] = None,
        play_error: Optional[Exception] = None,
        details: Optional[dict[str, Any]] = None,
        details_error: Optional[Exception] = None,
        resolve_effects: Optional[list[Any]] = None,
        post_watch: Iterable[str] = (),
        errors: Iterable[str] = (),
        post_download: Iterable[str] = (),
        select_episode: Any = None,
        download_ok: Any = True,
        config: Optional[Config] = None,
        local_exists: bool = True,
        search_error: Optional[Exception] = None,
        inputs: Optional[list[str]] = None,
    ) -> tuple[Optional[int], str, str]:
        episodes = episodes if episodes is not None else make_episodes([1])
        self.episodes = episodes
        self.history = MagicMock(wraps=self.real_history)
        self.ui = MagicMock()
        self.ui.select_anime.return_value = ANIME
        self.ui.select_episode.side_effect = select_episode if select_episode is not None else (lambda *a, **k: episodes[0])
        self.ui.post_watch_menu.side_effect = forever(post_watch)
        self.ui.error_menu.side_effect = forever(errors)
        self.ui.post_download_menu.side_effect = forever(post_download)

        self.scraper = MagicMock()
        self.scraper.search_anime.return_value = [ANIME]
        if search_error:
            self.scraper.search_anime.side_effect = search_error
        if details_error:
            self.scraper.get_anime_details.side_effect = details_error
        else:
            self.scraper.get_anime_details.return_value = details or {"episodes": episodes, "partial": False}

        self.resolver = MagicMock()
        self.resolver.resolve.side_effect = resolve_effects if resolve_effects is not None else itertools.repeat(STREAM)

        local_file = self.root / "Episodio_local.mp4"
        if local_exists:
            local_file.write_bytes(b"x")
        self.downloader = MagicMock()
        self.downloader.download_episode.side_effect = (
            download_ok if callable(download_ok) else (lambda *a, **k: download_ok)
        )
        self.downloader.episode_path.return_value = local_file
        self.local_file = local_file

        self.player = MagicMock()
        if play_error:
            self.player.play.side_effect = play_error
        else:
            results = play_results or [PlaybackResult(status="completed")]
            self.player.play.side_effect = itertools.chain(results, itertools.repeat(results[-1]))

        out, err = io.StringIO(), io.StringIO()
        code: Optional[int] = None
        self.config = config or Config()
        self.scraper_cls = MagicMock(return_value=self.scraper)
        self.resolver_cls = MagicMock(return_value=self.resolver)
        self.downloader_cls = MagicMock(return_value=self.downloader)
        with patch.multiple(
            "ani_it.cli",
            load_config=MagicMock(return_value=self.config),
            HistoryManager=MagicMock(return_value=self.history),
            FzfUI=MagicMock(return_value=self.ui),
            AnimeUnityScraper=self.scraper_cls,
            StreamResolver=self.resolver_cls,
            MpvController=MagicMock(return_value=self.player),
            DownloaderManager=self.downloader_cls,
            SignalHandler=MagicMock(),
        ), patch.object(sys, "argv", argv or ["ani-it", "serie"]), \
                patch("builtins.input", side_effect=inputs if inputs is not None else EOFError), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.main()
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return code, out.getvalue(), err.getvalue()

    def played_numbers(self) -> list[Any]:
        return [c.kwargs["episode_number"] for c in self.player.play.call_args_list]


class TestFindEpisodeIndex(unittest.TestCase):
    """-e N cerca prima per numero reale, solo poi per posizione."""

    def test_number_wins_over_position(self) -> None:
        """Con l'episodio 0, '-e 5' deve avviare l'episodio 5 (indice 5), non il quinto (indice 4)."""
        episodes = make_episodes([0, 1, 2, 3, 4, 5])
        self.assertEqual(cli.find_episode_index(episodes, "5"), 5)

    def test_episode_zero_is_found(self) -> None:
        self.assertEqual(cli.find_episode_index(make_episodes([0, 1, 2]), "0"), 0)

    def test_position_is_only_a_fallback(self) -> None:
        episodes = make_episodes([10, 11, 12, 13])
        self.assertEqual(cli.find_episode_index(episodes, "3"), 2)

    def test_decimal_numbers_are_matched(self) -> None:
        episodes = make_episodes([11, 12, 12.5, 13])
        self.assertEqual(cli.find_episode_index(episodes, "12.5"), 2)
        self.assertEqual(cli.find_episode_index(episodes, "12"), 1)

    def test_unknown_target_returns_none(self) -> None:
        episodes = make_episodes([1, 2, 3])
        self.assertIsNone(cli.find_episode_index(episodes, "99"))
        self.assertIsNone(cli.find_episode_index(episodes, "abc"))
        self.assertIsNone(cli.find_episode_index(episodes, "1.2.3"))

    def test_numeric_input_types(self) -> None:
        episodes = make_episodes([1, 2, 3])
        self.assertEqual(cli.find_episode_index(episodes, 2), 1)
        self.assertEqual(cli.find_episode_index(episodes, 2.0), 1)


class TestPlaybackOutcome(CliHarness):
    """Gli esiti di mpv devono riflettersi su cronologia e messaggi."""

    def test_completed_playback_updates_history(self) -> None:
        self.run_main(play_results=[PlaybackResult(status="completed")])
        self.history.update_progress.assert_called_once()

    def test_error_does_not_update_history_and_shows_message_in_error_menu(self) -> None:
        result = PlaybackResult(status="error", error_output="Failed to open https://cdn/1.m3u8")
        self.run_main(play_results=[result])
        self.history.update_progress.assert_not_called()
        self.ui.error_menu.assert_called_once()
        self.assertIn("Failed to open https://cdn/1.m3u8", self.ui.error_menu.call_args.args[1])
        self.ui.post_watch_menu.assert_not_called()

    def test_error_without_mpv_output_still_explains(self) -> None:
        self.run_main(play_results=[PlaybackResult(status="error")])
        self.history.update_progress.assert_not_called()
        self.assertIn("mpv", self.ui.error_menu.call_args.args[1])

    def test_missing_player_binary_exits_cleanly(self) -> None:
        code, out, err = self.run_main(play_error=FileNotFoundError("Il lettore video 'mpv' non è installato"))
        self.assertEqual(code, 1)
        self.assertIn("non è installato", out + err)
        self.history.update_progress.assert_not_called()


class TestProgressRecording(CliHarness):
    """Cosa viene scritto in cronologia dopo una visione."""

    def test_real_episode_number_is_saved(self) -> None:
        """12.5 non deve diventare 12."""
        self.run_main(episodes=make_episodes([12, 12.5, 13]), select_episode=lambda *a, **k: self.episodes[1])
        self.assertEqual(self.history.update_progress.call_args.kwargs["episode"], 12.5)

    def test_non_numeric_episode_number_does_not_crash(self) -> None:
        """Un numero come '1.2.3' mandava in crash float(): si ripiega sulla posizione."""
        self.run_main(episodes=make_episodes(["1.2.3"]))
        self.assertEqual(self.history.update_progress.call_args.kwargs["episode"], 1)

    def test_finished_middle_episode_is_not_series_completed(self) -> None:
        self.run_main(episodes=make_episodes([1, 2, 3]), select_episode=lambda *a, **k: self.episodes[0])
        kwargs = self.history.update_progress.call_args.kwargs
        self.assertTrue(kwargs["episode_completed"])
        self.assertFalse(kwargs["series_completed"])

    def test_finished_last_episode_is_series_completed(self) -> None:
        self.run_main(episodes=make_episodes([1, 2, 3]), select_episode=lambda *a, **k: self.episodes[2])
        self.assertTrue(self.history.update_progress.call_args.kwargs["series_completed"])

    def test_quitting_midway_on_last_episode_is_not_completed(self) -> None:
        """Uscire a metà dell'ultimo episodio non lo segna come finito."""
        result = PlaybackResult(status="quit", time_pos=300.0, duration=1400.0, percent_pos=21.0)
        self.run_main(episodes=make_episodes([1, 2, 3]), play_results=[result],
                      select_episode=lambda *a, **k: self.episodes[2])
        kwargs = self.history.update_progress.call_args.kwargs
        self.assertFalse(kwargs["episode_completed"])
        self.assertFalse(kwargs["series_completed"])
        self.assertEqual(kwargs["position"], 300.0)

    def test_negligible_position_is_not_saved(self) -> None:
        result = PlaybackResult(status="quit", time_pos=2.0, duration=1400.0, percent_pos=0.1)
        self.run_main(play_results=[result])
        self.assertEqual(self.history.update_progress.call_args.kwargs["position"], 0.0)


class TestResumePlayback(CliHarness):
    """--continue e ripresa della posizione."""

    def seed(self, episode: Any, completed: bool = False, series_done: bool = False,
             position: float = 0.0, total: int = 5) -> None:
        self.real_history.update_progress(
            "500", "Serie Test", "500-serie-test", episode=episode, total_episodes=total,
            episode_completed=completed, series_completed=series_done, position=position,
        )

    def test_continue_resumes_unfinished_episode_from_saved_position(self) -> None:
        self.seed(3, position=120.0)
        self.run_main(argv=["ani-it", "--continue"], episodes=make_episodes([1, 2, 3, 4, 5]))
        self.assertEqual(self.played_numbers()[0], 3)
        self.assertEqual(self.player.play.call_args_list[0].kwargs["start_time"], 120.0)

    def test_continue_after_finished_episode_plays_the_next_one(self) -> None:
        self.seed(3, completed=True)
        self.run_main(argv=["ani-it", "--continue"], episodes=make_episodes([1, 2, 3, 4, 5]))
        self.assertEqual(self.played_numbers()[0], 4)
        self.assertFalse(self.player.play.call_args_list[0].kwargs.get("start_time"))

    def test_continue_uses_real_numbers_when_numbering_is_offset(self) -> None:
        self.seed(14, completed=True)
        self.run_main(argv=["ani-it", "--continue"], episodes=make_episodes(range(13, 25)))
        self.assertEqual(self.played_numbers()[0], 15)

    def test_continue_skips_over_gaps_in_numbering(self) -> None:
        self.seed(2, completed=True)
        self.run_main(argv=["ani-it", "--continue"], episodes=make_episodes([1, 2, 4, 5]))
        self.assertEqual(self.played_numbers()[0], 4)

    def test_continue_on_finished_series_does_not_chase_a_missing_episode(self) -> None:
        """Serie terminata: niente 'episodio N+1 non trovato', ma un messaggio e la scelta episodi."""
        self.seed(5, completed=True, series_done=True)
        _, out, err = self.run_main(argv=["ani-it", "--continue"], episodes=make_episodes([1, 2, 3, 4, 5]))
        self.assertNotIn("non trovato", out + err)
        self.assertIn("completato", (out + err).lower())
        self.ui.select_episode.assert_called_once()

    def test_replay_restarts_from_the_beginning(self) -> None:
        self.seed(3, position=500.0)
        self.run_main(argv=["ani-it", "--continue"], episodes=make_episodes([1, 2, 3, 4, 5]),
                      play_results=[PlaybackResult(status="quit", time_pos=600.0)],
                      post_watch=["replay"])
        starts = [c.kwargs.get("start_time") for c in self.player.play.call_args_list]
        self.assertEqual(starts[0], 500.0)
        self.assertFalse(starts[1])

    def test_explicit_episode_resumes_only_if_it_is_the_unfinished_one(self) -> None:
        self.seed(3, position=200.0)
        self.run_main(argv=["ani-it", "serie", "-e", "2"], episodes=make_episodes([1, 2, 3, 4, 5]))
        self.assertFalse(self.player.play.call_args_list[0].kwargs.get("start_time"))

    def test_explicit_episode_by_real_number_with_episode_zero(self) -> None:
        self.run_main(argv=["ani-it", "serie", "-e", "5"], episodes=make_episodes([0, 1, 2, 3, 4, 5]))
        self.assertEqual(self.played_numbers()[0], 5)


class TestMenuFlows(CliHarness):
    """Menu di errore e post-visione: nessuna azione deve chiudere il programma per sbaglio."""

    def test_resolve_failure_opens_error_menu_with_the_reason(self) -> None:
        self.run_main(resolve_effects=[RuntimeError("HTTP 403")])
        self.ui.error_menu.assert_called_once()
        self.assertIn("HTTP 403", self.ui.error_menu.call_args.args[1])
        self.ui.post_watch_menu.assert_not_called()

    def test_retry_resolves_again_and_then_plays(self) -> None:
        self.run_main(resolve_effects=[RuntimeError("boom"), STREAM], errors=["retry"])
        self.assertEqual(self.resolver.resolve.call_count, 2)
        self.assertEqual(self.player.play.call_count, 1)

    def test_error_menu_download_action_downloads_instead_of_exiting(self) -> None:
        self.run_main(resolve_effects=[RuntimeError("boom")], errors=["download"])
        self.downloader.download_episode.assert_called_once()

    def test_error_menu_next_and_prev_navigate(self) -> None:
        self.run_main(episodes=make_episodes([1, 2, 3]), select_episode=lambda *a, **k: self.episodes[1],
                      resolve_effects=[RuntimeError("x"), RuntimeError("y"), STREAM, STREAM],
                      errors=["next", "prev"])
        # ep2 fallisce -> next -> ep3 fallisce -> prev -> ep2 ok
        self.assertEqual(self.played_numbers()[0], 2)

    def test_next_past_the_last_episode_warns_and_stays_in_the_menu(self) -> None:
        _, out, err = self.run_main(episodes=make_episodes([1]), post_watch=["next", "replay"])
        self.assertIn("ultimo episodio", out + err)
        self.assertEqual(self.player.play.call_count, 2)  # il 'replay' successivo è stato eseguito

    def test_prev_before_the_first_episode_warns_and_stays_in_the_menu(self) -> None:
        _, out, err = self.run_main(episodes=make_episodes([1]), post_watch=["prev", "replay"])
        self.assertIn("primo episodio", out + err)
        self.assertEqual(self.player.play.call_count, 2)

    def test_cancelling_episode_selection_returns_to_the_menu(self) -> None:
        """Annullare fzf dopo 's' non deve chiudere il programma."""
        eps = make_episodes([1, 2])
        self.run_main(episodes=eps, select_episode=[eps[0], None], post_watch=["select", "replay"])
        self.assertEqual(self.player.play.call_count, 2)  # dopo l'annullamento il 'replay' è stato eseguito

    def test_auto_next_advances_without_showing_the_menu(self) -> None:
        config = Config()
        config.general.auto_next = True
        self.run_main(episodes=make_episodes([1, 2, 3]), config=config)
        self.assertEqual(self.played_numbers(), [1, 2, 3])
        self.assertEqual(self.ui.post_watch_menu.call_count, 1)  # solo dopo l'ultimo episodio
        self.assertTrue(self.player.play.call_args.kwargs["quit_on_eof"])

    def test_without_auto_next_menu_follows_every_episode(self) -> None:
        self.run_main(episodes=make_episodes([1, 2, 3]), post_watch=["next", "next"])
        self.assertEqual(self.played_numbers(), [1, 2, 3])
        self.assertFalse(self.player.play.call_args.kwargs["quit_on_eof"])


class TestDownloadFlow(CliHarness):
    """Il flusso post-download è una macchina a stati, non un `continue` che riavvia lo streaming."""

    def test_failed_download_does_not_show_completed_menu(self) -> None:
        self.run_main(post_watch=["download"], download_ok=False)
        self.ui.post_download_menu.assert_not_called()
        self.ui.error_menu.assert_called_once()
        self.assertIn("download", self.ui.error_menu.call_args.args[1].lower())

    def test_successful_download_shows_completed_menu(self) -> None:
        self.run_main(post_watch=["download"], download_ok=True)
        self.ui.post_download_menu.assert_called_once()

    def test_playing_downloaded_file_does_not_restream(self) -> None:
        """Dopo 'riproduci file scaricato' non deve ripartire lo stesso episodio in streaming."""
        self.run_main(post_watch=["download"], post_download=["play"])
        self.assertEqual(self.resolver.resolve.call_count, 1)  # solo lo streaming iniziale
        self.assertEqual(self.player.play.call_count, 2)
        local_call = self.player.play.call_args_list[1]
        self.assertEqual(local_call.kwargs["stream_data"]["stream_url"], str(self.local_file))

    def test_playing_downloaded_file_updates_history(self) -> None:
        results = [PlaybackResult(status="quit", time_pos=10.0), PlaybackResult(status="completed")]
        self.run_main(play_results=results, post_watch=["download"], post_download=["play"])
        self.assertEqual(self.history.update_progress.call_count, 2)
        self.assertTrue(self.history.update_progress.call_args.kwargs["episode_completed"])

    def test_download_next_only_downloads_and_does_not_stream_it(self) -> None:
        self.run_main(episodes=make_episodes([1, 2, 3]), post_watch=["download"], post_download=["next"])
        downloaded = [c.args[1] for c in self.downloader.download_episode.call_args_list]
        self.assertEqual(downloaded, [1, 2])
        self.assertEqual(self.player.play.call_count, 1)  # nessuna riproduzione dell'episodio 2
        self.assertEqual(self.resolver.resolve.call_count, 1)

    def test_download_next_on_last_episode_warns_and_stays(self) -> None:
        _, out, err = self.run_main(episodes=make_episodes([1]), post_watch=["download"],
                                    post_download=["next", "quit"])
        self.assertIn("ultimo episodio", out + err)
        self.assertEqual(self.downloader.download_episode.call_count, 1)

    def test_missing_downloaded_file_is_reported_not_replaced_by_stream(self) -> None:
        self.run_main(post_watch=["download"], post_download=["play"], local_exists=False)
        self.assertEqual(self.player.play.call_count, 1)  # nessuna riproduzione locale né streaming extra
        self.ui.error_menu.assert_called_once()
        self.assertIn("non trovato", self.ui.error_menu.call_args.args[1])


class TestEpisodeLoading(CliHarness):
    """Gestione delle serie senza episodi e delle liste parziali."""

    def test_series_without_episodes_exits_with_clear_message(self) -> None:
        from ani_it.scraper import EpisodesNotFoundError

        code, out, err = self.run_main(details_error=EpisodesNotFoundError("nessun episodio"))
        self.assertEqual(code, 1)
        self.assertIn("Nessun episodio", out + err)
        self.player.play.assert_not_called()

    def test_partial_episode_list_warns_the_user(self) -> None:
        details = {"episodes": make_episodes([1, 2]), "partial": True, "partial_reason": "caduta di rete"}
        _, out, err = self.run_main(details=details)
        self.assertIn("incompleta", (out + err).lower())
        self.assertIn("caduta di rete", out + err)

    def test_complete_list_has_no_warning(self) -> None:
        _, out, err = self.run_main()
        self.assertNotIn("incompleta", (out + err).lower())


class TestHistoryListing(CliHarness):
    """--history: 'Completato' solo per le serie davvero finite."""

    def test_status_shows_completed_only_for_finished_series(self) -> None:
        self.real_history.update_progress("1", "Uno", "1-uno", episode=1, total_episodes=12, episode_completed=True)
        self.real_history.update_progress("2", "Due", "2-due", episode=12, total_episodes=12,
                                          episode_completed=True, series_completed=True)
        self.run_main(argv=["ani-it", "--history"])
        items = {i["id"]: i for i in self.ui.select_anime.call_args.args[0]}
        self.assertEqual(items["1"]["status"], "Ep. 1")
        self.assertEqual(items["2"]["status"], "Completato")


class TestStartupOrder(unittest.TestCase):
    """--version/--help non devono creare config, cronologia o cache: il parsing viene prima."""

    def run_cli(self, *argv: str) -> tuple[Optional[int], list[Path]]:
        import os

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {"HOME": str(root), "XDG_CONFIG_HOME": str(root / "config"),
                   "XDG_STATE_HOME": str(root / "state"), "XDG_CACHE_HOME": str(root / "cache")}
            code: Optional[int] = None
            with patch.dict(os.environ, env), patch.object(sys, "argv", ["ani-it", *argv]), \
                    patch("ani_it.cli.SignalHandler"), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    cli.main()
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 0
            return code, sorted(root.rglob("*"))

    def test_version_creates_nothing(self) -> None:
        code, files = self.run_cli("--version")
        self.assertEqual(code, 0)
        self.assertEqual(files, [])

    def test_help_creates_nothing(self) -> None:
        code, files = self.run_cli("--help")
        self.assertEqual(code, 0)
        self.assertEqual(files, [])

    def test_invalid_option_creates_nothing(self) -> None:
        code, files = self.run_cli("--non-esiste")
        self.assertEqual(code, 2)
        self.assertEqual(files, [])


class TestPreviewEntry(CliHarness):
    def test_preview_flag_is_lightweight(self) -> None:
        """--preview-anime non deve creare cronologia/UI né caricare l'intera applicazione."""
        with patch("ani_it.cli.render_preview") as render:
            code, _, _ = self.run_main(argv=["ani-it", "--preview-anime", "123"])
        self.assertEqual(code, 0)
        render.assert_called_once()
        self.assertEqual(render.call_args.args[0], "123")
        self.ui.select_anime.assert_not_called()


class TestConfiguredDomainWiring(CliHarness):
    def test_scraper_and_resolver_receive_the_configured_domain(self) -> None:
        config = Config()
        config.general.base_url = "https://www.animeunity.to"
        self.run_main(config=config)
        self.assertEqual(self.scraper_cls.call_args.kwargs["base_url"], "https://www.animeunity.to")
        self.assertEqual(self.resolver_cls.call_args.kwargs["base_url"], "https://www.animeunity.to")
        self.assertEqual(self.downloader_cls.call_args.args[0].general.base_url, "https://www.animeunity.to")

    def test_connection_error_message_shows_the_configured_domain(self) -> None:
        import requests

        config = Config()
        config.general.base_url = "https://www.animeunity.to"
        _, out, err = self.run_main(config=config, search_error=requests.exceptions.ConnectionError("down"))
        self.assertIn("https://www.animeunity.to", err)
        self.assertNotIn("animeunity.so", err)


class TestDownloadMode(CliHarness):
    """-d: i dettagli si scaricano una volta sola e la cartella usa lo stesso titolo del menu."""

    def test_details_are_fetched_once_and_passed_to_the_downloader(self) -> None:
        episodes = make_episodes([1, 2, 3])
        self.run_main(argv=["ani-it", "serie", "-d", "-e", "1-2"], episodes=episodes)
        self.assertEqual(self.scraper.get_anime_details.call_count, 1)
        call = self.downloader.download_range.call_args
        self.assertEqual(call.args[:3], ("500", "500-serie-test", "1-2"))
        self.assertEqual(call.kwargs["details"]["episodes"], episodes)

    def test_download_folder_uses_the_same_title_as_the_menu_download(self) -> None:
        self.run_main(argv=["ani-it", "serie", "-d", "-e", "all"], episodes=make_episodes([1, 2]))
        self.assertEqual(self.downloader.download_range.call_args.kwargs["anime_title"], "Serie Test")


if __name__ == "__main__":
    unittest.main()
