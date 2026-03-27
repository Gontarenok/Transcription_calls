#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
RAG v3.0 — Hybrid Topic Classification (Senior-grade)

Принципы:
- Qdrant = semantic candidate retriever
- Python = scoring + бизнес-правила
- LLM = финальный выбор подтемы ИЛИ "Другое"
"""

import os
import re
import json
import torch
import numpy as np
from typing import List, Dict
from datetime import datetime

from sentence_transformers import SentenceTransformer
from transformers import pipeline
from qdrant_client import QdrantClient
from qdrant_client.models import ScoredPoint

# ================= CONFIG =================

EMBED_MODEL_PATH = "../models/ai-forever--sbert_large_nlu_ru"
GEN_MODEL_PATH = "../models/gemma/gemma-3-4b-it"

SPRAVOCHNIK_PATH = "spravochnik.json"

QDRANT_HOST = "qdrant.metallprofil.ru"
QDRANT_PORT = 443
QDRANT_API_KEY = "akU2ofFNp4ostaQnfUw8tEdLghXGDUSH"
COLLECTION_NAME = "topics_spravochnik"

# INPUT_DIR = "transcripts"
INPUT_DIR = "../output_audio_contact_center/2025-09-28_15-23-08"
OUTPUT_DIR = "output_summary"

TOP_K = 8
SHORT_TEXT_LEN = 300

ALPHA_LONG, BETA_LONG = 0.35, 0.65
ALPHA_SHORT, BETA_SHORT = 0.2, 0.8

HARD_KW_RULE = True
HARD_KW_SCORE = 0.85

DEVICE = 0 if torch.cuda.is_available() else -1
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ================= INIT =================

def init_models():
    qdrant = QdrantClient(
        host=QDRANT_HOST,
        port=QDRANT_PORT,
        api_key=QDRANT_API_KEY
    )

    embedder = SentenceTransformer(EMBED_MODEL_PATH)

    generator = pipeline(
        "text-generation",
        model=GEN_MODEL_PATH,
        tokenizer=GEN_MODEL_PATH,
        device=DEVICE,
        torch_dtype="auto",
        model_kwargs={"local_files_only": True}
    )

    return qdrant, embedder, generator


def load_spravochnik(path: str) -> Dict[str, str]:
    data = json.load(open(path, encoding="utf-8"))
    return {r["subtopic"]: r["topic"] for r in data}

# ================= UTILS =================

def normalize(text: str) -> str:
    return re.sub(r"[^а-яёa-z0-9\s]", " ", text.lower())


def embed_text(model, text: str) -> List[float]:
    vec = model.encode([text], convert_to_numpy=True)[0]
    return (vec / max(np.linalg.norm(vec), 1e-9)).tolist()

# ================= KEYWORDS =================

def find_keywords(text: str, keywords: List[str]) -> List[str]:
    txt = normalize(text)
    found = set()

    for kw in keywords:
        kw_n = normalize(kw).strip()
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

# ================= SEARCH =================

def hybrid_candidates(
    qdrant,
    query_vec: List[float],
    transcript: str,
    top_k: int
) -> List[Dict]:

    response = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vec,
        limit=top_k,
        with_payload=True
    )

    candidates = []

    for hit in response.points:
        payload = hit.payload or {}
        keywords = payload.get("keywords", [])

        found = find_keywords(transcript, keywords)
        kw_signal = keyword_score(len(found), len(keywords))

        candidates.append({
            "subtopic": payload.get("subtopic"),
            "description": payload.get("description", ""),
            "raw_score": float(hit.score),
            "kw_found": found,
            "kw_signal": kw_signal
        })

    return candidates


def final_score(c: Dict, alpha: float, beta: float) -> float:
    score = alpha * c["raw_score"] + beta * c["kw_signal"]

    if HARD_KW_RULE and c["kw_signal"] > 0:
        score = max(score, HARD_KW_SCORE)

    return round(score, 4)


def no_signal(candidates: List[Dict]) -> bool:
    return all(c["kw_signal"] == 0 and c["raw_score"] < 0.3 for c in candidates)

# ================= PROMPT =================

def build_prompt(transcript: str, candidates: List[Dict]) -> str:
    lines = []
    for i, c in enumerate(candidates[:5], 1):
        lines.append(
            f"{i}. {c['subtopic']} — {c['description']} "
            f"(уверенность системы: {int(c['final_score'] * 100)}%)"
        )

    return f"""
Ты — аналитик телефонных звонков контакт-центра.

Сформируй отчёт строго по шаблону:
- Участники:
- Суть:
- Подтема: (только название подтемы, без ее описания)
- Вероятность определения:
- Действие в результате диалога:
- Итог: (помогли / не помогли / в работе / не указано)
- Краткое саммари (до 5 предложений)

Возможные подтемы:
{chr(10).join(lines)}

Если ни одна подтема не подходит — выбери «Другое».

Текст разговора:
{transcript}

Начинай ответ сразу.
""".strip()

# ================= PIPELINE =================

def process_file(
    fname: str,
    text: str,
    qdrant,
    embedder,
    generator,
    spravo_index: Dict[str, str],
    outdir: str
):
    query_vec = embed_text(embedder, text)
    candidates = hybrid_candidates(qdrant, query_vec, text, TOP_K)

    if not candidates or no_signal(candidates):
        topic = subtopic = "Другое"
        confidence = 0
        prompt = build_prompt(text, [])
    else:
        is_short = len(text) < SHORT_TEXT_LEN
        alpha, beta = (ALPHA_SHORT, BETA_SHORT) if is_short else (ALPHA_LONG, BETA_LONG)

        for c in candidates:
            c["final_score"] = final_score(c, alpha, beta)

        candidates.sort(key=lambda x: x["final_score"], reverse=True)

        prompt = build_prompt(text, candidates)
        subtopic = candidates[0]["subtopic"]
        topic = spravo_index.get(subtopic, "Другое")
        confidence = int(candidates[0]["final_score"] * 100)

    result = generator(prompt, max_new_tokens=400, do_sample=False)[0]["generated_text"]

    base = os.path.splitext(fname)[0]

    with open(os.path.join(outdir, base + "_meta.json"), "w", encoding="utf-8") as f:
        json.dump({
            "topic": topic,
            "subtopic": subtopic,
            "confidence": confidence
        }, f, ensure_ascii=False, indent=2)

    with open(os.path.join(outdir, base + "_summary.txt"), "w", encoding="utf-8") as f:
        f.write(result)

# ================= MAIN =================

def main():
    qdrant, embedder, generator = init_models()
    spravo = load_spravochnik(SPRAVOCHNIK_PATH)

    run_dir = os.path.join(OUTPUT_DIR, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
    os.makedirs(run_dir, exist_ok=True)

    for fname in sorted(os.listdir(INPUT_DIR)):
        if not fname.endswith(".txt"):
            continue

        text = open(os.path.join(INPUT_DIR, fname), encoding="utf-8").read().strip()
        if text:
            process_file(fname, text, qdrant, embedder, generator, spravo, run_dir)


if __name__ == "__main__":
    main()