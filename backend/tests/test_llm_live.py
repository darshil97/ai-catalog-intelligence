"""A live check of column mapping against the real model on Bedrock.

Skipped unless LIVE_LLM_TEST=1, with the Bedrock API key in backend/.env. It costs a fraction
of a cent. It proves what the scripted backend can't: that the configured model is reachable,
that its reply's reasoning is kept out of the text, and that the real prompt gets a usable
mapping for headers no known name covers.
"""

import os
from typing import Any
from uuid import uuid4

import pytest

from app.config import get_extraction_config, get_llm_task_config
from app.ingest.spreadsheet.base import Cell, SheetRow
from app.ingest.spreadsheet.headers import build_known_names
from app.ingest.spreadsheet.mapping import (
    SOURCE_LLM,
    AttributeDef,
    LlmAnswer,
    MappingAnswer,
    map_sheet,
)
from app.llm.backends.bedrock import BedrockBackend
from app.llm.base import LlmRequest
from app.llm.prompts import render_prompt
from app.llm.runner import extract_json

pytestmark = pytest.mark.skipif(
    os.environ.get("LIVE_LLM_TEST") != "1", reason="set LIVE_LLM_TEST=1 to call the real model"
)

# The descriptions the migration seeds, so the model sees what the worker would show it.
_DESCRIPTIONS = {
    "sku": "The supplier's own code for the product.",
    "model_number": "The manufacturer's model or part number.",
    "gtin": "The barcode number, also called GTIN, EAN or UPC.",
    "product_name": "The product's name or short title.",
    "brand": "The brand or manufacturer name.",
    "description": "Longer text describing the product.",
    "color": "The product's colour.",
    "material": "What the product is made of.",
    "price": "The price of one unit, as the supplier lists it.",
    "currency": "The currency of the price.",
    "pack_quantity": "How many units come in one pack or carton.",
    "net_weight": "The product's weight without packaging.",
    "capacity": "How much the product holds.",
    "length": "The product's length or depth.",
    "width": "The product's width.",
    "height": "The product's height.",
    "supplier_category": "The supplier's own category.",
}


def test_real_model_maps_a_german_price_list() -> None:
    settings = get_extraction_config()
    config = get_llm_task_config("column_mapping")
    backend = BedrockBackend()
    table = [
        ["Preisliste 2026"],
        [],
        [
            "Bestellnr",
            "Bezeichnung",
            "Farbe",
            "Gew. netto (g)",
            "Bruttogewicht (g)",
            "VK netto",
            "Lager",
        ],
        ["4711", "Kaffeebecher Classic", "rot", "320", "410", "4,95", "120"],
        ["4712", "Teetasse Nordic", "blau", "280", "365", "5,49", "0"],
    ]
    rows = [
        SheetRow(number=number, cells=tuple(Cell(text=text) for text in texts))
        for number, texts in enumerate(table, start=1)
    ]
    attributes = [
        AttributeDef(id=uuid4(), code=code, name=code, data_type="text", description=text)
        for code, text in _DESCRIPTIONS.items()
    ]
    replies: list[str] = []

    def ask(variables: dict[str, Any], parser: Any) -> LlmAnswer:
        system, user = render_prompt(config["prompt"], **variables)
        reply = backend.complete(
            LlmRequest(
                model_id=config["model_id"],
                system=system,
                user=user,
                max_tokens=config["max_tokens"],
                temperature=config["temperature"],
                timeout_seconds=config["timeout_seconds"],
            )
        )
        replies.append(reply.text)
        assert reply.input_tokens and reply.output_tokens
        answer: MappingAnswer = parser(extract_json(reply.text))
        return LlmAnswer(answer=answer, model_run_id=uuid4(), calls=1)

    mapping = map_sheet(
        rows,
        merged=False,
        attributes=attributes,
        known=build_known_names(settings["header_names"]),
        settings=settings,
        find_saved=lambda fingerprint: None,
        ask_llm=ask,
    )
    by_header = {column.header: column.attribute for column in mapping.columns}
    assert mapping.source == SOURCE_LLM and mapping.header_row == 3, replies
    assert by_header["Bestellnr"] == "sku", replies
    assert by_header["Bezeichnung"] == "product_name", replies
    assert by_header["Farbe"] == "color", replies
    assert by_header["Gew. netto (g)"] == "net_weight", replies
    assert by_header["VK netto"] == "price", replies
    # Gross weight and stock aren't attributes, and must not be forced into one.
    assert by_header["Bruttogewicht (g)"] is None, replies
    assert by_header["Lager"] is None, replies
