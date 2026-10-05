"""Test unitari per MpvController e MpvIpcClient (senza avviare mpv reale)."""

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock, patch

from ani_it.config import Config
from ani_it.player import MpvController, MpvIpcClient

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"


class FakeMpv(threading.Thread):
    """Server Unix minimale che simula il lato mpv del protocollo JSON-IPC.

    Per ogni comando ricevuto chiama `handler(msg)`, che restituisce la lista di
    chunk (bytes) da scrivere sul socket, nell'ordine e con una pausa fra l'uno e l'altro.
    """

    def __init__(self, sock: socket.socket, handler: Callable[[dict[str, Any]], list[bytes]]) -> None:
        super().__init__(daemon=True)
        self.sock = sock
        self.handler = handler
        self.received: list[dict[str, Any]] = []

    def run(self) -> None:
        buffer = b""
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    self.received.append(msg)
                    for out in self.handler(msg):
                        self.sock.sendall(out)
                        time.sleep(0.02)
        except OSError:
            return


def reply(msg: dict[str, Any], data: Any = None, error: str = "success") -> bytes:
    body: dict[str, Any] = {"request_id": msg.get("request_id"), "error": error}
    if error == "success":
        body["data"] = data
    return (json.dumps(body) + "\n").encode()


class TestMpvIpcClient(unittest.TestCase):
    """Il client deve sincronizzarsi con mpv tramite request_id e scartare gli eventi."""

    def _client(self, handler: Callable[[dict[str, Any]], list[bytes]]) -> MpvIpcClient:
        server, client_sock = socket.socketpair()
        client_sock.settimeout(1.0)
        client = MpvIpcClient(Path("/non/usato"))
        client._sock = client_sock
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        FakeMpv(server, handler).start()
        return client

    def test_stale_reply_and_events_do_not_leak_into_next_answer(self) -> None:
        """Una risposta in ritardo (duration) non deve finire in eof-reached."""

        def handler(msg: dict[str, Any]) -> list[bytes]:
            return [
                # risposta vecchia di un comando precedente: va scartata
                (json.dumps({"request_id": 9999, "error": "success", "data": 1450.2}) + "\n").encode(),
                # evento asincrono: va scartato
                (json.dumps({"event": "property-change", "name": "time-pos", "data": 3.0}) + "\n").encode(),
                reply(msg, data=False),
            ]

        client = self._client(handler)
        self.assertIs(client.get_property("eof-reached"), False)

    def test_reply_split_across_multiple_chunks(self) -> None:
        """Una risposta spezzata su più recv() viene ricomposta."""

        def handler(msg: dict[str, Any]) -> list[bytes]:
            full = reply(msg, data=42.5)
            return [full[:12], full[12:30], full[30:]]

        client = self._client(handler)
        self.assertEqual(client.get_property("time-pos"), 42.5)

    def test_two_replies_in_one_chunk(self) -> None:
        """Più messaggi nello stesso chunk vengono letti separatamente."""

        def handler(msg: dict[str, Any]) -> list[bytes]:
            event = (json.dumps({"event": "playback-restart"}) + "\n").encode()
            return [event + reply(msg, data="ok")]

        client = self._client(handler)
        self.assertEqual(client.get_property("path"), "ok")

    def test_error_reply_returns_none(self) -> None:
        def handler(msg: dict[str, Any]) -> list[bytes]:
            return [reply(msg, error="property unavailable")]

        client = self._client(handler)
        self.assertIsNone(client.get_property("duration"))

    def test_no_reply_times_out(self) -> None:
        client = self._client(lambda msg: [])
        start = time.monotonic()
        self.assertIsNone(client.send_command(["get_property", "duration"], timeout=0.3))
        self.assertLess(time.monotonic() - start, 1.5)

    def test_request_ids_are_unique_and_sent(self) -> None:
        seen: list[Any] = []

        def handler(msg: dict[str, Any]) -> list[bytes]:
            seen.append(msg.get("request_id"))
            return [reply(msg, data=1)]

        client = self._client(handler)
        client.get_property("a")
        client.get_property("b")
        self.assertEqual(len(seen), 2)
        self.assertNotIn(None, seen)
        self.assertNotEqual(seen[0], seen[1])


