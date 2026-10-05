"""Risolutore di flussi video per AnimeUnity (embed vixcloud, player interno, HLS m3u8 e MP4)."""

import html
import logging
import re
from typing import Any, Optional
import urllib.parse
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit

import requests

from ani_it.constants import BASE_URL, DEFAULT_USER_AGENT, FORMAT_HLS, FORMAT_MP4
from ani_it.utils import unpack_js

logger = logging.getLogger("ani_it.resolver")


def sanitize_stream_url(url: str) -> str:
    """Sanifica e codifica i caratteri speciali nel path dell'URL (es. spazi, '&').
    
    Preserva scheme, host, porta, query string e frammenti, codificando solo i caratteri
    del path che potrebbero causare errori con web server, CDN o downloader esterni.
    """
    if not url:
        return ""
    url = url.strip().replace(r"\/", "/")
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url

    # Quote path preservando i separatori validi (/ : @) e codificando '&' o spazi se presenti nel filename
    clean_path = quote(unquote(parsed.path), safe="/:@!$'()*+,;=")
    clean_query = parsed.query.strip()
    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        clean_path,
        clean_query,
        parsed.fragment,
    ))


def verify_and_fallback_protocol(url: str, session: Optional[requests.Session] = None) -> str:
    """Verifica la raggiungibilità del server multimediale su HTTPS (porta 443).
    
    Se la connessione viene rifiutata (Errno 111 Connection Refused) o fallisce con SSLError,
    esegue il downgrade immediato a HTTP (porta 80).
    """
    if not url.startswith("https://"):
        return url

    sess = session or requests.Session()
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Range": "bytes=0-0"}

    try:
        sess.get(url, headers=headers, timeout=2.5, stream=True, allow_redirects=True)
        return url
    except (requests.exceptions.SSLError, requests.exceptions.ConnectionError):
        # Il server non ha un listener TLS su 443 o la porta è chiusa
        http_url = "http://" + url[len("https://"):]
        try:
            sess.get(http_url, headers=headers, timeout=2.5, stream=True, allow_redirects=True)
            logger.info("Protocol downgrade riuscito: %s -> %s", url, http_url)
            return http_url
        except Exception:
            return url
    except Exception:
        return url


