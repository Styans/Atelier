"""The local model: llama.cpp's ``llama-server`` over its OpenAI-compatible API (ADR-034).

The worker owns one server process. It is started on demand on a free 127.0.0.1 port and takes
~6 GB of video memory (Qwen3-VL-8B Q4_K_M, 16K context), so it cannot share an 8 GB card with the
image and mesh models: the worker stops it before every GPU job. On Windows the process is bound
to a job object that kills it when the worker ends, however it ends.
"""

from __future__ import annotations

import contextlib
import json
import logging
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from atelier_assistant.llm import (
    AssistantMessage,
    Block,
    LLMError,
    Message,
    ModelT,
    OnText,
    StopReason,
    ToolSpec,
    Usage,
)

log = logging.getLogger("atelier.assistant.llama_server")

CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
STOP_TIMEOUT_S = 10.0
HEALTH_POLL_S = 0.25
LOG_TAIL_CHARS = 1500
STOP_REASONS: dict[str, StopReason] = {"tool_calls": "tool_use", "length": "max_tokens"}
ERROR_PREFIX = "ОШИБКА: "
INVALID_JSON_KEY = "__invalid_json__"
RETRY_TEMPERATURE = 0.8
RETRY_REPEAT_PENALTY = 1.25
SCHEMA_HINT = "Ответь одним JSON-объектом по этой JSON-схеме:"


class LlamaServerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exe: Path = Field(description="llama-server executable")
    model: Path = Field(description="GGUF weights")
    ctx: int = Field(16384, gt=0)
    gpu_layers: int = Field(99, ge=0)
    temperature: float = Field(0.4, ge=0)
    max_tokens: int = Field(2048, gt=0)
    startup_timeout_s: float = Field(180.0, gt=0)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


