"""Test unitari con mock per AnimeUnityScraper."""

import json
import unittest
from unittest.mock import MagicMock

import requests

from ani_it.scraper import AnimeUnityScraper


class TestAnimeUnityScraper(unittest.TestCase):
    """Verifica il comportamento del parser HTML/Vue/JSON per AnimeUnity."""

    def setUp(self) -> None:
        self.mock_session = MagicMock(spec=requests.Session)
        self.mock_session.headers = {}
        self.scraper = AnimeUnityScraper(session=self.mock_session)
        # Pre-imposta un token CSRF fittizio per evitare chiamate iniziali di bootstrap
        self.scraper._csrf_token = "fake-csrf-token"
        self.scraper._csrf_expires = 9999999999.0

    def test_search_via_get_animes_api(self) -> None:
        """Verifica la ricerca tramite l'endpoint interno /archivio/get-animes."""
        mock_data = {
            "records": [
                {
                    "id": 101,
                    "title_it": "Attack on Titan",
                    "title_eng": "Shingeki no Kyojin",
                    "slug": "101-attack-on-titan",
                    "type": "TV",
                    "date": 2013,
                    "episodes_count": 25,
                    "status": "Terminato",
                    "image_url": "https://img.animeunity.so/aot.jpg",
                    "plot": "Giganti e mura.",
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_data
        self.mock_session.post.return_value = mock_resp

        results = self.scraper.search_anime("Attack on Titan")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "101")
        self.assertEqual(results[0]["title"], "Attack on Titan")
        self.assertEqual(results[0]["year"], "2013")
        self.assertEqual(results[0]["episodes_count"], "25")

    def test_search_via_livesearch_api(self) -> None:
        """Verifica la serializzazione automatica quando /livesearch risponde."""
        mock_data = {
            "records": [
                {
                    "id": 202,
                    "title": "Jujutsu Kaisen",
                    "slug": "202-jujutsu-kaisen",
                    "type": "TV",
                    "episodes": 24,
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_data
        self.mock_session.post.return_value = mock_resp

        results = self.scraper.search_anime("Jujutsu")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "202")
        self.assertEqual(results[0]["title"], "Jujutsu Kaisen")

    def test_search_via_vue_attribute_fallback(self) -> None:
        """Verifica il fallback con estrazione Vue attribute :animes."""
        # Fai fallire le prime due chiamate POST
        self.mock_session.post.side_effect = Exception("API error")

        payload = [
            {
                "id": 303,
                "title_it": "Demon Slayer",
                "slug": "303-demon-slayer",
                "type": "TV",
            }
        ]
        html_markup = f"""
        <html><body>
            <archive-component :animes='{json.dumps(payload)}'></archive-component>
        </body></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html_markup
        self.mock_session.get.return_value = mock_resp

        results = self.scraper.search_anime("Demon Slayer")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "303")
        self.assertEqual(results[0]["title"], "Demon Slayer")

    def test_get_anime_details_and_episodes(self) -> None:
        """Verifica l'estrazione degli episodi da info_api e metadati HTML."""
        api_payload = {
            "episodes": [
                {
                    "id": 1001,
                    "number": 1,
                    "title": "Il risveglio",
                    "link": "https://vixcloud.co/embed/1001",
                    "created_at": "2024-01-01",
                },
                {
                    "id": 1002,
                    "number": 2,
                    "title": "La partenza",
                    "link": "https://vixcloud.co/embed/1002",
                    "created_at": "2024-01-08",
                },
            ]
        }
        mock_api_resp = MagicMock()
        mock_api_resp.status_code = 200
        mock_api_resp.json.return_value = api_payload

        html_page = """
        <html>
            <head><title>Solo Leveling - AnimeUnity</title></head>
            <body>
                <h1>Solo Leveling</h1>
                <p class="plot">Trama del cacciatore più debole.</p>
            </body>
        </html>
        """
        mock_html_resp = MagicMock()
        mock_html_resp.status_code = 200
        mock_html_resp.text = html_page

        # Prima chiamata a info_api (GET), seconda alla pagina HTML (GET)
        self.mock_session.get.side_effect = [mock_api_resp, mock_html_resp]

        details = self.scraper.get_anime_details("500", "500-solo-leveling")
        self.assertEqual(details["id"], "500")
        self.assertEqual(details["title"], "Solo Leveling")
        self.assertEqual(details["total_episodes"], 2)

        eps = details["episodes"]
        self.assertEqual(len(eps), 2)
        self.assertEqual(eps[0]["number"], 1)
        self.assertEqual(eps[0]["title"], "Il risveglio")
        self.assertEqual(eps[1]["number"], 2)

    def test_connection_error_raises_exception(self) -> None:
        """Verifica che dopo i tentativi di retry venga sollevata un'eccezione esplicita."""
        self.mock_session.post.side_effect = requests.exceptions.ConnectionError("Connection refused")
        self.mock_session.get.side_effect = requests.exceptions.ConnectionError("Connection refused")
        self.scraper.max_retries = 2
        self.scraper.base_backoff = 0.01

        with self.assertRaises(requests.exceptions.ConnectionError):
            self.scraper.search_anime("Test")


if __name__ == "__main__":
    unittest.main()
