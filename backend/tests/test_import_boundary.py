"""Covers the API/worker boundary in clean subprocesses, with heavy packages made unimportable."""

import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
# Render's free tier allows 512 MB. This leaves room for real work on top of the imports.
API_IMPORT_CEILING_MB = 150

BLOCK_HEAVY_IMPORTS = """
    import sys

    HEAVY = {"torch", "torchvision", "open_clip", "cv2", "PIL", "pandas", "numpy",
             "onnxruntime", "rapidocr", "rapidocr_onnxruntime", "pypdf", "boto3"}

    class Blocker:
        def find_spec(self, name, path=None, target=None):
            if name.partition(".")[0] in HEAVY:
                raise ImportError(f"{name} must not be imported by this entry point")
            return None

    sys.meta_path.insert(0, Blocker())
"""


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", textwrap.dedent(BLOCK_HEAVY_IMPORTS) + textwrap.dedent(code)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_api_loads_without_heavy_packages_and_within_memory_ceiling() -> None:
    result = _run(
        """
        import resource

        import app.api.main

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes, and Linux reports kilobytes.
        print(peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024)
        """
    )
    assert result.returncode == 0, result.stderr
    peak_mb = float(result.stdout.strip().splitlines()[-1])
    assert peak_mb < API_IMPORT_CEILING_MB, f"API import used {peak_mb:.0f} MB"


def test_worker_placeholder_runs() -> None:
    result = _run(
        """
        import worker

        worker.main()
        """
    )
    assert result.returncode == 0, result.stderr
    assert "Worker placeholder" in result.stdout


def test_worker_does_not_need_web_packages() -> None:
    # The worker has no web dependency installed, so shared code must not import one.
    result = _run(
        """
        import worker

        assert "fastapi" not in sys.modules
        assert "starlette" not in sys.modules
        """
    )
    assert result.returncode == 0, result.stderr
