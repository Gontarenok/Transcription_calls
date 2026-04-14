from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import torch
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
    get_topic_catalog_entry,
    set_call_status,
)
from db.models import TopicCatalogEntry
from model_paths import model_settings
from classification_rag.catalog_service import init_qdrant

PIPELINE_CODE = "КЦ_CLASSIFICATION"
PROMPT_VERSION = "kc-topic-classifier-v1"
CLASSIFIER_VERSION = "hybrid-rag-2026-03-18"
MAX_CANDIDATES_RETRIEVAL = 25
MAX_CANDIDATES_TO_LLM = 6
MIN_ACCEPT_SCORE = 0.42
DEVICE = 0 if torch.cuda.is_available() else -1

STOPWORDS = {
    "пожалуйста", "скажите", "подскажите", "интересует", "можно", "мне", "нам", "нужно", "хочу",
    "есть", "для", "это", "или", "как", "что", "по", "на", "в", "из", "у", "мы", "вы",
}

# Patterns from stereo-speakers transcripts: "[mm:ss] SPK1: text"
_LINE_PREFIX_RE = re.compile(r"^\s*\[\d{2}:\d{2}\]\s*SPK\d\s*:\s*", flags=re.I)


def normalize_call_text(raw: str) -> str:
    """
    Normalize ASR transcript for retrieval/scoring:
    - remove timestamps/speaker prefixes
    - lowercase, collapse whitespace
    - normalize ё -> е
    """
    if not raw:
        return ""
    lines = []
    for line in raw.splitlines():
        line = _LINE_PREFIX_RE.sub("", line).strip()
        if line:
            lines.append(line)
    text = " ".join(lines)
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def has_existing_deal_signals(text: str) -> bool:
    norm = normalize_text(text)
    signals_existing_deal = [
        "по счету", "по счёту", "счет", "счёт", "оплат", "оплата", "оплатил",
        "по кп", "кп", "по коммерческому",
        "ранее обсуждали", "по заказу", "заказ", "по текущему заказу",
        "пересчитать", "перерасчет", "пересчет", "скорректировать заказ",
        "выставить счет", "выставить счёт",
    ]
    return any(signal in norm for signal in signals_existing_deal)


def seed_active_deal_candidates(text: str, catalog_map: dict[int, TopicCatalogEntry]) -> list[TopicCatalogEntry]:
    if not has_existing_deal_signals(text):
        return []
    return [e for e in catalog_map.values() if e.is_active and normalize_text(e.topic_name) == normalize_text("Активная сделка")]


@dataclass
class Candidate:
    entry_id: int
    topic: str
    subtopic: str
    description: str
    keywords: list[str]
    synonyms: list[str]
    negative_keywords: list[str]
    semantic_score: float
    exact_hits: list[str]
    synonym_hits: list[str]
    negative_hits: list[str]
    lexical_score: float
    scenario_score: float
    final_score: float


def normalize_text(text: str) -> str:
    return re.sub(r"[^а-яёa-z0-9\s]", " ", (text or "").lower())


def tokenize(text: str) -> list[str]:
    return [token for token in normalize_text(text).split() if token and token not in STOPWORDS]


