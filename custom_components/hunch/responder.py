"""Everything Hunch says. Pure tables, en + de, en as fallback."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from hunch import Entity

OUTCOMES = (
    "action_done",
    "query_answer",
    "confirm",
    "clarify",
    "cancelled",
    "condition_not_met",
    "expired",
    "fallback_unavailable",
    "execution_failed",
)

# verb -> (past participle / done form, infinitive / question form)
VERB_PHRASES: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        "turn_on": ("turned on", "turn on"),
        "turn_off": ("turned off", "turn off"),
        "set_brightness": ("set the brightness of", "set the brightness of"),
        "open": ("opened", "open"),
        "close": ("closed", "close"),
        "set_position": ("set the position of", "set the position of"),
        "lock": ("locked", "lock"),
        "unlock": ("unlocked", "unlock"),
        "set_temperature": ("set the temperature of", "set the temperature of"),
        "set_volume": ("set the volume of", "set the volume of"),
        "media_play": ("resumed", "resume"),
        "media_pause": ("paused", "pause"),
        "arm": ("armed (away)", "arm (away mode)"),
        "disarm": ("disarmed", "disarm"),
        "activate": ("activated", "activate"),
        "query_state": ("read", "read"),
    },
    "de": {
        "turn_on": ("eingeschaltet", "einschalten"),
        "turn_off": ("ausgeschaltet", "ausschalten"),
        "set_brightness": ("Helligkeit gesetzt für", "Helligkeit setzen für"),
        "open": ("geöffnet", "öffnen"),
        "close": ("geschlossen", "schließen"),
        "set_position": ("Position gesetzt für", "Position setzen für"),
        "lock": ("abgesperrt", "absperren"),
        "unlock": ("aufgesperrt", "aufsperren"),
        "set_temperature": ("Temperatur gesetzt für", "Temperatur setzen für"),
        "set_volume": ("Lautstärke gesetzt für", "Lautstärke setzen für"),
        "media_play": ("fortgesetzt", "fortsetzen"),
        "media_pause": ("pausiert", "pausieren"),
        "arm": ("scharfgeschaltet (abwesend)", "scharfschalten (Modus abwesend)"),
        "disarm": ("unscharf geschaltet", "unscharf schalten"),
        "activate": ("aktiviert", "aktivieren"),
        "query_state": ("abgelesen", "ablesen"),
    },
}

TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "action_done": "Done: {body}.",
        "query_answer": "{lines}",
        "confirm": "Shall I {body}{condition}?{why}",
        "clarify": "Which one did you mean: {options}?",
        "cancelled": "Okay, I didn't change anything.",
        "condition_not_met": "{subject} is not {expected}, so I left everything as it is.",
        "condition_not_met_value": (
            "{subject} is {value}, not {expected}, so I left everything as it is."
        ),
        "expired": "That question has expired; I'm treating this as a new request.",
        "fallback_unavailable": "I can't do that myself, and no other assistant is available.",
        "execution_failed": "Done, except: {failed}.",
    },
    "de": {
        "action_done": "Erledigt: {body}.",
        "query_answer": "{lines}",
        "confirm": "Soll ich {body}{condition}?{why}",
        "clarify": "Welches meinst du: {options}?",
        "cancelled": "Okay, ich habe nichts geändert.",
        "condition_not_met": "{subject} ist nicht {expected}, darum habe ich nichts geändert.",
        "condition_not_met_value": (
            "{subject} ist {value}, nicht {expected}, darum habe ich nichts geändert."
        ),
        "expired": "Diese Frage ist abgelaufen; ich behandle das als neue Anfrage.",
        "fallback_unavailable": (
            "Das kann ich selbst nicht, und kein anderer Assistent ist verfügbar."
        ),
        "execution_failed": "Erledigt, außer: {failed}.",
    },
}

REASONS = {
    "en": {
        "blast_radius": " That is a lot at once.",
        "risk:confirm": " This needs a confirmation.",
        "confidence": " I'm not completely sure that's what you meant.",
        "collective_fallback": " I'm not sure you meant all of them.",
    },
    "de": {
        "blast_radius": " Das ist viel auf einmal.",
        "risk:confirm": " Das braucht eine Bestätigung.",
        "confidence": " Ich bin nicht ganz sicher, ob du das meinst.",
        "collective_fallback": " Ich bin nicht sicher, ob du alle meinst.",
    },
}

STATE_WORDS: dict[str, dict[tuple[str, str], str]] = {
    "en": {
        ("binary_sensor", "on"): "open / active",
        ("binary_sensor", "off"): "closed / clear",
        ("cover", "open"): "open",
        ("cover", "closed"): "closed",
        ("lock", "locked"): "locked",
        ("lock", "unlocked"): "unlocked",
        ("light", "on"): "on",
        ("light", "off"): "off",
        ("switch", "on"): "on",
        ("switch", "off"): "off",
    },
    "de": {
        ("binary_sensor", "on"): "offen / aktiv",
        ("binary_sensor", "off"): "geschlossen / inaktiv",
        ("cover", "open"): "offen",
        ("cover", "closed"): "geschlossen",
        ("lock", "locked"): "abgesperrt",
        ("lock", "unlocked"): "aufgesperrt",
        ("light", "on"): "an",
        ("light", "off"): "aus",
        ("switch", "on"): "an",
        ("switch", "off"): "aus",
    },
}
DOOR_WORDS = {"en": {"on": "open", "off": "closed"}, "de": {"on": "offen", "off": "geschlossen"}}
DOOR_CLASSES = {"door", "window", "garage_door", "opening"}
EMPTY_LIST = {"en": "empty", "de": "leer"}
CONDITION_WORD = {"en": " if {subject} is {state}", "de": ", wenn {subject} {state} ist"}
MANY = {"en": "{n} devices in {places}", "de": "{n} Geräte in {places}"}
MANY_NOWHERE = {"en": "{n} devices", "de": "{n} Geräte"}


def resolve_language(option: str, request_language: str | None) -> str:
    """Language "auto" follows the request's language (default en); otherwise the option wins."""
    code = option if option != "auto" else (request_language or "en")
    return "de" if code.lower().startswith("de") else "en"


