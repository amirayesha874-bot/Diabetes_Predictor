"""Intelligent Diabetes Risk Predictor - Flask application factory."""
from datetime import timedelta

from flask import Flask, g, render_template, request, session

from . import backup, db, security, services
from .config import Config


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=app.config["SESSION_TIMEOUT_MIN"])
    for d in ("MODEL_DIR", "UPLOAD_DIR", "BACKUP_DIR"):
        import os
        os.makedirs(app.config[d], exist_ok=True)

    app.teardown_appcontext(db.close_db)

    @app.before_request
    def _before():
        session.permanent = True            # sliding 15-minute inactivity timeout
        security.load_user()
        security.csrf_protect()
        security.csrf_token()               # make sure every session has a CSRF token

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Cache-Control", "no-store" if g.get("user") else "public, max-age=60")
        return resp

    @app.context_processor
    def _ctx():
        return {"csrf_token": security.csrf_token, "current_user": g.get("user"),
                "timeout_min": app.config["SESSION_TIMEOUT_MIN"]}

    @app.template_filter("pct")
    def _pct(v):
        return f"{v:.1f}%" if v is not None else "-"

    @app.template_filter("level_class")
    def _lc(level):
        return {"Low Risk": "low", "Moderate Risk": "mod", "High Risk": "high"}.get(level, "")

    for code, title in ((400, "Bad request"), (403, "Access denied"), (404, "Page not found"),
                        (413, "File too large"), (500, "Server error")):
        app.register_error_handler(code, lambda e, c=code, t=title: (render_template("error.html", code=c, title=t,
                                   message=getattr(e, "description", "")), c))

    from .auth import bp as auth_bp
    from .views import bp as main_bp
    from .pro import bp as pro_bp
    from .admin import bp as admin_bp
    for b in (auth_bp, main_bp, pro_bp, admin_bp):
        app.register_blueprint(b)

    with app.app_context():
        # init schema in the configured DB
        db.init_db()
        services.ensure_initialized()
    if app.config["AUTO_BACKUP"] and not app.config["TESTING"]:
        backup.start_daily_scheduler(app.config["DATABASE"], app.config["BACKUP_DIR"])
    return app
