"""Compares OCR engines (D7): RapidOCR, which the worker uses, against Tesseract.

Run from backend/ in the ai-catalog conda environment, which has the tesseract program:

    python tools/compare_ocr.py [--photos ../samples/photos] [--out ocr-comparison.md]

Two kinds of images are read by every registered engine:
- Generated images with known text, in conditions packaging photos have: small or large type,
  rotation, blur, low contrast, noise and busy backgrounds. These are scored exactly.
- Real photos, if a folder is given. They have no known text, so the report lists what each
  engine read, and which product codes (SKU-like codes and valid barcodes) it found, for a
  person to check against the photo.

Nothing here runs in production; the report goes into the docs.
"""

import argparse
import io
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalog.identifiers import gtin_check_digit_valid  # noqa: E402
from app.ocr import get_ocr_engine  # noqa: E402

ENGINES = ("rapidocr", "tesseract")
_CODE = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9][A-Z0-9-]{4,}[A-Z0-9]\b")
_DIGITS = re.compile(r"\b\d[\d ]{6,16}\d\b")
_FONTS = (
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
)


@dataclass(frozen=True)
class Case:
    """A generated image and the text on it."""

    name: str
    image: Image.Image
    lines: list[str]


def _font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size=size)


def _canvas(lines: list[str], font_path: str, size: int, fg: str, bg: str) -> Image.Image:
    font = _font(font_path, size)
    width = max(int(font.getlength(line)) for line in lines) + 80
    height = int(size * 1.6) * len(lines) + 60
    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((40, 30 + index * int(size * 1.6)), line, fill=fg, font=font)
    return image


def generated_cases(seed: int = 7) -> list[Case]:
    """Packaging-like text in conditions that make OCR harder, with the answer known."""
    rng = random.Random(seed)  # noqa: S311 - reproducible test images, not security
    texts = [
        ["Model AK-1003", "Capacity 1.5 L", "EAN 4006381333924"],
        ["SKU KT-1042-BLK", "Net weight 610 g", "Made in Portugal"],
        ["Art.-Nr. 4711-02", "Inhalt 250 ml", "Spülmaschinenfest"],
        ["Item No. 88-2301", "Size 30 x 20 x 10 cm", "UPC 036000291452"],
    ]
    cases: list[Case] = []
    for index, lines in enumerate(texts):
        font = _FONTS[index % len(_FONTS)]
        base = _canvas(lines, font, 44, "black", "white")
        cases.append(Case(f"clean-{index}", base, lines))
        cases.append(Case(f"small-{index}", _canvas(lines, font, 16, "black", "white"), lines))
        cases.append(
            Case(
                f"rotated-{index}",
                base.rotate(rng.choice([-7, 6]), expand=True, fillcolor="white"),
                lines,
            )
        )
        cases.append(Case(f"blurred-{index}", base.filter(ImageFilter.GaussianBlur(1.6)), lines))
        cases.append(
            Case(f"low-contrast-{index}", _canvas(lines, font, 40, "#9a9a9a", "#c8c8c8"), lines)
        )
        light = _canvas(lines, font, 40, "white", "#1f4e79")
        cases.append(Case(f"light-on-dark-{index}", light, lines))
        noisy = base.copy()
        pixels = noisy.load()
        for _ in range(noisy.width * noisy.height // 12):
            x, y = rng.randrange(noisy.width), rng.randrange(noisy.height)
            value = rng.randrange(256)
            pixels[x, y] = (value, value, value)
        cases.append(Case(f"noisy-{index}", noisy, lines))
        buffer = io.BytesIO()
        base.save(buffer, format="JPEG", quality=15)
        cases.append(Case(f"jpeg-q15-{index}", Image.open(io.BytesIO(buffer.getvalue())), lines))
    return cases


def _distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b))
            )
        previous = current
    return previous[-1]


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def char_accuracy(expected: str, read: str) -> float:
    """1 minus the edit distance as a share of the expected text, floored at 0."""
    expected, read = _squash(expected), _squash(read)
    if not expected:
        return 1.0
    return max(0.0, 1 - _distance(expected, read) / len(expected))


