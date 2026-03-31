"""
memory_skill_v3 · 端到端测试
==============================
测试 AI 可调用接口：remember / memorize / flush。
使用真实的本地 embedding（sentence-transformers）。

不需要真实 AI，下载模型后不需要网络连接。

运行方式（在 memory_skill_v3 的上级目录执行）：
    python -m memory_skill_v3.test_skill
"""

import os
import sys
import tempfile
import importlib
import json

# 将 DB 重定向到临时文件，避免污染真实数据库
_tmp_fd, _tmp_db = tempfile.mkstemp(suffix=".db")
os.close(_tmp_fd)
os.environ["MEMORY_SQLITE_PATH"] = _tmp_db
os.environ["MEMORY_EMBED_DIM"]   = "384"

import memory_skill_v3 as skill


def _reload_skill():
    global skill

    import memory_skill_v3.api as api_module
    from memory_skill_v3 import config
    from memory_skill_v3.core import analyze, inject, persist, retrieve, write
    from memory_skill_v3.db import redis_db, sqlite_db

    sqlite_db.close()

    importlib.reload(config)
    importlib.reload(redis_db)
    importlib.reload(sqlite_db)
    importlib.reload(analyze)
    importlib.reload(write)
    importlib.reload(retrieve)
    importlib.reload(persist)
    importlib.reload(inject)
    importlib.reload(api_module)
    skill = importlib.reload(skill)


_reload_skill()


def sep(title=""):
    print("\n" + "-" * 60)
    if title:
        print(f"  {title}")
    print("-" * 60)


def test_setup():
    sep("TEST 1 · setup()")
    skill.setup()
    from memory_skill_v3.db import sqlite_db

    assert sqlite_db.get_db_path() == _tmp_db, (
        f"Expected temp db path {_tmp_db}, got {sqlite_db.get_db_path()}"
    )
    print("  PASS")


def test_remember_empty():
    sep("TEST 2 · remember() 在无记忆时返回空字符串")
    result = skill.remember(
        user_id    = "user_new",
        session_id = "session_new",
        turn       = 1,
        query_text = "第一次提问，什么记忆都没有",
    )
    assert result == "", f"Expected empty string, got: {repr(result)}"
    print("  PASS")


def test_memorize_and_hot_remember():
    sep("TEST 3 · memorize() 后 remember() 能命中热记忆")
    USER, SID = "user_a", "session_001"

    mem_ids_1 = skill.memorize(
        user_id    = USER,
        session_id = SID,
        turn       = 1,
        summary    = "用户正在构建 AI 记忆系统；当前方案使用 Redis 和 SQLite",
        keywords   = ["Redis", "SQLite", "记忆系统", "AI"],
        raw_q      = "我想做一个记忆系统",
        raw_a      = "好的，推荐 Redis+SQLite 方案",
    )
    assert isinstance(mem_ids_1, list) and len(mem_ids_1) >= 2, mem_ids_1

    mem_ids_2 = skill.memorize(
        user_id    = USER,
        session_id = SID,
        turn       = 2,
        summary    = "用户确认使用 Python；运行环境为 Windows",
        keywords   = ["Python", "Windows", "开发环境"],
        raw_q      = "我用 Python，Windows 系统",
        raw_a      = "没问题，pip install 即可",
    )
    assert isinstance(mem_ids_2, list) and len(mem_ids_2) >= 2, mem_ids_2

    from memory_skill_v3.db import redis_db

    hot_keys = redis_db.get_hot_keys(USER, SID)
    assert len(hot_keys) >= 4, hot_keys

    context = skill.remember(
        user_id    = USER,
        session_id = SID,
        turn       = 3,
        query_text = "Redis 和 SQLite 怎么配合使用",
    )

    print("  Context returned:")
    print("  " + context.replace("\n", "\n  "))
    assert "第1轮" in context
    assert "第2轮" in context
    print("  PASS")


