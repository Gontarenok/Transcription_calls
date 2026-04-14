from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import torch
from pathlib import Path
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
from transformers import pipeline

from api_service.config import settings
from db.base import SessionLocal
from db.crud import (
    add_call_classification,
    create_pipeline_run,
    finish_pipeline_run,
    get_active_catalog_entries,
    get_calls_for_classification,
    set_call_status,
)
from db.models import TopicCatalogEntry
from model_paths import model_settings
from classification_rag.catalog_service import init_qdrant

PIPELINE_CODE = "КЦ_CLASSIFICATION"
PROMPT_VERSION = "kc-topic-classifier-v2-legacy-retrieval"
CLASSIFIER_VERSION = "hybrid-rag-legacy-retrieval-2026-03-30"

DEVICE = 0 if torch.cuda.is_available() else -1

# --- Retrieval/scoring scheme ported from classification_rag/old/rag_summary_with_qdrant_final_3.py ---
TOP_K = 8
SHORT_TEXT_LEN = 300

ALPHA_LONG, BETA_LONG = 0.35, 0.65
ALPHA_SHORT, BETA_SHORT = 0.2, 0.8

HARD_KW_RULE = True
HARD_KW_SCORE = 0.85


# Stereo-speakers transcripts pattern: "[mm:ss] SPK1: text"
_LINE_PREFIX_RE = re.compile(r"^\s*\[\d{2}:\d{2}\]\s*SPK\d\s*:\s*", flags=re.I)


def normalize_call_text(raw: str) -> str:
    if not raw:
        return ""
    lines: list[str] = []
    for line in raw.splitlines():
        line = _LINE_PREFIX_RE.sub("", line).strip()
        if line:
            lines.append(line)
    text = " ".join(lines)
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def normalize_text(text: str) -> str:
    return re.sub(r"[^а-яёa-z0-9\s]", " ", (text or "").lower())


def embed_text(embedder: SentenceTransformer, text: str) -> list[float]:
    vec = embedder.encode([text], convert_to_numpy=True)[0]
    norm = max(float(np.linalg.norm(vec)), 1e-9)
    return (vec / norm).tolist()


def normalize_list(value: str | None) -> list[str]:
    if not value:
        return []
    prepared = value.replace(";", "\n")
    return [item.strip() for item in prepared.splitlines() if item.strip()]


def find_keywords(text: str, keywords: list[str]) -> list[str]:
    txt = normalize_text(text)
    found: set[str] = set()
    for kw in keywords:
        kw_n = normalize_text(kw).strip()
        if not kw_n:
            continue
        if re.search(rf"\b{re.escape(kw_n)}\b", txt):
            found.add(kw)
        elif len(kw_n) >= 5 and kw_n in txt:
            found.add(kw)
    return list(found)


def keyword_score(found: int, total: int) -> float:
    if total == 0 or found == 0:
        return 0.0
    ratio = found / total
    if ratio >= 0.5:
        return 1.0
    if ratio >= 0.3:
        return 0.85
    if ratio >= 0.15:
        return 0.65
    return 0.4


@dataclass
class Candidate:
    entry_id: int
    topic: str
    subtopic: str
    description: str
    raw_score: float
    kw_found: list[str]
    syn_found: list[str]
    kw_signal: float
    final_score: float

    # for DB/debug parity with current pipeline fields
    semantic_score: float
    lexical_score: float
    rerank_score: float


def final_score(raw_score: float, kw_signal: float, *, alpha: float, beta: float) -> float:
    score = alpha * raw_score + beta * kw_signal
    if HARD_KW_RULE and kw_signal > 0:
        score = max(score, HARD_KW_SCORE)
    return round(float(score), 4)


def no_signal(candidates: list[Candidate]) -> bool:
    return all(c.kw_signal == 0 and c.raw_score < 0.3 for c in candidates)


def retrieve_candidates(qdrant: QdrantClient, query_vector: list[float]) -> list[Any]:
    response = qdrant.query_points(
        collection_name=settings.qdrant_collection_topics or "topics_spravochnik",
        query=query_vector,
        limit=TOP_K,
        with_payload=True,
    )
    return list(response.points)


