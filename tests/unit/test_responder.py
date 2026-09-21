from types import SimpleNamespace

import pytest
from hunch import DEFAULT_VOCABULARY as V
from hunch import Entity

from custom_components.hunch.responder import (
    OUTCOMES,
    action_clause,
    condition_clause,
    describe_state,
    describe_targets,
    pending_context,
    render,
    resolve_language,
    verb_phrase,
)


def _e(eid, name, area):
    return Entity(
        entity_id=eid,
        domain=eid.split(".")[0],
        name=name,
        aliases=(),
        area_id=area,
        device_id=None,
        device_name=None,
        verbs=frozenset(),
        state=None,
    )


AREAS = {"kuche": "Küche", "wohnzimmer": "Wohnzimmer"}


def test_language_resolution():
    assert resolve_language("auto", "de-AT") == "de"
    assert resolve_language("auto", "en-GB") == "en"
    assert resolve_language("auto", None) == "en"
    assert resolve_language("de", "en") == "de"
    assert resolve_language("auto", "fr") == "en"


@pytest.mark.parametrize("language", ["en", "de"])
def test_every_outcome_renders_in_both_languages(language):
    slots = {
        "action_done": dict(
            phrase=verb_phrase("turn_off", language, done=True), targets="13 lights"
        ),
        "query_answer": dict(lines=["Temperatur (Wohnzimmer): 23.6 °C"]),
        "confirm": dict(
            phrase=verb_phrase("turn_off", language, done=False),
            targets="26 lights",
            reason="blast_radius",
            condition=None,
        ),
        "clarify": dict(options=["Tür (Galerie)", "Tür (Schlafzimmer)"]),
        "cancelled": {},
        "condition_not_met": dict(subject="Dachterrassentür", expected="open"),
        "expired": {},
        "fallback_unavailable": {},
        "execution_failed": dict(failed=["Spots (Küche)"]),
    }
    for outcome in OUTCOMES:
        text = render(outcome, language, **slots[outcome])
        assert text and "{" not in text, (outcome, text)


@pytest.mark.parametrize("language", ["en", "de"])
def test_every_reason_the_resolver_emits_renders_a_why_clause(language):
    # hunch.resolver emits exactly these four; a missing one silently drops the "why".
    for reason in ("blast_radius", "risk:confirm", "confidence", "collective_fallback"):
        text = render(
            "confirm",
            language,
            phrase=verb_phrase("turn_off", language, done=False),
            targets="26 lights",
            reason=reason,
            condition=None,
        )
        assert text and "{" not in text, (reason, text)
        assert text.count("?") == 1 and not text.endswith("?"), (reason, text)


@pytest.mark.parametrize("language", ["en", "de"])
def test_confirm_tolerates_missing_condition_and_unknown_reason(language):
    text = render(
        "confirm",
        language,
        phrase=verb_phrase("turn_off", language, done=False),
        targets="26 lights",
        reason="some_unrecognised_reason",
        condition=None,
    )
    assert text and "{" not in text
    assert not text.endswith(" ")

    text_no_reason = render(
        "confirm",
        language,
        phrase=verb_phrase("turn_off", language, done=False),
        targets="26 lights",
        reason="",
        condition=None,
    )
    assert text_no_reason and "{" not in text_no_reason
    assert not text_no_reason.endswith(" ")


@pytest.mark.parametrize("language", ["en", "de"])
def test_confirm_includes_condition_when_given(language):
    condition = condition_clause("Dachterrassentür", "open", language)
    text = render(
        "confirm",
        language,
        phrase=verb_phrase("open", language, done=False),
        targets="Markise",
        reason="",
        condition=condition,
    )
    assert text and "{" not in text
    if language == "en":
        assert " if " in text
    else:
        assert "wenn" in text


def test_query_answer_joins_lines_with_newlines():
    lines = ["Temperatur (Wohnzimmer): 23.6 °C", "Tür (Galerie): offen"]
    text = render("query_answer", "de", lines=lines)
    assert text == "\n".join(lines)


