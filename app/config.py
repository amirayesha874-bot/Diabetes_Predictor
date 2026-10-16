"""Central configuration. Secrets/credentials come from environment variables or a
local ".env" file (never hard-coded) - see .env.example."""
import os
import secrets

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_dotenv():
    path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def _persistent_secret(filename):
    """Generate a random key once and keep it in instance/ so it survives restarts."""
    inst = os.path.join(BASE_DIR, "instance")
    os.makedirs(inst, exist_ok=True)
    path = os.path.join(inst, filename)
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(secrets.token_urlsafe(48))
    with open(path) as fh:
        return fh.read().strip()



def _configured_secret(env_name, filename):
    """Read secrets from environment on Vercel; use local files otherwise."""
    value = os.environ.get(env_name)
    if value:
        return value

    if os.environ.get("VERCEL") == "1":
        raise RuntimeError(
            f"Missing required Vercel environment variable: {env_name}"
        )

    return _persistent_secret(filename)
Ab class Config: ke andar yeh do lines dhoondein:

python
SECRET_KEY = os.environ.get("SECRET_KEY") or _persistent_secret("secret.key")
Aur:

python
DATA_KEY = os.environ.get("DATA_KEY") or _persistent_secret("data.key")
In dono ko replace karein:

python
SECRET_KEY = _configured_secret("SECRET_KEY", "secret.key")
python
DATA_KEY = _configured_secret("DATA_KEY", "data.key")







class Config:
    BASE_DIR = BASE_DIR
    SECRET_KEY = os.environ.get("SECRET_KEY") or _persistent_secret("secret.key")
    DATABASE = os.environ.get("DATABASE") or os.path.join(BASE_DIR, "instance", "diabetes.db")
    MODEL_DIR = os.path.join(BASE_DIR, "models")
    UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
    BACKUP_DIR = os.path.join(BASE_DIR, "backups")
    DEFAULT_DATASET = os.path.join(BASE_DIR, "data", "diabetic_data.csv")

    # Security (SRS 3.x)
    SESSION_TIMEOUT_MIN = 15          # inactivity timeout
    MAX_FAILED_LOGINS = 5
    LOCK_MINUTES = 10
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("HTTPS", "0") == "1"
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB upload cap
    DATA_KEY = os.environ.get("DATA_KEY") or _persistent_secret("data.key")

    # Mail
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    MAIL_FROM = os.environ.get("MAIL_FROM", "no-reply@diabetes-risk.local")

    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@diabetes.local")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@12345")

    AUTO_BACKUP = True
    TESTING = False
