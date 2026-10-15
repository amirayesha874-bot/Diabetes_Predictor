"""Public pages + patient-facing features: prediction, results, history, dashboard, export, feedback."""
import json
import time
from datetime import datetime, timedelta

from flask import (Blueprint, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, url_for)

from . import ml, reports, services
from .crypto import decrypt_json, encrypt_json
from .db import TS, execute, log_activity, now, query
from .security import login_required

bp = Blueprint("main", __name__)


# ---------------------------------------------------------------- helpers
def can_access_patient(patient_id):
    u = g.user
    if u["id"] == patient_id or u["role"] == "admin":
        return True
    if u["role"] == "professional":
        return bool(query("SELECT 1 FROM users WHERE id=? AND professional_id=?", (patient_id, u["id"]), one=True))
    return False


def load_prediction(pid):
    row = query("SELECT * FROM predictions WHERE id=?", (pid,), one=True)
    if not row:
        abort(404)
    if not can_access_patient(row["user_id"]):
        abort(403)
    return reports.prediction_dict(row)


def user_predictions(uid, order="DESC"):
    rows = query(f"SELECT * FROM predictions WHERE user_id=? ORDER BY created_at {order}, id {order}", (uid,))
    return [reports.prediction_dict(r) for r in rows]


def trend_data(uid, rng):
    days = {"week": 7, "month": 30, "year": 365}.get(rng)
    sql, args = "SELECT created_at, risk_score, risk_level FROM predictions WHERE user_id=?", [uid]
    if days:
        sql += " AND created_at >= ?"
        args.append((datetime.now() - timedelta(days=days)).strftime(TS))
    rows = query(sql + " ORDER BY created_at", tuple(args))
    return {"labels": [r["created_at"][:16] for r in rows], "scores": [r["risk_score"] for r in rows],
            "levels": [r["risk_level"] for r in rows]}


# ------------------------------------------------------------ public pages
@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/education")
def education():
    return render_template("education.html")


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


# --------------------------------------------------------------- dashboard
@bp.route("/dashboard")
@login_required
def dashboard():
    role = g.user["role"]
    if role == "professional":
        return redirect(url_for("pro.home"))
    if role == "admin":
        return redirect(url_for("admin.home"))
    preds = user_predictions(g.user["id"])
    latest = preds[0] if preds else None
    stats = {"count": len(preds),
             "avg": round(sum(p["risk_score"] for p in preds) / len(preds), 1) if preds else None,
             "change": round(preds[0]["risk_score"] - preds[1]["risk_score"], 1) if len(preds) > 1 else None}
    pro = query("SELECT name FROM users WHERE id=?", (g.user["professional_id"],), one=True) if g.user["professional_id"] else None
    return render_template("patient/dashboard.html", latest=latest, stats=stats, pro=pro,
                           model=services.active_model_row())


# -------------------------------------------------------------- prediction
@bp.route("/predict", methods=["GET", "POST"])
@login_required
def predict():
    u = g.user
    if u["role"] == "admin":
        abort(403)
    target = u
    pid = request.values.get("patient_id", type=int)
    if u["role"] == "professional":
        if not pid or not can_access_patient(pid):
            flash("Choose one of your patients first.", "warning")
            return redirect(url_for("pro.home"))
        target = query("SELECT * FROM users WHERE id=?", (pid,), one=True)
    if request.method == "POST":
        clean, errors = services.parse_health_form(request.form)
        if errors:
            flash("Please correct the highlighted fields.", "danger")
            return render_template("patient/predict.html", form=request.form, errors=errors, target=target), 400
        try:
            new_id = services.run_prediction(clean, target["id"], u["id"])
        except RuntimeError as exc:
            flash(str(exc), "danger")
            return render_template("patient/predict.html", form=request.form, errors={}, target=target), 503
        log_activity("prediction", f"prediction #{new_id} for user {target['id']}")
        return redirect(url_for("main.result", pid=new_id))
    # pre-fill from the previous record to speed up re-assessment
    prev = user_predictions(target["id"])
    form = {}
    if prev:
        i = prev[0]["payload"]["inputs"]
        g_ = lambda v: f"{v:g}" if isinstance(v, (int, float)) else v
        form = {"age": g_(i.get("Age")), "gender": i.get("Gender"), "height": g_(i.get("Height")),
                "weight": g_(i.get("Weight")), "family_history": i.get("FamilyHistory"),
                "smoking": i.get("Smoking"), "diet": i.get("Diet")}
    return render_template("patient/predict.html", form=form, errors={}, target=target)


