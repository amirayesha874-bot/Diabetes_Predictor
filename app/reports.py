"""CSV and PDF report generation (FR-13)."""
import csv
import io
import json
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .crypto import decrypt_json

DISCLAIMER = ("This report is produced by a machine-learning screening aid. It does not constitute a "
              "medical diagnosis and does not replace professional medical judgment.")


def prediction_dict(row):
    d = dict(row)
    d["payload"] = decrypt_json(row["payload_enc"])
    d.pop("payload_enc", None)
    return d


def _flat(p):
    i = p["payload"].get("inputs", {})
    return [p["id"], p["created_at"], i.get("Age"), i.get("Gender"), i.get("BMI"), i.get("Glucose"),
            i.get("Systolic"), i.get("BloodPressure"), i.get("FamilyHistory"), i.get("Smoking"),
            i.get("Activity"), i.get("Diet"), p["risk_score"], p["risk_level"], p["confidence"],
            p["model_name"]]


CSV_HEAD = ["PredictionID", "Date", "Age", "Gender", "BMI", "Glucose", "SystolicBP", "DiastolicBP",
            "FamilyHistory", "Smoking", "ActivityHrsWeek", "Diet", "RiskScore%", "RiskLevel",
            "Confidence%", "Model"]


def history_csv(preds, extra_cols=None):
    buf = io.StringIO()
    w = csv.writer(buf)
    head = (extra_cols or []) + CSV_HEAD
    w.writerow(head)
    for p in preds:
        w.writerow(((p.get("_extra") or []) if extra_cols else []) + _flat(p))
    return buf.getvalue().encode("utf-8-sig")


def _table(data, widths=None, head_color="#0f766e"):
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(head_color)),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("FONTSIZE", (0, 0), (-1, -1), 8),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t


def history_pdf(user, preds, model_row=None, patient_name=None):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=1.2 * cm, bottomMargin=1.2 * cm, title="Diabetes Risk Report")
    st = getSampleStyleSheet()
    el = [Paragraph("Intelligent Diabetes Risk Predictor - Prediction Report", st["Title"]),
          Paragraph(f"<b>Patient:</b> {patient_name or user['name']} &nbsp;&nbsp; <b>Email:</b> {user['email']} "
                    f"&nbsp;&nbsp; <b>Generated:</b> {datetime.now():%Y-%m-%d %H:%M}", st["Normal"]),
          Spacer(1, 10)]
    if preds:
        latest = preds[0]
        el += [Paragraph("Latest result", st["Heading2"]),
               Paragraph(f"<b>{latest['risk_level']}</b> - risk score {latest['risk_score']}% "
                         f"(confidence {latest['confidence']}%) on {latest['created_at']}.", st["Normal"])]
        expl = latest["payload"].get("explanation", [])[:5]
        if expl:
            el += [Spacer(1, 6), Paragraph("Key contributing factors", st["Heading3"]),
                   _table([["Factor", "Your value", "Typical", "Effect on risk"]] +
                          [[e["label"], e["value"], e["typical"], f"{e['impact']:+.1f} pts"] for e in expl],
                          [6 * cm, 4 * cm, 4 * cm, 4 * cm])]
        recs = latest["payload"].get("recommendations", [])
        if recs:
            el += [Spacer(1, 6), Paragraph("Recommendations", st["Heading3"])]
            el += [Paragraph(f"- <b>{r['category']}:</b> {r['text']}", st["Normal"]) for r in recs[:8]]
    el += [Spacer(1, 10), Paragraph("Prediction history", st["Heading2"])]
    rows = [["#", "Date", "Age", "BMI", "Glucose", "BP", "Risk %", "Level", "Conf. %"]]
    for p in preds:
        i = p["payload"].get("inputs", {})
        rows.append([p["id"], p["created_at"], i.get("Age"), i.get("BMI"), i.get("Glucose"),
                     f"{i.get('Systolic')}/{i.get('BloodPressure')}", p["risk_score"], p["risk_level"], p["confidence"]])
    el.append(_table(rows) if len(rows) > 1 else Paragraph("No predictions recorded.", st["Normal"]))
    if model_row:
        el += [Spacer(1, 10), Paragraph("Model evaluation metrics (active model)", st["Heading2"]),
               _table([["Algorithm", "Accuracy", "Precision", "Recall", "F1-score"],
                       [model_row["algo"], f"{model_row['accuracy']:.3f}", f"{model_row['precision_']:.3f}",
                        f"{model_row['recall']:.3f}", f"{model_row['f1']:.3f}"]])]
    el += [Spacer(1, 12), Paragraph(f"<i>{DISCLAIMER}</i>", st["Normal"])]
    doc.build(el)
    return buf.getvalue()


def model_report_pdf(models, run):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title="Model evaluation report")
    st = getSampleStyleSheet()
    el = [Paragraph("Model Evaluation Report", st["Title"]),
          Paragraph(f"Training run #{run['id']} - {run['trained_at']} - train rows {run['n_train']}, "
                    f"test rows {run['n_test']} (70/30 split)", st["Normal"]), Spacer(1, 10)]
    rows = [["Algorithm", "Accuracy", "Precision", "Recall", "F1", "Best"]]
    for m in models:
        rows.append([m["algo"], f"{m['accuracy']:.3f}", f"{m['precision_']:.3f}", f"{m['recall']:.3f}",
                     f"{m['f1']:.3f}", "Yes" if m["is_best"] else ""])
    el += [_table(rows), Spacer(1, 10), Paragraph("Confusion matrices (rows = actual, cols = predicted)", st["Heading2"])]
    for m in models:
        mat = json.loads(m["cm_json"])
        el += [Paragraph(m["algo"], st["Heading3"]),
               _table([["", "Pred 0", "Pred 1"], ["Actual 0", mat[0][0], mat[0][1]], ["Actual 1", mat[1][0], mat[1][1]]],
                      [3 * cm, 3 * cm, 3 * cm])]
    imp = json.loads(run["importance_json"] or "{}")
    if imp:
        el += [Spacer(1, 8), Paragraph("Feature importance (permutation, best model)", st["Heading2"]),
               _table([["Feature", "Importance"]] + [[k, f"{v:.4f}"] for k, v in sorted(imp.items(), key=lambda x: -x[1])],
                      [6 * cm, 4 * cm])]
    doc.build(el)
    return buf.getvalue()
