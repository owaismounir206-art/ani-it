"""Controller per il video player mpv con supporto IPC Unix socket, PipeWire e Wayland."""

import json
import os
import socket
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ani_it.config import Config
from ani_it.constants import SOCKET_NAME_TEMPLATE
from ani_it.utils import (
    SignalHandler,
    detect_display_server,
    get_runtime_dir,
)


@dataclass
class PlaybackResult:
    """Esito del ciclo di riproduzione mpv."""
    status: str  # 'completed' (EOF), 'quit' (uscita manuale), 'error'
    time_pos: float = 0.0
    duration: float = 0.0
    percent_pos: float = 0.0
    error_output: str = ""  # ultime righe del log di mpv, utili per diagnosticare un errore


# Righe del log di mpv conservate per diagnosticare un errore
LOG_TAIL_LINES = 10


class MpvIpcClient:
    """Client minimale per il protocollo JSON-IPC di mpv su socket Unix.

    mpv scrive sullo stesso socket sia le risposte ai comandi sia eventi asincroni
    (una riga JSON ciascuno). Ogni comando porta un `request_id` e il client legge per
    righe complete, scartando eventi e risposte che non corrispondono al comando atteso.
    """

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self._sock: Optional[socket.socket] = None
        self._buffer = b""
        self._next_request_id = 1
        # Ultimi eventi asincroni ricevuti (end-file, ecc.), consultabili dal chiamante
        self.events: deque[dict[str, Any]] = deque(maxlen=100)

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def try_connect(self) -> bool:
        """Un solo tentativo di connessione al socket di mpv, senza attese."""
        if self._sock is not None:
            return True
        if not self.socket_path.exists():
            return False
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(1.0)
            sock.connect(str(self.socket_path))
        except OSError:
            sock.close()
            return False
        self._sock = sock
        self._buffer = b""
        return True

    def connect(self, timeout: float = 5.0) -> bool:
        """Tenta la connessione al socket Unix di mpv con polling fino a timeout."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.try_connect():
                return True
            time.sleep(0.1)
        return False

    def pump(self, timeout: float) -> None:
        """Legge i messaggi per al più `timeout` secondi accumulando gli eventi di mpv.

        Ritorna prima se mpv chiude il socket (cioè esce). Le risposte a comandi vengono scartate.
        """
        deadline = time.monotonic() + timeout
        while self._sock is not None:
            message = self._read_message(deadline)
            if message is None:
                return
            if "event" in message:
                self.events.append(message)

    def _read_message(self, deadline: float) -> Optional[dict[str, Any]]:
        """Legge la prossima riga JSON completa; None su timeout, errore o socket chiuso."""
        while True:
            if b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # riga corrotta: scartata
                if isinstance(message, dict):
                    return message
                continue

            if self._sock is None:
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                self._sock.settimeout(remaining)
                chunk = self._sock.recv(4096)
            except TimeoutError:
                return None
            except OSError:
                self.close()
                return None
            if not chunk:  # mpv ha chiuso il socket
                self.close()
                return None
            self._buffer += chunk

    def send_command(self, command: list[Any], timeout: float = 2.0) -> Optional[Any]:
        """Invia un comando a mpv e restituisce il campo 'data' della *sua* risposta.

        Eventi asincroni e risposte con un `request_id` diverso (comandi scaduti) vengono
        scartati. Restituisce None su errore di mpv, timeout o connessione persa.
        """
        if not self._sock:
            return None

        request_id = self._next_request_id
        self._next_request_id += 1
        payload = json.dumps({"command": command, "request_id": request_id}) + "\n"
        try:
            self._sock.sendall(payload.encode("utf-8"))
        except OSError:
            self.close()
            return None

        deadline = time.monotonic() + timeout
        while True:
            message = self._read_message(deadline)
            if message is None:
                return None
            if "event" in message:
                self.events.append(message)
                continue
            if message.get("request_id") != request_id:
                continue
            if message.get("error", "success") != "success":
                return None
            return message.get("data")

    def get_property(self, prop_name: str) -> Optional[Any]:
        """Recupera il valore di una proprietà da mpv."""
        return self.send_command(["get_property", prop_name])

    def close(self) -> None:
        """Chiude il socket Unix."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


