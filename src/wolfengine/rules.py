"""Rules engine. Pure: no LLM, no I/O, no clock, no randomness except an injected RNG.

Everything here is a function of its arguments, which is what lets the whole board be
exhaustively tested without an endpoint. `test_rules.py` is the safety net for the claim
in plan §2 principle 1 — the engine, not the model, owns truth.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from .roles import Board, Team
from .state import Death, GameState, LegalSet, Phase, Winner


class RuleError(RuntimeError):
    pass


# --------------------------------------------------------------------------- dealing


def deal(board: Board, rng: random.Random) -> dict[int, str]:
    """Seat -> role. rng is supplied by the caller and seeded from deal_seed, so the
    same seed always yields the same table. Model randomness is a separate thing and is
    not reproducible on this endpoint at all (plan §2 principle 5)."""
    pool: list[str] = [r.id for r, n in board.composition for _ in range(n)]
    if len(pool) != board.seat_count:
        raise RuleError(f"board {board.id} declares {board.seat_count} seats but pools {len(pool)} roles")
    seats = list(range(1, board.seat_count + 1))
    rng.shuffle(pool)
    return dict(zip(seats, pool))


def speech_order(rng: random.Random, alive: list[int]) -> tuple[int, ...]:
    return tuple(rng.sample(alive, k=len(alive)))


# ------------------------------------------------------------------ legal action sets


def legal_actions(state: GameState, seat: int) -> LegalSet:
    """The only source of truth for what a seat may do right now.

    Dead seats have no actions except a pending hunter shot, which is its own phase —
    letting a dead seat act would be the single most damaging bug this module could have,
    because the model would never notice.
    """
    if not state.is_alive(seat):
        if state.phase is Phase.HUNTER_SHOT and state.pending_shots and state.pending_shots[0] == seat:
            # 不开枪 has to be legal: PHASE_TASK_ZH offers the hunter that choice, and a
            # hard-gate phase whose prompt promises an option the gate refuses is a retry
            # loop with a guaranteed fallback at the end of it.
            return LegalSet(acts=("shoot",), targets=frozenset(state.alive_seats) - {seat},
                            allow_pass=True)
        return LegalSet(reason_if_empty="dead")

    role = state.role_of(seat)
    spec = state.board.spec(role)
    phase = state.phase

    if phase is Phase.NIGHT_WOLF:
        if "kill" not in spec.abilities:
            return LegalSet(reason_if_empty="not_wolf")
        targets = frozenset(alive_good_or_wolf(state, seat))
        return LegalSet(acts=("kill",), targets=targets, allow_pass=state.house.allow_empty_kill)

    if phase is Phase.NIGHT_WITCH:
        if "save" not in spec.abilities:
            return LegalSet(reason_if_empty="not_witch")
        consumables = tuple(
            c for c, left in (("save", state.witch.save_left), ("poison", state.witch.poison_left)) if left > 0
        )
        return LegalSet(
            acts=("save", "poison", "pass") if consumables else ("pass",),
            targets=frozenset(state.alive_seats),
            allow_pass=True,
            consumables=consumables,
            reason_if_empty="",
        )

    if phase is Phase.NIGHT_SEER:
        if "check" not in spec.abilities:
            return LegalSet(reason_if_empty="not_seer")
        unchecked = frozenset(state.alive_seats) - {seat} - set(state.seer_results)
        return LegalSet(acts=("check",), targets=unchecked, allow_pass=not unchecked,
                        reason_if_empty="" if unchecked else "all_checked")

    if phase in (Phase.DAY_SPEECH, Phase.DAY_PK_SPEECH):
        # Same action set: *who* is addressed in a pk round is the orchestrator's decision
        # (phases.py only asks `state.pk_seats`), not a difference in what is legal.
        # It used to be a difference in that only DAY_SPEECH existed here and the pk round
        # got an empty LegalSet, i.e. every pk speech fell back to the engine.
        return LegalSet(acts=("accuse", "defend", "align", "probe", "pivot", "listen"),
                        targets=frozenset(state.alive_seats) - {seat})

    if phase is Phase.DAY_VOTE:
        targets = frozenset(state.alive_seats) - {seat}
        if state.pk_seats:
            targets = targets & frozenset(state.pk_seats)
        # 弃票 is legal but persona.py escalates the pressure on repeat abstainers.
        return LegalSet(acts=("vote",), targets=targets, allow_pass=True)

    if phase is Phase.LAST_WORDS:
        return LegalSet(acts=("last_words",), targets=frozenset(), allow_pass=True)

    return LegalSet(reason_if_empty=f"no_actions_in_{phase.value}")


def alive_good_or_wolf(state: GameState, seat: int) -> list[int]:
    """Wolf kill targets: anybody alive but themselves, minus teammates if 自刀 forbidden."""
    out = []
    for s in state.alive_seats:
        if s == seat:
            continue
        if not state.house.wolf_self_kill and state.team_of(s) == "wolf":
            continue
        out.append(s)
    return out


# ------------------------------------------------------------------- night resolution


@dataclass(frozen=True)
class NightPlan:
    """Collected private actions for one night. `witch_*` are None when she passed."""

    wolf_kill: int | None = None
    witch_save: bool = False
    witch_poison: int | None = None
    seer_check: int | None = None


@dataclass
class NightResolution:
    deaths: list[Death] = field(default_factory=list)
    seer_report: tuple[int, str] | None = None
    used_save: bool = False
    used_poison: bool = False
    # House-rule rejections and impossible inputs, kept as strings because they are the
    # raw material for the M2/M3 first-attempt illegal-action rate.
    blocked: list[str] = field(default_factory=list)
    peace: bool = False  # 平安夜

    @property
    def dead_seats(self) -> list[int]:
        return [d.seat for d in self.deaths]


def resolve_night(state: GameState, plan: NightPlan) -> NightResolution:
    """Deterministic night settlement, including the three-way 刀/救/毒 interaction.

    Written as a pure function of (state, plan) so every boundary in the truth table can
    be asserted without running a game.
    """
    h = state.house
    res = NightResolution()
    day = state.day

    kill = plan.wolf_kill
    if kill is not None and not state.is_alive(kill):
        # A kill resolved against an already-dead seat would silently produce a
        # duplicate death. The legality gate should have refused it; this is the backstop.
        res.blocked.append(f"invalid_wolf_kill_target:{kill}")
        kill = None
    poison = plan.witch_poison
    if poison is not None and not state.is_alive(poison):
        res.blocked.append(f"invalid_poison_target:{poison}")
        poison = None

    # --- witch: potion availability and the self-save rule ---------------------
    save = plan.witch_save
    if save:
        if state.witch.save_left <= 0:
            res.blocked.append("save_no_potion")
            save = False
        elif h.save_only_same_night and kill is None:
            res.blocked.append("save_without_kill")
            save = False
        elif kill is not None and kill == state.witch_seat:
            if h.self_save == "never" or (h.self_save == "night1_only" and day > 1):
                res.blocked.append(f"save_blocked_self_save_{h.self_save}")
                save = False
    if save and poison is not None and h.one_potion_per_night:
        # Two potions the same night. The save wins because it is the action the witch
        # took with knowledge of the knife; the poison is reported as blocked, not dropped
        # silently — a silently dropped action looks identical to a bug in the logs.
        res.blocked.append("one_potion_per_night:dropped_poison")
        poison = None

    # --- deaths ----------------------------------------------------------------
    dying: dict[int, str] = {}
    if poison is not None:
        dying[poison] = "poison"
    if kill is not None:
        if save and not (poison == kill and h.poison_overrides_save):
            res.used_save = True
        else:
            if save and poison == kill:
                res.blocked.append("poison_overrides_save")
            # 同刀同毒: the poison owns the death, because cause decides whether the
            # hunter may shoot (被毒死的猎人不开枪). Overwriting "poison" with
            # "wolf_kill" here would hand the wolves a free shot on that boundary.
            dying.setdefault(kill, "wolf_kill")
    if poison is not None:
        res.used_poison = True

    for seat in sorted(dying):
        res.deaths.append(Death(seat=seat, day=day, night=day, cause=dying[seat]))  # type: ignore[arg-type]
    res.peace = not res.deaths

    # --- seer ------------------------------------------------------------------
    check = plan.seer_check
    if check is not None:
        if not state.is_alive(check) or check == state.seer_seat:
            res.blocked.append(f"invalid_check_target:{check}")
        elif state.seer_results.get(check) is not None:
            # 不能重复验人. Reported, not swallowed: the retry path needs the reason.
            res.blocked.append(f"already_checked:{check}")
        else:
            verdict = "wolf" if state.team_of(check) == "wolf" else "good"
            if check in dying and h.check_killed_that_night == "unknown":
                verdict = "unknown"
            res.seer_report = (check, verdict)

    return res


def apply_night(state: GameState, res: NightResolution) -> None:
    """Mutate state from a resolution. Split out so the pure part stays testable."""
    if res.used_save:
        state.witch.save_left -= 1
    if res.used_poison:
        state.witch.poison_left -= 1
    if res.seer_report is not None:
        state.seer_results[res.seer_report[0]] = res.seer_report[1]
    for d in res.deaths:
        state.seats[d.seat].alive = False
        state.deaths.append(d)
        if "shoot_on_death" in state.board.spec(state.role_of(d.seat)).abilities:
            if d.cause in state.house.hunter_shoots_on:
                state.pending_shots.append(d.seat)


# ------------------------------------------------------------------------- voting


@dataclass
class VoteResult:
    tally: dict[int, int]
    abstainers: list[int]
    top_seats: list[int]
    out: int | None
    tied: bool
    # seats that must give a second-round speech before a revote
    pk_seats: tuple[int, ...] = ()


def tally_votes(votes: dict[int, int | None], eligible: list[int]) -> VoteResult:
    """One round of voting. votes maps voter -> target or None for 弃票.

    A vote for a seat that is not eligible is counted as an abstention by the caller,
    never as a vote — the legality gate rejects it first; this is the backstop so a gate
    bug cannot invent a phantom exile.
    """
    elig = set(eligible)
    tally: Counter[int] = Counter()
    abstainers: list[int] = []
    for voter, target in sorted(votes.items()):
        if target is None or target not in elig or target == voter:
            abstainers.append(voter)
            continue
        tally[target] += 1
    if not tally:
        return VoteResult({}, sorted(abstainers), [], None, False, ())
    top = max(tally.values())
    tied_seats = sorted(s for s, n in tally.items() if n == top)
    if len(tied_seats) == 1:
        return VoteResult(dict(tally), sorted(abstainers), tied_seats, tied_seats[0], False, ())
    return VoteResult(
        tally=dict(tally),
        abstainers=sorted(abstainers),
        top_seats=tied_seats,
        out=None,
        tied=True,
        pk_seats=tuple(tied_seats),
    )


def will_pk(state: GameState, res: VoteResult) -> bool:
    """Does this ballot send the tied seats to a PK speech?

    One predicate, one source: the branch in `phases.run_vote` and the sentence in
    `phases._tally_text` must give the same answer, because the judge cannot declare the case
    closed on one line and open a PK on the next. Spelled a second time anywhere, the wording
    drifts from the code — the bug `tests/test_tally_wording.py` opens with is exactly that.

    Only the day's *first* ballot may ask: a second tie is terminal, which is the `once` in
    `pk_once_then_nobody`. The revote therefore passes no flag at all.
    """
    return bool(res.tied and res.pk_seats and state.house.tie_break == "pk_once_then_nobody")


def resolve_tie(state: GameState, second: VoteResult) -> int | None:
    """PK rule: a tie gives the tied seats one more speech, then one revote restricted
    to them. A second tie exiles nobody (plan §3 house rule `pk_once_then_nobody`) — and
    that is read off `second` alone, so the first ballot is deliberately not an argument."""
    if state.house.tie_break == "nobody":
        return None
    return second.out


# -------------------------------------------------------------------- win conditions


def winner_for(board: Board, alive_teams: Counter[Team]) -> Winner | None:
    """Decided purely by which teams remain alive — by 阵营, never by the role id a seat holds.

    The keys are `roles.Team` ("wolf"/"villager"/"god"), not the five role ids a seat is
    dealt. A Counter built from `role_of` has no "god" key at all, so the 屠边 branch below
    would read `get("god", 0) == 0` as true on a board where nobody has died: 整局在第一次
    `check_win` 就判狼赢。`test_rules.py` pins that consequence and `test_wiring.py` pins
    the naming, the annotation and the absence of a role-keyed twin.

    屠边 means the wolves only need one whole group wiped, so a board where the gods die
    first is lost even with every villager standing. That asymmetry is the reason
    `seer_death_day` is a strategy metric (M8) rather than trivia.
    """
    if alive_teams.get("wolf", 0) == 0:
        return "good"
    if board.house.win_condition == "tu_bian":
        if alive_teams.get("villager", 0) == 0 or alive_teams.get("god", 0) == 0:
            return "wolf"
    else:  # 屠城
        if alive_teams.get("villager", 0) + alive_teams.get("god", 0) == 0:
            return "wolf"
    return None


def alive_team_counts(state: GameState) -> Counter[Team]:
    return Counter(state.team_of(s) for s in state.alive_seats)


def check_win(state: GameState) -> Winner | None:
    """Set and return the winner. Idempotent, so it is safe to call after every death."""
    if state.winner is not None:
        return state.winner
    w = winner_for(state.board, alive_team_counts(state))
    if w is not None:
        state.winner = w
        state.phase = Phase.OVER
    return w


def needs_last_words(state: GameState, death: Death) -> bool:
    h = state.house
    if death.cause == "exiled":
        return h.last_words_exiled
    if death.cause in ("wolf_kill", "poison"):
        if death.night == 1:
            return h.last_words_night_death_first_night
        return h.last_words_night_death_later
    return False


def day_limit_reached(state: GameState, max_days: int) -> bool:
    """Pre-registered tie rule (plan §12 R10): running out of days is a draw, and the
    denominator decision had to exist before the batch, not after seeing the result.
    `terminal="draw_day_limit"` games stay in the reported denominator's neighbour —
    `m1_win_rate` lists them under `excluded`, apart from `aborted`, and never mixes them
    into a faction win rate.

    `>=`, which is the whole content of this function: the cap counts the last day *whole*,
    so 6 means "day 6 ends the game" and not "there is a day 7". There used to be a second
    implementation of this predicate that said `>`, and nothing called it.
    """
    return state.day >= max_days
