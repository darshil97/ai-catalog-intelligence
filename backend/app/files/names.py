"""Cleaning file names, which are only ever stored and shown, never used in paths or keys."""

import posixpath
import unicodedata

DEFAULT_NAME = "unnamed"


def clean_name(raw: str, max_length: int) -> str:
    """Return a displayable file name: no folders, control characters or surrounding spaces.

    Browsers and ZIP tools can send names with folders (both / and \\), control characters or
    odd Unicode forms. Long names keep their extension.
    """
    name = unicodedata.normalize("NFC", raw).replace("\\", "/")
    name = posixpath.basename(name.rstrip("/"))
    name = "".join(char for char in name if unicodedata.category(char) not in {"Cc", "Cf"})
    name = " ".join(name.split()).strip(" .") or DEFAULT_NAME
    if len(name) > max_length:
        stem, dot, extension = name.rpartition(".")
        if dot and 0 < len(extension) <= 10:
            name = stem[: max_length - len(extension) - 1] + "." + extension
        else:
            name = name[:max_length]
    return name


def extension_of(name: str) -> str:
    """Return the lowercase extension without its dot, or "" if there is none."""
    stem, dot, extension = name.rpartition(".")
    return extension.lower() if dot and stem else ""
