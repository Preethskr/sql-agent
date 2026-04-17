import json

from groq import Groq

from config import GROQ_API_KEY, GROQ_MODEL
from providers.base import BaseProvider, ProviderResponse, ToolCall


class GroqProvider(BaseProvider):

    def __init__(self):
        self.client = Groq(api_key=GROQ_API_KEY)
        self._model = GROQ_MODEL

    @property
    def model_name(self) -> str:
        return self._model

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert Anthropic tool format → OpenAI/Groq format."""
        return [
            {
                "type": "function",
                "function": {
                    "name":        t["name"],
                    "description": t["description"],
                    "parameters":  t["input_schema"]   # same JSON Schema structure
                }
            }
            for t in tools
        ]

    def _to_openai_messages(self, system: str, messages: list[dict]) -> list[dict]:
        """Convert normalized messages → OpenAI/Groq format."""
        result = [{"role": "system", "content": system}]

        for msg in messages:
            if msg["role"] == "user":
                result.append({"role": "user", "content": msg["content"]})

            elif msg["role"] == "assistant":
                m: dict = {"role": "assistant", "content": msg.get("text")}
                if msg.get("tool_calls"):
                    m["tool_calls"] = [
                        {
                            "id":   tc["id"],
                            "type": "function",
                            "function": {
                                "name":      tc["name"],
                                "arguments": json.dumps(tc["input"])
                            }
                        }
                        for tc in msg["tool_calls"]
                    ]
                result.append(m)

            elif msg["role"] == "tool_result":
                result.append({
                    "role":         "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "content":      msg["content"]
                })

        return result

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> ProviderResponse:
        response = self.client.chat.completions.create(
            model      = self._model,
            max_tokens = 4096,
            messages   = self._to_openai_messages(system, messages),
            tools      = self._convert_tools(tools)
        )

        choice        = response.choices[0]
        finish_reason = choice.finish_reason

        if finish_reason == "stop":
            return ProviderResponse(stop_reason="end_turn", text=choice.message.content)

        if finish_reason == "tool_calls":
            tool_calls = [
                ToolCall(
                    id    = tc.id,
                    name  = tc.function.name,
                    input = json.loads(tc.function.arguments)
                )
                for tc in choice.message.tool_calls
            ]
            return ProviderResponse(stop_reason="tool_use", tool_calls=tool_calls)

        return ProviderResponse(stop_reason=finish_reason)

    def complete_text(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model      = self._model,
            max_tokens = 1024,
            messages   = [{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
