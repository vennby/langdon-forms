"""Langdon Forms, a web app for forms and the writing behind each answer."""

from __future__ import annotations

import json
import re
import secrets
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, current_app, g, jsonify, redirect, render_template, request, session
from writing.codec import LangdonError, parse_event
from writing.session import init_repo, open_session
from writing.viz import publish

from accounts import AccountError, authenticate, create_account, get_account, load_secret
from manual import load_pages
from forms import (
    begin_response,
    create_form,
    delete_form,
    form_owner,
    get_form,
    list_forms,
    list_responses,
    save_form,
    session_for_token,
    submit_response,
)

_FORM_ID = re.compile(r"[0-9a-f]{16}")
_MUTATIONS = frozenset({"POST", "PUT", "DELETE"})
_AUTH_LIMIT = 8
_AUTH_WINDOW_S = 600


class Desk:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()
        self.tokens: dict[str, str] = {}
        self.attempts = AttemptLimiter(_AUTH_LIMIT, _AUTH_WINDOW_S)


class AttemptLimiter:
    """Counts recent failures so a password cannot be guessed in a tight loop."""

    def __init__(self, limit: int, window_s: float):
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [stamp for stamp in self._hits.get(key, ()) if now - stamp < self.window_s]
            if len(recent) >= self.limit:
                self._hits[key] = recent
                return False
            recent.append(now)
            self._hits[key] = recent
            return True

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def create_app(root: Path | None = None) -> Flask:
    root = root or init_repo()
    app = Flask(__name__)
    app.secret_key = load_secret(root)
    app.config["MAX_CONTENT_LENGTH"] = 8_000_000
    app.config["SESSION_COOKIE_NAME"] = "langdon_forms"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # The app is served over http on 127.0.0.1. A Secure cookie would never be stored.
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)
    desk = Desk(root)
    app.extensions["desk"] = desk

    @app.before_request
    def _prepare():
        g.csp_nonce = secrets.token_urlsafe(16)
        if request.method in _MUTATIONS and request.path != "/api/events":
            if not _csrf_ok():
                return _plain("Refresh the page and try again.", 403)
        return None

    @app.after_request
    def _headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = _csp(request.path, g.get("csp_nonce", ""))
        return response

    @app.errorhandler(413)
    def _too_large(_exc):
        return _plain("too large", 413)

    def _shown():
        account = _account()
        if account is None:
            return None
        return {
            "name": account["name"],
            "email": account["email"],
            "kind": account.get("kind", "individual"),
        }

    def _render(name, **extra):
        return render_template(
            name,
            nonce=g.csp_nonce,
            csrf=_ensure_csrf(),
            account=_shown(),
            **extra,
        )

    def _next_path() -> str:
        nxt = request.args.get("next") or "/forms"
        if (
            not isinstance(nxt, str)
            or not nxt.startswith("/")
            or nxt.startswith("//")
            or nxt.startswith("/signin")
            or nxt.startswith("/signup")
        ):
            return "/forms"
        return nxt

    def _guard_form(form_id: str, suffix: str):
        if not _FORM_ID.fullmatch(form_id):
            return _render("notice.html", heading="Langdon Forms", message="This form is not available.")
        account = _account()
        if account is None:
            return redirect(f"/signin?next=/f/{form_id}{suffix}")
        owner = form_owner(desk.root, form_id)
        if owner is None:
            return _render("notice.html", heading="Langdon Forms", message="This form is not available.")
        if not _same(owner, account["id"]):
            return _render("notice.html", heading="Langdon Forms", message="This form belongs to another account.")
        return None

    @app.get("/")
    def landing_page():
        return _render("landing.html")

    @app.get("/docs")
    def docs_page():
        return _render("docs.html", pages=load_pages())

    @app.get("/signin")
    def signin_page():
        if _account() is not None:
            return redirect(_next_path())
        return _render("signin.html", next_path=_next_path())

    @app.get("/signup")
    def signup_page():
        if _account() is not None:
            return redirect("/forms")
        return _render("signup.html")

    @app.get("/profile")
    def profile_page():
        account = _account()
        if account is None:
            return redirect("/signin?next=/profile")
        stamp = account.get("created_unix_ms") or 0
        joined = datetime.fromtimestamp(stamp / 1000).strftime("%B %d, %Y") if stamp else ""
        return _render(
            "profile.html",
            profile={
                "name": account["name"],
                "email": account["email"],
                "kind": account.get("kind", "individual"),
                "contact": account.get("contact") or "",
                "joined": joined,
            },
        )

    @app.get("/forms")
    def forms_page():
        account = _account()
        if account is None:
            return redirect("/signin?next=/forms")
        return _render("desk.html", forms=list_forms(desk.root, account["id"]))

    @app.get("/f/<form_id>")
    def fill_page(form_id):
        account = _account()
        owner_id = form_owner(desk.root, form_id)
        owner = bool(account and owner_id and _same(owner_id, account["id"]))
        return _render("fill.html", form_id=form_id, owner=owner)

    @app.get("/f/<form_id>/build")
    def build_page(form_id):
        blocked = _guard_form(form_id, "/build")
        if blocked is not None:
            return blocked
        try:
            form = get_form(desk.root, form_id)
        except LangdonError as exc:
            return _render("notice.html", heading="Langdon Forms", message=str(exc))
        return _render("build.html", form_id=form_id, form=form)

    @app.get("/f/<form_id>/answers")
    def answers_page(form_id):
        blocked = _guard_form(form_id, "/answers")
        if blocked is not None:
            return blocked
        return _render("answers.html", form_id=form_id)

    @app.get("/api/docs")
    def docs_pages():
        return jsonify({"pages": load_pages()})

    @app.get("/api/session")
    def session_read():
        return jsonify(_session_payload(_account()))

    @app.post("/api/session")
    def session_login():
        payload, error = _payload()
        if error is not None:
            return error
        if not isinstance(payload, dict):
            return _plain("Email or password is wrong.", 401)
        key = _attempt_key("login")
        if not desk.attempts.allow(key):
            return _plain("Too many attempts. Wait a few minutes and try again.", 429)
        account = authenticate(desk.root, payload.get("email"), payload.get("password"))
        if account is None:
            return _plain("Email or password is wrong.", 401)
        desk.attempts.clear(key)
        _start_session(account["id"])
        return jsonify(_session_payload(account))

    @app.delete("/api/session")
    def session_logout():
        session.clear()
        return jsonify({"ok": True})

    @app.post("/api/accounts")
    def accounts_create():
        payload, error = _payload()
        if error is not None:
            return error
        if not isinstance(payload, dict):
            return _plain("Enter your name, email, and password.", 400)
        key = _attempt_key("signup")
        if not desk.attempts.allow(key):
            return _plain("Too many attempts. Wait a few minutes and try again.", 429)
        try:
            account = create_account(
                desk.root,
                payload.get("name"),
                payload.get("email"),
                payload.get("password"),
                payload.get("kind"),
                payload.get("contact"),
            )
        except AccountError as exc:
            return _plain(str(exc), exc.status)
        _start_session(account["id"])
        return jsonify(_session_payload(account))

    @app.get("/api/forms")
    def forms_list():
        account, error = _require_account()
        if error is not None:
            return error
        return jsonify(list_forms(desk.root, account["id"]))

    @app.post("/api/forms")
    def forms_create():
        account, error = _require_account()
        if error is not None:
            return error
        payload, error = _payload()
        if error is not None:
            return error
        title = payload.get("title") if isinstance(payload, dict) else None
        try:
            with desk.lock:
                created = create_form(desk.root, title or "Untitled form", account["id"])
            return jsonify(created)
        except LangdonError as exc:
            return _fail(exc)
        except OSError:
            return _plain("could not save the form", 500)

    @app.get("/api/forms/<form_id>")
    def form_get(form_id: str):
        try:
            form = get_form(desk.root, form_id)
        except LangdonError as exc:
            return _fail(exc)
        account = _account()
        form["owner"] = bool(account and _same(form_owner(desk.root, form_id), account["id"]))
        return jsonify(form)

    @app.put("/api/forms/<form_id>")
    def form_save(form_id: str):
        account, error = _require_account()
        if error is not None:
            return error
        payload, error = _payload()
        if error is not None:
            return error
        try:
            with desk.lock:
                saved = save_form(desk.root, form_id, payload if isinstance(payload, dict) else {}, account["id"])
            return jsonify(saved)
        except LangdonError as exc:
            return _fail(exc)
        except OSError:
            return _plain("could not save the form", 500)

    @app.delete("/api/forms/<form_id>")
    def form_delete(form_id: str):
        account, error = _require_account()
        if error is not None:
            return error
        try:
            with desk.lock:
                delete_form(desk.root, form_id, account["id"])
            return jsonify({"ok": True})
        except LangdonError as exc:
            return _fail(exc)

    @app.get("/api/forms/<form_id>/responses")
    def responses_list(form_id: str):
        account, error = _require_account()
        if error is not None:
            return error
        try:
            form = get_form(desk.root, form_id)
            form["owner"] = True
            return jsonify({"form": form, "responses": list_responses(desk.root, form_id, account["id"])})
        except LangdonError as exc:
            return _fail(exc)

    @app.post("/api/forms/<form_id>/responses")
    def responses_begin(form_id: str):
        _payload_error = _payload()[1]
        if _payload_error is not None:
            return _payload_error
        try:
            with desk.lock:
                draft = begin_response(desk.root, form_id)
            return jsonify(draft)
        except LangdonError as exc:
            return _fail(exc)
        except OSError:
            return _plain("could not save the answer", 500)

    @app.post("/api/forms/<form_id>/responses/<response_id>/submit")
    def responses_submit(form_id: str, response_id: str):
        payload, error = _payload()
        if error is not None:
            return error
        answers = payload.get("answers") if isinstance(payload, dict) else None
        try:
            with desk.lock:
                saved = submit_response(desk.root, form_id, response_id, answers)
            return jsonify(saved)
        except LangdonError as exc:
            return _fail(exc)
        except OSError:
            return _plain("could not save the answer", 500)

    @app.post("/api/events")
    def events():
        # The writing token authorizes this call. pagehide uses sendBeacon, which
        # cannot set the CSRF header the cookie-backed routes require.
        payload, error = _payload()
        if error is not None:
            return error
        if not isinstance(payload, dict):
            return _plain("bad json", 400)
        token = payload.get("token")
        raw_events = payload.get("events")
        if not isinstance(raw_events, list) or len(raw_events) > 500:
            return _plain("send at most 500 events at a time", 400)
        with desk.lock:
            session_id = desk.tokens.get(token) if isinstance(token, str) else None
            writing = _open_known(desk.root, session_id)
            if writing is None:
                writing = session_for_token(desk.root, token)
                if writing is not None and isinstance(token, str):
                    desk.tokens[token] = writing.id
            if writing is None or not _same(token, writing.meta.get("token")):
                return _plain("bad token", 403)
            if writing.sealed:
                return _plain("session is sealed", 400)
            try:
                stored = [parse_event(item) for item in raw_events]
                writing.append(stored)
                total = int(writing.meta["event_count"])
            except LangdonError as exc:
                return _plain(str(exc), 400)
            except OSError:
                return _plain("could not save the writing", 500)
        return jsonify({"stored": len(stored), "total": total})

    @app.get("/report/<session_id>")
    def report(session_id: str):
        account, error = _require_account()
        if error is not None:
            return error
        writing = _open_known(desk.root, session_id)
        if writing is None or not _same(form_owner(desk.root, writing.meta.get("form_id")), account["id"]):
            return _plain("not found", 404)
        if not writing.sealed:
            return _plain("not sealed yet", 404)
        path = writing.directory / "report.html"
        if not path.is_file():
            try:
                publish(writing)
            except OSError:
                return _plain("could not save the answer", 500)
        return path.read_bytes(), 200, {"Content-Type": "text/html; charset=utf-8"}

    return app


