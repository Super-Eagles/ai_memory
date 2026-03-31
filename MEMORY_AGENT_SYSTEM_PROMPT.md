# memory_skill_v3 · AI 使用手册

> **阅读顺序：** 先看模块结构 → 快速上手 → API 参考 → 配置 → 行为规程。
> 读完前三节，你就能独立完成接入。后两节是补充与约束。

---

## 一、模块结构

```
memory_skill_v3/
├── __init__.py        # 统一导出：直接从包名 import 即可，无需记内部路径
├── api.py             # ★ 核心入口：setup / remember / memorize / flush / get_stats
├── chat_wrapper.py    # ★ 高级封装：MemoryChatSession / run_chat_turn（推荐使用）
├── config.py          # 全局配置（优先读环境变量，支持运行时覆盖）
├── core/
│   ├── analyze.py     # 将问答拆解为可存储的记忆条目
│   ├── inject.py      # 将检索结果格式化并注入 prompt
│   ├── persist.py     # 热记忆 → 冷记忆归档（flush 内部调用）
│   ├── retrieve.py    # 向量检索（冷）+ Redis 读取（热）
│   └── write.py       # 写入 Redis 热记忆
├── db/
│   ├── redis_db.py    # 热记忆存储（当前会话，TTL=24h）
│   └── sqlite_db.py   # 冷记忆存储（向量索引 + FTS 全文检索）
└── utils/
    ├── embedding.py   # 文本嵌入（本地模型 或 远程服务二选一）
    └── vec_utils.py   # 向量序列化 / 相似度工具
```

**你只需要接触的两个文件：`api.py` 和 `chat_wrapper.py`。**
`core/`、`db/`、`utils/` 是内部实现，不要直接调用。

---

## 二、前置条件

在调用任何函数之前，确保以下服务已就绪：

| 依赖 | 说明 | 默认地址 |
|------|------|---------|
| Redis / Memurai | 热记忆存储，必须运行 | `redis://localhost:6379` |
| 向量嵌入 | 二选一：本地模型（首次下载 ~470 MB）或远程服务 | 见下方配置 |

**本地嵌入模型（默认）：** 首次调用 `setup()` 时自动下载，无需额外操作。

**远程嵌入服务（推荐生产环境）：**
```bash
# 终端 1：启动嵌入服务（只需启动一次）
python embed_server.py

# 终端 2：设置环境变量后启动主程序
export MEMORY_EMBED_SERVICE_URL=http://127.0.0.1:7731
python your_app.py
```

---

## 三、快速上手

### 方式 A：MemoryChatSession（推荐）

`MemoryChatSession` 自动处理 remember → 回答 → memorize → turn+1 的完整流程，
**你只需要提供一个 `call_llm` 函数即可。**

```python
from memory_skill_v3 import setup, MemoryChatSession

# ① 初始化（整个进程只需调用一次）
setup()

# ② 定义你的 LLM 调用函数
#    输入：messages 列表（OpenAI / Anthropic 格式均可）
#    输出：str 或包含 "content"/"text" 字段的 dict
def call_llm(messages):
    # 示例：Anthropic Claude
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=next((m["content"] for m in messages if m["role"] == "system"), ""),
        messages=[m for m in messages if m["role"] != "system"],
    )
    return resp.content[0].text

# ③ 创建会话（user_id 和 session_id 由你的应用负责生成和维护）
session = MemoryChatSession(
    user_id="user_001",
    session_id="session_abc",
    call_llm=call_llm,
    system_prompt="你是一个有记忆的助手。",
)

# ④ 对话（每次 chat() 自动执行完整记忆流程）
result = session.chat("我叫李明，平时用 Python 写后端。")
print(result["answer"])

result = session.chat("帮我写一个 FastAPI 的路由模板。")
print(result["answer"])

# ⑤ 会话结束时归档到冷记忆（可选，不调用热记忆也会在 TTL 后自动过期）
session.flush()
```

`result` 字典包含的字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `answer` | `str` | 本轮 LLM 回答 |
| `memory_text` | `str` | 本轮注入的记忆文本（可为空） |
| `summary` | `str` | 本轮提炼的记忆摘要 |
| `keywords` | `list[str]` | 本轮提炼的关键词 |
| `mem_ids` | `list[str]` | 写入的记忆 ID 列表 |
| `turn` | `int` | 本轮轮次号 |

---

### 方式 B：run_chat_turn（单次调用）

适合无法维护 session 对象的场景（如无状态函数、Lambda 等）：

