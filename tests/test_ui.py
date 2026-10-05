"""Test unitari per FzfUI (menu fzf, senza lanciare fzf reale)."""

import contextlib
import io
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

from ani_it.config import Config
from ani_it.history import HistoryManager
from ani_it.ui import FzfUI, display_width


class TestSelectEpisode(unittest.TestCase):
    """Verifica che il menu episodi non vada in crash e formatti i titoli correttamente."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)

        env = patch.dict(os.environ, {
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CONFIG_HOME": str(root / "config"),
        })
        env.start()
        self.addCleanup(env.stop)

        history = HistoryManager(custom_path=root / "history.json")
        self.ui = FzfUI(Config(), history)

    def _select(self, episodes: list[dict], fzf_stdout: str) -> tuple:
        fake = MagicMock(returncode=0, stdout=fzf_stdout)
        with patch("ani_it.ui.subprocess.run", return_value=fake) as run:
            selected = self.ui.select_episode("Serie Test", episodes, 1)
        return selected, run.call_args.kwargs["input"]

    def test_episode_with_real_title_does_not_crash(self) -> None:
        """Un titolo diverso da 'Episodio N' non deve sollevare NameError (re non importato)."""
        episodes = [
            {"id": 11, "number": 1, "title": "Il risveglio", "created_at": "2024-01-01", "link": "x"},
        ]
        selected, fzf_input = self._select(episodes, "riga\t2024-01-01\t11\n")
        self.assertEqual(selected["id"], 11)
        self.assertIn("Il risveglio", fzf_input)

    def test_placeholder_titles_are_collapsed(self) -> None:
        """I titoli segnaposto 'Episodio N' vengono sostituiti da '-'."""
        episodes = [
            {"id": 21, "number": 1, "title": "Episodio 1", "created_at": "", "link": "x"},
            {"id": 22, "number": 2, "title": "Episodio 2.5", "created_at": "", "link": "x"},
        ]
        _, fzf_input = self._select(episodes, "riga\t\t21\n")
        self.assertNotIn("Episodio 1 ", fzf_input)
        self.assertNotIn("Episodio 2.5", fzf_input)


ANSI = re.compile(r"\x1b\[[0-9;]*m")


class TestMenus(unittest.TestCase):
    """Menu post-visione, errore e post-download: voci disponibili e azioni restituite."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        env = patch.dict(os.environ, {
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CONFIG_HOME": str(root / "config"),
        })
        env.start()
        self.addCleanup(env.stop)
        self.ui = FzfUI(Config(), HistoryManager(custom_path=root / "history.json"))

    def ask(self, method: str, answers: list[str], *args, **kwargs) -> tuple[str, str]:
        out = io.StringIO()
        with patch("builtins.input", side_effect=answers), contextlib.redirect_stdout(out):
            result = getattr(self.ui, method)(*args, **kwargs)
        return result, out.getvalue()

    # --- post_watch_menu -----------------------------------------------------

    def test_post_watch_default_is_next_when_available(self) -> None:
        action, _ = self.ask("post_watch_menu", [""], 3, has_next=True, has_prev=True)
        self.assertEqual(action, "next")

    def test_post_watch_default_is_select_on_last_episode(self) -> None:
        action, _ = self.ask("post_watch_menu", [""], 12, has_next=False, has_prev=True)
        self.assertEqual(action, "select")

    def test_post_watch_hides_unavailable_entries(self) -> None:
        _, out = self.ask("post_watch_menu", ["q"], 1, has_next=False, has_prev=False)
        text = ANSI.sub("", out)
        self.assertNotIn("prossimo", text)
        self.assertNotIn("precedente", text)
        self.assertIn("Riavvia", text)

    def test_post_watch_shows_available_entries(self) -> None:
        _, out = self.ask("post_watch_menu", ["q"], 5, has_next=True, has_prev=True)
        text = ANSI.sub("", out)
        self.assertIn("prossimo", text)
        self.assertIn("precedente", text)

    def test_post_watch_unavailable_choice_is_asked_again(self) -> None:
        """'p' sul primo episodio non esiste: si richiede la scelta invece di indovinare."""
        action, _ = self.ask("post_watch_menu", ["p", "r"], 1, has_next=True, has_prev=False)
        self.assertEqual(action, "replay")

    def test_post_watch_unknown_input_is_asked_again(self) -> None:
        action, _ = self.ask("post_watch_menu", ["xyz", "d"], 2, has_next=True, has_prev=True)
        self.assertEqual(action, "download")

    def test_post_watch_eof_quits(self) -> None:
        with patch("builtins.input", side_effect=EOFError), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.ui.post_watch_menu(1, has_next=True, has_prev=False), "quit")

    def test_menu_box_lines_have_equal_visible_width(self) -> None:
        """Il box deve restare allineato anche con i codici colore ANSI."""
        for has_next, has_prev in [(True, True), (True, False), (False, True), (False, False)]:
            _, out = self.ask("post_watch_menu", ["q"], 1, has_next=has_next, has_prev=has_prev)
            box = [ANSI.sub("", row) for row in out.splitlines() if row.lstrip().startswith(("╭", "│", "╰"))]
            self.assertGreater(len(box), 3)
            self.assertEqual(len({len(row) for row in box}), 1, (has_next, has_prev, [len(row) for row in box]))

    # --- error_menu ----------------------------------------------------------

    def test_error_menu_shows_message_and_does_not_clear_screen(self) -> None:
        """Il messaggio d'errore deve restare leggibile: niente clear_screen."""
        _, out = self.ask("error_menu", ["q"], 4, "Impossibile risolvere lo stream: 403",
                          has_next=True, has_prev=True)
        self.assertIn("Impossibile risolvere lo stream: 403", ANSI.sub("", out))
        self.assertNotIn("\033[2J", out)
        self.assertNotIn("terminata", out)  # non è una "riproduzione terminata"

    def test_error_menu_actions(self) -> None:
        cases = {"": "retry", "r": "retry", "n": "next", "p": "prev", "s": "select", "d": "download", "q": "quit"}
        for key, expected in cases.items():
            with self.subTest(key=key):
                action, _ = self.ask("error_menu", [key], 4, "errore", has_next=True, has_prev=True)
                self.assertEqual(action, expected)

    def test_error_menu_respects_limits(self) -> None:
        action, out = self.ask("error_menu", ["n", "p", "r"], 1, "errore", has_next=False, has_prev=False)
        self.assertEqual(action, "retry")  # 'n' e 'p' non disponibili: richiesti di nuovo
        self.assertNotIn("prossimo", ANSI.sub("", out))

    # --- post_download_menu --------------------------------------------------

    def test_post_download_default_is_play(self) -> None:
        action, _ = self.ask("post_download_menu", [""], 2, has_next=True, has_prev=True)
        self.assertEqual(action, "play")

    def test_post_download_hides_next_on_last_episode(self) -> None:
        _, out = self.ask("post_download_menu", ["q"], 12, has_next=False, has_prev=True)
        self.assertNotIn("prossimo", ANSI.sub("", out))
        action, _ = self.ask("post_download_menu", ["n", "s"], 12, has_next=False, has_prev=True)
        self.assertEqual(action, "select")


