from wfm.services.broadcaster import SignalBroadcaster


def test_subscribe_returns_an_empty_queue():
    broadcaster = SignalBroadcaster()
    assert broadcaster.subscribe().empty()


async def test_publish_reaches_every_subscriber():
    broadcaster = SignalBroadcaster()
    a = broadcaster.subscribe()
    b = broadcaster.subscribe()
    broadcaster.publish({"slug": "x"})
    assert await a.get() == {"slug": "x"}
    assert await b.get() == {"slug": "x"}


def test_unsubscribe_stops_further_delivery():
    broadcaster = SignalBroadcaster()
    queue = broadcaster.subscribe()
    broadcaster.unsubscribe(queue)
    broadcaster.publish({"slug": "x"})
    assert queue.empty()


def test_subscriber_count_tracks_subscribe_and_unsubscribe():
    broadcaster = SignalBroadcaster()
    assert broadcaster.subscriber_count == 0
    queue = broadcaster.subscribe()
    assert broadcaster.subscriber_count == 1
    broadcaster.unsubscribe(queue)
    assert broadcaster.subscriber_count == 0
