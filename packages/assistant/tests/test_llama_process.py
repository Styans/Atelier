"""The llama-server process lifecycle, with a small Python HTTP server standing in for it."""

import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from atelier_assistant import LlamaServer, LlamaServerSettings, LLMError

FAKE_SERVER = textwrap.dedent(
    """
    import http.server, sys

    port, mode, ready_after = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    print("fake llama-server", mode, flush=True)
    if mode == "crash":
        print("ggml_cuda_init: out of memory", flush=True)
        sys.exit(3)
    polls = 0

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            global polls
            polls += 1
            ready = mode == "ok" and polls > ready_after
            body = b'{"status": "ok"}' if ready else b'{"error": {"message": "Loading model"}}'
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    """
)


class FakeServer(LlamaServer):
    """Runs the stand-in instead of the real executable; everything else is the real code."""

    script: Path
    mode = "ok"
    ready_after = 2

    def command(self, port: int) -> list[str]:
        return [sys.executable, str(self.script), str(port), self.mode, str(self.ready_after)]


@pytest.fixture
def settings(tmp_path: Path) -> LlamaServerSettings:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    return LlamaServerSettings(exe=Path(sys.executable), model=model, startup_timeout_s=30)


@pytest.fixture
def make_server(
    tmp_path: Path, settings: LlamaServerSettings
) -> Iterator[Callable[..., FakeServer]]:
    script = tmp_path / "fake_llama_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    servers: list[FakeServer] = []

    def make(mode: str = "ok", **overrides: Any) -> FakeServer:
        server = FakeServer(
            settings.model_copy(update=overrides), tmp_path / "logs" / "llama-server.log"
        )
        server.script, server.mode = script, mode
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.stop()


def test_command_line(tmp_path: Path) -> None:
    exe, model = tmp_path / "llama dir" / "llama-server.exe", tmp_path / "Qwen3VL-8B.gguf"
    server = LlamaServer(LlamaServerSettings(exe=exe, model=model), tmp_path / "log")

    assert server.command(5123) == [
        str(exe),
        "-m", str(model),
        "-c", "16384",
        "-ngl", "99",
        "-fa", "on",
        "--host", "127.0.0.1",
        "--port", "5123",
        "--no-webui",
        "-np", "1",
        "--jinja",
    ]  # fmt: skip


def test_missing_files_are_reported(tmp_path: Path, settings: LlamaServerSettings) -> None:
    no_exe = LlamaServer(settings.model_copy(update={"exe": tmp_path / "nope.exe"}), tmp_path / "l")
    with pytest.raises(LLMError, match=r"не найден llama-server: .*nope\.exe"):
        no_exe.start()

    no_model = LlamaServer(
        settings.model_copy(update={"model": tmp_path / "nope.gguf"}), tmp_path / "l"
    )
    assert no_model.missing() is not None
    with pytest.raises(LLMError, match=r"не найдены веса модели: .*nope\.gguf"):
        no_model.start()
    assert not no_model.running()


def test_start_waits_for_health_then_stop(make_server: Callable[..., FakeServer]) -> None:
    server = make_server()

    server.start()

    assert server.running()
    assert server.last_startup_s is not None and server.last_startup_s > 0
    assert httpx.get(server.url + "/health").json() == {"status": "ok"}
    port = server.port
    server.start()  # already running: nothing happens
    assert server.port == port

    server.stop()

    assert not server.running()
    with pytest.raises(httpx.TransportError):  # Windows may time out instead of refusing
        httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
    server.stop()  # stopping twice is fine
    assert "fake llama-server ok" in server.log_path.read_text(encoding="utf-8")


def test_exit_during_startup_shows_the_log_tail(make_server: Callable[..., FakeServer]) -> None:
    server = make_server("ok")
    server.start()
    server.stop()
    server.mode = "crash"

    with pytest.raises(LLMError) as error:
        server.start()

    message = str(error.value)
    assert "завершился, код 3" in message
    assert "out of memory" in message
    assert "fake llama-server ok" not in message  # only this start's part of the log
    assert not server.running()


def test_startup_timeout_stops_the_process(make_server: Callable[..., FakeServer]) -> None:
    server = make_server("loading", startup_timeout_s=1.5)

    with pytest.raises(LLMError, match=r"не загрузилась \(лимит ожидания 2 секунд\)"):
        server.start()

    assert not server.running()


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows-only")
def test_server_dies_with_its_parent(tmp_path: Path, settings: LlamaServerSettings) -> None:
    """The worker crashing must not leave llama-server holding the video memory."""
    script = tmp_path / "fake_llama_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    parent = tmp_path / "parent.py"
    parent.write_text(
        textwrap.dedent(
            f"""
            import sys, time
            from pathlib import Path
            from atelier_assistant import LlamaServer, LlamaServerSettings

            class Fake(LlamaServer):
                def command(self, port):
                    return [sys.executable, {str(script)!r}, str(port), "ok", "0"]

            settings = LlamaServerSettings.model_validate_json({settings.model_dump_json()!r})
            server = Fake(settings, Path({str(tmp_path / "parent.log")!r}))
            server.start()
            print(server.port, flush=True)
            time.sleep(60)
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, str(parent)], stdout=subprocess.PIPE, text=True, env=dict(os.environ)
    )
    try:
        assert proc.stdout is not None
        port = int(proc.stdout.readline())
        assert httpx.get(f"http://127.0.0.1:{port}/health").status_code == 200
    finally:
        proc.kill()  # TerminateProcess: no cleanup code runs in the parent
        proc.wait()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
        except httpx.HTTPError:
            return
        time.sleep(0.2)
    pytest.fail("llama-server outlived its parent")
