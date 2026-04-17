from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from config import DB_TYPE, LLM_PROVIDER
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
- `reset` — clear conversation history and start fresh
- `exit`  — quit
"""


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
        console.print(f"[dim]LLM:     [/dim]  [bold]{LLM_PROVIDER} / {provider.model_name}[/bold]\n")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return

    # --- Initialise agent ---
    agent = SQLAgent(conn, adapter, provider)

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
                continue

            console.print()
            response = agent.chat(user_input)

            console.print(Panel(
                Markdown(response),
                title="[bold green]Agent[/bold green]",
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
