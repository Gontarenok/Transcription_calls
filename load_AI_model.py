from sentence_transformers import SentenceTransformer

EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


print("Loading embed model:", EMBED_MODEL_NAME)
model = SentenceTransformer(EMBED_MODEL_NAME)