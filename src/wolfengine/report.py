"""M7 的对比层：配对检验、按局 cluster bootstrap、treatment-axis 守卫、漂移 canary。

依赖方向上这一层不认识 `metrics`，也不认识端点：它只做"两组已经算好的数字能不能放在一起
比、比出来的可信度是多少"。胜率、per-utterance 率由 `metrics.py` 提供，配对关系由
`batch.py` 提供，这里把两者变成结论与拒绝。

四件事各自都有一个"会假装没事"的实现方式，所以每一件都写成断言而不是注释：

* 48 局的胜率 CI 半宽 ≈±14pp（plan §8 M1 自己写的），`mcnemar` 在 b+c<6 时必须自报统计力不足；
* 同局 90 条发言共享牌局/死法/上下文长度，**不是独立样本**，naive binomial 会低估方差
  √deff 倍，deff 从实测算出并随 CI 一起报（§8 第 4 条）；
* 两个配置差了几个字段就要拒绝比较，且逐项打印（§8 第 2 条）；
* 尾探针和首探针不一致 ⇒ 整批 `INVALID_DRIFT`，专治"局域网服务被别人重启换了权重"（R7）。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Mapping, Sequence

from .config import DRIFT_RATIO, FORBIDDEN_AXIS, INERT_FIELDS, INERT_LEAVES

# plan §8 第 3 条的原话阈值：低于这个不一致对数，p 值只是描述，不是检验。
MIN_DISCORDANT = 6

# `FORBIDDEN_AXIS` lives in config.py: `Config.axis_fields` (what `--set` may touch) and
# `axis_diff` (what a report may declare) are the two ends of one rule, and the rule has to be
# read from one place.


# ------------------------------------------------------------------ McNemar exact
def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact binomial on the discordant pairs, no normal approximation.

    At b+c=9 the approximation disagrees with the exact tail by a factor of two in the
    direction that flatters the finding, and 48 paired games is exactly that neighbourhood.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * tail)


def mcnemar(b: int, c: int, *, min_discordant: int = MIN_DISCORDANT) -> dict[str, Any]:
    """The test, plus the sentence that outranks it.

    `verdict` carries the underpowered warning *inside* it rather than in a sibling flag
    nobody reads: a report line that can be quoted without its caveat is a report line that
    will be quoted without its caveat.
    """
    p = mcnemar_exact(b, c)
    n = b + c
    underpowered = n < min_discordant
    verdict = (f"McNemar exact：不一致对 b={b}, c={c}, p={p:.4f}（α=0.05 "
               f"{'拒绝' if (not underpowered and p < 0.05) else '不拒绝'}同分布）")
    if underpowered:
        verdict += (f"。本批统计力不足（不一致对 {n} < {min_discordant}），"
                    "胜率仅作描述，不得作为处理效应的证据。")
    elif p < 0.05 and b + c < 20:
        verdict += "。注：不一致对仍少，效应量与显著性分别看。"
    return {"b": b, "c": c, "n_discordant": n, "p": round(p, 6),
            "underpowered": underpowered, "min_discordant": min_discordant,
            "verdict": verdict}


def paired_win_test(pairs: Sequence[tuple[str | None, str | None]],
                    *, min_discordant: int = MIN_DISCORDANT) -> dict[str, Any]:
    """McNemar over paired games: `pairs[i]` is (winner_A, winner_B) for deal seed *i*.

    A pair where either side failed to produce a faction winner — aborted, or the pre-registered
    `draw_day_limit` — is dropped from the denominator and **counted** —
    48 rows on the page with 31 actually compared is the difference between a batch and a
    story. Ties on the same faction are the "no discordance" case McNemar expects and stay in
    `n_usable` with b/c untouched.
    """
    usable = [(a, b) for a, b in pairs if a in ("good", "wolf") and b in ("good", "wolf")]
    b = sum(1 for a, c in usable if a == "good" and c == "wolf")
    c = sum(1 for a, cc in usable if a == "wolf" and cc == "good")
    out = mcnemar(b, c, min_discordant=min_discordant)
    out["n_pairs"] = len(pairs)
    out["n_usable"] = len(usable)
    out["n_dropped"] = len(pairs) - len(usable)
    return out


# --------------------------------------------- cluster bootstrap (per-utterance rates)
def _ratio(points: Sequence[tuple[int, int]], idx: Sequence[int]) -> float:
    den = sum(points[i][1] for i in idx)
    return sum(points[i][0] for i in idx) / den if den else 0.0


def _pct(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    pos = min(max(q, 0.0), 1.0) * (len(sorted_vals) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _boot(params: random.Random, k: int, reps: int) -> list[int]:
    return [params.randrange(k) for _ in range(reps)]


def _var(vals: Sequence[float]) -> float:
    """Sample variance (n−1), not population variance: with B=2000 replicates the difference
    is in the 4th decimal, but `deff` is a ratio a reader checks against 1, so the estimator
    named in the code is the one that matches the interval actually printed.
    """
    if len(vals) < 2:
        return 0.0
    mu = sum(vals) / len(vals)
    return sum((v - mu) ** 2 for v in vals) / (len(vals) - 1)


def cluster_bootstrap_rate_diff(a: Sequence[tuple[int, int]], b: Sequence[tuple[int, int]],
                                *, B: int = 2000, seed: int = 7, paired: bool = True,
                                alpha: float = 0.05) -> dict[str, Any]:
    """Rate difference across paired games. `paired=False` exists only to be compared with.

    Paired mode resamples *game indices* and uses the same draw for both arms, so the
    between-game spread the pairing was designed to cancel actually cancels. Turning it off
    is the mistake this function documents: the point estimate barely moves while the
    interval inflates until it straddles zero, which is how a real effect gets reported as
    "no difference found".
    """
    m = min(len(a), len(b))
    ka, kb = [x for x in a[:m] if x[1] > 0], [x for x in b[:m] if x[1] > 0]
    if len(ka) < 2 or len(kb) < 2 or len(ka) != len(kb):
        # Same keys as the full return below. A caller that has to guess which columns exist
        # before rendering is a caller that crashes on the 1-game batch — and the 1-game batch
        # is exactly what a half-finished night leaves behind.
        sa, sb = sum(n for _, n in ka), sum(n for _, n in kb)
        pa = sum(k for k, _ in ka) / sa if sa else None
        pb = sum(k for k, _ in kb) / sb if sb else None
        return {"point_a": None if pa is None else round(pa, 4),
                "point_b": None if pb is None else round(pb, 4),
                "diff": None if pa is None or pb is None else round(pb - pa, 4),
                "ci": [None, None], "ci_halfwidth": None, "naive_ci_halfwidth": None,
                "deff": None, "paired": paired, "B": B,
                "n_clusters": min(len(ka), len(kb)), "seed": seed, "excludes_zero": None,
                "note": "配对局数不足两局或两臂不对齐：只给点估计，不给区间"}
    rng = random.Random(seed)
    pa = sum(k for k, _ in ka) / sum(n for _, n in ka)
    pb = sum(k for k, _ in kb) / sum(n for _, n in kb)
    diff = pb - pa
    reps = sorted((_ratio(kb, idx) if paired else _ratio(kb, _boot(rng, m, m))) - _ratio(ka, idx)
                  for idx in (_boot(rng, m, m) for _ in range(B)))
    var_naive = (pa * (1 - pa) / sum(n for _, n in ka) + pb * (1 - pb) / sum(n for _, n in kb))
    lo, hi = _pct(reps, alpha / 2), _pct(reps, 1 - alpha / 2)
    return {"point_a": round(pa, 4), "point_b": round(pb, 4), "diff": round(diff, 4),
            "ci": [round(lo, 4), round(hi, 4)], "ci_halfwidth": round((hi - lo) / 2, 4),
            "naive_ci_halfwidth": round(1.959964 * math.sqrt(var_naive), 4),
            "deff": round(_var(reps) / var_naive, 2) if var_naive > 0 else None,
            "paired": paired, "B": B, "n_clusters": m, "seed": seed,
            "excludes_zero": lo > 0 or hi < 0,
            "note": ("配对重采样：同一局的两臂一起进样本，局间基线差被抵消。"
                     if paired else "非配对重采样：两臂各自抽局，只用于对照说明配值的价值。")}


# ------------------------------------------------------------- treatment-axis guard
def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Config → dotted leaf paths, so a nested budget edit is named `regions.b2`."""
    out: dict[str, Any] = {}
    data = obj if isinstance(obj, Mapping) else _as_mapping(obj)
    for k, v in data.items():
        path = f"{prefix}{k}"
        if isinstance(v, (Mapping,)) or hasattr(v, "__dataclass_fields__"):
            out.update(flatten(v, path + "."))
        elif isinstance(v, (list, tuple)) and any(isinstance(x, (dict, tuple, list)) for x in v):
            out[path] = repr(list(v))
        else:
            out[path] = list(v) if isinstance(v, tuple) else v
    return out


