"""Test unitari per le funzioni di utilità (numerazione e intervalli di episodi)."""

import unittest

from ani_it.utils import normalize_episode_number, parse_episode_range


class TestNormalizeEpisodeNumber(unittest.TestCase):
    """Il numero reale dell'episodio deve essere preservato (niente int(float(x)))."""

    def test_integers_and_integral_floats(self) -> None:
        self.assertEqual(normalize_episode_number(12), 12)
        self.assertEqual(normalize_episode_number("12"), 12)
        self.assertEqual(normalize_episode_number(12.0), 12)
        self.assertIsInstance(normalize_episode_number(12.0), int)

    def test_decimal_numbers_are_preserved(self) -> None:
        self.assertEqual(normalize_episode_number("12.5"), 12.5)
        self.assertEqual(normalize_episode_number(12.5), 12.5)

    def test_episode_zero_is_valid(self) -> None:
        self.assertEqual(normalize_episode_number(0), 0)
        self.assertEqual(normalize_episode_number("0"), 0)

    def test_invalid_values_return_none(self) -> None:
        for bad in (None, "", "abc", "1.2.3", "nan", "inf", "-inf"):
            with self.subTest(value=bad):
                self.assertIsNone(normalize_episode_number(bad))


class TestParseEpisodeRange(unittest.TestCase):
    """Gli intervalli vanno validati contro i numeri realmente presenti, non contro il conteggio."""

    AVAILABLE = [0, 1, 2, 3, 12, 12.5, 13]

    def test_all_returns_every_available_episode(self) -> None:
        selected, missing = parse_episode_range("all", self.AVAILABLE)
        self.assertEqual(selected, [0, 1, 2, 3, 12, 12.5, 13])
        self.assertEqual(missing, [])

    def test_single_number_does_not_match_decimal_episode(self) -> None:
        selected, _ = parse_episode_range("12", self.AVAILABLE)
        self.assertEqual(selected, [12])
        selected, _ = parse_episode_range("12.5", self.AVAILABLE)
        self.assertEqual(selected, [12.5])

    def test_episode_zero_is_selectable(self) -> None:
        selected, missing = parse_episode_range("0", self.AVAILABLE)
        self.assertEqual(selected, [0])
        self.assertEqual(missing, [])

    def test_range_includes_decimal_episodes_inside_it(self) -> None:
        selected, _ = parse_episode_range("12-13", self.AVAILABLE)
        self.assertEqual(selected, [12, 12.5, 13])

    def test_range_on_series_not_starting_at_one(self) -> None:
        """Una serie numerata 13-24 (12 episodi) deve accettare '13-24'."""
        selected, missing = parse_episode_range("13-24", list(range(13, 25)))
        self.assertEqual(selected, list(range(13, 25)))
        self.assertEqual(missing, [])

    def test_list_reports_missing_numbers(self) -> None:
        selected, missing = parse_episode_range("1,5,13", self.AVAILABLE)
        self.assertEqual(selected, [1, 13])
        self.assertEqual(missing, [5])

    def test_range_reports_holes_in_numbering(self) -> None:
        selected, missing = parse_episode_range("1-5", [1, 2, 4, 5])
        self.assertEqual(selected, [1, 2, 4, 5])
        self.assertEqual(missing, [3])

    def test_range_beyond_the_end_is_not_reported_as_missing(self) -> None:
        selected, missing = parse_episode_range("1-100", [1, 2, 3])
        self.assertEqual(selected, [1, 2, 3])
        self.assertEqual(missing, [])

    def test_range_entirely_outside_selects_nothing(self) -> None:
        selected, _ = parse_episode_range("50-60", [1, 2, 3])
        self.assertEqual(selected, [])

    def test_reversed_range_is_normalised(self) -> None:
        selected, _ = parse_episode_range("3-1", self.AVAILABLE)
        self.assertEqual(selected, [1, 2, 3])

    def test_duplicates_are_collapsed(self) -> None:
        selected, _ = parse_episode_range("1,1,1-2", self.AVAILABLE)
        self.assertEqual(selected, [1, 2])

    def test_malformed_token_raises(self) -> None:
        for bad in ("abc", "1,abc", "1-", "-3", "1..2", "nan"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    parse_episode_range(bad, self.AVAILABLE)


class TestSignalHandlerCallbacks(unittest.TestCase):
    """Le callback di pulizia non si duplicano e si possono rimuovere."""

    def setUp(self) -> None:
        from ani_it.utils import SignalHandler

        self.handler = SignalHandler
        saved = list(SignalHandler._cleanup_callbacks)
        SignalHandler._cleanup_callbacks.clear()
        self.addCleanup(lambda: (SignalHandler._cleanup_callbacks.clear(), SignalHandler._cleanup_callbacks.extend(saved)))

    def test_registering_twice_keeps_one_entry(self) -> None:
        def callback() -> None:
            pass

        self.handler.register_cleanup(callback)
        self.handler.register_cleanup(callback)
        self.assertEqual(self.handler._cleanup_callbacks, [callback])

    def test_unregister_removes_the_callback(self) -> None:
        def callback() -> None:
            pass

        self.handler.register_cleanup(callback)
        self.handler.unregister_cleanup(callback)
        self.assertEqual(self.handler._cleanup_callbacks, [])

    def test_unregister_unknown_callback_is_harmless(self) -> None:
        self.handler.unregister_cleanup(lambda: None)


class TestSanitizeFilename(unittest.TestCase):
    """I titoli arrivano dall'API del sito: non devono poter uscire dalla cartella dei download."""

    def test_dot_names_cannot_traverse_directories(self) -> None:
        from ani_it.utils import sanitize_filename

        for hostile in ("..", ".", "...", " .. ", "./..", "../..", ". ."):
            with self.subTest(title=hostile):
                cleaned = sanitize_filename(hostile)
                self.assertNotIn("/", cleaned)
                self.assertNotEqual(cleaned.strip(". "), "", f"{hostile!r} -> {cleaned!r}")
                self.assertNotIn(cleaned, ("..", "."))

    def test_download_folder_stays_inside_the_base_directory(self) -> None:
        from pathlib import Path

        from ani_it.utils import sanitize_filename

        base = Path("/home/utente/Scaricati/Anime")
        for hostile in ("..", "../../etc", "a/../..", "..\\..\\x"):
            with self.subTest(title=hostile):
                target = (base / sanitize_filename(hostile)).resolve()
                self.assertTrue(str(target).startswith(str(base)), f"{hostile!r} esce da {base}: {target}")

    def test_control_characters_are_removed(self) -> None:
        from ani_it.utils import sanitize_filename

        cleaned = sanitize_filename("Titolo\x00con\x1bcontrollo\x7f\n\tfine")
        self.assertEqual(cleaned, "Titolo con controllo fine")
        self.assertFalse([c for c in cleaned if ord(c) < 0x20 or ord(c) == 0x7F])

    def test_ordinary_titles_are_unchanged(self) -> None:
        from ani_it.utils import sanitize_filename

        self.assertEqual(sanitize_filename("Chainsaw Man"), "Chainsaw Man")
        self.assertEqual(sanitize_filename("Re:Zero"), "Re_Zero")
        self.assertEqual(sanitize_filename("Fate/Zero"), "Fate_Zero")
        self.assertEqual(sanitize_filename("進撃の巨人"), "進撃の巨人")
        self.assertEqual(sanitize_filename("Bocchi the Rock!"), "Bocchi the Rock!")

    def test_empty_names_get_a_placeholder(self) -> None:
        from ani_it.utils import sanitize_filename

        self.assertEqual(sanitize_filename(""), "anime")
        self.assertEqual(sanitize_filename("   "), "anime")

    def test_overlong_names_fit_the_filesystem_limit(self) -> None:
        from ani_it.utils import sanitize_filename

        for title in ("A" * 500, "進" * 200):
            with self.subTest(length=len(title)):
                self.assertLessEqual(len(sanitize_filename(title).encode("utf-8")), 200)


class TestDownloadDir(unittest.TestCase):
    """Calcolare la cartella dei download non deve creare nulla (succedeva istanziando DownloaderManager)."""

    def test_fallback_directory_is_not_created_by_the_lookup(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from ani_it.utils import get_download_dir

        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if k != "XDG_DOWNLOAD_DIR"}
            env["HOME"] = home
            with patch.dict(os.environ, env, clear=True), \
                    patch("ani_it.utils.subprocess.run", side_effect=FileNotFoundError):
                result = get_download_dir()
            self.assertEqual(result, Path(home) / "Downloads" / "Anime")
            self.assertEqual(list(Path(home).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
