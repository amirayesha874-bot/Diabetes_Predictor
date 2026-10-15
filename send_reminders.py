"""Cron/Task-Scheduler entry point for monthly reassessment reminders."""
from app import create_app
from app.admin import send_monthly_reminders

app = create_app()
with app.app_context():
    print(send_monthly_reminders(), "reminder(s) queued")
