"""Phase orchestration: who is asked, in what order, and how much of it runs at once.

Nothing in here may know what an LLM is. `Table.actors` maps seat → `Actor`, and the guard
in `test_purity.py` fails this module if it imports `llm` or `transport` — that is what keeps
a human seat, a mock seat and a model seat addressable by the same three lines, and what
makes `--mock` a real alternative rather than a demo mode.

Three ordering facts are load-bearing rather than stylistic:

* **Speech is serial, ballots are not.** A speaker must hear what was already said (the
  information cascade is the game), while a vote cast before your turn is a queue. Ballots
  are collected in memory and written when the tally opens, and every voter is asked with
  `as_of` pinned to the moment the phase opened. Either half alone would leak.
* **The night is serial, and for a rule reason.** The witch is told who fell tonight, so her
  turn cannot precede the knife. The plan's concurrency table counts three parallel night
  calls; that would let her act blind, so the night follows `Board.night_order` and the
  saving is about two seconds.
* **Deaths are settled by `rules.resolve_night`, never by a phase.** A phase collects
  intentions; only the rules engine turns them into corpses.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from . import rules
from .actors import Actor
from .agent import Agent, TurnOutcome
from .belief import build_belief
from .compress import CAUSE_ZH
from .config import Config
from .events import EventLog, Kind, PUBLIC, Visibility, seats
from .persona import assign_speech_acts, repeat_fragments
from .schema import Action
from .state import Death, GameState, LegalSet, Phase


@dataclass
class Table:
    """One game's worth of things a phase can do.

    A value rather than a `Game` method set, so a fixture can build a table by hand and
    assert on one phase in isolation — that is how `run_night` gets tested with two scripted
    seats and no model anywhere in the process.
    """

    cfg: Config
    state: GameState
    log: EventLog
    agent: Agent
    actors: dict[int, Actor]
    rng: random.Random
    blocked: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ primitives
    def say(self, kind: str, *, visibility: Visibility = PUBLIC, actor: int | None = None,
            phase: Phase | None = None, **payload: Any) -> int:
        """Write a fact the engine itself authored: no prompt, no gate, no retry."""
        return self.log.append(
            kind, day=self.state.day, phase=(phase or self.state.phase).value,
            visibility=visibility, actor=actor, result={"ok": True, "source": "engine"},
            **payload).seq

    def alive(self, *teams: str) -> list[int]:
        out = self.state.alive_seats
        return [s for s in out if not teams or self.state.team_of(s) in teams]

    def wave_size(self, can_answer: Iterable[int]) -> int:
        """Static prefix table (plan §6), sized by the seats that *can* answer now.

        Not adaptive: changing k mid-phase would give two seats in the same phase different
        prompt-length distributions, which is exactly the comparability the evaluation layer
        protects. `blocking` seats are excluded from the count because they are waited on one
        at a time and are not worker slots.
        """
        n = sum(1 for s in can_answer if not self.actors[s].blocking)
        if n <= 1:
            return max(n, 1)
        prefix = self.agent.last_prefix_tokens
        k = 9 if prefix < 2000 else 6 if prefix < 3500 else 4 if prefix < 6000 else 2
        return max(1, min(n, k))

    async def ask(self, seat: int, *, legal: LegalSet | None = None, kind: str,
                  visibility: Visibility = PUBLIC, phase: Phase | None = None,
                  extra: dict[str, Any] | None = None, as_of: int | None = None,
                  repeat: tuple[str, ...] = ()) -> TurnOutcome:
        return await self.agent.take_turn(
            seat=seat, actor=self.actors[seat],
            legal=legal if legal is not None else rules.legal_actions(self.state, seat),
            phase=phase or self.state.phase, kind=kind, visibility=visibility,
            extra_payload=extra, repeat_fragments=repeat, as_of=as_of)


# --------------------------------------------------------------------------- night
async def run_night(t: Table) -> rules.NightResolution:
    """One night: wolves agree and knife, the witch is told and acts, the seer checks.

    Returns the resolution so the caller can decide about last words; the deaths themselves
    are written here, because "who died" is a fact of the log and not a summary of it.
    """
    t.state.phase = Phase.NIGHT_WOLF
    t.say(Kind.PHASE, text="天黑请闭眼。狼人请睁眼。")

    wolf_kill = await _wolves(t)
    witch_save, witch_poison = await _witch(t, wolf_kill)
    seer_check = await _seer(t)

    plan = rules.NightPlan(wolf_kill=wolf_kill, witch_save=witch_save,
                           witch_poison=witch_poison, seer_check=seer_check)
    res = rules.resolve_night(t.state, plan)
    t.blocked.extend(res.blocked)
    rules.apply_night(t.state, res)

    for d in res.deaths:
        t.say(Kind.DEATH, seat=d.seat, cause=d.cause, cause_zh=CAUSE_ZH.get(d.cause, d.cause))
    if res.seer_report is not None and t.state.seer_seat is not None:
        target, verdict = res.seer_report
        t.say(Kind.SEER_RESULT, visibility=seats(t.state.seer_seat), actor=t.state.seer_seat,
              target=target, verdict=verdict)
    return res


async def _wolves(t: Table) -> int | None:
    """One wolf proposes to the channel; a *different* wolf calls the knife.

    Two calls per night rather than one per wolf: with three wolves each deciding alone the
    knife splits three ways, good players survive on noise, and 狼队胜率 stops measuring
    anything (plan §12 R11). The proposer rotates by day so a team's style can be attributed
    to its seats across a batch.
    """
    wolves = t.alive("wolf")
    if not wolves:
        return None
    channel = seats(*wolves)
    proposer = wolves[(t.state.day - 1) % len(wolves)]
    killer = wolves[t.state.day % len(wolves)]
    if killer != proposer:
        discuss = LegalSet(acts=("discuss",),
                           targets=frozenset(s for s in t.state.alive_seats if s != proposer))
        await t.ask(proposer, legal=discuss, phase=Phase.NIGHT_WOLF, kind=Kind.WOLF_CHAT,
                    visibility=channel)
    out = await t.ask(killer, phase=Phase.NIGHT_WOLF, kind=Kind.NIGHT_ACTION,
                      visibility=channel, extra={"action": "kill"})
    return out.action.target if out.action.act == "kill" else None


async def _witch(t: Table, wolf_kill: int | None) -> tuple[bool, int | None]:
    witch = t.state.witch_seat
    if witch is None or not t.state.is_alive(witch) or t.state.witch.both_used:
        return False, None
    t.state.phase = Phase.NIGHT_WITCH
    # `notice` exists because the vocabulary had no way to say "the moderator spoke to one
    # seat". Without it the only options are publishing the death early (which tells every
    # seat the wolves' target) or letting the witch act blind.
    t.say(Kind.NOTICE, visibility=seats(witch),
          text="今晚无人倒下。" if wolf_kill is None else f"今晚{wolf_kill}号倒在了狼刀下。",
          about=wolf_kill)
    out = await t.ask(witch, phase=Phase.NIGHT_WITCH, kind=Kind.NIGHT_ACTION,
                      visibility=seats(witch), extra={"action": "save_or_poison"})
    # She saves *the person the knife found*, so `save` needs no target of its own; only the
    # poison carries one. Reading `target` off a save would let a hallucinated seat number
    # decide who lives.
    return (out.action.act == "save"), (out.action.target if out.action.act == "poison" else None)


async def _seer(t: Table) -> int | None:
    seer = t.state.seer_seat
    if seer is None or not t.state.is_alive(seer):
        return None
    t.state.phase = Phase.NIGHT_SEER
    out = await t.ask(seer, phase=Phase.NIGHT_SEER, kind=Kind.NIGHT_ACTION,
                      visibility=seats(seer), extra={"action": "check"})
    return out.action.target if out.action.act == "check" else None


# ------------------------------------------------------------------------- daytime
async def run_day(t: Table) -> None:
    """Dawn announcement, last words for the night's dead, speeches, a vote, hunter shots."""
    t.state.phase = Phase.DAY_SPEECH
    t.say(Kind.PHASE, text=f"天亮了，第{t.state.day}天开始。")
    await run_last_words(t, [d.seat for d in t.state.deaths
                             if d.night == t.state.day and d.cause != "exiled"])
    if rules.check_win(t.state) is not None:
        return
    await run_speeches(t, speakers=[s for s in t.state.speech_order if t.state.is_alive(s)])
    if rules.check_win(t.state) is None:
        await run_vote(t)
    await run_hunter_shots(t)


