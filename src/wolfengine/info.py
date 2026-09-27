"""What a seat is allowed to know, as a type rather than a convention.

`Percept` is the only object an actor receives. It cannot be constructed holding an event
that its seat may not see — `__post_init__` raises — so an isolation bug requires someone
to deliberately delete a guard, not to forget a filter. This is the module that makes the
product's whole premise (information asymmetry) enforceable instead of requested.

Corollary that matters for the seat abstraction: a human player is handed the *same*
`Percept` object, so the human UI cannot leak either, because the data isn't in it. Since `#124`
that is a tested claim about the *screen* and not only about the object: the canaries in
`tests/test_info_isolation.py` are asserted against the bytes `human.decision_card` renders, too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .events import Event, Kind


class IsolationError(RuntimeError):
    """Raised when a Percept would expose an event its seat has no right to read."""


def eid(seq: int) -> str:
    """Canonical citation id. Region B prints these; region C demands them back."""
    return f"e{seq}"


@dataclass(frozen=True)
class Percept:
    seat: int
    at_seq: int
    events: tuple[Event, ...] = ()

    def __post_init__(self) -> None:
        seen: list[int] = []
        for e in self.events:
            if not e.visible_to(self.seat):
                raise IsolationError(
                    f"seat {self.seat} was handed event seq={e.seq} kind={e.kind} "
                    f"visibility={e.visibility}; refusing to construct this Percept"
                )
            seen.append(e.seq)
        if sorted(seen) != seen:
            raise IsolationError("Percept events must be ordered by seq (the prompt is built from them)")

    # --- views ---------------------------------------------------------------
    @property
    def id_set(self) -> frozenset[str]:
        """Every citable id this seat holds. The evidence gate is membership in this set."""
        return frozenset(eid(s) for s in (e.seq for e in self.events))

    def by_kind(self, *kinds: str) -> tuple[Event, ...]:
        return tuple(e for e in self.events if e.kind in kinds)

    def role(self) -> str:
        """This seat's own role, read back out of the log.

        The deal is recorded as one private event per seat precisely so that roles travel
        the same visibility-filtered path as everything else. There is no separate
        role lookup to get wrong, and the canary test covers roles for free.
        """
        for e in reversed(self.events):
            if e.kind == Kind.DEAL and e.actor == self.seat:
                return str(e.payload.get("role", ""))
        return ""

    def teammates(self) -> frozenset[int]:
        for e in reversed(self.events):
            if e.kind == Kind.DEAL and e.actor == self.seat:
                return frozenset(e.payload.get("teammates", ()))
        return frozenset()

    def tail(self, n: int) -> tuple[Event, ...]:
        return self.events[-n:]

    def window(self, seqs: Iterable[int]) -> tuple[Event, ...]:
        want = set(seqs)
        return tuple(e for e in self.events if e.seq in want)


def percept_for(
    seat: int,
    events: Iterable[Event],
    *,
    at_seq: int | None = None,
) -> Percept:
    """The single entry point from the log to a seat's worldview.

    Filters *before* constructing, so nothing downstream has to remember to filter. Note
    this takes events, not a GameState: the prompt path never touches the seat→role table,
    so there is no separate "public projection" to keep role-free — the canary tests in
    tests/test_info_isolation.py are what pin that claim down.
    """
    src = list(events)
    # A transcript with no events has no last seq to cut at, and an IndexError on the read
    # side is not a verdict — see the seat branch of `cli.render_chronicle`.
    cut = at_seq if at_seq is not None else (src[-1].seq if src else 0)
    visible = tuple(e for e in src if e.seq <= cut and e.visible_to(seat))
    return Percept(seat=seat, at_seq=cut, events=visible)


"""Rendering lives in one place: `compress.render_line`. A second renderer here
would drift from it, and did — this module used to own a duplicate that no caller read.
"""
