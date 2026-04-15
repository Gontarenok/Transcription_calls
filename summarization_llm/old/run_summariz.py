"""
Исторический оркестратор (файловый поток: transcribe_audio → summarize_gemma → …).

**Прод:** из корня репозитория — ``python run_911_pipeline.py`` (режим ``full`` по умолчанию)
(транскрибация и саммари в PostgreSQL, Excel из БД, задача в Work).

Отдельные скрипты ``transcribe_audio.py``, ``summarize_gemma.py``, … можно запускать вручную
из каталога ``summarization_llm/`` для отладки без БД.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(
        "Для недельного пайплайна 911 с БД выполните из корня проекта:\n"
        f"  cd {root}\n"
        "  python run_911_pipeline.py\n\n"
        "Файловый поток без БД: по очереди запускайте скрипты в summarization_llm/ "
        "(transcribe_audio.py, summarize_gemma.py, export_summary_to_excel.py, build_report.py, upload_work_prod.py).",
        file=sys.stderr,
    )
    sys.exit(2)
