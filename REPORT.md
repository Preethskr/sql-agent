# Text-to-SQL Agent — Technical Design Report

---

## 1. Problem Statement

Enterprise databases contain valuable business intelligence, but access is gated behind
SQL knowledge. Most business analysts cannot write queries, and data teams become
bottlenecks answering repetitive questions.

This agent solves that directly: **a non-technical business analyst types a question in
plain English and receives the answer, the SQL used, and a plain-English interpretation
of the results** — without writing a single line of SQL.

The target domain is logistics — shipment tracking, order management, carrier performance,
and damage/return analysis — but the architecture is domain-agnostic.

---

## 2. What Makes This an Agent (Not Just a Chatbot)

A chatbot generates text. An agent **reasons, acts, observes, and decides** what to do
next — in a loop.

This system uses the **Anthropic tool-use API** (function calling) to implement a true
agentic loop:

```
User Question
     │
     ▼
┌─────────────────────────────────────────────┐
│               AGENTIC LOOP                  │
│                                             │
│  1. LLM decides what tool to call           │
│  2. Code executes the tool                  │
│  3. Result is returned to the LLM           │
│  4. LLM decides next action                 │
│  5. Repeat until stop_reason = "end_turn"   │
└─────────────────────────────────────────────┘
     │
     ▼
Answer + SQL + Results
```

The LLM never directly touches the database. It outputs structured tool calls. The
application code executes them and feeds results back. This separation is fundamental
to building safe, controllable AI systems.

---

## 3. Architecture Overview

```
sql_agent/
├── main.py                  CLI entry point — wires everything together
├── agent.py                 The agentic loop — provider-agnostic
├── tools.py                 Tool definitions (Anthropic schema) + dispatcher
├── config.py                All settings loaded from .env at startup
├── logger.py                Structured JSON logging (rotating file)
├── system_prompt.md         Agent reasoning strategy — versioned separately
│
├── adapters/                Database adapter layer
│   ├── base.py              Abstract interface
│   ├── postgres.py          PostgreSQL implementation
│   ├── oracle.py            Oracle implementation
│   ├── tibero.py            Tibero (extends Oracle — same dialect)
│   └── sqlserver.py         SQL Server implementation
│
└── providers/               LLM provider layer
    ├── base.py              Normalized message format + abstract interface
    ├── anthropic_provider.py  Claude (production)
    ├── groq_provider.py     Groq/Llama (free dev tier)
    └── gemini_provider.py   Google Gemini (free dev tier)
```

**Key principle:** Every layer communicates through an abstract interface.
`agent.py` never imports `psycopg2` or `anthropic` directly. It only knows about
`BaseAdapter` and `BaseProvider`. This is the **Dependency Inversion Principle** in practice.

---

## 4. Design Decision: Dynamic Schema Discovery vs. Static Schema in Prompt

### V1 approach (prior implementation)
The entire database schema was embedded in the system prompt upfront.

### V2 approach (this implementation)
The schema is discovered dynamically at runtime through tool calls.

| Dimension | V1 | V2 |
|---|---|---|
| Schema in context | Full schema upfront | Only relevant tables |
| Works with 50+ tables | No — hits token limits | Yes |
| Reasoning visible | No | Every tool call = reasoning step |
| Adapts to schema changes | No — prompt must be updated | Yes — tools query live metadata |
| Token cost | High (always) | Proportional to question complexity |

**Why this matters at enterprise scale:** A logistics database might have 80+ tables across
multiple schemas. V1 cannot work here — it would exceed the context window before the
user asks a single question. V2 handles this because the agent only fetches what it needs.

---

## 5. The Five Tools

Tools are the agent's only interface to the outside world. Each tool is defined as a
JSON schema (for the LLM) and implemented as a Python function (for execution).

### `list_schemas`
Entry point. The agent always calls this first to orient itself.
Returns all non-system schemas in the connected database.

### `list_tables(schema_name)`
Returns all base tables in a schema. The LLM reads the table names and infers which
ones are relevant to the question — this is the first reasoning step.

### `get_columns_with_types(schema_name, table_name)`
Returns column names, data types, primary keys, and **foreign key relationships**.
The FK data is critical — it tells the agent exactly how to JOIN tables without guessing.

### `get_column_unique_values(schema_name, table_name, column_name)`
Returns distinct values for categorical columns (status, type, region, etc.).
This prevents a common agent failure: generating `WHERE status = 'Delayed'` when the
actual stored value is `'IN_TRANSIT'`.

**Guardrail:** If the column has more than 50 unique values, a warning is returned
instead. The agent is instructed to use range filters for high-cardinality columns.

### `execute_sql(sql)`
Executes a query with the following guardrails enforced at the **code level**, not the LLM level:

