"""
CLI: создать задачу в Work с текстом из файла и вложением Excel.

Переменные окружения: WORK_PATH, WORK_USER, WORK_PASS и опционально WORK_* (см. work_client).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from summarization_llm.work_client import upload_weekly_911_task


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--text-file", type=str, required=True, help="UTF-8 текст задачи")
    p.add_argument("--excel", type=str, required=True, help="Путь к .xlsx")
    args = p.parse_args()

    text_path = Path(args.text_file)
    if not text_path.is_file():
        print("Нет файла текста:", text_path, file=sys.stderr)
        sys.exit(1)
    task_text = text_path.read_text(encoding="utf-8")
    upload_weekly_911_task(task_text=task_text, excel_path=args.excel)
    print("OK")


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parents[1])
    main()