def score_candidates_legacy(text: str, hits: list[Any], catalog_map: dict[int, TopicCatalogEntry]) -> list[Candidate]:
    is_short = len(text) < SHORT_TEXT_LEN
    alpha, beta = (ALPHA_SHORT, BETA_SHORT) if is_short else (ALPHA_LONG, BETA_LONG)

    candidates: list[Candidate] = []
    for hit in hits:
        payload = hit.payload or {}
        entry_id = payload.get("entry_id")
        if entry_id is None:
            continue
        entry = catalog_map.get(int(entry_id))
        if not entry or not entry.is_active:
            continue

        keywords = normalize_list(entry.keywords_text)
        synonyms = normalize_list(entry.synonyms_text)
        kw_found = find_keywords(text, keywords)
        syn_found = find_keywords(text, synonyms)

        # Legacy scheme primarily relies on keywords. If there are no keyword hits,
        # allow synonyms to create a weak positive signal (helps when catalog was enriched by synonyms).
        if kw_found:
            kw_signal = keyword_score(len(kw_found), len(keywords))
        elif syn_found:
            kw_signal = 0.4
        else:
            kw_signal = 0.0

        raw_score = float(hit.score)
        fs = final_score(raw_score, kw_signal, alpha=alpha, beta=beta)

        candidates.append(
            Candidate(
                entry_id=entry.id,
                topic=entry.topic_name,
                subtopic=entry.subtopic_name,
                description=entry.description,
                raw_score=round(raw_score, 4),
                kw_found=kw_found,
                syn_found=syn_found,
                kw_signal=round(kw_signal, 4),
                final_score=fs,
                semantic_score=round(raw_score, 4),
                lexical_score=round(kw_signal, 4),
                rerank_score=fs,
            )
        )

    candidates.sort(key=lambda x: x.final_score, reverse=True)
    return candidates


def safe_json_load(raw: str) -> dict[str, Any] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    # LLM can prepend/append garbage; prefer the last JSON object in output.
    matches = list(re.finditer(r"\{.*?\}", raw, flags=re.S))
    if not matches:
        return None
    try:
        return json.loads(matches[-1].group(0))
    except json.JSONDecodeError:
        return None


def normalize_confidence(value: Any) -> float | None:
    try:
        score = float(value)
    except Exception:
        return None
    if score > 1:
        score = score / 100.0
    return max(0.0, min(score, 1.0))


def build_prompt(text: str, candidates: list[Candidate]) -> str:
    lines = []
    for idx, c in enumerate(candidates[:6], 1):
        signals = ", ".join((c.kw_found[:4] + c.syn_found[:3])) or "нет"
        lines.append(
            f'{idx}. id={c.entry_id}; тема="{c.topic}"; подтема="{c.subtopic}"; '
            f"описание={c.description}; score={c.final_score:.2f}; sem={c.semantic_score:.2f}; kw={c.lexical_score:.2f}; сигналы={signals}"
        )

    return f"""
Ты — классификатор звонков контакт-центра компании «Металл Профиль».
Твоя задача — выбрать РОВНО ОДНУ подтему только из списка кандидатов ниже.
Запрещено придумывать новую тему или новую подтему.
Если ни один кандидат не подходит по смыслу разговора, верни decision="OTHER".

Верни ответ СТРОГО в JSON без markdown и без пояснений вокруг:
{{
  "decision": "<entry_id (ТОЛЬКО ЧИСЛО) или OTHER>",
  "confidence": <число от 0 до 1>,
  "reason": "краткая причина выбора",
  "evidence": ["короткий фрагмент 1", "короткий фрагмент 2"]
}}

Кандидаты:
{chr(10).join(lines) if lines else "кандидатов нет"}

Текст разговора:
{text}
""".strip()


def choose_result(generator, prompt: str, candidates: list[Candidate]) -> tuple[dict[str, Any], str]:
    raw = generator(
        prompt,
        max_new_tokens=260,
        do_sample=False,
        repetition_penalty=1.05,
        return_full_text=False,
    )[0]["generated_text"]
    payload = safe_json_load(raw) or {}

    allowed = {str(c.entry_id) for c in candidates[:6]}
    decision_raw = payload.get("decision", "OTHER")
    decision = "OTHER"

    if isinstance(decision_raw, (int, float)):
        decision = str(int(decision_raw))
    else:
        d = str(decision_raw or "").strip()
        if d.upper() == "OTHER":
            decision = "OTHER"
        else:
            m = re.search(r"\b(\d{1,10})\b", d)
            if m:
                decision = m.group(1)
            else:
                # Fallback: model may output subtopic text
                d_norm = normalize_text(d).strip()
                for c in candidates[:6]:
                    if normalize_text(c.subtopic).strip() == d_norm:
                        decision = str(c.entry_id)
                        break

    if decision != "OTHER" and decision not in allowed:
        decision = "OTHER"

    result = {
        "decision": decision,
        "confidence": normalize_confidence(payload.get("confidence")),
        "reason": str(payload.get("reason", "")).strip() or None,
        "evidence": payload.get("evidence") if isinstance(payload.get("evidence"), list) else [],
    }
    return result, raw


def get_active_transcription(call) -> Any | None:
    for transcription in call.transcriptions:
        if transcription.is_active:
            return transcription
    return None


