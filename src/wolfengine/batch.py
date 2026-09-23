"""M7 批次层：N 局 × K 配置、配对 deal seed、run_manifest、canary 前后夹。

这一层只负责**产出可配对的证据**，不做任何结论：结论在 `compare`，统计在 `report`。
分开的理由是配对这件事一旦坏了，症状是 p 值变小而不是报错——第 i 局两个配置必须同一副
牌（`deal_seed` 派生出发牌、座位序与夜晚 RNG 流），否则 McNemar 比较的是两组巧合。

canary 的探针执行器是注入的 async callable，不是内置的 LLM 调用：端点现在关着，而
"探针不一致就整批作废"这条逻辑必须能在端点下线时被验证——R7 的真实场景恰恰是"批跑到一半
被人重启换了权重"，那种时候最需要的就是这段代码已经是对的。
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field, fields as dc_fields, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from . import metrics, report, roles
from .actors import MockActor
from .config import Config
from .events import DAMAGE_WORDS
from .game import DRAW_DAY_LIMIT, play

PASSIVE_ACTS = ("listen", "align")

# plan §8 第 5 条的五条固定探针。答案对不对不归这里管——这里只回答"批首和批尾是不是同一个
# 模型"，那是对自身的一致性问题，不需要金答案。
CANARY_PROMPTS: tuple[tuple[str, str], ...] = (
    ("arith", "计算 37*11+6，只输出数字，不要任何解释。"),
    ("logic", "甲说乙在撒谎，乙说丙在撒谎，丙说甲和乙都在撒谎。三人中恰有一人说真话，"
              "说真话的是谁？只输出甲、乙或丙。"),
    ("insult3", "用三种互不相同的中文说法指责3号玩家，每句一行，不要解释。"),
    ("json_only", '只输出一个 JSON 对象 {"ok": true}，不要 markdown 围栏，不要其它字符。'),
    ("summary", "用一句话说明《红楼梦》讲的是什么。"),
)

ProbeFn = Callable[[str], Awaitable[dict[str, Any]]]
NO_CANARY: ProbeFn | None = None


class BadOverride(ValueError):
    """`--set` 打错一个字母就必须停下来：被静默忽略的 override 会让"两臂只差一根轴"变成谎话。"""


class BatchAborted(RuntimeError):
    """开局之前的失败（探针跑不动、配置冲突）。日志一条都不该已经写下。"""


@dataclass(frozen=True)
class Arm:
    name: str
    cfg: Config
    overrides: tuple[str, ...] = ()


@dataclass
class BatchResult:
    out_dir: Path
    pair_keys: list[int]
    n_logs: int
    terminal: str
    canary: dict[str, Any]
    rows: list[dict[str, Any]] = field(default_factory=list)


def apply_overrides(cfg: Config, overrides: Mapping[str, Any]) -> Config:
    """`{"regions.b2": 900}` → a Config with that nested leaf changed.

    Leaves only: `--set A.regions=...` would replace a whole budget table with a dict and the
    type check below would have nothing to compare against.
    """
    out = cfg
    for path, value in overrides.items():
        path = str(path)
        if path in cfg.inert_leaves:
            # 顶层的同类拒绝在 `_set_path` 里，那里只能看见一个字段名；点号路径要往下走两层才
            # 分得清 `regions.b0` 和 `regions.b2`，所以这一格停在门口。
            raise BadOverride(f"{path}: 这一格后面没有代码——plan §5 的预算表写着它，"
                              f"src/ 里却没有任何模块读它，改它只动 `config_hash`"
                              f"（所以也不能当处理轴）")
        out = _set_path(out, path.split("."), path, value)
    return out


def _set_path(obj: Any, parts: list[str], path: str, value: Any) -> Any:
    head, *rest = parts
    allowed = getattr(obj, "axis_fields", None)
    if allowed is not None and hasattr(obj, head) and head not in allowed:
        # Two different reasons share this one `if`, and saying the wrong one sends the reader
        # to the wrong place: `actor_kinds` is protected because changing it changes what the
        # batch *is*, `enable_sheriff` is refused because there is no code behind it to change.
        if head in getattr(obj, "inert_fields", ()):
            raise BadOverride(f"{path}: {head} 后面没有代码——src/ 里没有任何模块读它，改它只动"
                              f"`config_hash`，九个人玩的还是同一套规则（所以也不能当处理轴）")
        raise BadOverride(f"{path}: {head} 不是可比字段——它是这一批数据的身份，不是处理"
                          f"（plan §8 第 2 条）")
    if not hasattr(obj, head):
        raise BadOverride(f"{path}: 配置里没有 {head} 这一项{_near(head, obj)}")
    cur = getattr(obj, head)
    if rest:
        if not hasattr(cur, "__dataclass_fields__"):
            raise BadOverride(f"{path}: {head} 不是嵌套表，不能再往下走")
        return replace(obj, **{head: _set_path(cur, rest, path, value)})
    if hasattr(cur, "__dataclass_fields__"):
        raise BadOverride(f"{path}: 只能设叶子字段，不能整块替换 {head}")
    if not _type_ok(cur, value):
        raise BadOverride(f"{path}: 类型不符（现为 {type(cur).__name__}，收到 {value!r}）")
    if head == "seat_count":
        # 这份代码里存在哪些板子，只有 `roles` 知道——所以这里问它一次，不把 9 抄成第二份。
        # 从前 `--set A.seat_count=5` 过了类型校验，然后在开局发牌时炸出 43 行栈：命令写错了却
        # 拿到 rc 1，而批次目录已经建出来了。
        try:
            roles.board_for(value)
        except ValueError as e:
            raise BadOverride(f"{path}: {head} 只能是这份代码里存在的板子——{e}") from e
    return replace(obj, **{head: list(value) if isinstance(cur, tuple) and isinstance(value, list)
                           else value})


def _near(head: str, obj: Any) -> str:
    """A typo'd `--set` is the one place a batch can be silently mis-specified, so the error
    has to carry the spelling it meant."""
    import difflib

    names = ([f.name for f in dc_fields(obj)] if hasattr(obj, "__dataclass_fields__") else [])
    hit = difflib.get_close_matches(head, names, n=1, cutoff=0.5)
    return f"（是不是 {hit[0]}？）" if hit else ""


def _type_ok(old: Any, new: Any) -> bool:
    if isinstance(old, bool) or isinstance(new, bool):
        return isinstance(new, bool)
    if isinstance(old, (int, float)):
        return isinstance(new, (int, float))
    if isinstance(old, str):
        return isinstance(new, str)
    if isinstance(old, tuple):
        return isinstance(new, (tuple, list))
    return type(old) is type(new)


async def _probe_pass(fn: ProbeFn) -> list[dict[str, Any]]:
    out = []
    for pid, prompt in CANARY_PROMPTS:
        got = await fn(prompt)
        out.append({"id": pid, "prompt": prompt, **got})
    return out


async def _head(fn: ProbeFn | None, *, when: str) -> tuple[list[dict[str, Any]], str]:
    """One canary pass, plus the note saying why it did not happen. The note is not
    decoration: `compare` has to tell "no drift detected" apart from "nobody looked"."""
    if fn is None:
        return [], f"{when}未跑 canary 探针，本批无法排除端点漂移"
    try:
        return await _probe_pass(fn), ""
    except Exception as e:  # noqa: BLE001 — probe failure = do not start spending
        raise BatchAborted(f"canary {when}失败：{type(e).__name__}: {str(e)[:160]}") from e


async def run_batch(arms: Sequence[Arm], *, games: int, seed0: int,
                    out_dir: str | Path, mock: bool = False, transport: Any = None,
                    canary: ProbeFn | None = NO_CANARY) -> BatchResult:
    """`games` paired runs per arm, one file per run, one manifest for the batch."""
    if not arms:
        raise BatchAborted("没有配置臂")
    if len({a.name for a in arms}) != len(arms):
        raise BatchAborted("配置臂重名")
    out = Path(out_dir)
    pair_keys = [seed0 + i for i in range(games)]
    head, head_note = await _head(canary, when="批首")
    if not head:
        out.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for arm in arms:
        arm_dir = out / arm.name
        for seed in pair_keys:
            actors = _mock_table(arm.cfg, seed) if mock else None
            res = await play(cfg=arm.cfg, deal_seed=seed, out_dir=arm_dir,
                             actors=actors, transport=None if mock else transport)
            rows.append({"arm": arm.name, "path": str(res.path), "game_id": res.game_id,
                         "deal_seed": seed, "terminal": res.terminal, "winner": res.winner,
                         "days": res.days, "fallbacks": res.fallbacks, "retries": res.retries,
                         # The verdict next to the count it came from: `fallbacks` alone makes a
                         # reader re-apply a threshold, and the threshold they apply is whatever
                         # `Config()` says today, not what this batch was played under.
                         "degraded": res.degraded,
                         "context_overflows": res.context_overflows,
                         "completion_tokens": res.completion_tokens,
                         "wallclock_s": round(res.wallclock_s, 2)})

    # 批尾探针失败是另一回事：钱已经花完了。抛异常会留下一堆没有 manifest 的日志——compare
    # 只能报 JSONDecodeError，既不能用也不能归档。所以把失败记成一种终态，让闸门去拒绝它。
    try:
        tail, lost = await _head(canary, when="批尾")
    except BatchAborted as e:
        tail, lost = [], str(e)
    verdict = ({"terminal": "SKIPPED", "drifted": [], "ok": None, "note": head_note}
               if canary is None else
               {"terminal": "CANARY_LOST", "drifted": [], "ok": None, "note": lost}
               if lost else report.canary_verdict(head, tail))
    terminal = verdict["terminal"] if verdict["terminal"] not in ("SKIPPED", "ok") else "ok"
    kinds = ["mock" if mock else "llm"]
    man: dict[str, Any] = {
        "kind": "wolf-batch v1",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "games": games, "seed0": seed0, "pair_keys": pair_keys,
        "arms": {a.name: {"config_hash": a.cfg.config_hash(), "overrides": list(a.overrides),
                          "config": json.loads(json.dumps(a.cfg.to_dict(), default=list)),
                          "model": a.cfg.model, "base_url": a.cfg.base_url,
                          "contract_version": a.cfg.contract_version,
                          "rules_version": a.cfg.rules_version} for a in arms},
        # Written now because `compare` must be able to answer "was this whole table models?"
        # without anyone remembering which runs were real (plan §十五).
        "actor_kinds": kinds, "synthetic": kinds != ["llm"],
        "model": arms[0].cfg.model, "base_url": arms[0].cfg.base_url,
        "canary": {"head": head, "tail": tail, **verdict},
        "n_logs": len(rows), "rows": rows,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "run_manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    if terminal not in ("ok", "SKIPPED"):
        (out / "drift.md").write_text(_drift_md(man, out), encoding="utf-8")
    return BatchResult(out_dir=out, pair_keys=pair_keys, n_logs=len(rows),
                       terminal=terminal, canary=man["canary"], rows=rows)


def _mock_table(cfg: Config, seed: int) -> dict[int, MockActor]:
    """Same seed → same stand-in behaviour in both arms, so a mock batch differs only by config."""
    return {s: MockActor(s, synthesize=True, rng=random.Random(seed * 100 + s))
            for s in range(1, cfg.seat_count + 1)}


def _drift_md(man: dict[str, Any], d: Path) -> str:
    v = man["canary"]
    L = ["# 端点漂移（本批作废）\n",
         f"- 判定：`{v['terminal']}`，异常探针：{'、'.join(v['drifted']) or '（无）'}",
         f"- 说明：{v.get('note') or '批首与批尾探针不一致'}"]
    if v.get("latency_ratio") is not None:
        L.append(f"- 中位延迟比（尾/首）：{v['latency_ratio']}，阈值 {v.get('threshold')}")
    L += [f"- 本批目录：`{d}`",
          f"- 重新出报告：`wolf compare {d} --axis ...`\n",
          "| 探针 | 批首答案 | 批尾答案 | 批首 s | 批尾 s |", "|---|---|---|---|---|"]
    tail = {str(p["id"]): p for p in v["tail"]}
    for p in v["head"]:
        q = tail.get(str(p["id"]), {})
        L.append(f"| {p['id']} | {str(p.get('answer'))[:40]!r} | {str(q.get('answer'))[:40]!r} "
                 f"| {p.get('latency_s')} | {q.get('latency_s')} |")
    L.append(f"\n整批标 `{v['terminal']}`：这些日志不能进入任何对比结论（plan §8 第 5 条、R7）。")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------- read back
def read_arm(arm_dir: str | Path) -> list[dict[str, Any]]:
    """Every trajectory in one arm's directory, as the pairing needs it."""
    out: list[dict[str, Any]] = []
    for p in sorted(Path(arm_dir).glob("*.jsonl")):
        if p.name.endswith(".prompts.jsonl"):
            continue
        g = metrics.read_game(p)
        out.append({"path": str(p), "game_id": g.game_id, "terminal": g.terminal,
                    "winner": g.winner, "days": g.days,
                    "deal_seed": g.meta.get("deal_seed"),
                    "config_hash": g.meta.get("config_hash"),
                    "model": g.meta.get("model"),
                    "actor_kinds": g.meta.get("actor_kinds"),
                    "started_utc": g.meta.get("started_utc"),
                    "is_synthetic": g.is_synthetic})
    return out


