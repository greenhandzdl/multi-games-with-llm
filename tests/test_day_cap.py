"""日子天花板：一处判定、一个来源、平局落在哪个分母里。

plan §12 R10 把"日数上限 + 预注册平局裁定"当成 R10（全员被动 → 无人出局 → 拖到票型碎裂）
的兜底。这条链上原本有三处各自为政：`rules.game_over_by_day_limit` 用 `state.day > max_days`
并且顺手写 `state.terminal`、`game.play` 的循环用 `>=`、命令行上没有任何入口能调它。两套
比较号不是学术问题——`>` 意味着"上限 6 天"实际能打到第 7 天，而那正是预注册条款否认的东西。

第三个问题是前两个测不出来的原因。实测 30 局 mock（seed 1–30，就是本文件 `_play` 的那个桌）的
终局分布是 `{wolf_win: 21, good_win: 9}`、**0 局平局**、最远到第 5 天；把上限压到 3 才有 12/30 局
`draw_day_limit`。也就是说"平局不进胜率分母"这条预注册规则在真实批次里根本观察不到，除非
上限变成一个可调、可当处理轴比较的数。所以这里钉的是：判定只有一处、用的是 `>=`、来源只剩
`Config.max_days`、一次**真打出来的**平局落在 `excluded` 而不是分母里。
"""

from __future__ import annotations

import contextlib
import inspect
import io
import random
from pathlib import Path

import pytest

from wolfengine import batch, cli, game, metrics, rules
from wolfengine.config import Config
from wolfengine.roles import BOARD_9
from wolfengine.state import GameState, SeatState


def _state(day: int) -> GameState:
    return GameState(
        board=BOARD_9, game_id="t", deal_seed=1,
        seats={s: SeatState(seat=s, role="villager", alive=True) for s in range(1, 10)},
        day=day)


@contextlib.contextmanager
def _quiet():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


def _play(tmp_path: Path, seed: int, max_days: int) -> game.GameResult:
    """One real mock game at a chosen cap, with the cap arriving the way a batch would send it."""
    cfg = batch.apply_overrides(Config(), {"max_days": max_days})
    from wolfengine.actors import MockActor
    actors = {s: MockActor(s, synthesize=True, rng=random.Random(seed * 100 + s))
              for s in range(1, 10)}
    import asyncio
    with _quiet():
        return asyncio.run(game.play(cfg=cfg, deal_seed=seed, out_dir=tmp_path, actors=actors))


def _draw_at(tmp_path: Path, max_days: int, seeds: range) -> game.GameResult:
    for seed in seeds:
        res = _play(tmp_path, seed, max_days)
        if res.terminal == game.DRAW_DAY_LIMIT:
            return res
    raise AssertionError(f"cap={max_days} 下 {len(seeds)} 局 mock 里一局平局都没有：上限没接进引擎")


# --------------------------------------------------------------------------------- 一处判定
def test_the_cap_is_decided_in_one_place_and_counts_the_last_day_whole():
    """`>` 与 `>=` 的分歧就落在"上限 6 天"这句话上：第 6 天打完就是终局，不是还有第 7 天。"""
    assert not hasattr(rules, "game_over_by_day_limit"), \
        "两套判定共存时总有一套先被改，而改错的那套不会变红"
    assert rules.day_limit_reached(_state(5), 6) is False
    assert rules.day_limit_reached(_state(6), 6) is True


def test_the_engine_has_one_source_for_the_cap():
    """`play(max_days=…)` 这个参数就是第二套判定的入口：它让"这局的上限是多少"变成调用栈的
    秘密，而它必须进 `config_hash` 才配当处理轴。参数删掉，来源只剩 Config。"""
    assert "max_days" not in inspect.signature(game.play).parameters
    assert Config().max_days == 6


def test_the_cap_is_a_treatment_axis_a_batch_can_declare():
    """两臂只差日数上限是 R10 的直接实验（"压到 4 天会不会逼出更多攻击性动作"）。`--set` 到
    不了它，就说明它不在轴上，而 plan §8 要求批内的一切差异都落在声明的轴上。"""
    assert "max_days" in Config().axis_fields
    assert batch.apply_overrides(Config(), {"max_days": 4}).max_days == 4
    with pytest.raises(batch.BadOverride):
        batch.apply_overrides(Config(), {"max_days": "lots"})


