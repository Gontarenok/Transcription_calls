import os
import time
import argparse
import torch
import time
from datetime import timedelta
from transformers import pipeline
from datetime import datetime

# --- ЗАМЕР ВРЕМЕНИ: НАЧАЛО ---
start_time = time.time()
print(f"🚀 Скрипт запущен: {time.strftime('%Y-%m-%d %H:%M:%S')}")
# -----------------------------

# 📌 Конфиг
MODEL_PATH = "models/gemma/gemma-3-4b-it"   # локальный путь к модели
INPUT_DIR = "output_audio"                  # папка с транскриптами
OUTPUT_DIR = "output_summary"               # папка для саммари
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ⚙️ Устройство (GPU если есть, иначе CPU)
device = 0 if torch.cuda.is_available() else -1
print(f"⚙️ Используем {'GPU' if device == 0 else 'CPU'}")

# 📥 Загружаем пайплайн один раз
print("📥 Загружаем модель через pipeline...")
pipe = pipeline(
    task="text-generation",
    model=MODEL_PATH,
    tokenizer=MODEL_PATH,
    device=device,
    torch_dtype="auto",
    model_kwargs={"local_files_only": True}
)
print("✅ Модель загружена")

# 🔑 Промт для 911
SYSTEM_PROMPT = (
    '''Ты — помощник, делающий краткие саммари телефонных звонков в техническую поддержку.
    Сделай структурированное саммари следующего телефонного обсуждения по строгому плану:
    - Участники:
    - Платформа:
    - Тема:
    - Суть:
    - Действие в результате диалога:
    - Итог: только 4 варианта: помогли/ не помогли/ в работе / не указано - если итог по обсуждению не ясен
    - Краткое саммари: краткое саммари телефонного звонка не более 5 предложений. Если не ясно, то "Суть звонка не ясна".

    Не додумывай факты, не цитируй длинно.
    Важно! Если в звонке нет фактической информации (меньше 10 слов, или только приветствия), то для всех пунктов, кроме «Итог», запиши "Суть звонка не ясна".

    Возможные встречающиеся термины в звонках:
    - Компания "Металл Профиль"
    - ТП8 = модуль в 1С
    - 911 - номер технической поддержки
    '''
)

# 🔑 Промт для КЦ
# SYSTEM_PROMPT = (
#     '''Ты — помощник, делающий краткие саммари телефонных звонков.
#     Сделай структурированное саммари следующего телефонного обсуждения по строгому плану:
#     - Участники:
#     - Платформа:
#     - Тема:
#     - Суть:
#     - Действие в результате диалога:
#     - Итог: только 3 варианта: помогли/ не помогли/ в работе (если итог по обсуждению не ясен)
#     - Краткое саммари: краткое саммари телефонного звонка не более 5 предложений.
#
#     Не додумывай факты, не цитируй длинно"
#     Возможные встречающиеся термины в звонках:
#     -ТП8 = модуль в 1С
#     '''
# )



def summarize_with_gemma(text: str) -> str:
    """Создание саммари с помощью пайплайна"""
    prompt = SYSTEM_PROMPT + "\n\nТекст разговора:\n" + text + "\n\nСделай саммари."

    start = time.time()
    out = pipe(
        prompt,
        max_new_tokens=220,
        do_sample=False,               # без случайности (меньше "галлюцинаций")
        repetition_penalty=1.1
    )
    elapsed = time.time() - start
    print(f"⏱️ Время генерации: {elapsed:.2f} сек")

    summary = out[0]["generated_text"]

    # Убираем сам промт из результата
    if summary.startswith(prompt):
        summary = summary[len(prompt):].strip()

    return summary

def get_latest_subdir(base_dir: str) -> str:
    """Находит самую свежую подпапку в base_dir"""
    subdirs = [
        os.path.join(base_dir, d) for d in os.listdir(base_dir)
        if os.path.isdir(os.path.join(base_dir, d))
    ]
    if not subdirs:
        raise SystemExit(f"❌ В {base_dir} нет подпапок с результатами транскрибации")
    return max(subdirs, key=os.path.getmtime)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--indir", default=INPUT_DIR, help="Папка с транскриптами (.txt)")
    p.add_argument("--outdir", default=OUTPUT_DIR, help="Папка для саммари")
    args = p.parse_args()

    # 🔎 Берём самую свежую подпапку в output_audio
    latest_in_dir = get_latest_subdir(args.indir)
    # latest_in_dir = ("output_audio/2025-08-19_16-49-18")
    print(f"📂 Используем входную папку: {latest_in_dir}")

    # 🗂️ Создаём подпапку с датой и временем для саммари
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_outdir = os.path.join(args.outdir, timestamp)
    os.makedirs(run_outdir, exist_ok=True)
    print(f"📂 Результаты будут сохранены в: {run_outdir}")

    # Берём только .txt
    files = [f for f in os.listdir(latest_in_dir) if f.endswith(".txt")]
    if not files:
        print(f"⚠️ В папке {latest_in_dir} нет .txt файлов")
        return

    for file in files:
        path = os.path.join(latest_in_dir, file)
        text = open(path, encoding="utf8").read()
        base = os.path.splitext(file)[0]

        print(f"\n⚙️ Обрабатываем {file}...")
        summary = summarize_with_gemma(text)

        # .txt
        txt_out = os.path.join(run_outdir, base + "_summary.txt")
        with open(txt_out, "w", encoding="utf8") as f:
            f.write(summary)

        # # .html для Confluence
        # safe = summary.replace("]]>", "]]]]><![CDATA[>")
        # html = (
        #     '<ac:structured-macro ac:name="code">'
        #     "<ac:plain-text-body><![CDATA[" + safe + "]]></ac:plain-text-body>"
        #     "</ac:structured-macro>"
        # )
        # html_out = os.path.join(run_outdir, base + "_summary.html")
        # with open(html_out, "w", encoding="utf8") as f:
        #     f.write(html)

        print(f"✅ Сохранено: {txt_out}")


if __name__ == "__main__":
    main()
    # --- ЗАМЕР ВРЕМЕНИ: КОНЕЦ ---
    end_time = time.time()
    elapsed = end_time - start_time
    print(f"✅ Скрипт завершён за: {timedelta(seconds=elapsed)}")
    print(f"⏹️  Завершено: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    # -----------------------------
