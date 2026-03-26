import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from db.base import SessionLocal
from db.models import PipelineRun

# --- Конфиг запуска КЦ пайплайна (удобно для планировщика) ---
KC_ROOT = r"C:\Audio_share\Contact_center"
# KC_DAY = datetime.now().strftime("%d%m%Y")  # например: 24022026
KC_DAY = "04032026"
KC_MODE = "all"  # scan / transcribe / all
KC_MODEL = "medium"
KC_MANAGER = None  # например: "ivanov_ii" или None для всех
KC_MANAGER_LIMIT = None  # например: 10 для теста
KC_LIMIT = 10000

PIPELINE_CODE = "КЦ"

# --- Включаем глобально UTF-8 для подпроцессов ---
os.environ["PYTHONUTF8"] = "1"

# --- ЗАМЕР ВРЕМЕНИ: НАЧАЛО ---
start_time = time.time()
print(f"🚀 Скрипт КЦ запущен: {time.strftime('%Y-%m-%d %H:%M:%S')}")

# --- Настройка логирования ---
os.makedirs("logs", exist_ok=True)
log_filename = f"logs/kc_pipeline_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=[logging.FileHandler(log_filename, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
os.chdir(os.path.dirname(os.path.abspath(__file__)))


def create_pipeline_run() -> int:
    db = SessionLocal()
    try:
        run = PipelineRun(pipeline_code=PIPELINE_CODE, started_at=datetime.now(timezone.utc), status="RUNNING", processed_calls=0)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run.id
    finally:
        db.close()


def finalize_pipeline_run(run_id: int, *, status: str, processed_calls: int, total_audio_seconds: float, avg_rtf: float | None, error_message: str | None = None):
    db = SessionLocal()
    try:
        run = db.get(PipelineRun, run_id)
        if not run:
            return
        finished_at = datetime.now(timezone.utc)
        run.finished_at = finished_at
        run.duration_seconds = int((finished_at - run.started_at).total_seconds()) if run.started_at else None
        run.status = status
        run.processed_calls = processed_calls
        run.total_audio_seconds = total_audio_seconds
        run.avg_rtf = avg_rtf
        run.error_message = error_message
        db.commit()
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


def run_step(description, command):
    logging.info(f"▶️ Начало: {description}")
    collected: list[str] = []
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    for line in process.stdout:
        line = line.rstrip()
        collected.append(line)
        logging.info(line)
    if process.wait() != 0:
        raise RuntimeError(f"Ошибка в шаге '{description}'")
    return collected


if __name__ == "__main__":
    run_id = create_pipeline_run()
    processed_calls, total_audio_seconds, avg_rtf = 0, 0.0, None
    try:
        cmd = [sys.executable, "process_kc_calls_spikers.py", "--day", KC_DAY, "--root", KC_ROOT, "--mode", KC_MODE, "--model", KC_MODEL, "--limit", str(KC_LIMIT), "--pipeline-run-id", str(run_id)]
        if KC_MANAGER:
            cmd.extend(["--manager", KC_MANAGER])
        if KC_MANAGER_LIMIT is not None:
            cmd.extend(["--manager-limit", str(KC_MANAGER_LIMIT)])

        output_lines = run_step("Сканирование и транскрибация звонков КЦ", cmd)
        processed_calls, total_audio_seconds, avg_rtf = parse_stats(output_lines)

        elapsed = time.time() - start_time
        print(f"✅ Скрипт КЦ завершён за: {timedelta(seconds=elapsed)}")
        finalize_pipeline_run(run_id, status="SUCCESS", processed_calls=processed_calls, total_audio_seconds=total_audio_seconds, avg_rtf=avg_rtf)
    except Exception as exc:
        logging.exception(f"❌ KC pipeline ошибка: {exc}")
        finalize_pipeline_run(run_id, status="FAILED", processed_calls=processed_calls, total_audio_seconds=total_audio_seconds, avg_rtf=avg_rtf, error_message=str(exc))
        sys.exit(1)