```python
from memory_skill_v3 import setup, run_chat_turn

setup()

result = run_chat_turn(
    user_id="user_001",
    session_id="session_abc",
    turn=1,                   # 你的应用负责递增
    user_text="我最近在学 Rust，想了解所有权机制。",
    call_llm=call_llm,        # 同方式 A
    system_prompt="你是一个有记忆的助手。",
)
print(result["answer"])
```

---

### 方式 C：底层 API（最大灵活性）

需要自己控制每一步时使用：

```python
from memory_skill_v3 import setup, remember, memorize, flush

setup()

user_id    = "user_001"
session_id = "session_abc"
turn       = 1
user_text  = "我在用 Redis 做缓存层。"

# ① 检索记忆（必须最先执行）
memory_text = remember(user_id, session_id, turn, user_text)

# ② 将 memory_text 合并进 system prompt，再调用你的 LLM
messages = []
combined_system = f"你是有记忆的助手。\n\n{memory_text}".strip() if memory_text else "你是有记忆的助手。"
messages.append({"role": "system", "content": combined_system})
messages.append({"role": "user", "content": user_text})
answer = call_llm(messages)   # 你自己的 LLM 函数

# ③ 提炼本轮摘要（可用 LLM，也可手写）
summary  = "用户正在用 Redis 做缓存层，关注架构设计。"
keywords = ["Redis", "缓存", "架构"]

# ④ 写入记忆
mem_ids = memorize(
    user_id=user_id,
    session_id=session_id,
    turn=turn,
    summary=summary,
    keywords=keywords,
    raw_q=user_text,
    raw_a=answer,
)

# ⑤ 递增轮次
turn += 1

# ⑥ 会话结束时归档
flush(user_id, session_id)
```

---

## 四、API 参考

### `setup() → None`

初始化数据库连接和嵌入模型。**整个进程只调用一次**，通常在程序启动时。

失败时抛出 `RuntimeError`，说明 Redis 或嵌入服务不可达。

---

### `remember(user_id, session_id, turn, query_text) → str`

检索与 `query_text` 相关的历史记忆，返回可直接注入 prompt 的格式化文本。

| 参数 | 类型 | 说明 |
|------|------|------|
| `user_id` | `str` | 用户唯一标识，同一用户保持不变 |
| `session_id` | `str` | 会话唯一标识，同一会话保持不变 |
| `turn` | `int` | 当前轮次，从 1 开始 |
| `query_text` | `str` | 用户当前输入，用于语义检索 |

**返回值：**
- 空字符串 → 无相关记忆，直接继续
- 非空字符串 → 格式化的记忆文本，**必须合并进 system prompt**（不能作为独立的第二条 system 消息，部分模型会静默忽略）

```python
# 正确：合并到 system prompt
system = f"{your_system_prompt}\n\n{memory_text}".strip()

# 错误：不要作为第二条 system 消息
messages = [
    {"role": "system", "content": your_system_prompt},
    {"role": "system", "content": memory_text},   # ❌ 部分模型忽略
]
```

---

### `memorize(user_id, session_id, turn, summary, keywords, raw_q, raw_a) → list[str]`

将本轮问答写入热记忆（Redis），返回写入的 `mem_id` 列表。

| 参数 | 类型 | 说明 |
|------|------|------|
| `user_id` | `str` | 同 remember |
| `session_id` | `str` | 同 remember |
| `turn` | `int` | 当前轮次 |
| `summary` | `str` | 本轮有长期价值的摘要（见摘要规范） |
| `keywords` | `list[str]` | 2～8 个关键词，用于检索增强 |
| `raw_q` | `str` | 用户原始输入 |
| `raw_a` | `str` | 本轮完整回答 |

**失败时**返回空列表 `[]`，不影响本轮对话，记录失败原因即可。

---

### `flush(user_id, session_id) → dict`

将热记忆（Redis）归档到冷记忆（SQLite + 向量索引）。

返回值示例：`{"inserted": 5, "updated": 2, "skipped": 0}`

触发时机：
- 用户主动说"结束会话"、"保存记忆"、"归档"
- 会话正常结束时
- 热记忆会在 24 小时后自动过期（`SESSION_TTL`），但 flush 能确保立即持久化

---

### `get_stats(user_id) → dict`

查询用户的记忆统计信息。

返回值示例：`{"total_memories": 42, "sessions": 7}`

---

## 五、配置参考

所有配置**优先读环境变量**，也可在代码中直接覆盖：

