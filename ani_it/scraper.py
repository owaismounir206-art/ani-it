"""Scraper avanzato e resiliente per AnimeUnity con supporto API LiveSearch, Archivio e info_api."""

import html
import json
import logging
import re
import sys
import time
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ani_it.constants import (
    ARCHIVE_ENDPOINT,
    BASE_URL,
    DEFAULT_HEADERS,
    GET_ANIMES_ENDPOINT,
    INFO_API_ENDPOINT,
    INFO_ENDPOINT,
    LIVESEARCH_ENDPOINT,
    Colors,
    Icons,
)

logger = logging.getLogger("ani_it.scraper")


class AnimeUnityScraper:
    """Gestisce le comunicazioni HTTP verso AnimeUnity, con gestione sessione, CSRF token, retry e parsing multi-livello."""

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.max_retries = 3
        self.base_backoff = 1.0
        self._csrf_token: Optional[str] = None
        self._csrf_expires: float = 0.0

    def _ensure_session(self) -> None:
        """Inizializza la sessione HTTP e recupera il token CSRF necessario per le chiamate API interne."""
        if self._csrf_token and time.time() < self._csrf_expires:
            return

        try:
            resp = self.session.get(
                BASE_URL,
                headers=dict(self.session.headers),
                timeout=10,
                allow_redirects=True,
            )
            html_text = resp.text
            match = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html_text, re.I)
            if not match:
                match = re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']csrf-token["\']', html_text, re.I)

            if match:
                self._csrf_token = match.group(1).strip()
                self._csrf_expires = time.time() + 300
                self.session.headers["X-CSRF-TOKEN"] = self._csrf_token
                self.session.headers["X-Requested-With"] = "XMLHttpRequest"
        except Exception as exc:
            logger.debug("Impossibile recuperare il token CSRF da AnimeUnity: %s", exc)

    def _request_with_retry(
        self,
        method: str,
        url: str,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> requests.Response:
        """Invia una richiesta HTTP (GET o POST) con retry esponenziale e gestione Cloudflare."""
        req_headers = dict(self.session.headers)
        if headers:
            req_headers.update(headers)

        last_exception: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                if method.upper() == "POST":
                    response = self.session.post(
                        url,
                        params=params,
                        json=json_data,
                        headers=req_headers,
                        timeout=12,
                        allow_redirects=True,
                    )
                else:
                    response = self.session.get(
                        url,
                        params=params,
                        headers=req_headers,
                        timeout=12,
                        allow_redirects=True,
                    )

                if response.status_code in (403, 503):
                    sys.stderr.write(
                        f"\n{Colors.BRIGHT_YELLOW}{Icons.WARNING} Avviso AnimeUnity (HTTP {response.status_code}): "
                        f"Possibile protezione Cloudflare attiva. Tentativo {attempt}/{self.max_retries}...{Colors.RESET}\n"
                    )
                    if attempt < self.max_retries:
                        time.sleep(self.base_backoff * (2 ** (attempt - 1)))
                        continue
                    response.raise_for_status()

                response.raise_for_status()
                return response

            except requests.exceptions.RequestException as exc:
                last_exception = exc
                if attempt < self.max_retries:
                    time.sleep(self.base_backoff * (2 ** (attempt - 1)))
                else:
                    break

        error_msg = f"Impossibile connettersi ad AnimeUnity ({url}): {last_exception}"
        sys.stderr.write(f"\n{Colors.BRIGHT_RED}{Icons.ERROR} {error_msg}{Colors.RESET}\n")
        raise requests.exceptions.ConnectionError(error_msg) from last_exception

    def search_anime(self, query: str) -> list[dict[str, Any]]:
        """Cerca anime su AnimeUnity per titolo interrogando livesearch, archivio API e fallback HTML."""
        clean_query = query.strip()
        if not clean_query:
            return []

        self._ensure_session()
        seen_ids: set[str] = set()
        results: list[dict[str, Any]] = []
        connection_errors: list[Exception] = []

        api_headers = {
            "Content-Type": "application/json;charset=utf-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": ARCHIVE_ENDPOINT,
            "Origin": BASE_URL,
        }
        if self._csrf_token:
            api_headers["X-CSRF-TOKEN"] = self._csrf_token

        # ----------------------------------------------------------------------
        # STRATEGIA 1: POST /archivio/get-animes (API interna del catalogo)
        # ----------------------------------------------------------------------
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
            resp = self._request_with_retry("POST", GET_ANIMES_ENDPOINT, json_data=payload, headers=api_headers)
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

        # ----------------------------------------------------------------------
        # STRATEGIA 2: POST /livesearch (Ricerca istantanea di AnimeUnity)
        # ----------------------------------------------------------------------
        try:
            live_payload = {"title": clean_query}
            resp = self._request_with_retry("POST", LIVESEARCH_ENDPOINT, json_data=live_payload, headers=api_headers)
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

        # ----------------------------------------------------------------------
        # STRATEGIA 3: Ricerca per variante o parola chiave (es. 'umamusume' -> 'uma musume' o 'uma')
        # ----------------------------------------------------------------------
        if not results:
            fallback_query = clean_query
            if "musume" in clean_query.lower() and " " not in clean_query:
                fallback_query = clean_query.lower().replace("musume", " musume")
            elif len(clean_query) > 5 and not " " in clean_query:
                fallback_query = clean_query[:4]

            if fallback_query != clean_query:
                try:
                    payload = {
                        "title": fallback_query,
                        "type": False,
                        "year": False,
                        "order": "Popolarità",
                        "status": False,
                        "genres": False,
                        "season": False,
                        "offset": 0,
                        "dubbed": False,
                    }
                    resp = self._request_with_retry("POST", GET_ANIMES_ENDPOINT, json_data=payload, headers=api_headers)
                    data = resp.json()
                    records = data.get("records") if isinstance(data, dict) else []
                    if isinstance(records, list):
                        for item in records:
                            if isinstance(item, dict):
                                norm = self._normalize_anime_entry(item)
                                if norm["id"] and norm["id"] not in seen_ids:
                                    seen_ids.add(norm["id"])
                                    results.append(norm)
                except requests.exceptions.ConnectionError as exc:
                    connection_errors.append(exc)
                except Exception:
                    pass

        # ----------------------------------------------------------------------
        # STRATEGIA 4: Fallback GET /archivio HTML con estrazione Vue / DOM
        # ----------------------------------------------------------------------
        if not results:
            try:
                resp = self._request_with_retry("GET", ARCHIVE_ENDPOINT, params={"title": clean_query})
                html_text = resp.text
                vue_animes = self._extract_vue_attribute(html_text, [":animes", "v-bind:animes"])
                if vue_animes:
                    for item in vue_animes:
                        norm = self._normalize_anime_entry(item)
                        if norm["id"] and norm["id"] not in seen_ids:
                            seen_ids.add(norm["id"])
                            results.append(norm)

                if not results:
                    soup = BeautifulSoup(html_text, "html.parser")
                    dom_items = self._parse_dom_anime_items(soup)
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
        """Recupera la scheda completa dell'anime con episodi da info_api e markup HTML."""
        clean_slug = str(slug).strip()
        str_id = str(anime_id).strip()

        if clean_slug.startswith(f"{str_id}-"):
            target_slug = clean_slug
        else:
            target_slug = f"{str_id}-{clean_slug}"

        url = f"{INFO_ENDPOINT}/{target_slug}"
        self._ensure_session()

        episodes: list[dict[str, Any]] = []
        seen_ep_numbers: set[Any] = set()

        # ----------------------------------------------------------------------
        # 1. Recupero episodi da info_api/{anime_id}/1 (API interna di AnimeUnity)
        # In AnimeUnity le richieste sono suddivise in blocchi di 120 episodi:
        # start_range=1&end_range=120, poi 121-240, ecc.
        # ----------------------------------------------------------------------
        start_range = 1
        chunk_size = 120
        max_chunks = 15  # Fino a 1800 episodi (copre One Piece, Detective Conan, ecc.)

        for _ in range(max_chunks):
            end_range = start_range + chunk_size - 1
            info_url = f"{INFO_API_ENDPOINT}/{str_id}/1?start_range={start_range}&end_range={end_range}"
            api_headers = {
                "X-Requested-With": "XMLHttpRequest",
                "Referer": url,
            }
            try:
                resp = self._request_with_retry("GET", info_url, headers=api_headers)
                api_data = resp.json()
                raw_eps = api_data.get("episodes") if isinstance(api_data, dict) else (api_data if isinstance(api_data, list) else [])
                if not raw_eps or not isinstance(raw_eps, list):
                    break

                for ep in raw_eps:
                    if not isinstance(ep, dict):
                        continue
                    num_val = ep.get("number")
                    if num_val is None:
                        num_val = ep.get("id")

                    try:
                        num_float = float(num_val)
                        num_formatted = int(num_float) if num_float.is_integer() else num_float
                    except (ValueError, TypeError):
                        num_formatted = str(num_val)

                    if num_formatted in seen_ep_numbers:
                        continue
                    seen_ep_numbers.add(num_formatted)

                    raw_link = str(ep.get("link") or ep.get("scws_id") or ep.get("file_name") or "").strip()
                    ep_id = ep.get("id") or num_formatted
                    dead_hosts = ("animeunityserver", "animessvserver")

                    # Se il link non è un URL HTTP valido o punta a CDN deprecate con SSL rotto, usa l'endpoint embed-url
                    if (not raw_link.startswith("http") or any(d in raw_link for d in dead_hosts)) and ep.get("id"):
                        link = f"{BASE_URL}/embed-url/{ep['id']}"
                    else:
                        link = raw_link

                    ep_title = ep.get("title") or ep.get("name") or f"Episodio {num_formatted}"

                    episodes.append({
                        "id": ep_id,
                        "number": num_formatted,
                        "title": str(ep_title).strip(),
                        "created_at": ep.get("created_at", ""),
                        "file_name": ep.get("file_name", ""),
                        "link": link,
                    })

                # Se il blocco conteneva meno di chunk_size episodi, abbiamo finito
                if len(raw_eps) < chunk_size:
                    break
                start_range += chunk_size
            except Exception as exc:
                logger.debug("Tentativo info_api blocco %d-%d fallito: %s", start_range, end_range, exc)
                break

        # ----------------------------------------------------------------------
        # 2. Parsing pagina HTML per metadati completi (titolo, trama, voto, locandina)
        # ----------------------------------------------------------------------
        plot = ""
        title = target_slug
        image_url = ""
        score = ""
        genres: list[str] = []

        try:
            page_resp = self._request_with_retry("GET", url)
            html_text = page_resp.text
            soup = BeautifulSoup(html_text, "html.parser")

            # Titolo
            title_tag = soup.find("h1") or soup.find("title")
            if title_tag:
                title = title_tag.get_text(strip=True)
                title = re.sub(r"\s*-\s*AnimeUnity.*$", "", title, flags=re.IGNORECASE).strip()

            # Trama
            plot_tag = soup.find("p", class_=re.compile(r"plot|description|trama", re.I))
            if plot_tag:
                plot = plot_tag.get_text(strip=True)

            # Immagine di copertina
            img_tag = soup.find("img", class_=re.compile(r"poster|cover|anime-image", re.I))
            if img_tag and img_tag.get("src"):
                image_url = urljoin(BASE_URL, str(img_tag["src"]))

            # Valutazione
            score_tag = soup.find(class_=re.compile(r"score|rating|vote", re.I))
            if score_tag:
                score = score_tag.get_text(strip=True)

            # Generi
            genre_tags = soup.find_all("a", href=re.compile(r"/archivio\?.*genre", re.I))
            for g in genre_tags:
                g_text = g.get_text(strip=True).rstrip(",")
                if g_text and g_text not in genres:
                    genres.append(g_text)

            # ------------------------------------------------------------------
            # 3. Fallback estrazione episodi dall'HTML se info_api era vuota
            # ------------------------------------------------------------------
            if not episodes:
                player_tag = soup.find("video-player")
                raw_episodes = None
                if player_tag:
                    # 1. Attributo :episodes
                    ep_attr = player_tag.get(":episodes") or player_tag.get("episodes") or player_tag.get("v-bind:episodes")
                    if ep_attr:
                        try:
                            raw_episodes = json.loads(ep_attr)
                        except Exception:
                            pass

                    # 2. Attributo :anime
                    if not raw_episodes:
                        anime_attr = player_tag.get(":anime") or player_tag.get("anime") or player_tag.get("v-bind:anime")
                        if anime_attr:
                            try:
                                anime_obj = json.loads(anime_attr)
                                if isinstance(anime_obj, dict):
                                    raw_episodes = anime_obj.get("episodes")
                            except Exception:
                                pass

                    # 3. Attributo :episode (singolo per film/OAV)
                    if not raw_episodes:
                        single_ep = player_tag.get(":episode")
                        if single_ep:
                            try:
                                raw_episodes = [json.loads(single_ep)]
                            except Exception:
                                pass

                # 4. Fallback tramite regex script
                if not raw_episodes:
                    raw_episodes = self._extract_episodes_from_scripts(html_text)

                if isinstance(raw_episodes, list):
                    for ep in raw_episodes:
                        if not isinstance(ep, dict):
                            continue
                        num_val = ep.get("number") or ep.get("id")
                        try:
                            num_float = float(num_val)
                            num_formatted = int(num_float) if num_float.is_integer() else num_float
                        except (ValueError, TypeError):
                            num_formatted = str(num_val)

                        if num_formatted in seen_ep_numbers:
                            continue
                        seen_ep_numbers.add(num_formatted)

                        raw_link = str(ep.get("link") or ep.get("scws_id") or ep.get("file_name") or "").strip()
                        ep_id = ep.get("id") or num_formatted
                        dead_hosts = ("animeunityserver", "animessvserver")

                        if (not raw_link.startswith("http") or any(d in raw_link for d in dead_hosts)) and ep.get("id"):
                            link = f"{BASE_URL}/embed-url/{ep['id']}"
                        else:
                            link = raw_link

                        ep_title = ep.get("title") or ep.get("name") or f"Episodio {num_formatted}"

                        episodes.append({
                            "id": ep_id,
                            "number": num_formatted,
                            "title": str(ep_title).strip(),
                            "created_at": ep.get("created_at", ""),
                            "file_name": ep.get("file_name", ""),
                            "link": link,
                        })

        except Exception as exc:
            logger.debug("Errore durante il parsing HTML della scheda anime: %s", exc)

        # ----------------------------------------------------------------------
        # 4. Fallback estremo per film / opere con singolo episodio:
        # Se ancora nessun episodio è stato trovato, creiamo l'episodio 1 diretto
        # ----------------------------------------------------------------------
        if not episodes:
            episodes.append({
                "id": str_id,
                "number": 1,
                "title": title or "Film / Episodio Unico",
                "created_at": "",
                "file_name": "",
                "link": f"{BASE_URL}/embed-url/{str_id}",
            })

        # Ordina episodi per numero crescente
        def get_sort_key(item: dict[str, Any]) -> float:
            try:
                return float(item.get("number", 0))
            except (ValueError, TypeError):
                return 0.0

        episodes.sort(key=get_sort_key)

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
        }

    # ==========================================================================
    # METODI DI SUPPORTO PER IL PARSING
    # ==========================================================================

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

    def _extract_episodes_from_scripts(self, html_text: str) -> Optional[list[dict[str, Any]]]:
        """Estrae l'array degli episodi cercando variabili inline nei tag script."""
        patterns = [
            r"var\s+episodes\s*=\s*(\[.*?\]);",
            r"let\s+episodes\s*=\s*(\[.*?\]);",
            r'"episodes"\s*:\s*(\[\{.*?\}\])',
            r"episodes\s*:\s*(\[\{.*?\}\])",
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    if isinstance(data, list):
                        return data
                except Exception:
                    continue
        return None

    def _parse_dom_anime_items(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Estrae informazioni navigando i tag DOM nel caso di fallback server-side."""
        results: list[dict[str, Any]] = []
        anime_links = soup.find_all("a", href=re.compile(r"/anime/(\d+)-?([\w-]+)?"))
        seen_ids: set[str] = set()

        for a in anime_links:
            href = str(a.get("href", ""))
            match = re.search(r"/anime/(\d+)(?:-([\w-]+))?", href)
            if not match:
                continue

            anime_id = match.group(1)
            slug = match.group(2) or anime_id
            if anime_id in seen_ids:
                continue
            seen_ids.add(anime_id)

            title = a.get_text(strip=True)
            img = a.find("img")
            img_url = ""
            if img:
                img_url = urljoin(BASE_URL, str(img.get("src") or img.get("data-src") or ""))
                if not title and img.get("alt"):
                    title = str(img.get("alt")).strip()

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
                "image_url": img_url,
                "plot": "",
                "score": "",
                "genres": [],
            })

        return results

    def _normalize_anime_entry(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Uniforma la struttura dati di un anime restituita dalle API di AnimeUnity.
        Risolve accuratamente i titoli in italiano, inglese o slug per evitare voci 'Senza Titolo'.
        """
        anime_id = str(raw.get("id") or raw.get("anime_id") or "")
        slug = str(raw.get("slug") or raw.get("name_slug") or anime_id)

        # Ricerca robusta del titolo attraverso tutti i possibili campi
        name_it = str(raw.get("title_it") or "").strip()
        name_eng = str(raw.get("title_eng") or "").strip()
        name_orig = str(raw.get("title_original") or "").strip()
        name_main = str(raw.get("title") or raw.get("name") or "").strip()

        # Genera un fallback leggibile basato sullo slug
        clean_slug_name = (
            slug.replace("-ita", " (ITA)")
            .replace("-sub-ita", " (SUB)")
            .replace("-", " ")
            .title()
        )

        # Selezione gerarchica del titolo per non avere mai "Senza Titolo"
        title = name_it or name_eng or name_main or name_orig or clean_slug_name or f"Anime {anime_id}"
        title_eng = name_eng or name_orig or (name_it if name_it != title else "")

        image_url = raw.get("image_url") or raw.get("image") or raw.get("cover") or ""
        if image_url and not image_url.startswith(("http://", "https://")):
            image_url = urljoin(BASE_URL, image_url)

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
            "image_url": image_url,
            "plot": str(raw.get("plot") or raw.get("description") or ""),
            "score": score,
            "genres": genres,
        }
