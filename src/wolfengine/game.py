"""One game, start to finish.

Holds the clock, the budget and the RNG streams; delegates every decision to `phases.py` and
every fact to `rules.py`. The three things this module is solely responsible for are all
about *stopping*: the day limit, the completion-token budget, and the wall-clock budget — and
each has its own terminal value, because a draw, an aborted game and a won game must not
share a denominator (plan §8 M1).

The engine's randomness is seeded and recorded. The model's is not, and cannot be: this
endpoint is non-deterministic even at temp=0 with a fixed seed, which is why the log is the
artifact and a re-run is never a reproduction.
"""

from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import phases, roles, rules
from .actors import Actor, LlmActor
from .agent import Agent
from .config import Config
from .events import EventLog, Kind, seats
from .llm import EndpointUnavailable, LLM
from .persona import PersonaParams, sample_persona
from .state import GameState, Phase, SeatState

#: The pre-registered tie (plan §12 R10). A named constant because three places have to agree
#: on the exact string: this loop, `m1_win_rate`'s `excluded` bucket, and any script that decides
#: whether a run failed. The cap's *length* is `Config.max_days`, not a constant here — that is
#: what makes it a treatment axis instead of a fact about the source file.
DRAW_DAY_LIMIT = "draw_day_limit"


@dataclass
class GameResult:
    game_id: str
    path: Path
    terminal: str
    winner: str | None = None
    days: int = 0
    events: int = 0
    fallbacks: int = 0
    retries: int = 0
    timeouts: int = 0
    context_overflows: int = 0
    shrinks: int = 0
    blocked: list[str] = field(default_factory=list)
    wallclock_s: float = 0.0
    completion_tokens: int = 0
    degraded: bool = False


@dataclass
class RngStreams:
    """Independent streams from one recorded seed.

    Separate streams rather than one shared generator, because how many draws a phase makes
    depends on how many seats are alive: a shared stream would reseat the personas and the
    speech acts whenever someone dies early, and `--pair-by deal_seed` requires that two
    configurations really do start from the same table.
    """

    deal: random.Random
    order: random.Random
    persona: random.Random
    speech: random.Random

    @staticmethod
    def for_seed(deal_seed: int) -> "RngStreams":
        master = random.Random(deal_seed)
        return RngStreams(*[random.Random(master.getrandbits(64)) for _ in range(4)])


def build_game(cfg: Config, deal_seed: int, game_id: str) -> tuple[GameState, dict[int, PersonaParams], RngStreams]:
    rng = RngStreams.for_seed(deal_seed)
    board = roles.board_for(cfg.seat_count)
    deal = rules.deal(board, rng.deal)
    seats_ = {s: SeatState(seat=s, role=r) for s, r in deal.items()}
    state = GameState(
        board=board, game_id=game_id, deal_seed=deal_seed, seats=seats_,
        witch_seat=next((s for s, r in deal.items() if r == "witch"), None),
        seer_seat=next((s for s, r in deal.items() if r == "seer"), None),
        hunter_seat=next((s for s, r in deal.items() if r == "hunter"), None),
        phase=Phase.NIGHT_WOLF,
        speech_order=rules.speech_order(rng.order, sorted(deal)),
    )
    personas = {s: sample_persona(rng.persona) for s in deal}
    return state, personas, rng


def open_log(cfg: Config, state: GameState, path: Path, actor_kinds: tuple[str, ...],
             deal_seed: int, stamp: str) -> EventLog:
    log = EventLog(path, meta={
        "game_id": state.game_id, "deal_seed": deal_seed, "config_hash": cfg.config_hash(),
        "contract_version": cfg.contract_version, "rules_version": cfg.rules_version,
        "compress_version": cfg.compress_version, "board": state.board.id,
        "model": cfg.model, "actor_kinds": sorted(set(actor_kinds)),
        # Ordering evidence for the drift canary: two logs from the same batch share a
        # window, and "was the endpoint the same then" is answered by this, not by mtime.
        "started_utc": stamp,
        # The rulers travel with the thing they measure. `region_tokens` records four lengths
        # per prompt; without the caps that produced them, a log can only say "B2 was 1680"
        # and never "B2 was over budget" — and by the time anyone reads an old batch the config
        # in the shell has moved on. One cell per game, not per prompt: the caps do not change
        # inside a game, and duplicating them onto every prompt is how a scalar becomes a
        # denominator argument.
        "regions": asdict(cfg.regions),
        # The roles are in this file, but only as private `deal` events — which is the point:
        # there is no field a renderer can read to leak them.
        "reproducible": False,
        "reproducibility_note": "端点无确定性：seed 只决定发牌与座位序，重跑不是复现。",
    })
    log.write_meta()
    log.append(Kind.GAME_START, day=1, phase=Phase.NIGHT_WOLF.value,
               seats=sorted(state.seats), result={"ok": True, "source": "engine"})
    wolves = [s for s in state.seats if state.team_of(s) == "wolf"]
    # `state.seats` maps seat -> SeatState, so unpacking it as (seat, role) yields a
    # SeatState where the role string belongs. json.dumps rejected that loudly here, which
    # is the only reason it was caught: nothing else in this path reads the value.
    for seat in sorted(state.seats):
        role = state.role_of(seat)
        # teammates only for the wolves. A `teammates` list on a villager's own deal event
        # would name the wolf team to the person who is not allowed to know it — private
        # events are only as private as their payload.
        log.append(Kind.DEAL, day=1, phase=Phase.NIGHT_WOLF.value, visibility=seats(seat),
                   actor=seat, role=role,
                   teammates=[x for x in wolves if x != seat] if role == "wolf" else [],
                   result={"ok": True, "source": "engine"})
    return log


