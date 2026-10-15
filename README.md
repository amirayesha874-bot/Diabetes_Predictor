# Intelligent Diabetes Risk Predictor
Group **S26PROJECT7EE89** · Supervisor: Komal Khawer · Python / Flask / scikit-learn / SQLite

A web application that predicts diabetes risk (Low / Moderate / High) with a confidence score, explains the
prediction, recommends lifestyle changes, tracks history, and lets clinicians and administrators manage patients
and models.

## 1. Setup (Windows / Linux / macOS - Anaconda or plain Python 3.10+)
```bash
conda create -n diabetes python=3.11 -y && conda activate diabetes     # (or: python -m venv venv)
pip install -r requirements.txt
python run.py          # first start creates the DB, the admin account and trains the first model (~10 s)
```
Open **http://127.0.0.1:5000**

Optional demo data (doctor + 2 patients with 6 months of history):  `python seed_demo.py`

| Role | Email | Password |
|---|---|---|
| Admin | `admin@diabetes.local` | `Admin@12345` (change via `.env`, see `.env.example`) |
| Healthcare professional (after seed) | `doctor@demo.com` | `Demo@12345` |
| Patients (after seed) | `ali@demo.com`, `sara@demo.com` | `Demo@12345` |

Run the automated tests (22 end-to-end tests covering Design-document test cases TC-01 ... TC-08):
`python -m unittest discover tests -v`

## 2. Project structure
```
run.py                  start the server            seed_demo.py       optional demo data
send_reminders.py       monthly reminder job (cron) requirements.txt    .env.example
app/
  __init__.py           app factory, security headers, session timeout
  config.py             all settings (env / .env, nothing hard-coded)
  db.py  crypto.py      SQLite layer (parameterised SQL) / Fernet encryption of health data
  security.py auth.py   bcrypt, CSRF, roles, lockout / register, login, reset, profile, delete account
  ml.py                 preprocessing, 70/30 split, NN/SVM/DT/LR training, metrics, persistence, explanations
  services.py           training registry, deploy (atomic hot-swap), form validation, prediction pipeline
  recommend.py          personalised recommendations
  views.py pro.py admin.py   patient / professional / administrator features
  reports.py            PDF + CSV export            mailer.py  backup.py
  templates/ static/    HTML (Jinja2), CSS, JS, Chart.js (vendored, works offline)
data/diabetic_data.csv  Kaggle dataset (Pima, 768 rows)
database/schema.sql     DB schema (tables are created automatically)
models/                 saved models (*.joblib); active_model.joblib is the deployed one
notebooks/              prototype notebook from the prototype phase
tests/test_app.py
```

## 3. Requirement coverage (Project_Details.docx / SRS)
| # | Requirement | Where |
|---|---|---|
| 1 | Registration, login, RBAC, password recovery, profile | `auth.py`, `security.py` |
| 2 | Health-data input form; admin dataset import | `/predict`, `/admin/datasets` |
| 3 | EDA: summary stats, distributions, correlation, class balance | `/admin/eda` |
| 4 | Cleaning (zeros -> missing, median imputation, scaling), 70/30 stratified split | `ml.py` |
| 5 | Neural Network (MLP), SVM, Decision Tree, Logistic Regression | `ml.py` |
| 6 | Accuracy, precision, recall, F1, confusion matrix | `/admin/models` |
| 7 | Model comparison and best-model selection | `ml.train_all`, `/admin/models` |
| 8 | Model persistence (joblib), no retraining per request | `models/` |
| 9 | Risk-factor analysis: permutation importance + per-prediction explanation | result page |
| 10 | Personalised recommendations | `recommend.py` |
| 11 | Longitudinal tracking (history, trend chart, week/month/year/all filter) | dashboard, history |
| 12 | Dashboards (prediction, risk level, model performance, trends) | patient / pro / admin dashboards |
| 13 | Reports PDF/CSV (user data, predictions, model metrics) | `reports.py` |
| 14 | Education pages | `/education` |
| 15 | Clinical decision support (patient list, analysis, notes) | `/pro` |
| 16 | Feedback + retraining using confirmed outcomes | `/feedback`, admin training |
| 17 | Admin: datasets, train/update models, monitoring, users, logs, backups | `/admin/*` |
| 18 | Performance: a prediction takes roughly 10-30 ms | stored in `predictions.response_ms` |
| 19 | Security: bcrypt, encrypted data at rest, parameterised SQL, CSRF, 15-min session timeout, lockout (5 tries / 10 min), privacy policy, account erasure, daily + manual backups | see code |

## 4. Honest notes / limitations (read before the viva)
* **Accuracy.** The Kaggle/Pima dataset has only 768 rows; the best achievable test accuracy is about 73-78 % (this app gets about 74.5 %).
  The SRS target of >= 85 % accuracy and >= 80 % precision/recall per class is not realistic on this dataset. Explain this in the report
  (small data, class imbalance, noisy/missing values). The app reports the real metrics and never fakes them.
* **Inputs vs. dataset.** The model is trained only on the 8 Pima features. Height/weight give BMI; family history (Yes/No) is converted to an
  estimated "pedigree function" (0.25 / 0.65); skin-fold and insulin are optional (median-imputed). Smoking, diet, activity and systolic BP are
  **not** model inputs - they are stored and used for recommendations only.
* **Population.** The dataset contains only women of Pima heritage, so results for men/other groups are less reliable (the app shows a notice).
* **Risk categories** come from the predicted probability (< 35 % low, 35-65 % moderate, >= 65 % high), not from separate labels.
* **E-mail.** Without SMTP settings in `.env`, emails are saved in *Admin -> Outbox* (the password-reset link is shown on screen in demo mode).
* **HTTPS / scaling.** For deployment use a reverse proxy with HTTPS (set `HTTPS=1`) and a production WSGI server (waitress/gunicorn).
  SQLite is fine for the demo; switch to PostgreSQL for many concurrent users.
* Screening aid only - not a medical diagnosis.
