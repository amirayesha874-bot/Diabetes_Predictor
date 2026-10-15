"""Database backup / restore (SRS: automated daily backup + manual backup & restore)."""
import glob
import os
import sqlite3
import threading
import time
from datetime import datetime


def make_backup(db_path, backup_dir, label="manual"):
    os.makedirs(backup_dir, exist_ok=True)
    dest = os.path.join(backup_dir, f"diabetes_{label}_{datetime.now():%Y%m%d_%H%M%S}.db")
    src, dst = sqlite3.connect(db_path), sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    return dest


def list_backups(backup_dir):
    files = sorted(glob.glob(os.path.join(backup_dir, "diabetes_*.db")), reverse=True)
    return [{"name": os.path.basename(f), "size_kb": round(os.path.getsize(f) / 1024, 1),
             "time": datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")} for f in files]


def restore_backup(db_path, backup_dir, name):
    src_path = os.path.join(backup_dir, os.path.basename(name))
    if not os.path.exists(src_path):
        raise FileNotFoundError("Backup not found")
    make_backup(db_path, backup_dir, label="pre-restore")          # safety copy
    src, dst = sqlite3.connect(src_path), sqlite3.connect(db_path)
    with dst:
        src.backup(dst)
    src.close(); dst.close()


def start_daily_scheduler(db_path, backup_dir):
    """Background thread: makes one backup per calendar day and keeps the last 14."""
    def loop():
        while True:
            try:
                today = datetime.now().strftime("%Y%m%d")
                if not glob.glob(os.path.join(backup_dir, f"diabetes_daily_{today}_*.db")):
                    make_backup(db_path, backup_dir, label="daily")
                    old = sorted(glob.glob(os.path.join(backup_dir, "diabetes_daily_*.db")))[:-14]
                    for f in old:
                        os.remove(f)
            except Exception:
                pass
            time.sleep(3600)
    threading.Thread(target=loop, daemon=True, name="daily-backup").start()