class FakeIpc:
    """Client IPC programmabile: sostituisce MpvIpcClient nel ciclo di monitoraggio."""

    connect_after = 10 ** 9      # tentativi necessari prima che la connessione riesca
    props: dict[str, Any] = {}
    events_by_pump: dict[int, list[dict[str, Any]]] = {}
    instances: list["FakeIpc"] = []

    def __init__(self, socket_path: Any = None) -> None:
        self.events: deque[dict[str, Any]] = deque()
        self.connected = False
        self.connect_attempts = 0
        self.pumps = 0
        self.sent: list[list[Any]] = []
        type(self).instances.append(self)

    def try_connect(self) -> bool:
        self.connect_attempts += 1
        self.connected = self.connect_attempts >= self.connect_after
        return self.connected

    def get_property(self, name: str) -> Any:
        return self.props.get(name) if self.connected else None

    def pump(self, timeout: float) -> None:
        self.pumps += 1
        self.events.extend(self.events_by_pump.get(self.pumps, []))

    def send_command(self, command: list[Any], timeout: float = 2.0) -> Any:
        self.sent.append(command)

    def close(self) -> None:
        self.connected = False


def fake_ipc(connect_after: int = 10 ** 9, props: Any = None, events_by_pump: Any = None) -> type:
    """Crea una sottoclasse di FakeIpc con lo scenario indicato."""
    return type("ScenarioIpc", (FakeIpc,), {
        "connect_after": connect_after,
        "props": props or {},
        "events_by_pump": events_by_pump or {},
        "instances": [],
    })