def game_rates(g: metrics.Game) -> dict[str, tuple[int, int]]:
    """Per-game (numerator, denominator) for the per-utterance metrics M7 compares.

    Cluster sizes are returned per game rather than flattened into one pool because the
    bootstrap resamples *games*: 90 speeches from one board share a death order, a deal and a
    context length, and treating them as independent would understate the variance by √deff.
    """
    sp = [e for e in metrics.speeches(g.events) if e.payload.get("text")]
    turns = metrics.decisions(g.events)
    uncited = sum(1 for e in sp if (e.payload.get("meta") or {}).get(
        "citation_stats", {}).get("uncited"))
    refused = sum(1 for e in turns if e.attempts)
    passive = sum(1 for e in turns
                  if e.payload.get("act") in PASSIVE_ACTS
                  and not metrics.mentions_seat(str(e.payload.get("text") or "")))
    return {"uncited_speech": (uncited, len(sp)), "refused_turn": (refused, len(turns)),
            "passive_turn": (passive, len(turns))}


def compare_games(games_a: list[metrics.Game], games_b: list[metrics.Game]) -> dict[str, Any]:
    """The arithmetic of a two-arm comparison, over logs whose pairing is already established."""
    pairs: list[tuple[str | None, str | None]] = []
    # Why a pair leaves the denominator, counted apart in the same pass that drops it: "the server
    # died" and "nine seats played to the cap without a winner" are different findings, and one
    # merged count would let an outage batch read as a tie batch. Classifying here rather than
    # re-deriving from `n_dropped` is what keeps the two buckets adding up to it. An aborted
    # side dominates when a pair has both.
    draws = others = 0
    torn: list[str] = []
    for g, h in zip(games_a, games_b):
        a = g.winner if g.terminal in metrics.DECISIVE else None
        b = h.winner if h.terminal in metrics.DECISIVE else None
        pairs.append((a, b))
        if a is not None and b is not None:
            continue
        terms = (g.terminal, h.terminal)
        if any(t not in metrics.DECISIVE and t != DRAW_DAY_LIMIT for t in terms):
            others += 1
        else:
            draws += 1
        # A dropped pair whose file lost its last line is a *file* finding, not a play finding: the
        # line that goes missing is usually `GAME_OVER`, so `terminal` degrades to `unfinished` and
        # "中断或未完" would blame the model for someone's `kill -9`. Counted apart, not as a third
        # bucket — this pair was already out of the denominator for having no winner, and the
        # pre-registered denominators (plan §8) do not move because a cause got a name.
        torn += [f"{s.path.parent.name}/{s.path.name}" for s in (g, h) if s.torn_extent["lines"]]
    win = report.paired_win_test(pairs)
    win["n_dropped_draw"], win["n_dropped_other"] = draws, others
    win["n_dropped_torn"], win["dropped_torn_files"] = len(torn), sorted(set(torn))
    utter: dict[str, Any] = {}
    for metric in game_rates(games_a[0]) if games_a else {}:
        a = [game_rates(g)[metric] for g in games_a]
        b = [game_rates(h)[metric] for h in games_b]
        unpaired = report.cluster_bootstrap_rate_diff(a, b, paired=False)
        body = report.cluster_bootstrap_rate_diff(a, b)
        body["deff_gain"] = round((unpaired["ci_halfwidth"] or 0) / body["ci_halfwidth"], 2) \
            if body.get("ci_halfwidth") else None
        utter[metric] = body
    return {"win": win, "utterance": utter, "n_pairs": len(pairs)}


