import json
import re

from ..db import redis_db, sqlite_db
from ..utils import vec_utils
from .. import config


def retrieve(user_id, session_id, query_embedding, query_text=""):
    hot  = _get_hot(user_id, session_id)
    cold = _get_cold(user_id, session_id, query_embedding, query_text)
    return hot, cold


def _get_hot(user_id, session_id):
    r    = redis_db.get_client()
    keys = redis_db.get_hot_keys(user_id, session_id)
    if not keys:
        return []
    values = r.mget(keys)
    memories = []
    for key, val in zip(keys, values):
        if val:
            memories.append(json.loads(val))
        else:
            # key 已过期，从 index Set 清除，保持 Set 干净
            r.srem(redis_db.index_key(user_id, session_id), key)
    memories.sort(key=lambda m: (
        int(m.get("turn", 0)),
        int(m.get("item_index", 0)),
        m.get("created_at", ""),
    ))
    return memories


def _get_cold(user_id, session_id, query_embedding, query_text):
    """检索冷记忆，排除当前 session（当前 session 已在热记忆中，避免重复注入）。"""
    conn        = sqlite_db.get_conn()
    results     = []
    vec_bytes   = vec_utils.serialize(query_embedding)
    max_dist    = 1.0 - config.SIM_THRESHOLD
    fetch_limit = config.TOP_K * 2

    try:
        rows = conn.execute("""
            SELECT m.id, m.summary, m.keywords, m.created_at, v.distance
            FROM   memories_vec v
            JOIN   memories m ON m.rowid = v.rowid
            WHERE  v.embedding MATCH ?
            AND    m.user_id    = ?
            AND    m.session_id != ?
            ORDER  BY v.distance ASC
            LIMIT  ?
        """, (vec_bytes, user_id, session_id, fetch_limit)).fetchall()

        for row in rows:
            if row["distance"] <= max_dist:
                results.append({
                    "id":         row["id"],
                    "summary":    row["summary"],
                    "keywords":   json.loads(row["keywords"] or "[]"),
                    "created_at": row["created_at"],
                    "score":      1.0 - row["distance"],
                    "source":     "vec",
                })
    except Exception:
        pass

    if query_text and len(results) < config.TOP_K:
        fts_results = _fts_search(conn, user_id, session_id, query_text, config.TOP_K)
        seen_ids    = {r["id"] for r in results}
        for r in fts_results:
            if r["id"] not in seen_ids:
                results.append(r)
                seen_ids.add(r["id"])

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:config.TOP_K]


def _fts_search(conn, user_id, session_id, query_text, limit):
    safe_query = _build_fts_query(query_text)
    if not safe_query:
        return []
    try:
        rows = conn.execute("""
            SELECT m.id, m.summary, m.keywords, m.created_at, rank
            FROM   memories_fts f
            JOIN   memories m ON m.rowid = f.rowid
            WHERE  memories_fts MATCH ?
            AND    m.user_id    = ?
            AND    m.session_id != ?
            ORDER  BY rank
            LIMIT  ?
        """, (safe_query, user_id, session_id, limit)).fetchall()
        return [{
            "id":         row["id"],
            "summary":    row["summary"],
            "keywords":   json.loads(row["keywords"] or "[]"),
            "created_at": row["created_at"],
            "score":      0.5,
            "source":     "fts",
        } for row in rows]
    except Exception:
        return []


# FTS5 特殊字符，需替换为空格
_FTS_SPECIALS = re.compile(r'[\"*^()\[\]{}:,\-]')
# CJK 统一汉字区块
_CJK_RE       = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002A6DF]')


def _build_fts_query(text):
    """构建 FTS5 OR 查询字符串，同时支持中文和非中文。

    处理逻辑：
    1. 移除 FTS5 特殊字符
    2. 提取空格分隔的非 CJK token（长度 > 1）
    3. 提取所有 CJK 单字作为独立 token
    4. 用 OR 连接，去重保持顺序

    示例：
        "Redis和SQLite记忆系统搭配" → "Redis SQLite 记 忆 系 统 搭 配" → OR 查询
    """
    cleaned = _FTS_SPECIALS.sub(" ", text)

    # 非 CJK 词（按空格切分，去掉纯 CJK 混合段中的残留）
    non_cjk_tokens = [
        t for t in cleaned.split()
        if len(t) > 1 and not _CJK_RE.fullmatch(t)
    ]

    # CJK 单字
    cjk_tokens = _CJK_RE.findall(text)

    # 合并、去重、保持顺序
    seen   = set()
    tokens = []
    for t in non_cjk_tokens + cjk_tokens:
        if t not in seen:
            seen.add(t)
            tokens.append(t)

    return " OR ".join(tokens) if tokens else ""
