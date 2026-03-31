import json
import re

from . import api


SUMMARY_SYSTEM_PROMPT = """你是对话记忆整理器。
请从本轮问答中提炼未来仍有价值的稳定信息，只保留：
1. 用户长期偏好
2. 明确约束或前提
3. 已确认的决定
4. 对项目、代码或任务的重要结论

不要保留寒暄、一次性细节、临时状态或纯过程性废话。
只输出 JSON，格式必须是：
{"summary":"一段可读摘要，可包含多个要点，后续系统会自动拆分","keywords":["关键词1","关键词2"]}"""


def run_chat_turn(
    user_id,
    session_id,
    turn,
    user_text,
    call_llm,
    system_prompt="",
    extra_messages=None,
    summary_callable=None,
    flush_after_turn=False,
):
    memory_text = api.remember(
        user_id=user_id,
        session_id=session_id,
        turn=turn,
        query_text=user_text,
    )

    messages = build_chat_messages(
        user_text=user_text,
        system_prompt=system_prompt,
        memory_text=memory_text,
        extra_messages=extra_messages,
    )
    answer = _extract_text(call_llm(messages))

    summary_pack = summarize_turn(
        user_text=user_text,
        answer_text=answer,
        call_llm=summary_callable or call_llm,
        memory_text=memory_text,
    )

    mem_ids = api.memorize(
        user_id=user_id,
        session_id=session_id,
        turn=turn,
        summary=summary_pack["summary"],
        keywords=summary_pack["keywords"],
        raw_q=user_text,
        raw_a=answer,
    )

    flush_stats = None
    if flush_after_turn:
        flush_stats = api.flush(user_id, session_id)

    return {
        "turn": turn,
        "answer": answer,
        "memory_text": memory_text,
        "summary": summary_pack["summary"],
        "keywords": summary_pack["keywords"],
        "mem_ids": mem_ids,
        "flush_stats": flush_stats,
        "messages": messages,
    }


class MemoryChatSession:
    def __init__(
        self,
        user_id,
        session_id,
        call_llm,
        system_prompt="",
        summary_callable=None,
        start_turn=1,
        auto_setup=False,
    ):
        self.user_id = user_id
        self.session_id = session_id
        self.call_llm = call_llm
        self.summary_callable = summary_callable
        self.system_prompt = system_prompt
        self.turn = start_turn

        if auto_setup:
            api.setup()

    def chat(self, user_text, extra_messages=None, flush_after_turn=False):
        result = run_chat_turn(
            user_id=self.user_id,
            session_id=self.session_id,
            turn=self.turn,
            user_text=user_text,
            call_llm=self.call_llm,
            system_prompt=self.system_prompt,
            extra_messages=extra_messages,
            summary_callable=self.summary_callable,
            flush_after_turn=flush_after_turn,
        )
        self.turn += 1
        return result

    def flush(self):
        return api.flush(self.user_id, self.session_id)

    def stats(self):
        """返回当前用户的记忆统计信息，等价于 get_stats(user_id)。"""
        return api.get_stats(self.user_id)


def build_chat_messages(user_text, system_prompt="", memory_text="", extra_messages=None):
    # 将记忆上下文合并进 system prompt，而不是作为第二条 system 消息。
    # OpenAI / Claude 均只支持一条 system 消息；多条 system 消息行为未定义，
    # 部分模型会静默忽略第二条，导致记忆完全失效。
    if memory_text:
        merged_system = f"{system_prompt}\n\n{memory_text}".strip() if system_prompt else memory_text
    else:
        merged_system = system_prompt

    messages = []
    if merged_system:
        messages.append({"role": "system", "content": merged_system})
    if extra_messages:
        messages.extend(extra_messages)
    messages.append({"role": "user", "content": user_text})
    return messages


def summarize_turn(user_text, answer_text, call_llm, memory_text=""):
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"用户问题：\n{user_text}\n\n"
                f"助手回答：\n{answer_text}\n\n"
                f"相关记忆上下文（如有）：\n{memory_text or '无'}"
            ),
        },
    ]
    raw_result = call_llm(messages)
    result_text = _extract_text(raw_result)
    parsed = _parse_summary_json(result_text)
    if parsed is not None:
        return parsed
    return _fallback_summary_pack(user_text, answer_text)


def _extract_text(result):
    if isinstance(result, str):
        return result.strip()

    if isinstance(result, dict):
        for key in ("content", "text", "answer", "output", "output_text"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                text = first.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()

    output_text = getattr(result, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    content = getattr(result, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()

    return str(result).strip()


def _parse_summary_json(text):
    if not text:
        return None

    candidates = [text.strip()]

    match = re.search(r"\{.*\}", text, re.S)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue

        summary = str(data.get("summary", "")).strip()
        keywords = data.get("keywords", [])
        if not summary:
            continue
        if isinstance(keywords, str):
            keywords = [keywords]
        elif not isinstance(keywords, list):
            keywords = []
        keywords = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
        return {
            "summary": summary,
            "keywords": keywords[:8],
        }

    return None


def _fallback_summary_pack(user_text, answer_text):
    q = _first_sentence(user_text, 120)
    a = _first_sentence(answer_text, 180)
    parts = []
    if q:
        parts.append(f"用户问题：{q}")
    if a:
        parts.append(f"回答结论：{a}")

    summary = "；".join(parts)[:300].strip("；")
    if not summary:
        summary = "本轮对话完成，未提取到结构化摘要。"

    keywords = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_.+-]*|[\u4e00-\u9fff]{2,8}", summary):
        if token not in keywords:
            keywords.append(token)

    return {
        "summary": summary,
        "keywords": keywords[:8],
    }


def _first_sentence(text, limit):
    value = str(text or "").strip()
    if not value:
        return ""
    first = re.split(r"[\r\n。！？!?]", value, maxsplit=1)[0].strip()
    return first[:limit]