def degraded_games(games: Sequence[metrics.Game]) -> dict[str, Any]:
    """plan §143 的点名表：哪几局退化、阈值是多少、还有几局根本没判过。

    `ids`, not a count, because the only thing a name buys the reader is the ability to open
    that transcript and look at what the engine did on the model's behalf — a count of "3 out
    of 40" is not actionable, and "3 out of 40" *with* the ids is the pre-registered rule
    rather than an after-the-fact curation. `unrecorded` is kept separate from `n`: a batch
    played before the field shipped must not report as a healthy one.
    """
    named = [g.game_id for g in games if g.degraded_game]
    thresholds = sorted({g.degraded_threshold for g in games
                         if g.degraded_threshold is not None})
    return {"n": len(named), "ids": named,
            "unrecorded": sum(1 for g in games if g.degraded_game is None),
            # None when the logs disagree, which is itself worth seeing rather than hiding
            # behind whichever threshold happened to sort first.
            "threshold": thresholds[0] if len(thresholds) == 1 else None}


def numbering_damage(games: Sequence[metrics.Game]) -> dict[str, Any]:
    """批次侧的编号破损读数：有几局的编号不是引擎写出来的，分别是哪几个文件。

    点名用**路径**不用 `game_id`，理由不是"文件名的区分度更高"——实测两臂同 seed 的两个文件连
    文件名都一样（`A/` 与 `B/` 下都是 `<utc>_g00000005.jsonl`）。理由是 #54 那一轮的发现本身：
    `game_id` 是**文件里的一个字段**，而这一格要报的恰恰是"这个文件被人改过"，拿被改对象内部的
    标签去指它，正是"两局 `cat` 在一起、页眉照着后一条 manifest 报错局号"那个错的形状。文件名
    还带着这一次运行的 utc，指得动"是哪一批里的哪一个字节"。
    `n` 按文件计、三种损伤分开合计：一次编辑就能同时造出缺号和重号（把 6 改成 7），把两项相加
    会把一份文件说成两局。算术本身在 `events.seq_damage`，这里只做归约。
    """
    per = [g.seq_damage for g in games]
    hit = [g.path.name for g, d in zip(games, per) if any(d.values())]
    return {"n": len(hit), "files": hit,
            **{key: sum(d[key] for d in per) for key, _, _ in DAMAGE_WORDS}}


