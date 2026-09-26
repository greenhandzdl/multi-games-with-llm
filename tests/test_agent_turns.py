"""The retry loop in `Agent.take_turn`, driven one turn at a time.

`test_live_path.py` reaches these branches through a whole game, which is how it found that
the loop's two budgets had been merged into one — a turn that exhausted the *repair* budget
stopped pulling the *shrink* lever half-way and was then written as an ordinary fallback, so
the log claimed zero context overflows during a run the endpoint 400d 172 times. These tests
keep each branch on its own terms: what the seat was asked, how many times, what the log
says about it, and what the three counters added up to.

The actor here is a script, not a model. That is deliberate: the thing under test is the
engine's reaction to an answer, and a fake lets each turn end on exactly the branch named.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any, Callable

from wolfengine import game, rules
from wolfengine.agent import MAX_SHRINK, Agent
from wolfengine.actors import Proposal
from wolfengine.config import Config
from wolfengine.events import Kind, PUBLIC, seats
from wolfengine.llm import ContextTooLong
from wolfengine.schema import Action
from wolfengine.state import LegalSet, Phase


class Scripted:
    """An actor whose answer is chosen from the attempt number.

    `make` is called per attempt rather than replayed from a list, because some branches only
    exist when consecutive answers differ: a repair retry that repeats the identical
    complaint is stopped by the same "these bytes were just refused" guard that stops a
    stalled shrink, and a script that always says the same thing cannot tell them apart.
    """

    kind = "llm"
    blocking = False

    def __init__(self, make: Callable[[int], Any], *, limit: float | None = None) -> None:
        self.make = make
        self.limit = limit
        self.ctxs: list[Any] = []

    def timeout_for(self, phase: Phase) -> float | None:
        return self.limit

    async def act(self, ctx) -> Proposal:
        self.ctxs.append(ctx)
        reply = self.make(len(self.ctxs))
        if isinstance(reply, BaseException):
            raise reply
        if asyncio.iscoroutine(reply):
            return await reply
        return reply


def _table(tmp_path, *, chatter: int = 0, cfg: Config | None = None):
    """A night-1 table with a wolf seat, and `chatter` public speeches behind it.

    `chatter` is the lever's raw material: with nothing but the deal in the log, B2's verbatim
    window already holds every line this seat can see, so pulling the lever cannot change a
    byte — which is a branch worth having its own test, not a fluke to trip over.
    """
    cfg = cfg or Config()
    state, personas, _ = game.build_game(cfg, 7, "g00000007")
    log = game.open_log(cfg, state, tmp_path / "t.jsonl", ("llm",), 7, "20260921T000000Z")
    for i in range(chatter):
        log.append(Kind.SPEECH, day=1, phase="day_speech", actor=(i % 9) + 1,
                   text=f"{(i % 9) + 1}号说：第{i}轮我认为场上有人在带节奏，需要被点名解释。")
    agent = Agent(cfg=cfg, state=state, log=log, personas=personas,
                  known_ids=lambda: {f"e{e.seq}" for e in log.all()})
    wolf = next(s for s in state.seats if state.team_of(s) == "wolf")
    return cfg, state, log, agent, wolf, rules.legal_actions(state, wolf)


def _prompt_text(ctx) -> str:
    return "\n\n".join(m["content"] for m in ctx.prompt.messages)


async def _take(tmp_path, *, chatter: int = 0, make, limit=None, cfg: Config | None = None):
    """One turn, with the legal set resolved before the script is built.

    The script is a function of the legal set because its answers have to point at seats the
    judge actually offered — a hardcoded seat number would make an answer illegal for the
    wrong reason, and the test would then be checking the fixture.
    """
    cfg, state, log, agent, wolf, legal = _table(tmp_path, chatter=chatter, cfg=cfg)
    actor = Scripted(lambda n: make(legal, n), limit=limit)
    outcome = await agent.take_turn(
        seat=wolf, actor=actor, legal=legal, phase=Phase.NIGHT_WOLF,
        kind=Kind.NIGHT_ACTION, visibility=seats(wolf))
    return cfg, agent, actor, outcome, legal


async def _take_speech(tmp_path, *, make, cfg: Config | None = None):
    """One daytime speech turn with `accuse` assigned by the engine.

    The legal set comes out of `rules` rather than a literal tuple because the assignment is
    only enforceable while every assignable act is also a legal one; a hand-written fixture
    would stop testing that and the failure would surface as a game-wide fallback rate.
    """
    cfg, state, log, agent, wolf, _ = _table(tmp_path, cfg=cfg)
    state.phase = Phase.DAY_SPEECH
    legal = dataclasses.replace(rules.legal_actions(state, wolf), assigned_act="accuse")
    actor = Scripted(lambda n: make(legal, n))
    outcome = await agent.take_turn(
        seat=wolf, actor=actor, legal=legal, phase=Phase.DAY_SPEECH,
        kind=Kind.SPEECH, visibility=PUBLIC)
    return cfg, agent, actor, outcome, legal


def _said(act: str, text: str, target: int | None = None, attempt: int = 1) -> Proposal:
    return Proposal(action=Action(act=act, target=target, speech=text),
                    raw=f'{{"act":"{act}"}}', rung=0,
                    response={"latency_s": 0.01, "attempts": attempt})


def _kill(legal: LegalSet) -> Proposal:
    return Proposal(action=Action(act="kill", target=sorted(legal.targets)[0]),
                    raw='{"act":"kill"}', rung=0, response={"latency_s": 0.01, "attempts": 1})


def _ballot(legal: LegalSet) -> Proposal:
    return Proposal(action=Action(act="vote", target=sorted(legal.targets)[0]),
                    raw='{"act":"vote"}', rung=0, response={"latency_s": 0.01, "attempts": 1})


def _illegal(legal: LegalSet, attempt: int = 1) -> Proposal:
    """An act the wolf was never offered, alternating so each refusal says something new."""
    act = "check" if attempt % 2 else "vote"
    return Proposal(action=Action(act=act, target=sorted(legal.targets)[0]),
                    raw=f'{{"act":"{act}"}}', rung=0,
                    response={"latency_s": 0.01, "attempts": attempt})


# ---------------------------------------------------------------------------- the repair path
async def test_an_illegal_answer_is_retried_once_and_the_reason_reaches_the_next_prompt(
        tmp_path):
    cfg, agent, actor, outcome, legal = await _take(
        tmp_path, make=lambda legal, n: _illegal(legal) if n == 1 else _kill(legal))

    assert not outcome.fell_back, "a seat that answered legally on the second try was fallen back on"
    assert agent.retries == 1 and agent.fallbacks == 0
    assert len(outcome.attempts) == 1
    assert any("act_not_allowed" in v for v in outcome.attempts[0]["violations"])
    # The refused bytes are the dataset: without `raw` there is no preference pair.
    assert outcome.attempts[0]["raw"] == '{"act":"check"}'
    # C5 is the whole point of rebuilding the prompt: a retry that does not say what was
    # wrong asks the model to guess, and it guesses the same way.
    assert "act_not_allowed" in _prompt_text(actor.ctxs[1]), _prompt_text(actor.ctxs[1])[-400:]
    assert "act_not_allowed" not in _prompt_text(actor.ctxs[0])

    ev = agent.log.all()[-1]
    assert ev.payload["meta"]["fallback"] == 0
    assert ev.payload["meta"]["attempt"] == 2
    assert ev.attempts and "act_not_allowed" in ev.attempts[0]["violations"][0]


async def test_a_seat_that_never_answers_legally_is_recorded_as_the_engine_choosing(tmp_path):
    """The seat did not vote; a default did. The log has to say which."""
    cfg, agent, actor, outcome, legal = await _take(
        tmp_path, make=lambda legal, n: _illegal(legal, n))

    assert outcome.fell_back and agent.fallbacks == 1
    assert outcome.action.act in set(legal.acts) | {"pass"}
    # Bounded by the repair budget: one ask plus `max_repair_retries` re-asks, however
    # willing the loop would otherwise be.
    assert len(actor.ctxs) == cfg.max_repair_retries + 1, (
        f"{len(actor.ctxs)} sends for a repair budget of {cfg.max_repair_retries}")
    ev = agent.log.all()[-1]
    assert ev.payload["meta"]["fallback"] == 1
    assert ev.result["fallback"] == 1
    assert ev.payload["act"] == outcome.action.act


async def test_re_asking_the_same_complaint_is_stopped_without_spending_the_whole_budget(
        tmp_path):
    """The other bound on the same loop, and the reason both have to be named.

    A model that answers identically twice would be asked identically a third time, and the
    only thing that could change between those sends is the retry note — which is the same
    note. Those bytes were just refused, so the call is not made even when the budget allows
    it. A stalled repair is not a context loss, so nothing lands in `overflow=`.
    """
    roomy = dataclasses.replace(Config(), max_repair_retries=5)
    cfg, agent, actor, outcome, legal = await _take(
        tmp_path, make=lambda legal, n: _illegal(legal), cfg=roomy)

    assert len(actor.ctxs) == 2, [len(_prompt_text(c)) for c in actor.ctxs]
    assert outcome.fell_back and agent.fallbacks == 1
    assert agent.retries == 1
    assert agent.context_overflows == 0 and not outcome.context_overflow, (
        "a repeated illegality was reported as a context overflow")
    assert agent.log.all()[-1].payload["meta"]["context_overflow"] == 0


# ------------------------------------------------------------------------ the assigned act
async def test_a_seat_that_takes_its_assignment_after_being_asked_is_not_fallen_back_on(
        tmp_path):
    """The enforcement exists to be obeyed: one re-ask, then the game carries on normally."""
    cfg, agent, actor, outcome, legal = await _take_speech(
        tmp_path, make=lambda legal, n: _said("listen", "我先听听大家怎么说。") if n == 1
        else _said("accuse", "5号那句太顺了，我要他解释。", sorted(legal.targets)[0], n))

    assert len(actor.ctxs) == 2
    assert "act_not_as_assigned" in _prompt_text(actor.ctxs[1]), _prompt_text(actor.ctxs[1])[-400:]
    assert not outcome.fell_back and agent.fallbacks == 0 and agent.retries == 1
    assert outcome.action.act == "accuse"
    assert agent.log.all()[-1].payload["meta"]["attempt"] == 2


async def test_a_seat_that_still_ignores_the_assignment_keeps_its_words_and_its_own_act(
        tmp_path):
    """重问一次之后仍然不听指派：留下它说出口的那句话和它自己选的 act，标成 fallback。

    两个失败方向都不能要。用引擎的默认动作收尾会写出一条空发言——观众看到一行空白，而
    `passivity_rate` 反而看不出问题；把 act 改写成指派的那个，则让 M3 的主判据变成引擎
    自己造出来的数（persona.py 里"权重不许为 0"那条注释说的是同一件事）。所以这里选择
    第三条：话留着、标签按模型自己的记、fallback=1 让 M3 的代打率把这一类数进去。
    """
    cfg, agent, actor, outcome, legal = await _take_speech(
        tmp_path, make=lambda legal, n: _said("listen", "我先听听大家怎么说。", None, n))

    assert len(actor.ctxs) == cfg.max_repair_retries + 1
    assert outcome.fell_back and agent.fallbacks == 1
    assert outcome.action.act == "listen" and outcome.action.speech == "我先听听大家怎么说。"

    ev = agent.log.all()[-1]
    assert ev.payload["act"] == "listen", "引擎替它改了标签"
    assert ev.payload["text"] == "我先听听大家怎么说。", "它说过的话被一条空白顶掉了"
    assert ev.payload["meta"]["fallback"] == 1
    assert any(v.startswith("act_not_as_assigned") for v in ev.payload["meta"]["violations"])
    assert ev.attempts and any(v.startswith("act_not_as_assigned")
                               for v in ev.attempts[0]["violations"]), "被拒的那次没落盘"


async def test_the_salvage_never_launders_a_second_kind_of_violation(tmp_path):
    """只在"唯一没被满足的是标签"时才复用模型的话。编造事件编号的回合仍走引擎默认动作：
    那条编号一旦进正文，日志就在为一个不存在的事件作证——rung4 的全部意义就是它不能被
    说服。"""
    def both(legal, n):
        return Proposal(action=Action(act="listen", speech="我先听听大家怎么说。",
                                      evidence=["e999"]),
                        raw='{"act":"listen"}', rung=0,
                        response={"latency_s": 0.01, "attempts": n})

    cfg, agent, actor, outcome, legal = await _take_speech(tmp_path, make=both)
    ev = agent.log.all()[-1]
    stats = ev.payload["meta"]["citation_stats"]
    assert stats["valid"] == [] and stats["invented"] == [], "编造的 e999 被复用进了正文"
    assert ev.payload["act"] == "accuse", "引擎默认动作应当由指派决定，而不是照抄被拒的那次"
    assert outcome.fell_back and ev.payload["meta"]["fallback"] == 1
    assert any(v.startswith("invented_event_ids") for v in ev.attempts[0]["violations"])


# --------------------------------------------------------------------------- the hung seat
async def test_a_hung_seat_is_skipped_and_the_turn_still_costs_one_deadline(tmp_path):
    async def hang() -> Proposal:  # never returns inside the deadline
        await asyncio.sleep(30)
        return Proposal()

    started = time.monotonic()
    cfg, agent, actor, outcome, legal = await _take(
        tmp_path, make=lambda legal, n: hang(), limit=0.05)
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"the deadline did not fire: {elapsed:.1f}s"
    assert outcome.timed_out and agent.timeouts == 1
    assert outcome.fell_back
    ev = agent.log.all()[-1]
    assert ev.payload["meta"]["timed_out"] == 1
    assert ev.attempts and ev.attempts[0]["violations"] == ["timeout"]


# ---------------------------------------------------------------------------- the lever
async def test_a_context_400_pulls_the_lever_and_the_next_prompt_is_actually_smaller(tmp_path):
    """§6's remedy is fewer bytes. Two sends of the same size would be a retry, not a fix."""
    cfg, agent, actor, outcome, legal = await _take(
        tmp_path, chatter=20,
        make=lambda legal, n: ContextTooLong("too long") if n <= MAX_SHRINK else _kill(legal))

    assert [c.prompt.shrink for c in actor.ctxs] == [0, 1, 2]
    sizes = [len(_prompt_text(c)) for c in actor.ctxs]
    assert sizes[0] > sizes[1] > sizes[2], sizes
    assert agent.shrinks == MAX_SHRINK
    assert agent.context_overflows == 0, "the turn was recovered, so it is not a lost turn"
    assert not outcome.fell_back


