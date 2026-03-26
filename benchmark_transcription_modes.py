from __future__ import annotations

import argparse
import csv
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean

import librosa
import numpy as np
import whisper

from model_paths import model_settings

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a"}
DEFAULT_MODELS = [model_settings.whisper_model_large, model_settings.whisper_model_medium]
AUDIO_PATH = "C:/Audio_share/Night"
TARGET_SR = 16000


@dataclass
class RunResult:
    file_path: str
    model: str
    mode: str
    elapsed_sec: float
    audio_duration_sec: float
    text_len: int
    words_count: int
    words_per_sec: float
    chars_per_sec: float
    empty_text: bool
    repeat_noise_score: int


def list_audio_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
            files.append(p)
    return files


def load_audio(file_path: Path) -> tuple[np.ndarray, int]:
    audio, sr = librosa.load(str(file_path), sr=TARGET_SR, mono=False)
    if audio.ndim == 1:
        audio = np.expand_dims(audio, axis=0)
    return audio, sr


def transcribe_plain(model, audio: np.ndarray) -> str:
    # plain: без деления по каналам
    mono_audio = np.mean(audio, axis=0)
    result = model.transcribe(
        mono_audio,
        language="ru",
        fp16=False,
        temperature=[0, 0.2],
        best_of=3,
        beam_size=3,
        patience=1,
        condition_on_previous_text=False,
    )
    return (result.get("text") or "").strip()


def transcribe_stereo_speakers(model, audio: np.ndarray) -> str:
    entries: list[tuple[float, str, str]] = []

    channels = min(audio.shape[0], 2)
    for idx in range(channels):
        speaker = f"SPK{idx + 1}"
        out = model.transcribe(
            audio[idx],
            language="ru",
            fp16=False,
            temperature=[0, 0.2],
            best_of=3,
            beam_size=3,
            patience=1,
            condition_on_previous_text=False,
        )
        for seg in out.get("segments", []):
            text = (seg.get("text") or "").strip()
            if text:
                entries.append((float(seg.get("start", 0.0) or 0.0), speaker, text))

    entries.sort(key=lambda x: x[0])

    lines = []
    for start, speaker, text in entries:
        ts = int(start)
        mm, ss = divmod(ts, 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {speaker}: {text}")
    return "\n".join(lines).strip()


def repeat_noise_score(text: str) -> int:
    bad_markers = [
        "продолжение следует",
        "субтитры",
        "подписывайтесь",
        "спасибо за просмотр",
    ]
    low = text.lower()
    return sum(low.count(marker) for marker in bad_markers)


def eval_result(file_path: Path, model_name: str, mode: str, elapsed: float, text: str) -> RunResult:
    duration = float(librosa.get_duration(path=str(file_path)))
    words = text.split()
    words_count = len(words)

    return RunResult(
        file_path=str(file_path),
        model=model_name,
        mode=mode,
        elapsed_sec=elapsed,
        audio_duration_sec=duration,
        text_len=len(text),
        words_count=words_count,
        words_per_sec=(words_count / duration) if duration > 0 else 0.0,
        chars_per_sec=(len(text) / duration) if duration > 0 else 0.0,
        empty_text=(len(text.strip()) == 0),
        repeat_noise_score=repeat_noise_score(text),
    )


def save_text_output(out_dir: Path, file_path: Path, model_name: str, mode: str, text: str):
    stem = file_path.stem
    sub = out_dir / "transcripts"
    sub.mkdir(parents=True, exist_ok=True)
    out_file = sub / f"{stem}__{model_name}__{mode}.txt"
    out_file.write_text(text, encoding="utf-8")


def save_csv(out_dir: Path, results: list[RunResult]):
    out_file = out_dir / "benchmark_results.csv"
    with out_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "file_path",
                "model",
                "mode",
                "elapsed_sec",
                "audio_duration_sec",
                "rtf",
                "text_len",
                "words_count",
                "words_per_sec",
                "chars_per_sec",
                "empty_text",
                "repeat_noise_score",
            ]
        )
        for r in results:
            rtf = (r.elapsed_sec / r.audio_duration_sec) if r.audio_duration_sec > 0 else 0.0
            writer.writerow(
                [
                    r.file_path,
                    r.model,
                    r.mode,
                    round(r.elapsed_sec, 3),
                    round(r.audio_duration_sec, 3),
                    round(rtf, 3),
                    r.text_len,
                    r.words_count,
                    round(r.words_per_sec, 3),
                    round(r.chars_per_sec, 3),
                    r.empty_text,
                    r.repeat_noise_score,
                ]
            )


