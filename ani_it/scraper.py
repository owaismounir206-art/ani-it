"""Scraper ad alte prestazioni per AnimeUnity (Zero-Overhead, nessun BeautifulSoup, solo Regex + JSON)."""

import html
import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urljoin

import requests

from ani_it.constants import (
    DEFAULT_BASE_URL,
    DEFAULT_USER_AGENT,
    HTTP_TIMEOUT,
    RE_ANIME,
    RE_ANIMES,
    RE_COVER_IMG,
    RE_COVER_IMG_ALT,
    RE_CSRF,
    RE_EPISODE,
    RE_EPISODES,
    RE_GENRES,
    RE_H1_TITLE,
    RE_PLOT,
    RE_SCORE,
    RE_SCRIPT_EPISODES,
    RE_TITLE_TAG,
    Endpoints,
    site_headers,
)
from ani_it.utils import normalize_episode_number

logger = logging.getLogger("ani_it.scraper")


class EpisodesNotFoundError(Exception):
    """La scheda dell'anime è stata letta senza errori ma non contiene alcun episodio."""


class SiteUnreachableError(requests.exceptions.ConnectionError):
    """AnimeUnity non è raggiungibile (rete, timeout) o risponde con errori persistenti (403/429/5xx)."""


class ResourceNotFoundError(Exception):
    """AnimeUnity è raggiungibile ma la risorsa richiesta non esiste (HTTP 404/410)."""


RETRYABLE_STATUS = frozenset({403, 429})


def title_from_slug(slug: str) -> str:
    """Titolo leggibile ricavato dallo slug, con il suffisso di lingua come tag (ITA / SUB ITA)."""
    tokens = [t for t in slug.split("-") if t]
    tag = ""
    if len(tokens) > 2 and tokens[-2:] == ["sub", "ita"]:
        tag, tokens = " (SUB ITA)", tokens[:-2]
    elif len(tokens) > 1 and tokens[-1] == "ita":
        tag, tokens = " (ITA)", tokens[:-1]
    return " ".join(t.capitalize() for t in tokens) + tag


