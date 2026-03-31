import re
from .. import config


# 默认关键词词表，支持通过 config.CATEGORY_HINTS 在外部完整替换，
# 或通过 config.EXTRA_CATEGORY_HINTS 按类别追加扩展词。
_DEFAULT_CATEGORY_HINTS = {
    "preference": [
        "偏好", "喜欢", "不想", "不要", "希望", "要求", "优先", "倾向", "习惯",
        "只讨论", "先", "后",
    ],
    "constraint": [
        "前提", "条件", "固定", "必须", "不会", "仅在", "只有", "限定", "约束",
        "不切换", "不调", "先暂停",
    ],
    "decision": [
        "决定", "采用", "启用", "使用", "改为", "改成", "切换", "选择", "定为",
        "确定",
    ],
    "conclusion": [
        "结论", "没有确认", "无实际 bug", "无实际bug", "存在问题", "成立", "不成立",
        "适合", "局限", "风险", "问题点",
    ],
    "fact": [
        "是一个", "提供", "暴露", "支持", "流程是", "用于", "接口", "架构",
        "双层", "本地", "Python 包",
    ],
}

_KIND_PRIORITY = {
    "preference": 0,
    "constraint": 1,
    "decision":   2,
    "conclusion": 3,
    "fact":       4,
    "general":    5,
}

_LOW_VALUE_RE   = re.compile(
    r"^(好的|收到|明白|可以|没问题|嗯|好|行|继续|已处理|已完成|已返回)[。！! ]*$"
)
_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*•]|[0-9]+[.)、])\s*")
_SPLIT_RE       = re.compile(r"[;\n；]+|(?<=[。！？!?])")
_EN_TOKEN_RE    = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]*")
_CJK_TOKEN_RE   = re.compile(r"[\u4e00-\u9fff]{2,8}")


def _build_category_hints():
    """合并默认词表与外部扩展，返回最终词表。

    外部覆盖方式（两种，互不冲突）：
    - config.CATEGORY_HINTS        完整替换，dict 格式与默认相同
    - config.EXTRA_CATEGORY_HINTS  按类别追加，格式相同，不影响其他类别

    示例：
        from memory_skill_v3 import config
        config.EXTRA_CATEGORY_HINTS = {"preference": ["prefer", "喜好"]}
    """
    base  = getattr(config, "CATEGORY_HINTS", None)
    hints = {k: list(v) for k, v in (base or _DEFAULT_CATEGORY_HINTS).items()}

    extra = getattr(config, "EXTRA_CATEGORY_HINTS", None)
    if extra:
        for kind, words in extra.items():
            hints.setdefault(kind, []).extend(words)

    return hints


def _get_hints():
    """每次调用时重新构建词表，确保 import 后修改 config 能立即生效。
    性能无影响：调用方在同一次 build_memory_items 中只获取一次。
    """
    return _build_category_hints()


def build_memory_items(turn, summary, keywords, raw_q="", raw_a="", max_items=6):
    hints    = _get_hints()
    keywords = _normalize_keywords(keywords)
    candidates = []
    order = 0

    for source, text in (
        ("summary",  summary),
        ("question", raw_q),
        ("answer",   raw_a),
    ):
        for chunk in _extract_chunks(text):
            normalized = _normalize_text(chunk)
            if not normalized:
                continue
            candidates.append({
                "source": source,
                "order":  order,
                "text":   normalized,
            })
            order += 1

    items = []
    seen  = set()
    for candidate in candidates:
        text = candidate["text"]
        if not _is_useful(text, candidate["source"], hints):
            continue

        dedupe_key = _dedupe_key(text)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        kind          = _classify(text, hints)
        item_keywords = _extract_keywords(text, keywords, kind, hints)
        items.append({
            "turn":      turn,
            "summary":   text,
            "keywords":  item_keywords,
            "kind":      kind,
            "_priority": _KIND_PRIORITY[kind],
            "_order":    candidate["order"],
        })

    if not items:
        fallback = _fallback_text(summary, raw_q, raw_a)
        if fallback:
            items.append({
                "turn":      turn,
                "summary":   fallback,
                "keywords":  _extract_keywords(fallback, keywords, "general", hints),
                "kind":      "general",
                "_priority": _KIND_PRIORITY["general"],
                "_order":    0,
            })

    # 先按语义优先级筛选，再恢复到原始出现顺序，避免超长内容把低价值句子全塞进去。
    ranked  = sorted(items, key=lambda item: (item["_priority"], item["_order"]))
    limited = ranked[:max_items]
    limited.sort(key=lambda item: item["_order"])

    for idx, item in enumerate(limited):
        item["item_index"] = idx
        item.pop("_priority", None)
        item.pop("_order",    None)

    return limited


def _extract_chunks(text):
    if not text:
        return []

    lines = []
    for raw_line in str(text).splitlines():
        line = _LIST_PREFIX_RE.sub("", raw_line.strip())
        if line:
            lines.append(line)

    chunks = []
    for line in lines or [str(text)]:
        for part in _SPLIT_RE.split(line):
            part = part.strip(" \t\r\n-—:：,，")
            if not part:
                continue
            if "，" in part and _count_hits(part, None) >= 2:
                sub_parts = [p.strip(" ，,") for p in re.split(r"[，,]", part)]
                chunks.extend([p for p in sub_parts if p])
            else:
                chunks.append(part)

    return chunks


def _normalize_text(text):
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = text.strip("，,；;。.!！？ ")
    return text


def _is_useful(text, source, hints):
    if len(text) < 6:
        return False
    if _LOW_VALUE_RE.match(text):
        return False
    if _count_hits(text, hints) > 0:
        return True
    if source == "summary":
        return len(text) >= 12
    if source == "question":
        return any(t in text for t in ("我", "用户", "不要", "希望", "偏好", "要求", "前提"))
    if source == "answer":
        return any(t in text for t in ("建议", "结论", "适合", "不适合", "存在", "没有", "需要", "应"))
    return False


def _count_hits(text, hints):
    if hints is None:
        hints = _build_category_hints()
    count = 0
    for words in hints.values():
        for w in words:
            if w in text:
                count += 1
    return count


def _classify(text, hints):
    for kind in ("preference", "constraint", "decision", "conclusion", "fact"):
        for hint in hints.get(kind, []):
            if hint in text:
                return kind
    return "general"


def _extract_keywords(text, seed_keywords, kind, hints):
    keywords = []
    seen     = set()

    def add(token):
        token  = str(token).strip()
        if not token:
            return
        lowered = token.lower()
        if lowered in seen:
            return
        seen.add(lowered)
        keywords.append(token)

    for token in seed_keywords:
        add(token)
    add(kind)
    for token in _EN_TOKEN_RE.findall(text):
        add(token)
    for hint in hints.get(kind, []):
        if hint in text:
            add(hint)
    for token in _CJK_TOKEN_RE.findall(text):
        if any(mark in token for mark in (
            "用户", "项目", "模块", "记忆", "回滚", "前提",
            "偏好", "结论", "方案", "局限", "问题",
        )):
            add(token)

    return keywords[:8]


def _normalize_keywords(keywords):
    if keywords is None:
        return []
    if isinstance(keywords, str):
        return [keywords]
    return [str(k) for k in keywords if str(k).strip()]


def _dedupe_key(text):
    return re.sub(r"[\W_]+", "", text).lower()


def _fallback_text(summary, raw_q, raw_a):
    for value in (summary, raw_q, raw_a):
        text = _normalize_text(value)
        if text:
            return text[:200]
    return ""
