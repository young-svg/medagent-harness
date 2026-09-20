from __future__ import annotations


def summarize_messages(messages: list[dict[str, str]], max_chars: int = 600) -> str:
    text = " | ".join(f"{item['role']}: {item['content']}" for item in messages)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"
