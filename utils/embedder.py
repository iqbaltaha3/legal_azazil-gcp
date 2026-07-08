# utils/embedder.py
from sentence_transformers import SentenceTransformer
from config.settings import EMBEDDING_MODEL

class Embedder:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
        return cls._instance

    def encode(self, text: str) -> list:
        return self.model.encode(text).tolist()