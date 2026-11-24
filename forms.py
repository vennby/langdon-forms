"""Form storage for the web app.

A form lives in `.langdon/forms/<id>/`. Each submitted answer seals its own
`.tr` trace. The response stores the text and which session holds the process.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

from writing.codec import LangdonError
from writing.features import analyze
from writing.session import REPO_DIR, Session, create_session, open_session
from writing.viz import publish

_FORM_ID = re.compile(r"[0-9a-f]{16}")
_QUESTION_ID = re.compile(r"q[0-9a-f]{8}")
_ACCOUNT_ID = re.compile(r"[0-9a-f]{32}")
_KINDS = frozenset({"short", "long"})
_FORM_MISSING = "This form is not available."
_RESPONSE_MISSING = "This response is not available."


def _now_ms() -> int:
    return int(time.time() * 1000)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    delay = 0.02
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


def _write_json(path: Path, payload: dict) -> None:
    _atomic_write(path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _forms_root(root: Path) -> Path:
    return root / REPO_DIR / "forms"


def _check_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not _FORM_ID.fullmatch(value):
        if label == "response":
            raise LangdonError(_RESPONSE_MISSING)
        raise LangdonError(_FORM_MISSING)
    return value


def _same(left, right) -> bool:
    if not isinstance(left, str) or not isinstance(right, str) or len(left) != len(right) or not left:
        return False
    return secrets.compare_digest(left, right)


def _form_dir(root: Path, form_id: str) -> Path:
    return _forms_root(root) / _check_id(form_id, "form")


def _form_path(root: Path, form_id: str) -> Path:
    return _form_dir(root, form_id) / "form.json"


def _response_path(root: Path, form_id: str, response_id: str) -> Path:
    return _form_dir(root, form_id) / "responses" / f"{_check_id(response_id, 'response')}.json"


def _new_question(prompt: str = "", kind: str = "long") -> dict:
    return {"id": "q" + secrets.token_hex(4), "prompt": prompt, "kind": kind}


def _clean_questions(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise LangdonError("questions must be a list")
    if len(raw) > 40:
        raise LangdonError("a form can have at most 40 questions")
    cleaned = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise LangdonError("a question must be an object")
        prompt = item.get("prompt")
        if not isinstance(prompt, str):
            raise LangdonError("a question needs a prompt")
        prompt = prompt.strip()
        if len(prompt) > 400:
            raise LangdonError("a question prompt must be at most 400 characters")
        kind = item.get("kind") or "long"
        if kind not in _KINDS:
            raise LangdonError("a question is short or long text")
        qid = item.get("id")
        if not isinstance(qid, str) or not _QUESTION_ID.fullmatch(qid) or qid in seen:
            qid = "q" + secrets.token_hex(4)
        seen.add(qid)
        cleaned.append({"id": qid, "prompt": prompt, "kind": kind})
    return cleaned


def _public_form(form: dict, responses: int | None = None) -> dict:
    payload = {
        "id": form["id"],
        "title": form["title"],
        "description": form.get("description") or "",
        "created_unix_ms": form.get("created_unix_ms") or 0,
        "questions": form.get("questions") or [],
    }
    if responses is not None:
        payload["responses"] = responses
    return payload


def _response_count(root: Path, form_id: str) -> int:
    directory = _form_dir(root, form_id) / "responses"
    if not directory.is_dir():
        return 0
    total = 0
    for path in directory.glob("*.json"):
        try:
            if _read_json(path).get("submitted"):
                total += 1
        except (OSError, json.JSONDecodeError):
            continue
    return total


def list_forms(root: Path, owner_id: str) -> list[dict]:
    directory = _forms_root(root)
    if not directory.is_dir():
        return []
    found = []
    for child in directory.iterdir():
        path = child / "form.json"
        if not path.is_file():
            continue
        try:
            form = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if _same(form.get("owner_id"), owner_id):
            found.append(_public_form(form, _response_count(root, form["id"])))
    found.sort(key=lambda item: int(item.get("created_unix_ms") or 0))
    return found


def get_form(root: Path, form_id: str) -> dict:
    return _public_form(_load_form(root, form_id))


def form_owner(root: Path, form_id) -> str | None:
    if not isinstance(form_id, str) or not _FORM_ID.fullmatch(form_id):
        return None
    path = _forms_root(root) / form_id / "form.json"
    if not path.is_file():
        return None
    try:
        owner = _read_json(path).get("owner_id")
    except (OSError, json.JSONDecodeError):
        return None
    return owner if isinstance(owner, str) else None


def create_form(root: Path, title: str, owner_id: str) -> dict:
    if not isinstance(owner_id, str) or not _ACCOUNT_ID.fullmatch(owner_id):
        raise LangdonError("Sign in.")
    title = _clean_title(title)
    form_id = secrets.token_hex(8)
    form = {
        "id": form_id,
        "owner_id": owner_id,
        "title": title,
        "description": "",
        "created_unix_ms": _now_ms(),
        "questions": [_new_question()],
    }
    _write_json(_form_path(root, form_id), form)
    return _public_form(form, 0)


def _load_form(root: Path, form_id: str) -> dict:
    path = _form_path(root, form_id)
    if not path.is_file():
        raise LangdonError(_FORM_MISSING)
    return _read_json(path)


def _require_owner(form: dict, owner_id: str) -> None:
    if not _same(form.get("owner_id"), owner_id):
        raise LangdonError(_FORM_MISSING)


def _clean_title(title) -> str:
    if not isinstance(title, str):
        raise LangdonError("a form needs a title")
    title = title.strip() or "Untitled form"
    if len(title) > 120:
        raise LangdonError("a form title must be at most 120 characters")
    return title


def _clean_description(description) -> str:
    if description is None:
        return ""
    if not isinstance(description, str):
        raise LangdonError("the description must be text")
    description = description.strip()
    if len(description) > 2000:
        raise LangdonError("the description must be at most 2000 characters")
    return description


def save_form(root: Path, form_id: str, payload: dict, owner_id: str) -> dict:
    if not isinstance(payload, dict):
        raise LangdonError("the form must be an object")
    current = _load_form(root, form_id)
    _require_owner(current, owner_id)
    current["title"] = _clean_title(payload.get("title", current.get("title")))
    current["description"] = _clean_description(payload.get("description", current.get("description")))
    if "questions" in payload:
        current["questions"] = _clean_questions(payload.get("questions"))
    _write_json(_form_path(root, form_id), current)
    return _public_form(current, _response_count(root, form_id))


def delete_form(root: Path, form_id: str, owner_id: str) -> None:
    directory = _form_dir(root, form_id)
    _require_owner(_load_form(root, form_id), owner_id)
    sessions = root / REPO_DIR / "sessions"
    if sessions.is_dir():
        for child in list(sessions.iterdir()):
            meta = child / "meta.json"
            if not meta.is_file():
                continue
            try:
                stored = _read_json(meta)
            except (OSError, json.JSONDecodeError):
                continue
            if stored.get("form_id") == form_id:
                shutil.rmtree(child, ignore_errors=True)
    shutil.rmtree(directory)


def begin_response(root: Path, form_id: str) -> dict:
    form = get_form(root, form_id)
    if not form["questions"]:
        raise LangdonError("Add a question before people can answer this form.")
    if any(not question["prompt"] for question in form["questions"]):
        raise LangdonError("Finish every question before people can answer this form.")
    response_id = secrets.token_hex(8)
    fields = {}
    for question in form["questions"]:
        session = create_session(root, label=f"{form['title']} — {question['prompt']}")
        session.meta["form_id"] = form_id
        session.meta["question_id"] = question["id"]
        session.meta["response_id"] = response_id
        session.save_meta()
        fields[question["id"]] = {
            "session": session.id,
            "token": session.meta["token"],
            "prompt": question["prompt"],
            "kind": question["kind"],
        }
    draft = {
        "id": response_id,
        "form_id": form_id,
        "submitted": False,
        "created_unix_ms": _now_ms(),
        "questions": form["questions"],
        "fields": fields,
        "answers": {},
    }
    _write_json(_response_path(root, form_id, response_id), draft)
    return {
        "id": response_id,
        "form_id": form_id,
        "questions": [
            {
                "id": question["id"],
                "prompt": question["prompt"],
                "kind": question["kind"],
                "token": fields[question["id"]]["token"],
            }
            for question in form["questions"]
        ],
    }


def _load_draft(root: Path, form_id: str, response_id: str) -> tuple[Path, dict]:
    path = _response_path(root, form_id, response_id)
    if not path.is_file():
        raise LangdonError(_RESPONSE_MISSING)
    return path, _read_json(path)


def submit_response(root: Path, form_id: str, response_id: str, answers) -> dict:
    path, draft = _load_draft(root, form_id, response_id)
    if draft.get("submitted"):
        return _public_response(draft, with_sessions=False)
    if not isinstance(answers, dict):
        raise LangdonError("answers must be an object")
    stored = {}
    for question in draft.get("questions") or []:
        qid = question["id"]
        text = answers.get(qid, "")
        if text is None:
            text = ""
        if not isinstance(text, str):
            raise LangdonError("an answer must be text")
        if len(text) > 2_000_000:
            raise LangdonError("an answer is too large")
        field = draft["fields"][qid]
        session = open_session(root, field["session"])
        if not session.sealed:
            session.seal(final_text=text)
            publish(session)
        _header, events, _truncated = session.load()
        reading = analyze(events)
        stored[qid] = {
            "prompt": question["prompt"],
            "kind": question["kind"],
            "text": text,
            "session": session.id,
            "label": reading["label"],
            "reading": reading["title"],
        }
    draft["submitted"] = True
    draft["submitted_unix_ms"] = _now_ms()
    draft["answers"] = stored
    draft.pop("fields", None)
    _write_json(path, draft)
    return _public_response(draft, with_sessions=False)


def _public_response(draft: dict, *, with_sessions: bool = True) -> dict:
    answers = draft.get("answers") or {}
    if not with_sessions:
        answers = {
            key: {field: value for field, value in answer.items() if field != "session"}
            for key, answer in answers.items()
            if isinstance(answer, dict)
        }
    return {
        "id": draft["id"],
        "created_unix_ms": draft.get("created_unix_ms") or 0,
        "submitted_unix_ms": draft.get("submitted_unix_ms") or 0,
        "questions": draft.get("questions") or [],
        "answers": answers,
    }


def list_responses(root: Path, form_id: str, owner_id: str) -> list[dict]:
    _require_owner(_load_form(root, form_id), owner_id)
    directory = _form_dir(root, form_id) / "responses"
    if not directory.is_dir():
        return []
    found = []
    for path in directory.glob("*.json"):
        try:
            draft = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if draft.get("submitted"):
            found.append(_public_response(draft))
    found.sort(key=lambda item: int(item.get("submitted_unix_ms") or 0))
    return found


def session_for_token(root: Path, token: str) -> Session | None:
    if not isinstance(token, str) or len(token) != 32:
        return None
    directory = _forms_root(root)
    if not directory.is_dir():
        return None
    for form_dir in directory.iterdir():
        responses = form_dir / "responses"
        if not responses.is_dir():
            continue
        for path in responses.glob("*.json"):
            try:
                draft = _read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if draft.get("submitted"):
                continue
            for field in (draft.get("fields") or {}).values():
                if _same(field.get("token"), token):
                    return open_session(root, field["session"])
    return None
