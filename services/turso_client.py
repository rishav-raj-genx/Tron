"""
Turso Cloud Database (libSQL) HTTP Pipeline Client.

Provides a lightweight, zero-dependency SQLite-compatible interface to
Turso (libsql:// or https://) using Turso's v2 HTTP pipeline API.
Supports dictionary & index-based column lookups, transaction commits,
and automatic mapping of SQLite errors and constraints.
"""

import json
import logging
import sqlite3
import urllib.error
import urllib.request
from typing import Any, Iterator, Sequence

logger = logging.getLogger(__name__)


class TursoRow:
    """
    Row object mimicking sqlite3.Row.
    Allows lookups both by column name (row['col']) and by index (row[0]),
    as well as dict-like iteration and .get() method.
    """

    def __init__(self, cols: Sequence[str], values: Sequence[Any]):
        self._cols = list(cols)
        self._values = list(values)
        self._map = dict(zip(cols, values))

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._map[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._map.get(key, default)

    def keys(self) -> Sequence[str]:
        return self._cols

    def values(self) -> Sequence[Any]:
        return self._values

    def items(self) -> list[tuple[str, Any]]:
        return list(self._map.items())

    def __iter__(self) -> Iterator[str]:
        return iter(self._cols)

    def __len__(self) -> int:
        return len(self._cols)

    def __contains__(self, key: str) -> bool:
        return key in self._map

    def __repr__(self) -> str:
        return f"<TursoRow {self._map}>"


class TursoCursor:
    """
    Cursor object mimicking sqlite3.Cursor.
    Provides fetchone(), fetchall(), rowcount, lastrowid, and iteration.
    """

    def __init__(
        self,
        cols: Sequence[str],
        raw_rows: Sequence[Sequence[dict[str, Any]]],
        affected_row_count: int = 0,
        last_insert_rowid: str | int | None = None
    ):
        self._cols = list(cols)
        self.rowcount = affected_row_count
        self.lastrowid = int(last_insert_rowid) if last_insert_rowid is not None else None
        self.description = [(c, None, None, None, None, None, None) for c in self._cols]
        self._rows: list[TursoRow] = []

        for row in raw_rows:
            parsed_values: list[Any] = []
            for col in row:
                t = col.get("type")
                v = col.get("value")
                if t == "null" or v is None:
                    parsed_values.append(None)
                elif t == "integer":
                    parsed_values.append(int(v))
                elif t == "float":
                    parsed_values.append(float(v))
                else:
                    parsed_values.append(v)
            self._rows.append(TursoRow(self._cols, parsed_values))

    def fetchall(self) -> list[TursoRow]:
        return list(self._rows)

    def fetchone(self) -> TursoRow | None:
        if self._rows:
            return self._rows[0]
        return None

    def __iter__(self) -> Iterator[TursoRow]:
        return iter(self._rows)


class TursoConnection:
    """
    SQLite-compatible connection wrapper communicating over Turso v2 HTTP pipeline.
    """

    def __init__(self, db_url: str, auth_token: str):
        # Convert libsql:// URL format to https://
        clean_url = db_url.strip()
        if clean_url.startswith("libsql://"):
            clean_url = "https://" + clean_url[len("libsql://"):]
        clean_url = clean_url.rstrip("/")
        if not clean_url.endswith("/v2/pipeline"):
            clean_url = f"{clean_url}/v2/pipeline"

        self.endpoint = clean_url
        self.auth_token = auth_token.strip()
        self.row_factory = None

    def _format_arg(self, arg: Any) -> dict[str, Any]:
        """Convert a Python value into a Turso JSON argument object."""
        if arg is None:
            return {"type": "null"}
        elif isinstance(arg, bool):
            return {"type": "integer", "value": "1" if arg else "0"}
        elif isinstance(arg, int):
            return {"type": "integer", "value": str(arg)}
        elif isinstance(arg, float):
            return {"type": "float", "value": arg}
        elif isinstance(arg, (bytes, bytearray)):
            import base64
            return {"type": "blob", "base64": base64.b64encode(arg).decode("utf-8")}
        else:
            return {"type": "text", "value": str(arg)}

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> TursoCursor:
        """
        Execute an SQL statement on the Turso cloud database.
        Silently succeeds on PRAGMA statements (which are managed cluster-side).
        """
        stripped = sql.strip().upper()
        if stripped.startswith("PRAGMA"):
            return TursoCursor([], [], 0, None)

        stmt: dict[str, Any] = {"sql": sql}
        if params:
            stmt["args"] = [self._format_arg(p) for p in params]

        body = {
            "requests": [
                {"type": "execute", "stmt": stmt},
                {"type": "close"}
            ]
        }

        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.auth_token}",
                "Content-Type": "application/json"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="ignore")
            logger.error(f"[TURSO] HTTP Error {e.code}: {error_body}")
            raise sqlite3.OperationalError(f"Turso HTTP {e.code}: {error_body}")
        except Exception as e:
            logger.error(f"[TURSO] Request failed: {e}")
            raise sqlite3.OperationalError(f"Turso request failed: {e}")

        results = data.get("results", [])
        for r in results:
            if r.get("type") == "error":
                err = r.get("error", {})
                code = err.get("code", "")
                msg = err.get("message", "Turso database error")
                if "SQLITE_CONSTRAINT" in code or "UNIQUE constraint failed" in msg:
                    raise sqlite3.IntegrityError(msg)
                raise sqlite3.OperationalError(msg)
            elif r.get("type") == "ok":
                res = r.get("response", {}).get("result", {})
                cols = [c.get("name") for c in res.get("cols", [])]
                rows = res.get("rows", [])
                affected = res.get("affected_row_count", 0)
                last_id = res.get("last_insert_rowid")
                return TursoCursor(cols, rows, affected, last_id)

        return TursoCursor([], [], 0, None)

    def commit(self) -> None:
        """Pipeline statements auto-commit in Turso."""
        pass

    def rollback(self) -> None:
        """No-op for single pipeline calls."""
        pass

    def close(self) -> None:
        """No persistent socket to close."""
        pass

    def __enter__(self) -> "TursoConnection":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