class TestLanguageTag(unittest.TestCase):
    """Il tag [ITA]/[SUB] viene dal campo `dub` dell'API, non da una sottostringa dello slug."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        env = patch.dict(os.environ, {"XDG_CACHE_HOME": str(root / "c"), "XDG_STATE_HOME": str(root / "s"),
                                      "XDG_CONFIG_HOME": str(root / "cfg")})
        env.start()
        self.addCleanup(env.stop)
        self.ui = FzfUI(Config(), HistoryManager(custom_path=root / "history.json"))

    def lines_for(self, items: list[dict]) -> list[str]:
        with patch("ani_it.ui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")) as run:
            self.ui.select_anime(items)
        return [ANSI.sub("", line) for line in run.call_args.kwargs["input"].splitlines()]

    @staticmethod
    def item(aid: int, title: str, slug: str, **extra) -> dict:
        return {"id": str(aid), "title": title, "slug": slug, "type": "TV", "year": "2020",
                "episodes_count": "12", **extra}

    def test_dub_flag_decides_the_tag(self) -> None:
        lines = self.lines_for([
            self.item(1, "Naruto", "naruto-ita", dub=True),
            self.item(2, "Naruto", "naruto", dub=False),
        ])
        self.assertIn("[ITA]", lines[0])
        self.assertIn("[SUB]", lines[1])

    def test_slug_substring_does_not_make_a_series_italian(self) -> None:
        lines = self.lines_for([
            self.item(1, "Hospitality", "hospitalita", dub=False),
            self.item(2, "Altro", "altro", dub=False),
        ])
        self.assertIn("[SUB]", lines[0])

    def test_dubbed_series_without_ita_in_slug_is_tagged_italian(self) -> None:
        lines = self.lines_for([
            self.item(1, "Titolo Doppiato", "titolo-doppiato", dub=True),
            self.item(2, "Altro", "altro", dub=False),
        ])
        self.assertIn("[ITA]", lines[0])

    def test_history_items_without_dub_field_fall_back_to_slug_suffix(self) -> None:
        lines = self.lines_for([
            self.item(1, "Vecchio", "vecchio-ita"),
            self.item(2, "Altro", "altro"),
        ])
        self.assertIn("[ITA]", lines[0])
        self.assertIn("[SUB]", lines[1])


class TestEpisodeBadges(unittest.TestCase):
    """Un episodio già finito è 'VISTO', uno interrotto è 'IN CORSO'."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        env = patch.dict(os.environ, {"XDG_CACHE_HOME": str(root / "c"), "XDG_STATE_HOME": str(root / "s"),
                                      "XDG_CONFIG_HOME": str(root / "cfg")})
        env.start()
        self.addCleanup(env.stop)
        self.history = HistoryManager(custom_path=root / "history.json")
        self.ui = FzfUI(Config(), self.history)
        self.episodes = [{"id": n, "number": n, "title": "", "created_at": "", "link": "x"} for n in (1, 2, 3)]

    def fzf_input(self) -> str:
        with patch("ani_it.ui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")) as run:
            self.ui.select_episode("Serie", self.episodes, 77)
        return ANSI.sub("", run.call_args.kwargs["input"])

    def test_finished_last_episode_is_seen_not_in_progress(self) -> None:
        self.history.update_progress(77, "Serie", "77-s", episode=2, episode_completed=True)
        lines = self.fzf_input().splitlines()
        self.assertIn("VISTO", lines[0])
        self.assertIn("VISTO", lines[1])
        self.assertIn("NON VISTO", lines[2])

    def test_interrupted_episode_is_in_progress(self) -> None:
        self.history.update_progress(77, "Serie", "77-s", episode=2, episode_completed=False, position=100)
        lines = self.fzf_input().splitlines()
        self.assertIn("VISTO", lines[0])
        self.assertIn("IN CORSO", lines[1])


