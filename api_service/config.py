import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "5000"))

    api_key_911: str = os.getenv("API_KEY_911", "")
    api_key_kc: str = os.getenv("API_KEY_KC", "")
    api_key_admin: str = os.getenv("API_KEY_ADMIN", "")

    qdrant_url: str = os.getenv("QDRANT_URL", "")
    qdrant_api_key: str = os.getenv("QDRANT_API_KEY", "")
    qdrant_collection_topics: str = os.getenv("QDRANT_COLLECTION_TOPICS", "topics_spravochnik")


settings = Settings()