def truncated_tails(games: Sequence[metrics.Game]) -> dict[str, Any]:
    """批次侧的"末行被砍"读数：有几局的最后一行没能读成事件，分别是哪些文件。

    算术只有一只手：`Game.torn_extent` 转交 `events.torn_extent`，`torn_notice` 的那句话和
    `audit` 的那一格也从它取数，这里做的只是跨局归约。`n` 按**文件**计，`lines`/`chars` 单独留着
    ——今天 `n == lines` 恒成立（末行只容忍一行，两行算损坏、根本读不进来），但一旦有人放宽容忍，
    它们就是两件事，这一格得跟着变。

    为什么这一格要进报告：一局**打完了**的日志，末行就是 `game_over`，砍掉它就砍掉了 `terminal`。
    批次过去只看 `terminal`，于是"这份文件被人生砍剩 70 个字节"在报告里长成"这局没打完"——把文件
    的问题记成模型的行为，正是 #39 对端点拒答做过的那件事。
    """
    per = [g.torn_extent for g in games]
    hit = [g.path.name for g, d in zip(games, per) if d["lines"]]
    return {"n": len(hit), "files": hit,
            "lines": sum(d["lines"] for d in per), "chars": sum(d["chars"] for d in per)}


def region_budget_by_arm(games: Sequence[metrics.Game]) -> dict[str, Any]:
    """One arm's region excess, reduced from the caps each game recorded in its own log.

    A per-game readout is the right shape for `wolf audit` and the wrong shape for a batch: an
    operator with eighty files needs the arm's worst overshoot and how many games share it, and
    neither survives a `jq` pipeline someone reruns differently. This is that reduction, with the
    same rule as `degraded_games` — a missing ruler is not a clean measurement:

    - no game in the arm recorded `meta.regions` → `worst_over` is None, `n_without_caps` says so;
    - two different `meta.regions` in one arm → None as well. Excess is defined relative to a cap,
      and averaging two caps answers a question nobody asked. (Normally impossible: the caps are
      part of `config_hash`, so a mixed directory dies at the hash gate first. Reaching this
      branch means someone edited a log.)
    """
    per = [metrics.region_budget_check(g) for g in games]
    capped = [p for p in per if p["caps"] is not None]
    distinct = len({json.dumps(p["caps"], sort_keys=True, ensure_ascii=False) for p in capped})
    complete = bool(capped) and distinct == 1
    worst, over = {}, {}
    for region in metrics.REGION_CAP_KEYS:
        vals = [p["worst_over"][region] for p in capped if p["worst_over"][region] is not None]
        worst[region] = max(vals) if complete and vals else None
        over[region] = sum(1 for p in capped if p["worst_over"][region]) if complete else None
    witness = [p["b2_witness_agrees"] for p in per]
    thinned = [p["card_prompts_thinned"] for p in per]
    cuts = [p["card_worst_claims_dropped"] for p in per]
    return {
        "n_games": len(games),
        "n_without_caps": len(per) - len(capped),
        "distinct_caps": distinct,
        "worst_over": worst if complete else None,
        "games_over": over if complete else None,
        # None only when no game in the arm had a witness to check: 0 disagreements from a batch
        # that was never cross-checked would be the good news nobody measured.
        "witness_disagreements": None if all(w is None for w in witness)
        else sum(1 for w in witness if w is False),
        # Same null-vs-zero rule one level down. These two do *not* depend on `complete`: the card
        # count needs no cap, so an arm whose logs have no `meta.regions` still has an answer for
        # "was any belief card thinned" — and throwing it away with the excess cells would be a
        # reading destroyed by an unrelated missing ruler.
        "card_prompts_thinned": None if all(x is None for x in thinned)
        else sum(x for x in thinned if x),
        "card_worst_claims_dropped": None if all(x is None for x in cuts)
        else max((x for x in cuts if x is not None), default=0),
    }


# -------------------------------------------------------------------------------- compare
def prefix_cache_by_arm(games: Sequence[metrics.Game]) -> dict[str, Any]:
    """One arm's prefix-cache reuse, pooled over every timed call of every game in the arm.

    Pooled, not averaged. §5's discount is a number of tokens the server skipped, so the arm's
    answer is Σcached ÷ Σprompt; averaging per-game ratios instead would let a game that made one
    extra retried call vote with the same weight as a game that made fifty. The division itself is
    `metrics.prefix_cache_reuse`'s, so the only new arithmetic here is *which* calls enter the pool
    — one predicate, one source, and the per-file cell in `wolf audit` keeps reading the same one.

    `n_games_silent` says how many games contributed no answer at all. An arm reading 0.09 because
    one long game stayed quiet while four reported is a different finding from an arm the endpoint
    never answered, and only the second one is a claim about §5's economics.
    """
    cell = metrics.prefix_cache_reuse([e for g in games for e in g.events])
    cell["n_games"] = len(games)
    cell["n_games_silent"] = sum(1 for g in games
                                 if metrics.prefix_cache_reuse(g.events)["reported"] == 0)
    return cell