def _as_mapping(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "to_dict"):
        return dict(obj.to_dict())
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(obj)
    return dict(vars(obj))


@dataclass
class AxisDiff:
    """What actually differs between two configs, split into what may be claimed."""

    undeclared: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    rejected: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    inert: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    declared: tuple[str, ...] = ()
    diffs: dict[str, tuple[Any, Any]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.undeclared and not self.rejected and not self.inert

    def render(self, hash_a: str, hash_b: str) -> str:
        """The itemised list the refusal has to carry (plan §8 第 2 条).

        Hashes are passed in rather than pulled off the configs because `compare` runs on the
        *manifest's* recorded configs, and a bare 'not comparable' gets re-run tomorrow by the
        same person — naming the field is what makes the second run different from the first.
        """
        L = [f"config_hash: A={hash_a}  B={hash_b}",
             f"声明的处理轴: {', '.join(self.declared) or '（无）'}"]
        if self.rejected:
            L.append("禁止作为处理轴的差异（配对前提已失效，改任何白名单都不会放行）：")
            L += [f"  - {k}: A={v[0]!r} vs B={v[1]!r}" for k, v in sorted(self.rejected.items())]
        if self.inert:
            # 不是"配对前提"坏了，是这一格后面根本没有代码：修法是把代码补上，不是重跑批次。
            L.append("后面没有代码的差异（两臂玩的还是同一套规则，报告却说得出区别）：")
            L += [f"  - {k}: A={v[0]!r} vs B={v[1]!r}" for k, v in sorted(self.inert.items())]
        if self.undeclared:
            L.append("未声明的差异（要么加进 --axis，要么把它固定住）：")
            L += [f"  - {k}: A={v[0]!r} vs B={v[1]!r}" for k, v in sorted(self.undeclared.items())]
        if self.ok:
            L.append("除声明轴外无差异。")
        return "\n".join(L) + "\n"


def axis_diff(a: Any, b: Any, *, axis: Sequence[str] = ()) -> AxisDiff:
    """Compare two configs and enforce that only the declared axis moved."""
    fa, fb = flatten(a), flatten(b)
    declared = set(axis)
    unknown = [d for d in sorted(declared)
               if d not in fa and not any(k.startswith(d + ".") for k in fa)]
    diffs = {k: (fa.get(k), fb.get(k)) for k in set(fa) | set(fb) if fa.get(k) != fb.get(k)}
    rejected, undeclared, inert = {}, {}, {}
    for k, v in diffs.items():
        if k.split(".")[0] in FORBIDDEN_AXIS:
            rejected[k] = v
        elif k in INERT_FIELDS or k in INERT_LEAVES:
            # 声明了也不放行，跟 `model` 一样；但排在 `rejected` 之后判，两类的修法不同。
            # 这一格必须在这里挡，不能只挡在 `batch.apply_overrides` 的门口：`compare` 读的
            # 是 manifest 里记下来的配置，手改过的那一份不经过命令行。
            inert[k] = v
        elif not _axis_covers(declared, k):
            undeclared[k] = v
    for d in unknown:
        undeclared[f"--axis {d}(配置里没有这个字段)"] = (None, None)
    return AxisDiff(undeclared=undeclared, rejected=rejected, inert=inert,
                    declared=tuple(axis), diffs=diffs)


def _axis_covers(declared: set[str], key: str) -> bool:
    return any(key == d or key.startswith(d + ".") for d in declared)


# --------------------------------------------------------------------------- canary
def _norm(text: str) -> str:
    return " ".join(str(text).split()).strip().lower()


def canary_verdict(before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]],
                   *, drift_ratio: float = DRIFT_RATIO) -> dict[str, Any]:
    """Head-and-tail probe comparison: identical prompts, so any change is the endpoint's.

    An answer that changed is a different weight set regardless of timing, and a >1.5× move
    in median latency is the same threshold `metrics.m7_cost_profile` uses for its per-call
    drift check — two numbers for one phenomenon would mean neither is trusted.
    """
    tail = {str(p.get("id")): p for p in after}
    drifted: list[str] = []
    for head in before:
        pid = str(head.get("id"))
        got = tail.get(pid)
        if got is None:
            drifted.append(f"{pid}:missing")
            continue
        if _norm(got.get("answer")) != _norm(head.get("answer")):
            drifted.append(f"{pid}:answer")
    lb = median([float(p["latency_s"]) for p in before if p.get("latency_s")])
    la = median([float(p["latency_s"]) for p in after if p.get("latency_s")])
    ratio = (la / lb) if lb else 1.0
    if not (1 / drift_ratio <= ratio <= drift_ratio):
        drifted.append(f"latency×{ratio:.2f}")
    ok = not drifted
    return {"ok": ok, "drifted": drifted, "latency_ratio": round(ratio, 3),
            "n_probes": len(before), "threshold": drift_ratio,
            "terminal": "ok" if ok else "INVALID_DRIFT",
            "note": "canary 只跑固定 prompt；答案变了=权重变了，整批不可比（plan §8 第 5 条、R7）。"}
