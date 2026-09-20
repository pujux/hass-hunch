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
    param_value_question: str  # {param}: which literal number is the value
    param_relative_question: str  # Noul: is the number a change from the current value
    param_inverted_question: str  # Noul (covers): does the number say how far CLOSED
    all_of_question: str  # Noul: does the request mean every listed candidate
    outside_scope_question: str  # Noul: {phrasing} — a second action outside the named room?
    cond_subject_question: str
    cond_state_question: str
    device_question: str
    param_value_descriptions: Mapping[str, str] = field(
        default_factory=dict
    )  # for the NO_MATCH option
    verb_phrasing: Mapping[str, str] = field(default_factory=dict)  # overrides Verb.phrasing
    domain_labels: Mapping[str, str] = field(default_factory=dict)  # overrides raw domain ids
    param_labels: Mapping[str, str] = field(default_factory=dict)  # overrides ParamSpec.name
    score_levels: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )  # overrides ScoreSpec.levels
    # words people say for a domain, in this language
    domain_synonyms: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

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
        "names_specific": (
            "Does the request point at ONE particular device by a name or label of its own — "
            "such as 'Stehlampe', 'Spots', 'desk lamp', 'the TV' — rather than only by a kind of "
            "device ('light', 'blinds', 'Rollos') together with a room, floor or a word like "
            "'all'? A plural-looking proper name still counts as one device."
        ),
        "has_exception": (
            "Does the request exclude something, e.g. 'except', 'but not', 'apart from', "
            "'other than'?"
        ),
        "has_condition": (
            "Does the request make the action depend on a condition about the state of the "
            "world, e.g. 'if', 'when', 'unless', 'only if' — as opposed to merely excluding a "
            "device with 'except' / 'but not' / 'außer', which is not a condition?"
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
    param_value_question=(
        "Which of these numbers from the request is the {param} to set? Choose none of them if "
        "the number means something else (a time, a count, the weather, another device)."
    ),
    param_relative_question=(
        "Is that number a CHANGE relative to the current value — 'by 2 degrees warmer', '20% "
        "quieter', 'um 20% dunkler' — as opposed to the position or value to END AT? "
        "('15% zu', 'auf 15%', 'to 22 degrees' all name an end value, not a change.)"
    ),
    all_of_question=(
        "Looking at `candidates`: does the request mean ALL of these devices (a plural like "
        "'die Rollos', 'the lights', or a room/floor named with no particular device), rather "
        "than one particular device among them?"
    ),
    outside_scope_question=(
        "The request names a room or floor, but the devices in `candidates` are elsewhere. Does "
        "the request contain a SEPARATE instruction to {phrasing} that applies to devices like "
        "these — as in 'turn off the kitchen lights and close the blinds', where closing the "
        "blinds is its own action not limited to the kitchen? Answer no if the only thing asked "
        "is inside the named room and this verb just echoes a word like 'zu' or 'auf'."
    ),
    param_inverted_question=(
        "For blinds or shutters only: does the request give that number as how far CLOSED they "
        "should be — '15% zu', '15% geschlossen', 'closed 15%' — rather than the usual position "
        "where 'auf 15%', 'to 15%', '15% open' all mean 15% open?"
    ),
    param_value_descriptions={
        "none of these": (
            "the numbers mean something else: a time, a count, the weather, another device"
        )
    },
    cond_subject_question="Which device is the request's condition about?",
    cond_state_question="Which state must that device be in for the request's condition to hold?",
    device_question="Which device does the request refer to?",
    domain_synonyms={
        "light": ("light", "lights", "lamp", "lamps", "lighting"),
        "cover": (
            "blind",
            "blinds",
            "shade",
            "shades",
            "curtain",
            "curtains",
            "shutter",
            "shutters",
            "garage door",
            "awning",
        ),
        "switch": ("switch", "switches", "plug", "plugs", "socket", "sockets", "outlet", "outlets"),
        "media_player": ("tv", "television", "speaker", "speakers", "music", "radio", "stereo"),
        "fan": ("fan", "fans"),
        "climate": (
            "thermostat",
            "heating",
            "heater",
            "air conditioning",
            "air conditioner",
            "ac",
            "climate",
        ),
        "lock": ("lock", "locks", "door lock"),
        "vacuum": ("vacuum", "robot vacuum", "hoover"),
        "humidifier": ("humidifier",),
        "alarm_control_panel": ("alarm", "alarm system"),
    },
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
        "names_specific": (
            "Meint die Anfrage EIN bestimmtes Gerät mit einem eigenen Namen oder einer eigenen "
            "Bezeichnung — etwa 'Stehlampe', 'Spots', 'Schreibtischlampe', 'der Fernseher' — statt "
            "nur eine Geräteart ('Licht', 'Rollos') zusammen mit einem Raum, Stockwerk oder einem "
            "Wort wie 'alle'? Ein Eigenname in Pluralform zählt trotzdem als ein Gerät."
        ),
        "has_exception": (
            "Schließt die Anfrage etwas aus, z. B. mit 'außer', 'aber nicht', 'abgesehen von', "
            "'bis auf'?"
        ),
        "has_condition": (
            "Macht die Anfrage die Aktion von einer Bedingung über den Zustand der Welt abhängig, "
            "z. B. 'wenn', 'falls', 'sobald', 'nur wenn' — im Gegensatz zum bloßen Ausschließen "
            "eines Geräts mit 'außer' / 'aber nicht', was keine Bedingung ist?"
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
    param_value_question=(
        "Welche dieser Zahlen aus der Anfrage ist die einzustellende {param}? Wähle keine davon, "
        "wenn die Zahl etwas anderes meint (eine Uhrzeit, eine Anzahl, das Wetter, ein anderes "
        "Gerät)."
    ),
    param_relative_question=(
        "Ist diese Zahl eine ÄNDERUNG gegenüber dem aktuellen Wert — 'um 2 Grad wärmer', '20% "
        "leiser', 'um 20% dunkler' — im Gegensatz zu der Position bzw. dem Wert, bei dem es ENDEN "
        "soll? ('15% zu', 'auf 15%', 'auf 22 Grad' nennen alle einen Endwert, keine Änderung.)"
    ),
    all_of_question=(
        "Mit Blick auf `candidates`: meint die Anfrage ALLE diese Geräte (ein Plural wie 'die "
        "Rollos', 'die Lichter', oder ein Raum/Stockwerk ohne bestimmtes Gerät), statt eines "
        "bestimmten Geräts darunter?"
    ),
    outside_scope_question=(
        "Die Anfrage nennt einen Raum oder ein Stockwerk, aber die Geräte in `candidates` sind "
        "anderswo. Enthält die Anfrage eine EIGENE Anweisung, {phrasing}, die für solche Geräte "
        "gilt — wie in 'Küchenlicht aus und Rollos zu', wo das Schließen der Rollos eine eigene, "
        "nicht auf die Küche beschränkte Aktion ist? Antworte nein, wenn nur etwas im genannten "
        "Raum verlangt wird und dieses Verb bloß ein Wort wie 'zu' oder 'auf' widerspiegelt."
    ),
    param_inverted_question=(
        "Nur bei Rollos oder Jalousien: gibt die Anfrage die Zahl als Anteil GESCHLOSSEN an — "
        "'15% zu', '15% geschlossen' — statt als übliche Position, bei der 'auf 15%', 'zu 15% "
        "offen', '15% offen' alle 15% offen bedeuten?"
    ),
    param_value_descriptions={
        "none of these": "die Zahlen meinen etwas anderes: eine Uhrzeit, eine Anzahl, das Wetter, ein anderes Gerät"
    },
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
        "close": (
            "Rollos, Jalousien, Vorhänge oder ein Garagentor zu schließen bzw. herunterzufahren"
        ),
        "set_position": (
            "Rollos, Jalousien oder Vorhänge auf eine bestimmte Zwischenposition zu fahren, etwa "
            "halb oder auf einen Prozentwert, statt nur ganz auf oder ganz zu"
        ),
        "lock": "eine Tür abzusperren bzw. zu verriegeln (nicht aufsperren)",
        "unlock": "eine Tür aufzusperren bzw. zu entriegeln (nicht absperren)",
        "set_temperature": "eine Zieltemperatur einzustellen oder es wärmer oder kälter zu machen",
        "set_volume": (
            "die Lautstärke eines Lautsprechers oder Fernsehers zu ändern (lauter, leiser)"
        ),
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
    domain_synonyms={
        "light": ("licht", "lichter", "lampe", "lampen", "leuchte", "leuchten", "beleuchtung"),
        "cover": (
            "rollo",
            "rollos",
            "jalousie",
            "jalousien",
            "rolladen",
            "rollladen",
            "rollläden",
            "vorhang",
            "vorhänge",
            "markise",
            "markisen",
            "raffstore",
            "garagentor",
        ),
        "switch": ("schalter", "steckdose", "steckdosen", "stecker", "steckerleiste"),
        "media_player": (
            "fernseher",
            "tv",
            "lautsprecher",
            "musik",
            "radio",
            "box",
            "boxen",
            "anlage",
        ),
        "fan": ("lüfter", "ventilator", "ventilatoren", "abluft"),
        "climate": ("klimaanlage", "klima", "heizung", "thermostat", "klimagerät"),
        "lock": ("schloss", "türschloss"),
        "vacuum": ("staubsauger", "saugroboter", "roboter"),
        "humidifier": ("luftbefeuchter", "befeuchter"),
        "alarm_control_panel": ("alarmanlage", "alarm"),
    },
)

PHRASEBOOKS: dict[str, Phrasebook] = {"en": EN, "de": DE}
