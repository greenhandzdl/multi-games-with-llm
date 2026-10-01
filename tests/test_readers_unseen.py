"""执行普查里"call 阶段从没落到过"的四格，各补一枚读得懂它的证人。

四格分属三问：模糊子串有没有第二次命中（`schema.py` 的归一化）、人话标点有没有被记进
偏离清单（同一本里的 `_deviations_of`）、票数没结完时复盘页印什么、以及一条"没有席位
署名的决策"在 M6 里算不算一票。

每一格都配一具只打那一格的定位刀（见本项目归档 #243 一节）：断言必须只靠那一格才成立，
否则普查给的"没走到"就又被读成"没人读过"。
"""
from wolfengine import metrics, render_html, schema
from wolfengine.events import Event, Kind


def test_a_sentence_that_only_hides_a_synonym_still_normalizes_to_one_act():
    """`ACT_SYNONYMS` 的整词表没命中时，模糊子串那一支还得给出一个 act。"""
    # 「观望」不是表里的键，但含它的整句被"观望"这一枚子串接住。
    assert schema.normalize_act("我先观望一下再发言") == "listen"
    # 反面对照：表里根本没有的子串仍然没有人格化命中，归一化回空。
    assert schema.normalize_act(" xyz ") is None


def test_curly_quotes_in_a_response_are_recorded_as_a_deviation():
    """模型用弯引号写键名时，偏离清单要点名 smart_quotes，而不是只说"读不出对象"。"""
    devs = schema._deviations_of("{\u201cact\u201d: \u201cpass\u201d}")
    assert "smart_quotes" in devs
    # 同一句里的直引号版本不该带上这一枚——否则这枚标记等于没说。
    assert "smart_quotes" not in schema._deviations_of('{"act": "pass"}')


def test_a_vote_wave_without_a_result_prints_no_tally():
    """票还没结完时复盘页的那一格印空串，而不是自己编一个票数形状。"""
    assert render_html._tally(None) == ""


def _spoken(seq: int, actor, target: int) -> Event:
    return Event(seq=seq, kind=Kind.SPEECH, day=1, phase="day_speech", visibility="all",
                 actor=actor,
                 payload={"meta": {"model": "witness"}, "text": "我怀疑他",
                          "act": "accuse", "target": target,
                          "belief": {"suspects": [{"seat": target}, {"seat": 4}]}})


def test_a_decision_nobody_signed_is_not_counted_as_a_choice():
    """M6 只数署名了的决策：一条 actor 为空的事件进来，读数必须与没有它时逐字相同。"""
    signed = _spoken(1, 3, 5)
    unsigned = _spoken(2, None, 5)
    assert metrics.m6_belief_action([signed, unsigned]) == metrics.m6_belief_action([signed])
    # 这一格不是"空表也给空读数"那种地板：少了守卫，那条无名事件会被当成一票。
    assert metrics.m6_belief_action([unsigned]) == metrics.m6_belief_action([])
