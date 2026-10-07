"""Controller per il video player mpv con supporto IPC cross-platform (Unix Socket e Windows Named Pipe) v2.0."""

import gc
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from ani_it.config import Config
from ani_it.constants import PIPE_NAME_TEMPLATE, SOCKET_NAME_TEMPLATE
from ani_it.utils import (
    SignalHandler,
    detect_display_server,
    get_runtime_dir,
    is_windows,
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
    """Client per il protocollo JSON-IPC di mpv compatibile con Unix Socket e Windows Named Pipe."""

    def __init__(self, target: Union[Path, str]) -> None:
        self.target = target
        self._sock: Optional[socket.socket] = None
        self._pipe: Optional[Any] = None
        self._buffer = b""
        self._next_request_id = 1
        self.events: deque[dict[str, Any]] = deque(maxlen=100)

    @property
    def connected(self) -> bool:
        return self._sock is not None or self._pipe is not None

    def try_connect(self) -> bool:
        """Un solo tentativo non bloccante di connessione al canale IPC di mpv."""
        if self.connected:
            return True

        if is_windows():
            pipe_str = str(self.target)
            try:
                # Apertura Named Pipe Win32 in modalità binaria senza buffer
                self._pipe = open(pipe_str, "r+b", buffering=0)
                self._buffer = b""
                return True
            except OSError:
                return False
        else:
            socket_path = Path(self.target)
            if not socket_path.exists():
                return False
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.settimeout(1.0)
                sock.connect(str(socket_path))
            except OSError:
                sock.close()
                return False
            self._sock = sock
            self._buffer = b""
            return True

    def connect(self, timeout: float = 5.0) -> bool:
        """Tenta la connessione con polling fino a timeout."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.try_connect():
                return True
            time.sleep(0.1)
        return False

    def pump(self, timeout: float) -> None:
        """Legge i messaggi per al più `timeout` secondi accumulando gli eventi di mpv."""
        deadline = time.monotonic() + timeout
        while self.connected:
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
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None

            try:
                if self._sock is not None:
                    self._sock.settimeout(max(0.05, remaining))
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        self.close()
                        return None
                    self._buffer += chunk
                elif self._pipe is not None:
                    # Lettura Named Pipe Windows
                    chunk = self._pipe.read(4096)
                    if not chunk:
                        self.close()
                        return None
                    self._buffer += chunk
                else:
                    return None
            except (socket.timeout, BlockingIOError):
                return None
            except OSError:
                self.close()
                return None

    def send_command(self, command: list[Any], timeout: float = 1.0) -> Optional[dict[str, Any]]:
        """Invia un comando con request_id e attende la risposta corrispondente."""
        if not self.connected:
            return None

        req_id = self._next_request_id
        self._next_request_id += 1
        payload = json.dumps({"command": command, "request_id": req_id}) + "\n"

        try:
            raw_payload = payload.encode("utf-8")
            if self._sock is not None:
                self._sock.sendall(raw_payload)
            elif self._pipe is not None:
                self._pipe.write(raw_payload)
        except OSError:
            self.close()
            return None

        deadline = time.monotonic() + timeout
        while self.connected:
            message = self._read_message(deadline)
            if message is None:
                return None
            if message.get("request_id") == req_id:
                return message
            if "event" in message:
                self.events.append(message)

        return None

    def get_property(self, name: str, timeout: float = 1.0) -> Any:
        """Legge una proprietà di mpv; None su errore o timeout."""
        reply = self.send_command(["get_property", name], timeout=timeout)
        if reply and reply.get("error") == "success":
            return reply.get("data")
        return None

    def close(self) -> None:
        """Chiude la connessione socket o pipe."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

        if self._pipe is not None:
            try:
                self._pipe.close()
            except OSError:
                pass
            self._pipe = None


class MpvController:
    """Gestisce il ciclo di vita, il comando e il monitoraggio del player video mpv."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._init_ipc_target()

    def _init_ipc_target(self) -> None:
        """Inizializza il target IPC (Named Pipe per Windows, Unix Socket per Unix)."""
        if is_windows():
            pipe_id = uuid.uuid4().hex[:12]
            self.ipc_target_str = PIPE_NAME_TEMPLATE.format(uuid=pipe_id)
            self.socket_path = Path(self.ipc_target_str)
        else:
            self.runtime_dir = get_runtime_dir()
            socket_name = SOCKET_NAME_TEMPLATE.format(pid=os.getpid())
            self.socket_path = self.runtime_dir / socket_name
            self.ipc_target_str = str(self.socket_path)

    def _cleanup_socket(self) -> None:
        """Rimuove il file del socket Unix se ancora presente (su Windows le Named Pipe si distruggono da sole)."""
        if not is_windows():
            try:
                if self.socket_path.exists():
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
        """Esegue mpv registrando la pulizia del socket/pipe in caso di segnale."""
        SignalHandler.register_cleanup(self._cleanup_socket)
        try:
            return self._play(stream_data, anime_title, episode_number, start_time, quit_on_eof)
        finally:
            SignalHandler.unregister_cleanup(self._cleanup_socket)
            gc.collect()

    def _play(
        self,
        stream_data: dict[str, Any],
        anime_title: str,
        episode_number: Any,
        start_time: Optional[float] = None,
        quit_on_eof: bool = False,
    ) -> PlaybackResult:
        """Avvia mpv con hard-cap di memoria RAM e monitoraggio IPC."""
        self._cleanup_socket()

        stream_url = stream_data["stream_url"]
        headers = stream_data.get("headers", {})

        title_str = f"ani-it | {anime_title} - Ep. {episode_number}"

        vo_args = ["--vo=drm,caca"] if detect_display_server() == "tty" else []

        cmd = [
            self.config.player.binary,
            stream_url,
            f"--title={title_str}",
            f"--input-ipc-server={self.ipc_target_str}",
            # Hard-cap della memoria demuxing e cache per impedire il consumo di RAM
            "--demuxer-max-bytes=64M",
            "--demuxer-max-back-bytes=16M",
            "--cache=yes",
            "--cache-secs=30",
        ]

        # Intestazioni HTTP
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

        hls_bitrate = stream_data.get("hls_bitrate")
        if hls_bitrate:
            cmd.append(f"--hls-bitrate={int(hls_bitrate)}")

        # Rileva se è richiesta la riproduzione video all'interno del terminale
        is_terminal_vo = any(
            any(vo in arg for vo in ("--vo=tct", "--vo=kitty", "--vo=sixel", "--vo=caca"))
            for arg in (*vo_args, *self.config.player.args)
        )

        window_args = ["--force-window=no"] if is_terminal_vo else ["--force-window=immediate"]

        cmd += [
            *window_args,
            "--tls-verify=yes",
            "--msg-level=all=error",
            *vo_args,
            *self.config.player.args,
        ]

        for sub in stream_data.get("subtitles", []):
            cmd.append(f"--sub-file={sub['url']}")

        if start_time and start_time > 0:
            cmd.append(f"--start={int(start_time)}")

        if is_terminal_vo:
            # Per il video nel terminale, stdout/stdin non devono essere rediretti a file
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
                    "Installalo con il package manager di sistema (es. pacman -S mpv o brew install mpv)."
                ) from None

            SignalHandler.register_process(proc)
            ipc = MpvIpcClient(self.ipc_target_str)
            try:
                last_pos, duration, percent, eof_reached = self._monitor(proc, ipc, quit_on_eof)
            finally:
                SignalHandler.unregister_process(proc)
                ipc.close()
                self._cleanup_socket()
                gc.collect()

            return PlaybackResult(
                status=self._classify_exit(proc.returncode, eof_reached, percent),
                time_pos=last_pos,
                duration=duration,
                percent_pos=percent,
                error_output="",
            )

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
                    "Installalo con il package manager di sistema (es. pacman -S mpv o brew install mpv)."
                ) from None

            SignalHandler.register_process(proc)
            ipc = MpvIpcClient(self.ipc_target_str)
            try:
                last_pos, duration, percent, eof_reached = self._monitor(proc, ipc, quit_on_eof)
            finally:
                SignalHandler.unregister_process(proc)
                ipc.close()
                self._cleanup_socket()
                gc.collect()

            error_tail = ""
            if proc.returncode != 0:
                try:
                    mpv_log.seek(0)
                    raw_lines = mpv_log.readlines()
                    tail = [line.decode("utf-8", errors="replace") for line in raw_lines[-LOG_TAIL_LINES:]]
                    error_tail = "".join(tail).strip()
                except OSError:
                    pass

            return PlaybackResult(
                status=self._classify_exit(proc.returncode, eof_reached, percent),
                time_pos=last_pos,
                duration=duration,
                percent_pos=percent,
                error_output=error_tail,
            )

    def _monitor(
        self,
        proc: subprocess.Popen[bytes],
        ipc: MpvIpcClient,
        quit_on_eof: bool,
    ) -> tuple[float, float, float, bool]:
        """Segue il processo fino all'uscita interrogando mpv tramite IPC."""
        last_pos = 0.0
        duration = 0.0
        percent = 0.0
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

            ipc.pump(0.5)
            if absorb_events():
                eof_reached = True

            if eof_reached and quit_on_eof and not quit_sent:
                ipc.send_command(["quit"], timeout=1.0)
                quit_sent = True

        if ipc.connected:
            ipc.pump(0.2)
            if absorb_events():
                eof_reached = True

        return last_pos, duration, percent, eof_reached

    def _classify_exit(self, retcode: int, eof_reached: bool, percent_pos: float) -> str:
        """Deduce se l'uscita è stata un completamento, un quit manuale o un errore."""
        if eof_reached or percent_pos >= 90.0:
            return "completed"
        if retcode == 0:
            return "quit"
        return "error"
