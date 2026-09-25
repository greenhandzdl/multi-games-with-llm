"""金丝雀测试：信息隔离是被证明的，不是被声称的（plan §11）。

产品成立的前提是"3 号狼人不知道 7 号验了谁"。这件事通常靠"我们没把那条事件放进去"
来保证，而"没放"会因为多一个调用点、少一次过滤而失效。这里用两样东西把它钉住：

1. **canary**：给每条私有事件的可渲染字段里塞一个唯一串，然后把全体座位的 prompt
   **字节**拼起来查。断言是双向的（`canary in bytes == 有权看到`），因为只查"不该出现的
   没出现"会被一个坏掉的渲染器假绿——它什么都不渲染，于是永远不泄漏。
2. **反向对照**：故意把一条狼队私聊改成全体可见，测试必须失败。防的是断言写反、
   或者 percept 构造时根本没过滤这两种情况。

超时/并发的隔离在 `test_prefix_stability.py`；这里只管"谁能看到什么"。
"""

from __future__ import annotations

import dataclasses

import pytest

from wolfengine import actors, assemble, belief, human, info, persona, state
from wolfengine.config import Config
from wolfengine.events import Event, Kind, PUBLIC, seats

CFG = Config()
ALL_SEATS = tuple(range(1, 10))


def _event(seq: int, kind: str, *, day: int = 1, visibility=PUBLIC,
           actor: int | None = None, **payload: object) -> Event:
    return Event(seq=seq, kind=kind, day=day, phase="day_speech",
                 visibility=visibility, payload=dict(payload), actor=actor)


def _ctx_for(seat: int, events: tuple[Event, ...], at_seq: int):
    """The real `TurnContext` this seat would be handed at this moment.

    One builder for both readers — the prompt and the screen a person reads — because two
    fixtures would let a leak found in one be an artifact of the other. Assembled rather than
    filtered-by-hand: the claim under test is about what a seat is *shown*, so a test that only
    checked `Percept.events` would miss a renderer that goes looking for its own facts elsewhere.
    """
    percept = info.percept_for(seat, events, at_seq=at_seq)
    bs = belief.build_belief(seat, percept.events)
    legal = state.LegalSet(acts=("accuse", "defend", "listen"),
                           targets=frozenset(s for s in ALL_SEATS if s != seat))
    p = assemble.assemble(
        cfg=CFG, percept=percept, seat_role=percept.role(),
        persona=persona.PersonaParams(), belief=bs, legal=legal,
        phase=state.Phase.DAY_SPEECH)
    return actors.TurnContext(
        seat=seat, role=percept.role(), phase=state.Phase.DAY_SPEECH,
        percept=percept, legal=legal, persona=persona.PersonaParams(), prompt=p, belief=bs)


def _prompt_bytes(seat: int, events: tuple[Event, ...], at_seq: int) -> str:
    ctx = _ctx_for(seat, events, at_seq)
    return "\n".join(m["content"] for m in ctx.prompt.messages)


def _card_bytes(seat: int, events: tuple[Event, ...], at_seq: int) -> str:
    """The same seat's world, rendered for a person instead of a model.

    `#124`: the isolation guarantee was only ever proven on the prompt path. A human seat reads a
    screen built by a different function, and "it uses the same `Percept`" is a claim about bytes
    until something checks the bytes.
    """
    return human.decision_card(_ctx_for(seat, events, at_seq))


def _board() -> tuple[Event, ...]:
    """Nine deals, one wolf chat, one check result, one knife notice, one night action.

    Every private kind appears exactly once, each carrying a canary in the field its
    renderer reads — patching a field that is never printed would make the test pass while
    proving nothing.
    """
    wolves = (1, 2, 3)
    out = [_event(1, Kind.GAME_START, seats=list(ALL_SEATS))]
    for i, s in enumerate(ALL_SEATS):
        role = "wolf" if s in wolves else ("seer" if s == 7 else "witch" if s == 5 else "villager")
        out.append(_event(2 + i, Kind.DEAL, visibility=seats(s), actor=s,
                          role=f"{role}-CANARY_DEAL_{2+i}",
                          teammates=[x for x in wolves if x != s] if s in wolves else []))
    n = 11
    out += [
        _event(n, Kind.WOLF_CHAT, visibility=seats(*wolves), actor=1,
               text="CANARY_WOLFCHAT_12 今晚刀6号。"),
        _event(n + 1, Kind.NOTICE, visibility=seats(5), actor=None,
               text="CANARY_NOTICE_13 今晚6号倒在了狼刀下。", about=6),
        _event(n + 2, Kind.NIGHT_ACTION, visibility=seats(5), actor=5,
               act="CANARY_WITCH_14", target=6, potion="save"),
        _event(n + 3, Kind.NIGHT_ACTION, visibility=seats(7), actor=7,
               act="CANARY_SEER_15", target=1),
        _event(n + 4, Kind.SEER_RESULT, visibility=seats(7), actor=7,
               target=1, verdict="CANARY_RESULT_16"),
        _event(n + 5, Kind.SPEECH, actor=4, text="6号昨晚没说话，我怀疑他。",
               act="accuse", target=6),
    ]
    return tuple(out)


