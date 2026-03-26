import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ModelSettings:
    whisper_models_root: str = os.getenv("WHISPER_MODELS_ROOT", os.path.join(os.path.dirname(__file__), "models", "whisper"))
    whisper_model_default: str = os.getenv("WHISPER_MODEL_DEFAULT", "large-v3")
    whisper_model_small: str = os.getenv("WHISPER_MODEL_SMALL", "small")
    whisper_model_medium: str = os.getenv("WHISPER_MODEL_MEDIUM", "medium")
    whisper_model_large: str = os.getenv("WHISPER_MODEL_LARGE", "large-v3")

    gemma_model_path: str = os.getenv("GEMMA_MODEL_PATH", os.path.join(os.path.dirname(__file__), "models", "gemma", "gemma-3-4b-it"))

    # Embeddings: поддерживаем новые имена из .env.example
    embedding_model_mini_path: str = os.getenv("EMBEDDING_MODEL_MINI_PATH", "")
    embedding_model_sber_path: str = os.getenv("EMBEDDING_MODEL_SBER_PATH", "")

    # Backward-compat alias; порядок фоллбэков: EMBEDDING_MODEL_PATH -> SBER -> MINI -> default
    embedding_model_path: str = (
        os.getenv("EMBEDDING_MODEL_PATH", "")
        or os.getenv("EMBEDDING_MODEL_SBER_PATH", "")
        or os.getenv("EMBEDDING_MODEL_MINI_PATH", "")
        or os.path.join(os.path.dirname(__file__), "models", "embeddings")
    )


model_settings = ModelSettings()