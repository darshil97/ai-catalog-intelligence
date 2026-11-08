"""Covers the deploy checks and decisions. Nothing here touches AWS or a database."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from app.worker.selfcheck import migration_head

BACKEND = Path(__file__).resolve().parents[1]


def _load_production_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "production", BACKEND / "infra" / "ci" / "production.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_head_matches_alembic() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "heads"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.split()[0] == migration_head()


def test_diverged_migrations_are_refused(tmp_path: Path) -> None:
    # Two migrations from the same parent would leave the schema check ambiguous.
    for name in ("a", "b"):
        (tmp_path / f"{name}.py").write_text(
            f'revision = "{name}"\ndown_revision = "0001"\n', encoding="utf-8"
        )
    (tmp_path / "base.py").write_text('revision = "0001"\ndown_revision = None\n')
    with pytest.raises(ValueError, match="one migration head"):
        migration_head(tmp_path)


@pytest.mark.parametrize(
    ("state", "running", "fresh", "drained"),
    [
        ("paused", 0, 1, True),
        # Straight after a boot, before the worker has registered.
        ("stopped", 0, 0, True),
        # A job is still running, so wait.
        ("paused", 1, 1, False),
        # Alive but hasn't seen the pause yet, so it could still claim a job.
        ("running", 0, 1, False),
        # A dead worker's job is running until its lease expires and it's reaped.
        ("running", 1, 0, False),
    ],
)
def test_drain_decision(state: str, running: int, fresh: int, drained: bool) -> None:
    assert _load_production_script().is_drained(state, running, fresh) is drained


def _deploy_document() -> dict[str, object]:
    class Loader(yaml.SafeLoader):
        pass

    def _ignore_tag(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node) -> object:
        # CloudFormation tags like !Ref don't matter here, so keep the plain value.
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)  # type: ignore[arg-type]

    Loader.add_multi_constructor("!", _ignore_tag)
    template = yaml.load(  # noqa: S506
        (BACKEND / "infra" / "aws" / "worker-stack.yaml").read_text(encoding="utf-8"), Loader
    )
    document: dict[str, object] = template["Resources"]["DeployDocument"]["Properties"]["Content"]
    return document


def test_deploy_document_only_accepts_a_full_sha() -> None:
    # The pattern is all that stands between a GitHub secret and arbitrary commands as root.
    parameter = _deploy_document()["parameters"]["Sha"]  # type: ignore[index]
    assert parameter["allowedPattern"] == "^[0-9a-f]{40}$"


def test_deploy_document_requires_the_commit_to_be_on_main() -> None:
    commands = " ".join(_deploy_document()["mainSteps"][0]["inputs"]["runCommand"])  # type: ignore[index]
    assert 'merge-base --is-ancestor "$SHA" origin/main' in commands