async def test_a_turn_lost_to_context_is_counted_once_however_hard_the_lever_was_pulled(tmp_path):
    """`overflow=` in the summary and the per-event marker have to be the same quantity.

    The M3 criterion is literally "0 次 context 400", and metrics.py reads it out of the log.
    A counter that ticks per send says three lost rounds for one lost turn, and the run's
    summary stops agreeing with the transcript the same run wrote.
    """
    cfg, agent, actor, outcome, legal = await _take(
        tmp_path, chatter=20, make=lambda legal, n: ContextTooLong("too long"))

    assert len(actor.ctxs) == MAX_SHRINK + 1, (
        "the lever has to be pulled to the end before the turn is given up")
    assert agent.context_overflows == 1, f"summary counted {agent.context_overflows} for one turn"
    assert agent.shrinks == MAX_SHRINK
    assert outcome.context_overflow and outcome.fell_back
    ev = agent.log.all()[-1]
    assert ev.payload["meta"]["context_overflow"] == 1


async def test_a_turn_with_nothing_left_to_cut_is_ended_instead_of_resending_the_same_bytes(
        tmp_path):
    """The lever is a remedy only while it moves the bytes.

    On night 1 the verbatim window already holds every line this seat can see, so halving it
    changes nothing: the second prompt is the one that was just refused. Sending it again is
    the death loop §6 rules out, priced at one round trip each time.
    """
    cfg, agent, actor, outcome, legal = await _take(
        tmp_path, make=lambda legal, n: ContextTooLong("too long"))

    assert len(actor.ctxs) == 1, [len(_prompt_text(c)) for c in actor.ctxs]
    assert outcome.context_overflow and agent.context_overflows == 1
    assert agent.shrinks == 1, "the lever moved once; the guard has to catch it on the second"


