"""Machine-learning engine: preprocessing, training (NN, SVM, Decision Tree,
Logistic Regression), evaluation, persistence, prediction and explanations."""
import json
import os
import time
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score,
                             precision_score, recall_score)
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier

FEATURES = ["Pregnancies", "Glucose", "BloodPressure", "SkinThickness",
            "Insulin", "BMI", "DiabetesPedigreeFunction", "Age"]
ZERO_AS_MISSING = ["Glucose", "BloodPressure", "SkinThickness", "Insulin", "BMI"]
TARGET = "Outcome"
RENAME = {"preg": "Pregnancies", "plas": "Glucose", "pres": "BloodPressure",
          "skin": "SkinThickness", "test": "Insulin", "mass": "BMI",
          "pedi": "DiabetesPedigreeFunction", "age": "Age", "class": TARGET,
          # tolerate other common spellings
          "pregnancies": "Pregnancies", "glucose": "Glucose", "bloodpressure": "BloodPressure",
          "skinthickness": "SkinThickness", "insulin": "Insulin", "bmi": "BMI",
          "diabetespedigreefunction": "DiabetesPedigreeFunction", "outcome": TARGET}

LABELS = {"Pregnancies": "Pregnancies", "Glucose": "Glucose", "BloodPressure": "Blood pressure",
          "SkinThickness": "Skin-fold thickness", "Insulin": "Insulin", "BMI": "BMI",
          "DiabetesPedigreeFunction": "Family-history index", "Age": "Age"}

# Risk categories derived from the predicted probability of diabetes
LOW_MAX, MOD_MAX = 0.35, 0.65


def risk_level(p):
    return "Low Risk" if p < LOW_MAX else ("Moderate Risk" if p < MOD_MAX else "High Risk")


# ----------------------------------------------------------------- data
def load_dataset(source, strict=True):
    """Load + validate a CSV (path or DataFrame). Returns a clean DataFrame with
    FEATURES + TARGET where physiologically impossible zeros are set to NaN."""
    df = source.copy() if isinstance(source, pd.DataFrame) else pd.read_csv(source)
    df.columns = [RENAME.get(str(c).strip().lower(), str(c).strip()) for c in df.columns]
    missing = [c for c in FEATURES + [TARGET] if c not in df.columns]
    if missing:
        raise ValueError("Dataset is missing required column(s): " + ", ".join(missing))
    df = df[FEATURES + [TARGET]].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=[TARGET])
    if strict:
        df = df.drop_duplicates()
    df[TARGET] = df[TARGET].astype(int)
    if set(df[TARGET].unique()) - {0, 1}:
        raise ValueError("Target column must contain only 0/1 values.")
    if strict and (len(df) < 50 or df[TARGET].nunique() < 2):
        raise ValueError("Dataset is too small or has only one class (need >= 50 rows, both classes).")
    for c in ZERO_AS_MISSING:
        df.loc[df[c] <= 0, c] = np.nan
    return df.reset_index(drop=True)


def eda_summary(df):
    """Everything the EDA page needs (summary stats, balance, histograms, correlation)."""
    stats = df[FEATURES].describe().T.round(2)
    stats["missing"] = df[FEATURES].isna().sum()
    hist = {}
    for c in FEATURES:
        s = df[c].dropna()
        counts, edges = np.histogram(s, bins=12)
        hist[c] = {"labels": [f"{edges[i]:.1f}-{edges[i+1]:.1f}" for i in range(len(counts))],
                   "counts": counts.tolist()}
    corr = df[FEATURES + [TARGET]].corr().round(2)
    by_class = df.groupby(TARGET)[FEATURES].mean().round(2)
    return {"rows": len(df), "balance": df[TARGET].value_counts().sort_index().tolist(),
            "stats": stats.reset_index().rename(columns={"index": "feature"}).to_dict("records"),
            "hist": hist, "corr_labels": list(corr.columns), "corr": corr.values.tolist(),
            "class_means": {"labels": FEATURES, "non": by_class.loc[0].tolist(),
                            "dia": by_class.loc[1].tolist() if 1 in by_class.index else []}}


# ------------------------------------------------------------- training
def _estimators():
    def pipe(clf):
        return Pipeline([("imp", SimpleImputer(strategy="median")),
                         ("sc", StandardScaler()), ("clf", clf)])
    return {
        "Neural Network": pipe(MLPClassifier(hidden_layer_sizes=(32, 16), alpha=0.01,
                                             max_iter=2000, random_state=42)),
        "SVM": pipe(SVC(kernel="rbf", C=1.0, probability=True, random_state=42)),
        "Decision Tree": pipe(DecisionTreeClassifier(max_depth=4, min_samples_leaf=10, random_state=42)),
        "Logistic Regression": pipe(LogisticRegression(max_iter=1000, random_state=42)),
    }