def _account() -> dict | None:
    user_id = session.get("user")
    if not isinstance(user_id, str):
        return None
    return get_account(current_app.extensions["desk"].root, user_id)


def _require_account():
    account = _account()
    if account is None:
        return None, _plain("Sign in.", 401)
    return account, None


def _session_payload(account: dict | None) -> dict:
    shown = None
    if account is not None:
        shown = {"name": account["name"], "email": account["email"], "kind": account.get("kind", "individual")}
    return {"account": shown, "csrf": _ensure_csrf()}


def _start_session(account_id: str) -> None:
    session.clear()
    session.permanent = True
    session["user"] = account_id
    session["csrf"] = secrets.token_urlsafe(32)


def _ensure_csrf() -> str:
    token = session.get("csrf")
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def _csrf_ok() -> bool:
    return _same(session.get("csrf"), request.headers.get("X-CSRF-Token", ""))


def _attempt_key(action: str) -> str:
    return f"{action}:{request.remote_addr or 'local'}"


def _open_known(root: Path, session_id) -> object | None:
    if not isinstance(session_id, str):
        return None
    try:
        return open_session(root, session_id)
    except LangdonError:
        return None


def _payload():
    raw = request.get_data() or b"{}"
    try:
        return json.loads(raw.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _plain("bad json", 400)


def _plain(message: str, status: int):
    return message, status, {"Content-Type": "text/plain; charset=utf-8"}


def _fail(exc: LangdonError):
    text = str(exc)
    missing = text in {"This form is not available.", "This response is not available.", "not found"}
    return _plain(text, 404 if missing else 400)


def _same(left, right) -> bool:
    if not isinstance(left, str) or not isinstance(right, str) or len(left) != len(right) or not left:
        return False
    return secrets.compare_digest(left, right)


def _csp(path: str, nonce: str) -> str:
    if path.startswith("/report/"):
        return (
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )
    return (
        "default-src 'none'; "
        f"script-src 'self' 'nonce-{nonce}'; style-src 'self' 'nonce-{nonce}'; "
        "connect-src 'self'; "
        "media-src 'self'; "
        "frame-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'none'; form-action 'self'; object-src 'none'"
    )


def main() -> int:
    app = create_app()
    url = "http://127.0.0.1:5000/"
    print(f"Langdon Forms  {url}")
    print("Press Ctrl+C to stop.")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    app.run(host="127.0.0.1", port=5000, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
