"""Test unitari per la gestione della cronologia (HistoryManager)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ani_it.history import HistoryManager


class TestHistoryManager(unittest.TestCase):
    """Verifica le operazioni atomiche di lettura, scrittura e aggiornamento cronologia."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.history_file = Path(self.temp_dir.name) / "history.json"
        self.manager = HistoryManager(custom_path=self.history_file)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initial_state(self) -> None:
        """Verifica che il file inizializzato contenga la struttura corretta."""
        data = self.manager.load()
        self.assertEqual(data.get("series"), {})
        self.assertIsNone(data.get("last_played"))

    def test_update_progress_and_get_last_played(self) -> None:
        """Verifica l'inserimento di un anime e il recupero dell'ultimo riprodotto."""
        self.manager.update_progress(
            anime_id=123,
            title="Chainsaw Man",
            slug="123-chainsaw-man",
            episode=4,
            total_episodes=12,
        )

        last = self.manager.get_last_played()
        self.assertIsNotNone(last)
        self.assertEqual(last["id"], "123")
        self.assertEqual(last["title"], "Chainsaw Man")
        self.assertEqual(last["last_episode"], 4)
        self.assertFalse(last["episode_completed"])
        self.assertFalse(last["series_completed"])
        self.assertNotIn("completed", last)

    def test_last_episode_number_alone_does_not_complete_anything(self) -> None:
        """Aprire l'ultimo episodio e uscire subito non lo rende 'completato' né chiude la serie."""
        self.manager.update_progress(456, "Frieren", "456-frieren", episode=28, total_episodes=28)
        last = self.manager.get_last_played()
        self.assertFalse(last["episode_completed"])
        self.assertFalse(last["series_completed"])

    def test_numbering_not_starting_at_one_is_not_misread_as_completion(self) -> None:
        """Serie numerata 13-24 (12 episodi): l'episodio 13 non è 'oltre il totale'."""
        self.manager.update_progress(7, "Stagione 2", "7-s2", episode=13, total_episodes=12)
        last = self.manager.get_last_played()
        self.assertFalse(last["series_completed"])
        self.assertFalse(last["episode_completed"])

    def test_finishing_an_episode_does_not_complete_the_series(self) -> None:
        """Un solo episodio finito su 12: 'Completato' in --history sarebbe sbagliato."""
        self.manager.update_progress(8, "Serie", "8-s", episode=1, total_episodes=12, episode_completed=True)
        last = self.manager.get_last_played()
        self.assertTrue(last["episode_completed"])
        self.assertFalse(last["series_completed"])

    def test_series_completed_only_when_caller_says_so(self) -> None:
        self.manager.update_progress(
            9, "Serie", "9-s", episode=12, total_episodes=12, episode_completed=True, series_completed=True
        )
        last = self.manager.get_last_played()
        self.assertTrue(last["episode_completed"])
        self.assertTrue(last["series_completed"])

    def test_series_cannot_be_completed_without_finishing_the_episode(self) -> None:
        self.manager.update_progress(
            9, "Serie", "9-s", episode=12, total_episodes=12, episode_completed=False, series_completed=True
        )
        self.assertFalse(self.manager.get_last_played()["series_completed"])

    def test_decimal_episode_number_is_preserved(self) -> None:
        self.manager.update_progress(10, "Serie", "10-s", episode=12.5, total_episodes=13)
        self.assertEqual(self.manager.get_last_played()["last_episode"], 12.5)

    def test_position_is_saved_for_unfinished_episode(self) -> None:
        self.manager.update_progress(11, "Serie", "11-s", episode=3, position=734.5)
        self.assertEqual(self.manager.get_last_played()["last_position"], 734.5)

    def test_position_is_reset_when_episode_is_finished(self) -> None:
        self.manager.update_progress(11, "Serie", "11-s", episode=3, position=734.5)
        self.manager.update_progress(11, "Serie", "11-s", episode=3, episode_completed=True, position=1400.0)
        self.assertEqual(self.manager.get_last_played()["last_position"], 0.0)

    def test_old_format_is_migrated_on_load(self) -> None:
        """Il vecchio campo ambiguo `completed` viene separato in episode/series_completed."""
        old = {
            "last_played": "1",
            "series": {
                "1": {"title": "Un episodio visto", "slug": "1-a", "last_episode": 1, "total_episodes": 12,
                      "completed": True, "last_watched_timestamp": 3.0},
                "2": {"title": "Serie finita", "slug": "2-b", "last_episode": 12, "total_episodes": 12,
                      "completed": True, "last_watched_timestamp": 2.0},
                "3": {"title": "In corso", "slug": "3-c", "last_episode": 4, "total_episodes": 12,
                      "completed": False, "last_watched_timestamp": 1.0},
            },
        }
        self.history_file.write_text(json.dumps(old), encoding="utf-8")

        one = self.manager.get_anime_history(1)
        self.assertTrue(one["episode_completed"])
        self.assertFalse(one["series_completed"])  # prima risultava "Completato" dopo un solo episodio

        two = self.manager.get_anime_history(2)
        self.assertTrue(two["episode_completed"])
        self.assertTrue(two["series_completed"])

        three = self.manager.get_anime_history(3)
        self.assertFalse(three["episode_completed"])
        self.assertFalse(three["series_completed"])

    def test_migrated_file_no_longer_contains_old_field_after_write(self) -> None:
        old = {"last_played": "1", "series": {"1": {"title": "A", "slug": "1-a", "last_episode": 2,
                                                    "total_episodes": 5, "completed": True}}}
        self.history_file.write_text(json.dumps(old), encoding="utf-8")
        self.manager.update_progress(1, "A", "1-a", episode=3)
        raw = json.loads(self.history_file.read_text(encoding="utf-8"))
        self.assertNotIn("completed", raw["series"]["1"])
        self.assertIn("episode_completed", raw["series"]["1"])

    def test_list_history_ordered_by_timestamp(self) -> None:
        """Verifica che la lista sia ordinata in modo decrescente per timestamp."""
        self.manager.update_progress(1, "Anime A", "1-a", 1)
        self.manager.update_progress(2, "Anime B", "2-b", 5)

        history = self.manager.list_history()
        self.assertEqual(len(history), 2)
        # Anime B è stato aggiornato per ultimo
        self.assertEqual(history[0]["title"], "Anime B")
        self.assertEqual(history[1]["title"], "Anime A")

    def test_temp_file_names_are_unique_and_in_the_same_directory(self) -> None:
        """Un nome fisso farebbe sovrascrivere il file temporaneo a due istanze in parallelo."""
        replaced: list[Path] = []
        real_replace = os.replace

        def spy(src: str, dst: str) -> None:
            replaced.append(Path(src))
            real_replace(src, dst)

        with patch("ani_it.history.os.replace", side_effect=spy):
            self.manager.update_progress(1, "A", "1-a", 1)
            self.manager.update_progress(1, "A", "1-a", 2)

        self.assertEqual(len(replaced), 2)
        self.assertNotEqual(replaced[0], replaced[1])
        for tmp in replaced:
            self.assertEqual(tmp.parent, self.history_file.parent)
            self.assertFalse(tmp.exists(), "file temporaneo rimasto")

    def test_legacy_fixed_temp_name_is_left_alone(self) -> None:
        stale = self.history_file.parent / f".{self.history_file.name}.tmp"
        stale.write_text("{ broken", encoding="utf-8")
        self.manager.update_progress(1, "A", "1-a", 1)
        self.assertEqual(stale.read_text(encoding="utf-8"), "{ broken")
        self.assertEqual(self.manager.get_last_played()["last_episode"], 1)

    def test_failed_write_leaves_no_temp_file(self) -> None:
        before = set(self.history_file.parent.iterdir())
        with patch("ani_it.history.os.replace", side_effect=OSError("disco pieno")):
            with self.assertRaises(OSError):
                self.manager.update_progress(1, "A", "1-a", 1)
        self.assertEqual(set(self.history_file.parent.iterdir()), before)

    def test_parallel_writers_never_corrupt_the_file(self) -> None:
        import threading

        managers = [HistoryManager(custom_path=self.history_file) for _ in range(4)]
        errors: list[Exception] = []

        def worker(manager: HistoryManager, base: int) -> None:
            try:
                for i in range(25):
                    manager.update_progress(base + i, f"Anime {base + i}", f"{base + i}-x", i + 1)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(m, n * 100)) for n, m in enumerate(managers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        data = json.loads(self.history_file.read_text(encoding="utf-8"))  # JSON sempre valido
        self.assertIn("series", data)
        leftovers = [p.name for p in self.history_file.parent.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_list_titles(self) -> None:
        """Verifica la restituzione dei soli titoli per shell completions."""
        self.manager.update_progress(10, "Steins;Gate", "10-steins", 1)
        self.manager.update_progress(20, "Evangelion", "20-eva", 26)

        titles = self.manager.list_titles()
        self.assertIn("Steins;Gate", titles)
        self.assertIn("Evangelion", titles)

    def test_clear_history(self) -> None:
        """Verifica la cancellazione completa del database cronologia."""
        self.manager.update_progress(1, "Test", "1-test", 1)
        self.manager.clear()
        self.assertEqual(self.manager.list_history(), [])
        self.assertIsNone(self.manager.get_last_played())

    def test_corrupted_file_fallback(self) -> None:
        """Verifica la resilienza in caso di file JSON corrotto su disco."""
        self.history_file.write_text("INVALID JSON DATA {{{", encoding="utf-8")
        data = self.manager.load()
        self.assertEqual(data.get("series"), {})


if __name__ == "__main__":
    unittest.main()