BOARD = _board()
LAST_SEQ = BOARD[-1].seq

# (canary, seats entitled to see it) — read off the visibility sets above, not re-derived
# from a helper, so a bug in `visible_to` cannot make the expectation agree with the leak.
CANARIES = [
    ("CANARY_WOLFCHAT_12", {1, 2, 3}),
    ("CANARY_NOTICE_13", {5}),
    ("CANARY_WITCH_14", {5}),
    ("CANARY_SEER_15", {7}),
    ("CANARY_RESULT_16", {7}),
]


@pytest.mark.parametrize("canary,entitled", CANARIES, ids=[c for c, _ in CANARIES])
def test_private_facts_reach_exactly_their_owners(canary, entitled):
    for seat in ALL_SEATS:
        shown = canary in _prompt_bytes(seat, BOARD, LAST_SEQ)
        assert shown == (seat in entitled), (
            f"{canary} {'leaked to' if shown else 'missing from'} seat {seat}")


@pytest.mark.parametrize("seat", ALL_SEATS)
def test_a_seat_sees_its_own_role_and_nobody_elses(seat):
    """Role cards travel the same filtered path as everything else, so "只看到自己的" is
    the same property as the canaries — asserted separately because a seat that could not
    read *its own* role is unplayable, and that failure is invisible in the test above."""
    bytes_ = _prompt_bytes(seat, BOARD, LAST_SEQ)
    mine = f"CANARY_DEAL_{1 + seat}"
    assert mine in bytes_
    for other in ALL_SEATS:
        if other != seat:
            assert f"CANARY_DEAL_{1 + other}" not in bytes_


def test_reverse_control_a_deliberate_leak_is_caught():
    """Without this, an assertion that always passes and a filter that never ran look alike.

    The mutation is the one a real bug would make: someone moves an event to the shared
    channel because it is easier than threading the seat list through.
    """
    leaked = tuple(
        dataclasses.replace(e, visibility=PUBLIC) if e.kind == Kind.WOLF_CHAT else e
        for e in BOARD)
    victim = 6  # a villager with no claim on the wolf channel
    assert "CANARY_WOLFCHAT_12" not in _prompt_bytes(victim, BOARD, LAST_SEQ)
    assert "CANARY_WOLFCHAT_12" in _prompt_bytes(victim, leaked, LAST_SEQ)


# ------------------------------------------------------- the same board, on a person's screen
#
# `info.py`'s module docstring claims a human seat "cannot leak either, because the data isn't in
# it". That was true of the *object* and untested about the *screen* — and the screen is what a
# person reads. These three are that claim's witnesses; the first two mirror the prompt tests
# exactly so a green here means the same property, not a weaker one.


@pytest.mark.parametrize("canary,entitled", CANARIES, ids=[c for c, _ in CANARIES])
def test_the_screen_holds_exactly_what_this_seat_was_allowed_to_know(canary, entitled):
    for seat in ALL_SEATS:
        shown = canary in _card_bytes(seat, BOARD, LAST_SEQ)
        assert shown == (seat in entitled), (
            f"{canary} {'leaked onto' if shown else 'missing from'} seat {seat}'s screen")


@pytest.mark.parametrize("seat", ALL_SEATS)
def test_the_screen_names_the_role_this_seat_was_dealt_and_nobody_elses(seat):
    """The header line is the one place the card does *not* render events, so it needs its own
    assertion: `ctx.role` is read back out of this seat's own DEAL by `percept_for`, and a card
    that printed another seat's role would leak without touching the transcript."""
    card = _card_bytes(seat, BOARD, LAST_SEQ)
    assert f"CANARY_DEAL_{1 + seat}" in card, f"seat {seat} 的卡片上没有自己的身份"
    for other in ALL_SEATS:
        if other != seat:
            assert f"CANARY_DEAL_{1 + other}" not in card


