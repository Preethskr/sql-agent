import json
import os

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from config import MAX_ITERATIONS, DB_TYPE, CURRENT_DATE, ROW_LIMIT, MAX_RETRIES, LLM_PROVIDER
from adapters.base import BaseAdapter
from providers.base import BaseProvider
from memory import ConversationMemory
from tools import TOOL_DEFINITIONS, dispatch_tool
from logger import log

console = Console()


# ---------------------------------------------------------------------------
# System Prompt Loader
# ---------------------------------------------------------------------------

def load_system_prompt() -> str:
    prompt_path = os.path.join(os.path.dirname(__file__), "system_prompt.md")
    with open(prompt_path, "r") as f:
        prompt = f.read()
    for placeholder, value in {
        "DB_TYPE":      DB_TYPE,
        "CURRENT_DATE": CURRENT_DATE,
        "ROW_LIMIT":    str(ROW_LIMIT),
        "MAX_RETRIES":  str(MAX_RETRIES),
    }.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


# ---------------------------------------------------------------------------
# Display Helpers
# ---------------------------------------------------------------------------

def _display_tool_call(name: str, inputs: dict) -> None:
    console.print(Panel(
        f"[bold cyan]Tool:[/bold cyan]  {name}\n"
        f"[bold cyan]Input:[/bold cyan]\n{json.dumps(inputs, indent=2)}",
        title="[cyan]Agent Tool Call[/cyan]",
        border_style="cyan"
    ))


def _display_tool_result(name: str, result: dict) -> None:
    if "error" in result:
        console.print(Panel(
            f"[red]{result['error']}[/red]",
            title="[red]Tool Error[/red]",
            border_style="red"
        ))
        return
    if name == "execute_sql" and result.get("status") == "success" and result.get("columns"):
        _display_sql_table(result)
        return
    console.print(Panel(
        json.dumps(result, indent=2, default=str),
        title="[green]Tool Result[/green]",
        border_style="green"
    ))


def _display_sql_table(result: dict) -> None:
    table = Table(box=box.ROUNDED, header_style="bold magenta", border_style="green")
    for col in result["columns"]:
        table.add_column(str(col), overflow="fold")
    for row in result["rows"]:
        table.add_row(*[str(v) if v is not None else "NULL" for v in row])
    console.print(Panel(
        table,
        title=f"[green]Results — {result['row_count']} row(s) — {result.get('execution_time_ms', '—')}ms[/green]",
        border_style="green"
    ))


# ---------------------------------------------------------------------------
# SQL Agent
# ---------------------------------------------------------------------------

class SQLAgent:
    """
    Stateful text-to-SQL agent.

    State is managed by ConversationMemory which handles:
    - Sliding window (last N messages kept in full)
    - Summarization of older turns (injected into system prompt)
    - Persistence to JSON (dev) or PostgreSQL (production)
    """

    def __init__(self, conn, adapter: BaseAdapter, provider: BaseProvider):
        self.conn     = conn
        self.adapter  = adapter
        self.provider = provider
        self.system   = load_system_prompt()
        self.memory   = ConversationMemory(provider)

        log.info("session.start", extra={
            "session_id": self.memory.session_id,
            "provider":   LLM_PROVIDER,
            "model":      provider.model_name,
            "db_type":    DB_TYPE
        })

    @property
    def session_id(self) -> str:
        return self.memory.session_id

    def resume(self, session_id: str) -> bool:
        """Load a previous session. Returns True if found."""
        found = self.memory.load(session_id)
        if found:
            log.info("session.resumed", extra={
                "session_id": session_id,
                "turn_count": self.memory.turn_count
            })
        return found

    def list_sessions(self) -> list[dict]:
        return self.memory.list_sessions()

    def _ensure_connection(self) -> None:
        if not self.adapter.is_alive(self.conn):
            console.print("[yellow]Connection lost — reconnecting...[/yellow]")
            log.warning("db.reconnect", extra={"session_id": self.session_id})
            self.conn = self.adapter.connect()
            console.print("[green]Reconnected.[/green]")

    def chat(self, user_message: str) -> str:
        self.memory.add({"role": "user", "content": user_message})

        log.info("turn.start", extra={
            "session_id": self.session_id,
            "turn":       self.memory.turn_count + 1,
            "question":   user_message
        })

        for iteration in range(1, MAX_ITERATIONS + 1):
            console.print(
                f"\n[dim]── Iteration {iteration}/{MAX_ITERATIONS} "
                f"[{self.provider.model_name}] "
                f"| {len(self.memory.messages)} msgs in window ──[/dim]"
            )

            # Summary is injected into the system prompt — not the message list
            effective_system = self.memory.get_system(self.system)

            response = self.provider.complete(
                effective_system,
                self.memory.messages,
                TOOL_DEFINITIONS
            )

            log.debug("llm.response", extra={
                "session_id":  self.session_id,
                "iteration":   iteration,
                "stop_reason": response.stop_reason,
                "tool_calls":  len(response.tool_calls)
            })

            # ── Agent is done ──────────────────────────────────────────
            if response.stop_reason == "end_turn":
                self.memory.add({
                    "role": "assistant", "text": response.text, "tool_calls": []
                })
                self.memory.increment_turn()
                self.memory.maybe_compress()    # compress if window exceeded
                self.memory.save()              # persist after every turn

                log.info("turn.complete", extra={
                    "session_id": self.session_id,
                    "turn":       self.memory.turn_count,
                    "iterations": iteration,
                    "window_size": len(self.memory.messages),
                    "has_summary": bool(self.memory.summary)
                })
                return response.text or "Agent completed without a text response."

            # ── Agent wants to use tools ───────────────────────────────
            if response.stop_reason == "tool_use":
                self.memory.add({
                    "role":       "assistant",
                    "text":       None,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "input": tc.input}
                        for tc in response.tool_calls
                    ]
                })

                for tc in response.tool_calls:
                    _display_tool_call(tc.name, tc.input)
                    log.info("tool.call", extra={
                        "session_id": self.session_id,
                        "tool":       tc.name,
                        "input":      tc.input
                    })

                    self._ensure_connection()
                    result = dispatch_tool(tc.name, tc.input, self.conn, self.adapter)
                    _display_tool_result(tc.name, result)

                    if "error" in result:
                        log.warning("tool.error", extra={
                            "session_id": self.session_id,
                            "tool": tc.name, "error": result["error"]
                        })
                    elif tc.name == "execute_sql" and result.get("status") == "success":
                        log.info("sql.executed", extra={
                            "session_id":        self.session_id,
                            "row_count":         result.get("row_count"),
                            "execution_time_ms": result.get("execution_time_ms")
                        })

                    self.memory.add({
                        "role":         "tool_result",
                        "tool_call_id": tc.id,
                        "tool_name":    tc.name,
                        "content":      json.dumps(result, default=str)
                    })

                continue

            return f"Agent stopped unexpectedly (reason: {response.stop_reason})."

        log.warning("agent.max_iterations", extra={"session_id": self.session_id})
        return f"Reached {MAX_ITERATIONS} iterations without completing. Try a more specific question."

    def reset(self) -> None:
        log.info("session.reset", extra={"session_id": self.session_id})
        self.memory.reset()
        console.print("[dim]Conversation history cleared.[/dim]")
