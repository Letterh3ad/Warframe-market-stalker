import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "wfm"
FORBIDDEN_FOR_FRONTENDS = ("wfm.store", "wfm.api", "wfm.analyzers")
FORBIDDEN_FOR_ANALYZERS = ("wfm.api", "wfm.store")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _modules(package: str) -> list[Path]:
    return sorted((SOURCE_ROOT / package).rglob("*.py"))


@pytest.mark.parametrize("path", _modules("cli"), ids=lambda p: p.name)
def test_frontend_modules_only_reach_services(path):
    offenders = {
        name
        for name in _imports(path)
        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN_FOR_FRONTENDS)
    }
    assert offenders == set(), (
        f"{path.name} imports {offenders}. Frontends call wfm.services and nothing else, "
        "which is what keeps the planned GUI additive rather than a rewrite."
    )


def test_there_is_at_least_one_cli_module_to_check():
    assert len(_modules("cli")) >= 2


@pytest.mark.parametrize(
    "path", _modules("analyzers") or [SOURCE_ROOT / "cli" / "main.py"], ids=lambda p: p.name
)
def test_analyzer_modules_touch_neither_api_nor_store(path):
    if "analyzers" not in str(path):
        pytest.skip("analyzers package does not exist yet, arrives in phase 5")
    offenders = {
        name
        for name in _imports(path)
        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN_FOR_ANALYZERS)
    }
    assert offenders == set()