async def run_speeches(t: Table, *, speakers: list[int], pk: bool = False) -> None:
    phase = Phase.DAY_PK_SPEECH if pk else Phase.DAY_SPEECH
    t.state.phase = phase
    if not speakers:
        return
    beliefs = {s: build_belief(s, t.log.for_seat(s)) for s in speakers}
    legal = {s: rules.legal_actions(t.state, s) for s in speakers}
    assigned = assign_speech_acts(t.state, speakers, t.agent.personas, beliefs, t.rng, t.cfg, legal)
    said: list[str] = []
    for seat in speakers:
        act, target = assigned[seat]
        ls = replace(legal[seat], assigned_act=act, assigned_target=target)
        if t.state.abstain_streak.get(seat, 0) >= 2:
            # The forced-nomination pressure rides on the LegalSet rather than on the
            # persona, because personas are sampled once per game and mutating a shared
            # object mid-round is how a "temporary" flag ends up permanent.
            ls = replace(ls, reason_if_empty="forced_nominate")
        out = await t.ask(seat, legal=ls, phase=phase, kind=Kind.SPEECH,
                          repeat=repeat_fragments(said))
        if out.action.speech:
            said.append(out.action.speech)


async def run_vote(t: Table, *, pk_seats: tuple[int, ...] = ()) -> int | None:
    """One round of sealed ballots, then the tally, then possibly a PK round."""
    t.state.phase = Phase.DAY_VOTE
    voters = t.alive()
    if not voters:
        return None
    opened_at = t.log.last_seq
    ballots = await _ballots(t, voters, pk_seats, as_of=opened_at)
    for seat in voters:
        t.state.abstain_streak[seat] = t.state.abstain_streak.get(seat, 0) + 1 \
            if ballots[seat] is None else 0
    res = rules.tally_votes(ballots, t.state.alive_seats)
    pending_pk = rules.will_pk(t.state, res)
    _publish(t, res, pending_pk=pending_pk)

    if pending_pk:
        t.state.pk_seats = res.pk_seats
        t.say(Kind.PHASE, text=f"平票，{'、'.join(str(s) for s in res.pk_seats)}号进入PK。")
        await run_speeches(t, speakers=[s for s in res.pk_seats if t.state.is_alive(s)], pk=True)
        t.state.pk_seats = ()
        second_ballots = await _ballots(t, t.alive(), res.pk_seats, as_of=t.log.last_seq)
        second = rules.tally_votes(second_ballots, t.state.alive_seats)
        _publish(t, second)
        return await _exile(t, rules.resolve_tie(t.state, second))
    return await _exile(t, res.out)


