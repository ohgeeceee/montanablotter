from __future__ import annotations

from typing import Iterable

import requests

import config


class OpenAIEmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        base_url: str = "https://api.openai.com/v1/embeddings",
    ) -> None:
        self.api_key = api_key or config.EMBEDDING_API_KEY
        self.model = model or config.EMBEDDING_MODEL
        self.dimensions = dimensions or config.EMBEDDING_DIMENSIONS
        self.base_url = base_url
        if not self.api_key:
            raise RuntimeError("MB_EMBEDDING_API_KEY or OPENAI_API_KEY is required for embeddings")

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        payload = {
            "input": list(texts),
            "model": self.model,
        }
        if self.dimensions:
            payload["dimensions"] = self.dimensions

        response = requests.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        return [item["embedding"] for item in data.get("data", [])]

    def embed_text(self, text: str) -> list[float]:
        embeddings = self.embed_texts([text])
        if not embeddings:
            raise RuntimeError("Embedding response was empty")
        return embeddings[0]
