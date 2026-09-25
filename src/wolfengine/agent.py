"""One seat, one decision, start to finish.

The pipeline is in one function on purpose: assemble → ask → parse → gate → retry →
fallback → write the event. Splitting it across layers is how the retry path ends up
forgetting to log its rejected raw output, and those rejected outputs are the preference
pairs the whole dataset is worth having.

Three invariants worth stating because nothing else in the codebase enforces them:

* The gate runs on **every** actor kind, including a future human seat. Legality is a
  property of the game, not of the model, so a hand-written action must be checked by the
  same code that checks a generated one.
* `attempts[]` holds *rejected* outputs only. The accepted one lives in `response`, and
  duplicating it into `attempts` would double the file for every turn and make the
  preference-pair export need a dedup pass — which is to say, eventually, wrong.
* Nothing is written to the log until it is either legal or explicitly a fallback, and the
  fallback is marked as such in the same record. A seat whose vote was refused must not
  appear to have voted; a seat the engine voted for must say so. One fallback keeps the
  model's own action rather than taking the engine's: when the only thing refused was a
  speech-act label, replacing the sentence with the assigned act would report the
  assignment table as behaviour. It still says `fallback=1`.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .actors import Actor, Proposal, TurnContext
from .assemble import Prompt, assemble, payload_for_log
from .belief import BeliefState, build_belief
from .compress import chronicle, fold_body
from .config import Config
from .events import EventLog, Kind, Visibility
from .info import Percept, percept_for
from .legality import Verdict, check_action, default_action
from .llm import ContextTooLong, EndpointUnavailable
from .persona import PersonaParams
from .schema import Action, errors_to_prompt_lines
from .state import GameState, LegalSet, Phase

# How far the overflow lever is pulled before a turn is skipped. Each pull halves B2's
# verbatim window, which is the plan's sacrifice order step ①; beyond two the window is at
# its floor and the only thing left to cut is region A, and cutting A voids the cache for
# every seat in the game — a per-turn decision must never make that trade.
MAX_SHRINK = 2


@dataclass
class TurnOutcome:
    """What happened, as the metrics layer will read it."""

    action: Action
    percept: Percept
    prompt: Prompt
    belief: BeliefState
    verdict: Verdict
    seq: int = 0
    # the proposal the engine acted on: the accepted one, or the last one before fallback
    proposal: Proposal | None = None
    attempts: tuple[dict[str, Any], ...] = ()  # rejected outputs, with the reason each was rejected
    fell_back: bool = False
    timed_out: bool = False
    context_overflow: bool = False
    failure: str = ""

    @property
    def prompt_tokens_est(self) -> int:
        return self.prompt.total_tokens


@dataclass(frozen=True)
class _View:
    """Everything about this seat's turn that does not change between attempts."""

    seat: int
    role: str
    persona: PersonaParams
    percept: Percept
    belief: BeliefState
    legal: LegalSet
    phase: Phase
    repeat_fragments: tuple[str, ...] = ()


