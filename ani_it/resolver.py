"""Risolutore di flussi video per AnimeUnity (embed Vixcloud, player interno, HLS m3u8 e MP4) v2.0."""

import html
import logging
import re
from typing import Any, Optional
from urllib.parse import quote, urlencode, urljoin, urlparse, urlsplit, urlunsplit

import requests

from ani_it.constants import (
    DEFAULT_BASE_URL,
    DEFAULT_USER_AGENT,
    FORMAT_HLS,
    FORMAT_MP4,
    HTTP_TIMEOUT,
    Endpoints,
)
from ani_it.utils import is_safe_url, quality_height, unpack_js

logger = logging.getLogger("ani_it.resolver")

# Caratteri validi nel path che non vanno codificati; '%' resta per non toccare le sequenze già codificate
_PATH_SAFE = "/:@!$'()*+,;=%"


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

    path = re.sub(r"%(?![0-9A-Fa-f]{2})", "%25", parsed.path)
    clean_path = quote(path, safe=_PATH_SAFE)
    clean_query = parsed.query.strip()
    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        clean_path,
        clean_query,
        parsed.fragment,
    ))


class StreamResolver:
    """Estrae l'URL sorgente del flusso multimediale (HLS .m3u8 o MP4) da embed e player."""

    def __init__(self, session: Optional[requests.Session] = None, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.endpoints = Endpoints(self.base_url)
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": self.base_url,
        })

    def resolve(
        self,
        episode_link: str,
        quality: str = "best",
        episode_id: Optional[str | int] = None,
    ) -> dict[str, Any]:
        """Risolve un link episodio in un flusso riproducibile."""
        link = episode_link.strip()

        # 1. Se il link è un endpoint embed-url di AnimeUnity (/embed-url/{id})
        if "/embed-url/" in link:
            try:
                resp = self.session.get(link, timeout=HTTP_TIMEOUT)
                if resp.status_code == 200 and resp.text.strip().startswith("http"):
                    link = resp.text.strip()
            except Exception as exc:
                logger.debug("Tentativo recupero endpoint embed-url fallito: %s", exc)

        # 2. Se il link è già un flusso diretto .m3u8, .mp4 o .mkv
        is_hls = link.endswith(".m3u8") or ".m3u8?" in link
        is_direct_video = link.endswith((".mp4", ".mkv")) or ".mp4?" in link or ".mkv?" in link

        if is_hls or is_direct_video:
            clean_url = sanitize_stream_url(link)
            if not is_safe_url(clean_url):
                raise ValueError(f"Schema URL non autorizzato o non sicuro: {clean_url}")

            # Controllo host per link diretti: se la porta 443 rifiuta la connessione, prova fallback HTTP porta 80
            if is_direct_video and episode_id and not self._host_is_reachable(clean_url):
                # Fallback su porta 80 per CDN statiche note
                if clean_url.startswith("https://") and any(d in clean_url.lower() for d in ("animessvserver", "animeunityserver")):
                    http_fallback = "http://" + clean_url[len("https://"):]
                    if self._host_is_reachable(http_fallback):
                        parsed = urlsplit(clean_url)
                        logger.info("Fallback controllato a HTTP (porta 80) per CDN statica: %s", parsed.netloc)
                        clean_url = http_fallback

                if not self._host_is_reachable(clean_url):
                    alternative = self._resolve_via_embed_url(episode_id, quality)
                    if alternative is not None:
                        return alternative

            fmt = FORMAT_HLS if is_hls else FORMAT_MP4
            bitrate = self._hls_bitrate_for_quality(clean_url, self.base_url, quality) if is_hls else None
            return self._build_result(clean_url, fmt, referer=self.base_url, hls_bitrate=bitrate)

        # 3. Se è un path relativo (es. /embed-pc/123 o /embed/123)
        if link.startswith("/"):
            link = urljoin(self.base_url + "/", link)

        # Se il link è solo un ID numerico
        if link.isdigit():
            numeric_id = link
            link = f"{self.endpoints.embed_url}/{numeric_id}"
            try:
                resp = self.session.get(link, timeout=HTTP_TIMEOUT)
                if resp.status_code == 200 and resp.text.strip().startswith("http"):
                    link = resp.text.strip()
            except Exception:
                link = f"{self.endpoints.embed_pc}/{numeric_id}"

        # 4. Richiesta GET al player embed
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": self.base_url,
            "Origin": self.base_url,
        }

        try:
            resp = self.session.get(link, headers=headers, timeout=HTTP_TIMEOUT, allow_redirects=True)
            content = resp.text
            current_url = resp.url
        except Exception as exc:
            if episode_id:
                fb_endpoint = f"{self.endpoints.embed_url}/{episode_id}"
                try:
                    fb_resp = self.session.get(fb_endpoint, timeout=HTTP_TIMEOUT)
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
                        timeout=HTTP_TIMEOUT,
                    )
                    sub_content = sub_resp.text
                    if "eval(function(p,a,c,k,e,d)" in sub_content:
                        sub_content += "\n" + unpack_js(sub_content)
                    stream_url, fmt = self._extract_stream_url(sub_content, sub_resp.url)
                    current_url = sub_resp.url
                except Exception:
                    pass

        # Fallback a embed-url se l'estrazione non ha prodotto stream
        if not stream_url and episode_id:
            fb_endpoint = f"{self.endpoints.embed_url}/{episode_id}"
            try:
                fb_resp = self.session.get(fb_endpoint, timeout=HTTP_TIMEOUT)
                if fb_resp.status_code == 200 and fb_resp.text.strip().startswith("http"):
                    return self.resolve(fb_resp.text.strip(), quality=quality)
            except Exception:
                pass

        if not stream_url:
            raise ValueError(f"Impossibile estrarre lo stream video dalla pagina player: {link}")

        # Validazione rigida dello schema dell'URL estratto
        clean_stream = sanitize_stream_url(stream_url)
        if not is_safe_url(clean_stream):
            raise ValueError(f"Schema non autorizzato o URL stream non sicuro: {clean_stream}")

        # 7. Estrazione eventuali tracce sottotitoli
        subtitles = self._extract_subtitles(content, current_url)

        # 8. Qualità HLS
        bitrate = None
        if fmt == FORMAT_HLS:
            bitrate = self._hls_bitrate_for_quality(clean_stream, current_url, quality)

        return self._build_result(clean_stream, fmt, referer=current_url, subtitles=subtitles, hls_bitrate=bitrate)

    def _host_is_reachable(self, url: str) -> bool:
        """True se l'host risponde (con qualsiasi stato HTTP); False se la connessione fallisce."""
        try:
            response = self.session.head(url, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=4, allow_redirects=True)
            response.close()
            return True
        except requests.exceptions.RequestException as exc:
            logger.debug("Host non raggiungibile (%s): %s", url, exc)
            return False

    def _resolve_via_embed_url(self, episode_id: str | int, quality: str) -> Optional[dict[str, Any]]:
        """Risolve un episodio dal suo endpoint embed-url."""
        try:
            resp = self.session.get(f"{self.endpoints.embed_url}/{episode_id}", timeout=HTTP_TIMEOUT)
            if resp.status_code == 200 and resp.text.strip().startswith("http"):
                return self.resolve(resp.text.strip(), quality=quality)
        except Exception as exc:
            logger.debug("Fallback embed-url fallito per l'episodio %s: %s", episode_id, exc)
        return None

    @staticmethod
    def _extract_master_playlist(content: str, base_url: str) -> Optional[str]:
        """Ricostruisce l'URL HLS dal blocco `window.masterPlaylist` degli embed Vixcloud."""
        start = re.search(r"window\.masterPlaylist\s*=\s*\{", content)
        if not start:
            return None

        end = content.find("window.", start.end())
        block = content[start.end():end if end != -1 else None]

        url_match = re.search(r"\burl\s*:\s*['\"]([^'\"]+)['\"]", block)
        if not url_match:
            return None
        base = html.unescape(url_match.group(1).replace(r"\/", "/")).strip()
        if not base.startswith("http"):
            base = urljoin(base_url, base)

        params: dict[str, str] = {}
        params_match = re.search(r"\bparams\s*:\s*\{(.*?)\}", block, re.DOTALL)
        if params_match:
            for key, value in re.findall(r"['\"]?(\w+)['\"]?\s*:\s*['\"]([^'\"]*)['\"]", params_match.group(1)):
                if value:
                    params[key] = value

        if re.search(r"window\.canPlayFHD\s*=\s*true", content):
            params["h"] = "1"

        if not params:
            return base
        return base + ("&" if "?" in base else "?") + urlencode(params)

    def _extract_stream_url(self, content: str, base_url: str) -> tuple[Optional[str], str]:
        """Scansiona il contenuto HTML/JS per identificare flussi .m3u8 o .mp4."""
        master_playlist = self._extract_master_playlist(content, base_url)
        if master_playlist:
            return master_playlist, FORMAT_HLS

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

            lang_match = re.search(r'srclang=["\']([^"\']+)["\']', tag, re.I)
            label_match = re.search(r'label=["\']([^"\']+)["\']', tag, re.I)

            lang = lang_match.group(1) if lang_match else "it"
            label = label_match.group(1) if label_match else "Italiano"

            subtitles.append({"url": src, "lang": lang, "label": label})

        return subtitles

    def _hls_bitrate_for_quality(self, master_url: str, referer: str, requested_quality: str) -> Optional[int]:
        """Restituisce il BANDWIDTH della variante del master più adatta alla qualità richiesta."""
        target_height = quality_height(requested_quality)
        if target_height is None:
            return None

        try:
            resp = self.session.get(
                master_url,
                headers={"Referer": referer, "User-Agent": DEFAULT_USER_AGENT},
                timeout=5,
            )
            playlist = resp.text
        except Exception as exc:
            logger.debug("Playlist master non raggiungibile per la scelta qualità: %s", exc)
            return None

        variants: list[tuple[int, int]] = []
        for line in playlist.splitlines():
            if not line.startswith("#EXT-X-STREAM-INF"):
                continue
            height = re.search(r"RESOLUTION=\d+x(\d+)", line)
            bandwidth = re.search(r"(?<![\w-])BANDWIDTH=(\d+)", line)
            if height and bandwidth:
                variants.append((int(height.group(1)), int(bandwidth.group(1))))

        if not variants:
            return None

        within = [v for v in variants if v[0] <= target_height]
        chosen = max(within) if within else min(variants)
        return chosen[1]

    def _build_result(
        self,
        stream_url: str,
        fmt: str,
        referer: str,
        subtitles: Optional[list[dict[str, Any]]] = None,
        hls_bitrate: Optional[int] = None,
    ) -> dict[str, Any]:
        """Costruisce il payload finale strutturato con gli header indispensabili per la riproduzione."""
        clean_url = sanitize_stream_url(stream_url)
        parsed_referer = urlparse(referer)
        origin = (
            f"{parsed_referer.scheme}://{parsed_referer.netloc}" if parsed_referer.netloc else self.base_url
        )

        return {
            "stream_url": clean_url,
            "headers": {
                "User-Agent": DEFAULT_USER_AGENT,
                "Referer": referer,
                "Origin": origin,
            },
            "format": fmt,
            "subtitles": subtitles or [],
            "hls_bitrate": hls_bitrate,
        }
