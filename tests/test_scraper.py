"""Test unitari con mock per AnimeUnityScraper."""

import contextlib
import io
import json
import unittest
from unittest.mock import MagicMock, patch

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

    def test_no_episodes_raises_instead_of_inventing_one(self) -> None:
        """Senza episodi non va creato un 'episodio 1' con l'ID dell'anime come ID episodio."""
        from ani_it.scraper import EpisodesNotFoundError

        api_empty = MagicMock()
        api_empty.status_code = 200
        api_empty.json.return_value = {"episodes": []}
        html_page = MagicMock()
        html_page.status_code = 200
        html_page.text = "<html><body><h1>Qualcosa</h1></body></html>"
        self.mock_session.get.side_effect = [api_empty, html_page]

        with self.assertRaises(EpisodesNotFoundError):
            self.scraper.get_anime_details("500", "500-qualcosa")

    def test_network_failure_is_not_masked_as_no_episodes(self) -> None:
        """Se la rete è giù l'errore reale deve emergere, non una lista con un episodio finto."""
        self.mock_session.get.side_effect = requests.exceptions.ConnectionError("Connection refused")
        self.scraper.max_retries = 1
        self.scraper.base_backoff = 0

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(requests.exceptions.ConnectionError):
                self.scraper.get_anime_details("500", "500-qualcosa")


