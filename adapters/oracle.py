import re
import time

import oracledb

from config import (
    DB_HOST, DB_PORT, DB_SERVICE_NAME, DB_NAME, DB_USER, DB_PASSWORD,
    CARDINALITY_LIMIT, ROW_LIMIT, QUERY_TIMEOUT
)
from adapters.base import BaseAdapter

# Oracle system schemas to exclude from listing
_SYSTEM_OWNERS = {
    'SYS', 'SYSTEM', 'MDSYS', 'CTXSYS', 'XDB', 'WMSYS', 'ORDSYS',
    'ORDDATA', 'OUTLN', 'DBSNMP', 'APPQOSSYS', 'OJVMSYS', 'AUDSYS'
}


class OracleAdapter(BaseAdapter):

    def _dsn(self) -> str:
        # DB_SERVICE_NAME takes priority; fall back to DB_NAME for convenience
        service = DB_SERVICE_NAME or DB_NAME
        if not service:
            raise ValueError("Set DB_SERVICE_NAME (or DB_NAME) in your .env for Oracle connections.")
        return f"{DB_HOST}:{DB_PORT}/{service}"

    def connect(self):
        conn = oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=self._dsn())
        conn.call_timeout = QUERY_TIMEOUT * 1000   # milliseconds
        return conn

    def is_alive(self, conn) -> bool:
        try:
            conn.ping()
            return True
        except Exception:
            return False

    def inject_limit(self, sql: str, limit: int) -> str:
        if re.search(r'\bFETCH\s+FIRST\b', sql, re.IGNORECASE):
            return sql
        if re.search(r'\bROWNUM\b', sql, re.IGNORECASE):
            return sql
        return sql.rstrip('; \n') + f"\nFETCH FIRST {limit} ROWS ONLY"

    def list_schemas(self, conn) -> dict:
        try:
            with conn.cursor() as cur:
                placeholders = ",".join(f"'{s}'" for s in _SYSTEM_OWNERS)
                cur.execute(f"""
                    SELECT DISTINCT owner FROM all_tables
                    WHERE owner NOT IN ({placeholders})
                    ORDER BY owner
                """)
                schemas = [r[0] for r in cur.fetchall()]
            return {"schemas": schemas} if schemas else {"error": "No accessible schemas found."}
        except Exception as e:
            return {"error": str(e)}

    def list_tables(self, conn, schema_name: str) -> dict:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM all_tables WHERE owner = :1 ORDER BY table_name",
                    [schema_name.upper()]
                )
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
                    SELECT c.column_name, c.data_type, c.nullable,
                           CASE WHEN pk.column_name IS NOT NULL THEN 'Y' ELSE 'N' END
                    FROM all_tab_columns c
                    LEFT JOIN (
                        SELECT cc.column_name
                        FROM all_constraints con
                        JOIN all_cons_columns cc
                            ON con.constraint_name = cc.constraint_name
                           AND con.owner           = cc.owner
                        WHERE con.constraint_type = 'P'
                          AND con.owner      = :1
                          AND con.table_name = :2
                    ) pk ON c.column_name = pk.column_name
                    WHERE c.owner = :3 AND c.table_name = :4
                    ORDER BY c.column_id
                """, [schema_name.upper(), table_name.upper(),
                      schema_name.upper(), table_name.upper()])

                rows = cur.fetchall()
                if not rows:
                    return {"error": f"Table '{schema_name}.{table_name}' not found."}

                columns = [
                    {"column": r[0], "type": r[1], "nullable": r[2] == "Y",
                     "primary_key": r[3] == "Y", "foreign_key": None}
                    for r in rows
                ]

                # Foreign keys
                cur.execute("""
                    SELECT fk_col.column_name,
                           pk_con.owner, pk_con.table_name, pk_col.column_name
                    FROM all_constraints fk_con
                    JOIN all_cons_columns fk_col
                        ON fk_con.constraint_name = fk_col.constraint_name
                       AND fk_con.owner           = fk_col.owner
                    JOIN all_constraints pk_con
                        ON fk_con.r_constraint_name = pk_con.constraint_name
                       AND fk_con.r_owner           = pk_con.owner
                    JOIN all_cons_columns pk_col
                        ON pk_con.constraint_name = pk_col.constraint_name
                       AND pk_con.owner           = pk_col.owner
                    WHERE fk_con.constraint_type = 'R'
                      AND fk_con.owner      = :1
                      AND fk_con.table_name = :2
                """, [schema_name.upper(), table_name.upper()])

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
                    f'SELECT COUNT(DISTINCT "{column_name.upper()}") '
                    f'FROM "{schema_name.upper()}"."{table_name.upper()}"'
                )
                count = cur.fetchone()[0]

                if count > CARDINALITY_LIMIT:
                    return {"warning": f"High cardinality — {count:,} unique values. Use range filters.", "unique_count": count}

                cur.execute(
                    f'SELECT DISTINCT "{column_name.upper()}" '
                    f'FROM "{schema_name.upper()}"."{table_name.upper()}" '
                    f'WHERE "{column_name.upper()}" IS NOT NULL '
                    f'ORDER BY "{column_name.upper()}"'
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
                cur.execute(sql)
                if cur.description is None:
                    conn.commit()
                    return {"status": "success", "rows_affected": cur.rowcount}
                columns = [d[0] for d in cur.description]
                rows    = [list(r) for r in cur.fetchall()]
            elapsed = round((time.monotonic() - start) * 1000, 2)
            return {"status": "success", "columns": columns, "rows": rows,
                    "row_count": len(rows), "execution_time_ms": elapsed}
        except oracledb.exceptions.OperationalError as e:
            if "call timeout" in str(e).lower():
                return {"error": f"Query timed out after {QUERY_TIMEOUT}s."}
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}