# ------------------------------------------------------- 幂等守卫：不可逆事实的双写（#69）
async def _submit_twice(tmp_path, *, phase: Phase, kind: str, make, next_day: bool = False):
    """The same decision, submitted twice through the real turn path.

    `next_day` bumps the game day between the two submissions: the ballot a seat casts today
    and the one it casts tomorrow are two facts, and the only way to tell the guard which is
    which to read the key. The file's bytes after the first submission come back with the log
    because "nothing was written the second time" is a claim about bytes, not about a count.
    """
    cfg, state, log, agent, wolf, _ = _table(tmp_path)
    state.phase = phase
    legal = rules.legal_actions(state, wolf)
    actor = Scripted(lambda n: make(legal))
    vis = PUBLIC if kind == Kind.VOTE else seats(wolf)
    await agent.take_turn(seat=wolf, actor=actor, legal=legal, phase=phase,
                          kind=kind, visibility=vis)
    after_first = (tmp_path / "t.jsonl").read_bytes()
    if next_day:
        state.day += 1
    await agent.take_turn(seat=wolf, actor=actor, legal=legal, phase=phase,
                          kind=kind, visibility=vis)
    return log, agent, actor, after_first


async def test_a_repeated_submission_of_the_same_kill_leaves_one_fact(tmp_path):
    """夜里那一刀是一次写出去就收不回来的事实：同键的第二次提交不许变成第二行。

    `events.py` 的守卫自述"防的是一个真实的失败模式，不是理论上的"——客户端超时、服务端其实
    已经完成、重试路径再记一次。这个方向以前一条断言都没有：把早退那四行整块删掉，全仓库只有
    折叠标记的两条用例变红（`test_one_marker_per_distinct_fold_state` 与其直播版），
    VOTE / NIGHT_ACTION 这一路一声不响。所以这里钉的不是"代码能跑"，是那句话有没有读者。
    """
    log, agent, actor, after_first = await _submit_twice(
        tmp_path, phase=Phase.NIGHT_WOLF, kind=Kind.NIGHT_ACTION, make=_kill)

    assert len(actor.ctxs) == 2, "守卫在写侧，不在问侧：重复提交仍然花了一次模型调用"
    kills = [e for e in log.all() if e.kind == Kind.NIGHT_ACTION]
    assert len(kills) == 1, [e.seq for e in kills]
    assert (tmp_path / "t.jsonl").read_bytes() == after_first, "第二次提交往文件里加了字节"