def test_reverse_control_a_deliberate_leak_reaches_the_screen_too():
    """The screen's version of the control above, and it carries a second job.

    The card shows a *window* of the transcript (`human.SCREEN_TAIL`), so an event that fell out
    of the window would make the "leaked" half of a canary test unachievable — the absence
    assertion would pass for the wrong reason. This one asserts the leak does show up, which is
    only possible while the canary sits inside the window: grow this board past
    `SCREEN_TAIL` and it goes red and says so, instead of quietly degrading the tests above.
    """
    leaked = tuple(
        dataclasses.replace(e, visibility=PUBLIC) if e.kind == Kind.WOLF_CHAT else e
        for e in BOARD)
    victim = 6
    assert "CANARY_WOLFCHAT_12" not in _card_bytes(victim, BOARD, LAST_SEQ)
    assert "CANARY_WOLFCHAT_12" in _card_bytes(victim, leaked, LAST_SEQ)


def test_percept_refuses_to_be_constructed_with_someone_elses_event():
    """`percept_for` filters; `Percept.__post_init__` is the belt. Both are needed, because
    the second is what fails when someone later writes `Percept(seat=..., events=all_events)`
    by hand instead of going through the entry point."""
    private = [e for e in BOARD if e.kind == Kind.WOLF_CHAT]
    with pytest.raises(info.IsolationError):
        info.Percept(seat=6, at_seq=LAST_SEQ, events=tuple(BOARD[:1] + tuple(private)))


def test_as_of_cuts_a_voters_world_before_the_ballots_land():
    """密封投票靠的是可见性时点，不是写入顺序。

    Real engine order: every ballot is appended as its own turn closes, so by the time the
    last seat is asked, some ballots are already in the log. `as_of` pinned to the phase
    opening is the only thing between that fact and every later voter reading the tally.
    """
    opened_at = LAST_SEQ
    ballots = tuple(
        _event(seq, Kind.VOTE, day=2, actor=seat, target=(6 if seat != 6 else 9))
        for seat, seq in zip(ALL_SEATS, range(17, 17 + len(ALL_SEATS))))
    events = BOARD + ballots
    for seat in ALL_SEATS:
        mine = info.percept_for(seat, events, at_seq=opened_at)
        assert not mine.by_kind(Kind.VOTE), f"seat {seat} saw ballots cast after it opened"
        assert all(b.actor == seat for b in ballots if b.seq <= mine.at_seq)
    # ...and once the round closes, they are public — the same cut must not over-filter.
    after = info.percept_for(6, events, at_seq=ballots[-1].seq)
    assert len(after.by_kind(Kind.VOTE)) == len(ALL_SEATS)


def test_wolves_hear_each_other_and_nobody_else_hears_them():
    """The channel that keeps three wolves from knife-ing three different seats."""
    chats = [e for e in BOARD if e.kind == Kind.WOLF_CHAT]
    assert chats
    for seat in ALL_SEATS:
        p = info.percept_for(seat, BOARD, at_seq=LAST_SEQ)
        assert len(p.by_kind(Kind.WOLF_CHAT)) == (1 if seat <= 3 else 0)


def test_unordered_input_cannot_silently_build_a_shorter_world():
    with pytest.raises(info.IsolationError):
        info.Percept(seat=1, at_seq=LAST_SEQ, events=(BOARD[5], BOARD[2]))


def test_a_wolf_seat_reads_his_roster_in_his_own_prompt():
    """`#133` 印出了名册，可那是离线的四台机器：模型那一屏两条梯子都把 DEAL 筛在外面。

    `assemble.py` 的私有块明写着 `e.kind != Kind.DEAL`，B 段的 `chronicle()` 又只留
    `visibility == "all"`，所以发牌那一行从来没有到过 prompt。代价落在第一夜先开口的那只狼
    （`phases.py` 的 proposer）身上：他在任何一条私聊落盘之前就得决定刀谁，而他唯一的队友线索
    本该是私聊行。名册接进 `== 你的座位 ==` 那一句而不是新开一块：`block_tokens` 换东家的判据
    是"以 `== ` 开头"，这一句本来就不在 §5 的任何一格里，动它不挤占 B0/C1–C4 的预算。
    """
    for wolf, mates in {1: (2, 3), 2: (1, 3), 3: (1, 2)}.items():
        head = _prompt_bytes(wolf, BOARD, LAST_SEQ).split("== 你的座位 ==")[-1]
        head = head.split("== 你的性格参数 ==")[0]
        want = "你的队友是 " + "、".join(f"{s}号" for s in mates) + "。"
        assert want in head, f"{wolf} 号的座位块里没有名册 {want}：\n{head}"


def test_a_seat_that_was_not_dealt_a_team_reads_no_words_for_one():
    """只查"狼看得到"会放过一具无条件印名册的刀——那正是狼队名单公开的形状。"""
    for seat in (4, 5, 6, 7, 8, 9):
        bytes_ = _prompt_bytes(seat, BOARD, LAST_SEQ)
        assert "队友" not in bytes_, f"{seat} 号的 prompt 里冒出了队友"
