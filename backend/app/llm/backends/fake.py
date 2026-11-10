"""A scripted backend for tests, so no test calls a real model.

Queue replies with `replies.append(...)`: a string is returned as the model's text, and an
exception is raised. With nothing queued, a call fails as an unavailable model would.
"""

from app.llm.base import LlmReply, LlmRequest, LlmUnavailableError, register_chat_backend

PROVIDER = "fake"


class FakeBackend:
    """Returns queued replies in order, and records every request it receives."""

    name = PROVIDER

    def __init__(self) -> None:
        self.replies: list[str | Exception] = []
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmReply:
        """Return the next queued reply, or raise it if it is an exception."""
        self.requests.append(request)
        if not self.replies:
            raise LlmUnavailableError("No reply was scripted for this call")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LlmReply(text=reply, input_tokens=100, output_tokens=20)

    def clear(self) -> None:
        """Forget queued replies and recorded requests."""
        self.replies.clear()
        self.requests.clear()


register_chat_backend(PROVIDER, FakeBackend)