async def test_a_repeated_submission_of_the_same_ballot_leaves_one_fact(tmp_path):
    """白天那张票也一样：`hard` 元组的两个成员各自要有证人。

    上一条钉的是 `Kind.NIGHT_ACTION` 这一半，这一条钉 `Kind.VOTE` 那一半。少一条的话，把元组
    写成只剩一个成员——看起来只是"少锁一种事件"——测试不会红，而票是这一局里最不可逆的那类事实。
    """
    log, agent, actor, after_first = await _submit_twice(
        tmp_path, phase=Phase.DAY_VOTE, kind=Kind.VOTE, make=_ballot)

    votes = [e for e in log.all() if e.kind == Kind.VOTE]
    assert len(votes) == 1, [e.payload.get("_idem") for e in votes]
    assert (tmp_path / "t.jsonl").read_bytes() == after_first, "重复的那张票落进了文件"


async def test_todays_ballot_does_not_swallow_tomorrows(tmp_path):
    """反方向：键里少了"哪一天"，今天的票就会把明天的票吞掉，而且吞的是不可逆的那一头。

    这条与上一条成对，是因为守卫只有一种失败方式会被写歪的键造成：往回吞（同一天重复提交）由
    上一条钉住，往前吞（跨天）由这一条钉住。少了这一条，把键算成 `phase:seat:as_of` 这种"看
    起来更安全"的写法可以一路绿灯通过全部测试，而代价是每一局第二天起没人投得出票。
    """
    log, agent, actor, after_first = await _submit_twice(
        tmp_path, phase=Phase.DAY_VOTE, kind=Kind.VOTE, make=_ballot, next_day=True)

    votes = [e for e in log.all() if e.kind == Kind.VOTE]
    assert len(votes) == 2, [e.payload.get("_idem") for e in votes]
    assert len(actor.ctxs) == 2
    assert {e.day for e in votes} == {1, 2}, votes
    assert (tmp_path / "t.jsonl").read_bytes() != after_first, "第二天的那张票没落盘"


