"""Round 1: judge the *shape* of the request over a tiny state. Never shows Jev an entity list."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from hunch.config import Thresholds
from hunch.model import Area, Entity, HomeModel
from hunch.phrasing import EN, PHRASEBOOKS, Phrasebook
from hunch.questions import JSON, Answers, ChoiceQ, NoulQ, Question
from hunch.resolution import PreviousTurn, Trace
from hunch.vocabulary import Verb, Vocabulary

FLAGS = (
    "collective",
    "names_specific",
    "has_exception",
    "has_condition",
    "condition_numeric",
    "is_fragment",
    "has_timing",
    "is_destructive",
)


def _label(area: Area) -> str:
    return f"{area.name} ({', '.join(area.aliases)})" if area.aliases else area.name


NEW_REQUEST = "new request"
SAME_DEVICES = "same devices, new action"
SAME_ACTION = "same action, other place"
ADD_DEVICES = "add devices"
MORE_SAME = "more about the same"
FOLLOW_UP_OPTIONS = (NEW_REQUEST, SAME_DEVICES, SAME_ACTION, ADD_DEVICES, MORE_SAME)


def previous_turn_state(home: HomeModel, previous: PreviousTurn) -> JSON:
    """The last turn, structured: what was asked, what was done, to which devices."""
    areas = {a.area_id: a.name for a in home.areas}
    return {
        "request": previous.prompt,
        "actions": [
            {
                "action": a.verb.name,
                "devices": [
                    {"name": e.name, "area": areas.get(e.area_id or "")} for e in a.targets
                ],
                **({"values": dict(a.params)} if a.params else {}),
            }
            for a in previous.actions
        ],
    }


def build_round1_state(home: HomeModel, prompt: str, previous: PreviousTurn | None = None) -> JSON:
    from hunch.scope import verbatim_matches  # local import: scope imports this module's Shape

    # Code fetches, Jev decides: the exposed devices whose name, alias or device name appears in
    # the prompt are handed over with their type and room, so a bare name like "Kücheninsel" can
    # be judged as the light it is. Never the full entity list — that stays out of Round 1.
    mentioned = [
        {
            "name": e.name,
            "type": e.domain,
            "area": home.area_by_id(e.area_id).name
            if e.area_id and home.area_by_id(e.area_id)
            else None,
        }
        for e in verbatim_matches(home.entities + home.scenes, prompt)[:10]
    ]
    # The home as a hierarchy — floors with their areas, then areas on no floor — so Jev can see
    # that "unten" is a floor holding these rooms rather than two flat lists to reconcile.
    on_floor = {a for f in home.floors for a in f.area_ids}
    hierarchy: list[JSON] = [
        {
            "floor": f.name,
            "aliases": list(f.aliases),
            "areas": [_label(a) for a in home.areas if a.area_id in f.area_ids],
        }
        for f in home.floors
    ]
    loose = [_label(a) for a in home.areas if a.area_id not in on_floor]
    if loose:
        hierarchy.append({"floor": None, "areas": loose})
    state: dict[str, JSON] = {
        "request": prompt,
        "home": hierarchy,
        "domains": list(home.domains),
        "scenes": [s.name for s in home.scenes],
        "mentioned_devices": mentioned,
    }
    if previous is not None:
        state["previous"] = previous_turn_state(home, previous)
    return state


def _place_options(home: HomeModel) -> list[str]:
    """Area labels and floor names as Choice options (floors first so 'unten' has a home)."""
    return [f.name for f in home.floors] + [_label(a) for a in home.areas]


def _floor_areas(home: HomeModel, floor_id: str) -> tuple[str, ...]:
    return next((f.area_ids for f in home.floors if f.floor_id == floor_id), ())


def _place_lookup(home: HomeModel) -> dict[str, tuple[str, ...]]:
    """Option label -> the area ids it stands for."""
    out: dict[str, tuple[str, ...]] = {f.name: tuple(f.area_ids) for f in home.floors}
    out.update({_label(a): (a.area_id,) for a in home.areas})
    return out


def build_round1_questions(
    home: HomeModel,
    vocab: Vocabulary,
    pb: Phrasebook = EN,
    previous: PreviousTurn | None = None,
) -> dict[str, Question]:
    qs: dict[str, Question] = {}
    if previous is not None:
        # Only when a last turn exists: does this sentence lean on it, and how?
        qs["follow_up"] = ChoiceQ(
            pb.follow_up_question, FOLLOW_UP_OPTIONS, pb.follow_up_descriptions
        )

    def _cued(v: Verb) -> str:
        words: list[str] = []
        for book in PHRASEBOOKS.values():
            words.extend(w for w in book.verb_synonyms.get(v.name, ()) if w not in words)
        base = pb.phrasing_for(v.name, v.phrasing)
        return f"{base} — cue words: {', '.join(words)}" if words else base

    for v in vocab.verbs:
        qs[f"verb:{v.name}"] = NoulQ(pb.verb_question.format(phrasing=_cued(v)))
    for f in home.floors:
        qs[f"floor:{f.floor_id}"] = NoulQ(pb.floor_question.format(name=f.name))
    for a in home.areas:
        qs[f"area:{a.area_id}"] = NoulQ(pb.area_question.format(label=_label(a)))
    for d in home.domains:
        # Jev judges the domain; code just hands it the words people use for it, in every
        # supported language, so "Licht", "lights" and "Rollos" are not left to inference.
        words: list[str] = []
        for book in PHRASEBOOKS.values():
            words.extend(w for w in book.domain_synonyms.get(d, ()) if w not in words)
        label = f"{pb.domain_label(d)} ({', '.join(words)})" if words else pb.domain_label(d)
        qs[f"domain:{d}"] = NoulQ(pb.domain_question.format(domain=label))
    for flag in FLAGS:
        qs[f"flag:{flag}"] = NoulQ(pb.flags[flag])
    if home.scenes:
        qs["scene"] = ChoiceQ(
            pb.scene_question,
            tuple(s.name for s in home.scenes) + ("none",),
        )
    # Two comparators.
    # COMPARE them — "auf" is open OR turn_on, not both — and say "none" or "whole home" outright.
    qs["verb_primary"] = ChoiceQ(
        pb.verb_primary_question,
        tuple(v.name for v in vocab.verbs) + ("several", "none"),
        {
            **{v.name: _cued(v) for v in vocab.verbs},
            "several": pb.special_descriptions["several_verbs"],
            "none": pb.special_descriptions["no_verb"],
        },
    )
    qs["area_primary"] = ChoiceQ(
        pb.area_primary_question,
        tuple(_place_options(home)) + ("several", "whole home", "none"),
        {
            "several": pb.special_descriptions["several_places"],
            "whole home": pb.special_descriptions["whole_home"],
            "none": pb.special_descriptions["no_place"],
        },
    )
    qs["exception_place"] = ChoiceQ(
        pb.exception_place_question,
        tuple(_place_options(home)) + ("none",),
        {"none": pb.special_descriptions["no_exception_place"]},
    )
    if home.domains:
        descriptions = {
            d: pb.condition_domain_descriptions[d]
            for d in home.domains
            if d in pb.condition_domain_descriptions
        }
        descriptions["none"] = pb.special_descriptions["no_condition"]
        qs["condition_domain"] = ChoiceQ(
            pb.condition_domain_question,
            tuple(home.domains) + ("none",),
            descriptions,
        )
    return qs


@dataclass(frozen=True)
class Shape:
    fired_verbs: tuple[Verb, ...]
    scope_areas: tuple[str, ...]
    scope_domains: tuple[str, ...]
    area_probs: Mapping[str, float]
    domain_probs: Mapping[str, float]
    flags: Mapping[str, float]
    scene: Entity | None
    condition_domain: str | None
    floor_probs: Mapping[str, float] = field(default_factory=dict)
    whole_home: bool = False
    primary_verb: str | None = None  # the verb Choice's pick, when it named one
    several_verbs: bool = False  # the verb Choice said the request asks for several actions
    follow_up: str | None = None  # SAME_DEVICES | SAME_ACTION | ADD_DEVICES | MORE_SAME
    follow_up_conf: float = 0.0
    condition_numeric: bool = False  # the condition compares a measurement with a number
    # "alle Rollos außer das in der Küche": the exception is a whole place, not a device;
    # these areas leave the scope and their devices the candidates
    exception_areas: tuple[str, ...] = ()
    # numeric conditions: every device type Jev found plausible for the condition, best first
    # ("unter 20 Grad" may be the room thermometer or the weather); the subject Choice decides
    condition_domains: tuple[str, ...] = ()

    def flag(self, name: str) -> float:
        return self.flags.get(name, 0.0)


def interpret_round1(
    home: HomeModel, vocab: Vocabulary, answers: Answers, thresholds: Thresholds, trace: Trace
) -> Shape:
    trace.record(1, answers)

    fired = [
        v
        for v in vocab.verbs
        if trace.decide(f"verb:{v.name}", answers.noul(f"verb:{v.name}"), thresholds.verb_fire)
    ]
    primary_verb: str | None = None
    several_verbs = False
    if "verb_primary" in answers.answers:
        # Jev compares. A single winner drops co-firing echoes ("auf" -> open, not turn_on)
        # and can promote a verb the Nouls left just under the bar; "none" means no device
        # action at all ("Wie spät ist es?"); "several" leaves the Noul set alone.
        vp = answers.choice("verb_primary")
        if vp.choice == "none":
            if trace.decide("verb_primary:none", vp.confidence, thresholds.flag):
                for v in fired:
                    trace.note(f"verb_dropped:{v.name}:no_action")
                fired = []
        elif vp.choice == "several":
            several_verbs = trace.decide("verb_primary:several", vp.confidence, thresholds.flag)
        elif vp.choice in vocab.names:
            primary = vocab.by_name(vp.choice)
            primary_verb = primary.name
            for v in list(fired):
                if v is not primary:
                    trace.note(f"verb_conflict:{v.name}<{primary.name}")
                    fired.remove(v)
            if primary not in fired and trace.decide(
                f"verb_primary:{primary.name}",
                max(vp.confidence, answers.noul(f"verb:{primary.name}")),
                thresholds.confirm_band,
            ):
                # The Noul and the comparison agree on this verb; neither is certain on its
                # own ("Mach alles aus": 0.65 / 0.35). Fire it — its contribution stays low, so
                # the request ends in a confirmation rather than silence.
                trace.note(f"verb_promoted:{primary.name}")
                fired = [primary]
            if primary in fired:
                # the verb's contribution is the stronger of its own Noul and the comparison
                trace.decide(
                    f"verb:{primary.name}",
                    max(answers.noul(f"verb:{primary.name}"), vp.confidence),
                    thresholds.verb_fire,
                )
    fired_verbs = tuple(fired)

    area_probs: dict[str, float] = {
        a.area_id: answers.noul(f"area:{a.area_id}") for a in home.areas
    }
    scope_areas: list[str] = []
    floor_probs: dict[str, float] = {
        f.floor_id: answers.noul(f"floor:{f.floor_id}") for f in home.floors
    }
    for f in home.floors:
        floor_fires = trace.decide(
            f"floor:{f.floor_id}", answers.noul(f"floor:{f.floor_id}"), thresholds.scope_fire
        )
        if floor_fires:
            scope_areas.extend(f.area_ids)
    for a in home.areas:
        area_fires = trace.decide(f"area:{a.area_id}", area_probs[a.area_id], thresholds.scope_fire)
        if area_fires and a.area_id not in scope_areas:
            scope_areas.append(a.area_id)

    whole_home = False
    if "area_primary" in answers.answers:
        ap = answers.choice("area_primary")
        lookup = _place_lookup(home)
        if ap.choice == "none":
            if trace.decide("area_primary:none", ap.confidence, thresholds.flag) and scope_areas:
                trace.note("areas_dropped:no_place:" + ",".join(scope_areas))
                scope_areas = []
            elif scope_areas and all(
                max(
                    area_probs.get(a, 0.0),
                    *(p for f, p in floor_probs.items() if a in _floor_areas(home, f)),
                )
                < thresholds.place_override
                for a in scope_areas
            ):
                # Even a hesitant "none" beats rooms and floors that only just cleared the bar
                # ("Rollos runter": floor 0.70 vs none 0.54 narrowed a sweep to one floor,
                # silently). Narrowing without a named place needs real conviction.
                trace.note("areas_dropped:no_place_hesitant:" + ",".join(scope_areas))
                scope_areas = []
        elif ap.choice == "whole home":
            if trace.decide("area_primary:whole_home", ap.confidence, thresholds.flag):
                whole_home = True
                scope_areas = []
        elif ap.choice in lookup and trace.decide(
            "area_primary", ap.confidence, thresholds.place_override
        ):
            # Narrowing to one place removes options Jev could still compare in Round 2 (two
            # "Dachterrassentür" doors), so it takes a sure pick; a hesitant one leaves the
            # Noul set standing.
            # Jev singled out one room or floor: that is the place, whatever else half-fired.
            chosen = list(lookup[ap.choice])
            dropped = [x for x in scope_areas if x not in chosen]
            if dropped:
                trace.note("areas_dropped:primary:" + ",".join(dropped))
            scope_areas = chosen
        # "several", or a hesitant pick: the Noul set stands

    domain_probs = {d: answers.noul(f"domain:{d}") for d in home.domains}
    scope_domains = tuple(
        d
        for d in home.domains
        if trace.decide(f"domain:{d}", domain_probs[d], thresholds.scope_fire)
    )

    flags = {flag: answers.noul(f"flag:{flag}") for flag in FLAGS}

    scene: Entity | None = None
    if "scene" in answers.answers:
        c = answers.choice("scene")
        if c.choice != "none" and trace.decide(
            "scene", c.confidence, thresholds.target_choice_conf
        ):
            scene = next((s for s in home.scenes if s.name == c.choice), None)

    exception_areas: tuple[str, ...] = ()
    if "exception_place" in answers.answers and flags["has_exception"] >= thresholds.flag:
        ep = answers.choice("exception_place")
        lookup = _place_lookup(home)
        if ep.choice in lookup and trace.decide(
            "exception_place", ep.confidence, thresholds.target_choice_conf
        ):
            exception_areas = lookup[ep.choice]
            trace.note("exception_place:" + ",".join(exception_areas))

    condition_domain: str | None = None
    condition_numeric = False
    has_condition = trace.decide("flag:has_condition", flags["has_condition"], thresholds.flag)
    if has_condition:
        condition_numeric = trace.decide(
            "flag:condition_numeric", flags["condition_numeric"], thresholds.flag
        )
    condition_domains: tuple[str, ...] = ()
    if has_condition and condition_numeric and "condition_domain" in answers.answers:
        probs = answers.choice("condition_domain").probabilities
        condition_domains = tuple(
            sorted(
                (d for d, p in probs.items() if d != "none" and p >= 0.2),
                key=lambda d: -probs[d],
            )
        )
    if has_condition and "condition_domain" in answers.answers:
        c = answers.choice("condition_domain")
        if c.choice != "none" and trace.decide(
            "condition_domain", c.confidence, thresholds.target_choice_conf
        ):
            condition_domain = c.choice
    if condition_domain is None and condition_numeric and condition_domains:
        # a hesitant domain pick ("sensor 0.65 / weather 0.3") must not strand a numeric
        # condition: the subject Choice in Round 2 compares the concrete candidates instead
        condition_domain = condition_domains[0]
        trace.note("condition_domain:hesitant_numeric")

    follow_up: str | None = None
    follow_up_conf = 0.0
    if "follow_up" in answers.answers:
        fu = answers.choice("follow_up")
        if fu.choice != NEW_REQUEST and trace.decide(
            "follow_up", fu.confidence, thresholds.target_choice_conf
        ):
            follow_up, follow_up_conf = fu.choice, fu.confidence
            trace.note(f"follow_up:{fu.choice}")

    return Shape(
        fired_verbs=fired_verbs,
        scope_areas=tuple(scope_areas),
        scope_domains=scope_domains,
        area_probs=area_probs,
        domain_probs=domain_probs,
        flags=flags,
        scene=scene,
        condition_domain=condition_domain,
        floor_probs=floor_probs,
        whole_home=whole_home,
        primary_verb=primary_verb,
        several_verbs=several_verbs,
        follow_up=follow_up,
        follow_up_conf=follow_up_conf,
        condition_numeric=condition_numeric,
        condition_domains=condition_domains,
        exception_areas=exception_areas,
    )
