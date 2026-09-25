"""The human seat's side of the table: one screen out, one line of typing in.

Deliberately **not** in `schema.py`, for two reasons that are both load-bearing:

* `schema.py` is pure core (`test_purity.py`), and this module exists to touch the terminal.
  The grammar below is the half that reads a person, so putting it there would make "零 LLM
  单测整局" depend on a console being attached.
* `normalize_act` has a branch that accepts an act word *anywhere in the sentence* — that
  tolerance is right for a model output and wrong for a typed one. Read 「我票了3号」 with it
  and a player's aside becomes a ballot he never intended to cast. So the vocabulary is
  borrowed (`ACT_SYNONYMS`, exact token at the head of the line) and the tolerance is not.

Nothing here decides whether an answer is *allowed*. `agent.py` runs the same
`legality.check_action` gate over a typed line as over a generated one, which is the whole
point of the seam: a hand-typed ballot and a model's ballot fail in the same place, for the
same reason, and land in the same log columns.

Unintelligible input is re-asked rather than returned as a failed proposal — the loop lives in
`HumanActor.act`, since asking again needs the screen and this module has none. The reason not to
hand it back as a failure: a typo is not a contract violation, so burning
`cfg.max_repair_retries` on it would spend the model's budget on a person, and writing it into
`attempts[]` would add a preference pair whose "rejected" side was never an output anyone was
asked for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .compress import render_line
from .legality import TARGETLESS_ACTS
from .schema import ACT_SYNONYMS, Action, coerce_seat

if TYPE_CHECKING:  # `actors.py` imports this module; the annotation must not close the cycle
    from .actors import TurnContext

# Longest first, so 「弃票」 is read as 弃票 and not as 票 + 弃.
_ACT_TOKENS = sorted(ACT_SYNONYMS, key=len, reverse=True)
_SEAT_CHARS = frozenset("0123456789一二两三四五六七八九十")
_SEPARATORS = " \t，,、。．.：:；;！!？?（）()「」『』“”\"'—-…"
_CN_SUFFIXES = ("号位", "号", "位")

OFFER_HEADER = "这一轮可以答："
# A person reads one screen, not a transcript: the recap is for `replay`, this is for deciding.
# Sized to the shortest turn that still needs context — a seat picking a ballot has to have seen
# the speeches it is answering, and nine seats speaking once each is the normal-case window.
SCREEN_TAIL = 12


def parse_human_line(line: str) -> Action | None:
    """`动作词 [座位号] [要说的话]` -> Action. Returns None when the line is not an instruction.

    Two strictnesses, and they refuse different lines: the act word must *head* the line (that is
    what refuses 「先票 3」), and a seat number must sit *against* it with only punctuation between
    (that is what refuses 「指控他昨晚不说话」). 「我票了3号」 falls to both — and reading it any more
    loosely would turn a sentence about a ballot into a ballot nobody cast. Returns None rather
    than raising because the caller is about to ask again, and because a person who mistypes has
    done nothing the log should record.
    """
    text = (line or "").strip()
    token = next((t for t in _ACT_TOKENS if text.startswith(t)), None)
    if token is None:
        return None
    rest = text[len(token):]
    if rest and rest[0] not in _SEPARATORS:
        # 「票型分析我看不上」 — glued to the act word, so it was never an instruction.
        target, speech = _lead_seat(rest)
        if target is None:
            return None
    else:
        target, speech = _lead_seat(rest.strip(_SEPARATORS))
    return Action(act=ACT_SYNONYMS[token], target=target, speech=(speech or "").strip())


def _lead_seat(text: str) -> tuple[int | None, str]:
    """Consume a leading seat reference, or nothing at all.

    `coerce_seat` still owns *which* spellings name a seat (3 / 3号 / 三号 / P3); what it does
    not do is stop at a boundary, so it is handed a chunk cut here rather than the whole line.
    The boundary matters: 「听8号说两句」 points at nobody, and reading a target out of it would
    be the same invention the sentence above refuses.
    """
    i = 0
    while i < len(text) and text[i] in _SEAT_CHARS:
        i += 1
    if i == 0:
        return None, text
    seat = coerce_seat(text[:i])
    if seat is None:
        return None, text
    tail = text[i:]
    for suffix in _CN_SUFFIXES:
        if tail.startswith(suffix):
            tail = tail[len(suffix):]
            break
    if tail and tail[0] not in _SEPARATORS:
        return None, text
    return seat, tail


def decision_card(ctx: "TurnContext") -> str:
    """The screen a player reads before answering: what happened, then what he can answer with.

    Three rules shape it. The first two are checked by `tests/test_human_seat.py`, the third by
    `tests/test_info_isolation.py`:

    * Every word offered comes from `ACT_SYNONYMS` and only for an act in `ctx.legal`. A card
      that offers what the gate will refuse is a retry paid for by the player, and a card that
      silently *drops* a legal act takes a choice away from him. The offered acts are therefore
      a block with its own header: once the chronicle is on the screen too, scanning the whole
      card for act words would be counting what the judge said, not what the player was offered.
    * The assigned speech act is named, with what refusing it costs. `legality.py` treats that
      field as hard even in a soft phase; a player who is not told is being decided for
      without knowing it.
    * The chronicle is `ctx.percept`'s tail, rendered by the one renderer (`compress.render_line`)
      and never re-selected from the log. A seat that cannot see a fact must not read it here
      either — which is why this reads the same `Percept` object a model seat is given rather
      than reaching for `log.all()`.
    """
    legal = ctx.legal
    allowed = list(legal.acts) + (["pass"] if legal.allow_pass else [])
    out = [f"轮到你了：{ctx.seat} 号" + (f"（{ctx.role}）" if ctx.role else "")]
    heard = [render_line(e) for e in ctx.percept.tail(SCREEN_TAIL)]
    out.append(f"局况（你看得见的最近 {len(heard)} 条）：")
    out.extend(f"  {line}" for line in heard)
    out.append(OFFER_HEADER)
    for act in allowed:
        words = "/".join(zh for zh, en in ACT_SYNONYMS.items() if en == act)
        out.append(f"  {words}" + ("" if act in TARGETLESS_ACTS else " 加座位号")
                   + " 后面可以跟你要说的话")
    if legal.assigned_act:
        out.append(f"法官指派本轮使用的动作：{legal.assigned_act}")
        out.append("换成别的会被引擎代答一次，你那句话仍然算你说的。")
    if legal.targets:
        out.append("可点名的座位：" + "、".join(str(s) for s in sorted(legal.targets)))
    else:
        out.append("本轮没有可点名的座位。")
    return "\n".join(out)


class Console:
    """The real terminal. Split from `HumanActor` so a test can hand it a different screen.

    `read` returns `None` at end of input rather than raising: `KeyboardInterrupt` and EOF are
    how a person *leaves*, and that has to arrive as an event the engine records as its own
    doing, not as a traceback that eats the log.
    """

    def show(self, text: str) -> None:
        print(text, flush=True)

    def read(self, prompt: str) -> str | None:
        try:
            return input(prompt)
        except EOFError:
            return None
