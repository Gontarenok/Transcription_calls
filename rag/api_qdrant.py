# Проверка информации о векторах точек

from qdrant_client import QdrantClient
from dotenv import load_dotenv
import os

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API = os.getenv("QDRANT_API")
COLLECTION_NAME = "open-webui_files"
BATCH_SIZE = 1000

def audit_collection():
    client = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API,
        timeout=60
    )

    collection_info = client.get_collection(COLLECTION_NAME)
    total_points = collection_info.points_count
    print(f"Collection '{COLLECTION_NAME}' total points: {total_points}")

    offset = 0
    points_without_vectors = 0
    points_with_vectors = 0
    points_without_payload = 0
    points_with_payload = 0

    while offset < total_points:
        scroll_result = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=BATCH_SIZE,
            offset=offset,
            with_payload=True,
            with_vectors=True
        )

        points_batch = scroll_result.points  # теперь правильно берем список точек

        for p in points_batch:
            if not p.get("vector"):
                points_without_vectors += 1
            else:
                points_with_vectors += 1

            if not p.get("payload"):
                points_without_payload += 1
            else:
                points_with_payload += 1

        offset += BATCH_SIZE
        print(f"Processed {min(offset, total_points)}/{total_points} points...")

    print("\n=== AUDIT RESULT ===")
    print(f"Total points: {total_points}")
    print(f"Points with vectors: {points_with_vectors}")
    print(f"Points without vectors: {points_without_vectors}")
    print(f"Points with payload: {points_with_payload}")
    print(f"Points without payload: {points_without_payload}")

if __name__ == "__main__":
    audit_collection()