class UiTestCase(unittest.TestCase):
    """Base con directory XDG isolate e una FzfUI pronta."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        env = patch.dict(os.environ, {
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
        })
        env.start()
        self.addCleanup(env.stop)
        self.ui = FzfUI(Config(), HistoryManager(custom_path=self.root / "history.json"))

    def fzf_call(self, items: list[dict]) -> tuple[list[str], dict]:
        with patch("ani_it.ui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")) as run:
            self.ui.select_anime(items)
        return run.call_args.kwargs["input"].splitlines(), run.call_args


def anime(aid: Any, title: str, **extra: Any) -> dict:
    return {"id": aid, "title": title, "slug": "x", "type": "TV", "year": "2020", "episodes_count": "12", **extra}


class TestFzfInvocation(UiTestCase):
    """fzf disegna l'interfaccia su stderr in alcune versioni: non va catturato."""

    def test_anime_menu_captures_only_stdout(self) -> None:
        _, call = self.fzf_call([anime(1, "A"), anime(2, "B")])
        self.assertNotIn("capture_output", call.kwargs)
        self.assertEqual(call.kwargs.get("stdout"), subprocess.PIPE)
        self.assertNotEqual(call.kwargs.get("stderr"), subprocess.PIPE)

    def test_episode_menu_captures_only_stdout(self) -> None:
        episodes = [{"id": 1, "number": 1, "title": "", "created_at": "", "link": "x"}]
        with patch("ani_it.ui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")) as run:
            self.ui.select_episode("Serie", episodes, 5)
        self.assertNotIn("capture_output", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs.get("stdout"), subprocess.PIPE)
        self.assertNotEqual(run.call_args.kwargs.get("stderr"), subprocess.PIPE)


