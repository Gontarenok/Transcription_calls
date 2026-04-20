"""
Одноразовое скачивание локальных моделей в каталог моделей (MODELS_HOST_PATH).

Запускать на проде после развёртывания, до первого запуска пайплайнов:
    sudo -u deploy bash -c '
      cd /opt/transcription-calls &&
      source .env &&
      python3 scripts/download_models.py --target "$MODELS_HOST_PATH" --all
    '

Можно в контейнере (не требует, чтобы python-зависимости были на хосте):
    docker compose --env-file .env --env-file .image.env run --rm \
      -v ${MODELS_HOST_PATH}:/srv/ai-models web \
      python scripts/download_models.py --target /srv/ai-models --all

После скачивания — обновите в .env пути WHISPER_MODELS_ROOT, GEMMA_MODEL_PATH,
EMBEDDING_MODEL_MINI_PATH / SBER под актуальные значения (примеры в .env.example).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def download_whisper(target_root: Path, models: list[str]) -> None:
    import whisper  # openai-whisper

    target_root.mkdir(parents=True, exist_ok=True)
    for name in models:
        print(f"[whisper] downloading: {name} -> {target_root}")
        whisper.load_model(name, download_root=str(target_root))
    print(f"[whisper] done: {target_root}")


def download_hf_snapshot(repo_id: str, target_dir: Path, token: str | None) -> None:
    from huggingface_hub import snapshot_download

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    print(f"[hf] snapshot {repo_id} -> {target_dir}")
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(target_dir),
        local_dir_use_symlinks=False,
        token=token,
    )
    print(f"[hf] done: {target_dir}")


def main() -> int:
    p = argparse.ArgumentParser(description="Download local AI models for Transcription_calls.")
    p.add_argument("--target", required=True, help="Корневой каталог для моделей (MODELS_HOST_PATH)")
    p.add_argument("--whisper", nargs="*", default=["medium", "large-v3"], help="Whisper-модели")
    p.add_argument("--gemma", default="google/gemma-3-4b-it", help="HF repo id Gemma")
    p.add_argument("--embed-mini", default="BAAI/bge-m3", help="HF repo id эмбеддинг-модели bge-m3")
    p.add_argument("--embed-sber", default="ai-forever/sbert_large_nlu_ru", help="HF repo id SBERT")
    p.add_argument("--all", action="store_true", help="Скачать всё (whisper + gemma + оба embedding)")
    p.add_argument("--only", choices=["whisper", "gemma", "embed-mini", "embed-sber"], default=None)
    args = p.parse_args()

    target_root = Path(args.target).resolve()
    token = os.getenv("HF_TOKEN") or None

    do_whisper = args.all or args.only in (None, "whisper")
    do_gemma = args.all or args.only == "gemma"
    do_mini = args.all or args.only == "embed-mini"
    do_sber = args.all or args.only == "embed-sber"

    if do_whisper:
        download_whisper(target_root / "whisper", args.whisper)
    if do_gemma:
        download_hf_snapshot(args.gemma, target_root / "gemma" / args.gemma.split("/")[-1], token)
    if do_mini:
        download_hf_snapshot(args.embed_mini, target_root / "embeddings" / args.embed_mini.split("/")[-1], token)
    if do_sber:
        download_hf_snapshot(args.embed_sber, target_root / "embeddings" / args.embed_sber.split("/")[-1].replace("/", "--"), token)

    print("\n--- Пути для .env ---")
    print(f"WHISPER_MODELS_ROOT={target_root / 'whisper'}")
    print(f"GEMMA_MODEL_PATH={target_root / 'gemma' / args.gemma.split('/')[-1]}")
    print(f"EMBEDDING_MODEL_MINI_PATH={target_root / 'embeddings' / args.embed_mini.split('/')[-1]}")
    print(f"EMBEDDING_MODEL_SBER_PATH={target_root / 'embeddings' / args.embed_sber.split('/')[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
