"""Registration, login/logout, password recovery, profile, account deletion."""
import hashlib
import secrets
from datetime import datetime, timedelta

from flask import (Blueprint, current_app, flash, g, redirect, render_template, request,
                   session, url_for)

from .db import TS, execute, log_activity, now, query
from .mailer import send_mail
from .security import (EMAIL_RE, check_password, hash_password, login_required,
                       password_problem)

bp = Blueprint("auth", __name__)


def _home():
    return redirect(url_for("main.dashboard"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return _home()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw, pw2 = request.form.get("password", ""), request.form.get("confirm", "")
        role = request.form.get("role", "patient")
        err = None
        if len(name) < 2:
            err = "Please enter your full name."
        elif not EMAIL_RE.match(email):
            err = "Enter a valid email address."
        elif role not in ("patient", "professional"):
            err = "Invalid role selected."
        elif pw != pw2:
            err = "Passwords do not match."
        else:
            err = password_problem(pw)
        if not err and query("SELECT 1 FROM users WHERE email=?", (email,), one=True):
            flash("This email is already registered - please log in instead.", "info")
            return redirect(url_for("auth.login"))
        if err:
            flash(err, "danger")
            return render_template("auth/register.html", form=request.form), 400
        uid = execute("INSERT INTO users(name,email,password_hash,role,reminder_opt_in,created_at) "
                      "VALUES(?,?,?,?,?,?)",
                      (name, email, hash_password(pw), role, int(bool(request.form.get("reminders"))), now()))
        log_activity("register", f"{role} registered", user_id=uid)
        send_mail(email, "Welcome to Diabetes Risk Predictor",
                  f"Hello {name},\n\nYour {role} account was created successfully. "
                  "You can now log in and complete your first diabetes risk assessment.\n\n"
                  "This tool supports, but does not replace, professional medical advice.")
        flash("Account created! A confirmation email has been sent. Please log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/register.html", form={})


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return _home()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        u = query("SELECT * FROM users WHERE email=?", (email,), one=True)
        generic = "Invalid email or password."
        if not email or not pw:
            flash("Enter both email and password.", "danger")
        elif u is None:
            log_activity("login_failed", f"unknown email {email}")
            flash(generic, "danger")
        else:
            if u["locked_until"] and datetime.strptime(u["locked_until"], TS) > datetime.now():
                mins = int((datetime.strptime(u["locked_until"], TS) - datetime.now()).total_seconds() // 60) + 1
                log_activity("login_blocked", "account locked", user_id=u["id"])
                flash(f"Account temporarily locked after too many failed attempts. Try again in about {mins} minute(s).", "danger")
            elif not check_password(pw, u["password_hash"]):
                n = u["failed_attempts"] + 1
                lock = None
                cfg = current_app.config
                if n >= cfg["MAX_FAILED_LOGINS"]:
                    lock = (datetime.now() + timedelta(minutes=cfg["LOCK_MINUTES"])).strftime(TS)
                    n = 0
                    log_activity("account_locked", f"locked for {cfg['LOCK_MINUTES']} min", user_id=u["id"])
                execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?", (n, lock, u["id"]))
                log_activity("login_failed", "wrong password", user_id=u["id"])
                flash(generic, "danger")
            elif not u["is_active"]:
                flash("This account has been deactivated. Contact an administrator.", "danger")
            else:
                session.clear()
                session["user_id"] = u["id"]
                execute("UPDATE users SET failed_attempts=0, locked_until=NULL, last_login=? WHERE id=?",
                        (now(), u["id"]))
                log_activity("login", "success", user_id=u["id"])
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))
        return render_template("auth/login.html"), 401
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    if g.user:
        log_activity("logout", "")
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.index"))


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    link = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        u = query("SELECT * FROM users WHERE email=? AND is_active=1", (email,), one=True)
        if u:
            token = secrets.token_urlsafe(32)
            execute("INSERT INTO password_resets(token_hash,user_id,expires_at) VALUES(?,?,?)",
                    (hashlib.sha256(token.encode()).hexdigest(), u["id"],
                     (datetime.now() + timedelta(hours=1)).strftime(TS)))
            link = url_for("auth.reset", token=token, _external=True)
            send_mail(email, "Reset your password",
                      f"Hello {u['name']},\n\nUse this link within 1 hour to reset your password:\n{link}\n\n"
                      "If you did not request this, ignore this email.")
            log_activity("password_reset_requested", "", user_id=u["id"])
        flash("If that email is registered, a reset link has been sent.", "info")
        # Local/demo mode (no SMTP configured): show the link so the flow can be tested.
        if not current_app.config["SMTP_HOST"] and u:
            return render_template("auth/forgot.html", dev_link=link)
    return render_template("auth/forgot.html", dev_link=None)


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    h = hashlib.sha256(token.encode()).hexdigest()
    row = query("SELECT * FROM password_resets WHERE token_hash=? AND used=0", (h,), one=True)
    if not row or datetime.strptime(row["expires_at"], TS) < datetime.now():
        flash("This reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.forgot"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        err = password_problem(pw) or (None if pw == request.form.get("confirm") else "Passwords do not match.")
        if err:
            flash(err, "danger")
            return render_template("auth/reset.html"), 400
        execute("UPDATE users SET password_hash=?, failed_attempts=0, locked_until=NULL WHERE id=?",
                (hash_password(pw), row["user_id"]))
        execute("UPDATE password_resets SET used=1 WHERE token_hash=?", (h,))
        log_activity("password_reset", "", user_id=row["user_id"])
        flash("Password updated. You can now log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset.html")


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    u = g.user
    if request.method == "POST":
        action = request.form.get("action")
        if action == "info":
            name = request.form.get("name", "").strip()
            if len(name) < 2:
                flash("Please enter a valid name.", "danger")
            else:
                prof = request.form.get("professional_id") or None
                if u["role"] == "patient":
                    if prof and not query("SELECT 1 FROM users WHERE id=? AND role='professional' AND is_active=1",
                                          (prof,), one=True):
                        prof = None
                else:
                    prof = None
                execute("UPDATE users SET name=?, reminder_opt_in=?, professional_id=? WHERE id=?",
                        (name, int(bool(request.form.get("reminders"))), prof, u["id"]))
                log_activity("profile_update", "")
                flash("Profile updated.", "success")
        elif action == "password":
            if not check_password(request.form.get("current", ""), u["password_hash"]):
                flash("Current password is incorrect.", "danger")
            else:
                pw = request.form.get("password", "")
                err = password_problem(pw) or (None if pw == request.form.get("confirm") else "Passwords do not match.")
                if err:
                    flash(err, "danger")
                else:
                    execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(pw), u["id"]))
                    log_activity("password_change", "")
                    flash("Password changed.", "success")
        return redirect(url_for("auth.profile"))
    pros = query("SELECT id,name FROM users WHERE role='professional' AND is_active=1 ORDER BY name")
    return render_template("auth/profile.html", pros=pros)


@bp.route("/account/delete", methods=["POST"])
@login_required
def delete_account():
    """Right to erasure: removes the account and all associated data."""
    u = g.user
    if not check_password(request.form.get("password", ""), u["password_hash"]):
        flash("Password incorrect - account not deleted.", "danger")
        return redirect(url_for("auth.profile"))
    if u["role"] == "admin" and query("SELECT COUNT(*) c FROM users WHERE role='admin'", one=True)["c"] <= 1:
        flash("You are the only administrator and cannot delete this account.", "danger")
        return redirect(url_for("auth.profile"))
    log_activity("account_deleted", f"user {u['email']} erased own account")
    execute("DELETE FROM feedback WHERE user_id=?", (u["id"],))
    execute("DELETE FROM users WHERE id=?", (u["id"],))
    session.clear()
    flash("Your account and all associated data have been permanently deleted.", "info")
    return redirect(url_for("main.index"))