class StreamResolver:
    """Estrae l'URL sorgente del flusso multimediale (HLS .m3u8 o MP4) da embed e player."""

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": BASE_URL,
        })

    def resolve(
        self,
        episode_link: str,
        quality: str = "best",
        episode_id: Optional[str | int] = None,
    ) -> dict[str, Any]:
        """Risolve un link episodio in un flusso riproducibile.

        Args:
            episode_link: URL del player embed, endpoint embed-url o link diretto al file/stream.
            quality: Risoluzione desiderata ('1080p', '720p', '480p', 'best').
            episode_id: ID univoco dell'episodio nel DB AnimeUnity per fallback automatico.

        Returns:
            Dizionario con 'stream_url', 'headers', 'format', 'subtitles'.
        """
        link = episode_link.strip()

        # 1. Se il link è un endpoint embed-url di AnimeUnity (/embed-url/{id})
        if "/embed-url/" in link:
            try:
                resp = self.session.get(link, timeout=10)
                if resp.status_code == 200 and resp.text.strip().startswith("http"):
                    link = resp.text.strip()
            except Exception as exc:
                logger.debug("Tentativo recupero endpoint embed-url fallito: %s", exc)

        # 2. Se il link è già un flusso diretto .m3u8, .mp4 o .mkv
        is_hls = link.endswith(".m3u8") or ".m3u8?" in link
        is_direct_video = link.endswith((".mp4", ".mkv")) or ".mp4?" in link or ".mkv?" in link

        if is_hls or is_direct_video:
            clean_url = sanitize_stream_url(link)
            verified_url = verify_and_fallback_protocol(clean_url, self.session)
            fmt = FORMAT_HLS if is_hls else FORMAT_MP4
            return self._build_result(verified_url, fmt, referer=BASE_URL)

        # 3. Se è un path relativo (es. /embed-pc/123 o /embed/123)
        if link.startswith("/"):
            link = urljoin(BASE_URL, link)

        # Se il link è solo un ID numerico o scws_id
        if link.isdigit():
            link = f"{BASE_URL}/embed-url/{link}"
            try:
                resp = self.session.get(link, timeout=10)
                if resp.status_code == 200 and resp.text.strip().startswith("http"):
                    link = resp.text.strip()
            except Exception:
                link = f"{BASE_URL}/embed-pc/{link}"

        # 4. Effettua richiesta GET al player embed
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": BASE_URL,
            "Origin": BASE_URL,
        }

        try:
            resp = self.session.get(link, headers=headers, timeout=12, allow_redirects=True)
            content = resp.text
            current_url = resp.url
        except Exception as exc:
            # Se la richiesta al player fallisce e abbiamo episode_id, tenta endpoint embed-url
            if episode_id:
                fb_endpoint = f"{BASE_URL}/embed-url/{episode_id}"
                try:
                    fb_resp = self.session.get(fb_endpoint, timeout=10)
                    if fb_resp.status_code == 200 and fb_resp.text.strip().startswith("http"):
                        return self.resolve(fb_resp.text.strip(), quality=quality)
                except Exception:
                    pass
            raise RuntimeError(f"Errore durante l'accesso al player ({link}): {exc}") from exc

        # 5. Deoffuscamento se presente JS Packed
        if "eval(function(p,a,c,k,e,d)" in content:
            unpacked = unpack_js(content)
            content = f"{content}\n{unpacked}"

        # 6. Ricerca pattern stream nel markup / script
        stream_url, fmt = self._extract_stream_url(content, current_url)

        # Se non trovato direttamente, cerca se c'è un iframe incorporato
        if not stream_url:
            iframe_match = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', content, re.IGNORECASE)
            if iframe_match:
                sub_embed = iframe_match.group(1)
                sub_embed = urljoin(current_url, sub_embed)
                try:
                    sub_resp = self.session.get(
                        sub_embed,
                        headers={"Referer": current_url, "User-Agent": DEFAULT_USER_AGENT},
                        timeout=10,
                    )
                    sub_content = sub_resp.text
                    if "eval(function(p,a,c,k,e,d)" in sub_content:
                        sub_content += "\n" + unpack_js(sub_content)
                    stream_url, fmt = self._extract_stream_url(sub_content, sub_resp.url)
                    current_url = sub_resp.url
                except Exception:
                    pass

        # Fallback a embed-url se l'estrazione non ha prodotto stream e c'è episode_id
        if not stream_url and episode_id:
            fb_endpoint = f"{BASE_URL}/embed-url/{episode_id}"
            try:
                fb_resp = self.session.get(fb_endpoint, timeout=10)
                if fb_resp.status_code == 200 and fb_resp.text.strip().startswith("http"):
                    return self.resolve(fb_resp.text.strip(), quality=quality)
            except Exception:
                pass

        if not stream_url:
            raise ValueError(f"Impossibile estrarre lo stream video dalla pagina player: {link}")

        # 7. Estrazione eventuali tracce sottotitoli
        subtitles = self._extract_subtitles(content, current_url)

        # 8. Risoluzione della qualità se è un master m3u8 con varianti
        final_stream_url = self._select_stream_quality(stream_url, current_url, quality)

        return self._build_result(final_stream_url, fmt, referer=current_url, subtitles=subtitles)

    def _extract_stream_url(self, content: str, base_url: str) -> tuple[Optional[str], str]:
        """Scansiona il contenuto HTML/JS per identificare flussi .m3u8 o .mp4."""
        # Pattern HLS .m3u8
        hls_patterns = [
            r'masterUrl\s*=\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'window\.masterUrl\s*=\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'file:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'source\s*=\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'src:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'url:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'video_url\s*=\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'<source[^>]+src=["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'["\'](/[^"\']+\.m3u8[^"\']*)["\']',
        ]

        for pat in hls_patterns:
            matches = re.finditer(pat, content, re.IGNORECASE)
            for m in matches:
                url = m.group(1).replace(r"\/", "/")
                url = html.unescape(url)
                if not url.startswith("http"):
                    url = urljoin(base_url, url)
                return url, FORMAT_HLS

        # Pattern MP4
        mp4_patterns = [
            r'file:\s*["\'](https?://[^"\']+\.mp4[^"\']*)["\']',
            r'source\s*=\s*["\'](https?://[^"\']+\.mp4[^"\']*)["\']',
            r'src:\s*["\'](https?://[^"\']+\.mp4[^"\']*)["\']',
            r'<source[^>]+src=["\'](https?://[^"\']+\.mp4[^"\']*)["\']',
            r'["\'](https?://[^"\']+\.mp4[^"\']*)["\']',
        ]

        for pat in mp4_patterns:
            matches = re.finditer(pat, content, re.IGNORECASE)
            for m in matches:
                url = m.group(1).replace(r"\/", "/")
                url = html.unescape(url)
                if not url.startswith("http"):
                    url = urljoin(base_url, url)
                return url, FORMAT_MP4

        return None, FORMAT_HLS

    def _extract_subtitles(self, content: str, base_url: str) -> list[dict[str, Any]]:
        """Estrae tracce di sottotitoli (.vtt, .ass) presenti nel player."""
        subtitles: list[dict[str, Any]] = []
        track_pattern = re.compile(
            r'<track[^>]+src=["\']([^"\']+\.(?:vtt|ass|srt)[^"\']*)["\'][^>]*>',
            re.IGNORECASE,
        )
        for match in track_pattern.finditer(content):
            tag = match.group(0)
            src = match.group(1)
            src = urljoin(base_url, src)

            # Estrai label o lingua
            lang_match = re.search(r'srclang=["\']([^"\']+)["\']', tag, re.I)
            label_match = re.search(r'label=["\']([^"\']+)["\']', tag, re.I)

            lang = lang_match.group(1) if lang_match else "it"
            label = label_match.group(1) if label_match else "Italiano"

            subtitles.append({"url": src, "lang": lang, "label": label})

        return subtitles

    def _select_stream_quality(self, stream_url: str, referer: str, requested_quality: str) -> str:
        """Se lo stream è un playlist master m3u8, seleziona la variante con la risoluzione richiesta."""
        if requested_quality == "best" or not stream_url.endswith(".m3u8"):
            return stream_url

        try:
            resp = self.session.get(
                stream_url,
                headers={"Referer": referer, "User-Agent": DEFAULT_USER_AGENT},
                timeout=5,
            )
            playlist = resp.text
            if "#EXT-X-STREAM-INF" not in playlist:
                return stream_url

            # Parsing delle varianti m3u8
            lines = playlist.splitlines()
            variants: list[tuple[int, str]] = []  # (resolution_height, url)
            current_height = 0

            for line in lines:
                line = line.strip()
                if line.startswith("#EXT-X-STREAM-INF"):
                    res_match = re.search(r"RESOLUTION=\d+x(\d+)", line)
                    if res_match:
                        current_height = int(res_match.group(1))
                    else:
                        current_height = 0
                elif line and not line.startswith("#"):
                    variant_url = urljoin(stream_url, line)
                    variants.append((current_height, variant_url))

            if not variants:
                return stream_url

            # Cerca corrispondenza per la qualità richiesta (es. '1080p' -> 1080)
            target_h = int(re.sub(r"\D", "", requested_quality)) if re.search(r"\d+", requested_quality) else 1080
            # Ordina per distanza dalla qualità richiesta
            variants.sort(key=lambda item: abs(item[0] - target_h) if item[0] > 0 else 9999)
            return variants[0][1]

        except Exception:
            return stream_url

    def _build_result(
        self,
        stream_url: str,
        fmt: str,
        referer: str,
        subtitles: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Costruisce il payload finale strutturato con gli header indispensabili per la riproduzione."""
        clean_url = sanitize_stream_url(stream_url)
        clean_url = verify_and_fallback_protocol(clean_url, self.session)
        domain = urlparse(referer).netloc or "www.animeunity.so"
        origin = f"https://{domain}"

        return {
            "stream_url": clean_url,
            "headers": {
                "User-Agent": DEFAULT_USER_AGENT,
                "Referer": referer,
                "Origin": origin,
            },
            "format": fmt,
            "subtitles": subtitles or [],
        }
