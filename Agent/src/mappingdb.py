import sqlite3

class MappingDB:
    def __init__(self, mapping_path):
        self.conn = sqlite3.connect(mapping_path)
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("CREATE TABLE IF NOT EXISTS mapping_store "
        "(legacy_var TEXT PRIMARY KEY, pine_var TEXT NOT NULL)")
        self.conn.commit()

    def add_mapping(self, legacy_var, pine_var):
        self.conn.execute(
            "INSERT INTO mapping_store (legacy_var, pine_var) VALUES (?, ?) "
            "ON CONFLICT(legacy_var) DO UPDATE SET pine_var = excluded.pine_var",
            (legacy_var, pine_var)
        )
        self.conn.commit()

    def get_mapping(self, legacy_var):
        cursor = self.conn.execute("SELECT pine_var FROM mapping_store WHERE legacy_var = ?", (legacy_var,))
        row = cursor.fetchone()
        return row[0] if row else None

    def delete_mapping(self, legacy_var):
        self.conn.execute("DELETE FROM mapping_store WHERE legacy_var = ?", (legacy_var,))
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.conn.close()