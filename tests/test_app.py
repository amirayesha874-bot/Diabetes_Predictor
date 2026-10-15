"""End-to-end tests (run:  python -m unittest discover tests -v).
Maps to Design-document test cases TC-01 ... TC-08 plus security/role checks."""
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import create_app  # noqa: E402

GOOD = dict(age="45", gender="Female", pregnancies="2", height="165", weight="82", glucose="148",
            systolic="135", diastolic="88", family_history="Yes", smoking="Never", activity="1",
            diet="Poor")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(cls.tmp, "models"))
        cls.app = create_app({"TESTING": True, "AUTO_BACKUP": False,
                              "DATABASE": os.path.join(cls.tmp, "t.db"),
                              "MODEL_DIR": os.path.join(cls.tmp, "models"),
                              "UPLOAD_DIR": os.path.join(cls.tmp, "up"),
                              "BACKUP_DIR": os.path.join(cls.tmp, "bk")})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def client(self):
        return self.app.test_client()

    def tok(self, c):
        c.get("/login")
        with c.session_transaction() as s:
            return s["_csrf"]

    def post(self, c, url, data=None, **kw):
        d = dict(data or {})
        d["csrf_token"] = self.tok(c)
        return c.post(url, data=d, **kw)

    def register(self, c, email, role="patient", pw="Passw0rdX"):
        return self.post(c, "/register", dict(name="Test " + role, email=email, password=pw,
                                              confirm=pw, role=role))

    def login(self, c, email, pw="Passw0rdX"):
        return self.post(c, "/login", dict(email=email, password=pw), follow_redirects=True)


