from config import DB_TYPE
from adapters.base import BaseAdapter


def get_adapter() -> BaseAdapter:
    """
    Factory — reads DB_TYPE from config and returns the correct adapter instance.
    Set DB_TYPE in your .env file: postgres | oracle | tibero | sqlserver

    Imports are lazy — only the selected adapter's dependencies are loaded.
    This means pyodbc (Tibero/SQL Server) is never imported when using postgres/oracle.
    """
    key = DB_TYPE.lower().replace(" ", "").replace("_", "")

    if key in ("postgres", "postgresql"):
        from adapters.postgres import PostgresAdapter
        return PostgresAdapter()

    elif key == "oracle":
        from adapters.oracle import OracleAdapter
        return OracleAdapter()

    elif key == "tibero":
        from adapters.tibero import TiberoAdapter
        return TiberoAdapter()

    elif key in ("sqlserver", "mssql"):
        from adapters.sqlserver import SQLServerAdapter
        return SQLServerAdapter()

    else:
        raise ValueError(
            f"Unsupported DB_TYPE '{DB_TYPE}'. "
            f"Supported values: postgres, oracle, tibero, sqlserver"
        )