- **Write interception:** `INSERT`, `UPDATE`, `DELETE`, `DROP`, `TRUNCATE` are detected
  via regex before execution. The user sees the exact SQL and must approve it in the CLI.
  This is a hard boundary — the LLM cannot bypass it.
- **Row limit injection:** If no `LIMIT` clause is present, one is injected automatically
  using the correct syntax for the active database dialect.
- **Query timeout:** Enforced at the DB driver level (10 seconds by default).
- **Errors as data:** SQL errors are returned as structured dicts, not raised as Python
  exceptions. This allows the agent to read the error message and self-correct.

---

## 6. Design Decision: Where to Put Guardrails

There is a deliberate choice in this system: **safety-critical guardrails live in the
application code, not in the LLM prompt.**

The system prompt instructs the agent on *how to reason*. The code enforces *hard limits*.

```
Prompt-level (soft):   "Always call list_schemas first"
                        "Never assume column names"
                        "Cap results at 1000 rows"

Code-level (hard):     Write SQL interception (regex check before execution)
                        Row limit injection (appended to every query)
                        Query timeout (set at DB driver level)
                        Cardinality check (queried before returning values)
```

This separation means the guardrails cannot be bypassed by prompt injection or
unexpected LLM behaviour. A malicious input cannot trick the agent into deleting data
because the code-level check runs regardless of what the LLM decided.

---

## 7. The Agentic Reasoning Strategy

The system prompt encodes the agent's step-by-step reasoning strategy explicitly.
This is not prompt engineering for correctness — it is encoding a human analyst's
actual thought process:

```
Step 1:  Call list_schemas        → "Where is the data?"
Step 2:  Call list_tables         → "Which schema is relevant?"
Step 3:  Identify relevant tables → "Which tables answer my question?" (LLM reasoning)
Step 4:  Call get_columns         → "What columns exist? How do I JOIN?"
Step 5:  Call get_unique_values   → "What are the actual filter values?" (only if filtering)
Step 6:  Write SQL                → "Generate the query using confirmed schema"
Step 7:  Safety check             → "Is this SELECT or a write operation?"
Step 8:  Execute                  → "Run the query"
Step 9:  Summarise                → "Explain the result in plain English"
```

Making the reasoning explicit reduces hallucination. The agent cannot skip Step 4 and
assume column names because the prompt explicitly forbids it and Step 6 depends on it.

---

## 8. The System Prompt as a First-Class Artifact
The system prompt lives in `system_prompt.md` — not as a string buried in code.

**Why this matters:**
- It can be reviewed and edited without touching Python code
- It is versioned independently in git
- It is loaded at runtime with variables injected dynamically

**Runtime variable injection:**

```python
replacements = {
    "DB_TYPE":      "Oracle",
    "CURRENT_DATE": "2026-04-17",
    "ROW_LIMIT":    "1000",
    "MAX_RETRIES":  "3"
}
```

This means the agent always knows today's date (critical for time-relative queries like
"show orders delayed this week") and writes SQL in the correct dialect for the connected
database.

---

## 9. Multi-Database Support via Adapter Pattern

The adapter pattern abstracts all database-specific logic:

```python
class BaseAdapter(ABC):
    def connect(self)                              → connection
    def is_alive(conn)                             → bool
    def inject_limit(sql, limit)                  → str   # dialect-specific
    def list_schemas(conn)                         → dict
    def list_tables(conn, schema)                  → dict
    def get_columns_with_types(conn, schema, table)→ dict
    def get_column_unique_values(...)              → dict
    def run_query(conn, sql)                       → dict
```

Each database implements this interface with its own dialect:

| Database | Driver | LIMIT syntax | Schema discovery |
|---|---|---|---|
| PostgreSQL | psycopg2 | `LIMIT n` | `information_schema` |
| Oracle | oracledb | `FETCH FIRST n ROWS ONLY` | `ALL_TABLES`, `ALL_TAB_COLUMNS` |
| Tibero | pyodbc | `FETCH FIRST n ROWS ONLY` | `ALL_TABLES` (inherits Oracle) |
| SQL Server | pyodbc | `SELECT TOP n` | `information_schema` |

**Key insight:** Tibero is Oracle-compatible at the SQL dialect level. Its adapter
extends `OracleAdapter` and overrides only the `connect()` method. This demonstrates
the **Liskov Substitution Principle** — a Tibero adapter can be used anywhere an
Oracle adapter is expected.

**Lazy imports** ensure that `pyodbc` is never loaded when using PostgreSQL or Oracle,
preventing import errors from missing system-level ODBC drivers.

---

## 10. Multi-LLM Provider Support via Provider Pattern

The same adapter pattern is applied to LLM providers. Each provider has a fundamentally
different API:

