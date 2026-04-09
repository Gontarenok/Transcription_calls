"""Быстрое получение длительности аудиофайла (scan пайплайнов без полного декодирования)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def get_audio_duration_seconds(file_path: Path) -> float | None:
    """
    Порядок: ffprobe (если в PATH) → soundfile (wav/flac/…) → librosa.
    ffprobe не декодирует весь поток — обычно на порядок быстрее librosa.get_duration на больших mp3/m4a.
    """
    path = Path(file_path)
    if not path.is_file():
        return None

    dur = _duration_ffprobe(path)
    if dur is not None and dur > 0:
        return dur

    dur = _duration_soundfile(path)
    if dur is not None and dur > 0:
        return dur

    return _duration_librosa(path)


def _duration_ffprobe(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        # Windows: без консольного окна
        kwargs: dict = {
            "capture_output": True,
            "text": True,
            "timeout": 60,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        r = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            **kwargs,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        return float((r.stdout or "").strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _duration_soundfile(path: Path) -> float | None:
    try:
        import soundfile as sf
    except ImportError:
        return None
    try:
        info = sf.info(str(path))
        d = float(info.duration)
        return d if d > 0 else None
    except Exception:
        return None


def _duration_librosa(path: Path) -> float | None:
    try:
        import librosa
    except ImportError:
        return None
    try:
        return float(librosa.get_duration(path=str(path)))
    except Exception:
        return None