@bp.route("/result/<int:pid>")
@login_required
def result(pid):
    p = load_prediction(pid)
    notes = []
    for n in query("SELECT n.*, u.name FROM notes n JOIN users u ON u.id=n.professional_id "
                   "WHERE prediction_id=? ORDER BY n.created_at DESC", (pid,)):
        notes.append({"name": n["name"], "created_at": n["created_at"], "text": decrypt_json(n["note_enc"]).get("t", "")})
    owner = query("SELECT id,name FROM users WHERE id=?", (p["user_id"],), one=True)
    return render_template("patient/result.html", p=p, notes=notes, owner=owner,
                           is_pro=g.user["role"] in ("professional", "admin") and g.user["id"] != p["user_id"])


@bp.route("/history")
@login_required
def history():
    if g.user["role"] != "patient":
        abort(403)
    preds = user_predictions(g.user["id"])
    sort, direction = request.args.get("sort", "date"), request.args.get("dir", "desc")
    key = {"date": lambda p: p["created_at"], "risk": lambda p: p["risk_score"]}.get(sort, lambda p: p["created_at"])
    preds.sort(key=key, reverse=(direction != "asc"))
    return render_template("patient/history.html", preds=preds, sort=sort, direction=direction)


@bp.route("/history/<int:pid>/delete", methods=["POST"])
@login_required
def delete_prediction(pid):
    row = query("SELECT user_id FROM predictions WHERE id=?", (pid,), one=True)
    if not row:
        abort(404)
    if row["user_id"] != g.user["id"] and g.user["role"] != "admin":
        abort(403)
    execute("DELETE FROM predictions WHERE id=?", (pid,))
    log_activity("prediction_deleted", f"#{pid}")
    flash("Prediction record deleted.", "info")
    return redirect(request.form.get("next") or url_for("main.history"))


@bp.route("/api/trend")
@login_required
def api_trend():
    uid = request.args.get("user_id", type=int) or g.user["id"]
    if not can_access_patient(uid):
        abort(403)
    return jsonify(trend_data(uid, request.args.get("range", "all")))


# ------------------------------------------------------------------ export
@bp.route("/export/history.<fmt>")
@login_required
def export_history(fmt):
    if g.user["role"] != "patient":
        abort(403)
    preds = user_predictions(g.user["id"])
    log_activity("export", f"history {fmt}")
    if fmt == "csv":
        return Response(reports.history_csv(preds), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=prediction_history.csv"})
    if fmt == "pdf":
        pdf = reports.history_pdf(g.user, preds, services.active_model_row())
        return Response(pdf, mimetype="application/pdf",
                        headers={"Content-Disposition": "attachment; filename=prediction_report.pdf"})
    abort(404)


@bp.route("/result/<int:pid>/report.pdf")
@login_required
def result_pdf(pid):
    p = load_prediction(pid)
    owner = query("SELECT * FROM users WHERE id=?", (p["user_id"],), one=True)
    pdf = reports.history_pdf(owner, [p], services.active_model_row())
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=report_{pid}.pdf"})


# ---------------------------------------------------------------- feedback
@bp.route("/feedback", methods=["GET", "POST"])
@login_required
def feedback():
    u = g.user
    if request.method == "POST":
        rating = request.form.get("rating", type=int)
        comment = request.form.get("comment", "").strip()[:2000]
        pid = request.form.get("prediction_id", type=int)
        outcome = request.form.get("actual_outcome")
        if not comment and not rating and outcome in (None, ""):
            flash("Feedback cannot be empty.", "danger")
            return redirect(url_for("main.feedback"))
        if rating is not None and not 1 <= rating <= 5:
            rating = None
        features = None
        if pid:
            p = load_prediction(pid)               # also checks access rights
            if outcome in ("0", "1"):
                features = encrypt_json({k: p["payload"]["inputs"].get(k) for k in ml.FEATURES})
        else:
            outcome = None
        execute("INSERT INTO feedback(user_id,prediction_id,rating,comment,actual_outcome,features_enc,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (u["id"], pid, rating, comment, int(outcome) if outcome in ("0", "1") else None, features, now()))
        log_activity("feedback", f"prediction={pid}")
        flash("Thank you! Your feedback was saved and may be used to improve the model.", "success")
        return redirect(url_for("main.feedback"))
    pid = request.args.get("prediction_id", type=int)
    if u["role"] == "patient":
        recent = [dict(id=r["id"], created_at=r["created_at"], risk_level=r["risk_level"])
                  for r in query("SELECT id,created_at,risk_level FROM predictions WHERE user_id=? ORDER BY id DESC LIMIT 20", (u["id"],))]
    elif u["role"] == "professional":
        recent = [dict(id=r["id"], created_at=r["created_at"], risk_level=r["risk_level"])
                  for r in query("SELECT p.id,p.created_at,p.risk_level FROM predictions p JOIN users x ON x.id=p.user_id "
                                 "WHERE x.professional_id=? ORDER BY p.id DESC LIMIT 30", (u["id"],))]
    else:
        recent = []
    mine = query("SELECT * FROM feedback WHERE user_id=? ORDER BY id DESC LIMIT 10", (u["id"],))
    return render_template("patient/feedback.html", recent=recent, selected=pid, mine=mine)
