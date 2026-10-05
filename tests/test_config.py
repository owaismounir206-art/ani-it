"""Test di configurazione: validazione dei tipi, avvisi, serializzazione e dominio configurabile."""

import contextlib
import io
import tempfile
import tomllib
import unittest
from pathlib import Path

from ani_it.config import DEFAULT_CONFIG_TEMPLATE, Config, load_config


class ConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "config.toml"
        self.warnings: list[str] = []

    def load(self, toml_text: str) -> Config:
        self.path.write_text(toml_text, encoding="utf-8")
        return load_config(self.path, warn=self.warnings.append)

    def assertWarned(self, *fragments: str) -> None:
        joined = "\n".join(self.warnings)
        self.assertTrue(self.warnings, "nessun avviso emesso")
        for fragment in fragments:
            self.assertIn(fragment, joined)


class TestConfigOptions(ConfigTestCase):
    """`language` non era usata da nessuna parte: è stata rimossa invece di restare una promessa vuota."""

    def test_language_option_is_gone(self) -> None:
        self.assertFalse(hasattr(Config().general, "language"))
        self.assertNotIn("language", DEFAULT_CONFIG_TEMPLATE)
        self.assertNotIn("language", Config().to_toml())

    def test_old_config_file_with_language_still_loads_without_warnings(self) -> None:
        """Chi ha già `language = "it"` nel proprio config.toml non deve avere errori né perdere il resto."""
        config = self.load(
            '[player]\nbinary = "mpv"\n\n[general]\nquality = "720p"\nlanguage = "it"\nauto_next = true\n'
        )
        self.assertEqual(config.general.quality, "720p")
        self.assertTrue(config.general.auto_next)
        self.assertEqual(self.warnings, [])

    def test_downloader_args_are_documented(self) -> None:
        self.assertIn("args", DEFAULT_CONFIG_TEMPLATE.split("[downloader]")[1].split("[general]")[0])

    def test_default_template_loads_cleanly_and_matches_defaults(self) -> None:
        config = self.load(DEFAULT_CONFIG_TEMPLATE)
        self.assertEqual(self.warnings, [])
        self.assertEqual(config, Config())


