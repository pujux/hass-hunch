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
    verb_primary_question: str
    area_primary_question: str
    special_descriptions: Mapping[
        str, str
    ]  # several_verbs, no_verb, several_places, whole_home, no_place
    exclusion_question: str  # {name} {area}
    target_question: str  # {phrasing}
    param_question: str  # {param}
    param_value_question: str  # {param}: which literal number is the value
    param_relative_question: str  # Noul: is the number a change from the current value
    param_inverted_question: str  # Noul (covers): does the number say how far CLOSED
    all_of_question: str  # Noul: does the request mean every listed candidate
    outside_scope_question: str  # Noul: {phrasing} — a second action outside the named room?
    include_question: (
        str  # Noul per candidate of a mixed-type sweep: {name} {type} {area} {phrasing}
    )
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
    # words people say for a verb, in this language (cue words, shown to Jev with the verb)
    verb_synonyms: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # words people say for a domain, in this language
    domain_synonyms: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # what one READS of a device type to check a condition (descriptions of condition_domain)
    condition_domain_descriptions: Mapping[str, str] = field(default_factory=dict)
    # domain -> state -> what that state means in everyday words (descriptions of cond_state)
    condition_state_descriptions: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    # Choice per named room when a request mixes a set and a device: {room} {kind} {phrasing}
    room_target_question: str = (
        "The request names several rooms; this question is about the room '{room}', which it "
        "does mention. Does the request mean ALL of the {kind} devices there (the room is named "
        "with only the kind of device, like 'licht in der küche' or 'Rollos im Schlafzimmer'), "
        "or ONE particular device there (named by its own name, like 'Esszimmer Stehlampe')? "
        "Choose none of these only if the request does not refer to this room at all. The "
        "request asks to {phrasing}."
    )

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
    domain_question=(
        "Does the request act on or ask about devices of type '{domain}' — as its target, not "
        "merely a device it names as an exception ('except the fridge') or in a condition "
        "('if the blinds are closed')?"
    ),
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
        "condition_numeric": (
            "Does the request's condition compare a MEASURED VALUE against a number or threshold "
            "— 'wenn es wärmer als 23 Grad ist', 'if it is below 40%', 'über 25 Grad', 'when "
            "more than two people are home' — rather than checking a state such as open/closed, "
            "on/off, home/away, dark/bright, running/stopped? No if there is no condition at all."
        ),
        "has_timing": (
            "Does the request ask to delay, schedule, sequence or time a device action, e.g. 'in "
            "ten minutes', 'after', 'later', 'then', 'for 15 minutes'? Not a condition ('if', "
            "'when the door is open', 'wenn') and not a mere mention of a time in a request that "
            "controls no device (asking about tomorrow's weather)."
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
        "If the request contains a condition ('if', 'when', 'wenn', 'falls'): which device type "
        "would one READ to check it? Pick the type of the thing being observed in the condition, "
        "not the thing being controlled by the action."
    ),
    verb_primary_question=(
        "Which ONE action does the request primarily ask for? Compare the options: a word like "
        "'auf' or 'zu' can look like several verbs but the request means one, and 'außer' / "
        "'except' introduces an exception, not an action ('Licht aus außer …' is turn_off). "
        "Choose 'several' "
        "only if it clearly asks for more than one different action; 'none' if it asks for no "
        "device action at all (a question about the world, a joke, a calculation, the time)."
    ),
    area_primary_question=(
        "Which ONE room, area or floor does the request refer to? Choose 'several' if it names "
        "more than one, 'whole home' for 'alles', 'everything', 'überall', 'im ganzen Haus', and "
        "'none' if no place is named or implied (a bare 'Licht aus' names none)."
    ),
    special_descriptions={
        "several_verbs": "more than one distinct action is requested",
        "no_verb": "no device action is requested at all",
        "several_places": "two or more rooms or floors are named",
        "whole_home": "the whole house is meant",
        "no_place": "no room, area or floor is named or implied",
        "no_condition": (
            "the request has no condition, or its condition is about none of these device "
            "types (the time of day, the weather forecast, something a person said)"
        ),
        "no_condition_state": (
            "the condition is not about a state of this device — it compares a number or "
            "threshold, or refers to a time or the weather"
        ),
        "no_condition_subject": (
            "none of the listed devices is the thing the condition observes — e.g. it is about "
            "darkness, rain or presence and nothing here measures that"
        ),
        "all_in_room": (
            "every device of this kind in the room — the request names the room with only the "
            "kind of device, no particular one"
        ),
        "none_in_room": "the request does not refer to this room or to any device in it",
    },
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
        "'die Rollos', 'the lights', or only a place named with no particular device — `scope` "
        "says which floors or rooms the request named, with their aliases), rather than one "
        "particular device among them?"
    ),
    outside_scope_question=(
        "The devices listed in `candidates` are NOT of the kind or in the place the request "
        "refers to — they only came up because nothing closer matched. Does the request "
        "nevertheless contain an instruction to {phrasing} that applies to devices like these "
        "(e.g. 'turn off the kitchen lights and close the blinds' — the blinds are a separate "
        "action)? Answer no if the action was meant for something else (another room, a "
        "vacuum, a blind) or this verb just echoes a word like 'zu', 'auf' or 'start'."
    ),
    include_question=(
        "The request means several devices at once. Is '{name}' ({type}, {area}) one of the "
        "devices it asks to {phrasing}? Answer no for a device of a kind the request did not "
        "mean — e.g. a fridge plug when 'the kitchen lights' were asked for."
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
    verb_synonyms={
        "turn_on": ("on", "switch on", "turn on", "start"),
        "turn_off": ("off", "switch off", "turn off", "kill"),
        "open": ("open", "up", "raise"),
        "close": ("close", "down", "lower", "shut"),
        "set_position": ("halfway", "percent", "%", "position"),
        "set_brightness": ("dim", "brighter", "darker", "brightness"),
        "set_temperature": ("degrees", "warmer", "cooler", "temperature"),
        "set_volume": ("louder", "quieter", "volume"),
        "lock": ("lock",),
        "unlock": ("unlock",),
        "query_state": ("is …?", "how warm", "how much", "which … are"),
        "activate": ("scene", "run the script"),
    },
    condition_domain_descriptions={
        "sensor": "a measured value is read: temperature, humidity, brightness, power, air quality",
        "binary_sensor": (
            "an open/closed, detected/clear or present/away state is read: doors, windows, "
            "motion, presence, darkness, rain, water leaks"
        ),
        "climate": "a thermostat, heating or air conditioner: whether it runs and in which mode",
        "cover": "whether blinds, shutters, curtains or a garage door are open or closed",
        "light": "whether a light is on or off",
        "switch": "whether a switch or plug is on or off",
        "fan": "whether a fan is on or off",
        "media_player": "whether a TV or speaker is playing, paused or off",
        "lock": "whether a door is locked or unlocked",
        "alarm_control_panel": "whether the alarm is armed or disarmed",
        "person": "whether someone is at home or away",
        "vacuum": "whether the robot vacuum is cleaning, docked or idle",
        "humidifier": "whether a humidifier is on or off",
    },
    condition_state_descriptions={
        "binary_sensor": {
            "on": (
                "active — a door or window IS open, motion or presence IS detected, it IS dark, "
                "wet or occupied"
            ),
            "off": (
                "inactive — a door or window is closed, no motion or presence, it is bright, dry "
                "or empty"
            ),
        },
        "cover": {
            "open": "open, up",
            "closed": "closed, down",
            "opening": "moving up right now",
            "closing": "moving down right now",
        },
        "climate": {
            "heat": "heating",
            "cool": "cooling — the air conditioner is running",
            "auto": "running in automatic mode",
            "off": "switched off, not running",
        },
        "lock": {"locked": "locked", "unlocked": "unlocked", "jammed": "jammed, stuck"},
        "media_player": {
            "playing": "playing",
            "paused": "paused",
            "idle": "on but playing nothing",
            "off": "switched off",
        },
        "person": {"home": "at home", "not_home": "away, not at home"},
    },
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
    domain_question=(
        "Wirkt die Anfrage auf Geräte der Art '{domain}' oder fragt sie danach — als ihr Ziel, "
        "nicht bloß als Gerät, das sie als Ausnahme ('außer dem Kühlschrank') oder in einer "
        "Bedingung ('wenn die Rollos zu sind') nennt?"
    ),
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
        "condition_numeric": (
            "Vergleicht die Bedingung der Anfrage einen MESSWERT mit einer Zahl oder Schwelle — "
            "'wenn es wärmer als 23 Grad ist', 'unter 40 %', 'über 25 Grad', 'wenn mehr als zwei "
            "Personen zu Hause sind' — statt einen Zustand zu prüfen wie offen/geschlossen, "
            "an/aus, zu Hause/abwesend, dunkel/hell, läuft/steht? Nein, wenn es gar keine "
            "Bedingung gibt."
        ),
        "has_timing": (
            "Verlangt die Anfrage, eine Geräteaktion zu verzögern, zu planen, zeitlich zu steuern "
            "oder zu befristen, z. B. 'in zehn Minuten', 'nachher', 'später', 'danach', 'für 15 "
            "Minuten'? Keine Bedingung ('wenn', 'falls', 'sobald die Tür offen ist') und keine "
            "bloße Zeitangabe in einer Anfrage, die kein Gerät steuert (Wetter von morgen)."
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
        "Falls die Anfrage eine Bedingung enthält ('wenn', 'falls', 'sobald'): Welche Geräteart "
        "würde man ABLESEN, um sie zu prüfen? Wähle die Art der beobachteten Sache in der "
        "Bedingung, nicht die der gesteuerten."
    ),
    verb_primary_question=(
        "Welche EINE Aktion verlangt die Anfrage in erster Linie? Vergleiche die Optionen: ein "
        "Wort wie 'auf' oder 'zu' kann nach mehreren Verben aussehen, gemeint ist eines. Wähle "
        "'several' nur, wenn eindeutig mehrere verschiedene Aktionen verlangt sind; 'none', wenn "
        "gar keine Geräteaktion verlangt ist (eine Frage über die Welt, ein Witz, eine Rechnung, "
        "die Uhrzeit)."
    ),
    area_primary_question=(
        "Auf welchen EINEN Raum, Bereich oder welches Stockwerk bezieht sich die Anfrage? Wähle "
        "'several' bei mehreren, 'whole home' bei 'alles', 'überall', 'im ganzen Haus', und "
        "'none', wenn kein Ort genannt oder gemeint ist (ein bloßes 'Licht aus' nennt keinen)."
    ),
    special_descriptions={
        "several_verbs": "mehr als eine verschiedene Aktion ist verlangt",
        "no_verb": "es ist gar keine Geräteaktion verlangt",
        "several_places": "zwei oder mehr Räume oder Stockwerke sind genannt",
        "whole_home": "das ganze Haus ist gemeint",
        "no_place": "kein Raum, Bereich oder Stockwerk ist genannt oder gemeint",
        "no_condition": (
            "die Anfrage hat keine Bedingung, oder ihre Bedingung betrifft keine dieser "
            "Gerätearten (Uhrzeit, Wettervorhersage, etwas, das jemand gesagt hat)"
        ),
        "no_condition_state": (
            "die Bedingung betrifft keinen Zustand dieses Geräts — sie vergleicht eine Zahl "
            "oder Schwelle oder meint eine Uhrzeit oder das Wetter"
        ),
        "no_condition_subject": (
            "keines der aufgezählten Geräte ist das, was die Bedingung beobachtet — z. B. geht "
            "es um Dunkelheit, Regen oder Anwesenheit und nichts hier misst das"
        ),
        "all_in_room": (
            "jedes Gerät dieser Art im Raum — die Anfrage nennt den Raum nur mit der Geräteart, "
            "ohne bestimmtes Gerät"
        ),
        "none_in_room": "die Anfrage bezieht sich weder auf diesen Raum noch auf ein Gerät darin",
    },
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
        "Rollos', 'die Lichter', oder nur ein Ort ohne bestimmtes Gerät — `scope` nennt die "
        "Stockwerke oder Räume der Anfrage samt Aliasen), statt eines bestimmten Geräts darunter?"
    ),
    outside_scope_question=(
        "Die Geräte in `candidates` sind NICHT von der Art oder an dem Ort, auf die sich die "
        "Anfrage bezieht — sie kamen nur ins Spiel, weil nichts Näheres passte. Enthält die "
        "Anfrage trotzdem eine Anweisung, {phrasing}, die für solche Geräte gilt (z. B. "
        "'Küchenlicht aus und Rollos zu' — die Rollos sind eine eigene Aktion)? Antworte nein, "
        "wenn die Aktion etwas anderem galt (einem anderen Raum, einem Staubsauger, einem Rollo) "
        "oder das Verb bloß ein Wort wie 'zu', 'auf' oder 'starte' widerspiegelt."
    ),
    include_question=(
        "Die Anfrage meint mehrere Geräte auf einmal. Gehört '{name}' ({type}, {area}) zu den "
        "Geräten, die sie {phrasing} soll? Antworte nein bei einer Geräteart, die nicht gemeint "
        "war — z. B. eine Kühlschrank-Steckdose, wenn 'die Küchenlichter' verlangt wurden."
    ),
    param_inverted_question=(
        "Nur bei Rollos oder Jalousien: gibt die Anfrage die Zahl als Anteil GESCHLOSSEN an — "
        "'15% zu', '15% geschlossen' — statt als übliche Position, bei der 'auf 15%', 'zu 15% "
        "offen', '15% offen' alle 15% offen bedeuten?"
    ),
    param_value_descriptions={
        "none of these": (
            "die Zahlen meinen etwas anderes: eine Uhrzeit, eine Anzahl, das Wetter, ein "
            "anderes Gerät"
        )
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
    verb_synonyms={
        "turn_on": ("an", "ein", "einschalten", "anmachen", "aufdrehen", "starte"),
        "turn_off": ("aus", "ausschalten", "ausmachen", "abdrehen", "aus außer …"),
        "open": ("auf", "öffnen", "hoch", "hochfahren", "rauf"),
        "close": ("zu", "schließen", "runter", "runterfahren", "herunter"),
        "set_position": ("halb", "prozent", "%", "position"),
        "set_brightness": ("dimmen", "heller", "dunkler", "helligkeit"),
        "set_temperature": ("grad", "wärmer", "kälter", "temperatur"),
        "set_volume": ("lauter", "leiser", "lautstärke"),
        "lock": ("absperren", "zusperren", "verriegeln"),
        "unlock": ("aufsperren", "entriegeln"),
        "query_state": ("ist …?", "wie warm", "wie viel", "welche … sind"),
        "activate": ("szene", "skript starten"),
    },
    condition_domain_descriptions={
        "sensor": "ein Messwert wird abgelesen: Temperatur, Luftfeuchtigkeit, Helligkeit, Leistung",
        "binary_sensor": (
            "ein Zustand offen/geschlossen, erkannt/frei oder anwesend/abwesend wird abgelesen: "
            "Türen, Fenster, Bewegung, Präsenz, Dunkelheit, Regen, Wasser"
        ),
        "climate": "Thermostat, Heizung oder Klimaanlage: ob sie läuft und in welchem Modus",
        "cover": "ob Rollos, Jalousien, Vorhänge oder ein Tor offen oder geschlossen sind",
        "light": "ob ein Licht an oder aus ist",
        "switch": "ob ein Schalter oder eine Steckdose an oder aus ist",
        "fan": "ob ein Lüfter an oder aus ist",
        "media_player": "ob ein Fernseher oder Lautsprecher spielt, pausiert oder aus ist",
        "lock": "ob eine Tür abgesperrt oder aufgesperrt ist",
        "alarm_control_panel": "ob die Alarmanlage scharf oder unscharf ist",
        "person": "ob jemand zu Hause oder abwesend ist",
        "vacuum": "ob der Staubsaugerroboter saugt, in der Station steht oder wartet",
        "humidifier": "ob ein Luftbefeuchter an oder aus ist",
    },
    condition_state_descriptions={
        "binary_sensor": {
            "on": (
                "aktiv — eine Tür oder ein Fenster IST offen, Bewegung oder Präsenz IST erkannt, "
                "es IST dunkel, nass oder belegt"
            ),
            "off": (
                "inaktiv — Tür oder Fenster geschlossen, keine Bewegung oder Präsenz, es ist "
                "hell, trocken oder leer"
            ),
        },
        "cover": {
            "open": "offen, oben",
            "closed": "geschlossen, unten",
            "opening": "fährt gerade hoch",
            "closing": "fährt gerade runter",
        },
        "climate": {
            "heat": "heizt",
            "cool": "kühlt — die Klimaanlage läuft",
            "auto": "läuft im Automatikmodus",
            "off": "ausgeschaltet, läuft nicht",
        },
        "lock": {"locked": "abgesperrt", "unlocked": "aufgesperrt", "jammed": "verklemmt"},
        "media_player": {
            "playing": "spielt",
            "paused": "pausiert",
            "idle": "an, spielt aber nichts",
            "off": "ausgeschaltet",
        },
        "person": {"home": "zu Hause", "not_home": "abwesend, nicht zu Hause"},
    },
    room_target_question=(
        "Die Anfrage nennt mehrere Räume; diese Frage betrifft den Raum '{room}', den sie "
        "erwähnt. Meint die Anfrage ALLE {kind} dort (der Raum ist nur mit der Geräteart genannt, "
        "wie 'licht in der küche' oder 'Rollos im Schlafzimmer') oder EIN bestimmtes Gerät dort "
        "(mit eigenem Namen, wie 'Esszimmer Stehlampe')? Wähle none of these nur, wenn die "
        "Anfrage diesen Raum gar nicht meint. Die Anfrage will {phrasing}."
    ),
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
