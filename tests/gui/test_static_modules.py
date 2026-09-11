"""Every module import must resolve to a file the server actually serves.

A typo'd specifier is a silently blank tab: the browser logs a 404 for the module
and stops, and no Python test can see it. This is deliberately a regex over import
statements rather than a JS parser -- it catches the whole failure class (a path
typo, a moved file, a missing extension) without pretending to understand the
language.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "wfm" / "gui" / "static"

# Matches `from "./x.js"` and `import "./x.js"`, single or double quoted.
_SPECIFIER = re.compile(r"""(?:from|import)\s+["']([^"']+)["']""")


def _modules() -> list[Path]:
    return [p for p in STATIC.rglob("*.js") if "lightweight-charts" not in p.name]


def test_there_are_modules_to_check():
    # Guards the whole file: an empty glob would make every test below vacuous.
    assert _modules()


@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.name)
def test_every_import_resolves_to_a_served_file(module):
    for specifier in _SPECIFIER.findall(module.read_text(encoding="utf-8")):
        assert specifier.startswith("."), f"{module.name}: bare specifier {specifier!r} has no bundler to resolve it"
        assert specifier.endswith(".js"), f"{module.name}: {specifier!r} needs an explicit .js extension"
        target = (module.parent / specifier).resolve()
        assert target.is_file(), f"{module.name}: {specifier!r} resolves to {target}, which does not exist"
        assert STATIC in target.parents, f"{module.name}: {specifier!r} escapes the static directory"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.name)
def test_every_module_parses_as_an_es_module(module):
    # `node --check <path>` is a FALSE NEGATIVE here: on a .js file carrying `import`
    # it exits 0 even for a syntax error. Feeding the source on stdin with an explicit
    # --input-type=module is the form that actually parses as ESM and exits 1.
    result = subprocess.run(
        ["node", "--input-type=module", "--check"],
        stdin=module.open("rb"),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{module.name}: {result.stderr.strip()}"
