import argparse
import getpass
import json
import os
from datetime import datetime
from pathlib import Path

from . import api


STATE_FILE = Path(__file__).with_name("active_sessions.json")


def _normalize_workspace(workspace: str) -> str:
    return str(Path(workspace).resolve())


def _load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(data):
    STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _default_user_id():
    return f"codex_{getpass.getuser()}"


def _new_session_id(workspace: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = Path(workspace).name or "workspace"
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name)
    return f"{safe_name}_{stamp}"


def _ensure_session(workspace: str, user_id: str | None = None, reset: bool = False):
    workspace = _normalize_workspace(workspace)
    state = _load_state()
    entry = state.get(workspace)

    if reset or entry is None:
        now = datetime.now().isoformat()
        entry = {
            "workspace": workspace,
            "user_id": user_id or _default_user_id(),
            "session_id": _new_session_id(workspace),
            "turn": 1,
            "created_at": now,
            "updated_at": now,
        }
        state[workspace] = entry
        _save_state(state)
        return entry

    if user_id and entry.get("user_id") != user_id:
        entry["user_id"] = user_id
        entry["updated_at"] = datetime.now().isoformat()
        state[workspace] = entry
        _save_state(state)

    return entry


def _update_entry(workspace: str, entry):
    workspace = _normalize_workspace(workspace)
    state = _load_state()
    entry["updated_at"] = datetime.now().isoformat()
    state[workspace] = entry
    _save_state(state)


def _remove_entry(workspace: str):
    workspace = _normalize_workspace(workspace)
    state = _load_state()
    if workspace in state:
        del state[workspace]
        _save_state(state)


def _parse_keywords(value: str):
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, str) and parsed.strip():
        return [parsed.strip()]
    return []


def _read_text_file(path: str):
    target = Path(path)
    last_error = None
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "gbk"):
        try:
            return target.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return target.read_text(encoding="utf-8")


def _pick_text_arg(direct_value, file_value, field_name: str):
    if direct_value not in (None, ""):
        return direct_value
    if file_value not in (None, ""):
        return _read_text_file(file_value)
    raise SystemExit(f"{field_name} is required")


def cmd_ensure(args):
    entry = _ensure_session(args.workspace, args.user_id, args.reset)
    print(json.dumps(entry, ensure_ascii=False, indent=2))


def cmd_show(args):
    entry = _ensure_session(args.workspace, args.user_id, False)
    print(json.dumps(entry, ensure_ascii=False, indent=2))


def cmd_remember(args):
    api.setup()
    entry = _ensure_session(args.workspace, args.user_id, False)
    memory_text = api.remember(
        user_id=entry["user_id"],
        session_id=entry["session_id"],
        turn=int(entry["turn"]),
        query_text=args.query,
    )
    print(json.dumps({
        "workspace": entry["workspace"],
        "user_id": entry["user_id"],
        "session_id": entry["session_id"],
        "turn": entry["turn"],
        "memory_text": memory_text,
    }, ensure_ascii=False, indent=2))


def cmd_write(args):
    api.setup()
    entry = _ensure_session(args.workspace, args.user_id, False)
    question = _pick_text_arg(args.question, args.question_file, "question")
    answer = _pick_text_arg(args.answer, args.answer_file, "answer")
    summary = _pick_text_arg(args.summary, args.summary_file, "summary")
    keywords = _parse_keywords(args.keywords_json)
    mem_ids = api.memorize(
        user_id=entry["user_id"],
        session_id=entry["session_id"],
        turn=int(entry["turn"]),
        summary=summary,
        keywords=keywords,
        raw_q=question,
        raw_a=answer,
    )
    entry["turn"] = int(entry["turn"]) + 1
    _update_entry(args.workspace, entry)
    print(json.dumps({
        "workspace": entry["workspace"],
        "user_id": entry["user_id"],
        "session_id": entry["session_id"],
        "next_turn": entry["turn"],
        "mem_ids": mem_ids,
    }, ensure_ascii=False, indent=2))


def cmd_flush(args):
    api.setup()
    entry = _ensure_session(args.workspace, args.user_id, False)
    stats = api.flush(
        user_id=entry["user_id"],
        session_id=entry["session_id"],
    )
    _remove_entry(args.workspace)
    print(json.dumps({
        "workspace": entry["workspace"],
        "user_id": entry["user_id"],
        "session_id": entry["session_id"],
        "flushed": True,
        "stats": stats,
    }, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="memory_skill_v3 session CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--workspace", required=True)
        p.add_argument("--user-id", dest="user_id")

    p_ensure = subparsers.add_parser("ensure")
    add_common(p_ensure)
    p_ensure.add_argument("--reset", action="store_true")
    p_ensure.set_defaults(func=cmd_ensure)

    p_show = subparsers.add_parser("show")
    add_common(p_show)
    p_show.set_defaults(func=cmd_show)

    p_remember = subparsers.add_parser("remember")
    add_common(p_remember)
    p_remember.add_argument("--query", required=True)
    p_remember.set_defaults(func=cmd_remember)

    p_write = subparsers.add_parser("write")
    add_common(p_write)
    p_write.add_argument("--question")
    p_write.add_argument("--question-file")
    p_write.add_argument("--answer")
    p_write.add_argument("--answer-file")
    p_write.add_argument("--summary")
    p_write.add_argument("--summary-file")
    p_write.add_argument("--keywords-json", default="[]")
    p_write.set_defaults(func=cmd_write)

    p_flush = subparsers.add_parser("flush")
    add_common(p_flush)
    p_flush.set_defaults(func=cmd_flush)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
