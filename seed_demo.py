"""Optional: create demo users with back-dated assessments so dashboards/trends look populated.
Usage:  python seed_demo.py
Accounts created (password for all: Demo@12345):
    doctor@demo.com (professional), ali@demo.com, sara@demo.com (patients)"""
import random
from datetime import datetime, timedelta

from app import create_app
from app.db import TS, execute, get_db, query
from app.security import hash_password
from app.services import parse_health_form, run_prediction

app = create_app()
random.seed(7)
with app.app_context():
    def mk(name, email, role, pro=None):
        row = query("SELECT id FROM users WHERE email=?", (email,), one=True)
        if row:
            return row["id"]
        return execute("INSERT INTO users(name,email,password_hash,role,professional_id,created_at) VALUES(?,?,?,?,?,?)",
                       (name, email, hash_password("Demo@12345"), role, pro, datetime.now().strftime(TS)))
    doc = mk("Dr. Hina Malik", "doctor@demo.com", "professional")
    people = [("Ali Raza", "ali@demo.com", 52, "Male", 175, 96, 168, 142, 92, "Yes", "Current", 0.5, "Poor"),
              ("Sara Ahmed", "sara@demo.com", 34, "Female", 162, 58, 92, 112, 72, "No", "Never", 4, "Good")]
    for name, email, age, g, h, w, glu, sy, di, fam, smk, act, diet in people:
        uid = mk(name, email, "patient", doc)
        if query("SELECT 1 FROM predictions WHERE user_id=?", (uid,), one=True):
            continue
        for k in range(6):                       # six monthly readings, slowly improving for Ali
            drift = (5 - k) * (4 if email.startswith("ali") else 1)
            form = dict(age=age, gender=g, pregnancies=1 if g == "Female" else "", height=h, weight=w - k * 0.8,
                        glucose=glu - (5 - k) * 0 - k * 4 + random.randint(-3, 3) + drift * 0, systolic=sy - k, diastolic=di - k // 2,
                        family_history=fam, smoking=smk, activity=act + k * 0.3, diet=diet)
            clean, errs = parse_health_form({k_: str(v) for k_, v in form.items()})
            pid = run_prediction(clean, uid, doc)
            when = (datetime.now() - timedelta(days=30 * (5 - k))).strftime(TS)
            get_db().execute("UPDATE predictions SET created_at=? WHERE id=?", (when, pid))
            get_db().commit()
    print("Demo data ready. Login: doctor@demo.com / ali@demo.com / sara@demo.com  (Demo@12345)")