def safe_json_load(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
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


def normalize_list(value: str | None) -> list[str]:
    if not value:
        return []
    prepared = value.replace(";", "\n")
    return [item.strip() for item in prepared.splitlines() if item.strip()]


def embed_text(embedder: SentenceTransformer, text: str) -> list[float]:
    vec = embedder.encode([text], convert_to_numpy=True)[0]
    norm = max(np.linalg.norm(vec), 1e-9)
    return (vec / norm).tolist()


def hit_strength(text: str, phrase: str) -> bool:
    norm_text = normalize_text(text)
    norm_phrase = normalize_text(phrase).strip()
    if not norm_phrase:
        return False
    if len(norm_phrase.split()) > 1:
        return re.search(rf"\b{re.escape(norm_phrase)}\b", norm_text) is not None
    if re.search(rf"\b{re.escape(norm_phrase)}\b", norm_text):
        return True
    return len(norm_phrase) >= 5 and norm_phrase in norm_text


def auto_scenario_score(text: str, subtopic: str, topic: str) -> float:
    norm = normalize_text(text)
    signals_existing_deal = [
        "по счету", "по счёту", "по кп", "по коммерческому", "ранее обсуждали", "по заказу",
        "по текущему заказу", "по объекту", "пересчитать", "добавить в заказ", "скорректировать заказ",
        "продолжим", "повторно", "уже отправляли", "выставить счет", "выставить счёт",
    ]
    signals_general_consult = ["какая продукция", "что есть", "какие у вас", "проконсультировать", "наличие продукции"]

    subtopic_norm = normalize_text(subtopic)
    score = 0.0
    if "актив" in subtopic_norm or "сделк" in subtopic_norm:
        if any(signal in norm for signal in signals_existing_deal):
            score += 0.4
        if any(signal in norm for signal in signals_general_consult):
            score -= 0.15
    if "ваканс" in subtopic_norm and any(token in norm for token in ["работ", "ваканс", "трудоустрой"]):
        score += 0.25
    if "отгруз" in subtopic_norm and any(token in norm for token in ["отгруз", "достав", "самовывоз"]):
        score += 0.25
    if "продукц" in subtopic_norm and any(token in norm for token in ["продукц", "металлочереп", "профлист"]):
        score += 0.15
    return max(-0.25, min(score, 0.5))


def compute_lexical_score(text: str, entry: TopicCatalogEntry) -> tuple[float, list[str], list[str], list[str]]:
    keywords = normalize_list(entry.keywords_text)
    synonyms = normalize_list(entry.synonyms_text)
    negative_keywords = normalize_list(entry.negative_keywords_text)

    exact_hits = [item for item in keywords if hit_strength(text, item)]
    synonym_hits = [item for item in synonyms if hit_strength(text, item)]
    negative_hits = [item for item in negative_keywords if hit_strength(text, item)]

    unique_keywords = len(keywords) or 1
    exact_score = min(1.0, 0.38 * min(len(exact_hits), 3) + 0.12 * math.log1p(max(len(exact_hits) - 3, 0)))
    synonym_score = min(0.45, 0.18 * len(synonym_hits))
    coverage_bonus = min(0.15, len(exact_hits) / unique_keywords)
    negative_penalty = min(0.45, 0.22 * len(negative_hits))

    score = exact_score + synonym_score + coverage_bonus - negative_penalty
    return max(0.0, min(score, 1.0)), exact_hits, synonym_hits, negative_hits


def retrieve_candidates(qdrant: QdrantClient, query_vector: list[float]) -> list[Any]:
    response = qdrant.query_points(
        collection_name=settings.qdrant_collection_topics or "topics_spravochnik",
        query=query_vector,
        limit=MAX_CANDIDATES_RETRIEVAL,
        with_payload=True,
    )
    return list(response.points)


def score_candidates(text: str, hits: list[Any], catalog_map: dict[int, TopicCatalogEntry]) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen_entry_ids: set[int] = set()
    for hit in hits:
        payload = hit.payload or {}
        entry_id = payload.get("entry_id")
        entry = catalog_map.get(int(entry_id)) if entry_id is not None else None
        if not entry or not entry.is_active:
            continue
        seen_entry_ids.add(entry.id)
        lexical_score, exact_hits, synonym_hits, negative_hits = compute_lexical_score(text, entry)
        semantic_score = float(hit.score)
        scenario_score = auto_scenario_score(text, entry.subtopic_name, entry.topic_name)
        final_score = (
            0.45 * semantic_score
            + 0.30 * lexical_score
            + 0.15 * max(scenario_score, 0)
            + 0.10 * (1.0 if len(exact_hits) >= 2 else 0.0)
            - 0.10 * (1.0 if negative_hits else 0.0)
        )
        candidates.append(
            Candidate(
                entry_id=entry.id,
                topic=entry.topic_name,
                subtopic=entry.subtopic_name,
                description=entry.description,
                keywords=normalize_list(entry.keywords_text),
                synonyms=normalize_list(entry.synonyms_text),
                negative_keywords=normalize_list(entry.negative_keywords_text),
                semantic_score=round(semantic_score, 4),
                exact_hits=exact_hits,
                synonym_hits=synonym_hits,
                negative_hits=negative_hits,
                lexical_score=round(lexical_score, 4),
                scenario_score=round(scenario_score, 4),
                final_score=round(max(0.0, min(final_score, 1.0)), 4),
            )
        )
    candidates.sort(key=lambda item: item.final_score, reverse=True)

    # Domain rule: if transcript mentions existing order/invoice/payment,
    # ensure "Активная сделка" subtopics are present as candidates even if Qdrant retrieval missed them.
    for entry in seed_active_deal_candidates(text, catalog_map):
        if entry.id in seen_entry_ids:
            continue
        lexical_score, exact_hits, synonym_hits, negative_hits = compute_lexical_score(text, entry)
        semantic_score = 0.0
        scenario_score = auto_scenario_score(text, entry.subtopic_name, entry.topic_name)
        final_score = (
            0.45 * semantic_score
            + 0.30 * lexical_score
            + 0.15 * max(scenario_score, 0)
            + 0.10 * (1.0 if len(exact_hits) >= 2 else 0.0)
            - 0.10 * (1.0 if negative_hits else 0.0)
        )
        candidates.append(
            Candidate(
                entry_id=entry.id,
                topic=entry.topic_name,
                subtopic=entry.subtopic_name,
                description=entry.description,
                keywords=normalize_list(entry.keywords_text),
                synonyms=normalize_list(entry.synonyms_text),
                negative_keywords=normalize_list(entry.negative_keywords_text),
                semantic_score=round(semantic_score, 4),
                exact_hits=exact_hits,
                synonym_hits=synonym_hits,
                negative_hits=negative_hits,
                lexical_score=round(lexical_score, 4),
                scenario_score=round(scenario_score, 4),
                final_score=round(max(0.0, min(final_score, 1.0)), 4),
            )
        )
    candidates.sort(key=lambda item: item.final_score, reverse=True)
    return candidates


def build_prompt(text: str, candidates: list[Candidate]) -> str:
    candidate_lines = []
    for idx, candidate in enumerate(candidates[:MAX_CANDIDATES_TO_LLM], 1):
        candidate_lines.append(
            f"{idx}. id={candidate.entry_id}; тема={candidate.topic}; подтема={candidate.subtopic}; "
            f"описание={candidate.description}; семантика={candidate.semantic_score:.2f}; "
            f"лексика={candidate.lexical_score:.2f}; сценарий={candidate.scenario_score:.2f}; "
            f"сигналы={', '.join(candidate.exact_hits[:4] + candidate.synonym_hits[:3]) or 'нет'}; "
            f"антисигналы={', '.join(candidate.negative_hits[:3]) or 'нет'}"
        )

    return f"""
Ты — классификатор звонков контакт-центра компании «Металл Профиль».
Твоя задача — выбрать РОВНО ОДНУ подтему только из списка кандидатов ниже.
Запрещено придумывать новую тему или новую подтему.
Если ни один кандидат не подходит по смыслу разговора, верни decision="OTHER".

Сначала опирайся на смысл разговора.
Дополнительно учитывай совпавшие сигналы и антисигналы.
Если есть признаки текущего заказа / ранее начатой сделки / обсуждения счета, КП, пересчета, корректировки заказа,
предпочитай специальную подтему активной сделки, а не общую консультацию по продукции.

Верни ответ СТРОГО в JSON без markdown и без пояснений вокруг:
{{
  "decision": "<entry_id (ТОЛЬКО ЧИСЛО) или OTHER>",
  "confidence": <число от 0 до 1>,
  "reason": "краткая причина выбора",
  "evidence": ["короткий фрагмент 1", "короткий фрагмент 2"]
}}

Кандидаты:
{chr(10).join(candidate_lines) if candidate_lines else 'кандидатов нет'}

Текст разговора:
{text}
""".strip()


def choose_result(generator, prompt: str, candidates: list[Candidate]) -> tuple[dict[str, Any], str]:
    raw = generator(
        prompt,
        max_new_tokens=260,
        max_length=4096,
        do_sample=False,
        repetition_penalty=1.05,
        return_full_text=False,
    )[0]["generated_text"]
    payload = safe_json_load(raw) or {}
    allowed = {str(candidate.entry_id) for candidate in candidates[:MAX_CANDIDATES_TO_LLM]}

    decision_raw = payload.get("decision", "OTHER")
    decision = "OTHER"
    if isinstance(decision_raw, (int, float)):
        decision = str(int(decision_raw))
    else:
        d = str(decision_raw or "").strip()
        if d.upper() == "OTHER":
            decision = "OTHER"
        else:
            # Accept variants like "id=15", "15)", "entry_id: 15"
            m = re.search(r"\b(\d{1,10})\b", d)
            if m:
                decision = m.group(1)
            else:
                # Fallback: model may return subtopic text
                d_norm = normalize_text(d).strip()
                for c in candidates[:MAX_CANDIDATES_TO_LLM]:
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


def write_debug_artifacts(
    *,
    debug_dir: str,
    call_id: int,
    transcription_id: int,
    normalized_text: str,
    scored_candidates: list[Candidate],
    shortlist: list[Candidate],
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
        "shortlist": [c.__dict__ for c in shortlist],
        "candidates_top10": [c.__dict__ for c in scored_candidates[:10]],
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


def get_active_transcription(call) -> Any | None:
    for transcription in call.transcriptions:
        if transcription.is_active:
            return transcription
    return None


def main():
    parser = argparse.ArgumentParser(description="Классификация звонков КЦ по теме/подтеме с помощью Hybrid RAG")
    parser.add_argument("--call-type", default="КЦ")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--debug-dir", default=None, help="Папка для debug-артефактов (prompt/shortlist/raw/parsed) по каждому звонку")
    args = parser.parse_args()

    start_ts = time.time()
    db = SessionLocal()
    pipeline_run = create_pipeline_run(
        db,
        started_at=datetime.now(timezone.utc),
        status="RUNNING",
        pipeline_code=PIPELINE_CODE,
    )
    processed = 0
    call_type_code = (args.call_type or "").strip().upper()
    # Canonical code is "КЦ" (Cyrillic), but accept legacy "KЦ"/"KC".
    if call_type_code in {"КЦ", "KC", "KЦ"}:
        call_type_code = "КЦ"

    try:
        catalog_entries = get_active_catalog_entries(db)
        catalog_map = {entry.id: entry for entry in catalog_entries}
        if not catalog_map:
            raise SystemExit("Справочник тем пуст. Сначала выполните classification_rag/sync_topic_catalog.py")

        qdrant = init_qdrant()
        embedder = SentenceTransformer(model_settings.embedding_model_path)
        generator = pipeline(
            "text-generation",
            model=model_settings.gemma_model_path,
            tokenizer=model_settings.gemma_model_path,
            device=DEVICE,
            torch_dtype="auto",
            # model_kwargs={"local_files_only": True},
        )

        calls = get_calls_for_classification(db, call_type_code=call_type_code, limit=args.limit)
        print(f"Found {len(calls)} calls for classification (call_type_code={call_type_code})")
        skipped_no_transcription = 0
        skipped_empty_text = 0
        for call in calls:
            transcription = get_active_transcription(call)
            if not transcription or not transcription.text.strip():
                if not transcription:
                    skipped_no_transcription += 1
                else:
                    skipped_empty_text += 1
                continue

            set_call_status(db, call.id, "CLASSIFYING", error_message=None)
            raw_text = transcription.text.strip()
            text = normalize_call_text(raw_text)
            try:
                query_vector = embed_text(embedder, text)
                hits = retrieve_candidates(qdrant, query_vector)
                candidates = score_candidates(text, hits, catalog_map)
                shortlisted = candidates[:MAX_CANDIDATES_TO_LLM]
                best_pre_llm = shortlisted[0] if shortlisted else None

                # On noisy ASR text lexical score is often low; hard fallback before LLM
                # can over-route almost everything to OTHER.
                # Keep only "no candidates" as hard fallback; let LLM decide OTHER otherwise.
                if not shortlisted:
                    add_call_classification(
                        db,
                        call_id=call.id,
                        transcription_id=transcription.id,
                        catalog_entry_id=None,
                        pipeline_run_id=pipeline_run.id,
                        model_name="hybrid-rag-fallback",
                        embedding_model_name=model_settings.embedding_model_path,
                        prompt_version=PROMPT_VERSION,
                        classifier_version=CLASSIFIER_VERSION,
                        spravochnik_version="db-live",
                        decision_mode="fallback_other",
                        topic_name="Другое",
                        subtopic_name="Другое",
                        confidence=0.0,
                        lexical_score=best_pre_llm.lexical_score if best_pre_llm else None,
                        semantic_score=best_pre_llm.semantic_score if best_pre_llm else None,
                        rerank_score=best_pre_llm.final_score if best_pre_llm else None,
                        reasoning="Недостаточно сигнала по retrieval и lexical scoring.",
                        evidence=[],
                        candidates=[candidate.__dict__ for candidate in candidates[:10]],
                        raw_llm_output=None,
                    )
                    set_call_status(db, call.id, "CLASSIFIED")
                    processed += 1
                    continue

                # Use original transcript for LLM readability, but without timestamps/speaker prefixes.
                llm_text = normalize_call_text(raw_text)
                prompt = build_prompt(llm_text, shortlisted)
                result, raw_llm = choose_result(generator, prompt, shortlisted)
                decision = result["decision"]
                if args.debug_dir:
                    write_debug_artifacts(
                        debug_dir=args.debug_dir,
                        call_id=call.id,
                        transcription_id=transcription.id,
                        normalized_text=text,
                        scored_candidates=candidates,
                        shortlist=shortlisted,
                        prompt=prompt,
                        raw_llm=raw_llm,
                        parsed=result,
                    )

                chosen = None
                if decision != "OTHER":
                    chosen = next((candidate for candidate in shortlisted if str(candidate.entry_id) == decision), None)
                if chosen is None:
                    chosen_topic = "Другое"
                    chosen_subtopic = "Другое"
                    catalog_entry_id = None
                    chosen_conf = result["confidence"] if result["confidence"] is not None else 0.0
                    lexical_score = best_pre_llm.lexical_score if best_pre_llm else None
                    semantic_score = best_pre_llm.semantic_score if best_pre_llm else None
                    rerank_score = best_pre_llm.final_score if best_pre_llm else None
                    decision_mode = "llm_other"
                else:
                    chosen_topic = chosen.topic
                    chosen_subtopic = chosen.subtopic
                    catalog_entry_id = chosen.entry_id
                    chosen_conf = result["confidence"] if result["confidence"] is not None else chosen.final_score
                    lexical_score = chosen.lexical_score
                    semantic_score = chosen.semantic_score
                    rerank_score = chosen.final_score
                    decision_mode = "llm_constrained"

                add_call_classification(
                    db,
                    call_id=call.id,
                    transcription_id=transcription.id,
                    catalog_entry_id=catalog_entry_id,
                    pipeline_run_id=pipeline_run.id,
                    model_name="gemma-3-4b-it",
                    embedding_model_name=model_settings.embedding_model_path,
                    prompt_version=PROMPT_VERSION,
                    classifier_version=CLASSIFIER_VERSION,
                    spravochnik_version="db-live",
                    decision_mode=decision_mode,
                    topic_name=chosen_topic,
                    subtopic_name=chosen_subtopic,
                    confidence=chosen_conf,
                    lexical_score=lexical_score,
                    semantic_score=semantic_score,
                    rerank_score=rerank_score,
                    reasoning=result.get("reason"),
                    evidence=result.get("evidence") or [],
                    candidates=[candidate.__dict__ for candidate in candidates[:10]],
                    raw_llm_output=raw_llm,
                )
                set_call_status(db, call.id, "CLASSIFIED")
                processed += 1
            except Exception as exc:
                set_call_status(db, call.id, "CLASSIFICATION_FAILED", error_message=str(exc))

        print(
            f"Classification done. processed={processed}, "
            f"skipped_no_transcription={skipped_no_transcription}, skipped_empty_text={skipped_empty_text}"
        )
        finish_pipeline_run(
            db,
            pipeline_run_id=pipeline_run.id,
            status="SUCCESS",
            finished_at=datetime.now(timezone.utc),
            processed_calls=processed,
            duration_seconds=int(time.time() - start_ts),
            error_message=None,
        )
    except Exception as exc:
        finish_pipeline_run(
            db,
            pipeline_run_id=pipeline_run.id,
            status="FAILED",
            finished_at=datetime.now(timezone.utc),
            processed_calls=processed,
            duration_seconds=int(time.time() - start_ts),
            error_message=str(exc),
        )
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()