def compare(batch_dir: str | Path, *, axis: Sequence[str] = ()) -> dict[str, Any]:
    """Refuse first, conclude last. Every gate below exists because the run it blocks is plausible.

    Order is load-bearing: the axis and integrity gates cost nothing and catch the two ways a
    batch lies most often (an undeclared second difference, and logs that are not from this
    manifest). The endpoint-drift gate comes before the synthetic one because a drifted batch
    is worse than a mock one — a mock batch is honest about proving plumbing.
    """
    d = Path(batch_dir)
    man = json.loads((d / "run_manifest.json").read_text(encoding="utf-8"))
    names = sorted(man["arms"])
    if len(names) != 2:
        return _reject(d, man, "NEEDS_TWO_ARMS",
                       f"compare 需要恰好两个配置臂，本批有 {len(names)} 个：{'、'.join(names)}")
    a, b = names
    bad = _hash_integrity(d, man, names)
    if bad:
        return _reject(d, man, "HASH_MISMATCH",
                       "日志里记的 config_hash 与 manifest 不符，说明目录混进了别的批次的文件："
                       + "；".join(bad[:5]))
    diff = report.axis_diff(man["arms"][a]["config"], man["arms"][b]["config"], axis=axis)
    if not diff.ok:
        # `axis` rides along as an object because "which keys leaked" is a question a caller
        # asks programmatically (to fix the batch), not only a line to read in markdown.
        return _reject(d, man, "AXIS_VIOLATION", diff.render(
            man["arms"][a]["config_hash"], man["arms"][b]["config_hash"]), axis=diff)
    if not diff.diffs:
        return _reject(d, man, "IDENTICAL_ARMS",
                       "两臂配置完全相同：这一批测的是端点噪声，不是处理效应。"
                       "要看噪声就该把它当噪声报告，而不是给一个 p=1 的检验。")
    drift = man.get("canary", {})
    if drift.get("terminal") not in ("ok", "ran", "SKIPPED"):
        # 终态原样上报：INVALID_DRIFT（换了权重）和 CANARY_LOST（半路挂了）要修的东西不一样，
        # 把它们压成同一个码就等于把诊断丢了。
        return _reject(d, man, drift.get("terminal", "INVALID_DRIFT"),
                       f"批首/批尾 canary 未通过（{drift.get('terminal')}，"
                       f"异常探针 {'、'.join(drift.get('drifted') or []) or drift.get('note')}），"
                       f"整批不可比。详见 {d / 'drift.md'}")
    rows_a, rows_b = read_arm(d / a), read_arm(d / b)
    pair_err = _pairing_error(rows_a, rows_b)
    if pair_err:
        return _reject(d, man, "PAIRING_BROKEN", pair_err)
    games_a = [metrics.read_game(r["path"]) for r in rows_a]
    games_b = [metrics.read_game(r["path"]) for r in rows_b]
    stats = compare_games(games_a, games_b)
    synth = [g for g in games_a + games_b if g.is_synthetic]
    if synth:
        stats["win"] = {}
        return _reject(d, man, "SYNTHETIC_TABLE",
                       f"合成桌只验证管线，不产出结论：本批 actor_kinds={metrics.seat_kinds(synth)}，"
                       f"依据 {metrics.synthetic_basis(synth)}，永不进评测语料。", stats=stats)
    if drift.get("terminal") == "SKIPPED":
        stats["canary_note"] = drift.get("note")
    # M1's denominator is a batch, so `wolf audit` (one file) cannot produce it; this is the
    # only place that already has every game of both arms loaded. Without it `m1_win_rate` is
    # correct code nobody calls, and the CLI would report win rates only as a paired delta.
    stats["m1"] = {a: metrics.m1_win_rate(games_a), b: metrics.m1_win_rate(games_b)}
    # Same reason M1 lives here and not in `audit`: the gate is a claim about a batch. Per file
    # the criteria have no denominator worth quoting — a 9-call game's nearest-rank p95 *is* its
    # slowest turn — so a per-game "PASS" would be one outlier away from a "FAIL".
    stats["m3_verdict"] = {a: metrics.m3_gate_verdict(games_a),
                           b: metrics.m3_gate_verdict(games_b)}
    # Pointed at, never removed: the exclusion rule was declared before the batch ran (plan
    # §143), so `compare`'s job is to name the games that tripped it, not to quietly shorten a
    # denominator it did not pre-register.
    stats["degraded"] = {a: degraded_games(games_a), b: degraded_games(games_b)}
    # Same "pointed at, never removed" rule applied to the files rather than the play: a log whose
    # `seq` anchors have a hole or a twin is a log someone edited, and every `[e77]` cited from
    # it — in this report, in the transcript, in a belief dump — is then ambiguous. Nothing about
    # the game got worse, so nothing gets dropped; but the report cannot keep naming addresses in a
    # file it knows is inconsistent without saying which file.
    stats["numbering"] = {a: numbering_damage(games_a), b: numbering_damage(games_b)}
    # `seq` damage says a file was edited; a torn last line says a file was *cut*, and the two are
    # not the same finding — a clean cut leaves the numbering perfectly monotonic (measured
    # 04:40:12Z: 编号破损 reported 0 on a file whose `GAME_OVER` had been chopped off). The play
    # didn't finish on paper only because someone's bytes are missing, so this one has to be said
    # where the drop is counted, not two sections away from it.
    stats["torn"] = {a: truncated_tails(games_a), b: truncated_tails(games_b)}
    # Same argument as M1/M3 one more time: the caps are per game but the question
    # "did this arm fit its budget" is about an arm, and `audit` only ever sees one file.
    stats["region_budget"] = {a: region_budget_by_arm(games_a),
                              b: region_budget_by_arm(games_b)}
    # One more time the same argument: the endpoint reports `cached_tokens` per call, `audit` sees
    # one file, and the sentence plan §5 is betting on — "this arm's geometry made the server skip
    # prefill" — is about an arm. Without this cell the discount is prose nobody can quote.
    stats["prefix_cache"] = {a: prefix_cache_by_arm(games_a), b: prefix_cache_by_arm(games_b)}
    stats["verdict"] = "OK"
    stats["markdown"] = _comparison_md(d, man, a, b, stats, axis)
    return stats


