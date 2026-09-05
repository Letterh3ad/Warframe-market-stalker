from wfm.models import Item
from wfm.store.items import ItemsRepo

MIRAGE = Item(
    slug="mirage_prime_set",
    name="Mirage Prime Set",
    url_name="mirage_prime_set",
    tags=("set", "prime"),
    is_set=True,
    ducats=0,
)
CONTINUITY = Item(
    slug="primed_continuity",
    name="Primed Continuity",
    url_name="primed_continuity",
    tags=("mod", "primed"),
    max_rank=10,
    canonical_rank=10,
)


def test_upsert_and_get_round_trip(conn):
    repo = ItemsRepo(conn)
    assert repo.upsert_many([MIRAGE, CONTINUITY]) == 2
    got = repo.get("primed_continuity")
    assert got == CONTINUITY
    assert got.tags == ("mod", "primed")


def test_get_missing_returns_none(conn):
    assert ItemsRepo(conn).get("nope") is None


def test_upsert_updates_in_place(conn):
    repo = ItemsRepo(conn)
    repo.upsert_many([MIRAGE])
    repo.upsert_many([Item(slug="mirage_prime_set", name="Renamed", url_name="mirage_prime_set")])
    assert repo.count() == 1
    assert repo.get("mirage_prime_set").name == "Renamed"


def test_search_is_case_insensitive_and_substring(conn):
    repo = ItemsRepo(conn)
    repo.upsert_many([MIRAGE, CONTINUITY])
    assert [i.slug for i in repo.search("continu")] == ["primed_continuity"]
    assert [i.slug for i in repo.search("PRIME")] == ["mirage_prime_set", "primed_continuity"]
    assert repo.search("zzz") == []


def test_search_treats_underscore_as_a_literal(conn):
    repo = ItemsRepo(conn)
    repo.upsert_many([MIRAGE, CONTINUITY])
    assert repo.search("prime_set") == []


def test_canonical_rank_defaults_to_zero_for_unknown_items(conn):
    repo = ItemsRepo(conn)
    repo.upsert_many([CONTINUITY])
    assert repo.canonical_rank("primed_continuity") == 10
    assert repo.canonical_rank("unknown_slug") == 0


def test_page_returns_items_ordered_by_name_with_an_offset(conn):
    repo = ItemsRepo(conn)
    repo.upsert_many([
        Item(slug="c", name="Charlie", url_name="c"),
        Item(slug="a", name="Alpha", url_name="a"),
        Item(slug="b", name="Bravo", url_name="b"),
    ])
    assert [i.name for i in repo.page(limit=2, offset=0)] == ["Alpha", "Bravo"]
    assert [i.name for i in repo.page(limit=2, offset=2)] == ["Charlie"]
    assert repo.page(limit=2, offset=99) == []


def test_page_and_count_apply_the_same_filter(conn):
    repo = ItemsRepo(conn)
    repo.upsert_many([
        Item(slug="mp", name="Mirage Prime Set", url_name="mp"),
        Item(slug="mb", name="Mirage Prime Blueprint", url_name="mb"),
        Item(slug="tp", name="Tigris Prime Set", url_name="tp"),
    ])
    assert repo.count() == 3
    assert repo.count("mirage") == 2
    assert {i.slug for i in repo.page("mirage", limit=100)} == {"mp", "mb"}


def test_page_escapes_like_wildcards_in_the_query(conn):
    repo = ItemsRepo(conn)
    repo.upsert_many([
        Item(slug="pct", name="100% Status", url_name="pct"),
        Item(slug="other", name="Nothing Special", url_name="other"),
    ])
    # A bare % would match every row; escaped, it matches only the literal one.
    assert [i.slug for i in repo.page("%")] == ["pct"]
    assert repo.count("%") == 1


def test_page_and_count_match_on_tags_as_well_as_name(conn):
    repo = ItemsRepo(conn)
    repo.upsert_many([
        Item(slug="mp", name="Mirage Prime Set", url_name="mp", tags=("warframe", "prime", "set")),
        Item(slug="tb", name="Tigris Prime Barrel", url_name="tb", tags=("weapon", "prime", "component")),
        Item(slug="sc", name="Sortie Cache Scene", url_name="sc", tags=("scene",)),
    ])
    # "component" appears in no name, only in tb's tags.
    assert [i.slug for i in repo.page("component")] == ["tb"]
    assert repo.count("component") == 1
    # "prime" is in both names and tags; still one hit each, no duplication.
    assert {i.slug for i in repo.page("prime")} == {"mp", "tb"}
    assert repo.count("prime") == 2
    # A wildcard in a tag search is escaped too.
    assert repo.count("we%pon") == 0