def codes_in(text: str) -> list[str]:
    """Product codes a text holds: hyphenated SKU-like codes, and barcodes that check out."""
    upper = text.upper()
    found = set(_CODE.findall(upper))
    for digits in _DIGITS.findall(text):
        joined = digits.replace(" ", "")
        if gtin_check_digit_valid(joined):
            found.add(joined)
    return sorted(found)


def _expected_codes(lines: list[str]) -> list[str]:
    return codes_in("\n".join(lines))


def run(photos: Path | None, out: Path) -> None:
    engines = {name: get_ocr_engine(name) for name in ENGINES}
    for engine in engines.values():
        engine.read(Image.new("RGB", (64, 64), "white"), 0.5)  # load models before timing

    report = ["# OCR comparison: RapidOCR and Tesseract", ""]
    report.append("Engines: " + ", ".join(f"`{e.version}`" for e in engines.values()) + ".")
    report.append("")

    cases = generated_cases()
    scores: dict[str, dict[str, list[float]]] = {name: {} for name in engines}
    code_hits: dict[str, list[tuple[int, int]]] = {name: [] for name in engines}
    times: dict[str, list[float]] = {name: [] for name in engines}
    for case in cases:
        condition = case.name.rsplit("-", 1)[0]
        expected = "\n".join(case.lines)
        wanted = _expected_codes(case.lines)
        for name, engine in engines.items():
            started = time.perf_counter()
            text = engine.read(case.image, 0.5).text
            times[name].append(time.perf_counter() - started)
            scores[name].setdefault(condition, []).append(char_accuracy(expected, text))
            got = set(codes_in(text))
            code_hits[name].append((sum(1 for code in wanted if code in got), len(wanted)))

    report += [
        "## Generated images with known text",
        "",
        f"{len(cases)} images: 4 texts in 8 conditions. Character accuracy is 1 minus the edit "
        "distance as a share of the text, averaged per condition.",
        "",
        "| Condition | " + " | ".join(engines) + " |",
        "|---|" + "---|" * len(engines),
    ]
    conditions = list(next(iter(scores.values())))
    for condition in conditions:
        cells = [f"{statistics.mean(scores[name][condition]):.0%}" for name in engines]
        report.append(f"| {condition} | " + " | ".join(cells) + " |")
    overall = [
        f"{statistics.mean(v for values in scores[name].values() for v in values):.0%}"
        for name in engines
    ]
    report.append("| **All** | " + " | ".join(f"**{cell}**" for cell in overall) + " |")
    codes_row = [
        f"{sum(h for h, _ in code_hits[name])} of {sum(t for _, t in code_hits[name])}"
        for name in engines
    ]
    report.append("| Product codes read exactly | " + " | ".join(codes_row) + " |")
    time_row = [f"{statistics.median(times[name]) * 1000:.0f} ms" for name in engines]
    report.append("| Median time per image | " + " | ".join(time_row) + " |")
    report.append("")

    if photos and photos.is_dir():
        files = sorted(
            path
            for path in photos.iterdir()
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        )
        report += [
            "## Real photos",
            "",
            "No known text, so each engine's reading is listed for checking against the photo.",
            "",
        ]
        for path in files:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
            report.append(f"### {path.name} ({image.width} x {image.height})")
            report.append("")
            for name, engine in engines.items():
                started = time.perf_counter()
                result = engine.read(image, 0.5)
                elapsed = time.perf_counter() - started
                codes = codes_in(result.text)
                report.append(
                    f"**{name}**: {len(result.lines)} lines in {elapsed:.1f} s. "
                    f"Codes: {', '.join(codes) if codes else 'none'}."
                )
                report.append("")
                report.append("```text")
                report.append(result.text[:1500] or "(nothing read)")
                report.append("```")
                report.append("")
    out.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Wrote {out}")  # noqa: T201


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--photos", type=Path, default=None, help="A folder of real photos.")
    parser.add_argument("--out", type=Path, default=Path("ocr-comparison.md"))
    args = parser.parse_args()
    run(args.photos, args.out)


if __name__ == "__main__":
    main()
