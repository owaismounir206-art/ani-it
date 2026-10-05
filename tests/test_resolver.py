"""Test unitari per StreamResolver e il deoffuscatore JavaScript."""

import unittest
from unittest.mock import MagicMock, patch

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

    def test_protocol_fallback_on_connection_refused(self) -> None:
        """Verifica che un URL HTTPS con porta 443 non disponibile passi a HTTP porta 80."""
        from ani_it.resolver import verify_and_fallback_protocol

        mock_sess = MagicMock(spec=requests.Session)
        # La prima chiamata a HTTPS solleva ConnectionError (porta 443 chiusa)
        # La seconda chiamata a HTTP risponde con successo
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_sess.get.side_effect = [
            requests.exceptions.ConnectionError("Connection refused"),
            mock_ok,
        ]

        https_url = "https://animessvserverPP.online/am/951/video.mp4"
        fallback_url = verify_and_fallback_protocol(https_url, session=mock_sess)
        self.assertEqual(fallback_url, "http://animessvserverPP.online/am/951/video.mp4")

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


if __name__ == "__main__":
    unittest.main()
