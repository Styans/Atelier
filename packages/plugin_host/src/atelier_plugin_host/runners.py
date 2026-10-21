"""Running plugin components in this process or in a subprocess (JSON-RPC over stdio)."""

from __future__ import annotations

import collections
import contextlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, Any, Protocol

from atelier_plugin_sdk import API_VERSION, Health, Plugin, PluginError, PluginManifest, RunContext
from atelier_plugin_sdk.dispatch import invoke, normalize_params
from atelier_plugin_sdk.roles import RoleName
from atelier_plugin_sdk.rpc.codec import decode, dumps, encode, error_from_payload

ProgressCallback = Callable[[float, str], None]
LogCallback = Callable[[str, str], None]


class Runner(Protocol):
    def describe(self) -> list[dict[str, Any]]: ...
    def health(self) -> Health: ...
    def normalize_params(
        self, role: RoleName, name: str, params: Mapping[str, Any]
    ) -> dict[str, Any]: ...
    def invoke(
        self,
        role: RoleName,
        name: str,
        args: Mapping[str, Any],
        params: Mapping[str, Any],
        *,
        seed: int,
        workdir: Path,
        config: Mapping[str, str],
        on_progress: ProgressCallback | None,
        on_log: LogCallback | None,
    ) -> Any: ...
    def close(self) -> None: ...


def describe_plugin(plugin: Plugin) -> list[dict[str, Any]]:
    return [
        {
            "role": c.role,
            "name": c.name,
            "version": c.version,
            "description": c.description(),
            "params_schema": c.Params.model_json_schema(),
        }
        for c in plugin.components
    ]


