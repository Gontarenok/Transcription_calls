#!/usr/bin/env python3
# src/convert_spravochnik.py
"""
Парсинг справочника из простого текстового формата в JSON.

Ожидаемый формат входного файла (пример):
Тема: Общие вопросы
Подтема: Дилер
Ключевые слова: заявка на дилерство; стать дилером

Тема: Общие вопросы
Подтема: Трудоустройство
Ключевые слова: наличие вакансии; интересует вакансия; интересует работа

--- и т.д. ---
"""

import re
import json
from pathlib import Path
from typing import List, Dict

# --- Параметры ---
BASE_DIR = Path(__file__).resolve().parent
INPUT_PATH = BASE_DIR / "reference_topics.txt"
OUTPUT_PATH = BASE_DIR / "spravochnik.json"
# -------------------

def parse_block(block: str) -> Dict:
    """
    Парсит один блок вида:
    Тема: ...
    Подтема: ...
    Ключевые слова: a; b; c
    (description - опционально)
    """
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    d = {"topic": None, "subtopic": None, "keywords": [], "description": ""}
    for ln in lines:
        # Нормализуем пробелы и двоеточия
        if ln.lower().startswith("тема"):
            # Тема: value
            parts = ln.split(":", 1)
            d["topic"] = parts[1].strip() if len(parts) > 1 else ""
        elif ln.lower().startswith("подтема"):
            parts = ln.split(":", 1)
            d["subtopic"] = parts[1].strip() if len(parts) > 1 else ""
        elif ln.lower().startswith("ключевые") or ln.lower().startswith("ключевые слова"):
            parts = ln.split(":", 1)
            kws = parts[1].strip() if len(parts) > 1 else ""
            # Разделители - ; , или |
            raw = re.split(r"[;,\|]", kws)
            # Очистка и фильтр пустых
            d["keywords"] = [k.strip() for k in raw if k.strip()]
        elif ln.lower().startswith("описание") or ln.lower().startswith("description"):
            parts = ln.split(":", 1)
            d["description"] = parts[1].strip() if len(parts) > 1 else ""
        else:
            # Если строка не попала под шаблон, добавляем в описание
            if d["description"]:
                d["description"] += " " + ln
            else:
                d["description"] = ln
    # Fallbacks: если нет подтемы/темы — помечаем 'Не указано'
    if not d["topic"]:
        d["topic"] = "Не указано"
    if not d["subtopic"]:
        d["subtopic"] = "Не указано"
    return d

def create_doc_text(record: Dict) -> str:
    """Формируем одно поле doc_text для эмбеддинга"""
    topic = record.get("topic", "")
    subtopic = record.get("subtopic", "")
    keywords = record.get("keywords", [])
    description = record.get("description", "")
    doc_text = f"{topic} | {subtopic} : {' '.join(keywords)} {description}".strip()
    return " ".join(doc_text.split())  # убираем лишние пробелы

def convert(input_path: Path, output_path: Path):
    raw = input_path.read_text(encoding="utf8")
    # Разделяем блоки по пустой строке (два перевода строки и больше)
    blocks = re.split(r"\n\s*\n", raw.strip())
    records = []
    for b in blocks:
        if not b.strip():
            continue
        rec = parse_block(b)
        rec["doc_text"] = create_doc_text(rec)
        records.append(rec)
    # Сохраняем
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf8")
    print(f"Converted {len(records)} records -> {output_path}")

if __name__ == "__main__":
    if not INPUT_PATH.exists():
        print(f"Текстовый справочник не найден в папке: {INPUT_PATH}.")
    else:
        convert(INPUT_PATH, OUTPUT_PATH)