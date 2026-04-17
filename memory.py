import json
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

from config import (
    STORE_TYPE, SESSIONS_DIR,
    SESSIONS_WINDOW_SIZE, SESSIONS_SUMMARY_THRESHOLD,
    SESSIONS_DB_HOST, SESSIONS_DB_PORT, SESSIONS_DB_NAME,
    SESSIONS_DB_USER, SESSIONS_DB_PASSWORD
)
from logger import log


# ---------------------------------------------------------------------------
# Summarization prompt
# ---------------------------------------------------------------------------

def _build_summary_prompt(messages: list[dict], existing_summary: Optional[str]) -> str:
    lines = []
    for msg in messages:
        role = msg["role"]
        if role == "user":
            lines.append(f"User: {msg['content']}")
        elif role == "assistant" and msg.get("text"):
            # Truncate long responses to keep the prompt short
            text = msg["text"][:300] + "..." if len(msg.get("text", "")) > 300 else msg.get("text", "")
            lines.append(f"Assistant: {text}")
        elif role == "tool_result":
            lines.append(f"[Tool '{msg['tool_name']}' executed and returned data]")

    history_text = "\n".join(lines)

    prefix = (
        f"Existing summary:\n{existing_summary}\n\nAdditional conversation to incorporate:\n"
        if existing_summary else ""
    )

    return (
        "Summarize the following SQL agent conversation history in under 200 words.\n"
        "Preserve: questions asked, database schemas and tables discovered, "
        "key query results, and any context needed for future follow-up questions.\n\n"
        f"{prefix}{history_text}\n\nSummary:"
    )


# ---------------------------------------------------------------------------
# Storage backends
# ---------------------------------------------------------------------------

class ConversationStore(ABC):

    @abstractmethod
    def save(
        self,
        session_id: str,
        messages:   list,
        summary:    Optional[str],
        turn_count: int
    ) -> None: ...

    @abstractmethod
    def load(self, session_id: str) -> Optional[dict]: ...

    @abstractmethod
    def list_sessions(self) -> list[dict]: ...


class JsonStore(ConversationStore):
    """
    File-based store — one JSON file per session.
    Best for: development, single-user CLI, no external DB dependency.
    """

    def __init__(self, directory: str = None):
        self.directory = directory or SESSIONS_DIR
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, session_id: str) -> str:
        return os.path.join(self.directory, f"{session_id}.json")

    def save(self, session_id: str, messages: list, summary: Optional[str], turn_count: int) -> None:
        data = {
            "session_id": session_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "summary":    summary,
            "messages":   messages,
            "turn_count": turn_count
        }
        path = self._path(session_id)
        # Atomic write — temp file then rename prevents corrupt files on crash
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str, indent=2)
        os.replace(tmp, path)

    def load(self, session_id: str) -> Optional[dict]:
        path = self._path(session_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_sessions(self) -> list[dict]:
        sessions = []
        for filename in sorted(os.listdir(self.directory), reverse=True):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.directory, filename), "r") as f:
                    data = json.load(f)
                preview = next(
                    (m["content"][:70] for m in data.get("messages", []) if m.get("role") == "user"),
                    "No messages"
                )
                sessions.append({
                    "session_id": data["session_id"],
                    "updated_at": data.get("updated_at", "—"),
                    "turn_count": data.get("turn_count", 0),
                    "preview":    preview
                })
            except Exception:
                continue
        return sessions