| Aspect | Anthropic | Groq | Gemini |
|---|---|---|---|
| SDK | `anthropic` | `groq` | `google-genai` |
| Message format | Content blocks | OpenAI-compatible | Content objects |
| Tool definition | `input_schema` | `parameters` (JSON Schema) | `FunctionDeclaration` |
| Tool result format | `tool_result` in user message | `role: tool` message | `from_function_response` |
| Stop reason | `end_turn` / `tool_use` | `stop` / `tool_calls` | Inferred from parts |

The `agent.py` maintains a **normalized message history** that is provider-agnostic:

```python
# User message
{"role": "user", "content": "show delayed orders"}

# Assistant tool call
{"role": "assistant", "tool_calls": [{"id": "1", "name": "list_schemas", "input": {}}]}

# Tool result
{"role": "tool_result", "tool_call_id": "1", "tool_name": "list_schemas", "content": "..."}

# Assistant final response
{"role": "assistant", "text": "Here are the delayed orders...", "tool_calls": []}
```

Each provider converts this normalized format to its own API format on every call.
The `agent.py` loop never changes regardless of which LLM is used.

**Switching providers requires one line in `.env`:**
```
LLM_PROVIDER=anthropic   # production  — Claude Sonnet
LLM_PROVIDER=groq        # free dev    — Llama 3.3 70B
LLM_PROVIDER=gemini      # free dev    — Gemini 2.0 Flash
```

---

## 11. Stateful Conversation with Bounded Memory

LLM APIs are stateless — they have no memory between calls. Statefulness is implemented
by maintaining a message history and sending it with every request. The challenge is
that history grows linearly with conversation length, eventually hitting context limits
and increasing cost.

This system implements a **hybrid sliding window + summarization** strategy managed
by the `ConversationMemory` class in `memory.py`.

### How it works

```
Turns 1–20:   Full history kept in memory
              messages = [msg1, msg2, ... msg20]

Turn 21:      Threshold (20) exceeded — compression triggered:
              - messages[:-10] sent to LLM for summarization
              - Summary stored separately
              - messages = messages[-10:]  (window slides forward)
              - Summary injected into system prompt for next request

Turn 31:      Threshold exceeded again:
              - New old messages summarized and merged with existing summary
              - Window slides again
```

### Key design decision: summary goes in the system prompt, not the message list

```python
def get_system(self, base_system: str) -> str:
    if not self.summary:
        return base_system
    return (
        base_system
        + "\n\n## PREVIOUS CONVERSATION CONTEXT\n"
        + self.summary
    )
```

Injecting the summary into the system prompt is cleaner than inserting fake
`user`/`assistant` messages into the history. It keeps the message list structurally
clean and avoids confusing the LLM with synthetic conversation turns.

### Normalized message format

All providers receive history in the same normalized format:

```python
{"role": "user",        "content": "show delayed orders"}
{"role": "assistant",   "text": None, "tool_calls": [{"id": "1", "name": "list_schemas", "input": {}}]}
{"role": "tool_result", "tool_call_id": "1", "tool_name": "list_schemas", "content": "..."}
{"role": "assistant",   "text": "Here are the results...", "tool_calls": []}
```

Each provider (`AnthropicProvider`, `GroqProvider`, `GeminiProvider`) converts this
normalized format to its own API-specific format on every call. The agent never handles
provider-specific message structures directly.

### Session persistence

Conversations are persisted across restarts via a pluggable `ConversationStore`:

```
STORE_TYPE=json      → sessions/<session_id>.json   (development)
STORE_TYPE=postgres  → agent_sessions table          (production)
```

**JSON store** uses atomic writes (write to `.tmp`, then `os.replace()`) to prevent
corrupt session files on crash.

**PostgreSQL store** uses `INSERT ... ON CONFLICT DO UPDATE` (upsert) so every
`save()` is idempotent — safe to call after every turn.

```sql
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id  VARCHAR(8)   PRIMARY KEY,
    created_at  TIMESTAMPTZ  DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  DEFAULT NOW(),
    summary     TEXT,
    messages    JSONB        NOT NULL DEFAULT '[]',
    turn_count  INTEGER      DEFAULT 0
);
```

Messages are stored as `JSONB`, making them queryable if needed for auditing.

### Session management CLI commands

```
sessions           → list all saved sessions with preview of first question
resume a1b2c3d4    → restore a previous session (history + summary restored)
reset              → clear history, generate new session ID
```

On `resume`, the session summary is displayed so the user immediately sees what
was discussed previously before continuing.

### What this solves in production

| Problem | Solution |
|---|---|
| Context window limits | Sliding window caps active history at 10 messages |
| Token cost growth | Old turns compressed to ~200-word summary |
| Lost state on restart | JSON / PostgreSQL persistence |
| Multi-session workflows | Session IDs allow resuming any past conversation |

