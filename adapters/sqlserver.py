import re
import time

import pyodbc

from config import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_ODBC_DRIVER,
    CARDINALITY_LIMIT, ROW_LIMIT, QUERY_TIMEOUT
)
from adapters.base import BaseAdapter


class SQLServerAdapter(BaseAdapter):

    def connect(self):
        conn_str = (
            f"DRIVER={{{DB_ODBC_DRIVER}}};"
            f"SERVER={DB_HOST},{DB_PORT};"
            f"DATABASE={DB_NAME};"
            f"UID={DB_USER};"
            f"PWD={DB_PASSWORD}"
        )
        conn = pyodbc.connect(conn_str, timeout=QUERY_TIMEOUT)
        return conn

    def is_alive(self, conn) -> bool:
        try:
            conn.cursor().execute("SELECT 1")
            return True
        except Exception:
            return False

    def inject_limit(self, sql: str, limit: int) -> str:
        if re.search(r'\bFETCH\s+NEXT\b', sql, re.IGNORECASE):
            return sql
        if re.search(r'\bTOP\b', sql, re.IGNORECASE):
            return sql
        # Inject TOP n immediately after SELECT
        return re.sub(
            r'\bSELECT\b', f'SELECT TOP {limit}', sql,
            count=1, flags=re.IGNORECASE
        )

    def list_schemas(self, conn) -> dict:
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT table_schema
                FROM information_schema.tables
                WHERE table_schema NOT IN ('sys','INFORMATION_SCHEMA')
                ORDER BY table_schema
            """)
            schemas = [r[0] for r in cur.fetchall()]
            return {"schemas": schemas} if schemas else {"error": "No accessible schemas found."}
        except Exception as e:
            return {"error": str(e)}

    def list_tables(self, conn, schema_name: str) -> dict:
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = ? AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """, schema_name)
            tables = [r[0] for r in cur.fetchall()]
            return {"schema": schema_name, "tables": tables} if tables else {
                "error": f"No tables found in schema '{schema_name}'."
            }
        except Exception as e:
            return {"error": str(e)}

    def get_columns_with_types(self, conn, schema_name: str, table_name: str) -> dict:
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT c.column_name, c.data_type, c.is_nullable,
                       CASE WHEN pk.column_name IS NOT NULL THEN 1 ELSE 0 END
                FROM information_schema.columns c
                LEFT JOIN (
                    SELECT ku.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage ku
                        ON tc.constraint_name = ku.constraint_name
                       AND tc.table_schema    = ku.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_schema = ? AND tc.table_name = ?
                ) pk ON c.column_name = pk.column_name
                WHERE c.table_schema = ? AND c.table_name = ?
                ORDER BY c.ordinal_position
            """, (schema_name, table_name, schema_name, table_name))

            rows = cur.fetchall()
            if not rows:
                return {"error": f"Table '{schema_name}.{table_name}' not found."}

            columns = [
                {"column": r[0], "type": r[1], "nullable": r[2] == "YES",
                 "primary_key": bool(r[3]), "foreign_key": None}
                for r in rows
            ]

            # Foreign keys
            cur.execute("""
                SELECT
                    kcu.column_name,
                    ccu.table_schema, ccu.table_name, ccu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                   AND tc.table_schema    = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = ? AND tc.table_name = ?
            """, (schema_name, table_name))

            fk_map = {r[0]: f"{r[1]}.{r[2]}.{r[3]}" for r in cur.fetchall()}
            for col in columns:
                col["foreign_key"] = fk_map.get(col["column"])

            return {"schema": schema_name, "table": table_name, "columns": columns}
        except Exception as e:
            return {"error": str(e)}

    def get_column_unique_values(self, conn, schema_name: str, table_name: str, column_name: str) -> dict:
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT COUNT(DISTINCT [{column_name}]) "
                f"FROM [{schema_name}].[{table_name}]"
            )
            count = cur.fetchone()[0]

            if count > CARDINALITY_LIMIT:
                return {"warning": f"High cardinality — {count:,} unique values. Use range filters.", "unique_count": count}

            cur.execute(
                f"SELECT DISTINCT [{column_name}] FROM [{schema_name}].[{table_name}] "
                f"WHERE [{column_name}] IS NOT NULL ORDER BY [{column_name}]"
            )
            values = [r[0] for r in cur.fetchall()]

            return {"schema": schema_name, "table": table_name, "column": column_name,
                    "unique_values": values, "count": count}
        except Exception as e:
            return {"error": str(e)}

    def run_query(self, conn, sql: str) -> dict:
        try:
            start = time.monotonic()
            cur = conn.cursor()
            cur.execute(sql)
            if cur.description is None:
                conn.commit()
                return {"status": "success", "rows_affected": cur.rowcount}
            columns = [d[0] for d in cur.description]
            rows    = [list(r) for r in cur.fetchall()]
            elapsed = round((time.monotonic() - start) * 1000, 2)
            return {"status": "success", "columns": columns, "rows": rows,
                    "row_count": len(rows), "execution_time_ms": elapsed}
        except Exception as e:
            return {"error": str(e)}
