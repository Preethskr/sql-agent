from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich import box

from config import DB_TYPE, LLM_PROVIDER, STORE_TYPE
from adapters import get_adapter
from providers import get_provider
from agent import SQLAgent
from logger import log

console = Console()

WELCOME = """
# SQL Expert Agent
Ask questions about your logistics data in plain English.

**Commands:**
- Type your question and press Enter
- `sessions`          — list previous sessions
- `resume <id>`       — continue a previous session
- `reset`             — clear history and start fresh
- `exit`              — quit
"""


def _show_sessions(agent: SQLAgent) -> None:
    sessions = agent.list_sessions()
    if not sessions:
        console.print("[dim]No saved sessions found.[/dim]")
        return

    table = Table(box=box.ROUNDED, header_style="bold magenta", border_style="blue")
    table.add_column("Session ID", style="cyan")
    table.add_column("Last Active")
    table.add_column("Turns", justify="right")
    table.add_column("First Question")

    for s in sessions:
        table.add_row(
            s["session_id"],
            s["updated_at"][:19].replace("T", " "),
            str(s["turn_count"]),
            s["preview"]
        )

    console.print(Panel(table, title="[blue]Saved Sessions[/blue]", border_style="blue"))


def main():
    console.print(Panel(Markdown(WELCOME), border_style="blue"))

    # --- Initialise DB adapter ---
    try:
        adapter = get_adapter()
        console.print(f"[dim]Database:[/dim]  [bold]{DB_TYPE}[/bold]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return

    # --- Connect to the database ---
    try:
        conn = adapter.connect()
        console.print(f"[green]Connected successfully.[/green]")
        log.info("db.connected", extra={"db_type": DB_TYPE})
    except Exception as e:
        log.error("db.connection_failed", extra={"db_type": DB_TYPE, "error": str(e)})
        console.print(f"[red]Database connection failed:[/red] {e}")
        console.print("[dim]Check your .env file and ensure the database is reachable.[/dim]")
        return

    # --- Initialise LLM provider ---
    try:
        provider = get_provider()
        console.print(
            f"[dim]LLM:[/dim]       [bold]{LLM_PROVIDER} / {provider.model_name}[/bold]"
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return

    # --- Initialise agent ---
    agent = SQLAgent(conn, adapter, provider)
    console.print(
        f"[dim]Session:[/dim]   [bold cyan]{agent.session_id}[/bold cyan]  "
        f"[dim](store: {STORE_TYPE})[/dim]\n"
    )

    # --- Conversation loop ---
    while True:
        try:
            user_input = Prompt.ask("\n[bold blue]You[/bold blue]").strip()

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "bye"):
                console.print("[dim]Goodbye.[/dim]")
                break

            if user_input.lower() == "reset":
                agent.reset()
                console.print(f"[dim]New session ID:[/dim] [cyan]{agent.session_id}[/cyan]")
                continue

            if user_input.lower() == "sessions":
                _show_sessions(agent)
                continue

            if user_input.lower().startswith("resume "):
                session_id = user_input.split(maxsplit=1)[1].strip()
                if agent.resume(session_id):
                    console.print(
                        f"[green]Resumed session[/green] [cyan]{session_id}[/cyan] "
                        f"([bold]{agent.memory.turn_count}[/bold] previous turns)"
                    )
                    if agent.memory.summary:
                        console.print(Panel(
                            agent.memory.summary,
                            title="[dim]Conversation Summary[/dim]",
                            border_style="dim"
                        ))
                else:
                    console.print(f"[red]Session '{session_id}' not found.[/red]")
                continue

            console.print()
            response = agent.chat(user_input)

            console.print(Panel(
                Markdown(response),
                title=f"[bold green]Agent[/bold green] [dim]· turn {agent.memory.turn_count}[/dim]",
                border_style="green"
            ))

        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted. Goodbye.[/dim]")
            break
        except Exception as e:
            log.error("app.error", extra={"error": str(e)})
            console.print(f"\n[red]Unexpected error:[/red] {e}")

    log.info("app.shutdown")
    conn.close()


if __name__ == "__main__":
    main()
