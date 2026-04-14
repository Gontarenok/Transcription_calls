"""Gemma text-generation summarizer for 911 call transcripts (structured JSON fields)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import torch
from transformers import pipeline

from model_paths import model_settings

from summarization_llm.outcome_normalize import normalize_outcome_label

PROMPT_VERSION = "911-summarizer-v2"
_DEVICE = 0 if torch.cuda.is_available() else -1


@dataclass
class Summary:
    participants: str | None
    platform: str | None
    topic: str | None
    essence: str | None
    action_result: str | None
    outcome: str | None
    short_summary: str | None
    raw_text: str | None


@lru_cache(maxsize=1)
def get_text_generator():
    gen = pipeline(
        "text-generation",
        model=model_settings.gemma_model_path,
        tokenizer=model_settings.gemma_model_path,
        device=_DEVICE,
        torch_dtype="auto",
    )
    try:
        gc = gen.model.generation_config
        if getattr(gc, "max_length", None) is not None:
            gc.max_length = None
        if hasattr(gc, "do_sample"):
            gc.do_sample = False
    except Exception:
        pass
    return gen


def safe_json_load(raw: str) -> dict[str, Any] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    matches = list(re.finditer(r"\{.*?\}", raw, flags=re.S))
    if not matches:
        return None
    try:
        return json.loads(matches[-1].group(0))
    except Exception:
        return None


def build_prompt(transcript_text: str) -> str:
    return f"""
Ты — ассистент, который делает краткую структурированную выжимку звонка внутренней технической поддержки (911).
Верни ответ СТРОГО в JSON без markdown и без пояснений вокруг.
Контекст: компания «Металл Профиль»; ТП8 — модуль в 1С; 911 — линия техподдержки.
Не додумывай факты. Если в звонке нет смысла (меньше ~10 слов, одни приветствия), для текстовых полей укажи коротко «Суть звонка не ясна», outcome — «не указано».

Схема:
{{
  "participants": "кто с кем разговаривает (если понятно, иначе null)",
  "platform": "канал/система/продукт (если понятно, иначе null)",
  "topic": "краткая тема обращения",
  "essence": "суть проблемы (1-3 предложения)",
  "action_result": "какие действия предприняты/что сделали",
  "outcome": "строго одно из строк (маленькими буквами): помогли | не помогли | в работе | не указано",
  "short_summary": "1–2 предложения итоговой выжимки"
}}

Текст разговора:
{transcript_text}
""".strip()


def parse_summary(raw_llm: str) -> Summary:
    payload = safe_json_load(raw_llm) or {}

    def _s(key: str) -> str | None:
        v = payload.get(key)
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    return Summary(
        participants=_s("participants"),
        platform=_s("platform"),
        topic=_s("topic"),
        essence=_s("essence"),
        action_result=_s("action_result"),
        outcome=normalize_outcome_label(_s("outcome")),
        short_summary=_s("short_summary"),
        raw_text=raw_llm.strip()[:20000] if raw_llm else None,
    )


def summarize_transcript_text(transcript_text: str, *, max_new_tokens: int = 420) -> Summary:
    """Run Gemma on a single transcript and return structured fields."""
    gen = get_text_generator()
    prompt = build_prompt(transcript_text.strip())
    raw = gen(prompt, max_new_tokens=max_new_tokens, do_sample=False, return_full_text=False)[0]["generated_text"]
    return parse_summary(raw)
