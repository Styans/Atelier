"""The chat assistant: the LLM interface and the local llama-server provider (ADR-034)."""

from importlib.metadata import version

from atelier_assistant.llama_server import LlamaServer, LlamaServerClient, LlamaServerSettings
from atelier_assistant.llm import (
    AssistantMessage,
    Block,
    LLMClient,
    LLMError,
    Message,
    ScriptedClient,
    ScriptedRequest,
    StopReason,
    ToolSpec,
    Usage,
    text_block,
    tool_result_block,
    tool_use_block,
)

__version__ = version("atelier-assistant")

__all__ = [
    "AssistantMessage",
    "Block",
    "LLMClient",
    "LLMError",
    "LlamaServer",
    "LlamaServerClient",
    "LlamaServerSettings",
    "Message",
    "ScriptedClient",
    "ScriptedRequest",
    "StopReason",
    "ToolSpec",
    "Usage",
    "text_block",
    "tool_result_block",
    "tool_use_block",
]
