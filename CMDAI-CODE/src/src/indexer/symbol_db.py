import sqlite3
import os
from typing import List, Dict, Any, Optional
from .ts_parser import Symbol

class SymbolDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()
        
    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
                           
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT UNIQUE NOT NULL,
                file_hash TEXT NOT NULL
            )
            """)
            
                            
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER,
                name TEXT NOT NULL,
                symbol_type TEXT NOT NULL,
                start_line INTEGER,
                end_line INTEGER,
                content TEXT,
                FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
            )
            """)
            
                                                                        
            cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
                name,
                content,
                content='symbols',
                content_rowid='id'
            )
            """)
            
                                                 
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis_cache (
                symbol_id INTEGER,
                analysis_type TEXT,
                file_hash TEXT,
                result TEXT,
                created_at INTEGER,
                PRIMARY KEY (symbol_id, analysis_type)
            )
            """)
            
                           
            cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
              INSERT INTO symbols_fts(rowid, name, content) VALUES (new.id, new.name, new.content);
            END;
            """)
            cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
              INSERT INTO symbols_fts(symbols_fts, rowid, name, content) VALUES('delete', old.id, old.name, old.content);
            END;
            """)
            cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS symbols_au AFTER UPDATE ON symbols BEGIN
              INSERT INTO symbols_fts(symbols_fts, rowid, name, content) VALUES('delete', old.id, old.name, old.content);
              INSERT INTO symbols_fts(rowid, name, content) VALUES (new.id, new.name, new.content);
            END;
            """)
            
            conn.commit()

    def upsert_file_symbols(self, filepath: str, file_hash: str, symbols: List[Symbol]):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
                                        
            cursor.execute("INSERT OR IGNORE INTO files (filepath, file_hash) VALUES (?, ?)", (filepath, file_hash))
            cursor.execute("UPDATE files SET file_hash = ? WHERE filepath = ?", (file_hash, filepath))
            cursor.execute("SELECT id FROM files WHERE filepath = ?", (filepath,))
            file_id = cursor.fetchone()[0]
            
                                                
            cursor.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
            
                        
            for sym in symbols:
                cursor.execute("""
                INSERT INTO symbols (file_id, name, symbol_type, start_line, end_line, content)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (file_id, sym.name, sym.symbol_type, sym.start_line, sym.end_line, sym.content))
                
            conn.commit()

    def delete_file(self, filepath: str):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM files WHERE filepath = ?", (filepath,))
            conn.commit()
            
    def search_symbols(self, query: str) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
            SELECT s.id, f.filepath, s.name, s.symbol_type, s.start_line, s.end_line, s.content
            FROM symbols_fts fts
            JOIN symbols s ON fts.rowid = s.id
            JOIN files f ON s.file_id = f.id
            WHERE symbols_fts MATCH ?
            ORDER BY rank
            LIMIT 20
            """, (query,))
            
            return [dict(row) for row in cursor.fetchall()]

                                           
    def get_cached(self, symbol_id: int, analysis_type: str, current_file_hash: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT result, file_hash FROM analysis_cache 
            WHERE symbol_id = ? AND analysis_type = ?
            """, (symbol_id, analysis_type))
            row = cursor.fetchone()
            if row:
                cached_result, cached_hash = row
                if cached_hash == current_file_hash:
                    return cached_result
            return None

    def set_cached(self, symbol_id: int, analysis_type: str, file_hash: str, result: str):
        import time
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO analysis_cache (symbol_id, analysis_type, file_hash, result, created_at)
            VALUES (?, ?, ?, ?, ?)
            """, (symbol_id, analysis_type, file_hash, result, int(time.time())))
            conn.commit()
