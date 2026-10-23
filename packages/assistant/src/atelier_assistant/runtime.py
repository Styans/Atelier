"""The assistant inside the worker: settings, the model process, GPU hand-over (ADR-034/035).

The worker owns one runtime. The model server starts with the first chat turn, stays warm for
the next ones, and is stopped before every GPU job (an 8 GB card cannot hold it next to Z-Image
or Hunyuan) and after ``idle_unload_s`` without turns.

``atelier.toml``::

    [assistant]
    idle_unload_s = 600

    [assistant.llama_server]
    exe = 'C:\\...\\llama-server.exe'
    model = 'C:\\...\\Qwen3VL-8B-Instruct-Q4_K_M.gguf'
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from atelier_assistant.agent import Agent
from atelier_assistant.llama_server import LlamaServer, LlamaServerClient, LlamaServerSettings
from atelier_assistant.llm import LLMClient, LLMError
from atelier_jobs import ChatStore
from atelier_show_engine.config import AtelierConfig
from atelier_show_engine.service import ShowService


class AssistantSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["llama_server"] = "llama_server"
    idle_unload_s: float = Field(600, ge=0, description="0 = keep the model loaded")
    llama_server: LlamaServerSettings | None = None


def assistant_settings(config: AtelierConfig) -> AssistantSettings:
    """``[assistant]`` with its paths resolved against atelier.toml's folder."""
    settings = AssistantSettings.model_validate(config.assistant)
    if settings.llama_server is not None:
        llama = settings.llama_server
        settings.llama_server = llama.model_copy(
            update={"exe": config.resolve(llama.exe), "model": config.resolve(llama.model)}
        )
    return settings


class AssistantRuntime:
    def __init__(
        self, config: AtelierConfig, client_factory: Callable[[], LLMClient] | None = None
    ) -> None:
        self.config = config
        self._factory = client_factory
        self._server: LlamaServer | None = None
        self._client: LLMClient | None = None
        self._last_used = 0.0
        self.problem: str | None = None  # why the assistant cannot work, in Russian
        try:
            self.settings = assistant_settings(config)
        except ValidationError as exc:
            self.settings = AssistantSettings()
            self.problem = f"неверная секция [assistant] в atelier.toml: {exc.errors()[0]['msg']}"
        if self.problem is None and client_factory is None and self.settings.llama_server is None:
            self.problem = (
                "ассистент не настроен: добавьте в atelier.toml секцию [assistant.llama_server]"
                " с путями exe и model (см. atelier.example.toml)"
            )

    def running(self) -> bool:
        return self._server is not None and self._server.running()

    def client(self) -> LLMClient:
        if self.problem is not None:
            raise LLMError(self.problem)
        if self._client is None:
            if self._factory is not None:
                self._client = self._factory()
            else:
                assert self.settings.llama_server is not None
                log = self.config.cache_dir / "logs" / "llama-server.log"
                self._server = LlamaServer(self.settings.llama_server, log)
                missing = self._server.missing()
                if missing is not None:
                    raise LLMError(missing)
                self._client = LlamaServerClient(self._server)
        self._last_used = time.monotonic()
        return self._client

    def agent(self, service: ShowService, chat: ChatStore) -> Agent:
        return Agent(self.client(), service, chat)

    def release(self) -> None:
        """Free the video memory (before a GPU job); the next turn starts the model again."""
        if self.running() and self._server is not None:
            self._server.stop()

    def idle(self) -> None:
        limit = self.settings.idle_unload_s
        if limit and self.running() and time.monotonic() - self._last_used > limit:
            self.release()

    def health(self) -> dict[str, Any]:
        files = None
        if self.problem is None and self.settings.llama_server is not None:
            files = LlamaServer(self.settings.llama_server, self.config.cache_dir).missing()
        return {
            "configured": self.problem is None,
            "problem": self.problem or files,
            "running": self.running(),
            "provider": self.settings.provider,
        }

    def close(self) -> None:
        self.release()
        if isinstance(self._client, LlamaServerClient):
            self._client.close()
