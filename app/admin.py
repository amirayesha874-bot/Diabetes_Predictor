"""Administrator panel: users, logs, datasets, EDA, training, model management, backups."""
import json
import os
import re
from datetime import datetime, timedelta

from flask import (Blueprint, Response, current_app, flash, g, redirect, render_template, request,
                   url_for)

from . import backup, ml, reports, services
from .db import TS, execute, log_activity, now, query
from .mailer import send_mail
from .security import roles_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@roles_required("admin")
def home():
    cnt = {r["role"]: r["c"] for r in query("SELECT role, COUNT(*) c FROM users GROUP BY role")}
    perf = query("SELECT AVG(response_ms) a, MAX(response_ms) m, COUNT(*) c FROM predictions", one=True)
    since = (datetime.now() - timedelta(days=14)).strftime(TS)
    daily = query("SELECT substr(created_at,1,10) d, COUNT(*) c FROM predictions WHERE created_at>=? GROUP BY d ORDER BY d", (since,))
    levels = {r["risk_level"]: r["c"] for r in query("SELECT risk_level, COUNT(*) c FROM predictions GROUP BY risk_level")}
    model = services.active_model_row()
    run = query("SELECT * FROM model_runs ORDER BY id DESC LIMIT 1", one=True)
    models = query("SELECT * FROM models WHERE run_id=? ORDER BY id", (run["id"],)) if run else []
    logs = query("SELECT l.*, u.email FROM activity_logs l LEFT JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT 8")
    db_kb = round(os.path.getsize(current_app.config["DATABASE"]) / 1024, 1)
    fb = query("SELECT COUNT(*) c FROM feedback WHERE actual_outcome IS NOT NULL AND used_in_training=0", one=True)["c"]
    return render_template("admin/home.html", cnt=cnt, perf=perf, daily=daily, levels=levels, model=model,
                           models=models, logs=logs, db_kb=db_kb, pending_fb=fb)


