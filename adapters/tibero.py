import pyodbc

from config import DB_DSN, DB_USER, DB_PASSWORD, QUERY_TIMEOUT
from adapters.oracle import OracleAdapter


class TiberoAdapter(OracleAdapter):
    """
    Tibero is Oracle-compatible — same SQL dialect and data dictionary views
    (ALL_TABLES, ALL_TAB_COLUMNS, ALL_CONSTRAINTS).

    Only the connection method differs: Tibero uses ODBC via a pre-configured DSN.
    Everything else is inherited from OracleAdapter.

    Prerequisites:
    - Tibero ODBC driver installed on the host machine
    - DSN configured in ODBC Data Source Administrator (or odbcinst.ini on Linux)
    - DB_DSN in .env set to the configured DSN name
    """

    def connect(self):
        conn = pyodbc.connect(
            f"DSN={DB_DSN};UID={DB_USER};PWD={DB_PASSWORD}",
            timeout=QUERY_TIMEOUT
        )
        return conn

    def is_alive(self, conn) -> bool:
        try:
            conn.cursor().execute("SELECT 1 FROM DUAL")
            return True
        except Exception:
            return False
