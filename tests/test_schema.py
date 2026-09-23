"""Parse/repair ladder coverage.

These cases are CONSTRUCTED, not reaped. The plan asked for fixtures taken from real
endpoint output; the endpoint is offline, and nothing has been running long enough to
reject a response yet. Every rejected raw output is persisted by `events.py` in
`attempts[]`, so real ones arrive at M2 and should be appended here verbatim rather than
invented now. A fixture I write to match my expectation of the model proves nothing about
the model.
"""

from __future__ import annotations

import json

import pytest

from wolfengine.schema import (
    coerce_seat,
    errors_to_prompt_lines,
    extract_first_object,
    normalize_act,
    parse_action,
    salvage_candidates,
)

CLEAN = '{"act":"accuse","target":3,"speech":"3号你票型太干净了。","evidence":["e97","e132"],' \
         '"belief":{"suspects":[{"seat":3,"why":"票型"}],"read":{"1":"wolf"}}}'


class TestRung0:
    def test_clean_json(self):
        out = parse_action(CLEAN)
        assert out.ok and out.rung == 0
        assert out.action.target == 3 and out.action.evidence == ["e97", "e132"]
        assert out.action.belief.suspects[0].seat == 3

    def test_trailing_prose_after_close_brace_is_dropped(self):
        """`stop` is not honoured on this endpoint, so the scanner must be the truncator."""
        out = parse_action(CLEAN + "\n\n好的，以上就是我的发言，谢谢大家。")
        assert out.ok and out.rung == 0 and "谢谢" not in out.raw_used

    def test_prose_before_the_object_is_skipped(self):
        out = parse_action("让我想想。" + CLEAN)
        assert out.ok and out.action.act == "accuse"

    def test_scanner_respects_escapes_inside_strings(self):
        """A `}` and a whole fake object inside speech must not end the real object.

        Built with json.dumps so the fixture is valid JSON by construction; hand-written
        escapes here would test my typing rather than the scanner.
        """
        import json

        speech = '他说"我偷了}"，其实{"act":"kill","target":9}是编的'
        payload = json.dumps({"act": "defend", "speech": speech, "target": 2}, ensure_ascii=False)
        out = parse_action(payload)
        assert out.ok, out.errors
        assert out.action.target == 2
        assert out.action.speech == speech
        obj, rest = extract_first_object(payload)
        assert obj == payload and rest == ""


class TestRung1:
    def test_markdown_fence_is_parsed_but_flagged(self):
        """The scanner locates the object through a fence, so rung alone would call this
        clean. It is not clean: it is a contract violation, and M3a must count it."""
        out = parse_action("```json\n" + CLEAN + "\n```")
        assert out.ok and out.rung == 0 and "fence" in out.deviations

    def test_full_width_structural_punctuation(self):
        broken = CLEAN.replace('"act"', '＂act＂').replace(",", "，").replace("{", "｛").replace("}", "｝")
        out = parse_action(broken)
        assert out.ok and out.rung == 1 and "full_width_punctuation" in out.deviations

    def test_full_width_comma_inside_speech_survives(self):
        """The dangerous direction: a repair that normalises content would rewrite what the
        player actually said, and the transcript would silently stop matching the audio."""
        keep = '{"act":"accuse","target":3,"speech":"3号，你昨晚一直在笑，我很怀疑，非常怀疑。"}'
        out = parse_action(keep)
        assert out.ok
        assert out.action.speech == "3号，你昨晚一直在笑，我很怀疑，非常怀疑。"

    def test_truncated_mid_speech(self):
        out = parse_action('{"act":"vote","target":5,"speech":"我跟')
        assert out.ok and out.rung == 1 and out.action.target == 5
        assert out.action.speech == "我跟" and "truncated" in out.deviations

    def test_truncated_mid_key_falls_back_to_last_complete_pair(self):
        out = parse_action('{"act":"vote","target":5,"evidence":["e1"],\n"tar')
        assert out.ok and out.action.target == 5 and out.action.evidence == ["e1"]

    def test_salvage_leaves_a_complete_object_alone(self):
        assert salvage_candidates(CLEAN) == [CLEAN]
        assert salvage_candidates('{"a":1} trailing') == ['{"a":1}']

    def test_salvage_does_not_count_braces_inside_speech(self):
        import json

        body = json.dumps({"act": "defend", "speech": "他说了 } 还有 {"}, ensure_ascii=False)
        assert salvage_candidates(body) == [body]

    def test_salvage_returns_nothing_without_a_brace(self):
        assert salvage_candidates("没有 JSON") == []

    def test_trailing_comma(self):
        out = parse_action('{"act":"pass","evidence":[],}')
        assert out.ok and out.action.act == "pass"