# ------------------------------------------------------------------ users
@bp.route("/users")
@roles_required("admin")
def users():
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "")
    sql, args = "SELECT u.*, p.name pro_name, (SELECT COUNT(*) FROM predictions WHERE user_id=u.id) n_pred " \
                "FROM users u LEFT JOIN users p ON p.id=u.professional_id WHERE 1=1", []
    if q:
        sql += " AND (u.name LIKE ? OR u.email LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if role in ("patient", "professional", "admin"):
        sql += " AND u.role=?"
        args.append(role)
    rows = query(sql + " ORDER BY u.id DESC LIMIT 500", tuple(args))
    pros = query("SELECT id,name FROM users WHERE role='professional' AND is_active=1 ORDER BY name")
    return render_template("admin/users.html", rows=rows, q=q, role=role, pros=pros)


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
@roles_required("admin")
def toggle_user(uid):
    u = query("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not u:
        flash("User not found.", "danger")
    elif uid == g.user["id"]:
        flash("You cannot deactivate your own account.", "warning")
    else:
        new = 0 if u["is_active"] else 1
        execute("UPDATE users SET is_active=? WHERE id=?", (new, uid))
        log_activity("user_activated" if new else "user_deactivated", f"user {uid}")
        flash(f"{u['name']} {'activated' if new else 'deactivated'}.", "success")
    return redirect(request.referrer or url_for("admin.users"))


@bp.route("/users/<int:uid>/assign", methods=["POST"])
@roles_required("admin")
def assign(uid):
    pro = request.form.get("professional_id") or None
    if pro and not query("SELECT 1 FROM users WHERE id=? AND role='professional'", (pro,), one=True):
        pro = None
    execute("UPDATE users SET professional_id=? WHERE id=? AND role='patient'", (pro, uid))
    log_activity("assign_professional", f"patient {uid} -> {pro}")
    flash("Assignment updated.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:uid>/delete", methods=["POST"])
@roles_required("admin")
def delete_user(uid):
    u = query("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not u or uid == g.user["id"]:
        flash("This user cannot be deleted.", "warning")
    else:
        execute("DELETE FROM users WHERE id=?", (uid,))
        log_activity("user_deleted", f"{u['email']}")
        flash("User and all associated data deleted.", "info")
    return redirect(url_for("admin.users"))


@bp.route("/logs")
@roles_required("admin")
def logs():
    action = request.args.get("action", "")
    page = max(request.args.get("page", 1, type=int), 1)
    sql, args = "SELECT l.*, u.email FROM activity_logs l LEFT JOIN users u ON u.id=l.user_id", []
    if action:
        sql += " WHERE l.action=?"
        args.append(action)
    rows = query(sql + " ORDER BY l.id DESC LIMIT 100 OFFSET ?", tuple(args + [(page - 1) * 100]))
    actions = [r["action"] for r in query("SELECT DISTINCT action FROM activity_logs ORDER BY action")]
    return render_template("admin/logs.html", rows=rows, actions=actions, action=action, page=page)


# ------------------------------------------------------- datasets + training
@bp.route("/datasets", methods=["GET", "POST"])
@roles_required("admin")
def datasets():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith(".csv"):
            flash("Please choose a .csv file.", "danger")
            return redirect(url_for("admin.datasets"))
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(f.filename))
        path = os.path.join(current_app.config["UPLOAD_DIR"], f"{datetime.now():%Y%m%d%H%M%S}_{safe}")
        f.save(path)
        try:
            did = services.register_dataset(safe, path, g.user["id"])
            log_activity("dataset_uploaded", safe)
            flash(f"Dataset '{safe}' uploaded and validated. You can now train a model on it.", "success")
        except Exception as exc:
            os.remove(path)
            flash(f"Invalid dataset: {exc}", "danger")
        return redirect(url_for("admin.datasets"))
    rows = query("SELECT d.*, u.name uname FROM datasets d LEFT JOIN users u ON u.id=d.uploaded_by ORDER BY d.id DESC")
    fb = query("SELECT COUNT(*) c FROM feedback WHERE actual_outcome IS NOT NULL AND used_in_training=0", one=True)["c"]
    return render_template("admin/datasets.html", rows=rows, pending_fb=fb)


@bp.route("/datasets/<int:did>/delete", methods=["POST"])
@roles_required("admin")
def delete_dataset(did):
    d = query("SELECT * FROM datasets WHERE id=?", (did,), one=True)
    if d and query("SELECT COUNT(*) c FROM datasets", one=True)["c"] > 1:
        execute("DELETE FROM datasets WHERE id=?", (did,))
        if d["path"].startswith(current_app.config["UPLOAD_DIR"]) and os.path.exists(d["path"]):
            os.remove(d["path"])
        flash("Dataset removed.", "info")
    else:
        flash("At least one dataset must remain.", "warning")
    return redirect(url_for("admin.datasets"))


@bp.route("/datasets/<int:did>/train", methods=["POST"])
@roles_required("admin")
def train(did):
    try:
        run_id, best_id, res = services.train_and_register(
            did, g.user["id"], include_feedback=bool(request.form.get("feedback")),
            activate=bool(request.form.get("activate", "1")))
        log_activity("model_trained", f"run {run_id}, best {res['best']}")
        flash(f"Training complete. Best model: {res['best']}. It has been deployed without downtime.", "success")
    except Exception as exc:
        flash(f"Training failed: {exc}", "danger")
        return redirect(url_for("admin.datasets"))
    return redirect(url_for("admin.models", run=run_id))


@bp.route("/eda")
@roles_required("admin")
def eda():
    did = request.args.get("dataset", type=int)
    ds = query("SELECT * FROM datasets WHERE id=?", (did,), one=True) if did else \
        query("SELECT * FROM datasets ORDER BY id LIMIT 1", one=True)
    if not ds:
        flash("No dataset available.", "warning")
        return redirect(url_for("admin.datasets"))
    summary = ml.eda_summary(ml.load_dataset(ds["path"]))
    return render_template("admin/eda.html", ds=ds, s=summary,
                           all_ds=query("SELECT id,filename FROM datasets ORDER BY id"))


# ------------------------------------------------------------------ models
@bp.route("/models")
@roles_required("admin")
def models():
    runs = query("SELECT r.*, d.filename FROM model_runs r LEFT JOIN datasets d ON d.id=r.dataset_id ORDER BY r.id DESC")
    if not runs:
        flash("No models trained yet.", "warning")
        return redirect(url_for("admin.datasets"))
    rid = request.args.get("run", type=int) or runs[0]["id"]
    run = query("SELECT r.*, d.filename FROM model_runs r LEFT JOIN datasets d ON d.id=r.dataset_id WHERE r.id=?", (rid,), one=True)
    ms = query("SELECT * FROM models WHERE run_id=? ORDER BY id", (rid,))
    cms = {m["algo"]: json.loads(m["cm_json"]) for m in ms}
    return render_template("admin/models.html", runs=runs, run=run, ms=ms, cms=cms,
                           imp=json.loads(run["importance_json"] or "{}"))


@bp.route("/models/<int:mid>/deploy", methods=["POST"])
@roles_required("admin")
def deploy(mid):
    try:
        services.activate_model(mid)
        log_activity("model_deployed", f"model {mid}")
        flash("Model deployed. New predictions now use it (no restart needed).", "success")
    except Exception as exc:
        flash(f"Deploy failed: {exc}", "danger")
    return redirect(request.referrer or url_for("admin.models"))


@bp.route("/models/<int:rid>/report.pdf")
@roles_required("admin")
def model_report(rid):
    run = query("SELECT * FROM model_runs WHERE id=?", (rid,), one=True)
    ms = query("SELECT * FROM models WHERE run_id=? ORDER BY id", (rid,))
    return Response(reports.model_report_pdf(ms, run), mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=model_report_{rid}.pdf"})


# ----------------------------------------------------- backups / misc admin
@bp.route("/backups", methods=["GET", "POST"])
@roles_required("admin")
def backups():
    cfg = current_app.config
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "create":
                backup.make_backup(cfg["DATABASE"], cfg["BACKUP_DIR"])
                log_activity("backup_created", "manual")
                flash("Backup created.", "success")
            elif action == "restore":
                backup.restore_backup(cfg["DATABASE"], cfg["BACKUP_DIR"], request.form.get("name", ""))
                log_activity("backup_restored", request.form.get("name", ""))
                flash("Database restored (a safety backup of the previous state was created first).", "success")
        except Exception as exc:
            flash(f"Backup operation failed: {exc}", "danger")
        return redirect(url_for("admin.backups"))
    return render_template("admin/backups.html", files=backup.list_backups(cfg["BACKUP_DIR"]))


@bp.route("/feedback")
@roles_required("admin")
def feedback():
    rows = query("SELECT f.*, u.name, u.role FROM feedback f JOIN users u ON u.id=f.user_id ORDER BY f.id DESC LIMIT 300")
    return render_template("admin/feedback.html", rows=rows)


@bp.route("/outbox")
@roles_required("admin")
def outbox():
    return render_template("admin/outbox.html", rows=query("SELECT * FROM outbox ORDER BY id DESC LIMIT 100"))


@bp.route("/reminders", methods=["POST"])
@roles_required("admin")
def reminders():
    """Monthly reassessment reminders for opted-in patients (run from here or via cron:
    `python send_reminders.py`)."""
    n = send_monthly_reminders()
    flash(f"{n} reminder email(s) queued.", "success")
    return redirect(url_for("admin.outbox"))


def send_monthly_reminders():
    cutoff = (datetime.now() - timedelta(days=30)).strftime(TS)
    n = 0
    for u in query("SELECT * FROM users WHERE role='patient' AND is_active=1 AND reminder_opt_in=1 "
                   "AND (last_reminder IS NULL OR last_reminder<?)", (cutoff,)):
        last = query("SELECT MAX(created_at) m FROM predictions WHERE user_id=?", (u["id"],), one=True)["m"]
        if last is None or last < cutoff:
            send_mail(u["email"], "Time for your monthly diabetes risk check",
                      f"Hello {u['name']},\n\nIt has been a while since your last assessment. "
                      "Log in and record your latest readings to keep your trend up to date.")
            execute("UPDATE users SET last_reminder=? WHERE id=?", (now(), u["id"]))
            n += 1
    return n
