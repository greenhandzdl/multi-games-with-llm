"""Engine configuration.

The API key VALUE never lives here — only the name of the environment variable that
holds it. Transport reads `os.environ[api_key_env]` at call time and fails loudly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

from .state import Phase


class ConfigError(RuntimeError):
    pass


# One number, two consumers: `metrics.m7_cost_profile` compares each call's measured latency
# against the fitted prediction, and `report.canary_verdict` compares a batch's tail probes
# against its head ones. If they disagreed on the threshold, a run could be flagged stale by
# one and clean by the other (plan §8 M7 and §8 第 5 条).
DRIFT_RATIO = 1.5


# Region token budgets (plan §5). These are treatment variables: changing them
# changes model behaviour, so they are part of config_hash.
@dataclass(frozen=True)
class RegionBudget:
    a_target: int = 1200
    a_hard: int = 2000
    b_steady: int = 3200
    b0: int = 400
    b1: int = 1200
    b2: int = 1500
    c_persona: int = 250
    c_belief: int = 450
    c_private: int = 400
    c_task: int = 350
    c_total: int = 1450


@dataclass(frozen=True)
class TokenBudget:
    target: int = 5800
    warn: int = 7500
    force_compact: int = 9000
    absolute_ceiling: int = 10000

    # Measured 2026-09-20 from usage.prompt_tokens on the live endpoint (see
    # docs/calibration.md §4). CJK is ~0.90 tokens/char, NOT the 1/1.6=0.63 the plan
    # assumed: a Chinese-heavy prompt is ~43% bigger in tokens than that estimate said,
    # which would have made the estimator under-predict and turn budget pressure into
    # HTTP 400s. ASCII ~0.22 tok/char (1 per 4.6) so /4 stays, and is conservative.
    tok_per_cjk: float = 0.95  # 0.90 measured + margin, an estimator must err large
    tok_per_ascii: float = 0.25  # measured 0.217; /4 keeps the safety margin


ActorKind = Literal["llm", "mock", "human"]

# 换掉这些字段等于换了被试或换了端点，"同一批数据只有一根处理轴"的前提就不成立了（plan §8
# 第 2 条、§十五）。放在这里是因为它属于配置本身：`report.axis_diff` 拒绝把它声明成轴，
# `batch.apply_overrides` 拒绝在命令行上覆盖它——两处共用一张名单，才会只漂移一次。
FORBIDDEN_AXIS = ("model", "base_url", "api_key_env", "actor_kinds")

# The second refusal class: not "you may not change this", but "there is nothing behind it to
# change". A field in here moves `config_hash` while every seat keeps playing the same rules —
# the exact shape of a comparison report that measures nothing. Both readers must see this one
# list: `batch.apply_overrides` stops it on the command line, `report.axis_diff` stops it when
# the manifest was edited by hand (`compare` never goes through the CLI).
INERT_FIELDS = ("enable_sheriff",)  # plan §13 要的是"默认关的 flag"，不是"能开的开关"
# 同一件事的嵌套版。plan §5 预算表里 B0 和 C1–C4 这五格以前也是这张名单的成员——装配器只报
# A/B/C/B1/B2 五段总长，那五格连"量出来是多少"都没有。现在 `assemble.block_tokens` 逐格量、
# `metrics.REGION_CAP_KEYS` 逐格减，它们就有了读者，因此按判据必须离开这张名单。
# 差别要留在这里说清楚：**只有 `c_belief`（C2）后面真有一刀**（装配器会削主张卡），
# `b0`/`c_persona`/`c_private`/`c_task` 四格改的是 `audit` 里那一格超额判定，不动发出去的字节——
# plan §5 的牺牲顺序 ⑤⑥ 不许在那里下刀。把它们当处理轴去比较两臂之前先读这一句。
# `tokens.warn`/`force_compact` 两格既没有刀也没有尺子（折叠读的是 `target` 和
# `absolute_ceiling`），所以它们留在这儿。
INERT_LEAVES = ("tokens.warn", "tokens.force_compact")

# 按"发言"计价的阶段：一段话和一个动作的长度上限是两个数（`max_tokens_speech` /
# `max_tokens_action`），而"这局要花多少 token"就是每次调用这两个数之一的求和。名单住在
# `Config.token_budget_for` 一处，因为读它的不止发请求的人：`--dry-run` 的成本普查也读它。
SPEECH_PHASES = frozenset({Phase.DAY_SPEECH, Phase.DAY_PK_SPEECH, Phase.LAST_WORDS})


@dataclass
class Config:
    # --- endpoint (non-secret; base_url is a LAN address and is safe to commit) ---
    base_url: str = "http://100.87.65.60:13000/v1"
    model: str = "gemma-4-26b-a4b-nvfp4"
    api_key_env: str = "WOLF_LLM_API_KEY"

    # --- generation ---
    # 全局默认温度：`temperature_ladder` 为空时每座取它。以前这个字段没有任何一处代码读过，
    # 而帮助文本和文档示例都教人拿它当处理轴（`--set B.temperature=0.6`）：两臂发出同一串
    # 字节，报告却写着"声明的处理轴：temperature"。见 `#76` 与 `temperature_for`。
    temperature: float = 0.9
    max_tokens_speech: int = 140
    max_tokens_action: int = 60
    temperature_ladder: tuple[float, ...] = ()  # 非空=逐座覆盖，见 plan §7 与 `temperature_for`

    # --- budgets ---
    regions: RegionBudget = field(default_factory=RegionBudget)
    tokens: TokenBudget = field(default_factory=TokenBudget)

    # --- timeouts, keyed by actor kind (plan §15) ---
    llm_timeout_floor_s: float = 45.0
    llm_timeout_median_mult: float = 3.0
    # 建连单独封顶，因为 `llm_timeout_floor_s` 同时是 `agent._ask` 的席位 deadline：一个裸 float
    # 传给 httpx 会让 connect 与席位 deadline 是同一个数，于是端点黑洞（SYN 无应答）里两者同时响，
    # 抢赢的是 deadline——`is_upstream_error` 那条分类永远到不了，整局打成 37 分钟的引擎兜底。
    # 取值受 `test_transport.py` 那条不变量约束：重试预算必须赶在席位 deadline 之前判完。
    connect_timeout_s: float = 5.0
    max_game_wallclock_s: int = 900  # only enforced when every seat is an LlmActor
    max_game_completion_tokens: int = 20000
    max_retries_total: int = 30
    # 屠边在 5 天内解决；打到第 6 天结束就是平局（plan §12 R10 的预注册裁定）。唯一的来源：
    # `play()` 没有同名参数，所以"这局的上限是多少"只能来自这里，也因此必然进 `config_hash`、
    # 必然是批间可比的处理轴。默认值之下 mock 桌打不到它（实测 30 局最远第 5 天），把上限
    # 当成轴去压（`--set A.max_days=4`）才是让那条规则可测的唯一办法。
    max_days: int = 6

    # --- legality / retry (plan §4) ---
    max_repair_retries: int = 1
    degraded_game_fallbacks: int = 12

    # --- persona / anti-collapse (plan §7) ---
    # 一轮之内最多几个人被指派"先听听"。这是上限不是倾向：配额用完之后 listen 的权重是 0，
    # 所以 0 是一个合法取值（"这轮不许有人不表态"那一臂）。`max(v, 0.02)` 的地板曾经把它抬回
    # 2%，400 轮里漏出 17 次第二个 listen——地板只兜人格权重，那条不变量有用例守着。
    listen_quota_per_round: int = 1

    # --- rules house-rule flags (plan §13: default off) ---
    # 警长整条链没实现（plan §13 明写不做），所以这是一个"把门帘在门口"的字段：留着是为了
    # 声明默认关着，不是声明能开。它在 `_INERT` 里，`--set` 因此拒绝它。
    enable_sheriff: bool = False
    seat_count: int = 9

    # --- versioning: any prompt-literal change must bump these (plan §5) ---
    # v1.1 = the example now says its own [eNNN] ids are fictional. Nothing was in flight when
    # it moved (the endpoint is still offline), which is the only moment the rule is cheap.
    # v1.2 = the C1 persona card prints the number it had always sampled (话多). plan §5 ① ties
    # a version bump to an *A-region* edit, but R12 ties one to the card's content, and these
    # three strings are the only prompt versions inside `config_hash` — so a card edit that did
    # not bump anything would let two batches with different bytes compare as the same arm.
    contract_version: str = "CONTRACT v1.2"
    rules_version: str = "RULES v1.0"
    compress_version: str = "COMPACT v1.0"

    actor_kinds: tuple[ActorKind, ...] = ()  # filled by game.py

    # Not treatment axes under any circumstance (see the module constant for why both the
    # CLI and the guard read this list).
    _NOT_AN_AXIS = FORBIDDEN_AXIS
    # Fields the engine has no code for yet. Not identity, not a treatment — a label: setting
    # one changes `config_hash` while every seat keeps playing the same rules, which is exactly
    # the shape of a comparison report that measures nothing. `tests/test_batch_paired.py` pins
    # both directions of this list (nothing in it may be read; nothing outside it may be unread).
    _INERT = INERT_FIELDS
    _INERT_LEAVES = INERT_LEAVES

    @property
    def axis_fields(self) -> tuple[str, ...]:
        """Field names `--set` may legally change between two arms of a batch.

        Derived from the dataclass rather than typed out again: a new config field is then in
        the comparison by default, so the mistake it invites is a *loud* one (an undeclared
        axis veto in `compare`) instead of a silent one (an override that names nothing).
        """
        return tuple(k for k in self.to_dict()
                     if k not in self._NOT_AN_AXIS and k not in self._INERT)

    @property
    def inert_fields(self) -> tuple[str, ...]:
        """轴名单外的第二类字段：不是不许改，是改了没有对应行为。"""
        return self._INERT

    @property
    def inert_leaves(self) -> tuple[str, ...]:
        """同一类字段的**嵌套版**，按 `--set` 写的点号路径算（`regions.b0`）。"""
        return self._INERT_LEAVES

    def token_budget_for(self, phase: Phase) -> int:
        """The ``max_tokens`` this phase is billed for. One predicate, both readers.

        `actors.LlmActor.act` sends it; the `--dry-run` census sums it into the per-game
        completion budget, which is what the per-game wall clock is multiplied out of. Before
        this function existed the enumeration was an inline ternary at the call site, so the
        second reader could only retype it — and a disagreement between the two changes no
        behaviour at all, only the arithmetic in prose.
        """
        return self.max_tokens_speech if phase in SPEECH_PHASES else self.max_tokens_action

    def temperature_for(self, seat: int) -> float:
        """``temperature`` this seat is billed at. One predicate, both readers of it.

        `temperature_ladder` is plan §7's anti-collapse device: a short ladder clamps at its last
        rung, so seats past the end all get that rung. An empty ladder means no per-seat override
        exists at all, and every seat gets the global `temperature` — which is what makes
        `--set B.temperature=0.6` the axis the docs claim it is. Before this method the choice was
        an inline index expression in `LlmActor.act`, and the global field had no reader: two arms
        differing only in it sent byte-identical requests while `comparison.md` named the axis.
        """
        ladder = self.temperature_ladder or (self.temperature,)
        return ladder[min(seat - 1, len(ladder) - 1)]

    def require_key(self) -> str:
        """Resolve the key at call time. Never caches, never defaults."""
        import os

        value = os.environ.get(self.api_key_env)
        if not value:
            raise ConfigError(
                f"{self.api_key_env} not exported. "
                f"Run: export {self.api_key_env}=<key>  (do not put it in any file)"
            )
        return value

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # whitelist, not blacklist: api_key_env is a NAME and is safe; nothing else secret exists
        return d

    def config_hash(self) -> str:
        """Identity of everything that can change model behaviour or scoring."""
        payload = json.dumps(self.to_dict(), sort_keys=True, default=list)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]
