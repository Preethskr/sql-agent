import anthropic

from config import ANTHROPIC_API_KEY, MODEL
from providers.base import BaseProvider, ProviderResponse, ToolCall


class AnthropicProvider(BaseProvider):

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self._model = MODEL

    @property
    def model_name(self) -> str:
        return self._model

    def _to_anthropic_messages(self, messages: list[dict]) -> list[dict]:
        """Convert normalized messages to Anthropic API format."""
        result = []
        i = 0

        while i < len(messages):
            msg = messages[i]

            if msg["role"] == "user":
                result.append({"role": "user", "content": msg["content"]})
                i += 1

            elif msg["role"] == "assistant":
                content = []
                if msg.get("text"):
                    content.append({"type": "text", "text": msg["text"]})
                for tc in msg.get("tool_calls", []):
                    content.append({
                        "type":  "tool_use",
                        "id":    tc["id"],
                        "name":  tc["name"],
                        "input": tc["input"]
                    })
                result.append({"role": "assistant", "content": content})
                i += 1

            elif msg["role"] == "tool_result":
                # Collect consecutive tool results into one user message
                tool_results = []
                while i < len(messages) and messages[i]["role"] == "tool_result":
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": messages[i]["tool_call_id"],
                        "content":     messages[i]["content"]
                    })
                    i += 1
                result.append({"role": "user", "content": tool_results})

            else:
                i += 1

        return result

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> ProviderResponse:
        response = self.client.messages.create(
            model      = self._model,
            max_tokens = 4096,
            system     = system,
            tools      = tools,   # Anthropic format used as-is
            messages   = self._to_anthropic_messages(messages)
        )

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if hasattr(b, "text")), None)
            return ProviderResponse(stop_reason="end_turn", text=text)

        if response.stop_reason == "tool_use":
            tool_calls = [
                ToolCall(id=b.id, name=b.name, input=b.input)
                for b in response.content if b.type == "tool_use"
            ]
            return ProviderResponse(stop_reason="tool_use", tool_calls=tool_calls)

        return ProviderResponse(stop_reason=response.stop_reason)

    def complete_text(self, prompt: str) -> str:
        response = self.client.messages.create(
            model      = self._model,
            max_tokens = 1024,
            messages   = [{"role": "user", "content": prompt}]
        )
        return response.content[0].text
