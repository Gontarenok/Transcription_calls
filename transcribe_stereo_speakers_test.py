import os
import time
from datetime import datetime, timedelta

import librosa
import numpy as np
import whisper

from model_paths import model_settings

# Путь к конкретному аудио для ручного теста
AUDIO_FILE_PATH = r"C:\Audio_share\Contact_center\24022026\Manager\call.mp3"

OUTPUT_DIR = "output_audio"
MODEL_DIR = model_settings.whisper_models_root
MODEL_SIZE = model_settings.whisper_model_default
TARGET_SR = 16000


def format_ts(seconds_float: float) -> str:
    total_seconds = int(seconds_float)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def to_entries(speaker: str, segments: list[dict]) -> list[dict]:
    entries: list[dict] = []
    for seg in segments:
        start = float(seg.get("start", 0.0) or 0.0)
        text = seg.get("text", "").strip()
        if text:
            entries.append({"start": start, "speaker": speaker, "text": text})
    return entries


def render_entries(entries: list[dict]) -> list[str]:
    lines: list[str] = []
    for e in entries:
        lines.append(f"[{format_ts(e['start'])}] {e['speaker']}: {e['text']}")
    return lines


def transcribe_channel(model, channel_audio: np.ndarray) -> dict:
    return model.transcribe(
        channel_audio,
        language="ru",
        fp16=False,
        temperature=[0, 0.2],
        best_of=3,
        beam_size=3,
        patience=1,
        condition_on_previous_text=False,
    )


def save_result(lines: list[str], output_dir: str, base_name: str):
    os.makedirs(output_dir, exist_ok=True)
    txt_path = os.path.join(output_dir, f"{base_name}_stereo_speakers.txt")
    with open(txt_path, "w", encoding="utf8") as f:
        f.write("\n".join(lines))
    print(f"✅ Сохранён тестовый результат: {txt_path}")


def main():
    start_time = time.time()
    print(f"🚀 Тест стерео-транскрибации запущен: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    if not os.path.exists(AUDIO_FILE_PATH):
        raise SystemExit(f"❌ Аудиофайл не найден: {AUDIO_FILE_PATH}")

    weight_path = os.path.join(MODEL_DIR, f"{MODEL_SIZE}.pt")
    if not os.path.exists(weight_path):
        raise SystemExit(f"❌ Не найден файл весов: {weight_path}")

    print(f"📦 Загружаю локальную модель Whisper: {MODEL_SIZE}")
    model = whisper.load_model(MODEL_SIZE, download_root=MODEL_DIR)

    print("🎧 Загружаю аудио как стерео...")
    audio, sr = librosa.load(AUDIO_FILE_PATH, sr=TARGET_SR, mono=False)

    if audio.ndim == 1:
        audio = np.expand_dims(audio, axis=0)

    channels_count = audio.shape[0]
    print(f"ℹ️ Каналов в аудио: {channels_count}, sr={sr}")

    all_lines: list[str] = []

    if channels_count == 1:
        result = transcribe_channel(model, audio[0])
        merged_entries = to_entries("SPK1", result.get("segments", []))
        all_lines.append("=== ИТОГОВЫЙ РАЗГОВОР (по времени) ===")
        all_lines.extend(render_entries(merged_entries))
    else:
        # Берём первые 2 канала как 2 спикера (типичный случай Octell stereo)
        result_spk1 = transcribe_channel(model, audio[0])
        result_spk2 = transcribe_channel(model, audio[1])

        entries_spk1 = to_entries("SPK1", result_spk1.get("segments", []))
        entries_spk2 = to_entries("SPK2", result_spk2.get("segments", []))

        # Склеиваем общий диалог в порядке таймкодов
        merged_entries = sorted(entries_spk1 + entries_spk2, key=lambda x: x["start"])

        all_lines.append("=== ИТОГОВЫЙ РАЗГОВОР (по времени) ===")
        all_lines.extend(render_entries(merged_entries))
        all_lines.append("")

        # Дополнительно оставляем расшифровку по каналам для отладки качества
        all_lines.append("=== SPK1 (канал 1) ===")
        all_lines.extend(render_entries(entries_spk1))
        all_lines.append("")
        all_lines.append("=== SPK2 (канал 2) ===")
        all_lines.extend(render_entries(entries_spk2))

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = os.path.join(OUTPUT_DIR, timestamp)
    base_name = os.path.splitext(os.path.basename(AUDIO_FILE_PATH))[0]
    save_result(all_lines, out_dir, base_name)

    elapsed = time.time() - start_time
    print(f"✅ Тест завершён за: {timedelta(seconds=elapsed)}")


if __name__ == "__main__":
    main()