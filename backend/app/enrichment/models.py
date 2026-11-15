"""The shape of the LLM's reply. Lengths and counts are checked by the grounding rules."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Claim(BaseModel):
    """One title, sentence or bullet, and the facts it uses."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(..., min_length=1, max_length=2000)
    facts: list[str] = Field(..., max_length=30)

    @field_validator("text")
    @classmethod
    def _one_line(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("text is empty")
        return value


class EnrichmentReply(BaseModel):
    """A product's title, description sentences and bullets."""

    model_config = ConfigDict(extra="ignore")

    title: Claim
    description: list[Claim] = Field(..., max_length=20)
    bullets: list[Claim] = Field(..., max_length=20)