def test_every_verb_has_phrases():
    for verb in V.verbs:
        for lang in ("en", "de"):
            assert verb_phrase(verb.name, lang, done=True)
            assert verb_phrase(verb.name, lang, done=False)


def test_targets_short_and_long():
    e = [_e("light.a", "Spots", "kuche"), _e("light.b", "Kücheninsel", "kuche")]
    assert describe_targets(e, AREAS, "de") == "Spots (Küche), Kücheninsel (Küche)"
    many = [_e(f"light.{i}", f"L{i}", "kuche" if i % 2 else "wohnzimmer") for i in range(5)]
    assert describe_targets(many, AREAS, "en") == "5 devices in Küche, Wohnzimmer"


def test_state_words():
    door = SimpleNamespace(entity_id="binary_sensor.d", state="on", unit=None, device_class="door")
    assert describe_state(door, "de") == "offen"
    assert describe_state(door, "en") == "open"

    sensor = SimpleNamespace(entity_id="sensor.t", state="23.6", unit="°C", device_class=None)
    assert describe_state(sensor, "en") == "23.6 °C"
    assert describe_state(sensor, "de") == "23,6 °C"

    cover = SimpleNamespace(entity_id="cover.c", state="closed", unit=None, device_class=None)
    assert describe_state(cover, "de") == "geschlossen"
    assert describe_state(cover, "en") == "closed"

    unknown = SimpleNamespace(
        entity_id="switch.mystery", state="weird_state", unit=None, device_class=None
    )
    assert describe_state(unknown, "en") == "weird_state"
    assert describe_state(unknown, "de") == "weird_state"


def test_condition_clause():
    en = condition_clause("Dachterrassentür", "open", "en")
    de = condition_clause("Dachterrassentür", "open", "de")
    assert " if " in en
    assert "wenn" in de


def test_pending_context():
    question = "Turn off 13 lights?"
    description = "turn off 13 lights on the Untergeschoss"
    text = pending_context(question, description)
    assert question in text
    assert description in text


@pytest.mark.parametrize("language", ["en", "de"])
def test_a_single_action_sentence_reads_as_before(language):
    """The one-clause form is what the slots produce on their own, unchanged."""
    clause = action_clause(
        verb_phrase("turn_off", language, done=True), "Lampe (Wohnzimmer)", language
    )
    text = render("action_done", language, phrase="", targets=clause)
    expected = {
        "de": "Erledigt: Lampe (Wohnzimmer) ausgeschaltet.",
        "en": "Done: turned off Lampe (Wohnzimmer).",
    }
    assert text == expected[language]
    # the same sentence via the plain slots
    assert (
        render(
            "action_done",
            language,
            phrase=verb_phrase("turn_off", language, done=True),
            targets="Lampe (Wohnzimmer)",
        )
        == expected[language]
    )


def _clauses(*pairs, done, language):
    return "; ".join(
        action_clause(verb_phrase(verb, language, done=done), targets, language)
        for verb, targets in pairs
    )


def test_multi_verb_clauses_join_with_a_semicolon():
    plan = (("turn_off", "Lampe (Wohnzimmer)"), ("close", "Rollo (Wohnzimmer)"))
    assert render(
        "action_done", "de", phrase="", targets=_clauses(*plan, done=True, language="de")
    ) == ("Erledigt: Lampe (Wohnzimmer) ausgeschaltet; Rollo (Wohnzimmer) geschlossen.")
    assert render(
        "confirm",
        "de",
        phrase="",
        targets=_clauses(*plan, done=False, language="de"),
        reason="",
        condition=None,
    ) == ("Soll ich Lampe (Wohnzimmer) ausschalten; Rollo (Wohnzimmer) schließen?")
    assert render(
        "action_done", "en", phrase="", targets=_clauses(*plan, done=True, language="en")
    ) == ("Done: turned off Lampe (Wohnzimmer); closed Rollo (Wohnzimmer).")