```python
from memory_skill_v3 import config

config.TOP_K = 8                          # 检索返回的最大条数（默认 5）
config.SIM_THRESHOLD = 0.80              # 相似度阈值（默认 0.75，越高越严格）
config.MEMORY_TOKEN_BUDGET = 2000        # 注入记忆的 token 上限（长上下文模型可放大）
config.EXTRA_CATEGORY_HINTS = {          # 追加自定义分类关键词
    "preference": ["prefer", "喜好"],
    "fact": ["架构图", "ER图"],
}
```

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `MEMORY_REDIS_URL` | `redis://localhost:6379` | Redis 连接地址 |
| `MEMORY_SQLITE_PATH` | `./memory.db` | SQLite 文件路径 |
| `MEMORY_EMBED_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | 本地嵌入模型 |
| `MEMORY_EMBED_DIM` | `384` | 向量维度 |
| `MEMORY_EMBED_SERVICE_URL` | `""` | 远程嵌入服务地址（空=本地模式） |
| `MEMORY_TOP_K` | `5` | 检索返回条数 |
| `MEMORY_SIM_THRESHOLD` | `0.75` | 相似度阈值 |
| `MEMORY_MERGE_THRESHOLD` | `0.88` | flush 时合并相似记忆的阈值 |
| `MEMORY_SESSION_TTL` | `86400` | 热记忆 TTL（秒），默认 24 小时 |
| `MEMORY_TOKEN_BUDGET` | `1200` | 注入记忆的 token 上限 |

---

## 六、摘要规范

调用底层 `memorize()` 时，`summary` 和 `keywords` 的质量直接决定未来检索效果。

**应该保留：**
- 用户长期偏好（"偏好 Python，不想用 Java"）
- 明确约束或前提（"项目必须兼容 Python 3.9"）
- 已确认的决定（"采用 FastAPI + Redis 方案"）
- 重要结论（"当前架构存在单点故障风险"）
- 稳定事实（"项目是一个多租户 SaaS，使用 PostgreSQL"）

**不应保留：**
- 寒暄与客套（"好的，明白了"）
- 一次性过程细节（"用户粘贴了一段代码，我帮他 debug"）
- 临时状态（"用户现在在等待部署结果"）

**摘要格式示例：**
```json
{
  "summary": "用户使用 FastAPI + Redis 做缓存层，项目需兼容 Python 3.9，偏好简洁的代码风格。",
  "keywords": ["FastAPI", "Redis", "Python 3.9", "缓存", "偏好"]
}
```

---

## 七、行为规程（使用 MemoryChatSession 时可跳过此节）

> 本节仅适用于直接调用底层 API 的场景。
> 使用 `MemoryChatSession` 或 `run_chat_turn()` 时，以下流程已自动执行。

### 每轮必须按序执行

```
① remember()  →  ② 生成回答  →  ③ memorize()  →  ④ turn += 1
```

此顺序不可颠倒，不可跳过。即使用户说"跳过记忆"，流程也必须完整执行。

### 必须维护的三个标识

| 标识 | 规则 |
|------|------|
| `user_id` | 同一用户保持不变，跨会话不变 |
| `session_id` | 同一会话保持不变，新会话生成新 ID |
| `turn` | 从 1 开始，每轮 +1，不可重置 |

### 回答时如何使用记忆

- 把记忆当作背景常识，自然融入回答
- 不要生硬复述（不要说"根据我的记忆"或"我查到了记忆"）
- `remember()` 返回空字符串时，正常回答，本轮仍然要执行 `memorize()`

### 会话结束处理

用户说以下任意一句时，调用 `flush()`：

> "结束会话" / "flush 记忆" / "归档记忆" / "保存到长期记忆"

`flush()` 失败时，不丢弃会话标识，允许后续重试。

### 每轮回答结尾输出执行状态

```
[✓ remember(turn=X) | ✓ 回答 | ✓ memorize | turn 已递增至 X+1]
```

某步失败时：

```
[✓ remember | ✓ 回答 | ✗ memorize 失败：{原因} | turn=X 未递增]
```

---

## 八、常见错误排查

| 错误现象 | 原因 | 解决方法 |
|---------|------|---------|
| `RuntimeError: Cannot reach Redis` | Redis 未启动 | 启动 Memurai 或 Redis 服务 |
| `RuntimeError: Embedding service not reachable` | 远程嵌入服务未启动 | 运行 `python embed_server.py` |
| `remember()` 始终返回空字符串 | 从未调用过 `flush()`，冷记忆为空；或相似度低于阈值 | 检查 `SIM_THRESHOLD` 配置，或确认冷记忆已写入 |
| 记忆注入后 LLM 不理睬 | memory_text 作为第二条 system 消息传入 | 合并到单条 system prompt（见 API 参考） |
| `memorize()` 返回空列表 | 摘要未通过有价值过滤 | 检查 summary 是否过短或属于低价值内容 |
