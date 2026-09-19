import pytest

from hunch.vocabulary import (
    DEFAULT_VOCABULARY,
    ChoiceSpec,
    Risk,
    ScoreSpec,
    Verb,
    Vocabulary,
    verbs_for_domain,
)


def test_default_vocabulary_has_expected_core_verbs():
    names = set(DEFAULT_VOCABULARY.names)
    assert {"turn_on", "turn_off", "set_brightness", "open", "close", "lock", "unlock",
            "set_temperature", "activate", "query_state"} <= names


def test_lock_and_unlock_are_confirm_tier():
    assert DEFAULT_VOCABULARY.by_name("lock").risk is Risk.CONFIRM
    assert DEFAULT_VOCABULARY.by_name("unlock").risk is Risk.CONFIRM


def test_nothing_ships_destructive():
    assert all(v.risk is not Risk.DESTRUCTIVE for v in DEFAULT_VOCABULARY.verbs)


def test_brightness_is_a_score_param():
    v = DEFAULT_VOCABULARY.by_name("set_brightness")
    assert isinstance(v.param, ScoreSpec)
    assert v.param.name == "brightness_pct"
    assert 2 <= len(v.param.levels) <= 10


def test_query_state_is_flagged_as_query():
    assert DEFAULT_VOCABULARY.by_name("query_state").is_query is True


def test_verbs_for_domain():
    assert verbs_for_domain("light", DEFAULT_VOCABULARY) == frozenset(
        {"turn_on", "turn_off", "set_brightness", "query_state"}
    )
    assert verbs_for_domain("lock", DEFAULT_VOCABULARY) == frozenset({"lock", "unlock", "query_state"})
    assert verbs_for_domain("unknown_domain", DEFAULT_VOCABULARY) == frozenset({"query_state"})


def test_by_name_raises_on_unknown():
    with pytest.raises(KeyError):
        DEFAULT_VOCABULARY.by_name("teleport")


def test_vocabulary_extension_keeps_frozen_semantics():
    extra = Verb("party", frozenset({"light"}), ChoiceSpec("mode", ("disco", "chill")),
                 Risk.SAFE, "script.party", "start a party mode")
    v2 = Vocabulary(DEFAULT_VOCABULARY.verbs + (extra,))
    assert v2.by_name("party").intent == "script.party"
    assert "party" not in DEFAULT_VOCABULARY.names