class TestAuth(Base):
    def test_tc01_login_success_and_register(self):
        c = self.client()
        r = self.register(c, "a1@x.com")
        self.assertEqual(r.status_code, 302)
        r = self.login(c, "a1@x.com")
        self.assertIn(b"New assessment", r.data)

    def test_tc02_invalid_login(self):
        c = self.client()
        self.register(c, "a2@x.com")
        r = self.login(c, "a2@x.com", "wrong")
        self.assertEqual(r.status_code, 401)
        self.assertIn(b"Invalid email or password", r.data)
        r = self.post(c, "/login", dict(email="", password=""))
        self.assertEqual(r.status_code, 401)

    def test_lockout_after_5_failures(self):
        c = self.client()
        self.register(c, "lock@x.com")
        for _ in range(5):
            self.login(c, "lock@x.com", "bad")
        r = self.login(c, "lock@x.com", "Passw0rdX")            # correct pw but locked
        self.assertIn(b"temporarily locked", r.data)

    def test_weak_password_and_duplicate(self):
        c = self.client()
        r = self.register(c, "w@x.com", pw="short")
        self.assertEqual(r.status_code, 400)
        self.register(c, "dup@x.com")
        r = self.register(c, "dup@x.com")
        self.assertEqual(r.status_code, 302)

    def test_csrf_enforced(self):
        c = self.client()
        r = c.post("/login", data=dict(email="a@b.c", password="x"))
        self.assertEqual(r.status_code, 400)

    def test_dashboard_requires_login(self):
        r = self.client().get("/dashboard")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_password_hash_is_bcrypt(self):
        self.register(self.client(), "h@x.com")
        with self.app.app_context():
            from app.db import query
            h = query("SELECT password_hash FROM users WHERE email='h@x.com'", one=True)[0]
        self.assertTrue(h.startswith("$2"))

    def test_password_reset_flow(self):
        c = self.client()
        self.register(c, "rs@x.com")
        r = self.post(c, "/forgot", dict(email="rs@x.com"))
        import re
        link = re.search(rb"/reset/[A-Za-z0-9_\-]+", r.data).group(0).decode()
        r = self.post(c, link, dict(password="NewPass123", confirm="NewPass123"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(b"New assessment", self.login(c, "rs@x.com", "NewPass123").data)


class TestPatientFlow(Base):
    def setUp(self):
        self.c = self.client()
        self.email = f"p{id(self)}@x.com"
        self.register(self.c, self.email)
        self.login(self.c, self.email)

    def test_tc03_tc04_predict_store_and_view(self):
        r = self.post(self.c, "/predict", GOOD)
        self.assertEqual(r.status_code, 302)
        res = self.c.get(r.headers["Location"])
        self.assertIn(b"Estimated diabetes risk", res.data)
        self.assertTrue(any(k in res.data for k in (b"Low Risk", b"Moderate Risk", b"High Risk")))
        self.assertIn(b"recommendations", res.data.lower())
        self.assertIn(b"typical", res.data)                      # explanation shown

    def test_validation_rejects_bad_input(self):
        bad = dict(GOOD, age="5", glucose="9999")
        r = self.post(self.c, "/predict", bad)
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"must be between", r.data)
        r = self.post(self.c, "/predict", dict(GOOD, glucose=""))
        self.assertEqual(r.status_code, 400)

    def test_prediction_speed_under_2s(self):
        import time
        t = time.time()
        self.post(self.c, "/predict", GOOD)
        self.assertLess(time.time() - t, 2.0)

    def test_data_encrypted_at_rest(self):
        self.post(self.c, "/predict", GOOD)
        with self.app.app_context():
            from app.db import query
            raw = query("SELECT payload_enc FROM predictions ORDER BY id DESC LIMIT 1", one=True)[0]
        self.assertNotIn("Glucose", raw)
        self.assertTrue(raw.startswith("gAAAA"))

    def test_history_trend_export_delete(self):
        for g in ("100", "130", "170"):
            self.post(self.c, "/predict", dict(GOOD, glucose=g))
        h = self.c.get("/history")
        self.assertEqual(h.data.count(b"View</a>"), 3)
        self.assertEqual(self.c.get("/history?sort=risk&dir=asc").status_code, 200)
        for rng in ("week", "month", "year", "all"):
            j = self.c.get(f"/api/trend?range={rng}").get_json()
            self.assertEqual(len(j["scores"]), 3)
        csv = self.c.get("/export/history.csv")
        self.assertIn(b"RiskLevel", csv.data)
        pdf = self.c.get("/export/history.pdf")
        self.assertEqual(pdf.data[:4], b"%PDF")
        with self.app.app_context():
            from app.db import query
            pid = query("SELECT id FROM predictions ORDER BY id DESC LIMIT 1", one=True)[0]
        self.post(self.c, f"/history/{pid}/delete")
        self.assertEqual(len(self.c.get("/api/trend").get_json()["scores"]), 2)

    def test_tc08_feedback(self):
        r = self.post(self.c, "/feedback", dict(rating="4", comment=""))
        self.assertEqual(r.status_code, 302)
        r = self.post(self.c, "/feedback", dict(comment="", rating=""), follow_redirects=True)
        self.assertIn(b"cannot be empty", r.data)

    def test_cannot_open_other_users_prediction(self):
        self.post(self.c, "/predict", GOOD)
        with self.app.app_context():
            from app.db import query
            pid = query("SELECT id FROM predictions ORDER BY id DESC LIMIT 1", one=True)[0]
        other = self.client()
        self.register(other, f"o{id(self)}@x.com")
        self.login(other, f"o{id(self)}@x.com")
        self.assertEqual(other.get(f"/result/{pid}").status_code, 403)
        self.assertEqual(other.get("/admin/").status_code, 403)

    def test_account_deletion(self):
        r = self.post(self.c, "/account/delete", dict(password="Passw0rdX"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(b"Invalid email", self.login(self.c, self.email).data)


class TestProfessional(Base):
    def test_pro_panel_and_notes(self):
        pro, pat = self.client(), self.client()
        self.register(pro, "doc@x.com", role="professional")
        self.login(pro, "doc@x.com")
        self.register(pat, "pat@x.com")
        self.login(pat, "pat@x.com")
        self.post(pat, "/predict", GOOD)
        # not assigned -> forbidden
        with self.app.app_context():
            from app.db import query
            pid = query("SELECT id FROM users WHERE email='pat@x.com'", one=True)[0]
            prid = query("SELECT id FROM predictions WHERE user_id=?", (pid,), one=True)[0]
        self.assertEqual(pro.get(f"/pro/patient/{pid}").status_code, 403)
        r = self.post(pro, "/pro/add", dict(email="pat@x.com"), follow_redirects=True)
        self.assertIn(b"added to your patient list", r.data)
        self.assertEqual(pro.get(f"/pro/patient/{pid}").status_code, 200)
        self.assertEqual(pro.get(f"/api/trend?user_id={pid}").status_code, 200)
        self.post(pro, "/pro/note", dict(prediction_id=prid, note="Review diet."))
        self.assertIn(b"Review diet.", pat.get(f"/result/{prid}").data)
        self.assertIn(b"Patient", pro.get("/pro/export.csv").data)
        # professional creates prediction for patient
        r = self.post(pro, "/predict?patient_id=%d" % pid, dict(GOOD, patient_id=pid))
        self.assertEqual(r.status_code, 302)
        # patient cannot use professional panel
        self.assertEqual(pat.get("/pro/").status_code, 403)


class TestAdmin(Base):
    def admin(self):
        c = self.client()
        self.login(c, self.app.config["ADMIN_EMAIL"], self.app.config["ADMIN_PASSWORD"])
        return c

    def test_pages_load(self):
        c = self.admin()
        for u in ("/admin/", "/admin/users", "/admin/logs", "/admin/datasets", "/admin/eda",
                  "/admin/models", "/admin/backups", "/admin/feedback", "/admin/outbox"):
            self.assertEqual(c.get(u).status_code, 200, u)
        self.assertEqual(c.get("/admin/models/1/report.pdf").data[:4], b"%PDF")

    def test_activate_deactivate(self):
        c = self.admin()
        v = self.client()
        self.register(v, "victim@x.com")
        with self.app.app_context():
            from app.db import query
            uid = query("SELECT id FROM users WHERE email='victim@x.com'", one=True)[0]
        self.post(c, f"/admin/users/{uid}/toggle")
        r = self.login(v, "victim@x.com")
        self.assertIn(b"deactivated", r.data)
        self.post(c, f"/admin/users/{uid}/toggle")
        self.assertIn(b"New assessment", self.login(v, "victim@x.com").data)

    def test_tc05_upload_train_deploy(self):
        c = self.admin()
        csv = open(self.app.config["DEFAULT_DATASET"], "rb").read()
        r = self.post(c, "/admin/datasets", dict(file=(io.BytesIO(csv), "new.csv")),
                      content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"uploaded and validated", r.data)
        bad = self.post(c, "/admin/datasets", dict(file=(io.BytesIO(b"a,b\n1,2\n"), "bad.csv")),
                        content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"Invalid dataset", bad.data)
        with self.app.app_context():
            from app.db import query
            did = query("SELECT MAX(id) FROM datasets", one=True)[0]
        r = self.post(c, f"/admin/datasets/{did}/train", dict(feedback="1"), follow_redirects=True)
        self.assertIn(b"Training complete", r.data)
        self.assertIn(b"Confusion matrices", r.data)
        with self.app.app_context():
            from app.db import query
            self.assertEqual(query("SELECT COUNT(*) FROM models WHERE is_active=1", one=True)[0], 1)
        # new model works for predictions without restart
        u = self.client()
        self.register(u, "after@x.com")
        self.login(u, "after@x.com")
        self.assertEqual(self.post(u, "/predict", GOOD).status_code, 302)

    def test_backup_restore_and_reminders(self):
        c = self.admin()
        self.post(c, "/admin/backups", dict(action="create"))
        name = os.listdir(self.app.config["BACKUP_DIR"])[0]
        r = self.post(c, "/admin/backups", dict(action="restore", name=name), follow_redirects=True)
        self.assertIn(b"restored", r.data)
        c = self.admin()
        self.assertEqual(self.post(c, "/admin/reminders").status_code, 302)

    def test_feedback_improves_model(self):
        p = self.client()
        self.register(p, "fb@x.com")
        self.login(p, "fb@x.com")
        r = self.post(p, "/predict", GOOD)
        pid = int(r.headers["Location"].rsplit("/", 1)[1])
        self.post(p, "/feedback", dict(prediction_id=pid, actual_outcome="1", rating="5", comment="ok"))
        with self.app.app_context():
            from app.services import feedback_rows
            df, ids = feedback_rows()
            self.assertGreaterEqual(len(ids), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