def test_targets_without_a_known_area_render_the_bare_count():
    many = [_e(f"light.{i}", f"L{i}", None) for i in range(5)]
    assert describe_targets(many, AREAS, "en") == "5 devices"
    assert describe_targets(many, AREAS, "de") == "5 Geräte"


def test_condition_context_names_the_check():
    from custom_components.hunch.responder import condition_context

    text = condition_context()
    assert "condition" in text and "current state" in text


def test_todo_lists_render_their_items_not_their_count():
    from types import SimpleNamespace

    full = SimpleNamespace(
        entity_id="todo.shopping",
        state="4",
        unit=None,
        device_class=None,
        items=("Oliven Öl", "Butter"),
    )
    assert describe_state(full, "de") == "Oliven Öl, Butter"
    empty = SimpleNamespace(
        entity_id="todo.shopping", state="0", unit=None, device_class=None, items=()
    )
    assert describe_state(empty, "de") == "leer" and describe_state(empty, "en") == "empty"
    unread = SimpleNamespace(
        entity_id="todo.shopping", state="4", unit=None, device_class=None, items=None
    )
    assert describe_state(unread, "de") == "4"


def test_numeric_conditions_are_described_with_the_value():
    from types import SimpleNamespace

    from custom_components.hunch.responder import describe_expected, format_value

    def cond(op, thr, state, eid):
        return SimpleNamespace(
            operator=op, threshold=thr, expected_state=state, subject=SimpleNamespace(entity_id=eid)
        )

    assert describe_expected(cond("<", 20.0, "< 20", "sensor.t"), "de") == "unter 20"
    assert describe_expected(cond("<", 20.0, "< 20", "sensor.t"), "en") == "below 20"
    assert describe_expected(cond(">", 22.5, "> 22.5", "sensor.t"), "de") == "über 22,5"
    assert describe_expected(cond(None, None, "on", "binary_sensor.d"), "de").startswith("offen")
    assert format_value("24.5", "°C", "de") == "24,5 °C"
    assert format_value("24.5", None, "en") == "24.5"
    text = render(
        "condition_not_met",
        "de",
        subject="Temperatur (Wohnzimmer)",
        expected="unter 20",
        value="24,5 °C",
    )
    assert (
        text
        == "Temperatur (Wohnzimmer) ist 24,5 °C, nicht unter 20, darum habe ich nichts geändert."
    )
    assert "{" not in render("condition_not_met", "en", subject="x", expected="open")


def test_parametrised_actions_say_the_value_in_the_right_word_order():
    from custom_components.hunch.responder import describe_action, format_param

    assert format_param("brightness_pct", 50.0, "de") == "50 %"
    assert format_param("temperature", 22.5, "de") == "22,5 °C"
    assert format_param("temperature", 22.5, "en") == "22.5 °C"
    de = describe_action(
        "set_brightness", "Kücheninsel (Küche)", {"brightness_pct": 50.0}, "de", done=True
    )
    assert de == "Kücheninsel (Küche) auf 50 % gestellt"
    en = describe_action(
        "set_brightness", "Kücheninsel (Küche)", {"brightness_pct": 50.0}, "en", done=False
    )
    assert en == "set Kücheninsel (Küche) to 50 %"
    assert describe_action(
        "set_volume", "Fernseher (Wohnzimmer)", {"volume_level": 20.0}, "de", done=True
    ) == ("Lautstärke von Fernseher (Wohnzimmer) auf 20 % gestellt")
    # no value: the plain verb phrase in the language's word order
    assert (
        describe_action("turn_off", "Spots (Küche)", {}, "de", done=True)
        == "Spots (Küche) ausgeschaltet"
    )
    assert (
        describe_action("turn_off", "Spots (Küche)", {}, "en", done=True)
        == "turned off Spots (Küche)"
    )