def make_response(status: int, payload: object = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = text
    if status >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(f"HTTP {status}", response=resp)
    return resp


class TestRequestRetry(unittest.TestCase):
    """Retry solo sugli errori transitori; messaggi distinti; nessuna stampa dalla libreria."""

    def setUp(self) -> None:
        self.mock_session = MagicMock(spec=requests.Session)
        self.mock_session.headers = {}
        self.scraper = AnimeUnityScraper(session=self.mock_session)
        self.scraper._csrf_token = "tok"
        self.scraper._csrf_expires = 9999999999.0
        self.scraper.max_retries = 3
        self.scraper.base_backoff = 0

    def request(self, **kwargs):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            try:
                return self.scraper._request_with_retry("GET", "https://www.animeunity.so/x", **kwargs), err.getvalue()
            except Exception as exc:  # noqa: BLE001 - il test ispeziona il tipo
                self.exc = exc
                return None, err.getvalue()

    def test_404_is_not_retried_and_is_reported_as_not_found(self) -> None:
        from ani_it.scraper import ResourceNotFoundError

        self.mock_session.get.return_value = make_response(404)
        _, stderr = self.request()
        self.assertIsInstance(self.exc, ResourceNotFoundError)
        self.assertNotIsInstance(self.exc, requests.exceptions.ConnectionError)
        self.assertEqual(self.mock_session.get.call_count, 1)
        self.assertEqual(stderr, "")

    def test_other_client_errors_are_not_retried(self) -> None:
        self.mock_session.get.return_value = make_response(422)
        self.request()
        self.assertIsInstance(self.exc, requests.exceptions.HTTPError)
        self.assertEqual(self.mock_session.get.call_count, 1)

    def test_server_errors_are_retried_then_reported_as_unreachable(self) -> None:
        from ani_it.scraper import SiteUnreachableError

        self.mock_session.get.return_value = make_response(500)
        self.request()
        self.assertIsInstance(self.exc, SiteUnreachableError)
        self.assertIsInstance(self.exc, requests.exceptions.ConnectionError)  # compatibilità con i chiamanti
        self.assertEqual(self.mock_session.get.call_count, 3)
        self.assertIn("500", str(self.exc))

    def test_429_and_403_are_retried(self) -> None:
        for status in (429, 403):
            with self.subTest(status=status):
                self.mock_session.get.reset_mock()
                self.mock_session.get.side_effect = None
                self.mock_session.get.return_value = make_response(status)
                self.request()
                self.assertEqual(self.mock_session.get.call_count, 3)

    def test_network_errors_are_retried_and_success_is_returned(self) -> None:
        ok = make_response(200, {"a": 1})
        self.mock_session.get.side_effect = [
            requests.exceptions.ConnectionError("down"),
            requests.exceptions.Timeout("slow"),
            ok,
        ]
        resp, stderr = self.request()
        self.assertIs(resp, ok)
        self.assertEqual(stderr, "")  # nessun messaggio rosso se un tentativo successivo riesce

    def test_nothing_is_printed_even_on_final_failure(self) -> None:
        self.mock_session.get.side_effect = requests.exceptions.ConnectionError("down")
        _, stderr = self.request()
        self.assertEqual(stderr, "")


class TestSessionHeaders(unittest.TestCase):
    """CSRF e header XHR non devono finire sulla sessione condivisa (resolver, Vixcloud)."""

    def test_csrf_and_ajax_headers_do_not_leak_into_shared_session(self) -> None:
        session = requests.Session()
        scraper = AnimeUnityScraper(session=session)
        page = make_response(200, text='<meta name="csrf-token" content="SECRET123">')
        with patch.object(session, "get", return_value=page):
            scraper._ensure_session()

        self.assertEqual(scraper._csrf_token, "SECRET123")
        names = {k.lower() for k in session.headers}
        for forbidden in ("x-csrf-token", "x-requested-with", "origin", "sec-fetch-site", "sec-fetch-dest"):
            self.assertNotIn(forbidden, names)

    def test_api_calls_carry_csrf_but_page_calls_do_not(self) -> None:
        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        scraper = AnimeUnityScraper(session=mock_session)
        scraper._csrf_token = "SECRET123"
        scraper._csrf_expires = 9999999999.0
        mock_session.get.return_value = make_response(200, {"episodes": []}, text="<html></html>")

        scraper._request_with_retry("GET", "https://www.animeunity.so/info_api/1/1", api=True)
        api_headers = mock_session.get.call_args.kwargs["headers"]
        self.assertEqual(api_headers["X-CSRF-TOKEN"], "SECRET123")
        self.assertEqual(api_headers["X-Requested-With"], "XMLHttpRequest")

        scraper._request_with_retry("GET", "https://www.animeunity.so/anime/1-x")
        page_headers = mock_session.get.call_args.kwargs["headers"]
        self.assertNotIn("X-CSRF-TOKEN", page_headers)
        self.assertNotIn("X-Requested-With", page_headers)


class TestEpisodeListCompleteness(unittest.TestCase):
    """Una lista episodi troncata da un errore a metà non deve sembrare completa."""

    def setUp(self) -> None:
        self.mock_session = MagicMock(spec=requests.Session)
        self.mock_session.headers = {}
        self.scraper = AnimeUnityScraper(session=self.mock_session)
        self.scraper._csrf_token = "tok"
        self.scraper._csrf_expires = 9999999999.0
        self.scraper.max_retries = 1
        self.scraper.base_backoff = 0

    @staticmethod
    def chunk(start: int, count: int) -> dict:
        return {"episodes": [
            {"id": 9000 + n, "number": n, "title": "", "link": f"https://vixcloud.co/embed/{n}"}
            for n in range(start, start + count)
        ]}

    def test_failure_on_second_page_marks_the_list_as_partial(self) -> None:
        html_page = make_response(200, text="<html><h1>Serie lunga</h1></html>")
        self.mock_session.get.side_effect = [
            make_response(200, self.chunk(1, 120)),
            requests.exceptions.ConnectionError("caduta di rete"),
            html_page,
        ]
        details = self.scraper.get_anime_details("700", "700-serie-lunga")
        self.assertEqual(len(details["episodes"]), 120)
        self.assertTrue(details["partial"])
        self.assertIn("caduta di rete", details["partial_reason"])

    def test_complete_list_is_not_partial(self) -> None:
        self.mock_session.get.side_effect = [
            make_response(200, self.chunk(1, 12)),
            make_response(200, text="<html><h1>Serie</h1></html>"),
        ]
        details = self.scraper.get_anime_details("701", "701-serie")
        self.assertFalse(details["partial"])


class TestAnimeEntryNormalization(unittest.TestCase):
    """Campi reali dell'API di AnimeUnity (verificati dal vivo)."""

    def setUp(self) -> None:
        self.scraper = AnimeUnityScraper(session=MagicMock(spec=requests.Session, headers={}))

    def test_cover_is_read_from_imageurl_field(self) -> None:
        entry = self.scraper._normalize_anime_entry({
            "id": 2584, "title": "One Punch Man", "slug": "one-punch-man", "cover": None,
            "imageurl": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21087.jpg",
        })
        self.assertEqual(entry["image_url"], "https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21087.jpg")

    def test_dub_flag_comes_from_api_field(self) -> None:
        dubbed = self.scraper._normalize_anime_entry({"id": 1, "title": "Naruto (ITA)", "slug": "naruto-ita", "dub": 1})
        subbed = self.scraper._normalize_anime_entry({"id": 2, "title": "Naruto", "slug": "naruto", "dub": 0})
        self.assertTrue(dubbed["dub"])
        self.assertFalse(subbed["dub"])

    def test_dub_flag_is_not_guessed_from_slug_substring(self) -> None:
        """'-ita' dentro un'altra parola (es. 'hospitalita') non significa doppiaggio."""
        entry = self.scraper._normalize_anime_entry({"id": 3, "title": "Hospitality", "slug": "hospitalita", "dub": 0})
        self.assertFalse(entry["dub"])


class TestSearchFallback(unittest.TestCase):
    """La 'strategia 3' troncava la query a 4 caratteri e restituiva risultati non pertinenti."""

    def setUp(self) -> None:
        self.mock_session = MagicMock(spec=requests.Session)
        self.mock_session.headers = {}
        self.scraper = AnimeUnityScraper(session=self.mock_session)
        self.scraper._csrf_token = "tok"
        self.scraper._csrf_expires = 9999999999.0
        self.scraper.max_retries = 1
        self.scraper.base_backoff = 0

    def test_empty_search_never_retries_with_an_altered_query(self) -> None:
        self.mock_session.post.return_value = make_response(200, {"records": []})
        self.mock_session.get.return_value = make_response(200, text="<html><body></body></html>")

        for query in ("umamusume", "chainsawman", "jujutsu"):
            self.mock_session.post.reset_mock()
            self.assertEqual(self.scraper.search_anime(query), [])
            sent = [c.kwargs["json_data"]["title"] if "json_data" in c.kwargs else c.kwargs["json"]["title"]
                    for c in self.mock_session.post.call_args_list]
            self.assertTrue(sent)
            self.assertEqual(set(sent), {query}, f"query alterata per {query!r}: {sent}")


class TestSlugTitles(unittest.TestCase):
    """Titolo di ripiego ricavato dallo slug."""

    def setUp(self) -> None:
        self.scraper = AnimeUnityScraper(session=MagicMock(spec=requests.Session, headers={}))

    def title_for(self, slug: str) -> str:
        return self.scraper._normalize_anime_entry({"id": 1, "slug": slug})["title"]

    def test_sub_ita_suffix_is_recognised_before_ita(self) -> None:
        self.assertEqual(self.title_for("naruto-sub-ita"), "Naruto (SUB ITA)")

    def test_ita_suffix(self) -> None:
        self.assertEqual(self.title_for("naruto-ita"), "Naruto (ITA)")

    def test_tag_keeps_uppercase(self) -> None:
        """`.title()` trasformava 'ITA' in 'Ita'."""
        self.assertIn("(ITA)", self.title_for("one-piece-ita"))

    def test_ita_inside_a_word_is_not_a_tag(self) -> None:
        self.assertEqual(self.title_for("hospitalita"), "Hospitalita")
        self.assertEqual(self.title_for("ita-no-mono"), "Ita No Mono")

    def test_plain_slug(self) -> None:
        self.assertEqual(self.title_for("one-punch-man"), "One Punch Man")


class TestEpisodeNormalization(unittest.TestCase):
    """Una sola normalizzazione per i rami API e HTML; l'episodio 0 non va perso."""

    def setUp(self) -> None:
        self.mock_session = MagicMock(spec=requests.Session)
        self.mock_session.headers = {}
        self.scraper = AnimeUnityScraper(session=self.mock_session)
        self.scraper._csrf_token = "tok"
        self.scraper._csrf_expires = 9999999999.0
        self.scraper.max_retries = 1
        self.scraper.base_backoff = 0

    def test_html_branch_keeps_episode_zero(self) -> None:
        """`number or id` scartava lo 0 sostituendolo con l'ID del database."""
        html_page = (
            '<html><h1>Serie</h1><video-player :episodes=\'[{"id": 4242, "number": 0, '
            '"link": "https://vixcloud.co/embed/9"}, {"id": 4243, "number": 1, '
            '"link": "https://vixcloud.co/embed/10"}]\'></video-player></html>'
        )
        self.mock_session.get.side_effect = [make_response(200, {"episodes": []}), make_response(200, text=html_page)]
        details = self.scraper.get_anime_details("800", "800-serie")
        self.assertEqual([e["number"] for e in details["episodes"]], [0, 1])

    def test_api_and_html_branches_produce_the_same_shape(self) -> None:
        raw = {"id": 7, "number": 3, "title": "Titolo", "link": "https://vixcloud.co/embed/3",
               "created_at": "2024-01-01", "file_name": "x.mp4"}
        built = self.scraper._build_episode(raw)
        self.assertEqual(set(built), {"id", "number", "title", "created_at", "file_name", "link"})
        self.assertEqual(built["number"], 3)

    def test_dead_cdn_links_are_replaced_by_embed_url(self) -> None:
        raw = {"id": 55, "number": 1, "link": "https://animeunityserver.example/x.mp4"}
        self.assertEqual(self.scraper._build_episode(raw)["link"], "https://www.animeunity.so/embed-url/55")

    def test_decimal_and_string_numbers(self) -> None:
        self.assertEqual(self.scraper._build_episode({"id": 1, "number": "12.5"})["number"], 12.5)
        self.assertEqual(self.scraper._build_episode({"id": 1, "number": "12"})["number"], 12)
        self.assertEqual(self.scraper._build_episode({"id": 9, "number": None})["number"], 9)  # ripiego sull'ID

    def test_invalid_entries_are_skipped(self) -> None:
        self.assertIsNone(self.scraper._build_episode("non un dict"))  # type: ignore[arg-type]


class TestConfigurableDomain(unittest.TestCase):
    """Il dominio di AnimeUnity cambia spesso: non può essere scritto nel codice."""

    def setUp(self) -> None:
        self.mock_session = MagicMock(spec=requests.Session)
        self.mock_session.headers = {}
        self.scraper = AnimeUnityScraper(session=self.mock_session, base_url="https://www.animeunity.to/")
        self.scraper._csrf_token = "tok"
        self.scraper._csrf_expires = 9999999999.0
        self.scraper.max_retries = 1
        self.scraper.base_backoff = 0

    def test_trailing_slash_is_stripped(self) -> None:
        self.assertEqual(self.scraper.base_url, "https://www.animeunity.to")

    def test_search_uses_the_configured_domain(self) -> None:
        self.mock_session.post.return_value = make_response(200, {"records": [{"id": 1, "title": "X", "slug": "x"}]})
        self.scraper.search_anime("x")
        urls = [c.args[0] for c in self.mock_session.post.call_args_list]
        self.assertTrue(urls)
        self.assertTrue(all(u.startswith("https://www.animeunity.to/") for u in urls), urls)

    def test_headers_reference_the_configured_domain(self) -> None:
        headers = self.scraper._site_headers()
        self.assertEqual(headers["Referer"], "https://www.animeunity.to")
        self.assertEqual(headers["Origin"], "https://www.animeunity.to")

    def test_episode_fallback_links_use_the_configured_domain(self) -> None:
        raw = {"id": 55, "number": 1, "link": ""}
        self.assertEqual(self.scraper._build_episode(raw)["link"], "https://www.animeunity.to/embed-url/55")

    def test_default_domain_is_unchanged(self) -> None:
        default = AnimeUnityScraper(session=self.mock_session)
        self.assertEqual(default.base_url, "https://www.animeunity.so")


if __name__ == "__main__":
    unittest.main()