def train_all(df, out_dir, run_tag=None):
    """70/30 stratified split, train the four models, evaluate, persist each to disk.
    Returns a dict with per-model metrics and the best model name."""
    os.makedirs(out_dir, exist_ok=True)
    X, y = df[FEATURES], df[TARGET]
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.30, random_state=42, stratify=y)
    medians = X_tr.median().to_dict()
    tag = run_tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    results = []
    for name, est in _estimators().items():
        est.fit(X_tr, y_tr)
        pred = est.predict(X_te)
        m = {"algo": name,
             "accuracy": float(accuracy_score(y_te, pred)),
             "precision": float(precision_score(y_te, pred, zero_division=0)),
             "recall": float(recall_score(y_te, pred, zero_division=0)),
             "f1": float(f1_score(y_te, pred, zero_division=0)),
             "cm": confusion_matrix(y_te, pred, labels=[0, 1]).tolist()}
        m["file"] = os.path.join(out_dir, f"{tag}_{name.lower().replace(' ', '_')}.joblib")
        m["_est"] = est
        results.append(m)
    # best = highest accuracy, ties broken by F1 then recall
    best = max(results, key=lambda r: (round(r["accuracy"], 4), r["f1"], r["recall"]))
    imp = permutation_importance(best["_est"], X_te, y_te, n_repeats=15, random_state=42,
                                 scoring="accuracy")
    importance = {f: float(v) for f, v in zip(FEATURES, imp.importances_mean)}
    for m in results:
        bundle = {"pipeline": m.pop("_est"), "algo": m["algo"], "features": FEATURES,
                  "medians": medians, "metrics": {k: m[k] for k in ("accuracy", "precision", "recall", "f1")},
                  "importance": importance, "trained_at": tag}
        joblib.dump(bundle, m["file"])
    return {"models": results, "best": best["algo"], "n_train": len(X_tr), "n_test": len(X_te),
            "importance": importance}


# ----------------------------------------------------- active model I/O
_cache = {"path": None, "mtime": None, "bundle": None}


def active_path(model_dir):
    return os.path.join(model_dir, "active_model.joblib")


def deploy(model_file, model_dir):
    """Copy a trained model to active_model.joblib atomically (os.replace) so running
    requests are never interrupted - 'deploy without downtime'."""
    tmp = active_path(model_dir) + ".tmp"
    joblib.dump(joblib.load(model_file), tmp)
    os.replace(tmp, active_path(model_dir))


def load_active(model_dir):
    path = active_path(model_dir)
    if not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    if _cache["path"] != path or _cache["mtime"] != mtime:
        _cache.update(path=path, mtime=mtime, bundle=joblib.load(path))   # hot reload
    return _cache["bundle"]


# ----------------------------------------------------------- prediction
def _row(bundle, values):
    return pd.DataFrame([[np.nan if values.get(f) is None else float(values[f])
                          for f in bundle["features"]]], columns=bundle["features"], dtype=float)


def predict(bundle, values):
    """values: dict of FEATURES (None/NaN allowed for optional ones).
    Returns probability, label, confidence and a per-prediction explanation."""
    t0 = time.perf_counter()
    pipe = bundle["pipeline"]
    row = _row(bundle, values)
    p = float(pipe.predict_proba(row)[0][1])
    # Local explanation: replace each provided feature by the training median and see
    # how much the predicted risk changes (model-agnostic "occlusion" explanation).
    explanation = []
    for f in bundle["features"]:
        v = row.at[0, f]
        if pd.isna(v):
            continue
        alt = row.copy()
        alt.at[0, f] = bundle["medians"][f]
        delta = p - float(pipe.predict_proba(alt)[0][1])
        explanation.append({"feature": f, "label": LABELS[f], "value": round(float(v), 2),
                            "typical": round(float(bundle["medians"][f]), 2),
                            "impact": round(delta * 100, 1)})
    explanation.sort(key=lambda e: abs(e["impact"]), reverse=True)
    return {"probability": p, "risk_level": risk_level(p), "prediction": int(p >= 0.5),
            "confidence": round(max(p, 1 - p) * 100, 1), "explanation": explanation,
            "model": bundle["algo"], "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}
