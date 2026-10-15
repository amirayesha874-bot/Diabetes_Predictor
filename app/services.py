"""Business logic shared by routes: model training/registration, risk-input handling."""
import json
import os

import pandas as pd
from flask import current_app

from . import ml
from .crypto import decrypt_json, encrypt_json
from .db import execute, get_db, now, query
from .security import hash_password


# ------------------------------------------------------------ models
def register_dataset(filename, path, user_id=None):
    df = ml.load_dataset(path)
    return execute("INSERT INTO datasets(filename,path,n_rows,uploaded_by,uploaded_at) VALUES(?,?,?,?,?)",
                   (filename, path, len(df), user_id, now()))


def feedback_rows():
    """Clinician/user-confirmed outcomes that have not yet been used for training."""
    rows = query("SELECT id, features_enc, actual_outcome FROM feedback "
                 "WHERE actual_outcome IS NOT NULL AND features_enc IS NOT NULL AND used_in_training=0")
    data, ids = [], []
    for r in rows:
        f = decrypt_json(r["features_enc"])
        f[ml.TARGET] = r["actual_outcome"]
        data.append(f)
        ids.append(r["id"])
    return (pd.DataFrame(data) if data else None), ids


def train_and_register(dataset_id, user_id=None, include_feedback=True, activate=True):
    cfg = current_app.config
    ds = query("SELECT * FROM datasets WHERE id=?", (dataset_id,), one=True)
    df = ml.load_dataset(ds["path"])
    fb_ids = []
    if include_feedback:
        fb, fb_ids = feedback_rows()
        if fb is not None:
            for c in ml.FEATURES:
                if c not in fb:
                    fb[c] = None
            df = pd.concat([df, ml.load_dataset(fb[ml.FEATURES + [ml.TARGET]], strict=False)], ignore_index=True)
    res = ml.train_all(df, cfg["MODEL_DIR"])
    run_id = execute("INSERT INTO model_runs(dataset_id,trained_at,trained_by,n_train,n_test,n_feedback,"
                     "best_algo,importance_json) VALUES(?,?,?,?,?,?,?,?)",
                     (dataset_id, now(), user_id, res["n_train"], res["n_test"], len(fb_ids),
                      res["best"], json.dumps(res["importance"])))
    best_id = None
    for m in res["models"]:
        is_best = int(m["algo"] == res["best"])
        mid = execute("INSERT INTO models(run_id,algo,file_path,accuracy,precision_,recall,f1,cm_json,is_best)"
                      " VALUES(?,?,?,?,?,?,?,?,?)",
                      (run_id, m["algo"], m["file"], m["accuracy"], m["precision"], m["recall"],
                       m["f1"], json.dumps(m["cm"]), is_best))
        if is_best:
            best_id = mid
    if fb_ids:
        get_db().executemany("UPDATE feedback SET used_in_training=1 WHERE id=?", [(i,) for i in fb_ids])
        get_db().commit()
    if activate:
        activate_model(best_id)
    return run_id, best_id, res


def activate_model(model_id):
    m = query("SELECT * FROM models WHERE id=?", (model_id,), one=True)
    if not m:
        raise ValueError("Model not found")
    ml.deploy(m["file_path"], current_app.config["MODEL_DIR"])        # atomic hot-swap
    execute("UPDATE models SET is_active=0")
    execute("UPDATE models SET is_active=1 WHERE id=?", (model_id,))


def active_model_row():
    return query("SELECT m.*, r.trained_at, r.n_train, r.n_test FROM models m JOIN model_runs r ON r.id=m.run_id "
                 "WHERE m.is_active=1", one=True)


def ensure_initialized():
    """First-run setup: default admin, default dataset, initial trained model."""
    cfg = current_app.config
    if not query("SELECT 1 FROM users WHERE role='admin' LIMIT 1", one=True):
        execute("INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                ("System Admin", cfg["ADMIN_EMAIL"].lower(), hash_password(cfg["ADMIN_PASSWORD"]),
                 "admin", now()))
    if not query("SELECT 1 FROM datasets LIMIT 1", one=True) and os.path.exists(cfg["DEFAULT_DATASET"]):
        register_dataset("diabetic_data.csv (Kaggle - default)", cfg["DEFAULT_DATASET"])
    if not active_model_row() and not cfg.get("SKIP_TRAIN"):
        ds = query("SELECT id FROM datasets ORDER BY id LIMIT 1", one=True)
        if ds:
            train_and_register(ds["id"], include_feedback=False)


