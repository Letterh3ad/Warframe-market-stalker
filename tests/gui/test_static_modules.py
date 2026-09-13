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


def _top_level_args(source: str, open_index: int) -> list[str]:
    """The argument texts of the call whose "(" sits at `open_index`.

    Nested calls collapse into their own argument, so `el("h1", null, x)` passed as
    an argument does not look like a nullish argument of the OUTER call.
    """
    depth = 0
    args: list[str] = []
    current: list[str] = []
    for ch in source[open_index:]:
        if ch in "([{":
            depth += 1
            if depth == 1:
                continue
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                args.append("".join(current))
                return args
        if depth == 1 and ch == ",":
            args.append("".join(current))
            current = []
        else:
            current.append(ch)
    return args


_NATIVE_APPEND = re.compile(r"\.(replaceChildren|append|prepend)\(")


@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.name)
def test_no_nullish_child_reaches_a_native_append(module):
    """`el()` skips nullish children; the native DOM methods render "null" as text.

    So `cond ? el(...) : null` is correct inside `el(...)` and a visible bug inside
    `box.replaceChildren(...)` -- which is how a literal "null" once appeared above
    the News tab's buttons. Use `.filter(Boolean)` on the child list instead.
    """
    source = module.read_text(encoding="utf-8")
    for match in _NATIVE_APPEND.finditer(source):
        for arg in _top_level_args(source, match.end() - 1):
            stripped = arg.strip()
            assert stripped not in ("null", "undefined") and not stripped.endswith(
                (": null", ": undefined")
            ), f"{module.name}: {match.group(1)}() is passed a nullish child: {stripped!r}"
            if stripped.startswith("...["):
                assert ".filter(" in stripped, (
                    f"{module.name}: {match.group(1)}() spreads an unfiltered array; "
                    "a nullish element would render as the text \"null\""
                )
