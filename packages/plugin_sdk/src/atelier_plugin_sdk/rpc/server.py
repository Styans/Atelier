"""The plugin side of the subprocess protocol.

Requests (host -> plugin): ``atelier.initialize``, ``atelier.health``,
``atelier.normalize_params``, ``atelier.invoke``, ``atelier.cancel``, ``atelier.shutdown``.
Notifications (plugin -> host): ``atelier.progress``, ``atelier.log``.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import IO, Any

from atelier_plugin_sdk.context import RunContext
from atelier_plugin_sdk.dispatch import invoke, normalize_params
from atelier_plugin_sdk.errors import PluginError
from atelier_plugin_sdk.manifest import API_VERSION
from atelier_plugin_sdk.plugin import Plugin
from atelier_plugin_sdk.roles import ROLE_NAMES, RoleName
from atelier_plugin_sdk.rpc.codec import decode, dumps, encode, error_payload


def protect_stdout() -> IO[str]:
    """Keep the real stdout for the protocol and send everything else printed to stderr."""
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(protocol_fd, "w", encoding="utf-8", newline="\n", buffering=1)


class PluginServer:
    def __init__(self, plugin: Plugin, out: IO[str]) -> None:
        self.plugin = plugin
        self.out = out
        self._write_lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._worker: threading.Thread | None = None

    # --- output ---

    def _send(self, message: dict[str, Any]) -> None:
        line = dumps({"jsonrpc": "2.0", **message})
        with self._write_lock:
            self.out.write(line + "\n")
            self.out.flush()

    def _reply(self, request_id: Any, result: Any) -> None:
        self._send({"id": request_id, "result": result})

    def _fail(self, request_id: Any, exc: BaseException) -> None:
        self._send({"id": request_id, "error": error_payload(exc)})

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    # --- methods ---

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("api_version") != API_VERSION:
            asked = params.get("api_version")
            raise PluginError(f"plugin speaks api_version {API_VERSION}, host asked for {asked}")
        return {
            "api_version": API_VERSION,
            "components": [
                {
                    "role": c.role,
                    "name": c.name,
                    "version": c.version,
                    "description": c.description(),
                    "params_schema": c.Params.model_json_schema(),
                }
                for c in self.plugin.components
            ],
        }

    def _component_from(self, params: dict[str, Any]) -> Any:
        role = params["role"]
        if role not in ROLE_NAMES:
            raise PluginError(f"unknown role {role!r}")
        role_name: RoleName = role
        return self.plugin.component(role_name, params["component"])

    def _invoke(self, request_id: Any, params: dict[str, Any]) -> None:
        call_id = str(params["call_id"])
        cancel = self._cancel_events.setdefault(call_id, threading.Event())
        try:
            component = self._component_from(params)
            ctx_spec = params["ctx"]
            workdir = Path(ctx_spec["workdir"])
            ctx = RunContext(
                seed=int(ctx_spec["seed"]),
                workdir=workdir,
                config=ctx_spec.get("config", {}),
                on_progress=lambda f, m: self._notify(
                    "atelier.progress", {"call_id": call_id, "fraction": f, "message": m}
                ),
                on_log=lambda level, m: self._notify(
                    "atelier.log", {"call_id": call_id, "level": level, "message": m}
                ),
                cancel_event=cancel,
            )
            result = invoke(component, decode(params["args"]), params.get("params", {}), ctx)
            self._reply(request_id, encode(result, workdir))
        except BaseException as exc:
            if not isinstance(exc, PluginError):
                traceback.print_exc(file=sys.stderr)
            self._fail(request_id, exc)
        finally:
            self._cancel_events.pop(call_id, None)

    def handle(self, message: dict[str, Any]) -> bool:
        """Process one request; returns False when the server should stop."""
        request_id = message.get("id")
        method = message.get("method", "")
        params = message.get("params") or {}
        try:
            if method == "atelier.invoke":
                if self._worker is not None and self._worker.is_alive():
                    raise PluginError("a call is already running")
                self._worker = threading.Thread(
                    target=self._invoke, args=(request_id, params), daemon=True
                )
                self._worker.start()
            elif method == "atelier.initialize":
                self._reply(request_id, self._initialize(params))
            elif method == "atelier.health":
                self._reply(request_id, self.plugin.health_check().model_dump(mode="json"))
            elif method == "atelier.normalize_params":
                component = self._component_from(params)
                self._reply(request_id, normalize_params(component, params.get("params", {})))
            elif method == "atelier.cancel":
                event = self._cancel_events.get(str(params.get("call_id")))
                if event is not None:
                    event.set()
                self._reply(request_id, {"cancelled": event is not None})
            elif method == "atelier.shutdown":
                if self._worker is not None:
                    self._worker.join(timeout=5)
                self._reply(request_id, {"ok": True})
                return False
            else:
                self._send(
                    {"id": request_id, "error": {"code": -32601, "message": f"no method {method}"}}
                )
        except Exception as exc:
            self._fail(request_id, exc)
        return True

    def serve(self, lines: IO[str]) -> None:
        for line in lines:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                self._send({"id": None, "error": {"code": -32700, "message": str(exc)}})
                continue
            if not self.handle(message):
                break
        if self._worker is not None:
            self._worker.join(timeout=5)
