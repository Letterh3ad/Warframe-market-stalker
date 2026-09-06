"""Stdlib HTML to plain text, for feeding article bodies to the fuzzy gate.

No BeautifulSoup: the two things this needs (drop markup, pull one element out) are
twenty lines of html.parser, and a dependency for that would have to be justified on
every future install.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_WHITESPACE = re.compile(r"\s+")

# Never contribute text, whatever is between their tags.
_OPAQUE = frozenset({"script", "style"})

# Never close, so they must not count as a nesting level.
_VOID = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }
)


class _Stripper(HTMLParser):
    """Text with a space at every tag boundary.

    The space matters: the gate matches word tokens, and "<li>Rage</li><li>Fury</li>"
    without it becomes the single token "ragefury", which matches nothing.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._opaque_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _OPAQUE:
            self._opaque_depth += 1
        self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _OPAQUE and self._opaque_depth:
            self._opaque_depth -= 1
        self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._opaque_depth:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return _WHITESPACE.sub(" ", "".join(self._parts)).strip()


class _ElementText(_Stripper):
    """Text inside the first element carrying `id=<element_id>`."""

    def __init__(self, element_id: str) -> None:
        super().__init__()
        self._wanted = element_id
        self._depth = 0
        self._done = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self._done:
            return
        if self._depth:
            if tag not in _VOID:
                self._depth += 1
            super().handle_starttag(tag, attrs)
            return
        if dict(attrs).get("id") == self._wanted and tag not in _VOID:
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._done or not self._depth:
            return
        # A void tag never opened a level. HTMLParser fires this handler for the
        # self-closing spelling "<img/>" via handle_startendtag, so without this
        # guard the capture closes early and drops the rest of the element.
        if tag in _VOID:
            super().handle_endtag(tag)
            return
        self._depth -= 1
        if self._depth == 0:
            self._done = True
            return
        super().handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._depth and not self._done:
            super().handle_data(data)


def strip_tags(html: str) -> str:
    parser = _Stripper()
    parser.feed(html)
    parser.close()
    return parser.text


def extract_element_text(html: str, element_id: str) -> str:
    """Text inside the first element with that id, or "" if there is none.

    Empty rather than raising: a source that cannot find the body still has a title
    worth storing, and an article with no body simply matches nothing.
    """
    parser = _ElementText(element_id)
    parser.feed(html)
    parser.close()
    return parser.text
