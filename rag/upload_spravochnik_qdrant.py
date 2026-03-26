# src/upload_spravochnik_qdrant.py
import json
import os
from pathlib import Path
from typing import List

import numpy as np
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest
from sentence_transformers import SentenceTransformer

load_dotenv()

# Настройки
SPRAVOCHNIK_PATH = Path("spravochnik.json")
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API = os.getenv("QDRANT_API")
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME")


# EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"  # или "sentence-transformers/all-MiniLM-L6-v2"
EMBED_MODEL_NAME = os.getenv("EMBEDDING_MODEL_SBER_PATH")  # путь к SBERT (локальный)
BATCH_SIZE = 128


def load_spravochnik(path: Path):
    return json.loads(path.read_text(encoding="utf8"))


def init_qdrant_client():
    print("Creating Qdrant client...")
    client = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API,
        timeout=60,
        prefer_grpc=False,
        https=True,
    )

    # Отключаем TLS verify для корпоративного сертификата
    client._client.http.client.verify = False

    return client


def collection_exists(client: QdrantClient, name: str) -> bool:
    try:
        cols = client.get_collections().collections
        return any(c.name == name for c in cols)
    except Exception as e:
        print("⚠️ Cannot fetch collections:", e)
        return False


def create_collection(client: QdrantClient, vector_size: int):
    if collection_exists(client, COLLECTION_NAME):
        print("Collection exists — deleting:", COLLECTION_NAME)
        client.delete_collection(COLLECTION_NAME)

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=rest.VectorParams(
            size=vector_size,
            distance=rest.Distance.COSINE
        )
    )
    print("✅ Created collection:", COLLECTION_NAME)


def embed_texts(model: SentenceTransformer, texts: List[str], batch_size: int = 64):
    embs = model.encode(texts, batch_size=batch_size, convert_to_numpy=True, show_progress_bar=False)
    # normalize for cosine search in Qdrant (if using Cosine in Qdrant, normalization optional)
    # but Qdrant's Cosine works without normalization; we'll normalize to be consistent with faiss flows
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs = embs / norms
    return embs


def upsert_in_batches(client: QdrantClient, records: List[dict], embeddings: np.ndarray, batch_size: int = BATCH_SIZE):
    total = len(records)
    assert embeddings.shape[0] == total
    for i in range(0, total, batch_size):
        end = min(i + batch_size, total)
        points = []
        for idx in range(i, end):
            rec = records[idx]
            emb = embeddings[idx].astype(float).tolist()
            payload = {
                "topic": rec.get("topic"),
                "subtopic": rec.get("subtopic"),
                "keywords": rec.get("keywords", []),
                "description": rec.get("description", ""),
                "doc_text": rec.get("doc_text", "")
            }
            # id, используем индекс
            pid = idx
            points.append(rest.PointStruct(id=pid, vector=emb, payload=payload))
        client.upsert(collection_name=COLLECTION_NAME, points=points)
        print(f"Upserted {i}..{end}")


def main():
    assert SPRAVOCHNIK_PATH.exists(), f"spravochnik not found: {SPRAVOCHNIK_PATH}"
    recs = load_spravochnik(SPRAVOCHNIK_PATH)
    print(f"Loaded {len(recs)} records from spravochnik")

    # load embed model
    print("Loading embed model:", EMBED_MODEL_NAME)
    model = SentenceTransformer(EMBED_MODEL_NAME)

    # prepare texts
    texts = [r.get("doc_text", f"{r.get('topic')} | {r.get('subtopic')}") for r in recs]

    # embed in batches
    all_embs = embed_texts(model, texts, batch_size=64)
    print("Embeddings shape:", all_embs.shape)

    # init qdrant
    client = init_qdrant_client()
    vector_size = all_embs.shape[1]
    create_collection(client, vector_size=vector_size)

    # upsert
    upsert_in_batches(client, recs, all_embs, batch_size=BATCH_SIZE)
    print("Done. You can now search the collection:", COLLECTION_NAME)


if __name__ == "__main__":
    main()
