from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolCall:
    id:    str
    name:  str
    input: dict


@dataclass
class ProviderResponse:
    stop_reason: str                          # "end_turn" | "tool_use"
    text:        Optional[str]       = None
    tool_calls:  list[ToolCall]      = field(default_factory=list)


# ---------------------------------------------------------------------------
# Normalized message format used by agent.py
# ---------------------------------------------------------------------------
#
# User turn:
#   {"role": "user", "content": "show delayed orders"}
#
# Assistant with tool calls:
#   {"role": "assistant", "text": None,
#    "tool_calls": [{"id": "1", "name": "list_schemas", "input": {}}]}
#
# Assistant final response:
#   {"role": "assistant", "text": "Here are the results...", "tool_calls": []}
#
# Tool result:
#   {"role": "tool_result", "tool_call_id": "1",
#    "tool_name": "list_schemas", "content": "{...}"}
#
# Each provider converts this format to its own API format on every call.
# ---------------------------------------------------------------------------


class BaseProvider(ABC):

    @abstractmethod
    def complete(
        self,
        system:   str,
        messages: list[dict],
        tools:    list[dict]
    ) -> ProviderResponse:
        """
        Send a completion request and return a normalized response.

        system:   system prompt string
        messages: normalized history (see format above)
        tools:    Anthropic-format tool definitions (converted internally per provider)
        """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model identifier string for display."""
