import re
import json

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from config import ROW_LIMIT
from adapters.base import BaseAdapter
from logger import log

console = Console()

# ---------------------------------------------------------------------------
# Tool Definitions — passed to the Anthropic API.
# These are DB-agnostic: the adapter handles all dialect differences.
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "list_schemas",
        "description": (
            "Lists all available schemas in the database. "
            "Always call this first to orient yourself."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "list_tables",
        "description": "Lists all tables within a given schema.",
        "input_schema": {
            "type": "object",
            "properties": {
                "schema_name": {"type": "string", "description": "The schema to list tables from."}
            },
            "required": ["schema_name"]
        }
    },
    {
        "name": "get_columns_with_types",
        "description": (
            "Returns column names, data types, nullability, primary keys, and foreign key "
            "relationships for a table. Always call this before writing SQL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "schema_name": {"type": "string", "description": "The schema containing the table."},
                "table_name":  {"type": "string", "description": "The table to inspect."}
            },
            "required": ["schema_name", "table_name"]
        }
    },
    {
        "name": "get_column_unique_values",
        "description": (
            "Returns distinct values for a categorical column (status, type, region, etc.). "
            "Call this before filtering on any column where exact values are unknown. "
            f"Returns a warning if the column has more than {ROW_LIMIT} unique values."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "schema_name":  {"type": "string"},
                "table_name":   {"type": "string"},
                "column_name":  {"type": "string", "description": "The column to get unique values for."}
            },
            "required": ["schema_name", "table_name", "column_name"]
        }
    },
    {
        "name": "execute_sql",
        "description": (
            "Executes a SQL query. SELECT statements run automatically. "
            "INSERT, UPDATE, and DELETE require human approval before execution. "
            f"Results are capped at {ROW_LIMIT} rows."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The SQL query to execute."}
            },
            "required": ["sql"]
        }
    }
]


# ---------------------------------------------------------------------------
# Write-operation guard — runs at the Python level (not the agent level)
# ---------------------------------------------------------------------------

def _is_write_operation(sql: str) -> bool:
    return bool(re.match(
        r'^\s*(INSERT|UPDATE|DELETE|DROP|TRUNCATE|CREATE|ALTER)\b',
        sql.strip(), re.IGNORECASE
    ))


def _request_human_approval(sql: str) -> dict:
    """Display write SQL to the user and ask for explicit CLI approval."""
    console.print(Panel(
        f"[bold yellow]Write operation detected.[/bold yellow]\n\n"
        f"[bold]SQL to execute:[/bold]\n"
        f"{Syntax(sql, 'sql', theme='monokai')}",
        title="[yellow]Human Approval Required[/yellow]",
        border_style="yellow"
    ))
    log.info("approval.requested", extra={"sql": sql})
    answer = input("Approve this operation? (y/n): ").strip().lower()
    if answer == "y":
        log.info("approval.granted", extra={"sql": sql})
        return {"approved": True, "feedback": "User approved."}
    reason = input("Reason (press Enter to skip): ").strip()
    log.info("approval.denied", extra={"sql": sql, "reason": reason or "User rejected."})
    return {"approved": False, "feedback": reason or "User rejected."}


# ---------------------------------------------------------------------------
# Tool Dispatcher — routes agent tool calls to the correct adapter method.
# All DB-specific logic lives in the adapter, not here.
# ---------------------------------------------------------------------------

def dispatch_tool(tool_name: str, tool_input: dict, conn, adapter: BaseAdapter) -> dict:
    try:
        if tool_name == "list_schemas":
            return adapter.list_schemas(conn)

        elif tool_name == "list_tables":
            return adapter.list_tables(conn, tool_input["schema_name"])

        elif tool_name == "get_columns_with_types":
            return adapter.get_columns_with_types(
                conn, tool_input["schema_name"], tool_input["table_name"]
            )

        elif tool_name == "get_column_unique_values":
            return adapter.get_column_unique_values(
                conn, tool_input["schema_name"],
                tool_input["table_name"], tool_input["column_name"]
            )

        elif tool_name == "execute_sql":
            sql = tool_input["sql"]

            # Intercept write operations — human must approve before execution
            if _is_write_operation(sql):
                approval = _request_human_approval(sql)
                if not approval["approved"]:
                    return {
                        "status":  "rejected",
                        "message": f"Operation cancelled. {approval['feedback']}"
                    }

            # Inject row limit using DB-correct syntax
            safe_sql = adapter.inject_limit(sql, ROW_LIMIT)
            return adapter.run_query(conn, safe_sql)

        else:
            return {"error": f"Unknown tool '{tool_name}'."}

    except KeyError as e:
        return {"error": f"Missing required input for '{tool_name}': {e}"}
    except Exception as e:
        return {"error": f"Tool '{tool_name}' failed: {str(e)}"}
