"""Times each image step, for the M5A throughput figures and the EC2 cap check.

Run from backend/ (Titan calls cost $0.00008 each, with the Bedrock key in .env):

    python tools/benchmark_images.py --photos ../samples [--embed]

Each photo is also scaled to 4032 x 3024, the size of a 12-megapixel phone photo, since
uploads are usually larger than these samples. Steps: load, hash and colour, thumbnail, OCR
(RapidOCR, from M4) and, with --embed, a Titan embedding. Timings on a laptop are a lower
bound for the t3a.medium worker; real worker timings are stored with every processed image.
"""

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ocr import get_ocr_engine  # noqa: E402
from app.vision import (  # noqa: E402
    colour_signature,
    for_embedding,
    get_embedder,
    load_rgb,
    phash,
    thumbnail,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--photos", type=Path, required=True)
    parser.add_argument("--embed", action="store_true", help="Also call Titan (costs money).")
    args = parser.parse_args()

    paths = sorted(
        p for p in args.photos.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    engine = get_ocr_engine("rapidocr")
    embedder = get_embedder() if args.embed else None
    rows: dict[str, list[float]] = {}
    with tempfile.TemporaryDirectory() as workdir:
        for path in paths:
            for label, scale in (("as uploaded", None), ("12 MP", (4032, 3024))):
                source = path
                if scale:
                    big = load_rgb(path).resize(scale)
                    source = Path(workdir) / f"{path.stem}-big.jpg"
                    big.save(source, format="JPEG", quality=90)
                times: dict[str, float] = {}
                started = time.perf_counter()
                image = load_rgb(source)
                times["load"] = time.perf_counter() - started
                started = time.perf_counter()
                phash(image)
                colour_signature(image)
                times["hash + colour"] = time.perf_counter() - started
                started = time.perf_counter()
                thumbnail(image, 320, 82)
                times["thumbnail"] = time.perf_counter() - started
                started = time.perf_counter()
                engine.read(image, 0.5)
                times["OCR"] = time.perf_counter() - started
                if embedder is not None:
                    started = time.perf_counter()
                    embedder.embed(for_embedding(image, 2048))
                    times["Titan embed"] = time.perf_counter() - started
                for step, seconds in times.items():
                    rows.setdefault(f"{step} ({label})", []).append(seconds)
    print("| Step | Median | Slowest |")  # noqa: T201
    print("|---|---|---|")  # noqa: T201
    for step, values in rows.items():
        print(f"| {step} | {statistics.median(values):.2f} s | {max(values):.2f} s |")  # noqa: T201


if __name__ == "__main__":
    main()