class PostgresStore(ConversationStore):
    """
    PostgreSQL-backed store — production use.
    Uses a dedicated sessions database separate from the query database.

    Schema is created automatically on first use.
    """

    def __init__(self):
        self._conn = None
        self._ensure_table()

    def _get_conn(self):
        import psycopg2
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                host     = SESSIONS_DB_HOST,
                port     = SESSIONS_DB_PORT,
                dbname   = SESSIONS_DB_NAME,
                user     = SESSIONS_DB_USER,
                password = SESSIONS_DB_PASSWORD
            )
        return self._conn

    def _ensure_table(self) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS agent_sessions (
                        session_id  VARCHAR(8)   PRIMARY KEY,
                        created_at  TIMESTAMPTZ  DEFAULT NOW(),
                        updated_at  TIMESTAMPTZ  DEFAULT NOW(),
                        summary     TEXT,
                        messages    JSONB        NOT NULL DEFAULT '[]',
                        turn_count  INTEGER      DEFAULT 0
                    )
                """)
            conn.commit()

    def save(self, session_id: str, messages: list, summary: Optional[str], turn_count: int) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO agent_sessions
                        (session_id, messages, summary, turn_count, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (session_id) DO UPDATE SET
                        messages   = EXCLUDED.messages,
                        summary    = EXCLUDED.summary,
                        turn_count = EXCLUDED.turn_count,
                        updated_at = NOW()
                """, (session_id, json.dumps(messages, default=str), summary, turn_count))
            conn.commit()

    def load(self, session_id: str) -> Optional[dict]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT session_id, summary, messages, turn_count, updated_at
                    FROM agent_sessions WHERE session_id = %s
                """, (session_id,))
                row = cur.fetchone()

        if not row:
            return None
        return {
            "session_id": row[0],
            "summary":    row[1],
            "messages":   row[2] if isinstance(row[2], list) else json.loads(row[2]),
            "turn_count": row[3],
            "updated_at": row[4].isoformat() if row[4] else None
        }

    def list_sessions(self) -> list[dict]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT session_id, updated_at, turn_count,
                           messages->0->>'content'
                    FROM agent_sessions
                    ORDER BY updated_at DESC
                    LIMIT 20
                """)
                rows = cur.fetchall()
        return [
            {
                "session_id": r[0],
                "updated_at": r[1].isoformat() if r[1] else "—",
                "turn_count": r[2],
                "preview":    (str(r[3])[:70] if r[3] else "No messages")
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Store factory
# ---------------------------------------------------------------------------

def get_store() -> ConversationStore:
    if STORE_TYPE.lower() == "postgres":
        return PostgresStore()
    return JsonStore()


# ---------------------------------------------------------------------------
# ConversationMemory — hybrid sliding window + summarization
# ---------------------------------------------------------------------------

class ConversationMemory:
    """
    Manages conversation history with bounded context.

    Strategy:
    - Keep the last WINDOW_SIZE messages in full (sliding window)
    - When total messages exceed SUMMARY_THRESHOLD, compress older turns
      into a summary using the LLM
    - The summary is injected into the system prompt, not the message list
      (cleaner — no fake user/assistant messages)

    This keeps the context window bounded at ~WINDOW_SIZE messages regardless
    of how long the conversation runs.
    """

    def __init__(self, provider, store: ConversationStore = None):
        self.provider    = provider
        self.store       = store or get_store()
        self.session_id  = str(uuid.uuid4())[:8]
        self.messages:   list          = []
        self.summary:    Optional[str] = None
        self.turn_count: int           = 0

    # --- Persistence --------------------------------------------------------

    def load(self, session_id: str) -> bool:
        """Load a previous session. Returns True if found."""
        data = self.store.load(session_id)
        if not data:
            return False
        self.session_id = data["session_id"]
        self.messages   = data["messages"]
        self.summary    = data.get("summary")
        self.turn_count = data.get("turn_count", 0)
        log.info("memory.loaded", extra={
            "session_id": self.session_id,
            "turn_count": self.turn_count,
            "has_summary": bool(self.summary)
        })
        return True

    def save(self) -> None:
        try:
            self.store.save(self.session_id, self.messages, self.summary, self.turn_count)
            log.debug("memory.saved", extra={"session_id": self.session_id})
        except Exception as e:
            log.warning("memory.save_failed", extra={
                "session_id": self.session_id, "error": str(e)
            })

    # --- History management -------------------------------------------------

    def add(self, message: dict) -> None:
        self.messages.append(message)

    def maybe_compress(self) -> None:
        """
        If history exceeds the threshold, summarize old turns and
        slide the window forward.
        """
        if len(self.messages) <= SESSIONS_SUMMARY_THRESHOLD:
            return

        to_summarize  = self.messages[:-SESSIONS_WINDOW_SIZE]
        self.messages = self.messages[-SESSIONS_WINDOW_SIZE:]

        log.info("memory.compressing", extra={
            "session_id":      self.session_id,
            "msgs_summarized": len(to_summarize),
            "msgs_kept":       len(self.messages)
        })

        try:
            prompt       = _build_summary_prompt(to_summarize, self.summary)
            self.summary = self.provider.complete_text(prompt)
            log.info("memory.compressed", extra={"session_id": self.session_id})
        except Exception as e:
            # Summarization failure is non-fatal — keep previous summary
            log.warning("memory.compress_failed", extra={
                "session_id": self.session_id, "error": str(e)
            })

    def increment_turn(self) -> None:
        self.turn_count += 1

    def reset(self) -> None:
        """Clear history while keeping the session_id."""
        self.messages   = []
        self.summary    = None
        self.turn_count = 0
        self.save()
        log.info("memory.reset", extra={"session_id": self.session_id})

    # --- Context injection --------------------------------------------------

    def get_system(self, base_system: str) -> str:
        """
        Inject the conversation summary into the system prompt.
        This is cleaner than inserting fake messages into the history.
        """
        if not self.summary:
            return base_system
        return (
            base_system
            + "\n\n## PREVIOUS CONVERSATION CONTEXT\n"
            + "The following is a summary of earlier turns in this conversation. "
            + "Use it as background context when answering the current question.\n\n"
            + self.summary
        )

    # --- Utility ------------------------------------------------------------

    def list_sessions(self) -> list[dict]:
        return self.store.list_sessions()