def _reject(d: Path, man: dict, verdict: str, why: str, **extra: Any) -> dict[str, Any]:
    return {"verdict": verdict, "markdown": _reject_md(d, man, verdict, why), "win": {},
            "utterance": {}, "why": why, **extra}


def _hash_integrity(d: Path, man: dict, names: Sequence[str]) -> list[str]:
    bad: list[str] = []
    for name in names:
        want = man["arms"][name]["config_hash"]
        for r in read_arm(d / name):
            if r["config_hash"] != want:
                bad.append(f"{name}/{Path(r['path']).name}: {r['config_hash']} ≠ {want}")
    return bad


def _pairing_error(rows_a: list[dict], rows_b: list[dict]) -> str:
    sa = [r["deal_seed"] for r in sorted(rows_a, key=lambda r: str(r["deal_seed"]))]
    sb = [r["deal_seed"] for r in sorted(rows_b, key=lambda r: str(r["deal_seed"]))]
    if len(set(sa)) != len(sa) or len(set(sb)) != len(sb):
        return "同一 deal_seed 在某臂里出现了两次，配对表不再是一局一对"
    if sa != sb:
        return f"两臂的 deal_seed 集合不同：A={sa[:5]}… B={sb[:5]}…"
    return ""


def _repro(d: Path, man: dict, axis: Sequence[str]) -> str:
    """The command that would have produced this batch, spelled out so it can be pasted.

    Two rules it had to be written against: `batch` takes `--out`, not a positional, and the
    line must carry `--mock` when the batch was synthetic — a "repro" that silently turns two
    free mock games into paid endpoint games is worse than no repro line at all.
    """
    arms = ",".join(sorted(man["arms"]))
    sets = [f"--set {name}.{k}={v}" for name in sorted(man["arms"])
            for k in man["arms"][name]["overrides"]
            for v in [report.flatten(man["arms"][name]["config"]).get(k, "?")]]
    parts = [f"wolf batch --out {d}", f"--configs {arms}", f"--games {man['games']}",
             f"--seed0 {man['seed0']}"]
    if man.get("synthetic"):
        parts.append("--mock")
    parts += sets
    return (f"复现：`{' '.join(parts)}`\n"
            f"重出本报告：`wolf compare {d} --axis {','.join(axis) or '<声明的处理轴>'}`")


def _reject_md(d: Path, man: dict, verdict: str, why: str) -> str:
    return "\n".join([
        f"# 对比结果：{verdict}\n",
        f"本批**不出结论**。原因：\n\n{why}\n",
        f"- 批次：`{d}`（{man.get('games')} 局 × {len(man.get('arms', {}))} 臂，"
        f"seed0={man.get('seed0')}）",
        f"- canary：`{man.get('canary', {}).get('terminal')}`",
        "", _repro(d, man, ()), ""])


def _cell(v: Any) -> str:
    return "—" if v is None else str(v)


def _m1_md(a: str, b: str, m1: dict[str, dict[str, Any]]) -> list[str]:
    """Each arm's absolute win/survival table — the paired test above says *whether the arms
    differ*, and cannot say what either arm's rate is. The note is printed verbatim from the
    metric rather than rewritten here, because it changes with the shape of the sample (all
    draws says "分母为空", decisive games say "仅作描述")."""
    L = ["## 胜负与存活（M1，各臂绝对值）\n",
         "| 臂 | 局数 | 有胜负 | 好人胜率 | 95% CI | 平均天数 |", "|---|---|---|---|---|---|"]
    for name in (a, b):
        m = m1[name]
        lo, hi = m["wilson95"]
        L.append(f"| {name} | {m['n_games']} | {m['n_decisive']} | {_cell(m['good_win_rate'])} "
                 f"| [{_cell(lo)}, {_cell(hi)}] | {_cell(m['mean_days'])} |")
    surv = "; ".join(f"{name} 角色存活 {m1[name]['by_role_survival'] or '—'}" for name in (a, b))
    return L + [f"- {surv}"] + [f"- {name}：{m1[name]['note']}" for name in (a, b)] + [""]


def _m3_md(a: str, b: str, m3: dict[str, dict[str, Any]]) -> list[str]:
    """Per-arm gate verdict, printed above the per-utterance table on purpose: if an arm never
    cleared the risk gate, every interval below describes a table that is not worth comparing,
    and the reader should hit that before the numbers, not after them.

    The verdicts are joined with `｜` rather than a space so the line is greppable across a stack
    of reports (`grep -h '｜'` finds every arm's verdict and nothing else)."""
    L = ["## M3 闸门（各臂预注册判据，plan §十 M3★）\n"]
    for name in (a, b):
        v = m3[name]
        L.append(f"- {name}｜{v['verdict']}")
        for key in metrics.M3_GATE:
            c = v["criteria"][key]
            mark = {True: "达标", False: "未达标", None: "无读数"}[c["ok"]]
            L.append(f"  - {key} = {_cell(c['value'])}（需 {c['comparator']} "
                     f"{c['threshold']}，n={c['n']}）{mark}")
        L.append(f"  - {v['note']}")
    return L + [""]


def _degraded_md(a: str, b: str, deg: dict[str, dict[str, Any]]) -> list[str]:
    """plan §143：退化局**点名，不剔除**。

    两件事都必须印出来。只印计数，读的人开不了那一局的转录，点名就没有意义；写上"不剔除"，
    是因为这一格存在的目的正是防止有人在看完结果之后决定"那三局不算了吧"——阈值是跑之前定的
    12，剔除如果跟着结果走，同一批数据就能被算出两个方向的结论。
    """
    L = ["## 退化局（预先声明的判定，只点名不剔除，plan §143）\n"]
    for name in (a, b):
        d = deg[name]
        rule = (f"fallback > {d['threshold']}" if d["threshold"] is not None
                else "这一臂的阈值不一致或未记录")
        line = f"- {name}：{d['n']} 局退化（{rule}）"
        if d["ids"]:
            line += "：" + "、".join(d["ids"])
        if d["unrecorded"]:
            line += f"；另有 {d['unrecorded']} 局日志没有这一格，未判定"
        L.append(line)
    L.append("上面这些局仍然在所有分母里：没有被剔除，只是被点名。")
    return L + [""]