class Agent:
    """Holds what is stable across a game (config, state, log) and runs one turn."""
    def __init__(
        self,
        *,
        cfg: Config,
        state: GameState,
        log: EventLog,
        personas: dict[int, PersonaParams],
        known_ids: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.log = log
        self.personas = personas
        self._known_ids = known_ids
        self.fallbacks = 0
        self.retries = 0
        self.timeouts = 0
        self.context_overflows = 0
        self.shrinks = 0
        self.calls = 0
        # Cold-start value for the concurrency table: assume a full steady-state prefix
        # until a real assembly says otherwise, so the first wave of a game is the
        # conservative size rather than k=9 against a prompt nobody has measured yet.
        self.last_prefix_tokens = cfg.regions.a_target + cfg.regions.b_steady

    @property
    def known_ids(self) -> frozenset[str]:
        """Every citation id that has ever existed in this game.

        The gate needs this to tell "the model invented evidence" from "the model quoted a
        real event it may not see". A seat's own percept cannot answer that — by design —
        so the distinction only exists if the caller supplies the whole log. Without it the
        M4 hallucination rate silently becomes an isolation-bug rate.
        """
        return frozenset(self._known_ids()) if self._known_ids else frozenset()

    async def take_turn(
        self,
        *,
        seat: int,
        actor: Actor,
        legal: LegalSet,
        phase: Phase,
        kind: str,
        visibility: Visibility,
        extra_payload: dict[str, Any] | None = None,
        repeat_fragments: tuple[str, ...] = (),
        as_of: int | None = None,
    ) -> TurnOutcome:
        # Both derived views come from the *percept*, never from the log. Handing
        # build_belief the full log would fold every seat's private events into this one's
        # belief card, which is the information-isolation bug wearing the engine's own coat:
        # nothing in the prompt path would raise, because the numbers arrive already mixed.
        #
        # `as_of` is the other half of the same discipline. Votes are legal facts the whole
        # table eventually sees, so they must be public in the log — but a ballot cast by
        # seat 2 while seat 7 is still composing would turn a sealed vote into a queue. The
        # caller passes the seq the phase opened at, and this seat's world stops there.
        # Speech deliberately passes None: hearing what was already said is the point.
        percept = percept_for(seat, self.log.all(), at_seq=as_of)
        belief = build_belief(seat, percept.events)
        role = self.state.role_of(seat)
        persona = self.personas.get(seat) or PersonaParams()

        retry_note = ""
        shrink = 0
        # Two budgets, pulled apart on purpose. `max_repair_retries` counts re-asks after the
        # gate refused an answer; the shrink lever has to be pullable MAX_SHRINK times *after*
        # a 400 even when no repair retry is owed. Sharing one counter is how a turn lost to
        # context silently becomes an ordinary fallback: the loop runs out first, the lever
        # stops half-way, and `context_overflow` never gets written for the run that 400s.
        attempts: list[dict[str, Any]] = []
        action: Action | None = None
        accepted: Proposal | None = None
        last: Proposal = Proposal()
        timed_out = False
        overflow = False
        verdict = Verdict(ok=False, violations=["no_attempt"])
        view = _View(seat=seat, role=role, persona=persona, percept=percept, belief=belief,
                     legal=legal, phase=phase, repeat_fragments=repeat_fragments)

        attempt_no = 0
        sent: list[dict[str, str]] | None = None
        length_refused = False
        owed = False
        while True:
            attempt_no += 1
            # Reassembled every attempt, because the retry note is region C5 and a prompt
            # that never gets rebuilt would retry against the bytes that were already refused.
            prompt, shrink = self._fit(view, retry_note=retry_note, shrink=shrink)
            if prompt.over_ceiling:
                overflow = True
                break
            if prompt.messages == sent:
                # The rebuild produced the bytes that were just refused, so there is nothing
                # left to say with them — resending is the death loop §6 rules out, priced at
                # one round trip each time, and it bounds a stalled repair retry the same way
                # it bounds a stalled shrink. Only a length refusal counts as a lost turn
                # though: an identical *legality* complaint was never a context problem, and
                # marking it as one would put a retry into the number M3 is read from.
                overflow = length_refused
                break
            sent = prompt.messages
            # Counted here, at the send, rather than at the decision: a re-ask the guard above
            # decided not to make is not a retry that happened, and `retry=` has to stay
            # consistent with `calls=` in the same summary line.
            if owed:
                self.retries += 1

            proposal = await self._ask(actor, TurnContext(
                seat=seat, role=role, phase=phase, percept=percept, legal=legal,
                persona=persona, prompt=prompt, belief=belief, attempt=attempt_no))
            last = proposal
            self.calls += 1

            if proposal.context_too_long:
                # The local estimate thought this fit and the endpoint disagreed; the estimate
                # is the thing that was wrong, so cut and try again rather than lose the turn.
                length_refused = True
                if shrink >= MAX_SHRINK:
                    overflow = True
                    break
                shrink += 1
                self.shrinks += 1
                continue
            if proposal.timed_out:
                timed_out = True
                attempts.append(_attempt_record(proposal, ("timeout",)))
                break

            verdict = check_action(proposal.action, legal=legal, percept=percept, phase=phase,
                                   role=role, known_ids=self.known_ids)
            if verdict.ok and proposal.action is not None:
                action, accepted = proposal.action, proposal
                break

            attempts.append(_attempt_record(proposal, verdict.violations))
            # Only a repairable complaint is worth a second call: an empty or unparseable
            # answer will fail identically and burn the retry budget on the way down.
            retryable = proposal.action is not None or bool(proposal.parse_errors)
            if not retryable or attempt_no > self.cfg.max_repair_retries:
                break
            retry_note = errors_to_prompt_lines(
                [{"loc": (), "msg": m} for m in verdict.violations] + list(proposal.parse_errors),
                legal.acts, sorted(legal.targets))
            owed = True

        if overflow:
            # One per turn, not one per send: `metrics.py` counts the same quantity out of the
            # log, and the M3 criterion is "0 次 context 400" over games. If the two numbers
            # measure different things the run's own summary contradicts its own transcript.
            self.context_overflows += 1
        if action is None:
            if last.action is not None and _only_the_label_was_refused(verdict):
                # The seat said something real and got the label wrong. `default_action`
                # would replace its sentence with an empty one and keep the assigned act, so
                # the transcript would show a compliant turn and `passivity_rate` would score
                # the assignment table instead of the player — the exact failure the
                # never-zero weights in persona.py exist to avoid. Keep the words, keep the
                # act the model chose, and let `fallback=1` be the thing that reports it.
                action = last.action
            else:
                action = default_action(legal, belief.top_suspects(k=len(legal.targets) or 3))
            self.fallbacks += 1
            verdict = check_action(action, legal=legal, percept=percept, phase=phase,
                                   role=role, known_ids=self.known_ids)

        outcome = TurnOutcome(
            action=action, percept=percept, prompt=prompt, belief=belief, verdict=verdict,
            proposal=accepted if accepted is not None else last,
            attempts=tuple(attempts),
            fell_back=accepted is None,
            timed_out=timed_out,
            context_overflow=overflow,
            failure="" if accepted is not None else (last.failure or "no_legal_action"),
        )
        outcome.seq = self._write(kind=kind, seat=seat, phase=phase, visibility=visibility,
                                  action=action, outcome=outcome, as_of=as_of,
                                  extra=extra_payload or {})
        return outcome

    # ------------------------------------------------------------------ internals
    def _assemble(self, view: "_View", retry_note: str, shrink: int) -> Prompt:
        return assemble(
            cfg=self.cfg, percept=view.percept, seat_role=view.role, persona=view.persona,
            belief=view.belief, legal=view.legal, phase=view.phase,
            repeat_fragments=view.repeat_fragments, retry_note=retry_note, shrink=shrink,
        )

    def _fit(self, view: "_View", *, retry_note: str, shrink: int) -> tuple[Prompt, int]:
        """Assemble, pulling the overflow lever until it fits or the lever runs out.

        Doing this before the call is what makes the sacrifice order testable: the plan's
        order says B2's verbatim window halves first, and a code path that only a live
        HTTP 400 can reach is a code path nobody has ever verified.
        """
        while True:
            prompt = self._assemble(view, retry_note, shrink)
            ab = prompt.region_tokens
            self.last_prefix_tokens = ab.get("A", 0) + ab.get("B", 0)
            if not prompt.over_ceiling or shrink >= MAX_SHRINK:
                return prompt, shrink
            shrink += 1
            self.shrinks += 1

    async def _ask(self, actor: Actor, ctx: TurnContext) -> Proposal:
        """One call, one deadline, and the two endpoint failures kept apart.

        `ContextTooLong` becomes a flag because the caller is the only thing that can fix
        it — it owns the prompt. `EndpointUnavailable` propagates: turning an outage into a
        fallback action is how a dead server gets recorded as a passive model.
        """
        limit = actor.timeout_for(ctx.phase)
        try:
            if limit is None:
                return await actor.act(ctx)
            return await asyncio.wait_for(actor.act(ctx), timeout=limit)
        except asyncio.TimeoutError:
            self.timeouts += 1
            return Proposal(failure=f"timeout_after_{limit:.0f}s", timed_out=True)
        except ContextTooLong as e:
            return Proposal(failure=f"context_too_long:{str(e)[:120]}", context_too_long=True)
        except EndpointUnavailable:
            raise

    def _mark_fold(self, outcome: TurnOutcome, *, phase: Phase) -> None:
        """One marker per distinct prefix the seats were shown — plan §83's compaction event.

        `compress` is pure (no I/O), so the append lives here; what it writes is `fold_body()`'s
        own output rather than a paraphrase, so the summary in the log *is* the B1 block the
        seats were sent.

        The key is the digest of the B1 bytes, and only those: that block is what a cached prefix
        is keyed on, so a rewrite of it is exactly one flush. Nine seats read the same region B,
        so anything looser writes one line up to 49 times — and `window` must not join the key
        even though it also lands in the payload, because under day-anchored folding the verbatim
        tail grows every turn *without* invalidating anything (measured: 15 markers for 2 states).
        `folded_days` alone would give the same count today, since a folded day is a finished day
        and its summary can no longer grow; the digest is kept because it names the cause rather
        than a proxy that happens to track it.

        Two states get nothing. No folded days means there is nothing to report. `over_ceiling`
        means this prompt never reached the endpoint — the turn was skipped — so writing "the
        table was summarised" would describe a viewing that did not happen.
        """
        p = outcome.prompt
        if not p.folded_days or p.over_ceiling:
            return
        body = fold_body(chronicle(outcome.percept.events), p.folded_days)
        digest = hashlib.sha256(body.encode()).hexdigest()[:16]
        self.log.append(
            Kind.COMPACTION, day=self.state.day, phase=phase.value,
            summary=body, window=p.window, folded_days=list(p.folded_days),
            idempotency_key=f"compaction:{digest}",
        )

    def _write(self, *, kind: str, seat: int, phase: Phase, visibility: Visibility,
               action: Action, outcome: TurnOutcome,
               extra: dict[str, Any], as_of: int | None) -> int:
        self._mark_fold(outcome, phase=phase)
        p = outcome.proposal
        payload: dict[str, Any] = {
            "text": action.speech, "act": action.act, "target": action.target,
            "evidence": list(action.evidence),
            "belief": action.belief.model_dump() if action.belief else None,
        }
        payload.update({k: v for k, v in extra.items() if v is not None})
        # `action.potion` is deliberately *not* copied into the payload: `legality` already
        # refused any answer where it disagrees with `act`, so a stored copy is a second pen.
        # Provenance goes in a sub-dict rather than loose in the payload, so that
        # `render_line` — which reads payload keys for the chronicle — can never render a
        # token count, and so the vocabulary table in events.Kind stays about the game.
        payload["meta"] = {
            "rung": p.rung if p else -1,
            "deviations": list(p.deviations) if p else [],
            "violations": list(outcome.verdict.violations),
            "flags": list(outcome.verdict.flags),
            "citation_stats": {k: list(v) if isinstance(v, (list, set, tuple)) else v
                               for k, v in outcome.verdict.citation_stats.items()},
            "fallback": 1 if outcome.fell_back else 0,
            "timed_out": 1 if outcome.timed_out else 0,
            "context_overflow": 1 if outcome.context_overflow else 0,
            "shrink": outcome.prompt.shrink,
            "attempt": len(outcome.attempts) + (0 if outcome.fell_back else 1),
        }
        hard = kind in (Kind.VOTE, Kind.NIGHT_ACTION)
        # The key carries `as_of` because a PK round is a second sealed ballot from the same
        # seats on the same day: without the round marker the guard would match the first
        # ballot and the revote would vanish from the log — the opposite failure from the one
        # it exists to prevent, and invisible in a mock run that never reaches a PK.
        ev = self.log.append(
            kind, day=self.state.day, phase=phase.value, visibility=visibility, actor=seat,
            request=payload_for_log(outcome.prompt, (p.request_meta if p else None)),
            response=dict(p.response) if p else {},
            attempts=list(outcome.attempts),
            result={"ok": outcome.verdict.ok, "flags": list(outcome.verdict.flags),
                    "fallback": 1 if outcome.fell_back else 0},
            idempotency_key=f"{phase.value}:{self.state.day}:{seat}:{as_of}" if hard else None,
            **payload,
        )
        return ev.seq


def _only_the_label_was_refused(verdict: Verdict) -> bool:
    """True when the one outstanding complaint is the assigned speech act.

    Any other violation on the sheet — an invented citation id, a target the judge never
    offered — still ends in the engine's own default action. Salvaging those would write a
    fact the gate refused, and rung 4 exists precisely so it cannot be talked out of one.
    """
    return bool(verdict.violations) and all(
        v.startswith("act_not_as_assigned") for v in verdict.violations)


def _attempt_record(proposal: Proposal, violations: Iterable[str]) -> dict[str, Any]:
    """One rejected output, kept whole.

    This is the dataset: `raw` is what the model said, `violations` is why the engine said
    no, and the accepted action is the chosen answer. Rejected plus accepted is a preference
    pair, and it only exists if it is written here — nothing can be recovered after the fact,
    because this endpoint cannot be replayed.
    """
    return {
        "raw": proposal.raw,
        "failure": proposal.failure,
        "rung": proposal.rung,
        "deviations": list(proposal.deviations),
        "parse_errors": [dict(e) for e in proposal.parse_errors],
        "violations": list(violations),
        "completion_tokens": proposal.response.get("completion_tokens"),
        "latency_s": proposal.response.get("latency_s"),
    }
