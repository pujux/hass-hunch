"""The "already done" lookup behind "für": which targets a timed command leaves as they were
(spec addendum 2026-09-25, item 4)."""

import pytest
from hunch import DEFAULT_VOCABULARY as V
from hunch import INVERSES, Action, Entity

from custom_components.hunch.timers import COMMANDED_STATE, already_in_state, is_already_done


@pytest.mark.parametrize(
    ("verb", "prior", "done"),
    [
        ("close", ("closed", None), True),
        ("open", ("open", None), True),
        ("open", ("open", 50), False),  # a blind at 50% reports "open"
        ("open", ("open", 100), True),
        ("close", ("open", 0), True),
        ("close", ("closed", 30), False),
        ("unlock", ("unlocked", None), True),
        ("turn_on", ("heat", None), True),  # a thermostat that is already heating
        ("turn_on", ("playing", None), True),  # a media player that is already on
        ("turn_on", ("on", None), True),
        ("turn_on", ("off", None), False),
        ("turn_on", ("unavailable", None), False),
        ("turn_on", ("unknown", None), False),
        ("turn_off", ("off", None), True),
        ("turn_off", ("heat", None), False),
        ("media_pause", ("paused", None), True),
        ("media_play", ("paused", None), False),
        ("set_brightness", ("on", None), False),  # not in the table: counts as a change
        ("turn_on", None, False),  # no state: counts as a change
    ],
)
def test_is_already_done(verb, prior, done):
    assert is_already_done(verb, prior) is done


def _entity(entity_id):
    domain = entity_id.split(".")[0]
    return Entity(entity_id, domain, entity_id, (), "bad", None, None, frozenset(), None)


def test_already_in_state_names_the_targets_left_alone():
    heating, lamp, blind = _entity("climate.bad"), _entity("light.bad"), _entity("cover.bad")
    actions = (
        Action(V.by_name("turn_on"), (heating, lamp), {}),
        Action(V.by_name("open"), (blind,), {}),
    )
    before = {"climate.bad": ("heat", None), "light.bad": ("off", None), "cover.bad": ("open", 50)}
    assert already_in_state(actions, before) == {"climate.bad"}


def test_the_table_covers_exactly_the_invertible_verbs():
    assert set(COMMANDED_STATE) == set(INVERSES)
