from abc import ABC, abstractmethod


class BaseAdapter(ABC):
    """
    Abstract interface every database adapter must implement.
    tools.py calls only these methods — never anything DB-specific directly.
    """

    @abstractmethod
    def connect(self):
        """Return a live database connection."""

    @abstractmethod
    def is_alive(self, conn) -> bool:
        """Return True if the connection is still usable."""

    @abstractmethod
    def inject_limit(self, sql: str, limit: int) -> str:
        """Append a row-limit clause using the correct dialect."""

    @abstractmethod
    def list_schemas(self, conn) -> dict:
        """Return all user-accessible schemas/owners."""

    @abstractmethod
    def list_tables(self, conn, schema_name: str) -> dict:
        """Return all tables in the given schema."""

    @abstractmethod
    def get_columns_with_types(self, conn, schema_name: str, table_name: str) -> dict:
        """Return column metadata and FK relationships for a table."""

    @abstractmethod
    def get_column_unique_values(self, conn, schema_name: str, table_name: str, column_name: str) -> dict:
        """Return distinct values for a low-cardinality column."""

    @abstractmethod
    def run_query(self, conn, sql: str) -> dict:
        """Execute SQL and return structured results or an error dict."""
