from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from typing import Iterable

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest
from sentence_transformers import SentenceTransformer

from api_service.config import settings
from db.crud import compose_catalog_doc_text, parse_multiline_text
from db.models import TopicCatalogEntry
from model_paths import model_settings

DEFAULT_COLLECTION = settings.qdrant_collection_topics or "topics_spravochnik"


@dataclass
class CatalogPayload:
    topic: str
    subtopic: str
    description: str
    keywords: list[str]
    synonyms: list[str]
    negative_keywords: list[str]
    doc_text: str


def normalize_line_items(text: str | None) -> list[str]:
    return parse_multiline_text(text)


def build_doc_text(topic: str, subtopic: str, description: str, keywords_text: str, synonyms_text: str | None = None) -> str:
    return compose_catalog_doc_text(topic, subtopic, description, keywords_text, synonyms_text)


def entry_source_hash(topic: str, subtopic: str, description: str, keywords_text: str, synonyms_text: str | None) -> str:
    raw = "||".join([topic.strip(), subtopic.strip(), description.strip(), keywords_text.strip(), (synonyms_text or "").strip()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def qdrant_enabled() -> bool:
    return bool(settings.qdrant_url)


def init_embedder() -> SentenceTransformer:
    return SentenceTransformer(model_settings.embedding_model_path)


def init_qdrant() -> QdrantClient:
    if not settings.qdrant_url:
        raise RuntimeError("QDRANT_URL is not configured")
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=60, https=True)
    return client


def ensure_collection(client: QdrantClient, embedder: SentenceTransformer, collection_name: str = DEFAULT_COLLECTION):
    vector_size = int(embedder.get_sentence_embedding_dimension())
    collections = {c.name for c in client.get_collections().collections}
    if collection_name in collections:
        return
    client.create_collection(
        collection_name=collection_name,
        vectors_config=rest.VectorParams(size=vector_size, distance=rest.Distance.COSINE),
    )


def _qdrant_point_id_for_upsert(entry: TopicCatalogEntry) -> int | str:
    """Qdrant принимает только unsigned int или UUID; строки вида '123' — нет."""
    raw = (entry.qdrant_point_id or "").strip()
    if raw:
        if raw.isdigit():
            return int(raw)
        try:
            uuid.UUID(raw)
            return raw
        except ValueError:
            pass
    return int(entry.id)


def encode_texts(embedder: SentenceTransformer, texts: list[str]) -> np.ndarray:
    embs = embedder.encode(texts, batch_size=64, convert_to_numpy=True, show_progress_bar=False)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return embs / norms


def build_payload(entry: TopicCatalogEntry) -> CatalogPayload:
    return CatalogPayload(
        topic=entry.topic_name,
        subtopic=entry.subtopic_name,
        description=entry.description,
        keywords=normalize_line_items(entry.keywords_text),
        synonyms=normalize_line_items(entry.synonyms_text),
        negative_keywords=normalize_line_items(entry.negative_keywords_text),
        doc_text=entry.doc_text,
    )


def sync_catalog_entries(entries: Iterable[TopicCatalogEntry], collection_name: str = DEFAULT_COLLECTION) -> list[str]:
    entries = list(entries)
    if not entries or not qdrant_enabled():
        return []

    client = init_qdrant()
    embedder = init_embedder()
    ensure_collection(client, embedder, collection_name=collection_name)

    texts = [entry.doc_text for entry in entries]
    vectors = encode_texts(embedder, texts)
    synced_ids: list[str] = []

    for idx, entry in enumerate(entries):
        point_id = _qdrant_point_id_for_upsert(entry)
        payload = build_payload(entry)
        client.upsert(
            collection_name=collection_name,
            points=[
                rest.PointStruct(
                    id=point_id,
                    vector=vectors[idx].astype(float).tolist(),
                    payload={
                        "entry_id": entry.id,
                        "topic": payload.topic,
                        "subtopic": payload.subtopic,
                        "description": payload.description,
                        "keywords": payload.keywords,
                        "synonyms": payload.synonyms,
                        "negative_keywords": payload.negative_keywords,
                        "doc_text": payload.doc_text,
                        "is_active": entry.is_active,
                    },
                )
            ],
        )
        synced_ids.append(str(point_id))
    return synced_ids


def normalize_topic_catalog_text(raw: str) -> str:
    return re.sub(r"\s+", " ", raw or "").strip()