# --------------------------------------------------- health-input handling
RANGES = {  # name: (label, min, max)
    "age": ("Age", 18, 120), "height": ("Height (cm)", 100, 250), "weight": ("Weight (kg)", 25, 350),
    "glucose": ("Fasting glucose (mg/dL)", 30, 500), "systolic": ("Systolic BP", 70, 250),
    "diastolic": ("Diastolic BP", 40, 150), "pregnancies": ("Pregnancies", 0, 20),
    "activity": ("Physical activity (h/week)", 0, 100), "skin": ("Skin-fold thickness (mm)", 1, 100),
    "insulin": ("Insulin (mu U/ml)", 1, 900), "bmi": ("BMI", 10, 60),
}


def parse_health_form(form):
    """Validate the web form. Returns (clean_dict, errors_dict)."""
    errors, c = {}, {}

    def num(key, required=True):
        raw = (form.get(key) or "").strip()
        if raw == "":
            if required:
                errors[key] = "This field is required."
            return None
        try:
            v = float(raw)
        except ValueError:
            errors[key] = "Enter a valid number."
            return None
        label, lo, hi = RANGES[key]
        if not lo <= v <= hi:
            errors[key] = f"{label} must be between {lo} and {hi}."
            return None
        return v

    gender = form.get("gender", "")
    if gender not in ("Female", "Male", "Other"):
        errors["gender"] = "Select a gender."
    age, glucose = num("age"), num("glucose")
    height, weight = num("height"), num("weight")
    sys_, dia = num("systolic"), num("diastolic")
    preg = num("pregnancies", required=False)
    skin, ins = num("skin", required=False), num("insulin", required=False)
    act = num("activity")
    for key, opts in (("family_history", ("Yes", "No")), ("smoking", ("Never", "Former", "Current")),
                      ("diet", ("Poor", "Average", "Good"))):
        if form.get(key) not in opts:
            errors[key] = "Please choose an option."
    if sys_ and dia and sys_ <= dia:
        errors["systolic"] = "Systolic must be higher than diastolic."
    bmi = None
    if height and weight:
        bmi = round(weight / ((height / 100) ** 2), 1)
        if not 10 <= bmi <= 60:
            errors["weight"] = f"Calculated BMI {bmi} is outside 10-60; check height/weight."
    if errors:
        return None, errors
    c.update(Age=age, Gender=gender, Height=height, Weight=weight, BMI=bmi, Glucose=glucose,
             Systolic=sys_, BloodPressure=dia, Activity=act, FamilyHistory=form["family_history"],
             Smoking=form["smoking"], Diet=form["diet"],
             Pregnancies=0.0 if gender == "Male" else (preg or 0.0), SkinThickness=skin, Insulin=ins)
    # Family-history index (Pima "pedigree function") is estimated from yes/no answer.
    c["DiabetesPedigreeFunction"] = 0.65 if c["FamilyHistory"] == "Yes" else 0.25
    c["_dpf_estimated"] = True
    return c, {}


def model_inputs(clean):
    return {f: clean.get(f) for f in ml.FEATURES}


def run_prediction(clean, user_id, created_by):
    """Predict, build recommendations, store (encrypted) - returns prediction id."""
    from .recommend import recommendations
    bundle = ml.load_active(current_app.config["MODEL_DIR"])
    if bundle is None:
        raise RuntimeError("No trained model is available. Ask an administrator to train one.")
    res = ml.predict(bundle, model_inputs(clean))
    recs = recommendations(res["risk_level"], clean)
    payload = {"inputs": clean, "explanation": res["explanation"], "recommendations": recs,
               "model_importance": bundle.get("importance", {})}
    pid = execute("INSERT INTO predictions(user_id,created_by,created_at,risk_score,risk_level,prediction,"
                  "confidence,model_name,payload_enc,response_ms) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (user_id, created_by, now(), round(res["probability"] * 100, 1), res["risk_level"],
                   res["prediction"], res["confidence"], res["model"], encrypt_json(payload),
                   res["elapsed_ms"]))
    return pid