class TestConfigValidation(ConfigTestCase):
    """Valori del tipo sbagliato non devono essere trasformati in modo sorprendente né ignorati in silenzio."""

    def test_string_args_are_not_split_into_characters(self) -> None:
        config = self.load('[player]\nargs = "abc"\n')
        self.assertEqual(config.player.args, Config().player.args)
        self.assertWarned("player.args")

    def test_args_with_non_string_items_are_rejected(self) -> None:
        config = self.load('[downloader]\nargs = ["-x", 16]\n')
        self.assertEqual(config.downloader.args, Config().downloader.args)
        self.assertWarned("downloader.args")

    def test_valid_args_are_kept(self) -> None:
        config = self.load('[downloader]\nargs = ["-x", "4", "-s", "4"]\n')
        self.assertEqual(config.downloader.args, ["-x", "4", "-s", "4"])
        self.assertEqual(self.warnings, [])

    def test_string_false_is_not_treated_as_true(self) -> None:
        """bool("false") è True: un valore stringa va segnalato e non interpretato."""
        config = self.load('[general]\npreview_art = "false"\n')
        self.assertTrue(config.general.preview_art)  # valore predefinito
        self.assertWarned("preview_art")

    def test_auto_next_must_be_boolean(self) -> None:
        config = self.load('[general]\nauto_next = "yes"\n')
        self.assertFalse(config.general.auto_next)
        self.assertWarned("auto_next")

    def test_real_booleans_work(self) -> None:
        config = self.load("[general]\npreview_art = false\nauto_next = true\n")
        self.assertFalse(config.general.preview_art)
        self.assertTrue(config.general.auto_next)
        self.assertEqual(self.warnings, [])

    def test_quality_is_checked_against_supported_values(self) -> None:
        config = self.load('[general]\nquality = "4k"\n')
        self.assertEqual(config.general.quality, "best")
        self.assertWarned("quality", "4k")

    def test_quality_accepts_plain_numbers_and_any_case(self) -> None:
        self.assertEqual(self.load('[general]\nquality = "720"\n').general.quality, "720p")
        self.assertEqual(self.load('[general]\nquality = "BEST"\n').general.quality, "best")
        self.assertEqual(self.warnings, [])

    def test_empty_or_non_string_binary_is_rejected(self) -> None:
        config = self.load('[player]\nbinary = ""\n\n[downloader]\nbinary = 5\n')
        self.assertEqual(config.player.binary, "mpv")
        self.assertEqual(config.downloader.binary, "aria2c")
        self.assertWarned("player.binary", "downloader.binary")

    def test_section_of_wrong_type_is_reported(self) -> None:
        config = self.load("player = 3\n")
        self.assertEqual(config.player, Config().player)
        self.assertWarned("player")

    def test_corrupt_toml_warns_instead_of_failing_silently(self) -> None:
        config = self.load("[player\nbinary = = broken")
        self.assertEqual(config, Config())
        self.assertWarned(str(self.path))

    def test_default_warning_sink_writes_to_stderr(self) -> None:
        self.path.write_text('[general]\nquality = "8k"\n', encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            load_config(self.path)
        self.assertIn("quality", err.getvalue())

    def test_missing_file_creates_defaults_without_warning(self) -> None:
        config = load_config(self.path, warn=self.warnings.append)
        self.assertEqual(config, Config())
        self.assertEqual(self.warnings, [])
        self.assertTrue(self.path.is_file())


class TestBaseUrl(ConfigTestCase):
    """Il dominio di AnimeUnity cambia spesso: va in config.toml."""

    def test_default_base_url(self) -> None:
        self.assertEqual(Config().general.base_url, "https://www.animeunity.so")
        self.assertIn("base_url", DEFAULT_CONFIG_TEMPLATE)

    def test_custom_base_url_is_loaded_and_normalised(self) -> None:
        config = self.load('[general]\nbase_url = "https://www.animeunity.to/"\n')
        self.assertEqual(config.general.base_url, "https://www.animeunity.to")
        self.assertEqual(self.warnings, [])

    def test_invalid_base_url_falls_back_with_warning(self) -> None:
        for bad in ('"animeunity.to"', '"ftp://animeunity.to"', '""', "5"):
            with self.subTest(value=bad):
                self.warnings.clear()
                config = self.load(f"[general]\nbase_url = {bad}\n")
                self.assertEqual(config.general.base_url, "https://www.animeunity.so")
                self.assertWarned("base_url")


class TestSerialization(unittest.TestCase):
    """to_toml deve produrre TOML valido anche con virgolette e backslash."""

    def test_round_trip_with_special_characters(self) -> None:
        config = Config()
        config.player.binary = 'my "mpv" \\ path'
        config.player.args = ['--title="Ep 1"', "--path=C:\\dir\\file", "tab\there", "riga\nnuova", "àèì 日本語"]
        config.downloader.args = ["-x", 'a"b']
        config.general.quality = "720p"
        config.general.base_url = "https://www.animeunity.to"

        loaded = tomllib.loads(config.to_toml())
        self.assertEqual(loaded["player"]["binary"], config.player.binary)
        self.assertEqual(loaded["player"]["args"], config.player.args)
        self.assertEqual(loaded["downloader"]["args"], config.downloader.args)
        self.assertEqual(loaded["general"]["base_url"], "https://www.animeunity.to")

    def test_default_config_round_trips(self) -> None:
        loaded = tomllib.loads(Config().to_toml())
        self.assertEqual(loaded["player"]["binary"], "mpv")
        self.assertIs(loaded["general"]["preview_art"], True)
        self.assertIs(loaded["general"]["auto_next"], False)


if __name__ == "__main__":
    unittest.main()
