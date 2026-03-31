import json
import uuid
from datetime import datetime, timezone

from ..db import redis_db
from .. import config


def write(
    user_id,
    session_id,
    turn,
    summary,
    keywords,
    embedding,
    raw_q="",
    raw_a="",
    item_index=0,
    kind="general",
):
    mem_id = str(uuid.uuid4())
    mem = {
        "id":         mem_id,
        "user_id":    user_id,
        "session_id": session_id,
        "turn":       turn,
        "item_index": item_index,
        "kind":       kind,
        "summary":    summary,
        "keywords":   keywords,
        "embedding":  embedding,
        "raw_q":      raw_q,
        "raw_a":      raw_a,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    r   = redis_db.get_client()
    key = redis_db.hot_key(user_id, session_id, turn, item_index)
    r.setex(key, config.SESSION_TTL, json.dumps(mem, ensure_ascii=False))

    redis_db.register_hot_key(r, user_id, session_id, key, config.SESSION_TTL)

    tkey = redis_db.turns_key(session_id)
    r.setex(tkey, config.SESSION_TTL, str(turn))

    return mem_id


def write_many(user_id, session_id, turn, items, raw_q="", raw_a=""):
    mem_ids = []
    for item in items:
        mem_ids.append(write(
            user_id    = user_id,
            session_id = session_id,
            turn       = turn,
            summary    = item["summary"],
            keywords   = item.get("keywords", []),
            embedding  = item["embedding"],
            raw_q      = raw_q,
            raw_a      = raw_a,
            item_index = item.get("item_index", 0),
            kind       = item.get("kind", "general"),
        ))
    return mem_ids
