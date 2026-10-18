"""Autentificare, roluri, criptare la rest, jurnal de audit si retentie."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import db
from .config import settings

ROLES = ["viewer", "analyst", "reviewer", "admin"]
ROLE_RANK = {r: i for i, r in enumerate(ROLES)}


# --------------------------------------------------------------------------- criptare
_fernet: Fernet | None = None


def fernet() -> Fernet:
    """Cheia vine din HCA_ENCRYPTION_KEY; altfel se genereaza o cheie locala (0600).

    In productie cheia trebuie furnizata dintr-un secret manager, nu stocata
    langa date.
    """
    global _fernet
    if _fernet is None:
        key = settings.encryption_key
        if not key:
            kf = settings.data_dir / ".encryption.key"
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            if not kf.exists():
                kf.write_bytes(Fernet.generate_key())
                os.chmod(kf, 0o600)
            key = kf.read_bytes().decode()
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt_bytes(data: bytes) -> bytes:
    return fernet().encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    return fernet().decrypt(data)


def encrypt_text(text: str) -> str:
    return fernet().encrypt(text.encode("utf-8")).decode()


def decrypt_text(token: str) -> str:
    try:
        return fernet().decrypt(token.encode()).decode("utf-8")
    except (InvalidToken, ValueError):
        return "[nu poate fi decriptat]"


# --------------------------------------------------------------------------- utilizatori
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load_users() -> dict:
    p = Path(settings.users_file)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def authenticate(token: str | None) -> dict | None:
    if settings.auth_disabled:
        return {"username": "local-dev", "role": "admin"}
    if not token:
        return None
    th = hash_token(token)
    for username, u in load_users().items():
        if hmac.compare_digest(u.get("token_sha256", ""), th):
            return {"username": username, "role": u.get("role", "viewer")}
    return None


def has_role(user: dict, required: str) -> bool:
    return ROLE_RANK.get(user.get("role", "viewer"), 0) >= ROLE_RANK[required]


def create_user(username: str, role: str) -> str:
    """Creeaza un utilizator si intoarce tokenul in clar (afisat o singura data)."""
    assert role in ROLES
    token = secrets.token_urlsafe(32)
    p = Path(settings.users_file)
    users = load_users()
    users[username] = {"role": role, "token_sha256": hash_token(token)}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(users, indent=2), encoding="utf-8")
    os.chmod(p, 0o600)
    return token


# --------------------------------------------------------------------------- audit
def audit(user: dict | None, action: str, target: str = "", detail: dict | None = None) -> None:
    u = user or {"username": "system", "role": "system"}
    with db.tx() as c:
        c.execute(
            "INSERT INTO audit_log(ts, username, role, action, target, detail_json) VALUES (?,?,?,?,?,?)",
            (db.now(), u["username"], u["role"], action, target, db.dumps(detail or {})),
        )


# --------------------------------------------------------------------------- retentie
def apply_retention(days: int | None = None) -> dict:
    """Sterge analizele (inclusiv textul incidentului) mai vechi de N zile.

    Feedback-ul validat si jurnalul de audit nu sunt sterse automat: sunt
    inregistrari de control. Jurnalul nu contine textul incidentului.
    """
    days = settings.retention_days if days is None else days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with db.tx() as c:
        n = c.execute("DELETE FROM analyses WHERE created_at < ?", (cutoff,)).rowcount
    audit(None, "retention.purge", "analyses", {"cutoff": cutoff, "deleted": n})
    return {"cutoff": cutoff, "deleted": n}