async def _ballots(t: Table, voters: list[int], pk_seats: tuple[int, ...],
                   *, as_of: int) -> dict[int, int | None]:
    """The parallel wave. Returns voter → target (None = 弃票), and writes nothing.

    Nothing is appended here on purpose: the first seat to finish must not be able to
    influence the last, and in the log that means the ballots land *after* every one of them
    exists in memory.

    The phase is set here rather than trusted from the caller. `legal_actions` reads
    `state.phase`, and the PK path reaches this function straight out of a speech wave that
    left the phase as `DAY_SPEECH` — so the revote was built from a *speech* legal set, the
    seats answered with `act="accuse"`, the gate let them (accusing is legal in a speech),
    and this function mapped every such answer to `None`. The log then said 全员弃票 three
    lines below three ballots that had targets on them.
    """
    t.state.phase = Phase.DAY_VOTE
    sem = asyncio.Semaphore(t.wave_size(voters))

    async def cast(seat: int) -> tuple[int, Action]:
        legal = rules.legal_actions(t.state, seat)
        if pk_seats:
            legal = replace(legal, targets=frozenset(pk_seats) - {seat})
        async with sem:
            out = await t.ask(seat, legal=legal, phase=Phase.DAY_VOTE, kind=Kind.VOTE,
                              as_of=as_of)
        return seat, out.action

    results = await asyncio.gather(*(cast(s) for s in voters))
    return {seat: (action.target if action.act == "vote" else None)
            for seat, action in results}


