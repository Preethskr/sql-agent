import re
import time

import psycopg2
from psycopg2 import sql as pg_sql

from config import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    CARDINALITY_LIMIT, ROW_LIMIT, QUERY_TIMEOUT
)
from adapters.base import BaseAdapter


class PostgresAdapter(BaseAdapter):

    def connect(self):
        return psycopg2.connect(
            host=DB_HOST, port=DB_PORT,
            dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
        )

    def is_alive(self, conn) -> bool:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:
            return False

    def inject_limit(self, sql: str, limit: int) -> str:
        if re.search(r'\bLIMIT\b', sql, re.IGNORECASE):
            return sql
        return sql.rstrip('; \n') + f"\nLIMIT {limit}"

    def list_schemas(self, conn) -> dict:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT schema_name
                    FROM information_schema.schemata
                    WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast')
                    ORDER BY schema_name
                """)
                schemas = [r[0] for r in cur.fetchall()]
            return {"schemas": schemas} if schemas else {"error": "No accessible schemas found."}
        except Exception as e:
            return {"error": str(e)}

    def list_tables(self, conn, schema_name: str) -> dict:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = %s AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                """, (schema_name,))
                tables = [r[0] for r in cur.fetchall()]
            return {"schema": schema_name, "tables": tables} if tables else {
                "error": f"No tables found in schema '{schema_name}'."
            }
        except Exception as e:
            return {"error": str(e)}

    def get_columns_with_types(self, conn, schema_name: str, table_name: str) -> dict:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT c.column_name, c.data_type, c.is_nullable,
                           CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END
                    FROM information_schema.columns c
                    LEFT JOIN (
                        SELECT ku.column_name
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage ku
                            ON tc.constraint_name = ku.constraint_name
                           AND tc.table_schema    = ku.table_schema
                        WHERE tc.constraint_type = 'PRIMARY KEY'
                          AND tc.table_schema = %s AND tc.table_name = %s
                    ) pk ON c.column_name = pk.column_name
                    WHERE c.table_schema = %s AND c.table_name = %s
                    ORDER BY c.ordinal_position
                """, (schema_name, table_name, schema_name, table_name))

                rows = cur.fetchall()
                if not rows:
                    return {"error": f"Table '{schema_name}.{table_name}' not found."}

                columns = [
                    {"column": r[0], "type": r[1], "nullable": r[2] == "YES",
                     "primary_key": r[3], "foreign_key": None}
                    for r in rows
                ]

                cur.execute("""
                    SELECT kcu.column_name,
                           ccu.table_schema, ccu.table_name, ccu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage ccu
                        ON tc.constraint_name = ccu.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_schema = %s AND tc.table_name = %s
                """, (schema_name, table_name))

                fk_map = {r[0]: f"{r[1]}.{r[2]}.{r[3]}" for r in cur.fetchall()}
                for col in columns:
                    col["foreign_key"] = fk_map.get(col["column"])

            return {"schema": schema_name, "table": table_name, "columns": columns}
        except Exception as e:
            return {"error": str(e)}

    def get_column_unique_values(self, conn, schema_name: str, table_name: str, column_name: str) -> dict:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    pg_sql.SQL("SELECT COUNT(DISTINCT {}) FROM {}.{}").format(
                        pg_sql.Identifier(column_name),
                        pg_sql.Identifier(schema_name),
                        pg_sql.Identifier(table_name)
                    )
                )
                count = cur.fetchone()[0]

                if count > CARDINALITY_LIMIT:
                    return {"warning": f"High cardinality — {count:,} unique values. Use range filters.", "unique_count": count}

                cur.execute(
                    pg_sql.SQL(
                        "SELECT DISTINCT {col} FROM {schema}.{table} "
                        "WHERE {col} IS NOT NULL ORDER BY {col}"
                    ).format(
                        col=pg_sql.Identifier(column_name),
                        schema=pg_sql.Identifier(schema_name),
                        table=pg_sql.Identifier(table_name)
                    )
                )
                values = [r[0] for r in cur.fetchall()]

            return {"schema": schema_name, "table": table_name, "column": column_name,
                    "unique_values": values, "count": count}
        except Exception as e:
            return {"error": str(e)}

    def run_query(self, conn, sql: str) -> dict:
        try:
            start = time.monotonic()
            with conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {QUERY_TIMEOUT * 1000}")
                cur.execute(sql)
                if cur.description is None:
                    conn.commit()
                    return {"status": "success", "rows_affected": cur.rowcount}
                columns = [d[0] for d in cur.description]
                rows    = [list(r) for r in cur.fetchall()]
            elapsed = round((time.monotonic() - start) * 1000, 2)
            return {"status": "success", "columns": columns, "rows": rows,
                    "row_count": len(rows), "execution_time_ms": elapsed}
        except psycopg2.errors.QueryCanceled:
            conn.rollback()
            return {"error": f"Query timed out after {QUERY_TIMEOUT}s."}
        except Exception as e:
            conn.rollback()
            return {"error": str(e)}
