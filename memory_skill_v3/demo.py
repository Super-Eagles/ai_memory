"""
memory_skill_v3 · 快速演示
==========================
直接运行：
    cd <memory_skill_v3 的上级目录>
    python -m memory_skill_v3.demo
"""

import memory_skill_v3 as skill

skill.setup()

user_id    = "user_demo"
session_id = "session_demo"

# 第一轮：写入记忆
skill.memorize(
    user_id    = user_id,
    session_id = session_id,
    turn       = 1,
    summary    = "用户想用 Python 做聊天机器人",
    keywords   = ["Python", "聊天机器人"],
    raw_q      = "我想用 Python 做聊天机器人",
    raw_a      = "推荐你用 LangChain 或 Rasa",
)

# 第二轮：检索相关记忆（能否召回第一轮？）
context = skill.remember(
    user_id    = user_id,
    session_id = session_id,
    turn       = 2,
    query_text = "有没有更轻量的方案",
)
print(context)

# 会话结束，持久化到 SQLite
stats = skill.flush(user_id, session_id)
print(f"flush stats: {stats}")

print(skill.get_stats(user_id))
