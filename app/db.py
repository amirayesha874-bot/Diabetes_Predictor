"""SQLite access layer. All queries are parameterised (prevents SQL injection)."""
import sqlite3
from datetime import datetime

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('patient','professional','admin')),
  is_active INTEGER NOT NULL DEFAULT 1,
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  professional_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  reminder_opt_in INTEGER NOT NULL DEFAULT 0,
  last_reminder TEXT,
  created_at TEXT NOT NULL,
  last_login TEXT
);
CREATE TABLE IF NOT EXISTS predictions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  risk_score REAL NOT NULL,
  risk_level TEXT NOT NULL,
  prediction INTEGER NOT NULL,
  confidence REAL NOT NULL,
  model_name TEXT,
  payload_enc TEXT NOT NULL,
  response_ms REAL
);
CREATE INDEX IF NOT EXISTS idx_pred_user ON predictions(user_id, created_at);
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prediction_id INTEGER NOT NULL REFERENCES predictions(id) ON DELETE CASCADE,
  professional_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  note_enc TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  prediction_id INTEGER REFERENCES predictions(id) ON DELETE SET NULL,
  rating INTEGER,
  comment TEXT,
  actual_outcome INTEGER,
  features_enc TEXT,
  used_in_training INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  action TEXT NOT NULL,
  detail TEXT,
  ip TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS datasets(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT NOT NULL,
  path TEXT NOT NULL,
  n_rows INTEGER,
  uploaded_by INTEGER,
  uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dataset_id INTEGER,
  trained_at TEXT NOT NULL,
  trained_by INTEGER,
  n_train INTEGER, n_test INTEGER,
  n_feedback INTEGER DEFAULT 0,
  best_algo TEXT,
  importance_json TEXT
);
CREATE TABLE IF NOT EXISTS models(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  algo TEXT NOT NULL,
  file_path TEXT NOT NULL,
  accuracy REAL, precision_ REAL, recall REAL, f1 REAL,
  cm_json TEXT,
  is_best INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS password_resets(
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  to_email TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  sent INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
"""

TS = "%Y-%m-%d %H:%M:%S"


def now():
    return datetime.now().strftime(TS)


def connect(path=None):
    conn = sqlite3.connect(path or current_app.config["DATABASE"], timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = connect()
    db.executescript(SCHEMA)
    db.commit()
    db.close()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


def log_activity(action, detail="", user_id=None, ip=None):
    from flask import has_request_context, request, session
    if has_request_context():
        user_id = user_id if user_id is not None else session.get("user_id")
        ip = ip or request.remote_addr
    execute("INSERT INTO activity_logs(user_id,action,detail,ip,created_at) VALUES(?,?,?,?,?)",
            (user_id, action, detail, ip, now()))
