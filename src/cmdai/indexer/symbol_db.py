import os
import sqlite3
from typing import Any, Dict, List, Optional


class SymbolDB:

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    filepath TEXT PRIMARY KEY,
                    file_hash TEXT,
                    mtime REAL
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS symbols USING fts5(
                    name,
                    filepath UNINDEXED,
                    symbol_type,
                    start_line UNINDEXED,
                    end_line UNINDEXED,
                    signature,
                    docstring,
                    content
                )
                """
            )
            conn.commit()

    def clear_file_symbols(self, filepath: str) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM symbols WHERE filepath = ?", (filepath,))
            conn.execute("DELETE FROM files WHERE filepath = ?", (filepath,))
            conn.commit()

    def get_file_hash(self, filepath: str) -> Optional[str]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT file_hash FROM files WHERE filepath = ?", (filepath,)).fetchone()
            return row["file_hash"] if row else None

    def add_symbols(self, filepath: str, file_hash: str, mtime: float, symbols: List[Dict[str, Any]]) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM symbols WHERE filepath = ?", (filepath,))
            conn.execute(
                "INSERT OR REPLACE INTO files (filepath, file_hash, mtime) VALUES (?, ?, ?)",
                (filepath, file_hash, mtime),
            )
            for s in symbols:
                conn.execute(
                    """
                    INSERT INTO symbols (name, filepath, symbol_type, start_line, end_line, signature, docstring, content)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        s.get("name", ""),
                        filepath,
                        s.get("type", "symbol"),
                        s.get("start_line", 1),
                        s.get("end_line", 1),
                        s.get("signature", ""),
                        s.get("docstring", ""),
                        s.get("content", ""),
                    ),
                )
            conn.commit()

    def search_symbols(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        clean_query = query.replace('"', '""').strip()
        if not clean_query:
            return []

        fts_query = f'"{clean_query}"*' if not any(c in clean_query for c in "*:\"") else clean_query

        with self._get_connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    SELECT name, filepath, symbol_type, start_line, end_line, signature, docstring, content, rank
                    FROM symbols
                    WHERE symbols MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                )
                rows = cursor.fetchall()
            except sqlite3.OperationalError:
                cursor = conn.execute(
                    """
                    SELECT name, filepath, symbol_type, start_line, end_line, signature, docstring, content, 0 as rank
                    FROM symbols
                    WHERE name LIKE ? OR signature LIKE ?
                    LIMIT ?
                    """,
                    (f"%{clean_query}%", f"%{clean_query}%", limit),
                )
                rows = cursor.fetchall()

            results = []
            for r in rows:
                results.append({
                    "name": r["name"],
                    "filepath": r["filepath"],
                    "symbol_type": r["symbol_type"],
                    "start_line": r["start_line"],
                    "end_line": r["end_line"],
                    "signature": r["signature"],
                    "docstring": r["docstring"],
                    "content": r["content"],
                })
            return results

    def get_stats(self) -> Dict[str, int]:
        with self._get_connection() as conn:
            file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            return {"files": file_count, "symbols": symbol_count}
