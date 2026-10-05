"""Controller per il video player mpv con supporto IPC Unix socket, PipeWire e Wayland."""

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ani_it.config import Config
from ani_it.constants import DEFAULT_SOCKET_NAME
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


class MpvIpcClient:
    """Client minimale per il protocollo JSON-IPC di mpv su socket Unix."""

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self._sock: Optional[socket.socket] = None

    def connect(self, timeout: float = 5.0) -> bool:
        """Tenta la connessione al socket Unix di mpv con polling fino a timeout."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.socket_path.exists():
                try:
                    self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self._sock.settimeout(1.0)
                    self._sock.connect(str(self.socket_path))
                    return True
                except (socket.error, OSError):
                    if self._sock:
                        self._sock.close()
                        self._sock = None
            time.sleep(0.1)
        return False

    def send_command(self, command: list[Any]) -> Optional[Any]:
        """Invia un comando JSON a mpv e restituisce il campo 'data' della risposta."""
        if not self._sock:
            return None
        payload = json.dumps({"command": command}) + "\n"
        try:
            self._sock.sendall(payload.encode("utf-8"))
            raw = self._sock.recv(4096).decode("utf-8")
            # mpv invia risposte separate da newline
            for line in raw.splitlines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if "data" in data:
                    return data["data"]
        except Exception:
            pass
        return None

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
        self.socket_path = self.runtime_dir / DEFAULT_SOCKET_NAME

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
    ) -> PlaybackResult:
        """Avvia mpv con accelerazione hardware, PipeWire e monitoraggio IPC.

        Args:
            stream_data: Dizionario contenente 'stream_url' e 'headers'.
            anime_title: Titolo della serie per la barra del titolo.
            episode_number: Numero dell'episodio.
            start_time: Posizione di partenza opzionale in secondi.

        Returns:
            PlaybackResult con lo stato dell'uscita e il progresso raggiunto.
        """
        self._cleanup_socket()
        SignalHandler.register_cleanup(self._cleanup_socket)

        stream_url = stream_data["stream_url"]
        headers = stream_data.get("headers", {})

        # Intestazioni HTTP per mpv
        http_headers_list = [f"{k}: {v}" for k, v in headers.items()]
        http_headers_str = ",".join(http_headers_list)

        title_str = f"ani-it | {anime_title} - Ep. {episode_number}"

        # Configurazione display server e output video
        display = detect_display_server()
        vo_args = ["--vo=gpu-next", "--hwdec=auto-safe"]
        if display == "tty":
            vo_args = ["--vo=drm,caca"]

        cmd = [
            self.config.player.binary,
            stream_url,
            f"--title={title_str}",
            f"--input-ipc-server={self.socket_path}",
            f"--http-header-fields={http_headers_str}",
            "--referrer=" + headers.get("Referer", ""),
            "--user-agent=" + headers.get("User-Agent", ""),
            "--save-position-on-quit",
            "--force-window=immediate",
            "--ao=pipewire,pulse",
            *vo_args,
            *self.config.player.args,
        ]

        # Sottotitoli se disponibili
        for sub in stream_data.get("subtitles", []):
            cmd.append(f"--sub-file={sub['url']}")

        if start_time and start_time > 0:
            cmd.append(f"--start={int(start_time)}")

        # Avvio processo mpv
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Il lettore video '{self.config.player.binary}' non è installato sul sistema.\n"
                "Installalo con: sudo pacman -S mpv"
            )

        SignalHandler.register_process(proc)

        # Connessione al socket IPC in background
        ipc = MpvIpcClient(self.socket_path)
        ipc_connected = ipc.connect(timeout=3.0)

        last_pos = 0.0
        duration = 0.0
        percent = 0.0
        eof_reached = False

        try:
            while proc.poll() is None:
                if ipc_connected:
                    try:
                        p = ipc.get_property("time-pos")
                        d = ipc.get_property("duration")
                        eof = ipc.get_property("eof-reached")
                        pct = ipc.get_property("percent-pos")

                        if p is not None:
                            last_pos = float(p)
                        if d is not None:
                            duration = float(d)
                        if pct is not None:
                            percent = float(pct)
                        if eof:
                            eof_reached = True
                    except Exception:
                        pass
                time.sleep(0.5)

        finally:
            SignalHandler.unregister_process(proc)
            ipc.close()
            self._cleanup_socket()

        retcode = proc.returncode

        # Determina stato di visione
        # Considerato completato se EOF raggiunto o se l'utente ha visto almeno il 90%
        if eof_reached or percent >= 90.0:
            status = "completed"
        elif retcode == 0 or retcode is None or retcode == 2:  # mpv esce con 0 o 2 quando premuto 'q'
            status = "quit"
        else:
            status = "error"

        return PlaybackResult(
            status=status,
            time_pos=last_pos,
            duration=duration,
            percent_pos=percent,
        )
