"""Amazon Titan Multimodal Embeddings G1 on Bedrock, in Sydney (D34).

Request: {"inputImage": <base64 JPEG>, "embeddingConfig": {"outputEmbeddingLength": 1024}}.
Response: {"embedding": [...]}. Credentials come from the environment: the instance role on
EC2, and the Bedrock API key in .env locally. boto3 loads on the first call.
"""

import base64
import json
import threading
from typing import Any

from app.config import get_embeddings_config
from app.utils.logger import get_logger
from app.vision.embedders.base import EmbeddingUnavailableError, register_embedder

logger = get_logger(__name__)


class TitanEmbedder:
    """Calls the configured Titan model."""

    name = "bedrock"

    def __init__(self) -> None:
        self._config = get_embeddings_config()
        self._client: Any = None
        self._lock = threading.Lock()

    def _runtime(self) -> Any:
        with self._lock:
            if self._client is None:
                import boto3
                from botocore.config import Config

                self._client = boto3.client(
                    "bedrock-runtime",
                    region_name=self._config["region"],
                    config=Config(
                        read_timeout=self._config["timeout_seconds"],
                        retries={"max_attempts": 3, "mode": "standard"},
                    ),
                )
            return self._client

    def embed(self, jpeg: bytes) -> list[float]:
        """Embed one JPEG.

        Raises:
            EmbeddingUnavailableError: on any failure, naming the error class.
        """
        body = {
            "inputImage": base64.b64encode(jpeg).decode(),
            "embeddingConfig": {"outputEmbeddingLength": self._config["dimension"]},
        }
        try:
            response = self._runtime().invoke_model(
                modelId=self._config["model_id"], body=json.dumps(body)
            )
            embedding = json.loads(response["body"].read())["embedding"]
        except Exception as exc:  # noqa: BLE001 - botocore raises many types
            logger.warning("Embedding failed: error_class=%s", type(exc).__name__)
            raise EmbeddingUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        if len(embedding) != self._config["dimension"]:
            raise EmbeddingUnavailableError(
                f"Expected {self._config['dimension']} numbers, got {len(embedding)}"
            )
        return [float(value) for value in embedding]


register_embedder(TitanEmbedder.name, TitanEmbedder)
