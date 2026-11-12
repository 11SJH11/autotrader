"""Shared local durable SQLite primitive for simulator and broker preparation."""
import sqlite3


def open_database(path):
    db=sqlite3.connect(path,timeout=5,isolation_level=None)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    return db
