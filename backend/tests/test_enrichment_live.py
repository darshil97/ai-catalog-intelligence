"""A live check of enrichment against the real model on Bedrock (D37, decision 11).

Skipped unless LIVE_LLM_TEST=1, with the Bedrock API key in backend/.env. It costs about a
tenth of a cent. It proves what the scripted backend can't: that gpt-oss-120b's real replies
get through the grounding checks within the task's attempts, and leave out what isn't settled.
"""

import json
import logging
import os
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.config import (
    get_enrichment_config,
    get_llm_config,
    get_llm_task_config,
    get_normalization_config,
)
from app.enrichment import build_facts
from app.enrichment.grounding import Grounding
from app.enrichment.models import EnrichmentReply
from app.llm.backends.bedrock import BedrockBackend
from app.llm.base import LlmRequest
from app.llm.prompts import render_prompt
from app.llm.runner import extract_json

# Run with -o log_cli=true --log-cli-level=WARNING to see the replies.
_show = logging.getLogger(__name__).warning

pytestmark = pytest.mark.skipif(
    os.environ.get("LIVE_LLM_TEST") != "1", reason="set LIVE_LLM_TEST=1 to call the real model"
)


def _row(code: str, name: str, state: str, shown: str | None = None,
         number: str | None = None, family: str | None = None) -> dict[str, Any]:  # fmt: skip
    return {
        "attribute_id": uuid4(),
        "code": code,
        "name": name,
        "unit_family": family,
        "state": state,
        "shown": shown,
        "value_number": Decimal(number) if number else None,
        "reviewer_value": None,
        "selected_value_id": uuid4() if shown else None,
    }


def test_real_model_writes_grounded_text() -> None:
    settings = get_enrichment_config()
    config = get_llm_task_config("enrichment")
    rows = [
        _row("product_name", "Product name", "verified", "Insulated water bottle"),
        _row("brand", "Brand", "verified", "Milton"),
        _row("capacity", "Capacity", "verified", "1 L", "1000", "volume"),
        _row("material", "Material", "verified", "Stainless steel"),
        _row("net_weight", "Net weight", "verified", "380 g", "380", "mass"),
        _row("model_number", "Model number", "verified", "THERMOSTEEL-1000"),
        _row("description", "Description", "verified",
             "Double wall vacuum insulated bottle. Keeps drinks hot for 24 hours. Leak proof lid."),
        # Not settled: must not appear.
        _row("color", "Colour", "conflict"),
        _row("height", "Height", "missing"),
        _row("price", "Price", "verified", "899 INR", "899"),
    ]  # fmt: skip
    category = {"path": "Dining › Drinkware › Water bottles and flasks", "source": "llm",
                "state": "assigned"}  # fmt: skip
    sheet = build_facts(rows, category, settings)
    system, user = render_prompt(
        config["prompt"],
        product_key="THERMOSTEEL-1000",
        facts=[{"id": f.id, "name": f.name, "display": f.display} for f in sheet.facts],
        title_only=[f.id for f in sheet.facts if f.title_only],
        **{k: settings[k] for k in ("title_max_chars", "bullets_min", "bullets_max",
                                    "bullet_max_chars", "sentences_min", "sentences_max",
                                    "description_max_chars")},
    )  # fmt: skip
    grounding = Grounding(settings, get_normalization_config())
    price = get_llm_config()["prices"][config["model_id"]]
    backend = BedrockBackend()

    feedback, checked, cost = "", None, Decimal(0)
    for attempt in range(1, config["max_attempts"] + 1):
        reply = backend.complete(
            LlmRequest(
                model_id=config["model_id"],
                system=system,
                user=user + feedback,
                max_tokens=config["max_tokens"],
                temperature=config["temperature"],
                timeout_seconds=config["timeout_seconds"],
            )
        )
        cost += (
            Decimal(str(price["input"])) * (reply.input_tokens or 0)
            + Decimal(str(price["output"])) * (reply.output_tokens or 0)
        ) / 1_000_000
        _show(f"\nattempt {attempt}: in={reply.input_tokens} out={reply.output_tokens}")
        _show(reply.text)
        try:
            checked = grounding.check(EnrichmentReply.model_validate(extract_json(reply.text)),
                                      sheet.facts)  # fmt: skip
            break
        except ValueError as exc:
            _show(f"REJECTED: {exc}")
            feedback = (
                f"\n\nYour previous reply was rejected: ValueError: {exc}\n"
                "Reply again with only the JSON object described."
            )
    _show(f"cost=${cost:.6f}")
    assert checked is not None, "no reply passed the checks within the task's attempts"
    _show(json.dumps({"title": checked.title, "description": checked.description,
                      "bullets": checked.bullets}, indent=2))  # fmt: skip
    text = " ".join([checked.title, checked.description, *checked.bullets]).lower()
    assert "899" not in text and "₹" not in text
    assert all(colour not in text for colour in ("black", "blue", "red", "white", "silver"))
    assert "THERMOSTEEL-1000".lower() not in " ".join(checked.bullets).lower()
