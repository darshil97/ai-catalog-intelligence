"""Loads prompts from app/templates/prompts, rendered with Jinja2.

A prompt file holds the instructions, then a line containing only INPUT_MARKER, then the data
the instructions apply to. The two halves are sent as separate messages, so text from a
supplier's file is never part of the instructions.
"""

from functools import cache
from pathlib import Path
from typing import Any

_PROMPT_DIR = Path(__file__).resolve().parents[1] / "templates" / "prompts"
INPUT_MARKER = "<!-- input -->"


@cache
def _environment() -> Any:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    return Environment(
        loader=FileSystemLoader(_PROMPT_DIR),
        trim_blocks=True,
        lstrip_blocks=True,
        # Prompts are plain text for a model, not HTML for a browser.
        autoescape=False,  # noqa: S701
        undefined=StrictUndefined,
        keep_trailing_newline=False,
    )


def render_prompt(name: str, **variables: Any) -> tuple[str, str]:
    """Render a prompt and split it into instructions and input.

    Args:
        name: Path under the prompts directory, without ".md", such as
            "extraction/column_mapping".

    Returns:
        The system message and the user message.

    Raises:
        FileNotFoundError: if the prompt doesn't exist.
        ValueError: if the file has no input marker.
    """
    from jinja2 import TemplateNotFound

    try:
        template = _environment().get_template(f"{name}.md")
    except TemplateNotFound as exc:
        raise FileNotFoundError(f"No prompt named {name!r} in {_PROMPT_DIR}") from exc
    system, marker, user = template.render(**variables).partition(INPUT_MARKER)
    if not marker:
        raise ValueError(f"Prompt {name!r} has no {INPUT_MARKER} line")
    return system.strip(), user.strip()