def verb_phrase(verb_name: str, language: str, *, done: bool) -> str:
    """Past participle when done, infinitive/question form otherwise."""
    table = VERB_PHRASES.get(language, VERB_PHRASES["en"])
    pair = table.get(verb_name) or VERB_PHRASES["en"][verb_name]
    return pair[0] if done else pair[1]


def describe_targets(
    targets: Sequence[Entity], area_names: Mapping[str, str], language: str
) -> str:
    """≤3 entities: "Name (Area), ...". More: "<n> devices in <areas>"."""
    if len(targets) <= 3:
        return ", ".join(
            f"{e.name} ({area_names[e.area_id]})" if e.area_id in area_names else e.name
            for e in targets
        )
    places = sorted({area_names[e.area_id] for e in targets if e.area_id in area_names})
    if not places:  # "5 devices in —" says nothing; the bare count is the honest form
        return MANY_NOWHERE.get(language, MANY_NOWHERE["en"]).format(n=len(targets))
    return MANY.get(language, MANY["en"]).format(n=len(targets), places=", ".join(places))


def describe_state(reading: Any, language: str) -> str:
    """Reads `.entity_id`, `.state`, `.unit`, `.device_class` off any state-reading-shaped
    object."""
    domain = reading.entity_id.split(".", 1)[0]
    state = reading.state if reading.state is not None else "?"
    items = getattr(reading, "items", None)
    if domain == "todo" and items is not None:
        if not items:
            return EMPTY_LIST.get(language, EMPTY_LIST["en"])
        return ", ".join(items)
    if (
        domain == "binary_sensor"
        and reading.device_class in DOOR_CLASSES
        and state in DOOR_WORDS["en"]
    ):
        return DOOR_WORDS.get(language, DOOR_WORDS["en"])[state]
    words = STATE_WORDS.get(language, STATE_WORDS["en"])
    if (domain, state) in words:
        return words[(domain, state)]
    if reading.unit:
        value = state.replace(".", ",") if language == "de" else state
        return f"{value} {reading.unit}"
    return state