class AnimeUnityScraper:
    """Gestisce le comunicazioni HTTP verso AnimeUnity senza sovraccarico di DOM."""

    def __init__(self, session: Optional[requests.Session] = None, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.endpoints = Endpoints(self.base_url)
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
        self.max_retries = 3
        self.base_backoff = 1.0
        self._csrf_token: Optional[str] = None
        self._csrf_expires: float = 0.0

    def _site_headers(self, api: bool = False, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        """Header per le richieste ad AnimeUnity."""
        headers = site_headers(self.base_url)
        if api:
            headers.update({
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/plain, */*",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
            })
            if self._csrf_token:
                headers["X-CSRF-TOKEN"] = self._csrf_token
        if extra:
            headers.update(extra)
        return headers

    def _ensure_session(self) -> None:
        """Recupera il token CSRF necessario per le chiamate API interne."""
        if self._csrf_token and time.time() < self._csrf_expires:
            return

        try:
            resp = self.session.get(
                self.base_url,
                headers=self._site_headers(),
                timeout=HTTP_TIMEOUT,
                allow_redirects=True,
            )
            match = RE_CSRF.search(resp.text)
            if match:
                token = match.group(1) or match.group(2)
                if token:
                    self._csrf_token = token.strip()
                    self._csrf_expires = time.time() + 300
        except Exception as exc:
            logger.debug("Impossibile recuperare il token CSRF da AnimeUnity: %s", exc)

    def _request_with_retry(
        self,
        method: str,
        url: str,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        api: bool = False,
    ) -> requests.Response:
        """Invia una richiesta HTTP (GET o POST) con retry controllato ed esponenziale."""
        req_headers = self._site_headers(api=api, extra=headers)
        last_error: Optional[Exception] = None
        last_status: Optional[int] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                if method.upper() == "POST":
                    response = self.session.post(
                        url,
                        params=params,
                        json=json_data,
                        headers=req_headers,
                        timeout=HTTP_TIMEOUT,
                        allow_redirects=True,
                    )
                else:
                    response = self.session.get(
                        url,
                        params=params,
                        headers=req_headers,
                        timeout=HTTP_TIMEOUT,
                        allow_redirects=True,
                    )
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_error, last_status = exc, None
            else:
                status = response.status_code
                if status < 400:
                    return response
                if status in (404, 410):
                    raise ResourceNotFoundError(f"Risorsa non trovata (HTTP {status}): {url}")
                if status not in RETRYABLE_STATUS and status < 500:
                    response.raise_for_status()
                last_error, last_status = None, status

            if attempt < self.max_retries:
                time.sleep(self.base_backoff * (2 ** (attempt - 1)))

        if last_status is not None:
            hint = " (possibile protezione Cloudflare)" if last_status == 403 else ""
            raise SiteUnreachableError(f"AnimeUnity ha risposto HTTP {last_status}{hint}: {url}")
        raise SiteUnreachableError(f"Impossibile connettersi ad AnimeUnity ({url}): {last_error}") from last_error

    def search_anime(self, query: str) -> list[dict[str, Any]]:
        """Cerca anime su AnimeUnity interrogando get-animes, livesearch e fallback HTML/Vue."""
        clean_query = query.strip()
        if not clean_query:
            return []

        self._ensure_session()
        seen_ids: set[str] = set()
        results: list[dict[str, Any]] = []
        connection_errors: list[Exception] = []

        api_headers = {
            "Content-Type": "application/json;charset=utf-8",
            "Referer": self.endpoints.archive,
            "Origin": self.base_url,
        }

        # 1. STRATEGIA 1: POST /archivio/get-animes
        try:
            payload = {
                "title": clean_query,
                "type": False,
                "year": False,
                "order": "Popolarità",
                "status": False,
                "genres": False,
                "season": False,
                "offset": 0,
                "dubbed": False,
            }
            resp = self._request_with_retry(
                "POST", self.endpoints.get_animes, json_data=payload, headers=api_headers, api=True
            )
            data = resp.json()
            records = data.get("records") if isinstance(data, dict) else (data if isinstance(data, list) else [])
            if isinstance(records, list):
                for item in records:
                    if isinstance(item, dict):
                        norm = self._normalize_anime_entry(item)
                        if norm["id"] and norm["id"] not in seen_ids:
                            seen_ids.add(norm["id"])
                            results.append(norm)
        except requests.exceptions.ConnectionError as exc:
            connection_errors.append(exc)
            logger.debug("Tentativo get-animes fallito per errore di connessione: %s", exc)
        except Exception as exc:
            logger.debug("Tentativo get-animes fallito: %s", exc)

        # 2. STRATEGIA 2: POST /livesearch
        try:
            live_payload = {"title": clean_query}
            resp = self._request_with_retry(
                "POST", self.endpoints.livesearch, json_data=live_payload, headers=api_headers, api=True
            )
            live_data = resp.json()
            live_records = live_data.get("records") if isinstance(live_data, dict) else (live_data if isinstance(live_data, list) else [])
            if isinstance(live_records, list):
                for item in live_records:
                    if isinstance(item, dict):
                        norm = self._normalize_anime_entry(item)
                        if norm["id"] and norm["id"] not in seen_ids:
                            seen_ids.add(norm["id"])
                            results.append(norm)
        except requests.exceptions.ConnectionError as exc:
            connection_errors.append(exc)
            logger.debug("Tentativo livesearch fallito per errore di connessione: %s", exc)
        except Exception as exc:
            logger.debug("Tentativo livesearch fallito: %s", exc)

        # 3. STRATEGIA 3: Fallback GET /archivio HTML con estrazione Vue diretta (no BeautifulSoup)
        if not results:
            try:
                resp = self._request_with_retry("GET", self.endpoints.archive, params={"title": clean_query})
                html_text = resp.text
                vue_animes = self._extract_vue_attribute(html_text, [":animes", "v-bind:animes"])
                if vue_animes:
                    for item in vue_animes:
                        norm = self._normalize_anime_entry(item)
                        if norm["id"] and norm["id"] not in seen_ids:
                            seen_ids.add(norm["id"])
                            results.append(norm)

                if not results:
                    dom_items = self._parse_regex_anime_items(html_text)
                    for item in dom_items:
                        if item["id"] and item["id"] not in seen_ids:
                            seen_ids.add(item["id"])
                            results.append(item)
            except requests.exceptions.ConnectionError as exc:
                connection_errors.append(exc)
                logger.debug("Tentativo archivio HTML fallito per errore di connessione: %s", exc)
            except Exception as exc:
                logger.debug("Tentativo archivio HTML fallito: %s", exc)

        if not results and connection_errors:
            raise connection_errors[0]

        return results

    def get_anime_details(self, anime_id: str | int, slug: str) -> dict[str, Any]:
        """Recupera la scheda dell'anime ed episodi da info_api e markup HTML leggero."""
        clean_slug = str(slug).strip()
        str_id = str(anime_id).strip()

        if clean_slug.startswith(f"{str_id}-"):
            target_slug = clean_slug
        else:
            target_slug = f"{str_id}-{clean_slug}"

        url = f"{self.endpoints.info}/{target_slug}"
        self._ensure_session()

        episodes: list[dict[str, Any]] = []
        seen_ep_numbers: set[Any] = set()
        api_error: Optional[Exception] = None
        html_error: Optional[Exception] = None
        api_chunks_loaded = 0

        # 1. Recupero episodi da info_api/{anime_id}/1 in blocchi da 120
        start_range = 1
        chunk_size = 120
        max_chunks = 15

        for _ in range(max_chunks):
            end_range = start_range + chunk_size - 1
            info_url = f"{self.endpoints.info_api}/{str_id}/1?start_range={start_range}&end_range={end_range}"
            api_headers = {"Referer": url}
            try:
                resp = self._request_with_retry("GET", info_url, headers=api_headers, api=True)
                api_data = resp.json()
                raw_eps = api_data.get("episodes") if isinstance(api_data, dict) else (api_data if isinstance(api_data, list) else [])
                if not raw_eps or not isinstance(raw_eps, list):
                    break

                for raw_ep in raw_eps:
                    episode = self._build_episode(raw_ep)
                    if episode is None or episode["number"] in seen_ep_numbers:
                        continue
                    seen_ep_numbers.add(episode["number"])
                    episodes.append(episode)

                api_chunks_loaded += 1
                if len(raw_eps) < chunk_size:
                    break
                start_range += chunk_size
            except Exception as exc:
                logger.debug("Tentativo info_api blocco %d-%d fallito: %s", start_range, end_range, exc)
                api_error = exc
                break

        # 2. Parsing rapido con Regex per metadati della pagina HTML
        plot = ""
        title = target_slug
        image_url = ""
        score = ""
        genres: list[str] = []

        try:
            page_resp = self._request_with_retry("GET", url)
            html_text = page_resp.text

            # Titolo da <h1> o <title>
            h1_match = RE_H1_TITLE.search(html_text)
            if h1_match:
                raw_t = re.sub(r"<[^>]+>", "", h1_match.group(1))
                title = html.unescape(raw_t).strip()
                title = re.sub(r"\s*-\s*AnimeUnity.*$", "", title, flags=re.IGNORECASE).strip()
            else:
                title_match = RE_TITLE_TAG.search(html_text)
                if title_match:
                    raw_t = re.sub(r"<[^>]+>", "", title_match.group(1))
                    title = html.unescape(raw_t).strip()
                    title = re.sub(r"\s*-\s*AnimeUnity.*$", "", title, flags=re.IGNORECASE).strip()

            # Trama da <p class="...plot...">
            plot_match = RE_PLOT.search(html_text)
            if plot_match:
                raw_p = re.sub(r"<[^>]+>", "", plot_match.group(1))
                plot = html.unescape(raw_p).strip()

            # Copertina da <img class="...poster...">
            img_match = RE_COVER_IMG.search(html_text) or RE_COVER_IMG_ALT.search(html_text)
            if img_match:
                src = img_match.group(1).strip()
                image_url = urljoin(self.base_url, src)

            # Valutazione
            score_match = RE_SCORE.search(html_text)
            if score_match:
                score = html.unescape(score_match.group(1)).strip()

            # Generi
            for g_raw in RE_GENRES.findall(html_text):
                g_clean = html.unescape(g_raw).strip().rstrip(",")
                if g_clean and g_clean not in genres:
                    genres.append(g_clean)

            # 3. Fallback estrazione episodi dall'HTML se info_api non ha risposto
            if not episodes:
                raw_episodes = None

                # Estrazione diretta con regex su Vue attributes
                m_eps = RE_EPISODES.search(html_text)
                if m_eps:
                    try:
                        raw_episodes = json.loads(html.unescape(m_eps.group(1)))
                    except Exception:
                        pass

                if not raw_episodes:
                    m_anime = RE_ANIME.search(html_text)
                    if m_anime:
                        try:
                            a_obj = json.loads(html.unescape(m_anime.group(1)))
                            if isinstance(a_obj, dict):
                                raw_episodes = a_obj.get("episodes")
                        except Exception:
                            pass

                if not raw_episodes:
                    m_ep = RE_EPISODE.search(html_text)
                    if m_ep:
                        try:
                            raw_episodes = [json.loads(html.unescape(m_ep.group(1)))]
                        except Exception:
                            pass

                if not raw_episodes:
                    m_script = RE_SCRIPT_EPISODES.search(html_text)
                    if m_script:
                        try:
                            raw_episodes = json.loads(m_script.group(1))
                        except Exception:
                            pass

                if isinstance(raw_episodes, list):
                    for raw_ep in raw_episodes:
                        episode = self._build_episode(raw_ep)
                        if episode is None or episode["number"] in seen_ep_numbers:
                            continue
                        seen_ep_numbers.add(episode["number"])
                        episodes.append(episode)

        except Exception as exc:
            logger.debug("Errore durante il parsing HTML della scheda anime: %s", exc)
            html_error = exc

        # 4. Validazione finale episodi
        if not episodes:
            failure = html_error or api_error
            if failure is not None:
                raise failure
            raise EpisodesNotFoundError(f"Nessun episodio disponibile per '{title}' (id {str_id}).")

        # Ordina episodi
        def get_sort_key(item: dict[str, Any]) -> float:
            try:
                return float(item.get("number", 0))
            except (ValueError, TypeError):
                return 0.0

        episodes.sort(key=get_sort_key)
        partial = api_error is not None and api_chunks_loaded > 0

        return {
            "id": str_id,
            "title": title,
            "slug": target_slug,
            "plot": plot,
            "image_url": image_url,
            "score": score,
            "genres": genres,
            "total_episodes": len(episodes),
            "episodes": episodes,
            "partial": partial,
            "partial_reason": str(api_error) if partial else "",
        }

    def _build_episode(self, raw: Any) -> Optional[dict[str, Any]]:
        """Normalizza un episodio grezzo nella struttura dati standard di ani-it."""
        if not isinstance(raw, dict):
            return None

        number_raw = raw.get("number")
        if number_raw is None:
            number_raw = raw.get("id")
        number: Any = normalize_episode_number(number_raw)
        if number is None:
            number = str(number_raw)

        raw_link = str(raw.get("link") or raw.get("scws_id") or raw.get("file_name") or "").strip()
        dead_hosts = ("animeunityserver", "animessvserver")

        if (not raw_link.startswith("http") or any(d in raw_link for d in dead_hosts)) and raw.get("id"):
            link = f"{self.endpoints.embed_url}/{raw['id']}"
        else:
            link = raw_link

        title = raw.get("title") or raw.get("name") or f"Episodio {number}"
        return {
            "id": raw.get("id") or number,
            "number": number,
            "title": str(title).strip(),
            "created_at": raw.get("created_at", ""),
            "file_name": raw.get("file_name", ""),
            "link": link,
        }

    def _extract_vue_attribute(self, html_text: str, attr_names: list[str]) -> Optional[Any]:
        """Cerca e decodifica attributi Vue con payload JSON (es. :animes='[...]')."""
        for attr in attr_names:
            escaped_attr = re.escape(attr)
            patterns = [
                rf"{escaped_attr}=['\"](\[.*?\]|\{{.*?\}})\s*['\"](?=[\s/>])",
                rf"{escaped_attr}=\"([^\"]+)\"",
                rf"{escaped_attr}='([^']+)'",
            ]
            for pattern in patterns:
                match = re.search(pattern, html_text, re.DOTALL)
                if match:
                    raw_str = match.group(1)
                    unescaped = html.unescape(raw_str).strip()
                    try:
                        parsed = json.loads(unescaped)
                        if isinstance(parsed, (list, dict)):
                            return parsed
                    except json.JSONDecodeError:
                        continue
        return None

    def _parse_regex_anime_items(self, html_text: str) -> list[dict[str, Any]]:
        """Fallback leggero via Regex per estrarre schede anime dai link nell'HTML."""
        results: list[dict[str, Any]] = []
        link_pattern = re.compile(r'<a[^>]+href=["\'](?:https?://[^/]+)?/anime/(\d+)(?:-([\w-]+))?["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
        seen_ids: set[str] = set()

        for match in link_pattern.finditer(html_text):
            anime_id = match.group(1)
            slug = match.group(2) or anime_id
            inner_html = match.group(3)

            if anime_id in seen_ids:
                continue
            seen_ids.add(anime_id)

            title = re.sub(r"<[^>]+>", "", inner_html).strip()
            img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', inner_html, re.I)
            img_url = ""
            if img_match:
                img_url = urljoin(self.base_url, img_match.group(1).strip())
                alt_match = re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', inner_html, re.I)
                if not title and alt_match:
                    title = alt_match.group(1).strip()

            if not title:
                title = slug.replace("-", " ").title()

            results.append({
                "id": anime_id,
                "title": title,
                "title_eng": "",
                "slug": f"{anime_id}-{slug}" if slug != anime_id else anime_id,
                "type": "TV",
                "year": "N/D",
                "episodes_count": "?",
                "status": "In corso",
                "dub": slug.endswith("-ita"),
                "image_url": img_url,
                "plot": "",
                "score": "",
                "genres": [],
            })

        return results

    def _normalize_anime_entry(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Uniforma la struttura dati di un anime restituita da AnimeUnity."""
        anime_id = str(raw.get("id") or raw.get("anime_id") or "")
        slug = str(raw.get("slug") or raw.get("name_slug") or anime_id)

        name_it = str(raw.get("title_it") or "").strip()
        name_eng = str(raw.get("title_eng") or "").strip()
        name_orig = str(raw.get("title_original") or "").strip()
        name_main = str(raw.get("title") or raw.get("name") or "").strip()

        clean_slug_name = title_from_slug(slug)
        title = name_it or name_eng or name_main or name_orig or clean_slug_name or f"Anime {anime_id}"
        title_eng = name_eng or name_orig or (name_it if name_it != title else "")

        image_url = raw.get("imageurl") or raw.get("image_url") or raw.get("image") or raw.get("cover") or ""
        if image_url and not image_url.startswith(("http://", "https://")):
            image_url = urljoin(self.base_url, image_url)

        episodes_count = raw.get("episodes_count") or raw.get("episodes") or "?"
        if isinstance(episodes_count, list):
            episodes_count = len(episodes_count)

        genres_raw = raw.get("genres") or []
        genres: list[str] = []
        if isinstance(genres_raw, list):
            for g in genres_raw:
                if isinstance(g, dict) and "name" in g:
                    genres.append(str(g["name"]))
                elif isinstance(g, str):
                    genres.append(g)

        score = str(raw.get("score") or raw.get("rating") or "")
        raw_type = str(raw.get("type") or "TV").upper()

        return {
            "id": anime_id,
            "title": str(title).strip(),
            "title_eng": str(title_eng).strip(),
            "slug": slug,
            "type": raw_type,
            "year": str(raw.get("date") or raw.get("year") or "N/D"),
            "episodes_count": str(episodes_count),
            "status": str(raw.get("status") or "Terminato"),
            "dub": bool(raw.get("dub")),
            "image_url": image_url,
            "plot": str(raw.get("plot") or raw.get("description") or ""),
            "score": score,
            "genres": genres,
        }
