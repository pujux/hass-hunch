"""Question wording, separated from question structure.

A Phrasebook holds every natural-language string Jev sees: verb phrasings, the Round 1
templates and flag instructions, Round 2 templates, parameter labels and Score rubric levels.
Question ids, state keys and option sentinels ("none", NO_MATCH) stay language-neutral.
EN is the default and what the golden corpus was tuned on; DE exists to test whether a German
home is better served by German questions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Phrasebook:
    code: str
    verb_question: str  # {phrasing}
    floor_question: str  # {name}
    area_question: str  # {label}
    domain_question: str  # {domain}
    flags: Mapping[str, str]
    scene_question: str
    condition_domain_question: str
    exclusion_question: str  # {name} {area}
    target_question: str  # {phrasing}
    param_question: str  # {param}
    cond_subject_question: str
    cond_state_question: str
    device_question: str
    verb_phrasing: Mapping[str, str] = field(default_factory=dict)  # overrides Verb.phrasing
    domain_labels: Mapping[str, str] = field(default_factory=dict)  # overrides raw domain ids
    param_labels: Mapping[str, str] = field(default_factory=dict)  # overrides ParamSpec.name
    score_levels: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )  # overrides ScoreSpec.levels

    def phrasing_for(self, verb_name: str, default: str) -> str:
        return self.verb_phrasing.get(verb_name, default)

    def domain_label(self, domain: str) -> str:
        return self.domain_labels.get(domain, domain)

    def param_label(self, name: str) -> str:
        return self.param_labels.get(name, name.replace("_", " "))

    def levels_for(self, name: str, default: tuple[str, ...]) -> tuple[str, ...]:
        return self.score_levels.get(name, default)


EN = Phrasebook(
    code="en",
    verb_question="Does the request ask to {phrasing}?",
    floor_question="Does the request refer to the floor '{name}' or to all of it?",
    area_question="Does the request refer to the area '{label}'?",
    domain_question="Does the request involve devices of type '{domain}'?",
    flags={
        "collective": (
            "Does the request target every matching device in its scope for at least one of its "
            "actions — signaled by a plain plural (e.g. 'the kitchen lights'), or a word like "
            "'all', 'both' or 'every' — rather than exactly one specific device?"
        ),
        "has_exception": (
            "Does the request exclude something, e.g. 'except', 'but not', 'apart from', "
            "'other than'?"
        ),
        "has_condition": (
            "Does the request make the action depend on a condition, "
            "e.g. 'if', 'when', 'unless', 'only if'?"
        ),
        "has_timing": (
            "Does the request ask to delay, schedule, sequence or time a device action, "
            "e.g. 'in ten minutes', 'after', 'later', 'then' — as opposed to merely mentioning "
            "a future time in a request that is not about controlling a device (such as asking "
            "about tomorrow's weather)?"
        ),
        "is_destructive": (
            "Would fulfilling the request cause irreversible, unsafe or security-relevant "
            "effects beyond an ordinary lock, unlock, arm or disarm action (which are handled "
            "separately) — for example, disabling safety equipment, or leaving the home open to "
            "unauthorized entry?"
        ),
    },
    scene_question="Which scene or script does the request name, if any?",
    condition_domain_question=(
        "If the request contains a condition, which device type is the condition about?"
    ),
    exclusion_question=(
        "The request in `request` names an exception — something that must NOT be affected. "
        "Is the candidate named '{name}' in area '{area}' that exception?"
    ),
    target_question="Which single device does the request want to {phrasing}?",
    param_question="What {param} does the request ask for?",
    cond_subject_question="Which device is the request's condition about?",
    cond_state_question="Which state must that device be in for the request's condition to hold?",
    device_question="Which device does the request refer to?",
)

DE = Phrasebook(
    code="de",
    verb_question="Verlangt die Anfrage, {phrasing}?",
    floor_question=(
        "Bezieht sich die Anfrage auf das Stockwerk '{name}' oder auf das ganze Stockwerk?"
    ),
    area_question="Bezieht sich die Anfrage auf den Raum bzw. Bereich '{label}'?",
    domain_question="Betrifft die Anfrage Geräte der Art '{domain}'?",
    flags={
        "collective": (
            "Zielt die Anfrage bei mindestens einer ihrer Aktionen auf alle passenden Geräte in "
            "ihrem Bereich ab — erkennbar an einem einfachen Plural (z. B. 'die Lichter in der "
            "Küche') oder an Wörtern wie 'alle', 'beide' oder 'jedes' — statt auf genau ein "
            "bestimmtes Gerät?"
        ),
        "has_exception": (
            "Schließt die Anfrage etwas aus, z. B. mit 'außer', 'aber nicht', 'abgesehen von', "
            "'bis auf'?"
        ),
        "has_condition": (
            "Macht die Anfrage die Aktion von einer Bedingung abhängig, z. B. 'wenn', 'falls', "
            "'sobald', 'nur wenn', 'außer wenn'?"
        ),
        "has_timing": (
            "Verlangt die Anfrage, eine Geräteaktion zu verzögern, zu planen, zeitlich zu steuern "
            "oder in eine Reihenfolge zu bringen, z. B. 'in zehn Minuten', 'nachher', 'später', "
            "'danach' — im Gegensatz zur bloßen Erwähnung eines Zeitpunkts in einer Anfrage, die "
            "gar kein Gerät steuert (etwa die Frage nach dem Wetter von morgen)?"
        ),
        "is_destructive": (
            "Hätte die Erfüllung der Anfrage unumkehrbare, unsichere oder sicherheitsrelevante "
            "Folgen, die über ein gewöhnliches Absperren, Aufsperren, Scharfschalten oder "
            "Entschärfen hinausgehen (diese werden getrennt behandelt) — zum Beispiel das "
            "Abschalten von Sicherheitseinrichtungen oder ein Haus, das Unbefugten offen steht?"
        ),
    },
    scene_question="Welche Szene oder welches Skript nennt die Anfrage, falls überhaupt?",
    condition_domain_question=(
        "Falls die Anfrage eine Bedingung enthält: Um welche Geräteart geht es in der Bedingung?"
    ),
    exclusion_question=(
        "Die Anfrage in `request` nennt eine Ausnahme — etwas, das NICHT betroffen sein darf. "
        "Ist der Kandidat namens '{name}' im Raum '{area}' diese Ausnahme?"
    ),
    target_question="Auf welches einzelne Gerät bezieht sich die Anfrage ({phrasing})?",
    param_question="Welche {param} verlangt die Anfrage?",
    cond_subject_question="Um welches Gerät geht es in der Bedingung der Anfrage?",
    cond_state_question=(
        "In welchem Zustand muss dieses Gerät sein, damit die Bedingung der Anfrage erfüllt ist?"
    ),
    device_question="Auf welches Gerät bezieht sich die Anfrage?",
    verb_phrasing={
        "turn_on": (
            "ein bestimmtes Gerät oder bestimmte Geräte direkt einzuschalten "
            "(nicht über eine Szene oder ein Skript)"
        ),
        "turn_off": (
            "ein bestimmtes Gerät oder bestimmte Geräte direkt auszuschalten "
            "(nicht über eine Szene oder ein Skript)"
        ),
        "set_brightness": "einzustellen, wie hell ein Licht ist (dimmen, heller, dunkler)",
        "open": "Rollos, Jalousien, Vorhänge oder ein Garagentor zu öffnen bzw. hochzufahren",
        "close": "Rollos, Jalousien, Vorhänge oder ein Garagentor zu schließen bzw. herunterzufahren",
        "set_position": (
            "Rollos, Jalousien oder Vorhänge auf eine bestimmte Zwischenposition zu fahren, etwa "
            "halb oder auf einen Prozentwert, statt nur ganz auf oder ganz zu"
        ),
        "lock": "eine Tür abzusperren bzw. zu verriegeln (nicht aufsperren)",
        "unlock": "eine Tür aufzusperren bzw. zu entriegeln (nicht absperren)",
        "set_temperature": "eine Zieltemperatur einzustellen oder es wärmer oder kälter zu machen",
        "set_volume": "die Lautstärke eines Lautsprechers oder Fernsehers zu ändern (lauter, leiser)",
        "media_pause": "die Wiedergabe zu pausieren",
        "media_play": "die Wiedergabe zu starten oder fortzusetzen",
        "arm": "die Alarmanlage scharf zu schalten",
        "disarm": "die Alarmanlage zu entschärfen",
        "activate": "eine Szene zu aktivieren oder ein Skript beim Namen zu starten",
        "query_state": (
            "zu erfahren, ob etwas an, aus, offen, geschlossen oder gesperrt ist, "
            "oder welchen Wert es hat"
        ),
    },
    domain_labels={
        "light": "Licht / Lampen",
        "switch": "Schalter / Steckdosen",
        "cover": "Rollos, Jalousien, Markisen oder Tore",
        "media_player": "Fernseher / Lautsprecher / Medienwiedergabe",
        "fan": "Lüfter / Ventilatoren",
        "climate": "Klimaanlage / Heizung / Thermostat",
        "sensor": "Sensoren / Messwerte (Temperatur, Luftfeuchtigkeit …)",
        "binary_sensor": "Kontakte und Melder (Fenster, Türen, Bewegung, Präsenz)",
        "humidifier": "Luftbefeuchter",
        "vacuum": "Staubsaugerroboter",
        "todo": "Listen / Erinnerungen",
        "lock": "Türschlösser",
        "alarm_control_panel": "Alarmanlage",
        "scene": "Szenen",
        "script": "Skripte",
    },
    param_labels={
        "brightness_pct": "Helligkeit",
        "position": "Position (wie weit offen)",
        "temperature": "Temperatur",
        "volume_level": "Lautstärke",
    },
    score_levels={
        "brightness_pct": ("aus", "sehr gedimmt", "gedimmt", "mittel", "hell", "volle Helligkeit"),
        "position": (
            "ganz geschlossen",
            "größtenteils geschlossen",
            "halb offen",
            "größtenteils offen",
            "ganz offen",
        ),
        "temperature": (
            "kalt (16 °C)",
            "kühl (18 °C)",
            "mild (20 °C)",
            "warm (22 °C)",
            "heiß (24 °C)",
        ),
        "volume_level": ("stumm", "leise", "mittel", "laut", "maximal"),
    },
)

PHRASEBOOKS: dict[str, Phrasebook] = {"en": EN, "de": DE}
