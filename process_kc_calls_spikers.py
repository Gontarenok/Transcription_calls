from __future__ import annotations

import argparse
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import librosa
import numpy as np
import whisper

from audio_utils import get_audio_duration_seconds
from db.base import SessionLocal
from model_paths import model_settings
from db.crud import (
    add_call_part,
    add_transcription,
    create_or_get_call,
    get_calls_for_transcription,
    get_or_create_call_type,
    get_or_create_user,
    refresh_call_rollups,
    set_call_status,
)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a"}
DEFAULT_ROOT = r"C:\Audio_share\Contact_center"
DEFAULT_CALL_TYPE_CODE = "КЦ"
DEFAULT_CALL_TYPE_NAME = "Контакт-центр"
MODEL_DIR = model_settings.whisper_models_root
TARGET_SR = 16000


def normalize_day_folder(day: str) -> str:
    raw = (day or "").strip()
    if re.fullmatch(r"\d{8}", raw):
        return raw
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d%m%Y")
        except ValueError:
            continue
    raise ValueError("Неверный формат --day. Ожидается DDMMYYYY (например 24022026)")


def parse_octell_call_id(file_name: str) -> str | None:
    stem = Path(file_name).stem
    idx = stem.lower().find("_mix")
    if idx <= 0:
        return None
    octell_id = stem[:idx].strip("_-")
    return octell_id or None


def parse_part_number(file_name: str) -> int:
    stem = Path(file_name).stem
    m = re.search(r"_(\d{2})$", stem)
    if not m:
        return 1
    part = int(m.group(1))
    return part if part > 0 else 1


def parse_manager_folder(folder_name: str) -> tuple[str | None, str | None]:
    parts = [p for p in folder_name.split("_") if p]
    if len(parts) < 4:
        return None, None
    full_name = " ".join(parts[:3]).strip() or None
    domain = parts[-1].strip() or None
    return full_name, domain


def collect_kc_calls_metadata(db, *, root_dir: Path, day: str, manager_limit: int | None = None) -> dict[str, int]:
    day_dir = root_dir / day
    if not day_dir.exists() or not day_dir.is_dir():
        raise SystemExit(f"❌ Папка дня не найдена: {day_dir}")

    call_type = get_or_create_call_type(
        db,
        code=DEFAULT_CALL_TYPE_CODE,
        name=DEFAULT_CALL_TYPE_NAME,
        description="Звонки в контакт-центр компании",
    )

    manager_dirs = sorted([p for p in day_dir.iterdir() if p.is_dir()], key=lambda p: p.name.lower())
    if manager_limit:
        manager_dirs = manager_dirs[:manager_limit]

    calls_seen = 0
    managers_processed = 0

    for manager_dir in manager_dirs:
        managers_processed += 1
        full_name, domain = parse_manager_folder(manager_dir.name)
        manager = get_or_create_user(
            db,
            manager_folder=manager_dir.name,
            full_name=full_name,
            domain=domain,
            department="Contact Center",
        )

        files = [p for p in sorted(manager_dir.iterdir(), key=lambda p: p.name.lower()) if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS]
        for file_path in files:
            octell_call_id = parse_octell_call_id(file_path.name)
            if not octell_call_id:
                octell_call_id = f"NO_OCTELL::{file_path.stem}"

            modified_at = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
            part_number = parse_part_number(file_path.name)
            duration_seconds = get_audio_duration_seconds(file_path)

            call = create_or_get_call(
                db,
                manager_id=manager.id,
                call_type_id=call_type.id,
                octell_call_id=octell_call_id,
                file_name=file_path.name,
                source_file_path=str(file_path),
                call_started_at=modified_at,
                duration_seconds=duration_seconds,
                status="NEW",
            )

            add_call_part(
                db,
                call_id=call.id,
                part_number=part_number,
                file_name=file_path.name,
                source_file_path=str(file_path),
                call_started_at=modified_at,
                duration_seconds=duration_seconds,
            )
            refresh_call_rollups(db, call.id)
            calls_seen += 1

    return {"managers_processed": managers_processed, "calls_seen": calls_seen}


def format_ts(seconds_float: float) -> str:
    total_seconds = int(max(0.0, seconds_float))
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


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


def part_entries_with_speakers(model, file_path: str, time_offset: float) -> list[dict]:
    audio, _ = librosa.load(file_path, sr=TARGET_SR, mono=False)
    if audio.ndim == 1:
        audio = np.expand_dims(audio, axis=0)

    entries: list[dict] = []

    if audio.shape[0] == 1:
        result = transcribe_channel(model, audio[0])
        for seg in result.get("segments", []):
            text = seg.get("text", "").strip()
            if text:
                entries.append(
                    {
                        "start": time_offset + float(seg.get("start", 0.0) or 0.0),
                        "speaker": "SPK1",
                        "text": text,
                    }
                )
        return entries

    # stereo => два спикера
    result_spk1 = transcribe_channel(model, audio[0])
    result_spk2 = transcribe_channel(model, audio[1])

    for seg in result_spk1.get("segments", []):
        text = seg.get("text", "").strip()
        if text:
            entries.append(
                {
                    "start": time_offset + float(seg.get("start", 0.0) or 0.0),
                    "speaker": "SPK1",
                    "text": text,
                }
            )

    for seg in result_spk2.get("segments", []):
        text = seg.get("text", "").strip()
        if text:
            entries.append(
                {
                    "start": time_offset + float(seg.get("start", 0.0) or 0.0),
                    "speaker": "SPK2",
                    "text": text,
                }
            )

    return entries


