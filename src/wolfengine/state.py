"""In-memory game state, and the one projection that is allowed to reach a prompt.

`GameState` knows roles. `PublicState` does not. Region B of the prompt is rendered only
from `PublicState`; anything carrying a role must go through `info.percept_for()`, which
is where seat-authorized private data gets appended. Keeping the two types separate is
what makes "the renderer cannot leak" a structural fact rather than a review checklist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from .roles import Board, HouseRules


class Phase(str, Enum):
    NIGHT_WOLF = "night_wolf"
    NIGHT_WITCH = "night_witch"
    NIGHT_SEER = "night_seer"
    DAY_SPEECH = "day_speech"
    DAY_VOTE = "day_vote"
    DAY_PK_SPEECH = "day_pk_speech"
    LAST_WORDS = "last_words"
    HUNTER_SHOT = "hunter_shot"
    OVER = "over"


DeathCause = Literal["wolf_kill", "poison", "exiled", "hunter_shot"]
Winner = Literal["wolf", "good"]


@dataclass(frozen=True)
class Death:
    seat: int
    day: int
    night: int  # 0 = daytime death
    cause: DeathCause


@dataclass
class SeatState:
    seat: int
    role: str
    alive: bool = True


@dataclass
class WitchState:
    save_left: int = 1
    poison_left: int = 1

    @property
    def both_used(self) -> bool:
        return self.save_left == 0 and self.poison_left == 0


@dataclass
class LegalSet:
    """What the engine will accept from this seat right now.

    `targets` is a set of seats, never a range: `kill`/`vote`/`check` are mutually
    exclusive *values* of `act`, and a seat may be legal for one and not another
    (the witch may poison a teammate; the seer may not check the dead).
    """

    acts: tuple[str, ...] = ()
    targets: frozenset[int] = frozenset()
    allow_pass: bool = False
    # witch-specific: which potions are still available this night
    consumables: tuple[str, ...] = ()
    # day speech: the speech act the engine assigned, not chosen by the model (plan §7)
    assigned_act: str | None = None
    assigned_target: int | None = None
    reason_if_empty: str = ""


@dataclass
class GameState:
    board: Board
    game_id: str
    deal_seed: int
    seats: dict[int, SeatState]
    day: int = 1
    phase: Phase = Phase.NIGHT_WOLF
    deaths: list[Death] = field(default_factory=list)
    witch: WitchState = field(default_factory=WitchState)
    witch_seat: int | None = None
    seer_seat: int | None = None
    hunter_seat: int | None = None
    # seat -> ("good"|"wolf") — the seer's own knowledge, mirrored by the engine so the
    # belief card can state it as fact. Not the truth the audience may want later.
    seer_results: dict[int, str] = field(default_factory=dict)
    # Deaths awaiting a hunter shot, in the order they happened.
    pending_shots: list[int] = field(default_factory=list)
    winner: Winner | None = None
    terminal: str = ""
    speech_order: tuple[int, ...] = ()
    pk_seats: tuple[int, ...] = ()
    # seats that abstained on the previous two vote rounds; drives the forced-nominate
    # escalation in persona.py (plan §7 item 4)
    abstain_streak: dict[int, int] = field(default_factory=lambda: {})

    # --- accessors -------------------------------------------------------------
    @property
    def house(self) -> HouseRules:
        return self.board.house

    @property
    def alive_seats(self) -> list[int]:
        return [s.seat for s in self.seats.values() if s.alive]

    @property
    def dead_seats(self) -> list[int]:
        return [s.seat for s in self.seats.values() if not s.alive]

    def role_of(self, seat: int) -> str:
        """Private. Only game.py/phases.py/belief.py (for its own seat) may call this
        with a seat other than the caller's — enforced by test_info_isolation.py, which
        asserts no rendered prompt byte contains another seat's role."""
        return self.seats[seat].role

    def team_of(self, seat: int) -> str:
        return self.board.spec(self.role_of(seat)).team

    def is_alive(self, seat: int) -> bool:
        return self.seats[seat].alive

    def teammates_of(self, seat: int) -> frozenset[int]:
        """Wolves know each other; nobody else has a private team view."""
        if not self.board.spec(self.role_of(seat)).knows_teammates:
            return frozenset()
        return frozenset(
            s for s in self.alive_seats if s != seat and self.team_of(s) == "wolf"
        )

    # --- projection ------------------------------------------------------------
    def public_state(self) -> PublicState:
        return PublicState(
            day=self.day,
            phase=self.phase,
            alive=tuple(self.alive_seats),
            dead=tuple(self.dead_seats),
            deaths=tuple(self.deaths),
            winner=self.winner,
            terminal=self.terminal,
            speech_order=self.speech_order,
            pk_seats=self.pk_seats,
            seat_count=self.board.seat_count,
        )


@dataclass(frozen=True)
class PublicState:
    """Everything every seat is allowed to know by definition. Contains no role."""

    day: int
    phase: Phase
    alive: tuple[int, ...]
    dead: tuple[int, ...]
    deaths: tuple[Death, ...]
    winner: Winner | None
    terminal: str
    speech_order: tuple[int, ...]
    pk_seats: tuple[int, ...]
    seat_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "phase": self.phase.value,
            "alive": list(self.alive),
            "dead": list(self.dead),
            "deaths": [[d.seat, d.night, d.cause] for d in self.deaths],
            "winner": self.winner,
            "speech_order": list(self.speech_order),
            "pk_seats": list(self.pk_seats),
        }
