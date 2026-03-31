import json
import uuid
import logging

from ..db import redis_db, sqlite_db
from ..utils import vec_utils
from .. import config

logger = logging.getLogger(__name__)


def persist_session(user_id, session_id):
    r    = redis_db.get_client()
    keys = redis_db.get_hot_keys(user_id, session_id)

    if not keys:
        return {"inserted": 0, "updated": 0, "skipped": 0}

    # 批量拉取，过滤已过期的 key（mget 对过期 key 返回 None）
    values  = r.mget(keys)
    valid_keys = []
    memories   = []
    for key, val in zip(keys, values):
        if val:
            memories.append(json.loads(val))
            valid_keys.append(key)
        else:
            # key 已在 Redis 中过期，从 index Set 中清理掉，避免 Set 无限膨胀
            r.srem(redis_db.index_key(user_id, session_id), key)

    memories.sort(key=lambda m: (
        int(m.get("turn", 0)),
        int(m.get("item_index", 0)),
        m.get("created_at", ""),
    ))

    # 所有 key 均已过期（全部在 TTL 内未 flush），清理 index Set 后直接返回
    if not memories:
        redis_db.delete_hot_keys(r, user_id, session_id, [])
        return {"inserted": 0, "updated": 0, "skipped": 0}

    conn  = sqlite_db.get_conn()
    stats = {"inserted": 0, "updated": 0, "skipped": 0}

    with sqlite_db.write_lock:
        try:
            for mem in memories:
                _persist_one(conn, mem, stats)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # SQLite commit 成功后再清 Redis。
    # 极端情况（commit 后进程崩溃）下 Redis key 尚未删除，下次 flush 会因
    # MERGE_THRESHOLD 逻辑对重复记忆执行 update 而非 insert，不会产生真正的重复行。
    redis_db.delete_hot_keys(r, user_id, session_id, valid_keys)

    return stats


def _persist_one(conn, mem, stats):
    embedding = mem.get("embedding")
    if not embedding:
        stats["skipped"] += 1
        return

    vec_bytes  = vec_utils.serialize(embedding)
    merge_dist = 1.0 - config.MERGE_THRESHOLD
    # 限定在同一 user_id，且排除当前 session（当前 session 数据是热记忆，
    # 不应与自身 flush 前的冷记忆合并，避免跨 session 错误 merge）
    similar = _find_closest(conn, mem["user_id"], mem["session_id"], vec_bytes, mem.get("kind", "general"))

    if similar is None or similar["distance"] > merge_dist:
        _insert(conn, mem, vec_bytes)
        stats["inserted"] += 1
        return

    new_kw  = _to_list(mem.get("keywords", []))
    old_kw  = _to_list(similar["keywords"] or "[]")
    overlap = vec_utils.keyword_overlap(new_kw, old_kw)

    same_kind = similar.get("kind", "general") == mem.get("kind", "general")
    if overlap >= 0.4 and same_kind:
        _update(conn, similar["rowid"], similar["id"], mem, vec_bytes)
        stats["updated"] += 1
    else:
        _insert(conn, mem, vec_bytes)
        stats["inserted"] += 1


def _find_closest(conn, user_id, current_session_id, vec_bytes, kind):
    """在冷记忆中找最近邻，排除当前 session（当前 session 还未 flush 完成）。"""
    try:
        row = conn.execute("""
            SELECT m.rowid, m.id, m.keywords, m.kind, v.distance
            FROM   memories_vec v
            JOIN   memories m ON m.rowid = v.rowid
            WHERE  v.embedding MATCH ?
            AND    m.user_id    = ?
            AND    m.session_id != ?
            AND    m.kind       = ?
            ORDER  BY v.distance ASC
            LIMIT  1
        """, (vec_bytes, user_id, current_session_id, kind)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _insert(conn, mem, vec_bytes):
    mem_id   = mem.get("id") or str(uuid.uuid4())
    keywords = _to_json(mem.get("keywords", []))

    cursor = conn.execute("""
        INSERT INTO memories (
            id, user_id, session_id, turn, item_index, kind, summary, keywords, raw_q, raw_a
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        mem_id, mem["user_id"], mem["session_id"], mem["turn"],
        mem.get("item_index", 0), mem.get("kind", "general"),
        mem["summary"], keywords, mem.get("raw_q", ""), mem.get("raw_a", ""),
    ))

    rowid = cursor.lastrowid
    conn.execute(
        "INSERT INTO memories_vec (rowid, embedding) VALUES (?, ?)",
        (rowid, vec_bytes),
    )


def _update(conn, rowid, mem_id, new_mem, vec_bytes):
    keywords = _to_json(new_mem.get("keywords", []))
    conn.execute("""
        UPDATE memories
        SET summary    = ?,
            keywords   = ?,
            item_index = ?,
            kind       = ?,
            raw_q      = ?,
            raw_a      = ?,
            version    = version + 1,
            updated_at = datetime('now')
        WHERE id = ?
    """, (new_mem["summary"], keywords,
          new_mem.get("item_index", 0), new_mem.get("kind", "general"),
          new_mem.get("raw_q", ""), new_mem.get("raw_a", ""), mem_id))

    conn.execute("DELETE FROM memories_vec WHERE rowid = ?", (rowid,))
    conn.execute(
        "INSERT INTO memories_vec (rowid, embedding) VALUES (?, ?)",
        (rowid, vec_bytes),
    )


def _to_list(value):
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except Exception:
        return []


def _to_json(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