class TestRung2:
    @pytest.mark.parametrize("raw,expected", [
        ("3号", 3), ("三号", 3), ("P3", 3), (3, 3), ("3", 3), (3.0, 3),
        (" 12 号玩家 ", 12), ("", None), (None, None), ("零", None), (True, None), (-1, None),
    ])
    def test_coerce_seat(self, raw, expected):
        assert coerce_seat(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("怀疑", "accuse"), ("指控", "accuse"), ("vote", "vote"), ("跟票", "align"),
        ("观望", "listen"), ("弃票", "pass"), ("刀", "kill"), ("查验", "check"),
        (" ACCUSE ", "accuse"), (" nonsense ", None), ("", None), (7, None),
    ])
    def test_normalize_act(self, raw, expected):
        assert normalize_act(raw) == expected

    def test_string_seat_is_coerced_and_flagged(self):
        out = parse_action('{"act":"accuse","target":"3号","speech":"x"}')
        assert out.ok and out.action.target == 3 and "coerced_field" in out.deviations

    def test_suspects_given_as_bare_seats(self):
        out = parse_action('{"act":"accuse","target":3,"belief":{"suspects":["5号",{"seat":"7"}]}}')
        assert out.ok and [s.seat for s in out.action.belief.suspects] == [5, 7]

    def test_evidence_as_comma_string(self):
        out = parse_action('{"act":"accuse","target":3,"evidence":"e97, e132"}')
        assert out.ok and out.action.evidence == ["e97", "e132"]


class TestRung3AndFailure:
    def test_unknown_act_enum_is_a_validation_error_not_a_crash(self):
        out = parse_action('{"act":"静观其变","speech":"我先听听"}')
        assert not out.action and out.errors and out.rung == -1
        assert any("act" in ".".join(map(str, e["loc"])) for e in out.errors)

    def test_target_out_of_range(self):
        out = parse_action('{"act":"accuse","target":99}')
        assert not out.ok and out.errors

    def test_no_json_at_all(self):
        out = parse_action("我觉得吧，今天可能没人是狼。")
        assert not out.ok and out.rung == -1 and out.errors

    def test_empty_and_whitespace(self):
        for bad in ("", "   ", "\n\n"):
            assert not parse_action(bad).ok

    def test_object_of_wrong_shape_is_rejected(self):
        assert not parse_action("[1,2,3]").ok


class TestRetryPrompt:
    def test_lines_name_the_reject_reason_and_the_legal_set(self):
        out = parse_action('{"act":"nope","speech":"x"}')
        text = errors_to_prompt_lines(out.errors, ("accuse", "defend"), [3, 4, 5])
        assert "act" in text and "accuse" in text and "[3, 4, 5]" in text
        assert len(text.splitlines()) <= 8

    def test_no_errors_still_produces_legal_list(self):
        text = errors_to_prompt_lines([], ("vote",), [2])
        assert "vote" in text and "[2]" in text


# ---------------------------------------------------------------- the ladder must climb
def test_a_truncated_object_keeps_climbing_instead_of_giving_up():
    """The ladder used to `return` on the first candidate that parsed but failed pydantic,
    so rung1's salvage never ran for the most common real failure: the model talking past
    `max_tokens`. A truncated object *parses* and fails validation, which is precisely the
    case salvage exists for."""
    cut = '{"act":"accuse","target":"3号","speech":"这段发言还没有说完'
    out = parse_action(cut)
    assert out.ok, out.errors
    assert out.rung == 1
    assert "truncated" in out.deviations


def test_the_first_error_is_still_what_the_retry_prompt_quotes():
    """Climbing must not hide which rung hurt: if nothing validates, the caller retries
    against the strictest candidate's complaint, not the last one's."""
    out = parse_action('{"nonsense": 1}')
    assert not out.ok and out.errors


def test_coercion_is_counted_even_when_the_ladder_succeeds():
    out = parse_action('{"act":"怀疑","target":"P3","speech":"x"}')
    assert out.ok and "coerced_field" in out.deviations, out.deviations
    assert out.action is not None and out.action.target == 3 and out.action.act == "accuse"


def test_no_candidate_is_tried_twice():
    """`tried` can hold the same string from two paths; a duplicate would double-count a
    deviation in M3a."""
    seen: list[str] = []
    real = json.loads

    def spy(s):
        seen.append(s)
        return real(s)

    json.loads = spy  # type: ignore[assignment]
    try:
        parse_action('{"act":"accuse","target":3,"speech":"好"}')
    finally:
        json.loads = real  # type: ignore[assignment]
    assert len(seen) == len(set(seen)), seen