class TestMpvController(unittest.TestCase):
    """Verifica comando, header HTTP e classificazione dell'uscita di mpv."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        env = patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.temp_dir.name})
        env.start()
        self.addCleanup(env.stop)
        self.controller = MpvController(Config())

        self.stream_data = {
            "stream_url": "https://cdn.example.com/master.m3u8",
            "headers": {
                "User-Agent": UA,
                "Referer": "https://vixcloud.co/embed/1",
                "Origin": "https://vixcloud.co",
            },
            "subtitles": [],
        }

    def _play(self, retcode: int, log_text: bytes = b"", stream_data: Any = None,
              polls: int = 0, ipc: Any = None, **play_kwargs: Any):
        """Esegue play() con Popen finto: `polls` cicli di monitoraggio prima dell'uscita."""
        proc = MagicMock()
        proc.poll.side_effect = [None] * polls + [retcode] * 50
        proc.returncode = retcode

        def fake_popen(cmd, stdout=None, stderr=None, **kwargs):
            self.cmd = cmd
            # mpv scrive i suoi messaggi su stdout: il player li deve catturare lì
            self.stderr_arg = stderr
            if log_text and hasattr(stdout, "write"):
                stdout.write(log_text)
                stdout.flush()
            return proc

        with patch("ani_it.player.subprocess.Popen", side_effect=fake_popen), \
                patch("ani_it.player.MpvIpcClient", ipc or fake_ipc()), \
                patch("ani_it.player.time.sleep"):
            return self.controller.play(stream_data or self.stream_data, "Serie", 1, **play_kwargs)

    # --- header HTTP -------------------------------------------------------

    def test_user_agent_with_comma_is_not_split(self) -> None:
        """Lo User-Agent contiene una virgola: non può stare in --http-header-fields."""
        self._play(0)
        self.assertIn(f"--user-agent={UA}", self.cmd)
        self.assertIn("--referrer=https://vixcloud.co/embed/1", self.cmd)
        for arg in self.cmd:
            self.assertFalse(arg.startswith("--http-header-fields="), arg)
            if "KHTML" in arg:
                self.assertEqual(arg, f"--user-agent={UA}")

    def test_extra_headers_are_appended_one_by_one(self) -> None:
        self._play(0)
        self.assertIn("--http-header-fields-append=Origin: https://vixcloud.co", self.cmd)
        self.assertNotIn("--http-header-fields-append=User-Agent", " ".join(self.cmd))
        self.assertNotIn("--http-header-fields-append=Referer", " ".join(self.cmd))

    def test_missing_headers_are_not_passed_empty(self) -> None:
        self._play(0, stream_data={"stream_url": "https://cdn.example.com/v.mp4", "headers": {}})
        for arg in self.cmd:
            self.assertNotEqual(arg, "--referrer=")
            self.assertNotEqual(arg, "--user-agent=")

    # --- codici di uscita --------------------------------------------------

    def test_exit_code_0_is_quit(self) -> None:
        self.assertEqual(self._play(0).status, "quit")

    def test_exit_code_2_is_error_not_quit(self) -> None:
        """mpv esce con 2 quando il file non è riproducibile."""
        self.assertEqual(self._play(2).status, "error")

    def test_other_nonzero_exit_codes_are_errors(self) -> None:
        for code in (1, 3, 4):
            with self.subTest(code=code):
                self.assertEqual(self._play(code).status, "error")

    def test_mpv_log_tail_is_returned_on_error(self) -> None:
        lines = b"".join(f"riga {i}\n".encode() for i in range(30)) + b"Failed to open stream\n"
        result = self._play(2, log_text=lines)
        self.assertIn("Failed to open stream", result.error_output)
        self.assertIn("riga 29", result.error_output)
        self.assertNotIn("riga 0\n", result.error_output)  # solo le ultime righe

    def test_stderr_is_merged_into_the_captured_log(self) -> None:
        """stderr deve confluire nello stesso log, e il log deve essere solo di errori."""
        self._play(0)
        self.assertEqual(self.stderr_arg, subprocess.STDOUT)
        self.assertIn("--msg-level=all=error", self.cmd)


    # --- verifica TLS (come aria2c e yt-dlp) -----------------------------------

    def test_mpv_verifies_tls_certificates(self) -> None:
        """mpv di suo non verifica i certificati (--tls-verify=no): va attivata esplicitamente."""
        self._play(0)
        self.assertIn("--tls-verify=yes", self.cmd)

    def test_user_can_still_override_tls_verification(self) -> None:
        self.controller.config.player.args = ["--tls-verify=no"]
        self._play(0)
        self.assertLess(self.cmd.index("--tls-verify=yes"), self.cmd.index("--tls-verify=no"))  # l'ultima vince

    # --- opzioni forzate (punto 42) ----------------------------------------

    def test_no_forced_audio_or_video_output_in_graphical_sessions(self) -> None:
        """--ao/--vo imposti senza fallback scavalcano mpv.conf e rompono i sistemi solo ALSA."""
        self.controller.config.player.args = []  # solo ciò che aggiunge l'applicazione, non gli argomenti utente
        for display in ("wayland", "x11"):
            with self.subTest(display=display), patch("ani_it.player.detect_display_server", return_value=display):
                self._play(0)
                self.assertFalse([a for a in self.cmd if a.startswith(("--ao", "--vo", "--hwdec"))], self.cmd)

    def test_tty_session_still_gets_a_video_output(self) -> None:
        with patch("ani_it.player.detect_display_server", return_value="tty"):
            self._play(0)
        self.assertIn("--vo=drm,caca", self.cmd)

    def test_user_player_args_are_still_passed(self) -> None:
        self.controller.config.player.args = ["--vo=null", "--ao=null"]
        self._play(0)
        self.assertIn("--vo=null", self.cmd)
        self.assertIn("--ao=null", self.cmd)

    # --- callback di pulizia (punto 43) -------------------------------------

    def test_cleanup_callbacks_do_not_accumulate_across_plays(self) -> None:
        from ani_it.utils import SignalHandler

        before = list(SignalHandler._cleanup_callbacks)
        for _ in range(5):
            self._play(0)
        self.assertEqual(SignalHandler._cleanup_callbacks, before)

    def test_cleanup_callback_is_unregistered_even_if_player_is_missing(self) -> None:
        from ani_it.utils import SignalHandler

        before = list(SignalHandler._cleanup_callbacks)
        with patch("ani_it.player.subprocess.Popen", side_effect=FileNotFoundError), \
                patch("ani_it.player.MpvIpcClient", fake_ipc()):
            with self.assertRaises(FileNotFoundError):
                self.controller.play(self.stream_data, "Serie", 1)
        self.assertEqual(SignalHandler._cleanup_callbacks, before)

    # --- socket per istanza (punto 22) --------------------------------------

    def test_socket_name_contains_pid(self) -> None:
        """Due istanze di ani-it non devono usare (e cancellarsi) lo stesso socket."""
        self.assertEqual(self.controller.socket_path.name, f"ani-it-mpv-{os.getpid()}.sock")

    # --- posizione e qualità (punti 14, 20) ---------------------------------

    def test_save_position_on_quit_is_not_used(self) -> None:
        """Non funziona con URL che cambiano token: la posizione la salva ani-it in cronologia."""
        self._play(0)
        self.assertNotIn("--save-position-on-quit", self.cmd)

    def test_start_time_is_passed_to_mpv(self) -> None:
        self._play(0, start_time=734.9)
        self.assertIn("--start=734", self.cmd)

    def test_no_start_option_without_start_time(self) -> None:
        self._play(0)
        self.assertFalse([a for a in self.cmd if a.startswith("--start")])

    def test_hls_bitrate_selects_variant(self) -> None:
        self._play(0, stream_data={**self.stream_data, "hls_bitrate": 1800000})
        self.assertIn("--hls-bitrate=1800000", self.cmd)

    def test_no_hls_bitrate_option_when_unset(self) -> None:
        self._play(0, stream_data={**self.stream_data, "hls_bitrate": None})
        self.assertFalse([a for a in self.cmd if a.startswith("--hls-bitrate")])

    # --- fine episodio e connessione IPC (punto 21) --------------------------

    def test_ipc_connection_is_retried_while_mpv_runs(self) -> None:
        """Se il socket compare dopo qualche secondo la connessione va ritentata nel ciclo."""
        ipc = fake_ipc(connect_after=4, props={"time-pos": 12.0, "duration": 1400.0, "percent-pos": 0.8})
        result = self._play(0, polls=8, ipc=ipc)
        instance = ipc.instances[0]
        self.assertGreaterEqual(instance.connect_attempts, 4)
        self.assertEqual(result.time_pos, 12.0)
        self.assertEqual(result.duration, 1400.0)

    def test_end_file_eof_event_marks_completed_without_keep_open(self) -> None:
        """Senza --keep-open mpv esce a fine file: eof-reached non è leggibile, l'evento sì."""
        ipc = fake_ipc(connect_after=1, props={"time-pos": 1399.0, "duration": 1400.0},
                       events_by_pump={2: [{"event": "end-file", "reason": "eof"}]})
        result = self._play(0, polls=3, ipc=ipc)
        self.assertEqual(result.status, "completed")

    def test_end_file_quit_event_is_not_completion(self) -> None:
        ipc = fake_ipc(connect_after=1, props={"time-pos": 30.0, "duration": 1400.0, "percent-pos": 2.0},
                       events_by_pump={2: [{"event": "end-file", "reason": "quit"}]})
        self.assertEqual(self._play(0, polls=3, ipc=ipc).status, "quit")

    def test_end_file_arriving_right_before_exit_is_not_lost(self) -> None:
        """mpv può uscire subito dopo end-file: l'ultimo evento va letto prima di chiudere."""
        ipc = fake_ipc(connect_after=1, props={"time-pos": 1399.0, "duration": 1400.0},
                       events_by_pump={3: [{"event": "end-file", "reason": "eof"}]})
        result = self._play(0, polls=2, ipc=ipc)  # il ciclo termina prima del 3° pump: serve il drain finale
        self.assertEqual(result.status, "completed")

    def test_eof_reached_property_still_works_with_keep_open(self) -> None:
        ipc = fake_ipc(connect_after=1, props={"time-pos": 1400.0, "duration": 1400.0, "eof-reached": True})
        self.assertEqual(self._play(0, polls=2, ipc=ipc).status, "completed")

    def test_quit_is_sent_at_eof_when_requested(self) -> None:
        """Con auto_next e keep-open mpv resterebbe aperto a fine file: va chiuso da ani-it."""
        ipc = fake_ipc(connect_after=1, props={"time-pos": 1400.0, "duration": 1400.0, "eof-reached": True})
        self._play(0, polls=3, ipc=ipc, quit_on_eof=True)
        self.assertEqual(ipc.instances[0].sent.count(["quit"]), 1)

    def test_quit_is_not_sent_by_default(self) -> None:
        ipc = fake_ipc(connect_after=1, props={"time-pos": 1400.0, "duration": 1400.0, "eof-reached": True})
        self._play(0, polls=3, ipc=ipc)
        self.assertEqual(ipc.instances[0].sent, [])


