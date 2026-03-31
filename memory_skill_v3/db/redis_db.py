import redis as _redis
from .. import config

_client = None


def get_client():
    global _client
    if _client is None:
        _client = _redis.from_url(config.REDIS_URL, decode_responses=True)
    return _client


def ping():
    try:
        return get_client().ping()
    except Exception:
        return False


def check_persistence():
    """检查 Redis 是否开启了持久化（RDB 或 AOF）。
    未开启时打印 warning，提醒用户热记忆在 Redis 重启后会丢失。

    RDB 是否启用：通过 CONFIG GET save 判断，save 为空字符串表示禁用。
    rdb_last_bgsave_status 字段即使禁用 RDB 也存在，不能用于判断。
    """
    try:
        r = get_client()

        # RDB：save 配置非空表示启用（默认值类似 "3600 1 300 100 60 10000"）
        save_cfg    = r.config_get("save").get("save", "")
        rdb_enabled = bool(save_cfg.strip())

        # AOF：aof_enabled 为 1 表示启用
        info        = r.info("persistence")
        aof_enabled = info.get("aof_enabled", 0) == 1

        if not rdb_enabled and not aof_enabled:
            print(
                "[memory-skill] WARNING: Redis persistence is OFF (neither RDB nor AOF). "
                "Hot memories will be lost on Redis restart before flush(). "
                "Enable RDB (CONFIG SET save '3600 1') or AOF in redis.conf to avoid data loss."
            )
    except Exception:
        pass  # 不因检查失败阻断启动


def hot_key(user_id, session_id, turn, item_index=0):
    return f"mem:hot:{user_id}:{session_id}:{turn}:{item_index}"


def turns_key(session_id):
    return f"session:turns:{session_id}"


def index_key(user_id, session_id):
    """Set 键，记录该 session 所有热记忆的 hot_key 集合。
    用 Set 代替 scan_iter，避免全库扫描，在大 key 量下性能恒定 O(1)。
    """
    return f"mem:idx:{user_id}:{session_id}"


def get_hot_keys(user_id, session_id):
    """从 Set 中获取该 session 的全部热记忆 key。"""
    r = get_client()
    return list(r.smembers(index_key(user_id, session_id)))


def register_hot_key(r, user_id, session_id, key, ttl):
    """将 hot_key 注册到 session 的 index Set，并同步刷新 Set TTL。"""
    idx_key = index_key(user_id, session_id)
    r.sadd(idx_key, key)
    r.expire(idx_key, ttl)


def delete_hot_keys(r, user_id, session_id, keys):
    """删除全部热记忆 key 及 index Set 和 turns key。"""
    if keys:
        r.delete(*keys)
    r.delete(index_key(user_id, session_id))
    r.delete(turns_key(session_id))
