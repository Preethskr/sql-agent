import os
from datetime import date
from dotenv import load_dotenv

load_dotenv()

# --- LLM Provider ---
LLM_PROVIDER      = os.getenv("LLM_PROVIDER", "anthropic")   # anthropic | groq | gemini

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL             = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Groq (free tier)
GROQ_API_KEY      = os.getenv("GROQ_API_KEY")
GROQ_MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Google Gemini (free tier)
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL      = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# --- Database ---
DB_TYPE         = os.getenv("DB_TYPE", "postgres")       # postgres | oracle | tibero | sqlserver
DB_HOST         = os.getenv("DB_HOST", "localhost")
DB_PORT         = os.getenv("DB_PORT", "5432")
DB_NAME         = os.getenv("DB_NAME", "")               # postgres / sqlserver
DB_SERVICE_NAME = os.getenv("DB_SERVICE_NAME", "")       # oracle
DB_DSN          = os.getenv("DB_DSN", "")                # tibero (ODBC DSN name)
DB_USER         = os.getenv("DB_USER", "")
DB_PASSWORD     = os.getenv("DB_PASSWORD", "")
DB_ODBC_DRIVER  = os.getenv("DB_ODBC_DRIVER", "ODBC Driver 17 for SQL Server")  # sqlserver

# --- Agent Behaviour ---
ROW_LIMIT         = int(os.getenv("ROW_LIMIT", "1000"))
MAX_RETRIES       = int(os.getenv("MAX_RETRIES", "3"))
MAX_ITERATIONS    = int(os.getenv("MAX_ITERATIONS", "10"))
QUERY_TIMEOUT     = int(os.getenv("QUERY_TIMEOUT", "10"))
CARDINALITY_LIMIT = int(os.getenv("CARDINALITY_LIMIT", "50"))

# --- Conversation Memory ---
STORE_TYPE                 = os.getenv("STORE_TYPE", "json")          # json | postgres
SESSIONS_DIR               = os.getenv("SESSIONS_DIR", "sessions")
SESSIONS_WINDOW_SIZE       = int(os.getenv("SESSIONS_WINDOW_SIZE", "10"))
SESSIONS_SUMMARY_THRESHOLD = int(os.getenv("SESSIONS_SUMMARY_THRESHOLD", "20"))

# PostgreSQL sessions store (production only)
SESSIONS_DB_HOST     = os.getenv("SESSIONS_DB_HOST", "localhost")
SESSIONS_DB_PORT     = os.getenv("SESSIONS_DB_PORT", "5432")
SESSIONS_DB_NAME     = os.getenv("SESSIONS_DB_NAME", "agent_sessions")
SESSIONS_DB_USER     = os.getenv("SESSIONS_DB_USER", "")
SESSIONS_DB_PASSWORD = os.getenv("SESSIONS_DB_PASSWORD", "")

# --- Runtime ---
CURRENT_DATE = date.today().isoformat()
