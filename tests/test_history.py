"""Test unitari per la gestione della cronologia (HistoryManager)."""

import json
import tempfile
import unittest
from pathlib import Path

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
            completed=False,
        )

        last = self.manager.get_last_played()
        self.assertIsNotNone(last)
        self.assertEqual(last["id"], "123")
        self.assertEqual(last["title"], "Chainsaw Man")
        self.assertEqual(last["last_episode"], 4)
        self.assertFalse(last["completed"])

    def test_completion_detection(self) -> None:
        """Verifica che il flag completed diventi True quando si raggiunge il totale episodi."""
        self.manager.update_progress(
            anime_id=456,
            title="Frieren",
            slug="456-frieren",
            episode=28,
            total_episodes=28,
        )
        last = self.manager.get_last_played()
        self.assertIsNotNone(last)
        self.assertTrue(last["completed"])

    def test_list_history_ordered_by_timestamp(self) -> None:
        """Verifica che la lista sia ordinata in modo decrescente per timestamp."""
        self.manager.update_progress(1, "Anime A", "1-a", 1)
        self.manager.update_progress(2, "Anime B", "2-b", 5)

        history = self.manager.list_history()
        self.assertEqual(len(history), 2)
        # Anime B è stato aggiornato per ultimo
        self.assertEqual(history[0]["title"], "Anime B")
        self.assertEqual(history[1]["title"], "Anime A")

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