async def play(
    *,
    cfg: Config,
    deal_seed: int,
    out_dir: str | Path = "data",
    game_id: str | None = None,
    transport: Any = None,
    actors: dict[int, Actor] | None = None,
    wallclock_limit_s: float | None = None,
) -> GameResult:
    game_id = game_id or f"g{deal_seed:08d}"
    state, personas, rng = build_game(cfg, deal_seed, game_id)
    # `<stamp>_<game_id>` (plan §3): a game id is semantic and therefore repeats, while the
    # file must not — the log is append-only, and a second run that appended to the first
    # would interleave two `seq` numberings into an unreadable transcript.
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    if actors is None:
        if transport is None:
            raise ValueError("play() needs either `actors` or a `transport`")
        llm = LLM(transport, cfg)
        actors = {s: LlmActor(llm, cfg, s) for s in state.seats}
    # The counter has to be read off the LLM that is actually answering. `play()` only mints one
    # for a table it builds itself, while both shipped entries (`cli._llm_actors`, `batch`) hand
    # `actors` in — so the local `llm` stayed None on every real game: the file recorded
    # thousands of completion tokens while `spent()` answered 0, and the ceiling below, which
    # reads `spent()`, could not fire. Seats share one LLM, so dedupe by identity before summing.
    counters = {id(a.llm): a.llm for a in actors.values() if getattr(a, "llm", None) is not None}
    kinds = tuple(str(getattr(a, "kind", "unknown")) for a in actors.values())
    log = open_log(cfg, state, Path(out_dir) / f"{stamp}_{game_id}.jsonl", kinds, deal_seed,
                   stamp)
    agent = Agent(cfg=cfg, state=state, log=log, personas=personas,
                  known_ids=lambda: {f"e{e.seq}" for e in log.all()})
    table = phases.Table(cfg=cfg, state=state, log=log, agent=agent, actors=actors, rng=rng.speech)

    started = time.monotonic()
    # Only for a table of nothing but models: a seat a person is thinking in is not a stall,
    # and killing the game over it would discard the reason the human was there.
    enforce_clock = all(k == "llm" for k in kinds)
    limit = cfg.max_game_wallclock_s if wallclock_limit_s is None else wallclock_limit_s
    spent = lambda: sum(c.completion_tokens_spent for c in counters.values())  # noqa: E731

    terminal, winner = DRAW_DAY_LIMIT, None
    try:
        while True:
            await phases.run_night(table)
            if rules.check_win(state) is None:
                await phases.run_day(table)
            if state.winner is not None:
                winner = state.winner
                terminal = "good_win" if winner == "good" else "wolf_win"
                break
            if rules.day_limit_reached(state, cfg.max_days):
                break
            if spent() > cfg.max_game_completion_tokens or agent.retries > cfg.max_retries_total:
                # One terminal for both, because M7's denominator asks "did this game produce a
                # result" and not "which ceiling moved". Which one moved goes in the log:
                # running out of tokens and running out of patience are different findings, and
                # a reader of the transcript should not have to guess between them.
                terminal, winner = "aborted_budget", None
                log.append(Kind.PHASE, day=state.day, phase=Phase.OVER.value,
                           text=f"预算耗尽：completion_tokens={spent()} "
                                f"(上限 {cfg.max_game_completion_tokens}), "
                                f"retries={agent.retries} (上限 {cfg.max_retries_total})",
                           result={"ok": False, "source": "engine"})
                break
            if enforce_clock and time.monotonic() - started > limit:
                terminal, winner = "aborted_wallclock", None
                break
            state.day += 1
            state.phase = Phase.NIGHT_WOLF
            state.speech_order = rules.speech_order(rng.order, state.alive_seats)
    except EndpointUnavailable as e:
        # An outage is not a behaviour finding. It gets its own terminal so no metric can
        # mistake "the server was down" for "the model played passively".
        terminal, winner = "aborted_endpoint", None
        log.append(Kind.PHASE, day=state.day, phase=Phase.OVER.value,
                   text=f"端点不可用：{str(e)[:120]}", result={"ok": False, "source": "engine"})

    state.terminal = terminal
    # The verdict is computed once, here, and written into the artifact next to the counters it
    # was computed from. A reader of this file alone cannot see `cfg.degraded_game_fallbacks`
    # (the log's meta carries only `config_hash`), so the threshold rides along: a pre-registered
    # rule you cannot re-check from the artifact is a rule that becomes whatever the next
    # `Config()` says it was — plan §143's "预先声明，不做事后悄悄剔除" dies at that step.
    degraded = agent.fallbacks > cfg.degraded_game_fallbacks
    # `winner` stays None when no camp won. A placeholder string there ("draw") puts
    # "nothing to report" into the camp key space, and every renderer downstream then has to
    # guess — which is how a draw came to print "draw阵营获胜". `metrics.DECISIVE` is the one
    # source for which terminals have a winner; it keys off `terminal`, so nothing needs this.
    log.append(Kind.GAME_OVER, day=state.day, phase=Phase.OVER.value,
               winner=winner, terminal=terminal,
               degraded_game=degraded, degraded_threshold=cfg.degraded_game_fallbacks,
               result={"ok": terminal != "aborted_endpoint", "source": "engine"})

    return GameResult(
        game_id=game_id, path=log.path, terminal=terminal, winner=winner, days=state.day,
        events=len(log), fallbacks=agent.fallbacks, retries=agent.retries,
        timeouts=agent.timeouts, context_overflows=agent.context_overflows,
        shrinks=agent.shrinks, blocked=list(table.blocked),
        wallclock_s=time.monotonic() - started, completion_tokens=spent(),
        degraded=degraded,
    )
