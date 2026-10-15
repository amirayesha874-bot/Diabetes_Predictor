"""E-mail notifications. Every message is stored in the 'outbox' table; if SMTP is
configured it is also sent. Without SMTP (e.g. local demo) the outbox is the record."""
import smtplib
import threading
from email.message import EmailMessage

from flask import current_app

from .db import execute, now


def send_mail(to, subject, body):
    mail_id = execute("INSERT INTO outbox(to_email,subject,body,sent,created_at) VALUES(?,?,?,0,?)",
                      (to, subject, body, now()))
    cfg = current_app.config
    if not cfg["SMTP_HOST"] or cfg.get("TESTING"):
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["MAIL_FROM"], to, subject
    msg.set_content(body)
    app = current_app._get_current_object()

    def _go():
        try:
            with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as s:
                s.starttls()
                if cfg["SMTP_USER"]:
                    s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
                s.send_message(msg)
            with app.app_context():
                execute("UPDATE outbox SET sent=1 WHERE id=?", (mail_id,))
        except Exception as exc:                       # never break the request
            app.logger.warning("SMTP send failed: %s", exc)
    threading.Thread(target=_go, daemon=True).start()
    return True
