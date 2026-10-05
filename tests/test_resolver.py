"""Test unitari per StreamResolver e il deoffuscatore JavaScript."""

import unittest
from unittest.mock import MagicMock

import requests

from ani_it.resolver import StreamResolver
from ani_it.utils import unpack_js


class TestStreamResolver(unittest.TestCase):
    """Verifica l'estrazione di URL .m3u8/.mp4, deoffuscamento e gestione playlist."""

    def setUp(self) -> None:
        self.mock_session = MagicMock(spec=requests.Session)
        self.mock_session.headers = {}
        self.resolver = StreamResolver(session=self.mock_session)

    def test_direct_m3u8_link(self) -> None:
        """Verifica la gestione immediata dei link diretti .m3u8."""
        direct_url = "https://storage.cdn.com/videos/episode1.m3u8?token=xyz"
        result = self.resolver.resolve(direct_url)
        self.assertEqual(result["stream_url"], direct_url)
        self.assertEqual(result["format"], "hls")
        self.assertIn("Referer", result["headers"])

    def test_direct_mp4_link(self) -> None:
        """Verifica la gestione immediata dei link diretti .mp4."""
        direct_url = "https://storage.cdn.com/videos/episode1.mp4"
        result = self.resolver.resolve(direct_url)
        self.assertEqual(result["stream_url"], direct_url)
        self.assertEqual(result["format"], "mp4")

    def test_extract_from_embed_html(self) -> None:
        """Verifica l'estrazione del pattern file: '...' da una pagina player HTML."""
        embed_html = """
        <html>
            <head><script>
                var player = new Player({
                    file: "https://vixcloud.co/playlist/12345/master.m3u8?token=abc"
                });
            </script></head>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.text = embed_html
        mock_resp.url = "https://vixcloud.co/embed/12345"
        self.mock_session.get.return_value = mock_resp

        result = self.resolver.resolve("https://vixcloud.co/embed/12345")
        self.assertEqual(
            result["stream_url"],
            "https://vixcloud.co/playlist/12345/master.m3u8?token=abc",
        )
        self.assertEqual(result["format"], "hls")

    def test_unpack_js_dean_edwards(self) -> None:
        """Verifica il corretto deoffuscamento di codice packed con Dean Edwards."""
        # Esempio di codice packed reale
        # eval(function(p,a,c,k,e,d){...}('0 1="2";',3,3,'var|source|https'.split('|'),0,{}))
        packed = (
            "eval(function(p,a,c,k,e,d){while(c--)if(k[c])p=p.replace(new RegExp('\\\\b'+c.toString(a)+'\\\\b','g'),k[c]);return p}"
            "('0 1=\"2\";',3,3,'var|source|https'.split('|'),0,{}))"
        )
        unpacked = unpack_js(packed)
        self.assertIn('var source="https";', unpacked)

    def test_extract_from_packed_javascript(self) -> None:
        """Verifica che un player che offusca lo stream con eval() venga risolto."""
        # Payload packed contenente la riga: file: 'https://cdn.example.com/stream.m3u8'
        # k: 'file|https|cdn|example|com|stream|m3u8'
        packed_page = """
        <html><body>
        <script type="text/javascript">
        eval(function(p,a,c,k,e,d){while(c--)if(k[c])p=p.replace(new RegExp('\\\\b'+c.toString(a)+'\\\\b','g'),k[c]);return p}('0: \\'1://2.3.4/5.6\\'',7,7,'file|https|cdn|example|com|stream|m3u8'.split('|'),0,{}))
        </script>
        </body></html>
        """
        mock_resp = MagicMock()
        mock_resp.text = packed_page
        mock_resp.url = "https://embed.server.com/player/99"
        self.mock_session.get.return_value = mock_resp

        result = self.resolver.resolve("https://embed.server.com/player/99")
        self.assertEqual(result["stream_url"], "https://cdn.example.com/stream.m3u8")
        self.assertEqual(result["format"], "hls")

    def test_extract_subtitles(self) -> None:
        """Verifica l'estrazione delle tracce sottotitoli WebVTT dal player."""
        html_page = """
        <video id="player">
            <source src="https://cdn.example.com/video.mp4" type="video/mp4">
            <track kind="subtitles" src="/subtitles/ita.vtt" srclang="it" label="Italiano" default>
        </video>
        """
        mock_resp = MagicMock()
        mock_resp.text = html_page
        mock_resp.url = "https://embed.example.com/watch"
        self.mock_session.get.return_value = mock_resp

        result = self.resolver.resolve("https://embed.example.com/watch")
        self.assertEqual(len(result["subtitles"]), 1)
        self.assertEqual(result["subtitles"][0]["lang"], "it")
        self.assertEqual(result["subtitles"][0]["url"], "https://embed.example.com/subtitles/ita.vtt")

    def test_sanitize_stream_url(self) -> None:
        """Verifica la corretta sanitizzazione di caratteri speciali (& e spazi) nel path."""
        from ani_it.resolver import sanitize_stream_url

        raw_url = "https://cdn.example.com/Anime/Panty&Stocking/Panty&Stocking Ep 04.mp4?token=abc&exp=123"
        sanitized = sanitize_stream_url(raw_url)
        self.assertIn("Panty%26Stocking", sanitized)
        self.assertIn("Ep%2004.mp4", sanitized)
        self.assertIn("token=abc&exp=123", sanitized)

    def test_direct_https_stream_is_not_probed_or_downgraded(self) -> None:
        """Nessun downgrade silenzioso a HTTP e nessuna richiesta di 'verifica' allo stream."""
        result = self.resolver.resolve("https://animessvserverPP.online/am/951/video.mp4")
        self.assertEqual(result["stream_url"], "https://animessvserverPP.online/am/951/video.mp4")
        self.mock_session.get.assert_not_called()

    def test_embed_resolution_does_not_probe_the_stream_url(self) -> None:
        """Per un embed si fa una sola GET (la pagina player): niente sonde extra sullo stream."""
        resp = MagicMock()
        resp.text = '<script>file: "https://cdn.example.com/stream.m3u8"</script>'
        resp.url = "https://embed.example.com/p/1"
        self.mock_session.get.return_value = resp

        result = self.resolver.resolve("https://embed.example.com/p/1")
        self.assertEqual(self.mock_session.get.call_count, 1)
        self.assertTrue(result["stream_url"].startswith("https://"))

    def test_resolve_embed_url_endpoint(self) -> None:
        """Verifica che un endpoint embed-url venga risolto interrogando l'API e poi il player."""
        # 1. Chiamata a /embed-url/37448 -> restituisce link vixcloud
        mock_embed_api_resp = MagicMock()
        mock_embed_api_resp.status_code = 200
        mock_embed_api_resp.text = "https://vixcloud.co/embed/82235?token=xyz"

        # 2. Chiamata al player vixcloud -> restituisce HTML con masterUrl
        mock_player_resp = MagicMock()
        mock_player_resp.status_code = 200
        mock_player_resp.text = '<html><script>file: "https://cdn.vix.net/stream.m3u8"</script></html>'
        mock_player_resp.url = "https://vixcloud.co/embed/82235?token=xyz"

        self.mock_session.get.side_effect = [mock_embed_api_resp, mock_player_resp]

        result = self.resolver.resolve("https://www.animeunity.so/embed-url/37448")
        self.assertEqual(result["stream_url"], "https://cdn.vix.net/stream.m3u8")
        self.assertEqual(result["format"], "hls")

    def test_numeric_link_fallback_keeps_original_id(self) -> None:
        """Se embed-url fallisce, il fallback deve usare l'ID originale: /embed-pc/123."""
        from ani_it.constants import BASE_URL

        player_resp = MagicMock()
        player_resp.text = '<script>file: "https://cdn.example.com/stream.m3u8"</script>'
        player_resp.url = f"{BASE_URL}/embed-pc/123"
        calls: list[str] = []

        def fake_get(url: str, **kwargs: object) -> MagicMock:
            calls.append(url)
            if len(calls) == 1:
                raise requests.exceptions.ConnectionError("embed-url irraggiungibile")
            return player_resp

        self.mock_session.get.side_effect = fake_get

        self.resolver.resolve("123")
        self.assertEqual(calls[0], f"{BASE_URL}/embed-url/123")
        self.assertEqual(calls[1], f"{BASE_URL}/embed-pc/123")


# Struttura ridotta della pagina embed reale di Vixcloud (verificata su vixcloud.co):
# lo stream HLS NON compare con estensione .m3u8, mentre window.downloadUrl espone un MP4.
VIXCLOUD_EMBED = """<html><body>
<script>
    window.video = { id: '365857', filename: '', timestamps: [], };
    window.streams = [{"name":"Server1","active":true,"url":"https:\\/\\/vixcloud.co\\/playlist\\/365857?ub=1"}];
    window.masterPlaylist = {
        params: {
            'token': 'abc123def456',
            'expires': '1796403287',
            'asn': '',
        },
        url: 'https://vixcloud.co/playlist/365857',
    }
    window.canPlayFHD = %s
    window.thumbnailsUrl = 'https://au-u2-01.vix-content.net/hls/10/thumbnails/thumbnails.vtt'
</script>
<script>
    window.downloadUrl = 'https://au-d1-04.vix-content.net/download/3/8/1080p.mp4?token=Kz2W&expires=1791305695&filename=Ep_01.mp4'
</script>
</body></html>"""


class TestVixcloudMasterPlaylist(unittest.TestCase):
    """window.masterPlaylist va combinato in url?token=...&expires=...[&h=1]."""

    def setUp(self) -> None:
        self.mock_session = MagicMock(spec=requests.Session)
        self.mock_session.headers = {}
        self.resolver = StreamResolver(session=self.mock_session)

    def resolve_page(self, can_play_fhd: str) -> dict:
        resp = MagicMock()
        resp.text = VIXCLOUD_EMBED % can_play_fhd
        resp.url = "https://vixcloud.co/embed/365857?token=t&expires=1&canPlayFHD=1"
        self.mock_session.get.return_value = resp
        return self.resolver.resolve(resp.url)

    def test_master_playlist_is_preferred_over_download_mp4(self) -> None:
        from urllib.parse import parse_qs, urlsplit

        result = self.resolve_page("true")
        parts = urlsplit(result["stream_url"])
        self.assertEqual(result["format"], "hls")
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", "https://vixcloud.co/playlist/365857")
        query = parse_qs(parts.query)
        self.assertEqual(query["token"], ["abc123def456"])
        self.assertEqual(query["expires"], ["1796403287"])
        self.assertEqual(query["h"], ["1"])
        self.assertNotIn("asn", query)  # parametro vuoto: omesso
        self.assertNotIn(".mp4", result["stream_url"])

    def test_h_flag_only_when_can_play_fhd(self) -> None:
        from urllib.parse import parse_qs, urlsplit

        result = self.resolve_page("false")
        query = parse_qs(urlsplit(result["stream_url"]).query)
        self.assertNotIn("h", query)
        self.assertEqual(query["token"], ["abc123def456"])

    def test_extract_master_playlist_directly(self) -> None:
        url, fmt = self.resolver._extract_stream_url(VIXCLOUD_EMBED % "true", "https://vixcloud.co/embed/1")
        self.assertEqual(fmt, "hls")
        self.assertTrue(url.startswith("https://vixcloud.co/playlist/365857?"))

    def test_pages_without_master_playlist_still_use_generic_patterns(self) -> None:
        html_page = '<script>file: "https://cdn.example.com/stream.m3u8"</script>'
        url, fmt = self.resolver._extract_stream_url(html_page, "https://x.y/")
        self.assertEqual((url, fmt), ("https://cdn.example.com/stream.m3u8", "hls"))


MASTER_PLAYLIST = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=4500000,CODECS="avc1.640028,mp4a.40.2",RESOLUTION=1920x1080
https://vixcloud.co/playlist/1?type=video&rendition=1080p&token=a
#EXT-X-STREAM-INF:BANDWIDTH=1800000,CODECS="avc1.64001f,mp4a.40.2",RESOLUTION=1280x720
https://vixcloud.co/playlist/1?type=video&rendition=720p&token=b
#EXT-X-STREAM-INF:BANDWIDTH=1080000,CODECS="avc1.64001f,mp4a.40.2",RESOLUTION=854x480
https://vixcloud.co/playlist/1?type=video&rendition=480p&token=c
"""

MASTER_WITH_SEPARATE_AUDIO = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="ita",DEFAULT=YES,URI="audio_ita.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080,AUDIO="aud"
video_1080.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=1280x720,AUDIO="aud"
video_720.m3u8
"""


class TestStreamQuality(unittest.TestCase):
    """La qualità si sceglie con le opzioni di mpv/yt-dlp: il master resta sempre intatto."""

    def setUp(self) -> None:
        self.mock_session = MagicMock(spec=requests.Session)
        self.mock_session.headers = {}
        self.resolver = StreamResolver(session=self.mock_session)

    def serve_playlist(self, text: str) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.text = text
        self.mock_session.get.return_value = resp

    def test_master_url_is_kept_even_with_query_string(self) -> None:
        """`.m3u8?token=...` non deve più far saltare la scelta di qualità né sostituire il master."""
        url = "https://cdn.example.com/master.m3u8?token=xyz"
        self.serve_playlist(MASTER_PLAYLIST)
        result = self.resolver.resolve(url, quality="720p")
        self.assertEqual(result["stream_url"], url)
        self.assertEqual(result["hls_bitrate"], 1800000)

    def test_quality_applies_to_extensionless_vixcloud_playlist(self) -> None:
        page = MagicMock()
        page.text = VIXCLOUD_EMBED % "true"
        page.url = "https://vixcloud.co/embed/365857?token=t&expires=1"
        playlist = MagicMock(status_code=200, text=MASTER_PLAYLIST)
        self.mock_session.get.side_effect = [page, playlist]

        result = self.resolver.resolve(page.url, quality="480p")
        self.assertTrue(result["stream_url"].startswith("https://vixcloud.co/playlist/365857?"))
        self.assertEqual(result["hls_bitrate"], 1080000)

    def test_best_does_not_fetch_playlist_nor_set_bitrate(self) -> None:
        result = self.resolver.resolve("https://cdn.example.com/master.m3u8?token=xyz", quality="best")
        self.assertIsNone(result["hls_bitrate"])
        self.mock_session.get.assert_not_called()

    def test_picks_highest_variant_not_above_requested_height(self) -> None:
        self.serve_playlist(MASTER_PLAYLIST)
        result = self.resolver.resolve("https://cdn.example.com/m.m3u8", quality="900p")
        self.assertEqual(result["hls_bitrate"], 1800000)  # 720p: la più alta non oltre 900

    def test_falls_back_to_lowest_when_all_variants_exceed_request(self) -> None:
        self.serve_playlist(MASTER_PLAYLIST)
        result = self.resolver.resolve("https://cdn.example.com/m.m3u8", quality="240p")
        self.assertEqual(result["hls_bitrate"], 1080000)

    def test_separate_audio_master_is_never_replaced_by_a_variant(self) -> None:
        """Con tracce audio separate (EXT-X-MEDIA) una variante da sola darebbe video muto."""
        self.serve_playlist(MASTER_WITH_SEPARATE_AUDIO)
        url = "https://cdn.example.com/master.m3u8?token=1"
        result = self.resolver.resolve(url, quality="720p")
        self.assertEqual(result["stream_url"], url)
        self.assertEqual(result["hls_bitrate"], 900000)

    def test_unparseable_playlist_leaves_bitrate_unset(self) -> None:
        self.serve_playlist("<html>not a playlist</html>")
        result = self.resolver.resolve("https://cdn.example.com/m.m3u8", quality="720p")
        self.assertIsNone(result["hls_bitrate"])

    def test_playlist_fetch_error_does_not_break_resolution(self) -> None:
        self.mock_session.get.side_effect = requests.exceptions.ConnectionError("down")
        result = self.resolver.resolve("https://cdn.example.com/m.m3u8", quality="720p")
        self.assertEqual(result["stream_url"], "https://cdn.example.com/m.m3u8")
        self.assertIsNone(result["hls_bitrate"])

    def test_mp4_links_have_no_bitrate(self) -> None:
        result = self.resolver.resolve("https://cdn.example.com/v.mp4", quality="720p")
        self.assertIsNone(result["hls_bitrate"])
        self.mock_session.get.assert_not_called()


class TestResolverDomain(unittest.TestCase):
    """Il dominio di AnimeUnity è configurabile anche nel resolver."""

    def setUp(self) -> None:
        self.mock_session = MagicMock(spec=requests.Session)
        self.mock_session.headers = {}
        self.resolver = StreamResolver(session=self.mock_session, base_url="https://www.animeunity.to/")

    def test_numeric_id_uses_configured_domain(self) -> None:
        calls: list[str] = []
        player = MagicMock(text='<script>file: "https://cdn.example.com/s.m3u8"</script>', url="https://x/p")

        def fake_get(url: str, **kwargs: object) -> MagicMock:
            calls.append(url)
            if len(calls) == 1:
                raise requests.exceptions.ConnectionError("x")
            return player

        self.mock_session.get.side_effect = fake_get
        self.resolver.resolve("123")
        self.assertEqual(calls[0], "https://www.animeunity.to/embed-url/123")
        self.assertEqual(calls[1], "https://www.animeunity.to/embed-pc/123")

    def test_referer_and_origin_follow_configured_domain(self) -> None:
        result = self.resolver.resolve("https://cdn.example.com/v.mp4")
        self.assertEqual(result["headers"]["Referer"], "https://www.animeunity.to")
        self.assertEqual(result["headers"]["Origin"], "https://www.animeunity.to")

    def test_session_referer_follows_configured_domain(self) -> None:
        self.assertEqual(self.mock_session.headers["Referer"], "https://www.animeunity.to")

    def test_default_domain_is_unchanged(self) -> None:
        default = StreamResolver(session=self.mock_session)
        self.assertEqual(default.base_url, "https://www.animeunity.so")


class TestStreamUrlSanitizing(unittest.TestCase):
    """quote(unquote(path)) alterava gli URL firmati con path già codificati (%2F)."""

    def test_encoded_slash_in_signed_path_is_preserved(self) -> None:
        from ani_it.resolver import sanitize_stream_url

        url = "https://cdn.example.com/v/abc%2Fdef/video.mp4?token=t&exp=1"
        self.assertEqual(sanitize_stream_url(url), url)

    def test_existing_escapes_are_not_double_encoded(self) -> None:
        from ani_it.resolver import sanitize_stream_url

        url = "https://cdn.example.com/a%20b/c%26d.mp4"
        self.assertEqual(sanitize_stream_url(url), url)

    def test_raw_special_characters_are_still_encoded(self) -> None:
        from ani_it.resolver import sanitize_stream_url

        out = sanitize_stream_url("https://cdn.example.com/Panty&Stocking/Ep 04.mp4?x=1&y=2")
        self.assertEqual(out, "https://cdn.example.com/Panty%26Stocking/Ep%2004.mp4?x=1&y=2")

    def test_lone_percent_sign_is_encoded(self) -> None:
        from ani_it.resolver import sanitize_stream_url

        out = sanitize_stream_url("https://cdn.example.com/100%/video.mp4")
        self.assertEqual(out, "https://cdn.example.com/100%25/video.mp4")

    def test_mixed_encoded_and_raw(self) -> None:
        from ani_it.resolver import sanitize_stream_url

        out = sanitize_stream_url("https://cdn.example.com/a%2Fb c/d.mp4")
        self.assertEqual(out, "https://cdn.example.com/a%2Fb%20c/d.mp4")

    def test_escaped_json_slashes_are_unescaped(self) -> None:
        from ani_it.resolver import sanitize_stream_url

        self.assertEqual(sanitize_stream_url(r"https:\/\/cdn.example.com\/v.mp4"), "https://cdn.example.com/v.mp4")


class TestDeadDirectLinks(unittest.TestCase):
    """Alcuni film hanno link diretti verso host morti (es. forbiddenlol.cloud, né 80 né 443):
    lo stesso episodio è raggiungibile da /embed-url/{id}."""

    DEAD = "https://www.forbiddenlol.cloud/DDL/ANIME/KimiNoNaWa_Movie_SUB_ITA.mp4"

    def setUp(self) -> None:
        self.mock_session = MagicMock(spec=requests.Session)
        self.mock_session.headers = {}
        self.resolver = StreamResolver(session=self.mock_session)

    def serve_embed(self) -> None:
        embed_api = MagicMock(status_code=200, text="https://vixcloud.co/embed/156703?token=t&expires=1&canPlayFHD=1")
        page = MagicMock(text=VIXCLOUD_EMBED % "true", url="https://vixcloud.co/embed/156703?token=t&expires=1")
        self.mock_session.get.side_effect = [embed_api, page]

    def test_unreachable_direct_link_falls_back_to_embed_url(self) -> None:
        self.mock_session.head.side_effect = requests.exceptions.ConnectionError("Connection refused")
        self.serve_embed()

        result = self.resolver.resolve(self.DEAD, episode_id=56614)
        self.assertEqual(self.mock_session.get.call_args_list[0].args[0], "https://www.animeunity.so/embed-url/56614")
        self.assertTrue(result["stream_url"].startswith("https://vixcloud.co/playlist/"))
        self.assertEqual(result["format"], "hls")

    def test_timeout_also_triggers_the_fallback(self) -> None:
        self.mock_session.head.side_effect = requests.exceptions.ConnectTimeout("slow")
        self.serve_embed()
        result = self.resolver.resolve(self.DEAD, episode_id=56614)
        self.assertEqual(result["format"], "hls")

    def test_reachable_direct_link_is_used_as_is(self) -> None:
        self.mock_session.head.return_value = MagicMock(status_code=200)
        result = self.resolver.resolve(self.DEAD, episode_id=56614)
        self.assertEqual(result["stream_url"], self.DEAD)
        self.mock_session.get.assert_not_called()

    def test_http_error_status_still_means_the_host_is_alive(self) -> None:
        """403/405 sulla HEAD: il server risponde, non è un host morto."""
        self.mock_session.head.return_value = MagicMock(status_code=405)
        result = self.resolver.resolve(self.DEAD, episode_id=56614)
        self.assertEqual(result["stream_url"], self.DEAD)

    def test_probe_response_is_closed(self) -> None:
        response = MagicMock(status_code=200)
        self.mock_session.head.return_value = response
        self.resolver.resolve(self.DEAD, episode_id=56614)
        response.close.assert_called_once()

    def test_probe_has_a_short_timeout_and_never_changes_the_protocol(self) -> None:
        self.mock_session.head.side_effect = requests.exceptions.ConnectionError("x")
        self.mock_session.get.side_effect = requests.exceptions.ConnectionError("embed-url down")
        result = self.resolver.resolve(self.DEAD, episode_id=56614)
        self.assertLessEqual(self.mock_session.head.call_args.kwargs["timeout"], 5)
        self.assertEqual(self.mock_session.head.call_args.args[0], self.DEAD)  # solo https, mai http
        self.assertTrue(result["stream_url"].startswith("https://"))

    def test_without_episode_id_there_is_no_probe(self) -> None:
        result = self.resolver.resolve(self.DEAD)
        self.mock_session.head.assert_not_called()
        self.assertEqual(result["stream_url"], self.DEAD)

    def test_if_embed_url_fails_too_the_direct_link_is_kept(self) -> None:
        self.mock_session.head.side_effect = requests.exceptions.ConnectionError("x")
        self.mock_session.get.side_effect = requests.exceptions.ConnectionError("embed-url down")
        result = self.resolver.resolve(self.DEAD, episode_id=56614)
        self.assertEqual(result["stream_url"], self.DEAD)

    def test_hls_links_are_not_probed(self) -> None:
        self.resolver.resolve("https://cdn.example.com/master.m3u8?token=1", episode_id=5)
        self.mock_session.head.assert_not_called()


if __name__ == "__main__":
    unittest.main()
