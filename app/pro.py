"""Healthcare-professional panel: patient list, patient history/trends, notes, CSV export."""
from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from . import reports, services
from .crypto import encrypt_json
from .db import execute, log_activity, now, query
from .security import roles_required
from .views import can_access_patient, user_predictions

bp = Blueprint("pro", __name__, url_prefix="/pro")


@bp.route("/")
@roles_required("professional")
def home():
    patients = query("SELECT * FROM users WHERE professional_id=? AND is_active=1 ORDER BY name", (g.user["id"],))
    rows = []
    for p in patients:
        last = query("SELECT risk_level,risk_score,created_at FROM predictions WHERE user_id=? ORDER BY id DESC LIMIT 1",
                     (p["id"],), one=True)
        cnt = query("SELECT COUNT(*) c FROM predictions WHERE user_id=?", (p["id"],), one=True)["c"]
        rows.append({"u": p, "last": last, "count": cnt})
    summary = {"total": len(rows), "high": sum(1 for r in rows if r["last"] and r["last"]["risk_level"] == "High Risk"),
               "mod": sum(1 for r in rows if r["last"] and r["last"]["risk_level"] == "Moderate Risk"),
               "none": sum(1 for r in rows if not r["last"])}
    return render_template("pro/home.html", rows=rows, summary=summary)


@bp.route("/add", methods=["POST"])
@roles_required("professional")
def add_patient():
    email = request.form.get("email", "").strip().lower()
    p = query("SELECT * FROM users WHERE email=? AND role='patient' AND is_active=1", (email,), one=True)
    if not p:
        flash("No active patient account found with that email.", "danger")
    elif p["professional_id"] and p["professional_id"] != g.user["id"]:
        flash("That patient is already assigned to another professional.", "warning")
    else:
        execute("UPDATE users SET professional_id=? WHERE id=?", (g.user["id"], p["id"]))
        log_activity("patient_assigned", f"patient {p['id']}")
        flash(f"{p['name']} was added to your patient list.", "success")
    return redirect(url_for("pro.home"))


@bp.route("/remove/<int:pid>", methods=["POST"])
@roles_required("professional")
def remove_patient(pid):
    if can_access_patient(pid):
        execute("UPDATE users SET professional_id=NULL WHERE id=? AND professional_id=?", (pid, g.user["id"]))
        flash("Patient removed from your list.", "info")
    return redirect(url_for("pro.home"))


@bp.route("/patient/<int:pid>")
@roles_required("professional", "admin")
def patient(pid):
    if not can_access_patient(pid):
        abort(403)
    p = query("SELECT * FROM users WHERE id=? AND role='patient'", (pid,), one=True)
    if not p:
        abort(404)
    preds = user_predictions(pid)
    notes = {}
    for n in query("SELECT n.prediction_id, n.created_at, u.name FROM notes n JOIN users u ON u.id=n.professional_id "
                   "WHERE n.prediction_id IN (SELECT id FROM predictions WHERE user_id=?)", (pid,)):
        notes[n["prediction_id"]] = notes.get(n["prediction_id"], 0) + 1
    log_activity("view_patient", f"patient {pid}")
    return render_template("pro/patient.html", patient=p, preds=preds, notes=notes)


@bp.route("/note", methods=["POST"])
@roles_required("professional")
def add_note():
    pid = request.form.get("prediction_id", type=int)
    text = request.form.get("note", "").strip()[:2000]
    row = query("SELECT user_id FROM predictions WHERE id=?", (pid,), one=True) if pid else None
    if not row or not can_access_patient(row["user_id"]):
        abort(403)
    if not text:
        flash("Note cannot be empty.", "danger")
    else:
        execute("INSERT INTO notes(prediction_id,professional_id,note_enc,created_at) VALUES(?,?,?,?)",
                (pid, g.user["id"], encrypt_json({"t": text}), now()))
        log_activity("note_added", f"prediction {pid}")
        flash("Note added.", "success")
    return redirect(url_for("main.result", pid=pid))


def _csv_response(preds_with_names, filename):
    for p, name in preds_with_names:
        p["_extra"] = [name]
    data = reports.history_csv([p for p, _ in preds_with_names], extra_cols=["Patient"])
    return Response(data, mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


@bp.route("/patient/<int:pid>/export.csv")
@roles_required("professional", "admin")
def export_patient(pid):
    if not can_access_patient(pid):
        abort(403)
    name = query("SELECT name FROM users WHERE id=?", (pid,), one=True)["name"]
    log_activity("export", f"patient {pid} csv")
    return _csv_response([(p, name) for p in user_predictions(pid)], f"patient_{pid}.csv")


@bp.route("/patient/<int:pid>/report.pdf")
@roles_required("professional", "admin")
def patient_pdf(pid):
    if not can_access_patient(pid):
        abort(403)
    u = query("SELECT * FROM users WHERE id=?", (pid,), one=True)
    pdf = reports.history_pdf(u, user_predictions(pid), services.active_model_row())
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f"attachment; filename=patient_{pid}.pdf"})


@bp.route("/export.csv")
@roles_required("professional")
def export_all():
    out = []
    for u in query("SELECT id,name FROM users WHERE professional_id=?", (g.user["id"],)):
        out += [(p, u["name"]) for p in user_predictions(u["id"])]
    log_activity("export", "all patients csv")
    return _csv_response(out, "my_patients.csv")