async def test_the_same_words_twice_in_one_day_are_two_facts(tmp_path):
    """发言不该上这把锁：`hard` 那个成员判定一旦放宽，同一座位同一天的第二句会被第一句顶掉。

    观众看到的是"这人一天只说了一句"，而 `speech_acts`、`passivity_rate`、折叠窗口全都会照着
    一句去算——一个写侧的守卫改掉了读侧的整个行为分布，这才是它最贵的地方。
    """
    cfg, state, log, agent, wolf, _ = _table(tmp_path)
    state.phase = Phase.DAY_SPEECH
    legal = dataclasses.replace(rules.legal_actions(state, wolf), assigned_act="accuse")
    said = lambda legal: _said("accuse", "5号那句太顺了，我要他解释。", sorted(legal.targets)[0])
    actor = Scripted(lambda n: said(legal))
    for _ in range(2):
        await agent.take_turn(seat=wolf, actor=actor, legal=legal, phase=Phase.DAY_SPEECH,
                              kind=Kind.SPEECH, visibility=PUBLIC)

    speeches = [e for e in log.all() if e.kind == Kind.SPEECH and e.actor == wolf]
    assert len(speeches) == 2, [e.payload.get("_idem", "<无键>") for e in speeches]
    assert not any("_idem" in e.payload for e in speeches), "软事件被挂上了键，下一句就没了"
