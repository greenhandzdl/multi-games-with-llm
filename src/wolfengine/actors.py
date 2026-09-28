"""Who is sitting in a seat. The one abstraction the project must not get wrong twice.

Phase three of the plan reserves this seam deliberately: adding a human at the table later
means rewriting the orchestration layer if `phases.py` assumes "a call costs at most 45
seconds" and "every seat answers when asked". It costs about nothing to allow for now.

Three assumptions that must NOT leak into the phases:
  1. timeouts belong to the actor, not the phase (`None` = wait forever, which is what a
     thinking human needs and what would get an LLM seat killed);
  2. concurrency is chosen from the set of seats that *can* answer now, not from the head
     count at the table, or a human holding the floor blocks everyone else's semaphore;
  3. the wall-clock budget only applies to a game of nothing but LLM seats.

Assembly lives in `agent.py`, not here. That is what lets `test_info_isolation.py` assert
on rendered prompt bytes for *mock* seats: the information boundary is a property of the
prompt builder, so it has to be exercised by the same code path whether or not a model is
attached. A human player receives the same `Percept` object for the same reason, so the
canary test covers the human UI without knowing anything about it.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from . import human
from .assemble import Prompt
from .belief import BeliefState
from .config import ActorKind, Config
from .events import Kind
from .info import Percept, eid
from .llm import LLM
from .persona import PersonaParams
from .schema import Action, Belief, Suspect, parse_action
from .state import LegalSet, Phase

# The two strings this seat says out loud, as constants because `human.py`'s card is checked
# word by word against the legal act set (`test_human_seat.py`), and a re-ask that quietly
# grew a second offered verb would be a card the gate refuses.
PROMPT = "你的选择 > "
NOT_UNDERSTOOD = "没读懂这一行。用上面列出的那个词开头，后面跟座位号，再跟你要说的话。"


@dataclass
class TurnContext:
    """Everything one seat needs to produce one action, already assembled.

    `belief` is here rather than left to the actor to recompute because it is *not* the
    actor's business: the engine's reading of this seat's evidence is an input to the turn,
    the same as the prompt. A human seat needs it too, which is the second reason — the card
    a player reads before speaking is the same object the model gets.
    """

    seat: int
    role: str
    phase: Phase
    percept: Percept
    legal: LegalSet
    persona: PersonaParams
    prompt: Prompt
    belief: BeliefState
    attempt: int = 1
    # The complaints that caused this re-ask, as codes. `retry_note` is the same information
    # rendered for region C5 — a model's prompt text, ending in "只输出一个 JSON 对象" — and a
    # person would be told to emit JSON. So the codes travel, and each audience renders its own.
    refusal: tuple[str, ...] = ()


@dataclass
class Proposal:
    """An actor's answer, plus enough provenance to write the event honestly.

    `raw` is empty for actors that never speak in text (mock, human). `rung` is -1 for
    them, which is what separates "this seat produced nothing parseable" from "this seat
    was never asked to produce JSON" in the M3a distribution.
    """

    action: Action | None = None
    raw: str = ""
    rung: int = -1
    deviations: tuple[str, ...] = ()
    parse_errors: list[dict[str, Any]] = field(default_factory=list)
    response: dict[str, Any] = field(default_factory=dict)
    # what the actor actually sent, for `Event.request`. Only the actor knows this:
    # `agent.py` assembles bytes, `LlmActor` chooses the generation params.
    request_meta: dict[str, Any] = field(default_factory=dict)
    failure: str = ""
    # The two ways a turn ends that are *not* the model's fault, kept as flags rather than
    # as strings in `failure` because each has a different remedy (skip the seat / pull the
    # shrink lever) and a substring test is how those remedies quietly stop firing.
    timed_out: bool = False
    context_too_long: bool = False


@runtime_checkable
class Actor(Protocol):
    """`blocking` and `timeout_for` are the whole reason this protocol exists.

    A seat that may be timed out and skipped is a different kind of thing from a seat that
    must be waited for, and the difference has to be visible to `phases.py` without it
    having to know whether a model or a person is answering.
    """

    kind: ActorKind
    blocking: bool

    def timeout_for(self, phase: Phase) -> float | None: ...

    async def act(self, ctx: TurnContext) -> Proposal: ...


class LlmActor:
    """A seat played by the model. `blocking=False`: it may be skipped if it times out,
    and the phase can carry on with the other seats."""

    kind: ActorKind = "llm"
    blocking = False

    def __init__(self, llm: LLM, cfg: Config, seat: int) -> None:
        self.llm = llm
        self.cfg = cfg
        self.seat = seat

    def timeout_for(self, phase: Phase) -> float:
        """max(floor, 3 x this seat's median for this phase). Never a constant: the same
        prompt measured 1.2s and 17.6s on this endpoint depending on concurrent load."""
        m = self.llm.latency.median_prefix(f"{self.seat}:{phase.value}")
        return self.cfg.llm_timeout_floor_s if m is None else max(
            self.cfg.llm_timeout_floor_s, m * self.cfg.llm_timeout_median_mult)

    async def act(self, ctx: TurnContext) -> Proposal:
        phase_key = f"{ctx.seat}:{ctx.phase.value}"
        budget = self.cfg.token_budget_for(ctx.phase)
        temperature = self.cfg.temperature_for(ctx.seat)
        meta = {"model": self.cfg.model, "max_tokens": budget, "temperature": temperature}
        call = await self.llm.complete(ctx.prompt.messages, max_tokens=budget,
                                       temperature=temperature, phase_key=phase_key)
        # No latency bookkeeping here: `LLM.complete` already samples the transport's own
        # measurement, and sampling it twice would double the weight of every call.
        if not call.ok:
            return Proposal(failure=call.error, response=call.as_response_dict(),
                            request_meta=meta)
        parsed = parse_action(call.text)
        return Proposal(
            action=parsed.action, raw=call.text, rung=parsed.rung,
            deviations=parsed.deviations, parse_errors=parsed.errors,
            response=call.as_response_dict(), request_meta=meta,
        )


class MockActor:
    """A seat played without a model. Same contract, same gate, no network.

    Two different jobs, and they need different answers, which is why `script` and
    `synthesize` are separate switches rather than one "mock mode":

    * a **test** wants one specific utterance (canary strings, golden transcripts). That is
      `script`, and running out of it returns *silence*, so a scripted assertion cannot pass
      on a guess the policy happened to make.
    * **`--mock`** wants a whole game nobody authored. That is `synthesize`.

    Without the second, a mock game is nine engine fallbacks per round: the timeline proves
    the state machine does not crash, and nothing else. It is still not data — the manifest
    records `actor_kinds`, so a stand-in table can never be mistaken for a measured one.
    """

    kind: ActorKind = "mock"
    blocking = False

    def __init__(self, seat: int, script: list[Action] | None = None, *,
                 synthesize: bool = False, rng: random.Random | None = None) -> None:
        self.seat = seat
        self.script = list(script or [])
        self.synthesize = synthesize
        self.rng = rng if rng is not None else random.Random(seat)
        self.i = 0
        self.turns: list[TurnContext] = []

    def timeout_for(self, phase: Phase) -> None:
        return None

    async def act(self, ctx: TurnContext) -> Proposal:
        self.turns.append(ctx)
        if self.i < len(self.script):
            action = self.script[self.i]
            self.i += 1
            return Proposal(action=action, raw=action.model_dump_json())
        if self.synthesize:
            action = self._synthesize(ctx)
            return Proposal(action=action, raw=action.model_dump_json())
        # Silence, not a guess: the caller's fallback picks a legal default. Returning
        # a random action here would make a scripted test pass for the wrong reason.
        return Proposal(failure="mock_script_exhausted")

    # ------------------------------------------------------------- stand-in policy
    def _synthesize(self, ctx: TurnContext) -> Action:
        legal, belief, p = ctx.legal, ctx.belief, ctx.persona
        cands = sorted(legal.targets)
        top = [s for s in belief.top_suspects(k=len(cands) or 3) if s in cands]
        # Suspecting *myself* is never the read a mock should make: the engine's ranking has
        # no reason to exclude its own observer, and a seat that accuses itself on day one
        # would look like an engine bug rather than a policy.
        pick = next((s for s in top if s != self.seat), None)
        if pick is None and cands:
            pool = [c for c in cands if c != self.seat] or cands
            pick = self.rng.choice(pool)

        if "kill" in legal.acts:
            return Action(act="kill", target=pick, evidence=self._cite(ctx, pick))
        if "check" in legal.acts:
            return Action(act="check", target=pick)
        if "save" in legal.acts or "poison" in legal.acts:
            return self._witch(ctx, pick)
        if "vote" in legal.acts:
            # Aggressive seats rarely waste a ballot; a table that all abstained is the
            # stall (plan §12 R10), so the persona parameter is what decides it here.
            if pick is not None and self.rng.random() > 0.25 + 0.35 * (1 - p.aggression):
                return Action(act="vote", target=pick, evidence=self._cite(ctx, pick))
            return Action(act="pass")
        if "shoot" in legal.acts:
            return Action(act="shoot", target=pick) if pick is not None else Action(act="pass")
        if "discuss" in legal.acts:
            return Action(act="discuss", target=pick,
                          speech=self._line("discuss", pick))
        if "last_words" in legal.acts:
            if not legal.allow_pass:
                return Action(act="last_words", speech=self._line("last_words", pick))
            return Action(act="pass") if self.rng.random() < 0.15 else \
                Action(act="last_words", speech=self._line("last_words", pick))

        act = legal.assigned_act if legal.assigned_act in legal.acts else (legal.acts[0] if legal.acts else "pass")
        if act == "pass":
            return Action(act="pass")
        needs_target = act not in ("defend", "listen")
        tgt = pick if needs_target else None
        # `forced_nominate` is the escalation persona.py applies to repeat abstainers; a
        # stand-in that ignored it would leave that branch of the design untested.
        if legal.reason_if_empty == "forced_nominate" and tgt is None and pick is not None:
            act, tgt = "accuse", pick
        return Action(act=act, target=tgt, speech=self._line(act, tgt),
                      evidence=self._cite(ctx, tgt),
                      belief=self._stated(ctx, pick))

    def _witch(self, ctx: TurnContext, pick: int | None) -> Action:
        """首夜必救, then a low-rate poison. Two rules, not a policy search: the point of
        the mock night is that deaths *happen* and one potion gets spent, so the game can be
        replayed and the state machine exercised."""
        notice = next((e for e in reversed(ctx.percept.events) if e.kind == Kind.NOTICE), None)
        knifed = notice.payload.get("about") if notice else None
        if knifed is not None and "save" in ctx.legal.acts and "save" in ctx.legal.consumables:
            if knifed != self.seat and notice is not None and notice.day == 1:
                return Action(act="save", potion="save")
        if "poison" in ctx.legal.acts and "poison" in ctx.legal.consumables \
                and pick is not None and self.rng.random() < 0.3:
            return Action(act="poison", target=pick, potion="poison")
        return Action(act="pass")

    def _line(self, act: str, target: int | None) -> str:
        """Pick a sentence that *fits* the action it renders.

        Blanking `{t}` when there is no target produced `记住号今天是怎么赢的。` on real
        output — readable enough to slip past a glance, wrong enough to look like the
        engine mangles text. Choosing from the grammatically applicable subset instead is
        one more line of code and cannot produce a mangled sentence.
        """
        opts = LINES.get(act, LINES["listen"])
        named = tuple(o for o in opts if "{t}" in o)
        blank = tuple(o for o in opts if "{t}" not in o)
        if target is None:
            pool = blank or LINES["listen"]
            return self.rng.choice(pool)
        return self.rng.choice(named or opts).format(t=target)

    def _cite(self, ctx: TurnContext, target: int | None) -> list[str]:
        """Real ids only, and only ones that *support* the claim: events where someone else
        pointed at this seat. An empty list is legal (uncited is a statistic, not a
        violation), and inventing an id would trip the one hard rule speech still has."""
        if target is None:
            return []
        hits = [eid(e.seq) for e in ctx.percept.events
                if e.kind in (Kind.SPEECH, Kind.VOTE) and e.actor != self.seat
                and e.payload.get("target") == target]
        return hits[-2:]

    def _stated(self, ctx: TurnContext, pick: int | None) -> Belief | None:
        """Stated belief deliberately *not* a copy of the engine ranking (plan §12 R12): if
        the stand-in parroted the card, `stated_vs_engine_agreement` would read 1.0 on mock
        games and the metric's own sanity check would be the thing that lied."""
        others = [s for s in sorted(ctx.legal.targets) if s != pick][:2]
        order = [pick] + others if pick is not None else others
        if not order:
            return None
        return Belief(suspects=[Suspect(seat=s, why="站不住" if i == 0 else "先听")
                                for i, s in enumerate(order)])


LINES: dict[str, tuple[str, ...]] = {
    "accuse": ("{t}号那段话前后对不上，我先记一笔。",
               "票先给{t}号，他昨晚的沉默比发言说明问题。",
               "{t}号别绕了，你到底在替谁说话？"),
    "defend": ("我是好人，刚才咬我的那位才该被抬走。",
               "我这一票谁都不跟，先把自己的逻辑说完。"),
    "align": ("我跟前面那位，{t}号确实可疑。",
              "{t}号的判断我认可，不重复了。"),
    "probe": ("{t}号，你昨晚为什么第一个开口？",
              "我想听{t}号把自己那套再讲一遍。"),
    "pivot": ("本来我怀疑的是别人，现在改看{t}号了。",
              "{t}号一句话把我提醒了，我换方向。"),
    "listen": ("先听听还有谁没说话。",
               "我这轮不下结论，看票型。"),
    "discuss": ("刀{t}号，他发言太像神牌。", "{t}号留着夜里处理。"),
    "last_words": ("我是被冤枉的，票型自己会说话。", "记住{t}号今天是怎么赢的。"),
}


class HumanActor:
    """A seat a person is sitting in (plan §15). `blocking=True`, `timeout_for()` returns None.

    The three rules in this module's header are what the rest of the engine owes this seat, and
    they are asserted in `test_actor_contract.py`. What the seat owes back is one line of typing
    per turn: `human.py` reads it, this method turns it into a `Proposal`, and `agent.py` runs
    the same gate over it that it runs over a model's JSON. No legality lives here — a
    hand-written ballot that skipped the gate would make the transcript a record of whoever
    typed fastest rather than of what the rules allowed.

    `console` is the screen, injectable so a test can show itself a queue instead of a terminal.
    It has no default *value* on purpose: `Console()` binds `input()`, and constructing one at
    import time would leave a module import waiting on whoever ran it.
    """

    kind: ActorKind = "human"
    blocking = True

    def __init__(self, seat: int, *, console: "human.Console | None" = None) -> None:
        self.seat = seat
        self.console = console if console is not None else human.Console()

    def timeout_for(self, phase: Phase) -> None:
        """None means wait forever. A person thinking out loud is not a timeout case."""
        return None

    async def act(self, ctx: TurnContext) -> Proposal:
        card = human.decision_card(ctx)
        while True:
            self.console.show(card)
            # `to_thread`, not `await console.read(...)`: a blocking `input()` in this
            # coroutine holds the *event loop*, and the other eight seats are in it. Whether
            # this seat is waited on or waited for is what §15 rule 2 is about, and the read
            # is the first place that rule can actually be broken.
            line = await asyncio.to_thread(self.console.read, PROMPT)
            if line is None:
                # EOF is "this person left", and the engine must say so in the log rather than
                # answer for him silently. `failure` reaches `attempts[]`; `fallback=1` is what
                # the abstention below is recorded as.
                return Proposal(failure="human_input_closed")
            action = human.parse_human_line(line)
            if action is not None:
                return Proposal(action=action)
            # Re-asked inside this seat: a typo is neither a repair retry (that budget is the
            # model's) nor a rejected output (nothing was asked, so nothing is a preference pair).
            self.console.show(NOT_UNDERSTOOD)