def _publish(t: Table, res: rules.VoteResult, *, pending_pk: bool = False) -> None:
    """The tally, written once every ballot exists.

    The ballots themselves are already in the log — `agent.py` wrote each one as its turn
    closed, because that is the only record that carries the prompt, the rejected attempts
    and the provenance. Sealing therefore does not come from write order here; it comes from
    `as_of`, which cut every voter's view at the moment the phase opened. Writing a second
    copy of each ballot would not add privacy, it would add a lie: two VOTE events per seat,
    one of them with no attempt history.

    `pending_pk` is why this takes a flag instead of re-deriving it from `res`: a tie that is
    about to be re-voted has no outcome yet, and `res` alone cannot tell that apart from the
    terminal one. Only the day's first ballot passes it — see `rules.will_pk`.
    """
    t.say(Kind.VOTE_RESULT, summary=_tally_text(res, pending_pk=pending_pk),
          tally={str(k): v for k, v in res.tally.items()}, exiled=res.out,
          abstainers=list(res.abstainers))


def _tally_text(res: rules.VoteResult, *, pending_pk: bool = False) -> str:
    if not res.tally:
        return "全员弃票，无人出局。"
    bits = "、".join(f"{s}号{n}票" for s, n in sorted(res.tally.items()))
    if res.out is not None:
        who = f"{res.out}号被投票出局。"
    elif pending_pk:
        who = "票数并列，先不定人。"
    else:
        who = "平票，无人出局。"
    return f"票型：{bits}。弃票{len(res.abstainers)}人。{who}"


async def _exile(t: Table, seat: int | None) -> int | None:
    if seat is None:
        return None
    t.state.seats[seat].alive = False
    t.state.deaths.append(Death(seat=seat, day=t.state.day, night=0, cause="exiled"))
    t.say(Kind.DEATH, seat=seat, cause="exiled", cause_zh=CAUSE_ZH["exiled"])
    if "shoot_on_death" in t.state.board.spec(t.state.role_of(seat)).abilities \
            and "exiled" in t.state.house.hunter_shoots_on:
        t.state.pending_shots.append(seat)
    rules.check_win(t.state)
    await run_last_words(t, [seat])
    return seat


async def run_last_words(t: Table, seats_: list[int]) -> None:
    """Spoken-ness is derived from the log, not from a flag on `Death`: `Death` is frozen,
    so a flag would have raised, and a set beside the log is a second truth to keep in step."""
    already = {e.actor for e in t.log.all() if e.kind == Kind.LAST_WORDS}
    for seat in seats_:
        if seat in already:
            continue
        death = next((d for d in t.state.deaths if d.seat == seat), None)
        if death is None or not rules.needs_last_words(t.state, death):
            continue
        t.state.phase = Phase.LAST_WORDS
        await t.ask(seat, legal=LegalSet(acts=("last_words",), allow_pass=True),
                    phase=Phase.LAST_WORDS, kind=Kind.LAST_WORDS, visibility=PUBLIC)


async def run_hunter_shots(t: Table) -> None:
    while t.state.pending_shots:
        seat = t.state.pending_shots[0]
        t.state.phase = Phase.HUNTER_SHOT
        # Computed while the seat is still on the list: `rules.legal_actions` only grants
        # `shoot` to the seat at `pending_shots[0]`, so popping first would hand the hunter
        # an empty set, the hard gate would refuse everything, and the engine would quietly
        # take the shot on his behalf via the fallback — with the log saying he chose it.
        legal = rules.legal_actions(t.state, seat)
        t.state.pending_shots.pop(0)
        out = await t.ask(seat, legal=legal, phase=Phase.HUNTER_SHOT, kind=Kind.NIGHT_ACTION,
                          visibility=seats(seat), extra={"action": "shoot"})
        target = out.action.target if out.action.act == "shoot" else None
        if target is not None and t.state.is_alive(target):
            t.state.seats[target].alive = False
            t.state.deaths.append(Death(seat=target, day=t.state.day, night=0,
                                        cause="hunter_shot"))
            t.say(Kind.DEATH, seat=target, cause="hunter_shot",
                  cause_zh=CAUSE_ZH["hunter_shot"])
            rules.check_win(t.state)
