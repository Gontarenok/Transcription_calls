import argparse
import os
import time
from datetime import datetime
from datetime import timedelta

import whisper

# --- ЗАМЕР ВРЕМЕНИ: НАЧАЛО ---
start_time = time.time()
print(f"🚀 Скрипт запущен: {time.strftime('%Y-%m-%d %H:%M:%S')}")
# -----------------------------

INPUT_DIR = "C:/Audio_share/Night"  # Расшареная сетевая папка с аудио
# INPUT_DIR = "audio" # Локальная папка с аудио для тестов
OUTPUT_DIR = "output_audio"
MODEL_DIR = os.path.join(os.path.dirname(__file__), "models", "whisper")
MODEL_SIZE = "large-v3"


def transcribe_audio(path, model):
    result = model.transcribe(
        path,
        language="ru",
        fp16=False,
        temperature=[0, 0.2],
        best_of=5,
        beam_size=5,
        patience=1,
        initial_prompt="ТП8, 1C, ERP, 911, work",  # если понадобится вернуть
        condition_on_previous_text=False,
    )
    return result


def format_segments(segments):
    """Преобразует сегменты в читаемый вид с таймкодами"""
    lines = []
    for seg in segments:
        start = int(seg["start"])
        minutes, seconds = divmod(start, 60)
        timestamp = f"{minutes:02d}:{seconds:02d}"
        text = seg["text"].strip()
        lines.append(f"[{timestamp}] {text}")
    return "\n".join(lines)


def save_outputs(result, output_dir, base_name):
    os.makedirs(output_dir, exist_ok=True)

    # TXT с диалогом
    txt_path = os.path.join(output_dir, f"{base_name}.txt")
    with open(txt_path, "w", encoding="utf8") as f:
        if "segments" in result:
            f.write(format_segments(result["segments"]))
        else:
            f.write(result["text"])

    print(f"✅ Сохранил {base_name}.txt и {base_name}.json в {output_dir}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="medium", help="Размер модели Whisper (tiny, base, small, medium, large)")
    p.add_argument("--out", default=OUTPUT_DIR, help="Папка для сохранения результатов")
    args = p.parse_args()

    # Проверяем веса
    weight_path = os.path.join(MODEL_DIR, f"{MODEL_SIZE}.pt")
    if not os.path.exists(weight_path):
        raise SystemExit(
            f"❌ Не найден {weight_path}. Сначала скачай веса "
            f"(см. download_whisper_weights.py) или скопируй из кэша."
        )

    # Загружаем модель
    print(f"📦 Загружаю локальную модель Whisper: {MODEL_SIZE} из {MODEL_DIR}")
    model = whisper.load_model(MODEL_SIZE, download_root=MODEL_DIR)

    # Создаём подпапку с датой/временем
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_output_dir = os.path.join(args.out, timestamp)
    os.makedirs(run_output_dir, exist_ok=True)
    print(f"📂 Результаты будут сохранены в {run_output_dir}")

    files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith((".mp3", ".wav", ".m4a"))]
    if not files:
        print(f"⚠️ В папке {INPUT_DIR} нет аудиофайлов")
        return

    for file in files:
        path = os.path.join(INPUT_DIR, file)
        base = os.path.splitext(file)[0]
        print(f"\n🎧 Обрабатываю {file}...")
        start = time.time()
        result = transcribe_audio(path, model)
        elapsed = time.time() - start
        print(f"⏱️ Время транскрибации: {elapsed:.2f} сек")
        save_outputs(result, run_output_dir, base)

    print("\n✅ Все файлы обработаны")


if __name__ == "__main__":
    main()
    # --- ЗАМЕР ВРЕМЕНИ: КОНЕЦ ---
    end_time = time.time()
    elapsed = end_time - start_time
    print(f"✅ Скрипт завершён за: {timedelta(seconds=elapsed)}")
    print(f"⏹️  Завершено: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    # -----------------------------
