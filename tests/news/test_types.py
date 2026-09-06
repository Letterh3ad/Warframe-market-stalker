from wfm.news.types import NewsDirection, content_hash


def test_direction_sign_maps_to_plus_minus_zero():
    assert NewsDirection.UP.sign == 1
    assert NewsDirection.DOWN.sign == -1
    assert NewsDirection.UNCLEAR.sign == 0


def test_content_hash_is_stable_and_content_sensitive():
    a = content_hash("Title", "Body")
    assert a == content_hash("Title", "Body")
    assert a != content_hash("Title", "Body edited")
    assert a != content_hash("Title edited", "Body")


def test_content_hash_does_not_confuse_field_boundaries():
    # Without a separator, ("ab", "c") and ("a", "bc") would collide.
    assert content_hash("ab", "c") != content_hash("a", "bc")


def test_hashed_stamps_the_body_and_survives_a_bodyless_round_trip():
    from dataclasses import replace

    from wfm.news.types import Article, NewsSource

    fetched = Article(
        source=NewsSource.WARFRAME_NEWS,
        external_id="a1",
        url="https://example.test/a1",
        title="T",
        body="B",
    ).hashed()
    assert fetched.content_hash == content_hash("T", "B")
    # What a DB round-trip looks like: body gone, hash intact.
    assert replace(fetched, body="").content_hash == fetched.content_hash