def write_debug_artifacts(
    *,
    debug_dir: str,
    call_id: int,
    transcription_id: int,
    normalized_text: str,
    candidates: list[Candidate],
    prompt: str,
    raw_llm: str | None,
    parsed: dict[str, Any] | None,
):
    os.makedirs(debug_dir, exist_ok=True)
    safe_name = f"call_{call_id}_tr_{transcription_id}"
    payload = {
        "call_id": call_id,
        "transcription_id": transcription_id,
        "normalized_text": normalized_text,
        "candidates": [asdict(c) for c in candidates],
        "prompt": prompt,
        "raw_llm_output": raw_llm,
        "parsed": parsed,
    }
    with open(os.path.join(debug_dir, f"{safe_name}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(os.path.join(debug_dir, f"{safe_name}.prompt.txt"), "w", encoding="utf-8") as f:
        f.write(prompt)
    if raw_llm:
        with open(os.path.join(debug_dir, f"{safe_name}.raw.txt"), "w", encoding="utf-8") as f:
            f.write(raw_llm)


def init_generator():
    gen = pipeline(
        "text-generation",
        model=model_settings.gemma_model_path,
        tokenizer=model_settings.gemma_model_path,
        device=DEVICE,
        torch_dtype="auto",
    )
    # Reduce noisy warnings and prevent accidental truncation from model defaults.
    try:
        gc = gen.model.generation_config
        if getattr(gc, "max_length", None) is not None:
            gc.max_length = None
        for name in ("temperature", "top_p", "top_k"):
            if hasattr(gc, name):
                setattr(gc, name, None)
        if hasattr(gc, "do_sample"):
            gc.do_sample = False
    except Exception:
        pass
    return gen


def default_run_dir(pipeline_run_id: int) -> str:
    base = Path("output_audio_benchmark") / "classification_v2"
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return str(base / f"{ts}_run{pipeline_run_id}")


def main():
    parser = argparse.ArgumentParser(description="Классификация звонков КЦ по теме/подтеме (legacy retrieval scheme + new DB pipeline)")
    parser.add_argument("--call-type", default="КЦ")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--debug-dir", default=None, help="Папка для debug-артефактов по каждому звонку (если не задано — создаётся автоматически)")
    args = parser.parse_args()

    start_ts = time.time()
    db = SessionLocal()
    pipeline_run = create_pipeline_run(
        db,
        started_at=datetime.now(timezone.utc),
        status="RUNNING",
        pipeline_code=PIPELINE_CODE,
    )
    debug_dir = args.debug_dir or default_run_dir(pipeline_run.id)

    processed = 0
    skipped_no_transcription = 0
    skipped_empty_text = 0

    call_type_code = (args.call_type or "").strip().upper()
    if call_type_code in {"КЦ", "KC", "KЦ"}:
        call_type_code = "КЦ"

    try:
        catalog_entries = get_active_catalog_entries(db)
        catalog_map = {entry.id: entry for entry in catalog_entries}
        if not catalog_map:
            raise SystemExit("Справочник тем пуст. Сначала выполните classification_rag/sync_topic_catalog.py")

        qdrant = init_qdrant()
        embedder = SentenceTransformer(model_settings.embedding_model_path)
        generator = init_generator()

        calls = get_calls_for_classification(db, call_type_code=call_type_code, limit=args.limit)
        print(f"Found {len(calls)} calls for classification (call_type_code={call_type_code})")

        for call in calls:
            transcription = get_active_transcription(call)
            if not transcription or not (transcription.text or "").strip():
                if not transcription:
                    skipped_no_transcription += 1
                else:
                    skipped_empty_text += 1
                continue

            set_call_status(db, call.id, "CLASSIFYING", error_message=None)
            raw_text = transcription.text.strip()
            norm_text = normalize_call_text(raw_text)

            try:
                query_vector = embed_text(embedder, norm_text)
                hits = retrieve_candidates(qdrant, query_vector)
                candidates = score_candidates_legacy(norm_text, hits, catalog_map)

                if not candidates or no_signal(candidates):
                    print(f"[call={call.id}] -> OTHER (no_signal)")
                    add_call_classification(
                        db,
                        call_id=call.id,
                        transcription_id=transcription.id,
                        catalog_entry_id=None,
                        pipeline_run_id=pipeline_run.id,
                        model_name="legacy-retrieval-fallback",
                        embedding_model_name=model_settings.embedding_model_path,
                        prompt_version=PROMPT_VERSION,
                        classifier_version=CLASSIFIER_VERSION,
                        spravochnik_version="db-live",
                        decision_mode="no_signal_other",
                        topic_name="Другое",
                        subtopic_name="Другое",
                        confidence=0.0,
                        lexical_score=None,
                        semantic_score=None,
                        rerank_score=None,
                        reasoning="Недостаточно сигнала по семантике и ключевым словам (legacy no_signal).",
                        evidence=[],
                        candidates=[asdict(c) for c in candidates],
                        raw_llm_output=None,
                    )
                    set_call_status(db, call.id, "CLASSIFIED")
                    processed += 1
                    continue

                # For LLM keep readable text (without timestamps), but not over-normalized.
                llm_text = normalize_call_text(raw_text)
                prompt = build_prompt(llm_text, candidates)
                result, raw_llm = choose_result(generator, prompt, candidates)

                decision = result["decision"]
                chosen: Candidate | None = None
                if decision != "OTHER":
                    chosen = next((c for c in candidates[:6] if str(c.entry_id) == str(decision)), None)

                if debug_dir:
                    write_debug_artifacts(
                        debug_dir=debug_dir,
                        call_id=call.id,
                        transcription_id=transcription.id,
                        normalized_text=norm_text,
                        candidates=candidates,
                        prompt=prompt,
                        raw_llm=raw_llm,
                        parsed=result,
                    )

                if not chosen:
                    # If parsing failed and no reason, store a short fallback for UX/debug.
                    reason = result.get("reason") or None
                    if not reason and raw_llm:
                        reason = raw_llm.strip()[:500]
                    print(f"[call={call.id}] -> OTHER (llm_other) conf={float(result.get('confidence') or 0.0):.2f}")
                    add_call_classification(
                        db,
                        call_id=call.id,
                        transcription_id=transcription.id,
                        catalog_entry_id=None,
                        pipeline_run_id=pipeline_run.id,
                        model_name=model_settings.gemma_model_path,
                        embedding_model_name=model_settings.embedding_model_path,
                        prompt_version=PROMPT_VERSION,
                        classifier_version=CLASSIFIER_VERSION,
                        spravochnik_version="db-live",
                        decision_mode="llm_other",
                        topic_name="Другое",
                        subtopic_name="Другое",
                        confidence=float(result.get("confidence") or 0.0),
                        lexical_score=candidates[0].lexical_score if candidates else None,
                        semantic_score=candidates[0].semantic_score if candidates else None,
                        rerank_score=candidates[0].rerank_score if candidates else None,
                        reasoning=reason,
                        evidence=result.get("evidence") or [],
                        candidates=[asdict(c) for c in candidates],
                        raw_llm_output=raw_llm,
                    )
                    set_call_status(db, call.id, "CLASSIFIED")
                    processed += 1
                    continue

                reason = result.get("reason") or None
                if not reason and raw_llm:
                    reason = raw_llm.strip()[:500]
                print(
                    f"[call={call.id}] -> {chosen.topic} / {chosen.subtopic} "
                    f"conf={float(result.get('confidence') or 0.0):.2f} score={chosen.final_score:.2f} sem={chosen.semantic_score:.2f} kw={chosen.lexical_score:.2f}"
                )
                add_call_classification(
                    db,
                    call_id=call.id,
                    transcription_id=transcription.id,
                    catalog_entry_id=chosen.entry_id,
                    pipeline_run_id=pipeline_run.id,
                    model_name=model_settings.gemma_model_path,
                    embedding_model_name=model_settings.embedding_model_path,
                    prompt_version=PROMPT_VERSION,
                    classifier_version=CLASSIFIER_VERSION,
                    spravochnik_version="db-live",
                    decision_mode="llm_choice",
                    topic_name=chosen.topic,
                    subtopic_name=chosen.subtopic,
                    confidence=float(result.get("confidence") or 0.0),
                    lexical_score=chosen.lexical_score,
                    semantic_score=chosen.semantic_score,
                    rerank_score=chosen.rerank_score,
                    reasoning=reason,
                    evidence=result.get("evidence") or [],
                    candidates=[asdict(c) for c in candidates],
                    raw_llm_output=raw_llm,
                )
                set_call_status(db, call.id, "CLASSIFIED")
                processed += 1

            except Exception as e:
                set_call_status(db, call.id, "CLASSIFICATION_FAILED", error_message=str(e))

        finish_pipeline_run(
            db,
            pipeline_run_id=pipeline_run.id,
            status="FINISHED",
            finished_at=datetime.now(timezone.utc),
            processed_calls=processed,
            duration_seconds=int(time.time() - start_ts),
            error_message=None,
        )
    finally:
        db.close()

    elapsed = time.time() - start_ts
    print(f"Done. processed={processed}, skipped_no_transcription={skipped_no_transcription}, skipped_empty_text={skipped_empty_text}, elapsed={elapsed:.1f}s")


if __name__ == "__main__":
    main()

