"""Prompt assembly: region A (shared) + B (chronicle) + C (private tail), budgeted.

The ordering is the whole performance model: A and B are byte-identical across seats, so
they form the cached prefix, and everything seat-specific goes last in C. Putting the
persona card before the chronicle would cost a re-prefill per seat per turn.

Nothing here calls the model or touches the disk: `--dry-run` writes these prompts out,
which is the cheapest way to iterate on the budget table (plan §11).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .belief import BeliefState, render_card
from .compress import (WINDOW_BOUNDARIES, FoldPlan, chrono_bytes, chronicle, estimate_tokens,
                       fold_body, plan_fold, render_line)
from .config import Config, RegionBudget, TokenBudget
from .events import Kind
from .info import Percept, eid
from .persona import PersonaParams, render_persona_card
from .prompts.templates import region_a
from .state import LegalSet, Phase

PHASE_TASK_ZH = {
    Phase.NIGHT_WOLF: "现在是夜里。狼人行动：决定今晚的刀口（可以空刀）。夜里不用发言。",
    Phase.NIGHT_WITCH: "现在是夜里。女巫行动：你可以用一瓶解药救当晚被刀的人，或用一瓶毒药毒一人，"
                       "或者不用药（act=pass）。用药不需要发言。",
    Phase.NIGHT_SEER: "现在是夜里。预言家行动：查验一人的阵营。夜里不用发言。",
    Phase.DAY_SPEECH: "现在天亮了，轮到你发言。说你想说的话，但必须执行法官指派给你的 act。",
    Phase.DAY_VOTE: "现在投票。投出你认为最该出局的人，或者弃票（act=pass）。投票不用发言。",
    Phase.DAY_PK_SPEECH: "平票进入pk，只有平票的几人发言，其他人听。争取别被投出去。",
    Phase.LAST_WORDS: "你已被投出。留下遗言（act=last_words），可以说真话也可以继续骗。",
    Phase.HUNTER_SHOT: "你是猎人，刚死亡。可以开枪带走一人（act=shoot），或者不开枪（act=pass）。",
}


@dataclass
class Prompt:
    messages: list[dict[str, str]]
    region_tokens: dict[str, int] = field(default_factory=dict)
    total_tokens: int = 0
    compactions: int = 0
    # Which days the chronicle handed over to summaries, and how much of it stayed verbatim.
    # Together these are the *fold state*: `agent` writes one marker per distinct state.
    folded_days: tuple[int, ...] = ()
    window: int = 0
    # How far B2 went past `regions.b2` for *this* prompt: the day floor stops folding before the
    # budget is met, and the cap that was overshot lives in `Config`, which a log reader never
    # sees. Measured here or lost.
    b2_over_cap: int = 0
    # How many accusation lines the C knife took out of this prompt's belief card. The shipped
    # `region_tokens.C2` is measured *after* the cut, so on its own it cannot tell "the card held
    # 11 claims" from "it held 14 and 3 were removed" — and the second one is a treatment the model
    # received, which is exactly what an old log has to be able to say about itself.
    card_claims_dropped: int = 0
    over_ceiling: bool = False
    # how far the overflow lever was pulled for this prompt (0 = never)
    shrink: int = 0
    # what the model was told to do, so the legality gate can check `act` against it
    assigned_act: str | None = None
    legal_acts: tuple[str, ...] = ()
    legal_targets: tuple[int, ...] = ()


B2_CAP_START = WINDOW_BOUNDARIES[-1]

# plan §5 的预算表给 B0 和 C1–C4 各写了一格，`RegionBudget` 里也各有一个数，可装配器过去只报
# A/B/C/B1/B2 五段总长：那五格连"量出来是多少"都没有，于是"C1 装不装得进 250"只能是一句散文。
# 键名沿用计划里的块名，值一律从**发出去的那段字节**上取——见 `block_tokens`。
BLOCK_OF_HEADER = {"局况": "B0", "你的性格参数": "C1", "你目前掌握的事实": "C2",
                   "你的私有信息": "C3", "本轮任务": "C4"}


def _est(text: str, tok: TokenBudget) -> int:
    """空格读 0，不读 `estimate_tokens("")` 的那个 1。

    估算器给每段文本 +1，于是"这一格没内容"和"这一格有 1 tok"在账上分不开——没折叠过的局
    `B1` 就是空串，报 1 会让人以为折进去了一天。§5 的每一格要么没出现要么有几十字，
    所以这条规则只影响 0 和 1 之间的那一格，而那里正是"缺数据"和"读到了空"的边界。
    """
    return estimate_tokens(text, tok) if text else 0


def block_tokens(text: str, tok: TokenBudget) -> dict[str, int]:
    """每格 = 它的标题行到下一个标题行之间的那段字节。

    分块用的是装配时的拼接符 `"\\n\\n"`，而换东家的判据是"这一串以 `== ` 开头"：`你的座位`
    和 `上一轮被拒` 在 §5 的表里没有预算数，它们既不该进任何一格，也不该因为没人认领就被
    并进邻居的账上。

    只报这一串文本里**真的出现**的格。缺格补 0 是调用方的事（见 `assemble`）：没私有事件的
    座位，`C3` 要读作 0（"测到了，这一格是空的"）而不是缺键（"没测到"）——
    `metrics.region_budget_check` 对后一种写的是 None，两句话不能共用一个形状。
    """
    chunks: dict[str, list[str]] = {}
    current: str | None = None
    for part in text.split("\n\n"):
        head = part.splitlines()[0] if part else ""
        opened = next((name for key, name in BLOCK_OF_HEADER.items()
                       if head.startswith(f"== {key}")), None)
        if opened:
            current = opened
            chunks.setdefault(opened, []).append(part)
        elif head.startswith("== "):
            current = None
        elif current:
            chunks[current].append(part)
    return {name: _est("\n\n".join(found), tok) for name, found in chunks.items()}



def _region_b(percept: Percept, rb: RegionBudget, cfg: Config, *,
              shrink: int = 0) -> tuple[str, FoldPlan, dict[str, int]]:
    """B1 folded days + B2 verbatim tail, then B0's status card **last**.

    Returns the two halves' token estimates alongside the text because the day floor in
    `plan_fold` is allowed to let B2 overshoot its soft region budget, and "by how much, how
    often" is a claim about this prompt — it has to be measured here, where the cap is in hand,
    not re-derived by a reader who only has the log (a single file carries `config_hash`, not
    `regions.b2`).

    The card is the one block in B that is not append-only: a death announced at dawn and an
    exile announced after the vote both rewrite it, so on a measured mock game it changed 2–3
    times *per day*. Anything after a change re-prefills, which made the card — about forty
    tokens — cost the whole chronicle's cached prefix every time it moved. Putting it at the
    end costs its own ~40 tokens of prefill and keeps everything before it byte-stable. The
    same trick region C already uses, one level up.

    `shrink` halves the verbatim window: the plan's sacrifice order puts B2 first, and the
    overflow path has to be reachable offline, since a code path that only fires on a live
    HTTP 400 is a code path nobody has ever tested.
    """
    pub = chronicle(percept.events)
    plan = plan_fold(pub, est=estimate_tokens, b2_cap=rb.b2,
                     start_window=B2_CAP_START, shrink=shrink)
    tail = chrono_bytes(pub, (), plan.window)
    parts = {"B1": _est(fold_body(pub, plan.folded_days), cfg.tokens),
             "B2": _est(tail, cfg.tokens)}
    body = chrono_bytes(pub, plan.folded_days, plan.window)
    return body + "\n\n" + _status_card(percept, pub), plan, parts


def _status_card(percept: Percept, pub: tuple) -> str:
    """B0: the facts a moderator would announce. Rewritten per day, never per turn.

    Rewriting the head of region B flushes the cached tail, so this block must change at
    most once per day — at the day checkpoint, never mid-speech (plan §5).
    """
    alive = [s for e in pub if e.kind == Kind.GAME_START for s in e.payload.get("seats", ())]
    day = percept.events[-1].day if percept.events else 1
    deaths = [e for e in pub if e.kind == Kind.DEATH]
    out = [f"== 局况 ==\n第{day}天。"]
    if alive:
        out.append(f"开局座位：{'、'.join(str(s) for s in alive)}号。")
    if deaths:
        out.append("已出局：" + "、".join(
            f"{e.payload.get('seat')}号(第{e.day}天{e.payload.get('cause_zh') or e.payload.get('cause','')})"
            for e in deaths) + "。")
    else:
        out.append("无人出局。")
    return "\n".join(out)


def _accusation_lines(card: str) -> int:
    """How many accusation lines a rendered belief card carries — counted from its own bytes.

    `render_card` opens with `第N天。`, so every claim line is preceded by a newline. The table
    header and the seat's own check record are not lines of this kind, which is what makes the
    number mean "what the model stopped being told" rather than "how much shorter the block got".
    """
    return card.count("\n- [")


def _region_c(
    percept: Percept,
    *,
    seat_role: str,
    persona: PersonaParams,
    belief: BeliefState,
    legal: LegalSet,
    phase: Phase,
    cfg: Config,
    rb: RegionBudget,
    repeat_fragments: tuple[str, ...] = (),
    retry_note: str = "",
) -> tuple[str, str | None, int]:
    assigned = legal.assigned_act
    card = render_card(belief)
    # The C2 block is this header plus the card, and nothing else: the private-information
    # header below opens a new block, so `block_tokens` measures exactly this string. Binding
    # the header here instead of re-typing it is what keeps the knife and the reading on one
    # ruler — two spellings is how "C2 passed the audit and still ate the budget" happens.
    fact_head = "== 你目前掌握的事实 ==\n"
    parts = [
        f"== 你的座位 ==\n你是{percept.seat}号。你的身份是：{seat_role}。这条信息只有法官和你看得到。",
        render_persona_card(persona),
        fact_head + card,
    ]
    priv = [e for e in percept.events if e.visibility != "all" and e.kind != Kind.DEAL]
    if priv:
        parts.append("== 你的私有信息（只有你能看到，说出来就是自爆）==")
        parts += [render_line(e).replace(f"[{eid(e.seq)}]", f"[{eid(e.seq)}·私有]") for e in priv[-8:]]

    task = [PHASE_TASK_ZH.get(phase, "轮到你了。")]
    # Several phase texts say "必须执行指派给你的 act". If nothing was assigned, that
    # sentence points at nothing and the model invents an act to obey it.
    if not legal.assigned_act and any("指派" in t_ for t_ in task):
        task[0] += "（本轮不指派动作，从合法动作里自己选一个。）"
    if legal.acts:
        task.append(f"合法动作：{'/'.join(legal.acts)}"
                    + (f"（可用药：{'/'.join(legal.consumables)}）" if legal.consumables else ""))
        task.append(f"可选目标：{sorted(legal.targets)}" if legal.targets else "可选目标：无（本轮不需要目标）")
    if assigned:
        task.append(f"法官指派你本轮的 act = {assigned}。")
    if repeat_fragments:
        task.append("禁止复述以下已经出现过的说法：" + "；".join(frag[:24] for frag in repeat_fragments[:4]))
    if legal.reason_if_empty == "forced_nominate":
        task.append("提示：你连续两轮没有实质表态了，本轮必须点名一个座位。")
    parts.append("== 本轮任务 ==\n" + "\n".join(task))
    if retry_note:
        parts.append("== 上一轮被拒 ==\n" + retry_note)
    text = "\n\n".join(parts)
    if estimate_tokens(text, cfg.tokens) > rb.c_total or _est(fact_head + card, cfg.tokens) > rb.c_belief:
        # The card is the only block this knife touches: the persona card and this turn's task
        # are load-bearing (a thinning persona card is plan §7's P1/P3 defence quietly failing,
        # a thinning task block is the act gate), and the private-information block is the only
        # copy those night sentences have anywhere in the prompt.
        #
        # Two rulers pull this one lever. `c_total` asks "did the whole region fit"; `c_belief`
        # asks "did the card fit". A keep value must satisfy both, which costs nothing when only
        # one of them is over: slimming shrinks the card and therefore shrinks both numbers.
        claims = [c for c in belief.claims if c.actor != belief.observer]
        for keep in range(min(len(claims), 14) - 1, -1, -1):
            slim_card = render_card(belief, max_claims=keep)
            if _est(fact_head + slim_card, cfg.tokens) > rb.c_belief:
                continue
            slim = text.replace(card, slim_card)
            if estimate_tokens(slim, cfg.tokens) <= rb.c_total:
                return slim, assigned, _accusation_lines(card) - _accusation_lines(slim_card)
        # Nothing fits: hand over the card with the accusations cut and the check record kept.
        # A cap below that floor — of either ruler — is a batch arm's own choice, and the honest
        # residue is `region_tokens.C`/`C2` next to the cap the operator set, not one more claim
        # line handed over in search of a green audit cell.
        floor = render_card(belief, max_claims=0)
        return (text.replace(card, floor), assigned,
                _accusation_lines(card) - _accusation_lines(floor))
    return text, assigned, 0


def assemble(
    *,
    cfg: Config,
    percept: Percept,
    seat_role: str,
    persona: PersonaParams,
    belief: BeliefState,
    legal: LegalSet,
    phase: Phase,
    repeat_fragments: tuple[str, ...] = (),
    retry_note: str = "",
    shrink: int = 0,
) -> Prompt:
    rb = cfg.regions
    a = region_a(cfg.contract_version, cfg.rules_version)
    b, plan, parts = _region_b(percept, rb, cfg, shrink=shrink)
    c, assigned, dropped = _region_c(
        percept, seat_role=seat_role, persona=persona, belief=belief, legal=legal,
        phase=phase, cfg=cfg, rb=rb, repeat_fragments=repeat_fragments, retry_note=retry_note,
    )
    ta, tb, tc = estimate_tokens(a, cfg.tokens), estimate_tokens(b, cfg.tokens), estimate_tokens(c, cfg.tokens)
    total = ta + tb + tc
    over = total > cfg.tokens.absolute_ceiling
    sub = {name: 0 for name in BLOCK_OF_HEADER.values()}  # §5 的每一格都要有数
    for text in (b, c):  # B0 住在 B 段末尾，C1–C4 全在 C 段
        sub.update(block_tokens(text, cfg.tokens))
    return Prompt(
        messages=[{"role": "system", "content": a},
                  {"role": "system", "content": b},
                  {"role": "user", "content": c}],
        region_tokens={"A": ta, "B": tb, "C": tc, **parts, **sub},
        total_tokens=total,
        compactions=plan.rounds,
        folded_days=plan.folded_days,
        window=plan.window,
        b2_over_cap=max(0, parts["B2"] - rb.b2),
        card_claims_dropped=dropped,
        over_ceiling=over,
        shrink=shrink,
        assigned_act=assigned,
        legal_acts=legal.acts,
        legal_targets=tuple(sorted(legal.targets)),
    )


def payload_for_log(p: Prompt, generation: dict[str, Any] | None = None) -> dict[str, Any]:
    """What goes into `Event.request`. A whitelist, and `headers` is not in it.

    `generation` is merged in rather than guessed here because the actor owns those
    numbers — only `LlmActor` knows which temperature rung and which `max_tokens` budget it
    actually sent, and a request record that guesses them is worse than one that omits them.
    """
    out = {
        "messages": p.messages,
        "region_tokens": p.region_tokens,
        "total_tokens_est": p.total_tokens,
        "compactions": p.compactions,
        "b2_over_cap": p.b2_over_cap,
        "card_claims_dropped": p.card_claims_dropped,
        "shrink": p.shrink,
        "assigned_act": p.assigned_act,
    }
    out.update(generation or {})
    return out