def _numbering_md(a: str, b: str, num: dict[str, dict[str, Any]]) -> list[str]:
    """哪几份文件的 `seq` 锚点不是引擎写出来的。

    这份报告的每一个数字都可以被引用成 `[e77]`（转录、信念报告、本文件自己都是这个地址），一份
    有洞或有重号的日志就让那些地址悄悄含糊或悄悄不存在——那是对**文件**的断言，不是对这一局的断言，
    所以它跟着退化局那一节走：点名，不动分母。三种损伤分开列（`DAMAGE_WORDS` 那一套词），因为
    "手工删了一行"和"两局拼在一起"是两种要查的东西。
    """
    L = ["## 编号破损（`seq` 锚点，只点名不剔除）\n"]
    for name in (a, b):
        d = num[name]
        parts = [f"{label} {d[key]} {unit}" for key, label, unit in DAMAGE_WORDS if d[key]]
        line = f"- {name}：{d['n']} 局的编号不是引擎写出来的"
        if parts:
            line += f"（{'、'.join(parts)}）：" + "、".join(d["files"])
        L.append(line)
    L.append("这些局没有被从任何分母里拿走：编号是文件的问题，不是这一局算不算数的问题。")
    return L + [""]


def _torn_md(a: str, b: str, torn: dict[str, dict[str, Any]]) -> list[str]:
    """每一臂有几局的末行没能读成事件 —— 点文件名，带上行数与字节数。

    字节数取自 `events.torn_extent`，和 `replay` 那句话里的是同一个数；内容一个字不印，被砍的半行
    可能是狼聊。这一节挨着编号那一节放是有意的：同一份文件在编号那里报 0、在这里报 1，两节对着读
    才看得出"编号没破"不等于"文件没被人动过"。
    """
    L = ["## 末行截断（只点名不剔除）\n"]
    for name in (a, b):
        d = torn[name]
        line = f"- {name}：{d['n']} 局的末行没能读成事件"
        if d["n"]:
            line += f"（{d['lines']} 行、{d['chars']} 字节）：" + "、".join(d["files"])
        L.append(line)
    L.append("被砍掉的那一行通常落在 `game_over` 上，所以这些局同时也不在上面的 usable 对里——"
             "那是文件被砍了，不是这局没打完。这一节不额外剔除任何局，它只交代『没有赢家』那条理由"
             "是怎么来的。")
    return L + [""]


def _region_md(a: str, b: str, reg: dict[str, dict[str, Any]]) -> list[str]:
    """每臂一行区域预算：最坏超了多少，以及有几局一起超。

    最坏值单独一列不够。`worst_over.C = 85` 在"两局都压不过地板"和"四十一局里有一局改过提示词"
    之间是同一条读数，前者是配置错了，后者是某一次采样撞上了——只有局数能分开这两种。
    两者都来自同一臂自己的日志，不引外部配置：上限进 `meta.regions` 就是为了报告可以离开
    当时那个 shell 重算。
    """
    L = ["## 区域预算（各臂日志里自己记的上限，超出 token 数）\n",
         # The column names come out of the same list the cells are read from. A hand-typed
         # header is how this table shipped four titles over nine numbers when §5's five extra
         # rulers landed: markdown renders that silently, so the reader trusts the titles.
         "| " + " | ".join(["臂", "局数", "证人分歧", *metrics.REGION_CAP_KEYS]) + " |",
         "|" + "---|" * (3 + len(metrics.REGION_CAP_KEYS))]
    notes: list[str] = []
    for name in (a, b):
        r = reg[name]
        cells = [r["worst_over"][k] if r["worst_over"] else None
                 for k in metrics.REGION_CAP_KEYS]
        L.append(f"| {name} | {r['n_games']} | {_cell(r['witness_disagreements'])} | "
                 + " | ".join(_cell(v) for v in cells) + " |")
        if r["n_without_caps"]:
            notes.append(f"- {name}：{r['n_without_caps']} 局日志里没有 `meta.regions`，"
                         f"无上限读数（不是 0，是没人量过）")
        elif not r["worst_over"]:
            notes.append(f"- {name}：这一臂的日志里有两份上限（{r['distinct_caps']} 个互不相同的 "
                         f"meta.regions）——超额相对哪一把没有唯一答案，拒绝挑一个，这一行不给读数")
        if r["games_over"]:
            notes.append(f"- {name}：越界局数 "
                         + "/".join(str(v) for v in r["games_over"].values())
                         + f"（共 {r['n_games']} 局）")
    notes.append("- 证人分歧 = 逐条请求里 `request.b2_over_cap` 与从长度重算的 B2 超额对不上的局数。")
    # The scope of that sentence is the arms which have a witness at all. Summing across a `None`
    # and calling the result "both arms agree" is the one place in this table where a missing
    # reading can still turn into good news — the per-arm cells are already honest.
    armed = {n: reg[n]["witness_disagreements"] for n in (a, b)}
    silent = [n for n, v in armed.items() if v is None]
    measured = [n for n, v in armed.items() if v is not None]
    total = sum(armed[n] for n in measured)
    if not measured:
        notes.append("- 两臂的日志里都没有 `b2_over_cap`，这一栏整列没有读数。")
    else:
        if silent:
            notes.append(f"- 只有 {'、'.join(measured)} 臂有证人读数；"
                         f"{'、'.join(silent)} 臂的日志里没有 `b2_over_cap`，"
                         f"下面那句“全部对得上”管不着它。")
        if total:
            notes.append(f"- {'、'.join(measured)} 臂有 {total} 局的证人对不上：日志里的长度与发送时"
                         f"的判定不一致，先怀疑文件被改过，再怀疑引擎。")
        elif silent:
            notes.append(f"- {'、'.join(measured)} 臂有读数的局全部对得上。")
        else:
            notes.append("- 本批两臂的证人全部与长度一致。")
    # The C knife's witness, one line rather than a column: it counts lines, not tokens, and it
    # needs no cap beside it, so it stays readable on the arms the excess columns above had to
    # leave blank. A thinned card is a treatment — the model was handed fewer accusations than the
    # other arm's model read — and an arm-level table that shows only byte totals cannot say so.
    thinned = {n: reg[n]["card_prompts_thinned"] for n in (a, b)}
    if all(v is None for v in thinned.values()):
        notes.append("- 两臂的日志里都没有 `card_claims_dropped`（那一版装配器还没记这一格），"
                     "主张卡被动过没有 = 没有读数，不是 0。")
    else:
        worst = max((v for v in (reg[n]["card_worst_claims_dropped"] for n in (a, b))
                     if v is not None), default=0)
        notes.append("- 削过主张卡的 prompt："
                     + "、".join(f"{n} 臂 {'—' if thinned[n] is None else thinned[n]} 个"
                                 for n in (a, b))
                     + (f"（最狠的一条少发 {worst} 条指控）" if worst else "")
                     + "。被砍掉的指控模型永远看不到，比两臂的事实量时先看这一格。")
    return L + notes + [""]


