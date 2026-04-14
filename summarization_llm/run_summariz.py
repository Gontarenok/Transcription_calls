import subprocess
import sys
import os
import logging
import time
from datetime import datetime, timedelta

# --- Включаем глобально UTF-8 для подпроцессов ---
os.environ["PYTHONUTF8"] = "1"

# --- ЗАМЕР ВРЕМЕНИ: НАЧАЛО ---
start_time = time.time()
print(f"🚀 Скрипт запущен: {time.strftime('%Y-%m-%d %H:%M:%S')}")
# -----------------------------

# --- Настройка логирования ---
os.makedirs("logs", exist_ok=True)
log_filename = f"logs/logs_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

# фиксируем рабочую директорию
os.chdir(os.path.dirname(os.path.abspath(__file__)))
logging.info(f"Python exe: {sys.executable}")
logging.info(f"Working dir: {os.getcwd()}")

ENCODING = "utf-8"  # жёстко фиксируем UTF-8, без locale

def run_step(description, command):
    logging.info(f"▶️ Начало: {description}")
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # stderr → stdout
            text=True,
            encoding=ENCODING,
            errors="replace"
        )

        # читаем построчно в реальном времени
        for line in process.stdout:
            logging.info(line.rstrip())

        returncode = process.wait()
        if returncode == 0:
            logging.info(f"✅ Успешно: {description}")
        else:
            logging.error(f"❌ Ошибка в шаге: {description}, код возврата {returncode}")
            sys.exit(1)

    except Exception as e:
        logging.exception(f"⚠️ Исключение при запуске {description}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    logging.info("🚀 Запуск пайплайна run_summariz")

    run_step("Транскрипция", [sys.executable, "transcribe_audio.py"])
    run_step("Создание саммари", [sys.executable, "summarize_gemma.py"])
    run_step("Создание Excel отчета", [sys.executable, "export_summary_to_excel.py"])
    run_step("Создание мини-отчета", [sys.executable, "build_report.py"])
    run_step("Отправка задачи в Work", [sys.executable, "upload_work_prod.py"])
    # run_step("Загрузка отчета в Confluence", [sys.executable, "upload_confluence.py"])

    # --- ЗАМЕР ВРЕМЕНИ: КОНЕЦ ---
    end_time = time.time()
    elapsed = end_time - start_time
    print(f"✅ Скрипт завершён за: {timedelta(seconds=elapsed)}")
    print(f"⏹️  Завершено: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    # -----------------------------

    logging.info("🏁 Все шаги успешно выполнены")