def test_flush_and_cold_remember():
    sep("TEST 4 · flush() 后在新 session 能命中冷记忆")
    USER, SID = "user_a", "session_001"

    stats = skill.flush(user_id=USER, session_id=SID)
    print(f"  Flush stats: {stats}")
    assert stats["inserted"] >= 4, f"Expected >= 4 inserts, got {stats}"

    context = skill.remember(
        user_id    = USER,
        session_id = "session_002",
        turn       = 1,
        query_text = "Redis SQLite 记忆系统搭配",
    )

    print("  Context in new session:")
    print("  " + context.replace("\n", "\n  "))
    assert "历史记忆" in context, "Expected cold memory block"
    print("  PASS")


def test_flush_merge():
    sep("TEST 5 · flush() 合并近似重复记忆")
    USER = "user_merge"

    skill.memorize(
        user_id=USER, session_id="s_a", turn=1,
        summary  = "用户偏好使用轻量本地部署方案，不想引入复杂依赖",
        keywords = ["轻量", "本地部署", "依赖"],
    )
    skill.flush(user_id=USER, session_id="s_a")

    skill.memorize(
        user_id=USER, session_id="s_b", turn=1,
        summary  = "用户仍然坚持轻量本地部署，明确排除云服务方案",
        keywords = ["轻量", "本地部署", "云服务"],
    )
    stats = skill.flush(user_id=USER, session_id="s_b")

    print(f"  Second flush stats: {stats}")
    assert stats["updated"] >= 1 or stats["inserted"] >= 1
    print("  PASS")


def test_chat_wrapper():
    sep("TEST 6 · chat_wrapper 自动执行检索、回答、整理和记忆写入")
    USER, SID = "user_wrap", "session_wrap"

    def fake_llm(messages):
        first_system = messages[0]["content"] if messages else ""
        user_prompt = messages[-1]["content"] if messages else ""

        if "只输出 JSON" in first_system:
            if "继续，看看你记不记得刚才的要求" in user_prompt:
                return json.dumps({
                    "summary": "用户继续要求保持记忆闭环，并验证系统是否记住上一轮约束。",
                    "keywords": ["记忆闭环", "验证", "上一轮约束"],
                }, ensure_ascii=False)
            return json.dumps({
                "summary": "用户要求 AI 先检索记忆再综合回答，并在每轮后自动整理保存。",
                "keywords": ["先检索记忆", "综合回答", "自动整理保存"],
            }, ensure_ascii=False)

        return f"正式回答：{messages[-1]['content']}"

    session = skill.MemoryChatSession(
        user_id=USER,
        session_id=SID,
        call_llm=fake_llm,
        system_prompt="你是测试助手。",
        start_turn=1,
    )

    result_1 = session.chat("请使用本地记忆闭环回答我")
    assert result_1["turn"] == 1
    assert result_1["answer"] == "正式回答：请使用本地记忆闭环回答我"
    assert isinstance(result_1["mem_ids"], list) and result_1["mem_ids"]
    assert result_1["memory_text"] == ""

    result_2 = session.chat("继续，看看你记不记得刚才的要求")
    assert result_2["turn"] == 2
    assert "【本轮对话】" in result_2["memory_text"]
    assert "先检索记忆再综合回答" in result_2["memory_text"]
    assert session.turn == 3

    stats = session.flush()
    assert stats["inserted"] >= 2, stats
    print("  PASS")


def test_get_stats():
    sep("TEST 7 · get_stats()")
    stats = skill.get_stats("user_a")
    print(f"  {stats}")
    assert stats["total_memories"] > 0
    assert stats["sessions"] > 0
    print("  PASS")


def test_empty_flush():
    sep("TEST 8 · 对不存在的 session 执行 flush() 返回全零")
    result = skill.flush("user_a", "session_nonexistent_xyz")
    assert result == {"inserted": 0, "updated": 0, "skipped": 0}
    print(f"  {result}")
    print("  PASS")


def run_all():
    tests = [
        test_setup,
        test_remember_empty,
        test_memorize_and_hot_remember,
        test_flush_and_cold_remember,
        test_flush_merge,
        test_chat_wrapper,
        test_get_stats,
        test_empty_flush,
    ]
    passed = 0
    failed = 0

    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"\n  FAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    sep()
    print(f"  {passed} passed  /  {failed} failed")

    try:
        os.remove(_tmp_db)
    except Exception:
        pass

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    run_all()
