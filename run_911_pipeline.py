"""
Пайплайн 911: режимы ``scan`` | ``transcribe`` | ``summarize`` | ``full`` (по умолчанию).

- **full** — скан → транскрибация → саммари за отчётную неделю → Excel → Work.
- **scan** — только сканирование папки и запись метаданных в БД.
- **transcribe** — только транскрибация уже известных звонков (нужен предварительный scan).
- **summarize** — только саммари + Excel + Work за период (без аудио-шагов).

Примеры: ``python run_911_pipeline.py`` (full), ``python run_911_pipeline.py --mode scan``,
``python run_911_pipeline.py --mode summarize --skip-work``.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from db.base import SessionLocal
from db.crud import (
    count_911_calls_in_range,
    create_pipeline_run,
    create_weekly_911_report,
    finalize_weekly_911_report,
    finish_pipeline_run,
    list_911_calls_summarized_in_range,
)
from db.models import PipelineRun
from jobs.summarize_911 import run_summarize_911_batch
from summarization_llm.excel_from_db import export_911_calls_to_excel
from summarization_llm.report_week_range import period_to_utc_half_open, previous_iso_week_mon_sun
from summarization_llm.weekly_stats import aggregate_outcomes_for_calls, build_weekly_task_text
from summarization_llm.work_client import upload_weekly_911_task

N911_ROOT = r"C:\Audio_share\Night"
N911_MODEL = "medium"
N911_LIMIT = 10000
N911_RECURSIVE = False
PIPELINE_CODE_TRANSCRIBE = "911"
PIPELINE_CODE_WEEKLY = "911_WEEKLY"

os.environ["PYTHONUTF8"] = "1"


def create_transcribe_pipeline_run() -> int:
    db = SessionLocal()
    try:
        run = create_pipeline_run(
            db,
            started_at=datetime.now(timezone.utc),
            status="RUNNING",
            pipeline_code=PIPELINE_CODE_TRANSCRIBE,
        )
        return run.id
    finally:
        db.close()


def finalize_transcribe_pipeline_run(
    run_id: int,
    *,
    status: str,
    processed_calls: int,
    total_audio_seconds: float,
    avg_rtf: float | None,
    error_message: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        run = db.get(PipelineRun, run_id)
        dur: int | None = None
        if run and run.started_at:
            dur = int((datetime.now(timezone.utc) - run.started_at).total_seconds())
        finish_pipeline_run(
            db,
            pipeline_run_id=run_id,
            status=status,
            finished_at=datetime.now(timezone.utc),
            processed_calls=processed_calls,
            duration_seconds=dur,
            error_message=error_message,
            total_audio_seconds=total_audio_seconds,
            avg_rtf=avg_rtf,
        )
    finally:
        db.close()


def parse_stats(output_lines: list[str]) -> tuple[int, float, float | None]:
    text = "\n".join(output_lines)
    m_ok = re.search(r"ok=(\d+)", text)
    m_audio = re.search(r"total_audio_seconds=([0-9.]+)", text)
    m_rtf = re.search(r"avg_rtf=([0-9.]+|NA)", text)
    ok = int(m_ok.group(1)) if m_ok else 0
    audio = float(m_audio.group(1)) if m_audio else 0.0
    rtf = None if not m_rtf or m_rtf.group(1) == "NA" else float(m_rtf.group(1))
    return ok, audio, rtf


def run_step(description: str, command: list[str]) -> list[str]:
    logging.info("Начало: %s", description)
    collected: list[str] = []
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip()
        collected.append(line)
        logging.info(line)
    if process.wait() != 0:
        raise RuntimeError(f"Ошибка в шаге '{description}'")
    return collected


def _parse_date(s: str) -> date:
    return date.fromisoformat(s.strip())


def _build_process_911_cmd(
    *,
    root: Path,
    audio_root: str,
    process_mode: str,
    pipeline_run_id: int | None,
) -> list[str]:
    cmd = [
        sys.executable,
        str(root / "process_911_calls_spikers.py"),
        "--root",
        audio_root,
        "--mode",
        process_mode,
        "--model",
        N911_MODEL,
        "--limit",
        str(N911_LIMIT),
    ]
    if pipeline_run_id is not None:
        cmd.extend(["--pipeline-run-id", str(pipeline_run_id)])
    if N911_RECURSIVE:
        cmd.append("--recursive")
    return cmd


def main() -> None:
    pipeline_t0 = time.time()

    parser = argparse.ArgumentParser(
        description="Пайплайн 911: режимы scan / transcribe / summarize / full (полный цикл по умолчанию).",
    )
    parser.add_argument(
        "--mode",
        choices=("full", "scan", "transcribe", "summarize"),
        default="full",
        help="full = scan+transcribe+саммари+Excel+Work; остальные — отдельные этапы",
    )
    parser.add_argument(
        "--period-start",
        type=str,
        default=None,
        help="YYYY-MM-DD для саммари/отчёта (с full и summarize; иначе предыдущая пн–вс)",
    )
    parser.add_argument("--period-end", type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--skip-work", action="store_true", help="Не создавать задачу в Work (full/summarize)")
    parser.add_argument("--summarize-limit", type=int, default=50_000, help="Макс. звонков на шаг саммари")
    parser.add_argument("--root", type=str, default=N911_ROOT, help="Каталог с аудио 911")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    os.chdir(root)
    os.makedirs("logs", exist_ok=True)
    os.makedirs("reports_911", exist_ok=True)
    log_filename = f"logs/pipeline_911_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_filename, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )

    print(f"Скрипт 911 (режим={args.mode}) запущен: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    period_start: date | None = None
    period_end: date | None = None
    weekly_report_id: int | None = None
    orchestrator_id: int | None = None

    if args.mode in ("full", "summarize"):
        if args.period_start and args.period_end:
            period_start = _parse_date(args.period_start)
            period_end = _parse_date(args.period_end)
        elif args.period_start or args.period_end:
            raise SystemExit("Задайте обе даты: --period-start и --period-end")
        else:
            period_start, period_end = previous_iso_week_mon_sun()
        logging.info("Отчётный период (саммари/Excel): %s — %s", period_start, period_end)
        db_o = SessionLocal()
        try:
            orch = create_pipeline_run(
                db_o,
                started_at=datetime.now(timezone.utc),
                status="RUNNING",
                pipeline_code=PIPELINE_CODE_WEEKLY,
            )
            orchestrator_id = orch.id
            wr = create_weekly_911_report(
                db_o,
                period_start=period_start,
                period_end=period_end,
                pipeline_run_id=orchestrator_id,
            )
            weekly_report_id = wr.id
        finally:
            db_o.close()

    transcribe_run_id: int | None = None
    processed_calls, total_audio_seconds, avg_rtf = 0, 0.0, None
    summarize_processed = 0

    try:
        if args.mode in ("scan", "full"):
            cmd = _build_process_911_cmd(
                root=root,
                audio_root=args.root,
                process_mode="scan",
                pipeline_run_id=None,
            )
            run_step("Сканирование (реестр звонков 911 в БД)", cmd)

        if args.mode in ("transcribe", "full"):
            transcribe_run_id = create_transcribe_pipeline_run()
            cmd = _build_process_911_cmd(
                root=root,
                audio_root=args.root,
                process_mode="transcribe",
                pipeline_run_id=transcribe_run_id,
            )
            output_lines = run_step("Транскрибация звонков 911", cmd)
            processed_calls, total_audio_seconds, avg_rtf = parse_stats(output_lines)
            finalize_transcribe_pipeline_run(
                transcribe_run_id,
                status="SUCCESS",
                processed_calls=processed_calls,
                total_audio_seconds=total_audio_seconds,
                avg_rtf=avg_rtf,
            )

        if args.mode in ("full", "summarize") and period_start and period_end and weekly_report_id is not None:
            start_utc, end_utc_excl = period_to_utc_half_open(period_start, period_end)
            db_s = SessionLocal()
            try:
                sum_result = run_summarize_911_batch(
                    db_s,
                    limit=args.summarize_limit,
                    call_started_at_gte=start_utc,
                    call_started_at_lt=end_utc_excl,
                )
                summarize_processed = int(sum_result.get("processed") or 0)
            finally:
                db_s.close()

            db_f = SessionLocal()
            try:
                calls_in_period = count_911_calls_in_range(db_f, start_utc=start_utc, end_utc_exclusive=end_utc_excl)
                summarized_calls = list_911_calls_summarized_in_range(
                    db_f, start_utc=start_utc, end_utc_exclusive=end_utc_excl
                )
                outcome_counts = aggregate_outcomes_for_calls(summarized_calls)
                task_text = build_weekly_task_text(
                    period_start=period_start,
                    period_end=period_end,
                    calls_summarized=len(summarized_calls),
                    outcome_counts=outcome_counts,
                )
                excel_name = f"calls_911_weekly_{period_start}_{period_end}.xlsx"
                excel_path = export_911_calls_to_excel(summarized_calls, root / "reports_911" / excel_name)

                work_task_id = None
                if not args.skip_work:
                    work_task_id = upload_weekly_911_task(task_text=task_text, excel_path=excel_path)

                finalize_weekly_911_report(
                    db_f,
                    report_id=weekly_report_id,
                    status="SUCCESS",
                    calls_in_period=calls_in_period,
                    calls_summarized_in_period=len(summarized_calls),
                    outcome_helped=outcome_counts.get("Помогли", 0),
                    outcome_not_helped=outcome_counts.get("Не помогли", 0),
                    outcome_in_progress=outcome_counts.get("В работе", 0),
                    outcome_unknown=outcome_counts.get("Не указано", 0),
                    task_text=task_text,
                    work_task_id=work_task_id,
                    excel_file_path=str(excel_path),
                    error_message=None,
                )

                if orchestrator_id is not None:
                    finish_pipeline_run(
                        db_f,
                        pipeline_run_id=orchestrator_id,
                        status="SUCCESS",
                        finished_at=datetime.now(timezone.utc),
                        processed_calls=processed_calls + summarize_processed,
                        duration_seconds=int(time.time() - pipeline_t0),
                        error_message=None,
                    )
            finally:
                db_f.close()

        elapsed = time.time() - pipeline_t0
        print(f"Скрипт 911 завершён за: {timedelta(seconds=int(elapsed))}")
    except Exception as exc:
        logging.exception("911 pipeline ошибка: %s", exc)
        if transcribe_run_id is not None:
            finalize_transcribe_pipeline_run(
                transcribe_run_id,
                status="FAILED",
                processed_calls=processed_calls,
                total_audio_seconds=total_audio_seconds,
                avg_rtf=avg_rtf,
                error_message=str(exc),
            )
        if weekly_report_id is not None and orchestrator_id is not None:
            db_e = SessionLocal()
            try:
                finalize_weekly_911_report(
                    db_e,
                    report_id=weekly_report_id,
                    status="FAILED",
                    calls_in_period=0,
                    calls_summarized_in_period=0,
                    outcome_helped=0,
                    outcome_not_helped=0,
                    outcome_in_progress=0,
                    outcome_unknown=0,
                    task_text=None,
                    work_task_id=None,
                    excel_file_path=None,
                    error_message=str(exc),
                )
                finish_pipeline_run(
                    db_e,
                    pipeline_run_id=orchestrator_id,
                    status="FAILED",
                    finished_at=datetime.now(timezone.utc),
                    processed_calls=processed_calls,
                    duration_seconds=int(time.time() - pipeline_t0),
                    error_message=str(exc),
                )
            finally:
                db_e.close()
        sys.exit(1)


if __name__ == "__main__":
    main()