class TestMpvIpcConnection(unittest.TestCase):
    """try_connect, pump ed eventi su un vero socket Unix."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "m.sock"

    def serve(self) -> socket.socket:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.path))
        server.listen(1)
        self.addCleanup(server.close)
        return server

    def test_try_connect_is_a_single_non_blocking_attempt(self) -> None:
        client = MpvIpcClient(self.path)
        start = time.monotonic()
        self.assertFalse(client.try_connect())
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertFalse(client.connected)

        self.serve()
        self.assertTrue(client.try_connect())
        self.assertTrue(client.connected)
        client.close()

    def test_pump_collects_events_and_ignores_replies(self) -> None:
        server = self.serve()
        client = MpvIpcClient(self.path)
        self.assertTrue(client.try_connect())
        conn, _ = server.accept()
        self.addCleanup(conn.close)
        conn.sendall(b'{"event":"start-file"}\n{"request_id":9,"error":"success","data":1}\n{"event":"end-file","reason":"eof"}\n')

        client.pump(0.3)
        self.assertEqual([e["event"] for e in client.events], ["start-file", "end-file"])
        client.close()

    def test_pump_returns_when_mpv_closes_the_socket(self) -> None:
        server = self.serve()
        client = MpvIpcClient(self.path)
        client.try_connect()
        conn, _ = server.accept()
        conn.sendall(b'{"event":"end-file","reason":"eof"}\n')
        conn.close()

        start = time.monotonic()
        client.pump(5.0)
        self.assertLess(time.monotonic() - start, 2.0)  # non attende l'intero timeout
        self.assertEqual(client.events[0]["reason"], "eof")
        self.assertFalse(client.connected)


if __name__ == "__main__":
    unittest.main()