class InProcessRunner:
    def __init__(self, plugin: Plugin) -> None:
        self.plugin = plugin

    def describe(self) -> list[dict[str, Any]]:
        return describe_plugin(self.plugin)

    def health(self) -> Health:
        return self.plugin.health_check()

    def normalize_params(
        self, role: RoleName, name: str, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return normalize_params(self.plugin.component(role, name), params)

    def invoke(
        self,
        role: RoleName,
        name: str,
        args: Mapping[str, Any],
        params: Mapping[str, Any],
        *,
        seed: int,
        workdir: Path,
        config: Mapping[str, str],
        on_progress: ProgressCallback | None,
        on_log: LogCallback | None,
    ) -> Any:
        ctx = RunContext(
            seed=seed, workdir=workdir, config=config, on_progress=on_progress, on_log=on_log
        )
        return invoke(self.plugin.component(role, name), args, params, ctx)

    def close(self) -> None:
        pass


def kill_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


class SubprocessRunner:
    """A plugin living in its own process (possibly its own venv).

    The process starts lazily, stays warm between calls and is stopped by ``close()``.
    """

    def __init__(
        self,
        manifest: PluginManifest,
        root: Path,
        *,
        log_path: Path | None = None,
        call_timeout_s: float = 3600.0,
        request_timeout_s: float = 60.0,
        startup_timeout_s: float = 900.0,
    ) -> None:
        self.manifest = manifest
        self.root = root
        self.log_path = log_path
        self.call_timeout_s = call_timeout_s
        self.request_timeout_s = request_timeout_s
        self.startup_timeout_s = startup_timeout_s  # the first start may build a venv
        self._proc: subprocess.Popen[str] | None = None
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._next_id = 0
        self._lock = threading.Lock()
        self._call_lock = threading.Lock()
        self._callbacks: tuple[ProgressCallback | None, LogCallback | None] = (None, None)
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=40)
        self._log_file: IO[str] | None = None
        self._threads: list[threading.Thread] = []

    @property
    def started(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def command(self) -> list[str]:
        src = self.root / "src"
        import_path = src if src.is_dir() else self.root
        if not self.manifest.command:
            return [
                sys.executable,
                "-m",
                "atelier_plugin_sdk.serve",
                "--entry",
                self.manifest.entry,
                "--path",
                str(import_path),
            ]
        values = {
            "python": sys.executable,
            "uv": os.environ.get("UV") or shutil.which("uv") or "uv",
            "entry": self.manifest.entry,
            "plugin_dir": str(self.root),
            "src": str(import_path),
        }
        return [part.format(**values) for part in self.manifest.command]

    def _start(self) -> subprocess.Popen[str]:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(
            self.command(),
            cwd=self.root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=flags,
        )
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = self.log_path.open("a", encoding="utf-8")
        self._threads = [
            threading.Thread(target=self._read_stdout, args=(self._proc,), daemon=True),
            threading.Thread(target=self._read_stderr, args=(self._proc,), daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        return self._proc

    def _read_stdout(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._stderr_tail.append(f"[non-protocol stdout] {line.rstrip()}")
                continue
            request_id = message.get("id")
            if request_id is not None and ("result" in message or "error" in message):
                waiter = self._pending.get(int(request_id))
                if waiter is not None:
                    waiter.put(message)
                continue
            on_progress, on_log = self._callbacks
            params = message.get("params", {})
            if message.get("method") == "atelier.progress" and on_progress:
                on_progress(float(params.get("fraction", 0.0)), str(params.get("message", "")))
            elif message.get("method") == "atelier.log" and on_log:
                on_log(str(params.get("level", "info")), str(params.get("message", "")))
        # The process is gone: let it finish and drain stderr so the error says why.
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
        for thread in self._threads[1:]:
            thread.join(timeout=5)
        for waiter in list(self._pending.values()):
            waiter.put({"error": {"code": 1001, "message": self._exit_message()}})

    def _read_stderr(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            self._stderr_tail.append(line.rstrip())
            if self._log_file is not None:
                self._log_file.write(line)
                self._log_file.flush()

    def _exit_message(self) -> str:
        code = self._proc.poll() if self._proc else None
        tail = "\n".join(self._stderr_tail)
        return f"plugin {self.manifest.id} process exited (code {code}). Last stderr:\n{tail}"

    def request(self, method: str, params: dict[str, Any], timeout: float | None = None) -> Any:
        proc = self._start()
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            waiter: queue.Queue[dict[str, Any]] = queue.Queue()
            self._pending[request_id] = waiter
            message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            assert proc.stdin is not None
            try:
                proc.stdin.write(dumps(message) + "\n")
                proc.stdin.flush()
            except OSError as exc:
                self._pending.pop(request_id, None)
                raise PluginError(self._exit_message()) from exc
        try:
            response = waiter.get(timeout=timeout or self.request_timeout_s)
        except queue.Empty:
            kill_process_tree(proc)
            raise PluginError(
                f"plugin {self.manifest.id}: {method} timed out after "
                f"{timeout or self.request_timeout_s:.0f} s; the process was stopped"
            ) from None
        finally:
            self._pending.pop(request_id, None)
        if "error" in response:
            raise error_from_payload(response["error"])
        return response["result"]

    def describe(self) -> list[dict[str, Any]]:
        result = self.request(
            "atelier.initialize",
            {"api_version": API_VERSION, "host_version": "atelier"},
            timeout=self.startup_timeout_s,
        )
        components: list[dict[str, Any]] = result["components"]
        return components

    def health(self) -> Health:
        return Health.model_validate(self.request("atelier.health", {}))

    def normalize_params(
        self, role: RoleName, name: str, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        result: dict[str, Any] = self.request(
            "atelier.normalize_params", {"role": role, "component": name, "params": dict(params)}
        )
        return result

    def invoke(
        self,
        role: RoleName,
        name: str,
        args: Mapping[str, Any],
        params: Mapping[str, Any],
        *,
        seed: int,
        workdir: Path,
        config: Mapping[str, str],
        on_progress: ProgressCallback | None,
        on_log: LogCallback | None,
    ) -> Any:
        with self._call_lock:
            self._callbacks = (on_progress, on_log)
            try:
                payload = {
                    "call_id": uuid.uuid4().hex,
                    "role": role,
                    "component": name,
                    "args": encode(dict(args), workdir / "rpc"),
                    "params": dict(params),
                    "ctx": {"seed": seed, "workdir": str(workdir), "config": dict(config)},
                }
                return decode(self.request("atelier.invoke", payload, timeout=self.call_timeout_s))
            finally:
                self._callbacks = (None, None)

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                self.request("atelier.shutdown", {}, timeout=10)
                proc.wait(timeout=10)
            except (PluginError, subprocess.TimeoutExpired):
                kill_process_tree(proc)
        for thread in self._threads:
            thread.join(timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):  # the pipe may already be broken
                    stream.close()
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
        self._proc = None
