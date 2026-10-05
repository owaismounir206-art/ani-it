"""Gestione atomica della cronologia locale di riproduzione conforme a XDG."""

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from ani_it.utils import get_state_dir

HISTORY_FILE_NAME = "history.json"


class HistoryManager:
    """Gestisce la cronologia degli anime guardati con operazioni atomiche di lettura e scrittura."""

    def __init__(self, custom_path: Optional[Path] = None) -> None:
        self.path = custom_path or (get_state_dir() / HISTORY_FILE_NAME)
        self._ensure_file()

    def _ensure_file(self) -> None:
        """Crea il file della cronologia vuoto se non esiste."""
        if not self.path.exists():
            default_data: dict[str, Any] = {"series": {}, "last_played": None}
            self._write_atomic(default_data)

    def load(self) -> dict[str, Any]:
        """Carica l'intero stato della cronologia da disco."""
        if not self.path.is_file():
            return {"series": {}, "last_played": None}

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return {"series": {}, "last_played": None}
                data.setdefault("series", {})
                data.setdefault("last_played", None)
                self._migrate(data)
                return data
        except Exception:
            return {"series": {}, "last_played": None}

    @staticmethod
    def _migrate(data: dict[str, Any]) -> None:
        """Converte il vecchio campo ambiguo `completed` in episode_completed/series_completed.

        Nel vecchio formato `completed` indicava sia "episodio finito" sia "serie finita".
        La serie è considerata finita solo se l'ultimo episodio visto era già il totale.
        """
        series = data.get("series")
        if not isinstance(series, dict):
            data["series"] = {}
            return
        for entry in series.values():
            if not isinstance(entry, dict) or "episode_completed" in entry:
                continue
            old_completed = bool(entry.pop("completed", False))
            total = entry.get("total_episodes") or 0
            last = entry.get("last_episode") or 0
            entry["episode_completed"] = old_completed
            entry["series_completed"] = bool(old_completed and total > 0 and last >= total)
            entry.setdefault("last_position", 0.0)

    def _write_atomic(self, data: dict[str, Any]) -> None:
        """Scrive i dati in modo atomico: file temporaneo univoco nella stessa directory + os.replace.

        Il nome del temporaneo è unico per ogni scrittura (mkstemp): due istanze in parallelo
        non si sovrascrivono a vicenda il file di appoggio.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def update_progress(
        self,
        anime_id: str | int,
        title: str,
        slug: str,
        episode: int | float,
        total_episodes: int = 0,
        episode_completed: bool = False,
        series_completed: bool = False,
        position: float = 0.0,
    ) -> None:
        """Aggiorna lo stato di avanzamento per una serie specifica.

        Args:
            episode: Numero reale dell'episodio (anche decimale, es. 12.5).
            episode_completed: L'episodio è stato guardato fino alla fine.
            series_completed: Anche la serie è finita (ultimo episodio finito). Il chiamante
                conosce la lista episodi: confrontare il numero con il conteggio sarebbe
                sbagliato per numerazioni che non partono da 1 o hanno buchi.
            position: Secondi raggiunti nell'episodio, per la ripresa; azzerati a fine episodio.
        """
        key = str(anime_id)
        data = self.load()
        series_dict = data.setdefault("series", {})

        current_entry = series_dict.get(key, {})
        tot = total_episodes or current_entry.get("total_episodes", 0)

        series_dict[key] = {
            "title": title,
            "slug": slug,
            "last_episode": episode,
            "last_watched_timestamp": time.time(),
            "total_episodes": tot,
            "episode_completed": bool(episode_completed),
            "series_completed": bool(series_completed and episode_completed),
            "last_position": 0.0 if episode_completed else max(0.0, float(position)),
        }
        data["last_played"] = key
        self._write_atomic(data)

    def get_last_played(self) -> Optional[dict[str, Any]]:
        """Restituisce i dati dell'ultimo anime riprodotto, se presente."""
        data = self.load()
        last_id = data.get("last_played")
        if not last_id:
            return None
        series = data.get("series", {})
        item = series.get(str(last_id))
        if item:
            item_copy = dict(item)
            item_copy["id"] = str(last_id)
            return item_copy
        return None

    def get_anime_history(self, anime_id: str | int) -> Optional[dict[str, Any]]:
        """Restituisce le informazioni di cronologia per un ID anime specifico."""
        data = self.load()
        series = data.get("series", {})
        return series.get(str(anime_id))

    def list_history(self) -> list[dict[str, Any]]:
        """Restituisce la lista ordinata di tutti gli anime visti (dal più recente)."""
        data = self.load()
        series = data.get("series", {})
        result: list[dict[str, Any]] = []

        for aid, info in series.items():
            entry = dict(info)
            entry["id"] = aid
            result.append(entry)

        result.sort(key=lambda x: x.get("last_watched_timestamp", 0), reverse=True)
        return result

    def list_titles(self) -> list[str]:
        """Restituisce la lista dei soli titoli degli anime in cronologia (utile per shell completions)."""
        history = self.list_history()
        return [entry["title"] for entry in history if entry.get("title")]

    def clear(self) -> None:
        """Svuota completamente il database della cronologia."""
        self._write_atomic({"series": {}, "last_played": None})