def save_summary(out_dir: Path, results: list[RunResult]):
    lines = ["# Сводный анализ benchmark", ""]

    groups: dict[tuple[str, str], list[RunResult]] = {}
    for r in results:
        groups.setdefault((r.model, r.mode), []).append(r)

    for (model, mode), rows in sorted(groups.items()):
        avg_elapsed = mean(r.elapsed_sec for r in rows)
        avg_dur = mean(r.audio_duration_sec for r in rows)
        avg_rtf = mean((r.elapsed_sec / r.audio_duration_sec) if r.audio_duration_sec > 0 else 0.0 for r in rows)
        empty_cnt = sum(1 for r in rows if r.empty_text)
        noise_avg = mean(r.repeat_noise_score for r in rows)

        lines.append(f"## model={model}, mode={mode}")
        lines.append(f"- файлов: {len(rows)}")
        lines.append(f"- среднее время транскрибации: {avg_elapsed:.2f} сек")
        lines.append(f"- средняя длительность аудио: {avg_dur:.2f} сек")
        lines.append(f"- средний RTF (ниже лучше): {avg_rtf:.3f}")
        lines.append(f"- пустых транскриптов: {empty_cnt}")
        lines.append(f"- средний noise score: {noise_avg:.2f}")
        lines.append("")

    out_file = out_dir / "benchmark_summary.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description="Сравнительный benchmark транскрибации: plain vs stereo-speakers")
    p.add_argument("--root", default=AUDIO_PATH,  help="Папка с аудио для теста")
    p.add_argument("--models", default=",".join(DEFAULT_MODELS), help="Список моделей через запятую, напр. large-v3,medium")
    p.add_argument("--limit", type=int, default=0, help="Ограничение числа файлов (0 = без ограничения)")
    p.add_argument("--out", default="output_audio_benchmark", help="Папка для результатов")
    args = p.parse_args()

    root = Path(args.root)
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"❌ Папка не найдена: {root}")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        raise SystemExit("❌ Не переданы модели")

    files = list_audio_files(root)
    if args.limit and args.limit > 0:
        files = files[: args.limit]

    if not files:
        raise SystemExit(f"❌ В папке {root} не найдено аудиофайлов")

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = Path(args.out) / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[RunResult] = []

    for model_name in models:
        print(f"\n📦 Загружаю модель: {model_name}")
        model = whisper.load_model(model_name, download_root=model_settings.whisper_models_root)

        for file_path in files:
            print(f"\n🎧 {file_path.name} | model={model_name}")
            audio, sr = load_audio(file_path)
            print(f"ℹ️ sr={sr}, channels={audio.shape[0]}")

            # 1) plain
            t0 = time.time()
            plain_text = transcribe_plain(model, audio)
            elapsed_plain = time.time() - t0
            save_text_output(out_dir, file_path, model_name, "plain", plain_text)
            results.append(eval_result(file_path, model_name, "plain", elapsed_plain, plain_text))
            print(f"✅ plain: {elapsed_plain:.1f} сек")

            # 2) stereo speakers
            t1 = time.time()
            speakers_text = transcribe_stereo_speakers(model, audio)
            elapsed_speakers = time.time() - t1
            save_text_output(out_dir, file_path, model_name, "stereo_speakers", speakers_text)
            results.append(eval_result(file_path, model_name, "stereo_speakers", elapsed_speakers, speakers_text))
            print(f"✅ stereo_speakers: {elapsed_speakers:.1f} сек")

    save_csv(out_dir, results)
    save_summary(out_dir, results)
    print(f"\n✅ Benchmark завершён. Результаты: {out_dir}")


if __name__ == "__main__":
    main()
