import json
import os
import uuid

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from config import MAX_ITERATIONS, DB_TYPE, CURRENT_DATE, ROW_LIMIT, MAX_RETRIES, LLM_PROVIDER
from adapters.base import BaseAdapter
from providers.base import BaseProvider
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

    Maintains a normalized message history that any provider can consume.
    The provider handles all LLM-specific formatting internally.
    The adapter handles all database-specific logic internally.
    """

    def __init__(self, conn, adapter: BaseAdapter, provider: BaseProvider):
        self.conn     = conn
        self.adapter  = adapter
        self.provider = provider
        self.system   = load_system_prompt()
        self.messages: list[dict] = []
        self.session_id = str(uuid.uuid4())[:8]

        log.info("session.start", extra={
            "session_id": self.session_id,
            "provider":   LLM_PROVIDER,
            "model":      provider.model_name,
            "db_type":    DB_TYPE
        })

    def _ensure_connection(self) -> None:
        if not self.adapter.is_alive(self.conn):
            console.print("[yellow]Connection lost — reconnecting...[/yellow]")
            log.warning("db.reconnect", extra={"session_id": self.session_id, "db_type": DB_TYPE})
            self.conn = self.adapter.connect()
            console.print("[green]Reconnected.[/green]")
            log.info("db.reconnected", extra={"session_id": self.session_id})

    def chat(self, user_message: str) -> str:
        turn_id = str(uuid.uuid4())[:8]

        log.info("turn.start", extra={
            "session_id": self.session_id,
            "turn_id":    turn_id,
            "question":   user_message
        })

        self.messages.append({"role": "user", "content": user_message})

        for iteration in range(1, MAX_ITERATIONS + 1):
            console.print(f"\n[dim]── Iteration {iteration}/{MAX_ITERATIONS} [{self.provider.model_name}] ──[/dim]")

            log.debug("agent.iteration", extra={
                "session_id": self.session_id,
                "turn_id":    turn_id,
                "iteration":  iteration
            })

            response = self.provider.complete(self.system, self.messages, TOOL_DEFINITIONS)

            log.debug("llm.response", extra={
                "session_id":       self.session_id,
                "turn_id":          turn_id,
                "iteration":        iteration,
                "stop_reason":      response.stop_reason,
                "tool_calls_count": len(response.tool_calls)
            })

            # ── Agent is done ──────────────────────────────────────────────
            if response.stop_reason == "end_turn":
                self.messages.append({
                    "role": "assistant", "text": response.text, "tool_calls": []
                })
                log.info("turn.complete", extra={
                    "session_id": self.session_id,
                    "turn_id":    turn_id,
                    "iterations": iteration
                })
                return response.text or "Agent completed without a text response."

            # ── Agent wants to use tools ───────────────────────────────────
            if response.stop_reason == "tool_use":
                self.messages.append({
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
                        "turn_id":    turn_id,
                        "iteration":  iteration,
                        "tool":       tc.name,
                        "input":      tc.input
                    })

                    self._ensure_connection()
                    result = dispatch_tool(tc.name, tc.input, self.conn, self.adapter)
                    _display_tool_result(tc.name, result)

                    # Log result summary (not full data to keep logs readable)
                    if "error" in result:
                        log.warning("tool.error", extra={
                            "session_id": self.session_id,
                            "turn_id":    turn_id,
                            "tool":       tc.name,
                            "error":      result["error"]
                        })
                    elif tc.name == "execute_sql" and result.get("status") == "success":
                        log.info("sql.executed", extra={
                            "session_id":        self.session_id,
                            "turn_id":           turn_id,
                            "row_count":         result.get("row_count"),
                            "execution_time_ms": result.get("execution_time_ms")
                        })
                    elif result.get("status") == "rejected":
                        log.info("sql.rejected", extra={
                            "session_id": self.session_id,
                            "turn_id":    turn_id,
                            "message":    result.get("message")
                        })
                    else:
                        log.debug("tool.result", extra={
                            "session_id": self.session_id,
                            "turn_id":    turn_id,
                            "tool":       tc.name,
                            "status":     "ok"
                        })

                    self.messages.append({
                        "role":         "tool_result",
                        "tool_call_id": tc.id,
                        "tool_name":    tc.name,
                        "content":      json.dumps(result, default=str)
                    })

                continue

            log.error("agent.unexpected_stop", extra={
                "session_id":  self.session_id,
                "turn_id":     turn_id,
                "stop_reason": response.stop_reason
            })
            return f"Agent stopped unexpectedly (reason: {response.stop_reason})."

        log.warning("agent.max_iterations", extra={
            "session_id": self.session_id,
            "turn_id":    turn_id,
            "iterations": MAX_ITERATIONS
        })
        return f"Reached {MAX_ITERATIONS} iterations without completing. Try a more specific question."

    def reset(self) -> None:
        log.info("session.reset", extra={"session_id": self.session_id})
        self.messages = []
        console.print("[dim]Conversation history cleared.[/dim]")

    def __del__(self):
        try:
            log.info("session.end", extra={"session_id": self.session_id})
        except Exception:
            pass
