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
             "onnxruntime", "rapidocr", "rapidocr_onnxruntime", "pypdf", "boto3",
             "openpyxl", "defusedxml", "jinja2", "langchain_aws", "langchain_core",
             "pdfplumber", "pdfminer", "pypdfium2", "rapidocr", "onnxruntime", "shapely",
             "pytesseract"}

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

        # On Linux, ru_maxrss carries over from the parent that started this process, here the
        # pytest process, which earlier tests may have filled with OCR models. VmHWM is this
        # program's own peak. macOS has no /proc, and its ru_maxrss starts fresh (in bytes).
        try:
            with open("/proc/self/status") as status:
                hwm = next(line for line in status if line.startswith("VmHWM:"))
            print(int(hwm.split()[1]) / 1024)
        except OSError:
            print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024))
        """
    )
    assert result.returncode == 0, result.stderr
    peak_mb = float(result.stdout.strip().splitlines()[-1])
    assert peak_mb < API_IMPORT_CEILING_MB, f"API import used {peak_mb:.0f} MB"


def test_api_does_not_load_job_handlers() -> None:
    # Handlers will pull in heavy packages, and only the worker may load them.
    result = _run(
        """
        import app.api.main

        assert "app.jobs.handlers" not in sys.modules
        assert "app.worker.loop" not in sys.modules
        # The API never calls an LLM, so it must not even load the code that does.
        assert "app.llm" not in sys.modules
        """
    )
    assert result.returncode == 0, result.stderr


def test_worker_does_not_need_web_packages() -> None:
    # The worker has no web dependency installed, so shared code must not import one.
    result = _run(
        """
        import worker

        assert "fastapi" not in sys.modules
        assert "starlette" not in sys.modules
        names = __import__("app.jobs.base", fromlist=["x"]).get_registered_handler_names()
        assert {"noop", "ingest_file", "extract_select", "extract_rows", "documents_plan",
                "documents_read", "document_values"} <= names
        """
    )
    assert result.returncode == 0, result.stderr


def test_local_worker_refuses_the_production_database() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "worker.py"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            "PATH": "/usr/bin",
            "APP_ENV": "local",
            "DATABASE_URL": "postgresql://u:p@<PROD_DB_ENDPOINT>-pooler.example/db",
        },
    )
    assert result.returncode == 2
    assert "Refusing to start" in result.stdout
