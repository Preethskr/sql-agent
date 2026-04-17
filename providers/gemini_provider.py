import json
import re
import time

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL
from providers.base import BaseProvider, ProviderResponse, ToolCall


class GeminiProvider(BaseProvider):

    def __init__(self):
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self._model = GEMINI_MODEL

    @property
    def model_name(self) -> str:
        return self._model

    def _convert_tools(self, tools: list[dict]) -> list[types.Tool]:
        """Convert Anthropic tool format → Gemini FunctionDeclaration format."""
        declarations = []
        for t in tools:
            declarations.append(types.FunctionDeclaration(
                name        = t["name"],
                description = t["description"],
                parameters  = t["input_schema"]   # JSON Schema is compatible
            ))
        return [types.Tool(function_declarations=declarations)]

    def _to_gemini_contents(self, messages: list[dict]) -> list[types.Content]:
        """Convert normalized messages → Gemini Content format."""
        contents = []
        i = 0

        while i < len(messages):
            msg = messages[i]

            if msg["role"] == "user":
                contents.append(types.Content(
                    role  = "user",
                    parts = [types.Part.from_text(text=msg["content"])]
                ))
                i += 1

            elif msg["role"] == "assistant":
                parts = []
                if msg.get("text"):
                    parts.append(types.Part.from_text(text=msg["text"]))
                for tc in msg.get("tool_calls", []):
                    parts.append(types.Part.from_function_call(
                        name = tc["name"],
                        args = tc["input"]
                    ))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
                i += 1

            elif msg["role"] == "tool_result":
                # Collect consecutive tool results into one user Content
                parts = []
                while i < len(messages) and messages[i]["role"] == "tool_result":
                    m = messages[i]
                    try:
                        response_data = json.loads(m["content"])
                    except (json.JSONDecodeError, TypeError):
                        response_data = {"result": m["content"]}

                    parts.append(types.Part.from_function_response(
                        name     = m["tool_name"],
                        response = response_data
                    ))
                    i += 1
                contents.append(types.Content(role="user", parts=parts))

            else:
                i += 1

        return contents

    def _call_api(self, system: str, messages: list[dict], tools: list[dict]):
        """Call Gemini API with automatic retry on 429 rate limit."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return self.client.models.generate_content(
                    model    = self._model,
                    contents = self._to_gemini_contents(messages),
                    config   = types.GenerateContentConfig(
                        system_instruction = system,
                        tools              = self._convert_tools(tools),
                        max_output_tokens  = 4096
                    )
                )
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    # Parse suggested retry delay from error message
                    match = re.search(r'retry in (\d+)', error_str)
                    wait  = int(match.group(1)) + 2 if match else 30

                    if attempt < max_retries - 1:
                        from rich.console import Console
                        Console().print(f"[yellow]Gemini rate limit hit — waiting {wait}s before retry ({attempt + 1}/{max_retries - 1})...[/yellow]")
                        time.sleep(wait)
                        continue

                    raise RuntimeError(
                        f"Gemini quota exhausted. "
                        f"Either wait ~{wait}s and retry, or switch to another provider:\n"
                        f"  Set LLM_PROVIDER=groq or LLM_PROVIDER=anthropic in your .env"
                    ) from e
                raise

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> ProviderResponse:
        response  = self._call_api(system, messages, tools)
        candidate = response.candidates[0]
        parts     = candidate.content.parts

        tool_calls = []
        for part in parts:
            if part.function_call:
                tool_calls.append(ToolCall(
                    id    = part.function_call.name,
                    name  = part.function_call.name,
                    input = dict(part.function_call.args)
                ))

        if tool_calls:
            return ProviderResponse(stop_reason="tool_use", tool_calls=tool_calls)

        text = next((p.text for p in parts if hasattr(p, "text") and p.text), None)
        return ProviderResponse(stop_reason="end_turn", text=text)

    def complete_text(self, prompt: str) -> str:
        response = self.client.models.generate_content(
            model    = self._model,
            contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            config   = types.GenerateContentConfig(max_output_tokens=1024)
        )
        return response.candidates[0].content.parts[0].text
