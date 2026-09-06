from datetime import timezone
from pathlib import Path

from wfm.news.sources.feed import parse_atom, parse_rss

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "news"

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>PC Update Notes</title>
<item>
  <title>Hotfix 43.5.3</title>
  <link>https://forums.warframe.com/topic/1520640-hotfix-4353/</link>
  <description><![CDATA[<p>Fixed <strong>Rage</strong>.</p>]]></description>
  <guid isPermaLink="false">1520640</guid>
  <pubDate>Tue, 18 Aug 2026 19:04:45 +0000</pubDate>
</item>
</channel></rss>
"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
  <content type="html">&lt;p&gt;Banshee rework&lt;/p&gt;</content>
  <id>t3_1vy9lck</id>
  <link href="https://www.reddit.com/r/Warframe/comments/1vy9lck/x/" />
  <updated>2026-08-25T19:20:27+00:00</updated>
  <published>2026-08-25T19:20:27+00:00</published>
  <title>Dev Workshop</title>
</entry>
</feed>
"""


def test_parse_rss_reads_one_item():
    (entry,) = parse_rss(RSS)
    assert entry.entry_id == "1520640"
    assert entry.title == "Hotfix 43.5.3"
    assert entry.url == "https://forums.warframe.com/topic/1520640-hotfix-4353/"
    assert "<strong>Rage</strong>" in entry.body_html


def test_parse_rss_reads_an_rfc_2822_date_as_utc():
    (entry,) = parse_rss(RSS)
    assert entry.published is not None
    assert entry.published.utcoffset() == timezone.utc.utcoffset(None)
    assert entry.published.isoformat() == "2026-08-18T19:04:45+00:00"


def test_parse_rss_reports_no_date_rather_than_guessing_a_timezone():
    naive = RSS.replace(
        "<pubDate>Tue, 18 Aug 2026 19:04:45 +0000</pubDate>",
        "<pubDate>Tue, 18 Aug 2026 19:04:45</pubDate>",
    )
    (entry,) = parse_rss(naive)
    assert entry.published is None


def test_parse_rss_falls_back_to_the_link_when_there_is_no_guid():
    without = RSS.replace('<guid isPermaLink="false">1520640</guid>', "")
    (entry,) = parse_rss(without)
    assert entry.entry_id == "https://forums.warframe.com/topic/1520640-hotfix-4353/"


def test_parse_rss_skips_an_item_with_neither_id_nor_link():
    broken = RSS.replace('<guid isPermaLink="false">1520640</guid>', "").replace(
        "<link>https://forums.warframe.com/topic/1520640-hotfix-4353/</link>", ""
    )
    assert parse_rss(broken) == []


def test_parse_atom_reads_one_entry():
    (entry,) = parse_atom(ATOM)
    assert entry.entry_id == "t3_1vy9lck"
    assert entry.title == "Dev Workshop"
    assert entry.url == "https://www.reddit.com/r/Warframe/comments/1vy9lck/x/"
    assert entry.body_html == "<p>Banshee rework</p>"
    assert entry.published is not None
    assert entry.published.isoformat() == "2026-08-25T19:20:27+00:00"


def test_parse_rss_reads_the_real_forums_fixture():
    entries = parse_rss((FIXTURES / "forums_updates.xml").read_text(encoding="utf-8"))
    assert len(entries) == 3
    assert entries[0].entry_id == "1520640"
    assert all(e.published is not None for e in entries)
    # The whole point of the forums feed: the body arrives inline, no second request.
    assert all(len(e.body_html) > 500 for e in entries)


def test_parse_atom_reads_the_real_reddit_fixture():
    entries = parse_atom((FIXTURES / "reddit_hot.xml").read_text(encoding="utf-8"))
    assert len(entries) == 3
    assert all(e.entry_id.startswith("t3_") for e in entries)
    assert all(e.url.startswith("https://www.reddit.com/r/Warframe/comments/") for e in entries)
    assert all(e.published is not None for e in entries)