---

## 12. Connection Resilience

Enterprise database connections drop. Long-running CLI sessions, idle timeouts, and
network interruptions all cause this. The agent handles it silently:

```python
def _ensure_connection(self) -> None:
    if not self.adapter.is_alive(self.conn):
        self.conn = self.adapter.connect()
```

This is called before every tool dispatch. The `is_alive()` check uses a lightweight
ping (e.g., `SELECT 1` for PostgreSQL, `conn.ping()` for Oracle). If the check fails,
the connection is recreated transparently. The user sees a brief warning message but
the agent continues without interruption.

---

## 13. Structured Logging

All agent activity is written to `logs/agent.log` as newline-delimited JSON (JSONL).

```json
{"timestamp": "2026-04-17T10:00:00Z", "level": "INFO",  "event": "session.start",  "provider": "anthropic", "db_type": "oracle"}
{"timestamp": "2026-04-17T10:00:01Z", "level": "INFO",  "event": "turn.start",     "question": "show delayed orders"}
{"timestamp": "2026-04-17T10:00:03Z", "level": "INFO",  "event": "tool.call",      "tool": "list_schemas"}
{"timestamp": "2026-04-17T10:00:05Z", "level": "INFO",  "event": "sql.executed",   "row_count": 42, "execution_time_ms": 187}
{"timestamp": "2026-04-17T10:00:06Z", "level": "INFO",  "event": "approval.requested", "sql": "UPDATE orders SET..."}
{"timestamp": "2026-04-17T10:00:08Z", "level": "INFO",  "event": "approval.denied"}
```

**Key decisions:**
- SQL query bodies are logged for write operations (audit trail) but not for SELECT
  (avoids logging potentially sensitive data in query results)
- Files rotate at 5 MB with 3 backups — prevents unbounded disk usage
- Console output only shows WARNING+ to avoid duplicating the Rich terminal output

---

## 14. Stopping Conditions

The agent has explicit stopping conditions to prevent infinite loops:

| Condition | Handling |
|---|---|
| Query executed successfully | Return results |
| All 3 retry attempts failed | Inform user, show errors |
| User rejected a write operation | Acknowledge, ask what to do instead |
| Question is ambiguous | Ask for clarification |
| Question is out of scope | Inform user clearly |
| Schema or table inaccessible | Inform user |
| No data returned | Inform user |
| 10 iterations reached | Hard stop — inform user |

The iteration cap is a **safety contract**: the program always terminates, regardless
of unexpected LLM behaviour.

---

## 15. Key Concepts Demonstrated

| Concept | Where |
|---|---|
| Agentic loop with tool use | `agent.py` — `chat()` method |
| Adapter pattern (DB) | `adapters/` — 4 implementations of `BaseAdapter` |
| Adapter pattern (LLM) | `providers/` — 3 implementations of `BaseProvider` |
| Dependency inversion | `agent.py` depends on abstractions, not concrete classes |
| Lazy imports | `adapters/__init__.py`, `providers/__init__.py` |
| Human-in-the-loop | `tools.py` — `_request_human_approval()` |
| Errors as data | All tool functions return dicts, never raise |
| Dynamic prompt templating | `agent.py` — `load_system_prompt()` |
| Hybrid sliding window + summarization | `memory.py` — `ConversationMemory` |
| Pluggable session persistence | `memory.py` — `JsonStore` / `PostgresStore` |
| Normalized message history | `providers/base.py` — provider-agnostic format |
| Connection resilience | `agent.py` — `_ensure_connection()` |
| Structured logging | `logger.py` — JSONL rotating file |
| Prompt as versioned artifact | `system_prompt.md` — separate from code |

---

## 16. What Would Be Added in Production

| Gap | Status | Production Solution |
|---|---|---|
| Token growth in long conversations | **Built** — sliding window + summarization | Tune window/threshold per use case |
| Session persistence | **Built** — JSON (dev) + PostgreSQL (prod) | Already implemented via `STORE_TYPE` |
| Single-user CLI | Gap | FastAPI REST endpoints or Streamlit UI |
| Hardcoded credentials in .env | Gap | Secrets manager (AWS Secrets Manager, Vault) |
| No authentication | Gap | OAuth2 / SSO integration |
| No query caching | Gap | Cache frequent query results (Redis) |
| No cost tracking | Gap | Token usage logging per session |
| Single-threaded | Gap | Async tool dispatch for parallel tool calls |
| No schema versioning | Gap | Schema change detection + prompt invalidation |

---

*Built with: Python 3.10, Anthropic SDK, google-genai, groq, psycopg2, oracledb, pyodbc, Rich*

---

*Built with: Python 3.10, Anthropic SDK, google-genai, groq, psycopg2, oracledb, pyodbc, Rich*
