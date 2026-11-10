"""OCR engines. Importing this registers them; none loads its models until first used."""

import app.ocr.engines.rapidocr_engine  # noqa: F401
import app.ocr.engines.tesseract_engine  # noqa: F401