class _KillOnCloseJob:
    """Windows: a job object that kills its processes when its last handle closes.

    The handle is never closed explicitly, so it closes when this process ends (normally,
    by a crash, or from the task manager) and takes llama-server and its video memory with it.
    """

    _handle: int | None = None

    @classmethod
    def assign(cls, pid: int) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        if cls._handle is None:

            class BasicLimits(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IoCounters(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

            class ExtendedLimits(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", BasicLimits),
                    ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                raise ctypes.WinError(ctypes.get_last_error())
            info = ExtendedLimits()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            extended_limit_information = 9
            if not kernel32.SetInformationJobObject(
                job, extended_limit_information, ctypes.byref(info), ctypes.sizeof(info)
            ):
                error = ctypes.get_last_error()
                kernel32.CloseHandle(job)
                raise ctypes.WinError(error)
            cls._handle = job

        process_set_quota, process_terminate = 0x0100, 0x0001
        process = kernel32.OpenProcess(process_set_quota | process_terminate, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not kernel32.AssignProcessToJobObject(cls._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel32.CloseHandle(process)


class LlamaServer:
    """One ``llama-server`` process, started on demand and stopped for GPU jobs. Thread-safe."""

    def __init__(self, settings: LlamaServerSettings, log_path: Path) -> None:
        self.settings = settings
        self.log_path = log_path
        self.port = 0
        self.last_startup_s: float | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def missing(self) -> str | None:
        """Why the server cannot start (in Russian), or None when the files are in place."""
        if not self.settings.exe.is_file():
            return f"не найден llama-server: {self.settings.exe}"
        if not self.settings.model.is_file():
            return f"не найдены веса модели: {self.settings.model}"
        return None

    def command(self, port: int) -> list[str]:
        s = self.settings
        # No --mmproj: the vision projector costs video memory and phase 3 sends no pictures.
        return [
            str(s.exe),
            "-m", str(s.model),
            "-c", str(s.ctx),
            "-ngl", str(s.gpu_layers),
            "-fa", "on",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--no-webui",
            "-np", "1",
            "--jinja",
        ]  # fmt: skip

    def start(self) -> None:
        """Start the server if it is not running and wait until the model is loaded. Blocking."""
        with self._lock:
            if self.running():
                return
            reason = self.missing()
            if reason is not None:
                raise LLMError(f"Локальная модель недоступна: {reason}")
            self.port = free_port()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("ab") as logfile:
                log_offset = logfile.tell()
                try:
                    self._proc = subprocess.Popen(
                        self.command(self.port),
                        stdin=subprocess.DEVNULL,
                        stdout=logfile,
                        stderr=subprocess.STDOUT,
                        cwd=self.settings.exe.resolve().parent,  # it loads its DLLs from there
                        creationflags=CREATION_FLAGS,
                    )
                except OSError as e:
                    raise LLMError(f"Ошибка запуска llama-server: {e}") from None
            if sys.platform == "win32":
                try:
                    _KillOnCloseJob.assign(self._proc.pid)
                except OSError:  # only a safety net; the server still works without it
                    log.warning("could not bind llama-server to this process", exc_info=True)
            log.info("llama-server starting on port %d (pid %d)", self.port, self._proc.pid)
            self._wait_ready(self._proc, log_offset)

    def _wait_ready(self, proc: subprocess.Popen[bytes], log_offset: int) -> None:
        started = time.monotonic()
        deadline = started + self.settings.startup_timeout_s
        with httpx.Client(timeout=2.0) as client:
            while time.monotonic() < deadline:
                code = proc.poll()
                if code is not None:
                    self._proc = None
                    raise LLMError(
                        f"Локальная модель не запустилась: llama-server завершился, код {code} "
                        "(возможно, не хватает видеопамяти).\n"
                        f"Конец журнала {self.log_path}:\n{self._log_tail(log_offset)}"
                    )
                if _healthy(client, self.url):
                    self.last_startup_s = time.monotonic() - started
                    log.info("llama-server ready in %.1f s", self.last_startup_s)
                    return
                time.sleep(HEALTH_POLL_S)
        self._stop_locked()
        raise LLMError(
            "Локальная модель не загрузилась "
            f"(лимит ожидания {self.settings.startup_timeout_s:.0f} секунд).\n"
            f"Конец журнала {self.log_path}:\n{self._log_tail(log_offset)}"
        )

    def _log_tail(self, offset: int) -> str:
        try:
            with self.log_path.open("rb") as f:
                f.seek(offset)
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            return "(журнал недоступен)"
        return text[-LOG_TAIL_CHARS:].strip() or "(пусто)"

    def stop(self) -> None:
        """Stop the server and free its video memory: terminate, kill after 10 s."""
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        log.info("stopping llama-server (pid %d)", proc.pid)
        proc.terminate()
        try:
            proc.wait(STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _healthy(client: httpx.Client, url: str) -> bool:
    """llama-server answers 503 while the model loads and ``{"status": "ok"}`` once it can serve."""
    try:
        response = client.get(url + "/health")
        return response.status_code == 200 and response.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False


# --- the client -----------------------------------------------------------------------------


def to_openai_messages(system: str, messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Neutral history -> OpenAI chat messages (what llama-server's chat template expects)."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        blocks = message["content"]
        text = "\n\n".join(b["text"] for b in blocks if b["type"] == "text" and b["text"])
        if message["role"] == "assistant":
            reply: dict[str, Any] = {"role": "assistant", "content": text}
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {
                        "name": b["name"],
                        "arguments": json.dumps(b["input"], ensure_ascii=False),
                    },
                }
                for b in blocks
                if b["type"] == "tool_use"
            ]
            if calls:
                reply["tool_calls"] = calls
            out.append(reply)
            continue
        for b in blocks:
            if b["type"] == "tool_result":
                content = b["content"] or "(пусто)"
                if b.get("is_error"):
                    content = ERROR_PREFIX + content
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": content})
        if text:
            out.append({"role": "user", "content": text})
    return out


def to_openai_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.schema},
        }
        for t in tools
    ]


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema with every object closed (``additionalProperties: false``).

    Limits (lengths, ranges) stay: llama.cpp turns them into its grammar, so a small model
    cannot loop inside one string until the answer is cut off. Pydantic checks the rest.
    """

    def close(node: Any, named: bool = False) -> Any:
        """``named``: the keys of ``node`` are field or definition names, not keywords."""
        if isinstance(node, dict):
            out = {
                k: close(v, named=not named and k in ("properties", "$defs"))
                for k, v in node.items()
                if named or k != "title"
            }
            if not named and out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [close(v) for v in node]
        return node

    schema: dict[str, Any] = close(model.model_json_schema())
    return schema


def _sse_data(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """The JSON payloads of ``data:`` lines, up to ``[DONE]``."""
    for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        if not data:
            continue
        try:
            chunk = json.loads(data)
        except ValueError:
            raise LLMError(f"Локальная модель прислала неразборчивый поток: {data[:200]}") from None
        if isinstance(chunk, dict):
            yield chunk


def _error_text(payload: Any) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or payload)


class _ToolCall:
    def __init__(self) -> None:
        self.id = ""
        self.name = ""
        self.arguments = ""

    def block(self) -> Block:
        try:
            parsed = json.loads(self.arguments) if self.arguments.strip() else {}
        except ValueError:
            parsed = None
        args = parsed if isinstance(parsed, dict) else {INVALID_JSON_KEY: self.arguments}
        call_id = self.id or f"call_{uuid.uuid4().hex[:12]}"
        return {"type": "tool_use", "id": call_id, "name": self.name, "input": args}


class LlamaServerClient:
    """``LLMClient`` over a local llama-server; starts the server on the first request."""

    def __init__(
        self,
        server: LlamaServer,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 600.0,
    ) -> None:
        self.server = server
        self._http = httpx.Client(
            transport=transport, timeout=httpx.Timeout(timeout_s, connect=10.0)
        )

    def close(self) -> None:
        self._http.close()

    def _url(self) -> str:
        self.server.start()
        return self.server.url + "/v1/chat/completions"

    def respond(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        on_text: OnText | None = None,
    ) -> AssistantMessage:
        body: dict[str, Any] = {
            "messages": to_openai_messages(system, messages),
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": self.server.settings.max_tokens,
            "temperature": self.server.settings.temperature,
        }
        if tools:
            body["tools"] = to_openai_tools(tools)
        text: list[str] = []
        calls: dict[int, _ToolCall] = {}
        stop: StopReason = "end_turn"
        usage: dict[str, Any] = {}
        url = self._url()
        try:
            with self._http.stream("POST", url, json=body) as response:
                if response.status_code != 200:
                    raise _status_error(response.status_code, response.read())
                for chunk in _sse_data(response.iter_lines()):
                    if "error" in chunk:
                        raise LLMError(f"Локальная модель вернула ошибку: {_error_text(chunk)}")
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            text.append(delta["content"])
                            if on_text is not None:
                                on_text(delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            call = calls.setdefault(tc.get("index", 0), _ToolCall())
                            fn = tc.get("function") or {}
                            call.id = tc.get("id") or call.id
                            call.name = fn.get("name") or call.name
                            call.arguments += fn.get("arguments") or ""
                        reason = choice.get("finish_reason")
                        if reason:
                            stop = STOP_REASONS.get(reason, "end_turn")
        except httpx.HTTPError as e:
            raise LLMError(f"Локальная модель не отвечает: {e}") from None
        content: list[Block] = []
        joined = "".join(text)
        if joined.strip():
            content.append({"type": "text", "text": joined})
        content += [calls[i].block() for i in sorted(calls)]
        if calls and stop == "end_turn":
            stop = "tool_use"
        return AssistantMessage(content, stop, _usage(usage))

    def structured(self, system: str, messages: Sequence[Message], model: type[ModelT]) -> ModelT:
        """A JSON answer by the schema, validated. A small model sometimes loops inside one string
        and never closes the JSON: the second try is livelier and penalises repetition.

        The grammar only constrains the answer; the model learns what the fields mean (their
        descriptions, units) from the schema appended to the system prompt.
        """
        schema = strict_schema(model)
        compact = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        body: dict[str, Any] = {
            "messages": to_openai_messages(f"{system}\n\n{SCHEMA_HINT}\n{compact}", messages),
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": model.__name__, "strict": True, "schema": schema},
            },
            "max_tokens": self.server.settings.max_tokens,
            "temperature": self.server.settings.temperature,
        }
        problem = ""
        for attempt in range(2):
            if attempt:
                body["temperature"] = max(RETRY_TEMPERATURE, self.server.settings.temperature)
                body["repeat_penalty"] = RETRY_REPEAT_PENALTY
            choice = self._complete(body)
            text = (choice.get("message") or {}).get("content") or ""
            if choice.get("finish_reason") == "length":
                problem = "ответ оборвался на лимите длины"
            else:
                try:
                    return model.model_validate_json(text)
                except ValidationError as e:
                    first = e.errors()[0]
                    where = ".".join(str(p) for p in first["loc"])
                    problem = f"{where}: {first['msg']}" if where else first["msg"]
            log.warning("structured answer does not fit (attempt %d): %s", attempt + 1, problem)
        raise LLMError(f"Локальная модель ответила не по схеме {model.__name__}: {problem}")

    def _complete(self, body: dict[str, Any]) -> dict[str, Any]:
        url = self._url()
        try:
            response = self._http.post(url, json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"Локальная модель не отвечает: {e}") from None
        if response.status_code != 200:
            raise _status_error(response.status_code, response.content)
        try:
            choice: dict[str, Any] = response.json()["choices"][0]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError(
                f"Локальная модель прислала неожиданный ответ: {response.text[:200]}"
            ) from None
        return choice


def _status_error(status: int, raw: bytes) -> LLMError:
    text = raw.decode("utf-8", errors="replace")
    with contextlib.suppress(ValueError):
        text = _error_text(json.loads(text))
    return LLMError(f"Локальная модель вернула ошибку {status}: {text[:400]}")


def _usage(raw: dict[str, Any]) -> Usage:
    return Usage(
        input_tokens=int(raw.get("prompt_tokens") or 0),
        output_tokens=int(raw.get("completion_tokens") or 0),
    )
