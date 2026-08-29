from expregaze_jali.text_utils import match_anchor_word_sequence


def test_match_anchor_word_sequence_preserves_exact_matching():
    assert match_anchor_word_sequence("dangerous.", ["dangerous"], 0) == 1
    assert match_anchor_word_sequence("he'll", ["hell"], 0) is None


def test_match_anchor_word_sequence_supports_strict_hyphenated_fragments():
    assert match_anchor_word_sequence("Mm-hmm.", ["mm", "hmm"], 0) == 2
    assert match_anchor_word_sequence("Mm-hmm.", ["mmhmm"], 0) == 1
    assert match_anchor_word_sequence("mother-in-law", ["mother", "in", "law"], 0) == 3
    assert match_anchor_word_sequence("Mm-hmm.", ["mm"], 0) is None
    assert match_anchor_word_sequence("Mm-hmm.", ["mm", "why"], 0) is None
