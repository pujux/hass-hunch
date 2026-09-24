from hunch import ActiveTimer, Timing

from custom_components.hunch.pending import PendingConfirm, PendingStore, PendingTimerPick


def test_take_is_single_use_and_expires():
    t = [100.0]
    store = PendingStore(ttl_seconds=120, clock=lambda: t[0])
    turn = PendingConfirm(actions=(), condition=None, question="q", created=store.now())
    store.put("c1", turn)
    assert store.take("c1") is turn
    assert store.take("c1") is None
    store.put("c1", turn)
    t[0] = 221.0
    assert store.take("c1") is None
    store.put("c1", turn)
    store.put("c1", PendingConfirm(actions=(), condition=None, question="q2", created=store.now()))
    assert store.take("c1").question == "q2"  # a new request replaces the old turn


def test_peek_expired_removes_expired_turn():
    """peek_expired: expired turns are removed and return True; fresh turns return False."""
    t = [100.0]
    store = PendingStore(ttl_seconds=120, clock=lambda: t[0])

    # Put a turn
    turn = PendingConfirm(actions=(), condition=None, question="q", created=store.now())
    store.put("c1", turn)

    # peek_expired with fresh turn should return False and leave store intact
    assert store.peek_expired("c1") is False
    assert store.take("c1") is turn

    # Put another turn
    store.put("c1", turn)

    # Advance clock past TTL
    t[0] = 221.0

    # peek_expired with expired turn should return True and remove it
    assert store.peek_expired("c1") is True
    assert store.take("c1") is None

    # peek_expired on non-existent conversation should return False
    assert store.peek_expired("c2") is False


def test_put_sweeps_expired_turns_of_other_conversations():
    """Conversation ids are unbounded; an abandoned question must not linger for ever."""
    t = [100.0]
    store = PendingStore(ttl_seconds=120, clock=lambda: t[0])
    for cid in ("c1", "c2", "c3"):
        store.put(cid, PendingConfirm((), None, cid, store.now()))
    assert len(store._turns) == 3
    t[0] = 221.0
    store.put("c4", PendingConfirm((), None, "c4", store.now()))
    assert list(store._turns) == ["c4"]
    # the fresh turn itself survives its own put
    assert store.take("c4").question == "c4"


def test_last_turn_store_keeps_the_latest_and_expires():
    from hunch import PreviousTurn

    from custom_components.hunch.pending import LastTurnStore

    t = [0.0]
    store = LastTurnStore(ttl_seconds=300, clock=lambda: t[0])
    a = PreviousTurn("a", ())
    b = PreviousTurn("b", ())
    store.put("c", a)
    assert store.get("c") is a and store.get("c") is a  # reading does not remove
    store.put("c", b)
    assert store.get("c") is b
    t[0] = 301.0
    assert store.get("c") is None


def test_pending_confirm_timing_defaults_to_none():
    turn = PendingConfirm((), None, "q", 0.0)
    assert turn.timing is None
    timed = PendingConfirm((), None, "q", 0.0, timing=Timing("delayed", 900))
    assert timed.timing == Timing("delayed", 900)


def test_pending_timer_pick_stored_and_taken_back():
    t = [100.0]
    store = PendingStore(ttl_seconds=120, clock=lambda: t[0])
    timers = (ActiveTimer("1", "Nudeln", 200.0, "timer"),)
    turn = PendingTimerPick(
        timers=timers,
        labels=("Nudeln (3:20 left)", "alle Timer"),
        question="q",
        created=store.now(),
    )
    store.put("c1", turn)
    assert store.take("c1") is turn
    assert store.take("c1") is None
