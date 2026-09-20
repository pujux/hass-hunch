from hunch.phrasing import DE, EN, PHRASEBOOKS
from hunch.round1 import FLAGS, build_round1_questions
from hunch.vocabulary import DEFAULT_VOCABULARY, ScoreSpec


def test_default_phrasebook_is_english_and_unchanged(home, vocab):
    assert build_round1_questions(home, vocab) == build_round1_questions(home, vocab, EN)
    assert build_round1_questions(home, vocab)["verb:turn_off"].instructions.startswith(
        "Does the request ask to"
    )


def test_german_phrasebook_produces_german_questions(home, vocab):
    qs = build_round1_questions(home, vocab, DE)
    assert qs["verb:turn_off"].instructions.startswith(
        "Verlangt die Anfrage, ein bestimmtes Gerät oder bestimmte Geräte direkt auszuschalten "
        "(nicht über eine Szene oder ein Skript)"
    )
    assert "Licht / Lampen" in qs["domain:light"].instructions
    assert (
        qs["area:living"].instructions
        == "Bezieht sich die Anfrage auf den Raum bzw. Bereich 'Living room (lounge)'?"
    )
    assert set(qs) == set(build_round1_questions(home, vocab, EN))  # same ids, same structure


def test_german_phrasebook_is_complete():
    assert set(DE.flags) == set(FLAGS) == set(EN.flags)
    assert set(DE.verb_phrasing) == set(DEFAULT_VOCABULARY.names)
    for v in DEFAULT_VOCABULARY.verbs:
        if isinstance(v.param, ScoreSpec):
            assert len(DE.levels_for(v.param.name, ())) == len(v.param.levels), v.param.name
            assert v.param.name in DE.param_labels
    assert PHRASEBOOKS == {"en": EN, "de": DE}
