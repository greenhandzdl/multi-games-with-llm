"""Persona as parameters, and the speech act the engine assigns.

This is the anti-collapse machinery (plan §7), and it is the answer to the single most
important measurement in M0-adjacent probing: raising temperature bought 8/8 distinct
*openings* while the behaviour stayed passive — everyone saying "我先听听大家的发言". Wording
diversity is a sampling problem; action diversity is not, so it is fixed where actions are
chosen: here, in code.

`listen` is capped globally per round rather than discouraged in the prompt, because a
prompt that could be obeyed would have been obeyed already.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .belief import BeliefState
from .config import Config
from .state import GameState, LegalSet

# Assignable acts that put nobody forward: an escalation out of 弃票 must not land here.
NOMINATION_FREE_ACTS = ("listen", "align", "pass", "defend")

STYLE_ZH = {
    "blunt": "说话直接，不留面子，短句。",
    "hedged": "爱绕，先说两层铺垫再进正题。",
    "warm": "语气客气，习惯先肯定别人再转折。",
    "dry": "冷淡，讲事实和数字，不讲情绪。",
    "agitated": "情绪外漏，会急，会反问。",
}


@dataclass(frozen=True)
class PersonaParams:
    """Four numbers and a style tag. No backstory paragraphs.

    A persona that only exists as prose gets *read* by the model but not acted on, which
    is how nine players end up sounding like the same cautious analyst. These feed the
    action prior directly, so the personality shows up in who they accuse.
    """

    aggression: float = 0.5
    suspicion_prior: float = 0.5
    conformity: float = 0.5
    verbosity: float = 0.5
    style: str = "hedged"


def sample_persona(rng: random.Random) -> PersonaParams:
    return PersonaParams(
        aggression=round(rng.uniform(0.15, 0.95), 3),
        suspicion_prior=round(rng.uniform(0.2, 0.9), 3),
        conformity=round(rng.uniform(0.1, 0.9), 3),
        verbosity=round(rng.uniform(0.2, 0.9), 3),
        style=rng.choice(tuple(STYLE_ZH)),
    )


def render_persona_card(p: PersonaParams) -> str:
    """The seat's only view of its own persona: a number the card omits cannot act on.

    `verbosity` was sampled for a long time without ever being printed, which made the
    class docstring's "four numbers" true of the dataclass and false of the product.
    """
    return (
        "== 你的性格参数 ==\n"
        f"攻击性 {p.aggression:.2f}（越高越敢点名），多疑 {p.suspicion_prior:.2f}，"
        f"从众 {p.conformity:.2f}（越高越爱跟票）。\n"
        f"话多 {p.verbosity:.2f}（越高越把一句话说长），"
        f"说话风格：{STYLE_ZH.get(p.style, p.style)}"
    )


def _act_weights(p: PersonaParams, listen_left: int) -> dict[str, float]:
    return {
        "accuse": 0.20 + 0.85 * p.aggression,
        "defend": 0.25 + 0.35 * (1 - p.conformity),
        "align": 0.15 + 0.80 * p.conformity,
        "probe": 0.30 + 0.30 * p.suspicion_prior,
        "pivot": 0.20 + 0.25 * (1 - p.conformity),
        # 唯一的 0 在这里：它是这一轮的配额用完了，是禁止而不是"不太想"。原先整张表套
        # `max(v, 0.02)`（为了 M5 的 passivity 不去量抽样表），把 0 又抬回 2%：400 轮 mock
        # 里配额 1 漏了 17 次第二个 listen。而那五个由人格决定的权重在抽样区间上最小 0.15，
        # 地板对它们从来没有生效过——防不住的场景，换了一次真实的漏。守这条不变量的是
        # `test_no_persona_on_the_sampled_range_makes_an_act_unreachable`。
        "listen": 0.55 if listen_left > 0 else 0.0,
    }


def assign_speech_acts(
    state: GameState,
    speakers: list[int],
    personas: dict[int, PersonaParams],
    beliefs: dict[int, BeliefState],
    rng: random.Random,
    cfg: Config,
    legal: dict[int, LegalSet],
) -> dict[int, tuple[str, int | None]]:
    """One (act, target) per speaker for this round.

    The `listen` quota is enforced across the round rather than per seat: with nine seats
    and an independent draw, a fifth of the table passes on doing anything, which is
    exactly the stall (plan §12 R10) that turns a demo into a wait.
    """
    out: dict[int, tuple[str, int | None]] = {}
    listen_left = max(0, cfg.listen_quota_per_round)
    for seat in speakers:
        p = personas[seat]
        b = beliefs.get(seat)
        weights = _act_weights(p, listen_left)
        act = rng.choices(list(weights), weights=list(weights.values()), k=1)[0]
        if act == "listen":
            listen_left -= 1

        targets = sorted(legal[seat].targets) if seat in legal else [s for s in state.alive_seats if s != seat]
        target: int | None = None
        if targets and act != "defend":
            ranked = b.top_suspects(k=len(targets)) if b else []
            pool = [t for t in targets]
            if ranked and p.suspicion_prior > 0.35:
                # High-suspicion players get the engine's ranking; low-suspicion ones get
                # a shuffle. That mix is the point: it makes stated_vs_engine_agreement
                # (M6) a real question instead of a foregone copy.
                prefer = [t for t in ranked if t in pool][:2]
                target = rng.choice(prefer) if prefer and rng.random() < 0.5 + 0.4 * p.suspicion_prior \
                    else rng.choice(pool)
            else:
                target = rng.choice(pool)
        elif act == "defend":
            # 辩护 needs someone to be defending *against*; name the loudest accuser.
            target = _top_accuser(b, seat)

        # 弃票满两轮要的升级必须是"把名字往前推"的动作。`defend` 在这份名单里，理由和
        # listen 一样：它替别人对你的指控说话，换了标签还是不点名。漏掉它曾让引擎一边指派
        # defend、一边在 C4 要求点名，闸门（核对指派）于是拒掉 stand-in 桌照 §7.4 改出来的
        # accuse —— 一个引擎自己组装的矛盾，100 局里以 7 次 fallback 的代价显形。
        if state.abstain_streak.get(seat, 0) >= 2 and act in NOMINATION_FREE_ACTS:
            act, target = "accuse", (target or (rng.choice(targets) if targets else None))

        out[seat] = (act, target)
    return out


def _top_accuser(b: BeliefState | None, seat: int) -> int | None:
    if b is None:
        return None
    accusers = [c.actor for c in b.claims if c.label == "accuse" and c.target == seat]
    return max(set(accusers), key=accusers.count) if accusers else None


def repeat_fragments(speeches: list[str], top: int = 4) -> tuple[str, ...]:
    """High-frequency chunks already used this round, for the C4 anti-repeat line."""
    from .metrics import template_top_fragments

    return tuple(f for f, _ in template_top_fragments(speeches)[:top])
