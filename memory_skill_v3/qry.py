"""
memory_skill_v3 · 调试查询工具
================================
查看当前 Redis 热记忆与 SQLite 冷记忆的内容。

运行方式（在 memory_skill_v3 的上级目录执行）：
    python -m memory_skill_v3.qry
"""

import json

import redis

from memory_skill_v3.db.sqlite_db import get_db_path, get_conn

r       = redis.from_url("redis://localhost:6379", decode_responses=True)
db_path = get_db_path()
print(f"SQLite 路径: {db_path}\n")

# ── Redis 热记忆 ──────────────────────────────────────────────────────────────
keys = r.keys("mem:hot:*")
print(f"Redis 热记忆条数: {len(keys)}")
for key in sorted(keys):
    raw = r.get(key)
    if not raw:
        continue
    data = json.loads(raw)
    print(f"\n  [第{data.get('turn', '?')}轮] {data['summary'][:60]}")
    print(f"  用户: {data['user_id']}  会话: {data['session_id']}")

# ── SQLite 冷记忆 ─────────────────────────────────────────────────────────────
conn = get_conn()
rows = conn.execute(
    "SELECT user_id, session_id, turn, summary, keywords, created_at, version "
    "FROM memories ORDER BY created_at DESC"
).fetchall()

print(f"\nSQLite 冷记忆条数: {len(rows)}")
for row in rows:
    print(f"\n  [{row['created_at'][:10]}] 第{row['turn']}轮  v{row['version']}")
    print(f"  摘要: {row['summary'][:60]}")
    print(f"  关键词: {row['keywords']}")
    print(f"  用户: {row['user_id']}  会话: {row['session_id']}")
