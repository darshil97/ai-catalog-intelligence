"""Amazon Bedrock through LangChain's ChatBedrockConverse (D24).

Credentials come from the environment: the instance role on EC2, and the Bedrock API key in
.env locally. LangChain and boto3 load on the first call, so importing this is cheap.

Replies differ by model. gpt-oss-120b returns a list of blocks, with its reasoning in
reasoning_content blocks beside the text, while Claude returns a plain string. Only text is
kept, so reasoning is never stored or parsed.
"""

from typing import Any

from app.config import get_llm_config
from app.llm.base import LlmReply, LlmRequest, LlmUnavailableError, register_chat_backend
from app.utils.logger import get_logger

logger = get_logger(__name__)

PROVIDER = "bedrock"


def _text_only(content: Any) -> str:
    """Return the text of a reply, leaving out reasoning and any other block type."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


class BedrockBackend:
    """Calls Bedrock models in the configured region."""

    name = PROVIDER

    def __init__(self) -> None:
        self._clients: dict[tuple[str, int, float, int], Any] = {}

    def _client(self, request: LlmRequest) -> Any:
        key = (request.model_id, request.max_tokens, request.temperature, request.timeout_seconds)
        if key not in self._clients:
            from botocore.config import Config
            from langchain_aws import ChatBedrockConverse

            config = get_llm_config()
            self._clients[key] = ChatBedrockConverse(
                model=request.model_id,
                region_name=config["region"],
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                config=Config(
                    connect_timeout=config["connect_timeout_seconds"],
                    read_timeout=request.timeout_seconds,
                    # Throttling is retried here; the caller handles everything else.
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        return self._clients[key]

    def complete(self, request: LlmRequest) -> LlmReply:
        """Send the request to Bedrock.

        Raises:
            LlmUnavailableError: on any failure to get a reply, naming the error class.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        try:
            message = self._client(request).invoke(
                [SystemMessage(content=request.system), HumanMessage(content=request.user)]
            )
        except Exception as exc:  # noqa: BLE001 - botocore and LangChain raise many types
            logger.warning(
                "Bedrock call failed: model_id=%s error_class=%s",
                request.model_id,
                type(exc).__name__,
            )
            raise LlmUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        usage = getattr(message, "usage_metadata", None) or {}
        return LlmReply(
            text=_text_only(message.content),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )


register_chat_backend(PROVIDER, BedrockBackend)