def action_clause(phrase: str, targets: str, language: str) -> str:
    """One "<verb> <targets>" clause in the word order of the language.

    English puts the verb first ("turn off Spots (Küche)"), German last
    ("Spots (Küche) ausschalten"). Either slot may be empty: callers that already hold a
    finished, multi-action description pass it as `targets` with an empty `phrase`.
    """
    lang = language if language in TEMPLATES else "en"
    parts = (phrase, targets) if lang == "en" else (targets, phrase)
    return " ".join(p for p in parts if p)


THRESHOLD_WORDS = {"en": {"<": "below", ">": "above"}, "de": {"<": "unter", ">": "über"}}


def describe_expected(condition: Any, language: str) -> str:
    """What the condition waits for, in words: 'below 20' / 'unter 20' for a threshold, the
    state word (or raw state) otherwise. Reads `.operator`, `.threshold`, `.expected_state`,
    `.subject.entity_id`."""
    op = getattr(condition, "operator", None)
    threshold = getattr(condition, "threshold", None)
    if op is not None and threshold is not None:
        words = THRESHOLD_WORDS.get(language, THRESHOLD_WORDS["en"])
        number = f"{threshold:g}".replace(".", ",") if language == "de" else f"{threshold:g}"
        return f"{words[op]} {number}"
    domain = condition.subject.entity_id.split(".", 1)[0]
    words = STATE_WORDS.get(language, STATE_WORDS["en"])
    return words.get((domain, condition.expected_state), condition.expected_state)


def format_value(value: str, unit: str | None, language: str) -> str:
    """A measured value with its unit, decimal comma in German."""
    shown = value.replace(".", ",") if language == "de" else value
    return f"{shown} {unit}" if unit else shown


def condition_clause(subject: str, state: str, language: str) -> str:
    """A trailing "if <subject> is <state>" clause, for use as `confirm`'s `condition` slot."""
    return CONDITION_WORD.get(language, CONDITION_WORD["en"]).format(subject=subject, state=state)


def render(outcome: str, language: str, **slots: Any) -> str:
    lang = language if language in TEMPLATES else "en"
    tpl = TEMPLATES[lang][outcome]
    if outcome == "query_answer":
        return "\n".join(slots["lines"])
    if outcome == "clarify":
        return tpl.format(options=", ".join(slots["options"]))
    if outcome == "execution_failed":
        return tpl.format(failed=", ".join(slots["failed"]))
    if outcome == "condition_not_met" and slots.get("value"):
        return TEMPLATES[lang]["condition_not_met_value"].format(**slots)
    if outcome == "condition_not_met":
        return tpl.format(subject=slots["subject"], expected=slots["expected"])
    if outcome in ("action_done", "confirm"):
        # `phrase`/`targets` are one clause; a multi-verb plan arrives pre-joined in
        # `targets` with an empty `phrase` (spec §9).
        body = action_clause(slots.get("phrase") or "", slots.get("targets") or "", lang)
        if outcome == "action_done":
            return tpl.format(body=body)
        return tpl.format(
            body=body,
            condition=slots.get("condition") or "",
            why=REASONS[lang].get(slots.get("reason") or "", ""),
        )
    return tpl.format(**slots)


def condition_context() -> str:
    """English context for `extra_system_prompt` when a request is handed off because of a
    condition Hunch cannot evaluate (a numeric threshold, a sensor without discrete states)."""
    return (
        "Context from the Hunch assistant: this request makes its action depend on a condition "
        "('if', 'when', 'wenn', 'falls'). Check the condition against the current state first and "
        "act only if it holds right now. If it does not hold, do nothing and tell the user the "
        "current value instead."
    )


def pending_context(question: str, description: str) -> str:
    """English context for `extra_system_prompt` when a pending question got a non-yes/no reply."""
    return (
        "Context from the Hunch assistant: it proposed to " + description + " and asked the user: "
        f'"{question}". The user did not simply confirm or decline; their reply follows. '
        "Handle the reply as a modification or a new request about that proposal."
    )