# --------------------------------------------------------------------------------- 真打出来的平局
def test_a_real_draw_is_a_terminal_the_engine_emits_and_m1_refuses_to_score(tmp_path):
    """不是伪造日志：一局真的打到上限的棋。这一局是 mock 桌，所以 plan §15 先一步把它整个
    丢掉——平局的分母规则要等下一局才看得到，但"上限到了、终局名是 `draw_day_limit`、胜率是
    None 而不是 0"这三件事在这里就该钉住。"""
    drawn = _draw_at(tmp_path, 3, range(1, 12))
    assert drawn.days == 3 and drawn.terminal == "draw_day_limit"

    m1 = metrics.m1_win_rate([metrics.read_game(drawn.path)])
    assert m1["n_games"] == 1 and m1["n_decisive"] == 0
    assert m1["good_win_rate"] is None, "一个平局算不出 0.0 的胜率，那是在说好人没赢过"
    assert m1["n_synthetic_excluded"] == 1


def test_a_draw_on_a_real_table_lands_in_excluded_not_in_the_denominator(tmp_path):
    """同一个终局，换一个桌型：`excluded` 里它占自己那一格，不并进 `aborted`、也不留在分母里。

    这里改了 manifest 的 `actor_kinds` 为 `["llm"]` —— 整份日志只动那一个字段，为的是在没有
    端点的情况下问出"真桌上平局怎么算"。改字段这件事本身由上一条测试盯着：mock 桌确实进不了
    评测语料，所以不把它一起改掉，这条断言永远跑不到 `excluded` 那一层。
    """
    drawn = _draw_at(tmp_path, 3, range(1, 12))
    game_on_a_real_table = metrics.read_game(drawn.path)
    game_on_a_real_table.meta["actor_kinds"] = ["llm"]

    m1 = metrics.m1_win_rate([game_on_a_real_table])
    assert m1["n_decisive"] == 0 and m1["n_synthetic_excluded"] == 0
    assert m1["excluded"] == {"draw_day_limit": 1}, "平局既不该并进 aborted，也不该留在分母里"
    assert m1["good_win_rate"] is None


def test_the_cap_is_reachable_only_below_the_shipped_default(tmp_path):
    """R10 的兜底在默认上限下**打不到**：30 局 mock 最远第 5 天。这不是"规则没用"，而是
    "它现在测不到"——把这句话钉成测试，是因为它两边都可能变：真实 LLM 桌更可能拖到上限，
    那时这条断言变红，就该有人回来重数这批数字。"""
    terms = {}
    for seed in range(1, 31):
        res = _play(tmp_path, seed, Config().max_days)
        terms[res.terminal] = terms.get(res.terminal, 0) + 1
        assert res.days <= 6
    assert terms.get("draw_day_limit", 0) == 0, f"默认上限下出现了平局 {terms}：重数 R10"
    assert set(terms) == {"good_win", "wolf_win"}


# --------------------------------------------------------------------------------- 命令行
def test_run_exposes_the_cap_on_the_command_line(tmp_path):
    """`--max-days 2` 之后日志里不许出现第 3 天。这是对"参数真的接到了引擎"的唯一证明；
    只看返回码会放过一个把参数丢掉、然后照常打完 6 天的实现。"""
    with _quiet():
        rc = cli.main(["run", "--mock", "--seed", "3", "--max-days", "2",
                       "--out", str(tmp_path), "--quiet"])
    assert rc in (0, 1)
    g = metrics.read_game(sorted(tmp_path.glob("*.jsonl"))[0])
    assert g.days <= 2
    assert g.meta["config_hash"] != batch.apply_overrides(
        Config(), {"max_days": 9}).config_hash(), "上限变了而哈希不变，两臂就是同一臂"


def test_a_draw_is_a_result_and_not_a_failed_run(tmp_path):
    """`cmd_run` 以前把"不是胜负"一律当失败返回 rc=1，于是预注册的平局和端点断了长得一样：
    脚本照着 rc 重跑，就把一批合法结果重新烧一遍。"""
    for seed in range(1, 12):
        with _quiet():
            rc = cli.main(["run", "--mock", "--seed", str(seed), "--max-days", "2",
                           "--out", str(tmp_path), "--quiet"])
        g = metrics.read_game(sorted(tmp_path.glob("*.jsonl"))[-1])
        if g.terminal == game.DRAW_DAY_LIMIT:
            assert rc == 0, "平局是结果，rc=1 会把它读成中断"
            return
    raise AssertionError("没在命令行路径上打出平局：`--max-days` 可能没生效")