class MpvController:
    """Gestisce l'invocazione di mpv, gli argomenti di sistema Arch e il monitoraggio IPC."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.runtime_dir = get_runtime_dir()
        self.socket_path = self.runtime_dir / SOCKET_NAME_TEMPLATE.format(pid=os.getpid())

    def _cleanup_socket(self) -> None:
        """Rimuove il file del socket Unix se rimasto aperto."""
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass

    def play(
        self,
        stream_data: dict[str, Any],
        anime_title: str,
        episode_number: Any,
        start_time: Optional[float] = None,
        quit_on_eof: bool = False,
    ) -> PlaybackResult:
        """Avvia mpv con monitoraggio IPC (vedi `_play`), registrando la pulizia del socket
        solo per la durata della riproduzione: le callback non si accumulano tra un episodio e l'altro."""
        SignalHandler.register_cleanup(self._cleanup_socket)
        try:
            return self._play(stream_data, anime_title, episode_number, start_time, quit_on_eof)
        finally:
            SignalHandler.unregister_cleanup(self._cleanup_socket)

    def _play(
        self,
        stream_data: dict[str, Any],
        anime_title: str,
        episode_number: Any,
        start_time: Optional[float] = None,
        quit_on_eof: bool = False,
    ) -> PlaybackResult:
        """Avvia mpv con accelerazione hardware, PipeWire e monitoraggio IPC.

        Args:
            stream_data: Dizionario contenente 'stream_url' e 'headers'.
            anime_title: Titolo della serie per la barra del titolo.
            episode_number: Numero dell'episodio.
            start_time: Posizione di partenza opzionale in secondi.
            quit_on_eof: Chiude mpv appena l'episodio finisce (necessario per l'avanzamento
                automatico quando mpv.conf imposta keep-open e mpv resterebbe aperto).

        Returns:
            PlaybackResult con lo stato dell'uscita e il progresso raggiunto.
        """
        self._cleanup_socket()

        stream_url = stream_data["stream_url"]
        headers = stream_data.get("headers", {})

        title_str = f"ani-it | {anime_title} - Ep. {episode_number}"

        # Output video/audio: si lascia scegliere a mpv (mpv.conf dell'utente incluso). Solo in una
        # TTY pura, dove il valore predefinito non funziona, serve un'uscita video esplicita.
        vo_args = ["--vo=drm,caca"] if detect_display_server() == "tty" else []

        cmd = [
            self.config.player.binary,
            stream_url,
            f"--title={title_str}",
            f"--input-ipc-server={self.socket_path}",
        ]

        # Intestazioni HTTP. User-Agent e Referer hanno opzioni dedicate: in
        # --http-header-fields (lista separata da virgole) lo User-Agent verrebbe spezzato
        # a "(KHTML, like Gecko)". Gli altri header sono accodati uno per volta.
        user_agent = referer = None
        extra_headers: list[str] = []
        for name, value in headers.items():
            if not value:
                continue
            if name.lower() == "user-agent":
                user_agent = value
            elif name.lower() == "referer":
                referer = value
            else:
                extra_headers.append(f"{name}: {value}")
        if user_agent:
            cmd.append(f"--user-agent={user_agent}")
        if referer:
            cmd.append(f"--referrer={referer}")
        cmd.extend(f"--http-header-fields-append={header}" for header in extra_headers)

        # Variante HLS scelta dal resolver per la qualità richiesta (il master resta intatto)
        hls_bitrate = stream_data.get("hls_bitrate")
        if hls_bitrate:
            cmd.append(f"--hls-bitrate={int(hls_bitrate)}")

        # Rileva se è richiesto il rendering del video direttamente nel terminale
        is_terminal_vo = any(
            any(vo in arg for vo in ("--vo=tct", "--vo=kitty", "--vo=sixel", "--vo=caca"))
            for arg in (*vo_args, *self.config.player.args)
        )

        window_args = ["--force-window=no"] if is_terminal_vo else ["--force-window=immediate"]

        cmd += [
            *window_args,
            # mpv non verifica i certificati TLS di default: come per aria2c e yt-dlp, va chiesto
            "--tls-verify=yes",
            # Solo gli errori nel log (mpv li scrive su stdout): niente riga di stato
            # continua, così il file di log resta piccolo.
            "--msg-level=all=error",
            *vo_args,
            *self.config.player.args,
        ]

        # Sottotitoli se disponibili
        for sub in stream_data.get("subtitles", []):
            cmd.append(f"--sub-file={sub['url']}")

        if start_time and start_time > 0:
            cmd.append(f"--start={int(start_time)}")

        if is_terminal_vo:
            # Per il video nel terminale, stdout/stdin non devono essere rediretti a file temporanei
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=None,
                    stdout=None,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"Il lettore video '{self.config.player.binary}' non è installato sul sistema.\n"
                    "Installalo con: sudo pacman -S mpv"
                ) from None

            SignalHandler.register_process(proc)

            ipc = MpvIpcClient(self.socket_path)
            try:
                last_pos, duration, percent, eof_reached = self._monitor(proc, ipc, quit_on_eof)
            finally:
                SignalHandler.unregister_process(proc)
                ipc.close()
                self._cleanup_socket()

            return PlaybackResult(
                status=self._classify_exit(proc.returncode, eof_reached, percent),
                time_pos=last_pos,
                duration=duration,
                percent_pos=percent,
                error_output="",
            )

        # L'output di mpv va in un file temporaneo (non in una pipe, che bloccherebbe mpv
        # se piena): serve a mostrare all'utente il motivo di un errore di riproduzione.
        with tempfile.TemporaryFile() as mpv_log:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=mpv_log,
                    stderr=subprocess.STDOUT,
                )
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"Il lettore video '{self.config.player.binary}' non è installato sul sistema.\n"
                    "Installalo con: sudo pacman -S mpv"
                ) from None

            SignalHandler.register_process(proc)

            ipc = MpvIpcClient(self.socket_path)
            try:
                last_pos, duration, percent, eof_reached = self._monitor(proc, ipc, quit_on_eof)
            finally:
                SignalHandler.unregister_process(proc)
                ipc.close()
                self._cleanup_socket()

            error_output = self._read_log_tail(mpv_log)

        return PlaybackResult(
            status=self._classify_exit(proc.returncode, eof_reached, percent),
            time_pos=last_pos,
            duration=duration,
            percent_pos=percent,
            error_output=error_output,
        )

    @staticmethod
    def _monitor(
        proc: "subprocess.Popen[bytes]",
        ipc: MpvIpcClient,
        quit_on_eof: bool,
    ) -> tuple[float, float, float, bool]:
        """Segue mpv finché è in esecuzione: posizione, durata e fine episodio.

        La connessione IPC viene ritentata a ogni ciclo (il socket può comparire dopo
        qualche secondo). La fine episodio si riconosce sia dalla proprietà `eof-reached`
        (con keep-open) sia dall'evento `end-file` con reason "eof" (senza keep-open mpv
        esce subito e la proprietà non è più leggibile).
        """
        last_pos = duration = percent = 0.0
        eof_reached = False
        quit_sent = False

        def absorb_events() -> bool:
            reached = False
            while ipc.events:
                event = ipc.events.popleft()
                if event.get("event") == "end-file" and event.get("reason") == "eof":
                    reached = True
            return reached

        while proc.poll() is None:
            if not ipc.connected:
                ipc.try_connect()
            if not ipc.connected:
                time.sleep(0.5)
                continue

            try:
                position = ipc.get_property("time-pos")
                total = ipc.get_property("duration")
                pct = ipc.get_property("percent-pos")
                if position is not None:
                    last_pos = float(position)
                if total is not None:
                    duration = float(total)
                if pct is not None:
                    percent = float(pct)
                if ipc.get_property("eof-reached"):
                    eof_reached = True
            except (TypeError, ValueError):
                pass

            ipc.pump(0.5)  # attende gli eventi (al posto di un sleep) e li accumula
            if absorb_events():
                eof_reached = True

            if eof_reached and quit_on_eof and not quit_sent:
                ipc.send_command(["quit"], timeout=1.0)
                quit_sent = True

        # mpv è uscito: gli ultimi eventi (end-file) possono essere ancora nel buffer del socket
        if ipc.connected:
            ipc.pump(0.2)
        if absorb_events():
            eof_reached = True

        return last_pos, duration, percent, eof_reached

    @staticmethod
    def _classify_exit(retcode: Optional[int], eof_reached: bool, percent: float) -> str:
        """Determina lo stato di visione dall'uscita di mpv.

        Codici di uscita di mpv: 0 = uscita normale (anche con 'q'), 1 = errore di
        inizializzazione, 2 = file non riproducibile, 3 = alcuni file non riproducibili,
        4 = interrotto da un segnale. Solo 0 è un'uscita volontaria.
        """
        # Considerato completato se EOF raggiunto o se l'utente ha visto almeno il 90%
        if eof_reached or percent >= 90.0:
            return "completed"
        if retcode == 0 or retcode is None:
            return "quit"
        return "error"

    @staticmethod
    def _read_log_tail(mpv_log: Any) -> str:
        """Restituisce le ultime righe non vuote scritte da mpv nel file di log."""
        try:
            mpv_log.seek(0)
            text = mpv_log.read().decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return ""
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines[-LOG_TAIL_LINES:])