class TestColumnLayout(UiTestCase):
    """Troncamento e allineamento si calcolano sul testo visibile, prima della colorazione."""

    def title_field(self, line: str) -> str:
        return line.split("\t")[1]

    def test_title_column_has_constant_visible_width(self) -> None:
        lines, _ = self.fzf_call([
            anime(1, "Corto"),
            anime(2, "Un titolo molto molto molto lungo " * 4, title_eng="An extremely long english title " * 3),
            anime(3, "Medio con inglese", title_eng="Medium english"),
        ])
        widths = {display_width(ANSI.sub("", self.title_field(line))) for line in lines}
        self.assertEqual(widths, {65})

    def test_truncation_never_cuts_an_ansi_sequence(self) -> None:
        lines, _ = self.fzf_call([
            anime(1, "Titolo " * 20, title_eng="Original title " * 10, dub=False),
            anime(2, "Altro " * 20, title_eng="Orig " * 10, dub=True),
        ])
        for line in lines:
            field = self.title_field(line)
            self.assertNotIn("\x1b", ANSI.sub("", field), "sequenza ANSI troncata")
            # ogni apertura di colore deve essere chiusa da un reset prima della fine del campo
            self.assertTrue(field.rstrip().endswith("\x1b[0m") or "\x1b[" not in field.rsplit("\x1b[0m", 1)[-1])

    def test_long_titles_are_marked_as_truncated(self) -> None:
        lines, _ = self.fzf_call([anime(1, "X" * 200), anime(2, "Y")])
        self.assertIn("…", ANSI.sub("", self.title_field(lines[0])))

    def test_wide_characters_count_double(self) -> None:
        lines, _ = self.fzf_call([anime(1, "進撃の巨人 " * 15), anime(2, "Attack on Titan " * 8)])
        widths = {display_width(ANSI.sub("", self.title_field(line))) for line in lines}
        self.assertEqual(widths, {65})

    def test_display_width_helper(self) -> None:
        self.assertEqual(display_width("abc"), 3)
        self.assertEqual(display_width("日本"), 4)
        self.assertEqual(display_width("e\u0301"), 1)  # accento combinante: larghezza 0


class TestPreviewCommandAndCache(UiTestCase):
    def preview_arg(self) -> str:
        _, call = self.fzf_call([anime(1, "A"), anime(2, "B")])
        return next(a for a in call.args[0] if a.startswith("--preview="))[len("--preview="):]

    def test_preview_command_quotes_the_python_path(self) -> None:
        with patch("ani_it.ui.sys.executable", "/opt/my python/bin/python3"):
            command = self.preview_arg()
        parts = shlex.split(command)
        self.assertEqual(parts[0], "/opt/my python/bin/python3")
        self.assertEqual(parts[-1], "{5}")

    def test_preview_uses_the_lightweight_module(self) -> None:
        """Ogni preview è un nuovo processo: non deve importare tutta l'applicazione."""
        parts = shlex.split(self.preview_arg())
        self.assertEqual(parts[1:3], ["-m", "ani_it.preview"])

    def test_only_numeric_ids_are_used_as_cache_file_names(self) -> None:
        evil = anime("../../evil", "Cattivo")
        self.fzf_call([evil, anime(7, "Buono")])
        cache = self.ui.data_cache_dir
        self.assertTrue((cache / "7.json").is_file())
        self.assertFalse((cache.parent.parent / "evil.json").exists())
        self.assertEqual([p.name for p in cache.iterdir()], ["7.json"])

    def test_preview_rejects_non_numeric_ids(self) -> None:
        (self.ui.data_cache_dir.parent / "secret.json").write_text('{"title": "SEGRETO"}', encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.ui.render_anime_preview("../secret")
        self.assertNotIn("SEGRETO", out.getvalue())


class TestDependencyCheck(UiTestCase):
    """Il controllo deve usare i binari realmente configurati e segnalare yt-dlp."""

    def verify(self, installed: set[str]) -> tuple[Optional[int], str]:
        err = io.StringIO()
        code: Optional[int] = None
        with patch("ani_it.utils.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name in installed else None), \
                contextlib.redirect_stderr(err):
            try:
                self.ui.verify_system_requirements()
            except SystemExit as exc:
                code = exc.code
        return code, err.getvalue()

    def test_configured_player_is_checked_not_a_fixed_mpv(self) -> None:
        self.ui.config.player.binary = "vlc"
        code, err = self.verify({"fzf", "mpv", "yt-dlp"})
        self.assertEqual(code, 1)
        self.assertIn("vlc", err)

    def test_custom_player_binary_passes_when_installed(self) -> None:
        self.ui.config.player.binary = "mpv-custom"
        code, _ = self.verify({"fzf", "mpv-custom", "yt-dlp"})
        self.assertIsNone(code)

    def test_missing_fzf_is_fatal(self) -> None:
        code, err = self.verify({"mpv", "yt-dlp"})
        self.assertEqual(code, 1)
        self.assertIn("fzf", err)

    def test_missing_ytdlp_warns_but_does_not_block_streaming(self) -> None:
        code, err = self.verify({"fzf", "mpv"})
        self.assertIsNone(code)
        self.assertIn("yt-dlp", err)

    def test_all_present_is_silent(self) -> None:
        code, err = self.verify({"fzf", "mpv", "yt-dlp"})
        self.assertIsNone(code)
        self.assertEqual(err, "")

    def test_pacman_hint_only_for_known_packages(self) -> None:
        self.ui.config.player.binary = "vlc"
        _, err = self.verify({"fzf", "yt-dlp"})
        self.assertIn("player.binary", err)


if __name__ == "__main__":
    unittest.main()
