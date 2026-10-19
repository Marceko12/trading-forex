import json
import os
import sqlite3
from datetime import datetime, timezone

class Store:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS backtests (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, symbol TEXT NOT NULL, result TEXT NOT NULL)")
    def _db(self): return sqlite3.connect(self.path)
    def log(self, kind, payload):
        with self._db() as db:
            db.execute("INSERT INTO events(kind,created_at,payload) VALUES(?,?,?)", (kind, datetime.now(timezone.utc).isoformat(), json.dumps(payload, default=str)))
    def events(self, limit=100):
        with self._db() as db:
            rows = db.execute("SELECT id,kind,created_at,payload FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id":i,"kind":k,"created_at":t,"payload":json.loads(p)} for i,k,t,p in rows]
    def latest_event(self, kind):
        with self._db() as db:
            row = db.execute("SELECT id,kind,created_at,payload FROM events WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
        return None if row is None else {"id":row[0],"kind":row[1],"created_at":row[2],"payload":json.loads(row[3])}
    def recent_auto_signal_keys(self, limit=5000):
        with self._db() as db:
            rows = db.execute("SELECT payload FROM events WHERE kind IN ('auto_order_proposed','auto_order_result','signal_held') ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]
    def save_backtest(self, symbol, result):
        with self._db() as db:
            cur = db.execute("INSERT INTO backtests(created_at,symbol,result) VALUES(?,?,?)", (datetime.now(timezone.utc).isoformat(),symbol,json.dumps(result, default=str)))
            return cur.lastrowid
    def backtests(self, limit=20):
        with self._db() as db: rows=db.execute("SELECT id,created_at,symbol,result FROM backtests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id":i,"created_at":t,"symbol":s,"result":json.loads(r)} for i,t,s,r in rows]
