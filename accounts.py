"""Accounts for people who create forms.

Passwords are stored only as scrypt hashes. The session signing key lives in
`.langdon/secret` and is created on first run.
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from writing.session import REPO_DIR

_EMAIL = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+")
_ACCOUNT_ID = re.compile(r"[0-9a-f]{32}")
_DUMMY_HASH = generate_password_hash("langdon-forms-dummy-password")


class AccountError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def load_secret(root: Path) -> str:
    path = root / REPO_DIR / "secret"
    if path.is_file():
        stored = path.read_text(encoding="utf-8").strip()
        if len(stored) >= 32:
            return stored
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = secrets.token_hex(32)
    try:
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        stored = path.read_text(encoding="utf-8").strip()
        if len(stored) < 32:
            raise AccountError("could not start sessions", 500)
        return stored
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        file.write(stored + "\n")
    return stored


def create_account(root: Path, name, email, password, kind="individual", contact="") -> dict:
    cleaned_kind = _kind(kind)
    cleaned_email = _email(email)
    cleaned_password = _password(password)
    if cleaned_kind == "organization":
        cleaned_name = _label(name, "Enter the organization name.")
        cleaned_contact = _label(contact, "Enter your name.")
    else:
        cleaned_name = _label(name, "Enter your name.")
        cleaned_contact = ""
    account_id = secrets.token_hex(16)
    record = (
        account_id,
        cleaned_email,
        cleaned_name,
        generate_password_hash(cleaned_password),
        int(time.time() * 1000),
        cleaned_kind,
        cleaned_contact,
    )
    try:
        with _connect(root) as connection:
            connection.execute(
                """
                INSERT INTO accounts (id, email, name, password_hash, created_unix_ms, kind, contact)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                record,
            )
    except sqlite3.IntegrityError:
        raise AccountError("An account with that email already exists.", 409) from None
    return _public(account_id, cleaned_email, cleaned_name, cleaned_kind)


def authenticate(root: Path, email, password) -> dict | None:
    candidate = password if isinstance(password, str) and len(password) <= 128 else "invalid"
    try:
        cleaned = _email(email)
    except AccountError:
        check_password_hash(_DUMMY_HASH, candidate)
        return None
    with _connect(root) as connection:
        row = connection.execute(
            "SELECT id, email, name, kind, password_hash FROM accounts WHERE email = ?",
            (cleaned,),
        ).fetchone()
    if row is None or not check_password_hash(row["password_hash"], candidate):
        if row is None:
            check_password_hash(_DUMMY_HASH, candidate)
        return None
    return _public(row["id"], row["email"], row["name"], row["kind"])


def get_account(root: Path, account_id: str) -> dict | None:
    if not isinstance(account_id, str) or not _ACCOUNT_ID.fullmatch(account_id):
        return None
    with _connect(root) as connection:
        row = connection.execute(
            "SELECT id, email, name, kind, contact, created_unix_ms FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
    if row is None:
        return None
    account = _public(row["id"], row["email"], row["name"], row["kind"])
    account["contact"] = row["contact"] or ""
    account["created_unix_ms"] = row["created_unix_ms"] or 0
    return account


@contextmanager
def _connect(root: Path):
    repo = root / REPO_DIR
    repo.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(repo / "accounts.sqlite", timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_unix_ms INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'individual',
            contact TEXT NOT NULL DEFAULT ''
        )
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(accounts)")}
    if "kind" not in columns:
        connection.execute("ALTER TABLE accounts ADD COLUMN kind TEXT NOT NULL DEFAULT 'individual'")
    if "contact" not in columns:
        connection.execute("ALTER TABLE accounts ADD COLUMN contact TEXT NOT NULL DEFAULT ''")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _email(value) -> str:
    if not isinstance(value, str):
        raise AccountError("Enter an email address.")
    email = value.strip().lower()
    if not _EMAIL.fullmatch(email):
        raise AccountError("Enter a valid email address.")
    return email


def _label(value, message: str) -> str:
    if not isinstance(value, str):
        raise AccountError(message)
    name = " ".join(value.split())
    if not name or len(name) > 80 or any(ord(ch) < 32 for ch in name):
        raise AccountError(message if len(name) == 0 else "Use 80 characters or fewer.")
    return name


def _kind(value) -> str:
    if value in (None, "", "individual"):
        return "individual"
    if value == "organization":
        return "organization"
    raise AccountError("Choose an individual or an organization.")


def _public(account_id: str, email: str, name: str, kind: str) -> dict:
    return {
        "id": account_id,
        "name": name,
        "email": email,
        "kind": kind if kind in ("individual", "organization") else "individual",
    }


def _password(value) -> str:
    if not isinstance(value, str) or not (10 <= len(value) <= 128):
        raise AccountError("Use a password of at least 10 characters.")
    return value