def render_dialog(entries: list[dict]) -> str:
    lines = [f"[{format_ts(e['start'])}] {e['speaker']}: {e['text']}" for e in entries]
    return "\n".join(lines).strip()


def transcribe_kc_calls(db, *, model_size: str, manager_folder: str | None = None, limit: int = 10000, pipeline_run_id: int | None = None) -> dict[str, float | int]:
    call_type = get_or_create_call_type(
        db,
        code=DEFAULT_CALL_TYPE_CODE,
        name=DEFAULT_CALL_TYPE_NAME,
        description="Звонки в контакт-центр компании",
    )

    manager_id = None
    if manager_folder:
        manager = get_or_create_user(db, manager_folder=manager_folder, department="Contact Center")
        manager_id = manager.id

    weight_path = os.path.join(MODEL_DIR, f"{model_size}.pt")
    if not os.path.exists(weight_path):
        raise SystemExit(f"❌ Не найден файл весов: {weight_path}")

    print(f"📦 Загружаю Whisper {model_size} из {MODEL_DIR}")
    model = whisper.load_model(model_size, download_root=MODEL_DIR)

    calls = get_calls_for_transcription(
        db,
        call_type_code=call_type.code,
        manager_id=manager_id,
        statuses=("NEW", "FAILED", "TRANSCRIPTION_FAILED"),
        limit=limit,
    )
    if not calls:
        print("ℹ️ Нет звонков для транскрибации")
        return {"transcribed": 0, "failed": 0}

    transcribed = 0
    failed = 0
    total_audio_seconds = 0.0
    total_transcribe_seconds = 0.0
    current_manager = None

    for call in calls:
        if call.manager and call.manager.manager_folder != current_manager:
            current_manager = call.manager.manager_folder
            print(f"\n👤 Менеджер: {current_manager}")

        print(f"🎧 [{call.id}] octell={call.octell_call_id} parts={call.parts_count}")
        set_call_status(db, call.id, "TRANSCRIBING")

        try:
            started = time.time()
            parts = sorted(call.call_parts, key=lambda p: (p.part_number, p.call_started_at))
            if not parts:
                raise RuntimeError("Для звонка не найдены файлы частей")

            entries: list[dict] = []
            running_offset = 0.0
            for part in parts:
                entries.extend(part_entries_with_speakers(model, part.source_file_path, running_offset))
                running_offset += float(part.duration_seconds or 0.0)

            entries = sorted(entries, key=lambda x: x["start"])
            merged_dialog = render_dialog(entries)

            if pipeline_run_id is not None:
                call.pipeline_run_id = pipeline_run_id
                db.commit()

            add_transcription(
                db,
                call_id=call.id,
                model_name=f"whisper-{model_size}-stereo-speakers",
                text=merged_dialog,
                deactivate_previous=True,
            )
            set_call_status(db, call.id, "TRANSCRIBED", error_message=None)
            elapsed = time.time() - started
            total_transcribe_seconds += elapsed
            total_audio_seconds += float(call.duration_seconds or 0.0)
            transcribed += 1
            print(f"✅ Готово за {elapsed:.1f} c")

        except Exception as exc:
            if pipeline_run_id is not None:
                call.pipeline_run_id = pipeline_run_id
                db.commit()
            set_call_status(db, call.id, "TRANSCRIPTION_FAILED", error_message=str(exc))
            failed += 1
            print(f"❌ Ошибка: {exc}")

    avg_rtf = (total_transcribe_seconds / total_audio_seconds) if total_audio_seconds > 0 else None
    return {"transcribed": transcribed, "failed": failed, "total_audio_seconds": total_audio_seconds, "total_transcribe_seconds": total_transcribe_seconds, "avg_rtf": avg_rtf}


def main():
    parser = argparse.ArgumentParser(description="Обработка звонков КЦ со спикеризацией по стереоканалам")
    parser.add_argument("--day", default=datetime.now().strftime("%d%m%Y"), help="Папка дня DDMMYYYY")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="Корневая папка C:/Audio_share/Contact_center")
    parser.add_argument("--mode", default="all", choices=["scan", "transcribe", "all"])
    parser.add_argument("--model", default=model_settings.whisper_model_default, help="Размер Whisper модели")
    parser.add_argument("--manager", default=None, help="Ограничить транскрибацию одним manager_folder")
    parser.add_argument("--manager-limit", type=int, default=None, help="Ограничить число папок менеджеров на scan")
    parser.add_argument("--limit", type=int, default=10000, help="Лимит звонков на транскрибацию")
    parser.add_argument("--pipeline-run-id", type=int, default=None, help="ID запуска pipeline_runs")
    args = parser.parse_args()

    day_folder = normalize_day_folder(args.day)

    db = SessionLocal()
    try:
        if args.mode in {"scan", "all"}:
            stats = collect_kc_calls_metadata(db, root_dir=Path(args.root), day=day_folder, manager_limit=args.manager_limit)
            print(f"📥 Scan завершён: day={day_folder}, managers={stats['managers_processed']}, calls={stats['calls_seen']}")

        if args.mode in {"transcribe", "all"}:
            stats = transcribe_kc_calls(db, model_size=args.model, manager_folder=args.manager, limit=args.limit, pipeline_run_id=args.pipeline_run_id)
            print(f"📝 Transcribe завершён: ok={stats['transcribed']}, failed={stats['failed']}, total_audio_seconds={stats['total_audio_seconds']:.2f}, total_transcribe_seconds={stats['total_transcribe_seconds']:.2f}, avg_rtf={stats['avg_rtf'] if stats['avg_rtf'] is not None else 'NA'}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
