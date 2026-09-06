from pathlib import Path

from wfm.news.html import extract_element_text, strip_tags

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "news"


def test_tags_become_whitespace_so_adjacent_words_stay_separate():
    assert strip_tags("<ul><li>Rage</li><li>Fury</li></ul>") == "Rage Fury"


def test_entities_are_unescaped():
    assert strip_tags("<p>Enemy &amp; Ally</p>") == "Enemy & Ally"


def test_whitespace_is_collapsed():
    assert strip_tags("<p>\n\tCitrine   Prime\n</p>") == "Citrine Prime"


def test_script_and_style_bodies_are_dropped():
    html = "<div>Real<script>var x = 'Rage';</script><style>.a{}</style>Text</div>"
    assert strip_tags(html) == "Real Text"


def test_plain_text_survives_untouched():
    assert strip_tags("no markup here") == "no markup here"


def test_extract_element_text_returns_only_that_element():
    html = "<div>outside<div id='post-body'>inside <b>bold</b></div>after</div>"
    assert extract_element_text(html, "post-body") == "inside bold"


def test_extract_element_text_handles_nested_elements_of_the_same_tag():
    html = "<div id='post-body'>a<div>b<div>c</div></div></div><div>outside</div>"
    assert extract_element_text(html, "post-body") == "a b c"


def test_extract_element_text_is_not_confused_by_void_elements():
    # <img> never closes. Counting it as a level would swallow the rest of the document.
    html = "<div id='post-body'>a<img src='x.png'>b</div><div>outside</div>"
    assert extract_element_text(html, "post-body") == "a b"


def test_extract_element_text_is_not_confused_by_self_closing_void_elements():
    # HTMLParser routes "<img/>" through handle_startendtag, which fires BOTH the
    # start and the end handler. Decrementing on that end closes the capture early
    # and swallows the rest of the element.
    html = "<div id='post-body'>a<img src='x.png'/>b<br/>c</div><div>outside</div>"
    assert extract_element_text(html, "post-body") == "a b c"


def test_extract_element_text_returns_empty_when_the_id_is_absent():
    assert extract_element_text("<div>nothing</div>", "post-body") == ""


def test_extract_element_text_reads_the_real_warframe_article_fixture():
    html = (FIXTURES / "warframe_article.html").read_text(encoding="utf-8")
    body = extract_element_text(html, "post-body")
    assert body.startswith("Shine on with a diamond")
    assert "Citrine Prime enters Prime Access on September 23" in body
    # The page is 66KB of chrome around a ~2KB article. Extracting the whole document
    # instead of the element would sail past this.
    assert len(body) < 6000
    assert "OptanonWrapper" not in body