def _prefix_md(a: str, b: str, pc: dict[str, dict[str, Any]]) -> list[str]:
    """每臂一行前缀缓存：分子分母都取自这一臂自己的调用，比值是池内相除。

    印 `—` 而不是 0.0000 是 `#70` 在单局那一格上立的判据，臂这一级不另立一套：端点没回答与
    端点回答了"一次都没复用"是两件事，把前者印成后者就是把 plan §5 的折扣读成模型的问题。
    局数单独一列同 `_region_md` 的理由：`reuse_ratio = 0.09` 在"一局长局没报"与"两局都没报"
    之间是同一条读数，只有下面那句注脚能分开它们。
    """
    L = ["## 前缀缓存（端点报回的 cached_tokens，按臂池内相除）\n",
         "| 臂 | 局数 | 调用 | 报了 | 没说 | 配不上 | cached | prompt | 复用率 |",
         "|" + "---|" * 9]
    notes: list[str] = []
    for name in (a, b):
        c = pc[name]
        L.append("| " + " | ".join([
            name, str(c["n_games"]), str(c["calls"]), str(c["reported"]), str(c["silent"]),
            str(c["unpairable"]), _cell(c["cached_tokens"]), _cell(c["prompt_tokens"]),
            "—" if c["reuse_ratio"] is None else f"{c['reuse_ratio']:.4f}"]) + " |")
        if c["n_games_silent"]:
            notes.append(f"- {name}：{c['n_games_silent']}/{c['n_games']} 局端点没报过 "
                         f"`cached_tokens`，那是『没问过它』不是『一次都没复用』。")
    notes.append("- 比值 = 这一臂全部配对调用的 Σcached ÷ Σprompt，不是各局比值取平均；"
                 "口径与 `wolf audit` 的 `prefix_cache` 同一只函数。")
    return L + notes + [""]


def _comparison_md(d: Path, man: dict, a: str, b: str, stats: dict,
                   axis: Sequence[str]) -> str:
    win = stats["win"]
    # 为什么这一对被丢弃，这句话以前只有两种说法，而砍掉末行是第三种：`中断或未完` 说的是一种模型
    # 行为，接住的却是一份被人生砍过的文件。归因要说在**这一句**里，不能推到两节之后去——读者是先
    # 在这里看到"没打完"的。新开一桶会动预注册的分母（plan §8），所以这里只给理由，不动计数。
    drop_reasons = ("没有赢家的局不进分母，两种原因分开数"
                    + (f"；其中 {win['n_dropped_torn']} 对的日志末行被砍，不是这局没打完："
                       + "、".join(win["dropped_torn_files"]) if win["n_dropped_torn"] else ""))
    L = [f"# 两配置对比：{a} vs {b}\n",
         f"- 配对局数 **{stats['n_pairs']}**（{man['games']} 局 × 同一 deal_seed）",
         f"- 声明的处理轴：{'、'.join(axis) or '（无）'}",
         f"- canary：`{man.get('canary', {}).get('terminal')}`"
         + (f"（{stats['canary_note']}）" if stats.get("canary_note") else ""),
         "", _repro(d, man, axis), "",
         "## 胜率（配对）\n", f"- {win['verdict']}",
         f"- usable 对 {win['n_usable']}/{win['n_pairs']}，被丢弃 {win['n_dropped']} 对"
         f"（平局 {win['n_dropped_draw']}、中断或未完 {win['n_dropped_other']}：{drop_reasons}）",
         f"- A 先手侧好人胜 {win['b']} 对、B 侧 {win['c']} 对\n",
         *_m1_md(a, b, stats["m1"]),
         *_degraded_md(a, b, stats["degraded"]),
         *_numbering_md(a, b, stats["numbering"]),
         *_torn_md(a, b, stats["torn"]),
         *_m3_md(a, b, stats["m3_verdict"]),
         *_region_md(a, b, stats["region_budget"]),
         *_prefix_md(a, b, stats["prefix_cache"]),
         "## 逐条率（按局 cluster bootstrap，B=2000）\n",
         "| 指标 | A | B | 差 | 95% CI | deff | 配对收益 |", "|---|---|---|---|---|---|---|"]
    for k, v in stats["utterance"].items():
        ci = "—" if v["ci"][0] is None else f"[{v['ci'][0]}, {v['ci'][1]}]"
        L.append(f"| {k} | {_cell(v['point_a'])} | {_cell(v['point_b'])} | {_cell(v['diff'])} | "
                 f"{ci} | {_cell(v['deff'])} | ×{_cell(v['deff_gain'])} |")
    L += ["",
          "表中的 — 表示这批数据给不出这一项（可重采样的局数不足，或两臂之间没有可测的差），不是 0。\n",
          "`deff` 是与 naive binomial 的方差比：>1 说明同局发言确实相关，naive 区间过窄。"
          "`配对收益` = 非配对 CI 宽度 / 本表 CI 宽度：>1 才是配对设计换来的东西；`×0.8` 这种小于 1"
          "的读数是这一批上配对让区间变宽了，那是一条结论，不是缺数据——缺数据印 `—`。\n",
          "每个数字都可在上面的复现命令里重算；本文件不含密钥，只含配置名与 LAN 地址。\n"]
    return "\n".join(L)
