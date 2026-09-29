"""The three commands CI and the demo both depend on.

`--dry-run` is the budget tool (plan §11): the claim under test is not "it printed something"
but "it assembled every prompt while physically unable to send one". A test that only counts
lines would still pass if someone wired a real call into it, which is the exact regression
this command exists to avoid.

`replay` is the demo: it must work with the endpoint offline, from the JSONL alone. That is
the same file, so `--seat` (one player's view) and `--god` (the audience's) are asserted as
differing by exactly the private events — the isolation property, checked through the UI
rather than through the prompt builder.
"""

from __future__ import annotations

import json
import re
import html as html_mod
from pathlib import Path
from types import SimpleNamespace

import pytest

from wolfengine import batch, cli, metrics
from wolfengine.config import Config
from wolfengine.state import Phase

SEED = 7


@pytest.fixture(scope="module")
def played(tmp_path_factory):
    out = tmp_path_factory.mktemp("clidata")
    rc = cli.main(["run", "--mock", "--seed", str(SEED), "--quiet", "--out", str(out)])
    assert rc == 0
    return out, sorted(out.glob(f"*_g{SEED:08d}.jsonl"))[0]


# ---------------------------------------------------------------------------- dry run
def test_dry_run_assembles_every_prompt_without_calling(no_network, tmp_path):
    rc = cli.main(["run", "--dry-run", "--seed", str(SEED), "--out", str(tmp_path)])
    assert rc == 0
    assert no_network == [], "the endpoint was reached"
    dump = tmp_path / f"g{SEED:08d}.prompts.jsonl"
    rows = [json.loads(l) for l in dump.read_text(encoding="utf-8").splitlines()]
    assert len({r["seat"] for r in rows}) == 9, "a seat never got a prompt"
    assert all(r["messages"] and r["region_tokens"] for r in rows)
    # The whole point of the command: the numbers that drive §5's budget table.
    assert all(r["total_tokens"] > 0 for r in rows)
    assert sum(r["over_ceiling"] for r in rows) == 0, "a mock game blew the context ceiling"


def test_dry_run_reports_the_phases_it_reached(no_network, tmp_path, capsys):
    cli.main(["run", "--dry-run", "--seed", str(SEED), "--out", str(tmp_path)])
    printed = capsys.readouterr().out
    for phase in ("night_wolf", "night_witch", "night_seer", "day_speech", "day_vote"):
        assert phase in printed, f"{phase} missing from the dry-run report:\n{printed}"


def _dump_b_max(directory) -> int:
    rows = [json.loads(l) for l in
            next(Path(directory).glob("*.prompts.jsonl")).read_text(encoding="utf-8").splitlines()]
    return max(r["region_tokens"]["B"] for r in rows)


def test_the_budget_tool_can_vary_the_budget_table(no_network, tmp_path, capsys):
    """§5 的预算表此前只能在 python 里改：`--dry-run` 是预算工具，却不接受 `--set`。

    这句限界在 `#4`（M3★ 要两档前缀长度）上被当成事实引用过，而它是不成立的：
    `regions.b2` 既有键、也不在 inert 名单里，缺的只是把 `--set` 接到 `run` 上。
    一条命令补完之后，"这一档前缀够不够长"就不再是一句要读代码才能核的话。
    """
    base, cut = tmp_path / "base", tmp_path / "cut"
    assert cli.main(["run", "--dry-run", "--seed", str(SEED), "--out", str(base)]) == 0
    rc = cli.main(["run", "--dry-run", "--seed", str(SEED), "--out", str(cut),
                   "--set", "regions.b2=400"])
    assert rc == 0, capsys.readouterr().err
    assert _dump_b_max(cut) < _dump_b_max(base), "换表没有改变装配出来的 B 区"

    # 换过的表要留在产物里，否则两份普查长得一模一样，读者分不清拿的哪张。
    head = json.loads(next(base.glob(f"*_g{SEED:08d}.jsonl")).read_text(
        encoding="utf-8").splitlines()[0])
    meta = json.loads(next(cut.glob(f"*_g{SEED:08d}.jsonl")).read_text(
        encoding="utf-8").splitlines()[0])
    assert head["meta"]["regions"]["b2"] == 1500 and meta["meta"]["regions"]["b2"] == 400, \
        "seq:0 的 meta 记的不是命令行上那张表"
    assert head["meta"]["config_hash"] != meta["meta"]["config_hash"], \
        "两张表进了同一个 config_hash——两批不同字节的 prompt 会自称同一臂"


def test_the_run_override_meets_the_same_refusals_as_the_batch(no_network, tmp_path, capsys):
    """`run --set` 必须走 `batch.apply_overrides`，不能自己判什么能改。

    两把尺的代价是：命令行上放行一个 inert 格子，普查就印出一张"改了预算"的表，
    而发出去的字节一个都没变——那是 `#62` 在批次那一侧刚刚堵掉的同一个洞。
    """
    for pair in ("tokens.warn=8000",           # 有键、没人读（INERT_LEAVES）
                 "actor_kinds=mock",           # 换被试（FORBIDDEN_AXIS）
                 "regions.not_a_cap=10"):      # 根本没有这一格
        out = tmp_path / pair
        rc = cli.main(["run", "--dry-run", "--seed", str(SEED), "--out", str(out),
                       "--set", pair])
        err = capsys.readouterr().err
        assert rc == 2, f"{pair} 被放行了（rc={rc}）"
        assert "配置错误" in err and "Traceback" not in err, f"{pair}: {err}"
        assert not list(out.glob("*.prompts.jsonl")), f"{pair} 拒了却还是把桌子打完了"


def test_a_run_set_pair_that_cannot_be_read_stops_with_a_hint(no_network, tmp_path, capsys):
    """`--set regions.b2`（漏了 `=`）与 `--set regions.b2=abc` 都是人要敲出来的东西。

    这两条路各有一句文案（一句是"怎么写"，一句是"读成了什么"），而文案只在终端上出现一次：
    没有断言读它，改成 traceback 或者改成一句"配置错误"就没人发现——`#50`/`#51` 那一族就是这个形状。
    """
    for pair, hint in (("regions.b2", "写成"),          # 少了 `=`：整对读不出键值
                       ("=400", "写成"),                # 少了键名
                       ("regions.b2=abc", "类型不符"),   # 值读不进 int 格子
                       ("regions.b2=", "类型不符"),      # 空值同样不该被当成 0
                       ("temperature_ladder=[0.7", "值读不出来")):  # 列表格走 json，残缺的要停下
        out = tmp_path / pair
        rc = cli.main(["run", "--dry-run", "--seed", str(SEED), "--out", str(out),
                       "--set", pair])
        err = capsys.readouterr().err
        assert rc == 2, f"{pair} 没有被停下（rc={rc}）：{err}"
        assert "配置错误" in err and hint in err, f"{pair}: {err}"
        assert "Traceback" not in err, f"{pair} 留下的是 traceback：{err}"
        assert not list(out.glob("*.prompts.jsonl")), f"{pair} 停了却还是把桌子打完了"


def test_the_batch_path_refuses_an_unreadable_value_with_the_same_hint(no_network, tmp_path,
                                                                       capsys):
    """`值读不出来` 在 `cli.py` 里印了两遍，此前只有一处有人读。

    两个解析器共用 `_coerce`，却各自抄了一次文案：`_parse_run_set` 的那份被上面那条用例钉住，
    `_parse_sets` 的这份零断言。文案是同一句、缺一半读者，改另一处不会有人变红——把它试着改成
    漏出 ValueError，整套绿着走完，就是这个形状。
    """
    out = tmp_path / "b"
    rc = cli.main(_batch_cmd(out, "--set", "B.temperature_ladder=[0.7"))
    err = capsys.readouterr().err
    assert rc == 2, f"批次这一侧被放行了（rc={rc}）：{err}"
    assert "配置错误" in err and "值读不出来" in err, err
    assert "Traceback" not in err, err
    assert not (out / "run_manifest.json").exists(), "停在解析，却还是把批次目录建出来了"


def test_two_spellings_of_one_knob_do_not_silently_pick_a_winner(no_network, tmp_path, capsys):
    """`--max-days` 与 `--set max_days=` 是同一根旋钮的两个名字，同时给就是命令行在问两遍。

    有了 `run --set` 之后这一天注定要来：`--max-days` 是它的一个特例。谁赢都不该是
    字典写入顺序决定的——那份普查到底按几天打，读者必须能只看命令就知道。
    """
    out = tmp_path / "both"
    rc = cli.main(["run", "--dry-run", "--seed", str(SEED), "--out", str(out),
                   "--max-days", "4", "--set", "max_days=5"])
    err = capsys.readouterr().err
    assert rc == 2, f"两个拼法被静默裁决了（rc={rc}）"
    assert "max_days" in err and "--max-days" in err, err
    # 反向对照：只给一个拼法时那条路必须照样通，否则上面的 rc 2 只是命令本身坏了。
    assert cli.main(["run", "--dry-run", "--seed", str(SEED), "--out", str(tmp_path / "one"),
                     "--set", "max_days=5"]) == 0


# ------------------------------------------------------------- the per-game cost inventory
# Spelled by hand, on purpose: it is the second ruler the guards below compare against.
SPEECH_PHASES_BY_HAND = {"day_speech", "day_pk_speech", "last_words"}


def test_only_the_three_prose_phases_are_billed_the_speech_budget():
    """140 tok 和 60 tok 的分岔口只由一张名单决定，而这张名单此前活在一个内联三元组里。

    每局的墙钟 = 每局调用次数 × 每次的解码量，plan §187 的"8–12 局/小时"和
    `max_game_completion_tokens` 这根硬顶都建立在这条分岔上。给 `night_wolf` 换成发言级预算、
    新增一个发言阶段却忘了进名单、或者改个阶段名——两句散文都会变假，而没有任何用例会红。
    名单抄在测试里（而不是调用被测函数得来），漂移就只能以红的形式发生。
    """
    cfg = Config()
    priced = {p.value: cfg.token_budget_for(p) for p in Phase}
    assert priced == {p.value: (cfg.max_tokens_speech if p.value in SPEECH_PHASES_BY_HAND
                                else cfg.max_tokens_action) for p in Phase}, priced
    # 反向对照：两档预算若相等，上面那条等式就是由常量相等自证的假绿。
    assert cfg.max_tokens_speech != cfg.max_tokens_action
    assert len(priced) == len(tuple(Phase)), "有阶段没被定价"


def test_the_census_prices_a_game_in_calls_tokens_and_completion_budget(
        no_network, tmp_path, capsys):
    """普查要把"这局会花掉什么"印成三个数，而不是留给人按 9×4.5 心算。"""
    cli.main(["run", "--dry-run", "--seed", str(SEED), "--out", str(tmp_path)])
    printed = capsys.readouterr().out
    rows = [json.loads(line) for line in
            (tmp_path / f"g{SEED:08d}.prompts.jsonl").read_text(encoding="utf-8").splitlines()]
    cfg = Config()
    calls = len(rows)
    ptok = sum(r["total_tokens"] for r in rows)
    ctok = sum(cfg.max_tokens_speech if r["phase"] in SPEECH_PHASES_BY_HAND
               else cfg.max_tokens_action for r in rows)
    assert calls >= 40 and ptok > 0 and ctok > 0, "这条线空转了：三格里有格是 0"
    assert f"{calls} 次调用" in printed, f"没有印出每局调用次数：\n{printed}"
    assert f"{ptok} tok" in printed, f"prompt token 合计与落盘的 dump 不符：\n{printed}"
    assert f"完成预算 {ctok} tok" in printed, \
        f"完成预算与 `token_budget_for` 之和不符（名单漂移？）：\n{printed}"
    # 上限不是实测：印出来的必须是"按 max_tokens 的上限"，并把每局硬顶一起给出。
    assert f"硬顶 {cfg.max_game_completion_tokens}" in printed, printed
    assert "上限" in printed and "实测" in printed, \
        "没有声明这个数是上限而不是实测生成长度——读者会当结论用"


def test_dry_run_over_a_batch_writes_one_dump_per_seed(no_network, tmp_path):
    cli.main(["run", "--dry-run", "--seed", "11", "--games", "3", "--out", str(tmp_path)])
    assert no_network == []
    assert sorted(p.name for p in tmp_path.glob("*.prompts.jsonl")) == \
        ["g00000011.prompts.jsonl", "g00000012.prompts.jsonl", "g00000013.prompts.jsonl"]


def test_an_empty_census_refuses_to_conclude(no_network, tmp_path, capsys, monkeypatch):
    """普查自己说"这本身就是失败"的那一刻，退出码不能还是 0。

    `_print_census` 那句空普查是为"装配钩子失效"写的（一局跑了、一个 prompt 都没存下来），
    而 `_cmd_dry_run` 以前无论印什么都 `return 0`。这里把 `play` 换成不记录任何上下文的假手，
    走的正是那条真空分支——断言只认两件事：那句话印出来了，退出码是"拒绝出结论"的那个。
    """
    async def fake_play(**kw):
        return SimpleNamespace(game_id="g-empty", terminal="good_win", days=1)

    monkeypatch.setattr(cli, "play", fake_play)
    monkeypatch.setattr(cli, "make_actors", lambda cfg, seed, *, mock: {})
    rc = cli.main(["run", "--dry-run", "--seed", "11", "--games", "2", "--out", str(tmp_path)])
    printed = capsys.readouterr().out
    assert "没有捕获到任何 prompt" in printed, printed
    assert rc == 1, f"rc={rc}：普查那句自报失败，退出码却是一份结论"
    assert no_network == []


def test_the_batch_loader_ignores_the_prompt_dumps(no_network, tmp_path):
    """`--dry-run` writes its dump next to the games that produced it, because both take the
    same `--out`. So one folder legitimately holds two kinds of `*.jsonl` — and `read_dir` is
    the batch layer's entry point (M1's denominator, and every comparison in plan §8's two-config
    protocol). A loader that dies on the other artifact of its own tool cannot be pointed at
    `data/`, which is the only directory the demo ever uses.
    """
    cli.main(["run", "--dry-run", "--seed", str(SEED), "--out", str(tmp_path)])
    assert no_network == []
    games = metrics.read_dir(tmp_path)
    assert len(games) == 1, sorted(p.name for p in tmp_path.glob("*.jsonl"))
    assert games[0].game_id == f"g{SEED:08d}"
    # M1 must survive a batch of one stand-in table: it reports None with a note, not a crash
    # and not a 0% win rate (plan §11 — a mock table never enters the corpus).
    rate = metrics.m1_win_rate(games)
    assert rate["good_win_rate"] is None and rate["n_synthetic_excluded"] == 1


def test_the_batch_reader_skips_the_dump_by_the_same_name_as_the_writer(no_network, tmp_path):
    """`compare` never goes through `read_dir`: `batch.read_arm` walks the arm folder with its own
    copy of "this name is not a trajectory". That copy had no assertion behind it — turning it into
    one that never matches left all three test files green (`/tmp/mut_predup.py` P2,
    2026-09-22T01:14:10Z), which is what "a branch nobody reads" looks like when it is a `continue`.

    Written against the real writer rather than a hand-made filename: the predicate is a copy of an
    f-string in `cli.py`, so a test carrying its own literal proves only that two literals still
    agree with each other, not that the folder the tool actually produces loads.
    """
    cli.main(["run", "--dry-run", "--seed", str(SEED), "--out", str(tmp_path)])
    assert no_network == []
    dump = f"g{SEED:08d}.prompts.jsonl"
    names = sorted(p.name for p in tmp_path.glob("*.jsonl"))
    assert dump in names, f"写入方没把转储落在这个目录里，用例就在空转：{names}"
    rows = batch.read_arm(tmp_path)
    assert [Path(r["path"]).name for r in rows] == [n for n in names if n != dump], \
        f"批次读取器读到的不是「除了转储的那一份」：{names} → {[r['path'] for r in rows]}"
    assert rows[0]["game_id"] == f"g{SEED:08d}", rows


# ------------------------------------------------------------------------------ run
def test_a_real_run_without_the_key_says_which_variable_is_missing(monkeypatch, capsys,
                                                     tmp_path):
    monkeypatch.delenv(Config().api_key_env, raising=False)
    out = tmp_path / "nokey"
    rc = cli.main(["run", "--seed", "3", "--out", str(out)])
    err = capsys.readouterr().err
    assert rc == 2 and "WOLF_LLM_API_KEY" in err, f"rc={rc} err={err!r}"
    assert not list(out.glob("*.jsonl")), "it started a game it could not play"


# ---------------------------------------------------------------------------- replay
def test_replay_prints_the_public_timeline_and_only_that(played):
    _, path = played
    cap = _replay(played, [])
    assert "[e1] 法官：开局座位" in cap
    assert "狼队私聊" not in cap, "the audience view showed the wolf channel"
    assert "你的身份是" not in cap, "the audience view dealt roles in the open"
    assert "查验" not in cap, "the audience view read the seer's result"


def test_god_view_is_exactly_the_private_events(played):
    """`--god` must *add* lines, never rewrite a public one: the audience's timeline and a
    player's timeline are the same append-only render, and if they were two renderers the
    demo would disagree with itself."""
    _, path = played
    plain, god = _replay(played, []).splitlines(), _replay(played, ["--god"]).splitlines()
    public = set(plain)
    assert [ln for ln in god if ln in public] == plain, "god view reworded a public line"
    hidden = [ln for ln in god if ln not in public]
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
              if json.loads(l)["seq"] != 0]
    private = [e for e in events if e["visibility"] != "all"]
    assert len(hidden) == len(private), f"{len(hidden)} extra lines for {len(private)} private events"
    assert {"deal", "wolf_chat", "notice", "seer_result", "night_action"} \
        >= {e["kind"] for e in private}
    assert "game_over" not in {e["kind"] for e in private}, "the result must stay public"


def test_a_seat_replay_sees_its_own_night_and_nobody_elses(played):
    seer = _replay(played, ["--seat", "7"])
    assert "1号是狼人" in seer or "你查验的1号" in seer, seer[-400:]
    assert "狼队私聊" not in seer
    witch = _replay(played, ["--seat", "5"])
    assert "狼队私聊" not in witch and "你查验的" not in witch
    villager = _replay(played, ["--seat", "3"])
    for line in ("狼队私聊", "倒在了狼刀下", "你查验的"):
        assert line not in villager, f"{line} leaked into seat 3's replay"


def test_sitting_at_a_seat_wins_over_the_god_view_on_the_same_file(played):
    """views.md 里"`--seat` 与 `--god` 同时给时坐进某一位优先"以前只有散文撑着。

    `render_chronicle` 的那一支 `if as_seat is not None: ... elif god:` 从来没被任何用例同时给过两个
    参数——仓库里 `god=True` 的那几处都在别的文件、且只给一个参数，所以把两支换序也不会有人红。
    07:55:40Z 在同一份日志上量过四种给法（行数和哈希在 `docs/iterations.md` 的 `#219` 那一节——它们跟着
    渲染版本挪，`#183` 给实录加的那一格就把两份各顶了一行）：这里只钉住得住的那半句。
    """
    seat = _replay(played, ["--seat", "3"])
    both = _replay(played, ["--god", "--seat", "3"])
    swapped = _replay(played, ["--seat", "3", "--god"])
    god = _replay(played, ["--god"])
    assert both == seat == swapped, "给了座位之后 `--god` 还能改变输出——座位不是那一支的第一判据"
    assert "狼队私聊" in god, "这份夹具里没有私有事件，三方比对无从分辨"
    assert "狼队私聊" not in seat, "3 号看见了自己桌子之外的东西，那个相等就是两边都漏了"


@pytest.mark.parametrize("verb", ["replay", "watch"])
@pytest.mark.parametrize("seat", ["0", "10", "-1"])
def test_a_seat_that_is_not_at_the_table_is_refused_before_the_timeline(played, capsys,
                                                                        verb, seat):
    """`--seat 10` 印的是公开视图、退出码 0，和什么都不填逐字节相同（06:24:14Z 实测三份
    sha `e090c3f68af3`）：九人桌上没有第 10 把椅子，而这句话以前没有 owner，于是"点了一个
    不存在的座位"这件事在终端上长得像一次成功的查询。名册读的是开局记录里的 `seats`，和
    `render_html` 画票型矩阵用的是同一份——不在这里另数一遍 1..9。
    """
    _, path = played
    argv = [verb, str(path), "--seat", seat] + (["--once"] if verb == "watch" else [])
    rc = cli.main(argv)
    cap = capsys.readouterr()
    assert rc == 2, f"rc={rc} out={cap.out[:80]!r}：越界的座位号是命令写错了，不是引擎拒绝"
    assert cap.out == "", "报错之前不该先印一份看着正常的时间线"
    assert cap.err.startswith("配置错误：") and "--seat" in cap.err, cap.err
    assert seat in cap.err, f"要把收到的那个号说回去：{cap.err!r}"
    assert "1-9" in cap.err and "9 席" in cap.err, f"要说出这一局的名册：{cap.err!r}"
    assert "Traceback" not in cap.err


def test_the_two_ends_of_the_roster_are_still_seats(played, capsys):
    """名册的两端都得放行，否则"什么号都拒"也能骗过上一条：1 号和 9 号是真实存在的椅子。
    """
    _, path = played
    for seat in ("1", "9"):
        assert cli.main(["replay", str(path), "--seat", seat]) == 0, f"{seat} 号被拒了"
        printed = capsys.readouterr().out
        assert "〔私有〕" in printed, f"{seat} 号连自己的身份牌都看不到：{printed[:120]!r}"


@pytest.mark.parametrize("seat", ["3", "42"])
def test_a_seat_view_of_a_file_with_nothing_after_the_manifest_says_the_sentence(tmp_path, played,
                                                                                 capsys, seat):
    """两个方向同一条用例。没有开局记录就没有名册，"这局没有 42 号"这句无从谈起，范围检查要在
    这儿让路——那份文件有自己的那句话（"这一局只有开局记录"）和退出码 0，是 `#53` 定过的。而**
    真号**在这里也不许炸：修前实测（06:35:46Z）`--seat 3` 与 `--seat 42` 都是
    `IndexError: list index out of range`、rc 1，同一个文件不带 `--seat` 时那句照印、rc 0。
    那个 1 是 traceback 给的，不是判据给的。
    """
    _, path = played
    stub = tmp_path / "manifest-only.jsonl"
    stub.write_text(path.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    assert cli.main(["replay", str(stub), "--seat", seat]) == 0
    printed = capsys.readouterr()
    assert "只有开局记录" in printed.out, printed
    assert printed.err == "" and "Traceback" not in printed.out, printed


def test_replay_needs_nothing_but_the_file(played):
    """The demo has to survive the endpoint being down. `replay` takes a path, prints text,
    and reads no `request`/`response` field to decide what happened — the log line itself is
    enough, which is why the same bytes can drive the live terminal and the HTML."""
    _, path = played
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert events[0]["seq"] == 0 and events[-1]["payload"]["terminal"] in \
        ("good_win", "wolf_win", "draw_day_limit")
    lines = _replay(played, ["--god"]).splitlines()
    assert lines[-1].startswith("[e77]") or "获胜" in lines[-1], lines[-1]
    assert not any("prompt_tokens" in ln or "latency" in ln for ln in lines), \
        "the timeline is printing call bookkeeping instead of game facts"


# ------------------------------------------------------------------------------ audit
def test_audit_counts_match_the_log_it_was_handed(played, capsys):
    """Recomputed here from the raw lines, with a *different* definition of "decision turn"
    than `metrics.decisions()` uses: this counts the events whose `result` carries a `fallback`
    key — the shape `agent.py` publishes — while metrics counts by event kind plus `meta`. Two
    independent readings of the same file must agree, or one of them is counting something else.
    """
    _, path = played
    assert cli.main(["audit", str(path)]) == 0
    printed = capsys.readouterr().out
    stats = _last_json_block(printed)
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
              if json.loads(l)["seq"] != 0]
    turns = [e for e in events if "fallback" in e["result"]]
    assert stats["events"] == len(events)
    assert stats["kinds"]["speech"] == len([e for e in events if e["kind"] == "speech"])
    assert stats["terminal"] == events[-1]["payload"]["terminal"]
    assert stats["synthetic"] is True, "a mock table must announce itself in its own audit"
    assert stats["m3_gate"]["n_turns"] == len(turns)
    assert stats["m3_gate"]["fallback_rate"] == round(
        sum(1 for e in turns if e["result"].get("fallback")) / len(turns), 4)
    assert stats["m2_illegal"]["n_turns"] == len(turns)
    assert stats["m4_hallucination"]["n_speech"] == len(
        [e for e in events if e["kind"] == "speech" and e["payload"].get("text")])
    assert stats["compactions"]["events"] == stats["kinds"].get("compaction", 0)
    # 两份 `fallback` 拷贝在这里再各数一遍，和 audit 那一格对数（`#119`）。这里的分母走的是
    # 本用例那个"result 里有 fallback 键"的定义，与 `metrics.decisions()` 的"kind + meta"是两条
    # 独立的路；两者同数才说明那一格读的是同一份文件，而不是另一个口径。
    both = [e for e in turns if "fallback" in e["payload"].get("meta", {})]
    assert stats["fallback_copy_check"] == {
        "n_compared": len(both),
        "divergent": sum(1 for e in both
                         if e["payload"]["meta"]["fallback"] != e["result"]["fallback"])}, \
        stats["fallback_copy_check"]


def test_audit_carries_the_version_stamps_the_log_was_written_with(played, capsys):
    """三份版本戳从开局就落了盘，机器出口里却没有它们（`#178` 的三格）。

    `open_log` 把 `contract_version` / `rules_version` / `compress_version` 写进 meta，
    `prompts/templates.py` 顶上那句"改这里就是新的 `rules_version`/`contract_version`"讲的就是
    它们——可那一片当时没有任何一条读侧的路问得出"这份日志是哪一版规则写的"：`audit` 的 meta 那一格是
    一串**拼出来的**键名，当时里面只有 game_id / deal_seed / config_hash / actor_kinds / model /
    reproducible 六格，今天那串是十格。同一趟普查把这一串里的 `reproducible` 也报成了零读者，那一格是假缺陷——
    它有人读，就在这同一串键名里，所以这把尺换成闸门之前得先补这一层——而那一个读者自己到 `#181` 才有用例钉。
    """
    _, path = played
    assert cli.main(["audit", str(path)]) == 0
    stats = _last_json_block(capsys.readouterr().out)
    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[0])["meta"]
    for k in ("contract_version", "rules_version", "compress_version"):
        assert stats["meta"][k] == raw[k], f"{k} 出的不是这份日志自己的值"
        assert stats["meta"][k], f"{k} 印了个空的"


def test_audit_carries_which_board_the_log_was_dealt_on(played, capsys):
    """同一把尺（`#177` 的零读者普查）量出来的六格之一：`board` 落了盘、没有人读。

    这一格和三份版本戳不是同一种缺陷——版本戳是"问得出但没接线"，`board` 是"机器出口分不出
    这两份日志坐在哪张桌上"：座位数不同的两块板，接线之前那串键名里只有
    `config_hash` 那串 12 位十六进制会不同，而那串是**整体配置**的指纹，说不了"是板子不同"。
    """
    _, path = played
    assert cli.main(["audit", str(path)]) == 0
    stats = _last_json_block(capsys.readouterr().out)
    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[0])["meta"]
    assert stats["meta"]["board"] == raw["board"]
    assert stats["meta"]["board"], "board 印了个空的"


def test_the_reproducibility_flag_reaches_the_machine_exit(played, capsys):
    """`audit` 那串拼出来的键名里，`reproducible` 那一格今天有了第二个读者，也才有了证人。

    `#178`/`#179` 数读者的那把尺说这一格有人读，读它的就是那串键名；可**那一串里每一格都没有证人**。
    `#181` 的电池现形过一次：05:38:37Z 把 `game.py` 里这一格整行摘掉，红的只有读文件的那两条用例，
    这一本（`test_cli.py`）一条没红——也就是说把键名从 `audit` 的出口里删掉，当时没有任何东西会响。
    这条钉的是"落盘的声明到得了机器出口"这一格，其余五格按同一形状逐格补。
    """
    _, path = played
    assert cli.main(["audit", str(path)]) == 0
    stats = _last_json_block(capsys.readouterr().out)
    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[0])["meta"]
    assert stats["meta"]["reproducible"] is raw["reproducible"] is False


@pytest.mark.parametrize("cell", ["game_id", "deal_seed", "config_hash", "actor_kinds", "model"])
def test_audit_prints_this_meta_cell_with_the_value_the_file_carries(played, capsys, cell):
    """`#181` 的 K3 现形的那一格：`audit` 的 meta 是一串**拼出来的**键名，那一串里每一格都没有证人。

    05:38:37Z 那一趟（四本合跑、基线 177 个用例）把 `game.py` 里那个布尔整行摘掉，红的两条都在
    `tests/test_golden_game.py`，`tests/test_cli.py` 一条没红。所以那一句"这一格有人读，读它的就是那串键名"
    当时只能由键名推，不能由断言证。
    `#181` 补了 `reproducible` 那一格；这条按同一形状补余下五格，一格一个参数——谁从 `audit` 的出口里掉出去，
    红的那一条就点名谁（不是整串一起红，那样只会说"名单钉子松了"）。
    """
    _, path = played
    assert cli.main(["audit", str(path)]) == 0
    stats = _last_json_block(capsys.readouterr().out)
    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[0])["meta"]
    assert stats["meta"][cell] == raw[cell], f"{cell} 从机器出口里掉了，或出的不是这份文件自己的值"


def test_audit_prints_metrics_and_nothing_else(played, capsys):
    """`audit` is machine-readable by contract: one JSON object, no prose to parse around it,
    and the numbers are the M-keys a batch report also uses — not a parallel set of names for
    the same quantities."""
    _, path = played
    cli.main(["audit", str(path)])
    stats = _last_json_block(capsys.readouterr().out)
    assert set(stats) == {"meta", "synthetic", "events", "terminal", "torn_tail", "seq_damage",
                          "days", "kinds", "speech_acts", "assignment", "soft_flags",
                          "prompt_tokens_est", "prefix_cache",
                          "compactions",
                          "region_budget_check", "fallback_copy_check", "degraded_game",
                          "m2_illegal", "m3_gate", "m4_hallucination", "m5_style",
                          "m6_belief_action", "m7_cost", "m8_strategy"}
    assert not any(k in stats for k in ("fallback_rate", "latency_s", "repair_rung")), \
        "a second denominator for a rate metrics already owns"


def test_audit_reads_the_fold_rounds_out_of_the_requests_it_writes_them_into(tmp_path, played,
                                                                             capsys):
    """`compactions` 的键是几个不同的量，谁都不能替谁回答"这局折叠得厉害吗"。

    - `max_rounds`：单个 prompt 的折叠点推进过几天（`request.compactions`，每 prompt 一个数）
    - `prompts_folded`：有多少个 prompt 带着折叠发出
    - `events`：日志里有几格 `Kind.COMPACTION`，即出现过几种不同的折叠状态
    - `b2_prompts_on_the_floor` / `b2_worst_over_tokens`：天地板生效到何种程度——折到底仍然超
      软预算的那些 prompt 有几个、最狠的一个超了多少 tok（读 `request.b2_over_cap`）
    - `card_prompts_thinned` / `card_worst_claims_dropped`：主张卡被动过刀的 prompt 有几个、
      最狠的一条被砍掉几条指控（读 `request.card_claims_dropped`；单位是**条**不是 tok，因为
      那一刀砍的是行，而一行值多少 tok 随主张措辞变）
    - `last_fold`：最后一格标记记下的那次折叠状态长什么样——逐字窗口的条数与被折掉的日子
      （逐字抄 `payload.window` / `payload.folded_days`，不重算）。`events` 只说出现过几种
      状态，这一格说的才是最后那一种的形状；"最后一次发给模型的"不总是它，因为超硬天花板的
      prompt 按 `agent.py` 的口径不写标记

    前两个从 request 里读，所以手工往一份复制的日志里写 3 和 1 就能推动它们；第三个只能由
    真的标记事件推动（见 `tests/test_live_path.py::test_a_fold_the_model_was_shown_leaves_a_marker_in_the_log`）。
    一份"2 个折叠 prompt、0 格标记"的日志必须照实报 0：把它读成"这局没折叠"就是拿一个量替
    另一个量撒谎——标记落地之前跑的批次全都是这个形状。
    """
    _, path = played
    # 先量一份没被改过的：出厂预算下那一桌从没动过刀，所以两格都该是"测过了，没超"的 0。
    # 这一句必须是 0 而不是 null——`payload_for_log` 漏掉这个键，读出来的就是 null，而 null
    # 在那两格里说的是"这份日志还没这个字段"，一句完全不同的话。
    assert cli.main(["audit", str(path)]) == 0
    clean = _last_json_block(capsys.readouterr().out)["compactions"]
    assert clean["card_prompts_thinned"] == 0 and clean["card_worst_claims_dropped"] == 0, clean
    lines = path.read_text(encoding="utf-8").splitlines()
    seeded = 0
    for i, ln in enumerate(lines):
        rec = json.loads(ln)
        if rec["seq"] != 0 and rec.get("request"):
            rec["request"]["compactions"] = [3, 1][seeded]
            rec["request"]["b2_over_cap"] = [96, 0][seeded]
            rec["request"]["card_claims_dropped"] = [4, 0][seeded]
            lines[i] = json.dumps(rec, ensure_ascii=False)
            seeded += 1
            if seeded == 2:
                break
    assert seeded == 2, "this fixture log has no per-turn request records"
    forged = tmp_path / "forged.jsonl"
    forged.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert cli.main(["audit", str(forged)]) == 0
    stats = _last_json_block(capsys.readouterr().out)
    assert stats["compactions"] == {"max_rounds": 3, "prompts_folded": 2, "events": 0,
                                    "b2_prompts_on_the_floor": 1, "b2_worst_over_tokens": 96,
                                    "card_prompts_thinned": 1,
                                    "card_worst_claims_dropped": 4,
                                    "last_fold": None}, stats["compactions"]

    # 一格标记只动 `events`：另两个数读的是 request，折叠状态数不能替它们作证。
    marker = {"seq": 999, "kind": "compaction", "day": 2, "phase": "day_speech", "actor": None,
              "t_wall": 0.0, "visibility": "all",
              "payload": {"summary": "第1天：发言8人。 出局：无人。", "window": 4,
                          "folded_days": [1], "_idem": "compaction:c477d287922738af"},
              "request": {}, "response": {}, "attempts": [], "result": {}}
    marked = tmp_path / "marked.jsonl"
    marked.write_text("\n".join(lines + [json.dumps(marker, ensure_ascii=False)]) + "\n",
                      encoding="utf-8")
    assert cli.main(["audit", str(marked)]) == 0
    stats = _last_json_block(capsys.readouterr().out)
    assert stats["compactions"] == {"max_rounds": 3, "prompts_folded": 2, "events": 1,
                                    "b2_prompts_on_the_floor": 1, "b2_worst_over_tokens": 96,
                                    "card_prompts_thinned": 1,
                                    "card_worst_claims_dropped": 4,
                                    "last_fold": {"seq": 999, "window": 4,
                                                  "folded_days": [1]}}, stats["compactions"]
    assert stats["kinds"]["compaction"] == 1, "events 和 kinds 必须是同一个数，不是两份账"

    # 两格标记时"最后一格"必须真的是后写入的那一格：只有一格时 first==last，这条断言什么都没说。
    # 后一格窗口更大，抄的是 `data/real-20260924/20260924T153715Z_g00000302.jsonl` 的**顺序**（实测
    # 01:15:55Z：seq 55 窗口 9 条、seq 87 窗口 15 条；这里前一格沿用上面那个 window=4，两格 `_idem`
    # 也照抄那一份，虽然这条断言谁都不读它）。钉顺序而不钉极值：max/min 在这两份形状上都会碰巧蒙对。
    later = dict(marker, seq=1042,
                 payload=dict(marker["payload"], window=15, folded_days=[1, 2],
                              _idem="compaction:a990bd77b5e013ad"))
    twice = tmp_path / "twice.jsonl"
    twice.write_text("\n".join(lines + [json.dumps(marker, ensure_ascii=False),
                                        json.dumps(later, ensure_ascii=False)]) + "\n",
                     encoding="utf-8")
    assert cli.main(["audit", str(twice)]) == 0
    stats = _last_json_block(capsys.readouterr().out)
    assert stats["compactions"]["events"] == 2, stats["compactions"]
    assert stats["compactions"]["last_fold"] == {"seq": 1042, "window": 15,
                                                 "folded_days": [1, 2]}, stats["compactions"]

    # 字段落地之前的日志读成 `null`，不是 0："`b2_prompts_on_the_floor`: 0" 是一句好消息，
    # 而这份文件从没被测过地板，把它印成好消息就是替作者撒了个没人撒过的谎。
    lines2 = forged.read_text(encoding="utf-8").splitlines()
    stripped = 0
    for i, ln in enumerate(lines2):
        rec = json.loads(ln)
        if rec.get("request") and "b2_over_cap" in rec["request"]:
            del rec["request"]["b2_over_cap"]
            lines2[i] = json.dumps(rec, ensure_ascii=False)
            stripped += 1
    assert stripped >= 2, "这份日志没带上这一格，剥不了"
    old = tmp_path / "before-the-field.jsonl"
    old.write_text("\n".join(lines2) + "\n", encoding="utf-8")
    assert cli.main(["audit", str(old)]) == 0
    gone = _last_json_block(capsys.readouterr().out)["compactions"]
    assert gone["b2_prompts_on_the_floor"] is None and gone["b2_worst_over_tokens"] is None, gone
    assert gone["prompts_folded"] == 2, "同一份文件里另三个键照常读数，null 只属于没落盘的那一个"
    assert gone["card_prompts_thinned"] == 1 and gone["card_worst_claims_dropped"] == 4, \
        "剥掉 b2 的证人不能顺手把刀的读数一起抹掉：两格读的是不同的字段"


def test_a_log_without_the_knife_count_reports_it_as_unmeasured(played, tmp_path, capsys):
    """`card_claims_dropped` 缺席的那两格必须是 `null`，不是 0——和 b2 那一族同一个理由。

    反向对照在同一份文件里做完：只剥这一格，`b2_worst_over_tokens` 就得照常是数字。少了这半句，
    "把没找到的字段读成 0"这种写法可以一次污染两格而只红一条断言；而"这一批没有一个 prompt 被
    削过主张卡"是一句好消息，只有真的逐条数过才配印出来。
    """
    _, path = played

    def strip(rec):
        req = rec.get("request")
        if req and "card_claims_dropped" in req:
            del req["card_claims_dropped"]
        return rec

    lines = [json.dumps(strip(json.loads(ln)), ensure_ascii=False)
             for ln in path.read_text(encoding="utf-8").splitlines()]
    assert sum("card_claims_dropped" in ln for ln in lines) == 0, "剥不干净，这一条就没测到东西"
    old = tmp_path / "before-the-knife-field.jsonl"
    old.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert cli.main(["audit", str(old)]) == 0
    gone = _last_json_block(capsys.readouterr().out)["compactions"]
    assert gone["card_prompts_thinned"] is None and gone["card_worst_claims_dropped"] is None, gone
    assert gone["b2_worst_over_tokens"] == 0, "另一格的读数不该被牵连"


def test_audit_prints_an_unrecorded_verdict_as_null_not_as_false(played, tmp_path, capsys):
    """`degraded_game` 这一格不存在时，audit 要打印 `null`，不能打印 `false`。

    单个文件是唯一读者：这条判定的意义在于"退化局别混进结论"，而字段落地之前跑的批次在磁盘
    上永远存在。缺失误读成好消息，一份旧日志就会自己声明"这局引擎没替任何人做主"——没有人
    写过这个结论。反向对照（同一份文件删掉这一格之前必须报 `false`）证明 `null` 是那两行字节
    造成的，不是 audit 从来不看这一格。
    """
    _, path = played
    assert cli.main(["audit", str(path)]) == 0
    before = _last_json_block(capsys.readouterr().out)
    assert before["degraded_game"] is False, before

    lines = path.read_text(encoding="utf-8").splitlines()
    stripped = 0
    for i, ln in enumerate(lines):
        rec = json.loads(ln)
        if rec.get("kind") == "game_over" and "degraded_game" in rec["payload"]:
            del rec["payload"]["degraded_game"]
            del rec["payload"]["degraded_threshold"]
            lines[i] = json.dumps(rec, ensure_ascii=False)
            stripped += 1
    assert stripped == 1, "this fixture log carries no verdict to strip"
    old = tmp_path / "before-the-field.jsonl"
    old.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert cli.main(["audit", str(old)]) == 0
    assert _last_json_block(capsys.readouterr().out)["degraded_game"] is None


def test_audit_carries_no_fill_rate_for_a_table_that_never_called(played, capsys):
    """`m7_cost` 的兑现率要顺着 audit 一起出去，而 mock 桌上它必须是 null。

    audit 是这个读数面向产品的那一面（一份日志 → 一份 JSON → 批次报告）。替身桌从没发过 HTTP
    请求，`request` 里没有 `max_tokens`、`response` 里没有 `completion_tokens`；要是这里落成
    0.0，一份 mock 产物就在声称"模型一个 token 也没用完"——一个关于行为的结论，而它唯一的依据
    是这局根本没用模型。
    """
    _, path = played
    assert cli.main(["audit", str(path)]) == 0
    m7 = _last_json_block(capsys.readouterr().out)["m7_cost"]
    assert m7["fill_rate"] is None and m7["asked_total"] == 0 and m7["asked_calls"] == 0
    assert m7["n_calls"] == 0, "a mock seat never billed a call either"


def test_audit_without_the_flag_does_not_read_the_filesystem(played, capsys):
    """The default `audit` object must be reproducible from the log alone. Silently picking up
    whatever `data/calibration.json` happens to sit next to the CWD would make the same file
    print two different answers on two days, and the drift number is exactly the kind of
    external claim that has to name where it came from."""
    _, path = played
    cli.main(["audit", str(path)])
    stats = _last_json_block(capsys.readouterr().out)
    assert "calibration" not in stats
    assert "未标定" in stats["m7_cost"]["drift_note"]


def _sidecar(tmp_path, **over):
    body = {"ran_utc": "2026-09-20T18:45:36Z", "model": "gemma-4-26b-a4b-nvfp4",
            "constants": {"D_decode_tok_s": 41.2, "per_call_fixed_overhead_s": 0.83,
                          "P_prefill_tok_s_best_observed": 3664.2}}
    body.update(over)
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def test_audit_names_the_external_input_it_used_and_why_it_declined(played, tmp_path, capsys):
    """A half-done calibration run is the common case, not the edge case: the box was shared,
    the run died, the sidecar stayed on disk. `audit` must print the reason it could not use
    that file, in the same words `metrics` uses, rather than a bare `drift: null`."""
    _, path = played
    cal = _sidecar(tmp_path, constants={"D_decode_tok_s": None,
                                        "per_call_fixed_overhead_s": 0.83,
                                        "P_prefill_tok_s_best_observed": 3664.2})
    assert cli.main(["audit", str(path), "--calibration", str(cal)]) == 0
    stats = _last_json_block(capsys.readouterr().out)
    assert stats["calibration"]["usable"] is False
    assert "D_decode_tok_s" in stats["calibration"]["note"]
    assert stats["m7_cost"]["drift_note"] == stats["calibration"]["note"], \
        "两处各写一份『为什么没有 drift』，迟早一处说缺 D、另一处说文件不存在"


def test_audit_refuses_constants_fit_for_a_different_model(played, tmp_path, capsys):
    """The log records which model it was played against in its own seq-0 meta, so a mismatch
    is checkable without asking the user to remember which sidecar is current."""
    _, path = played
    cal = _sidecar(tmp_path, model="some-other-weights")
    assert cli.main(["audit", str(path), "--calibration", str(cal)]) == 0
    stats = _last_json_block(capsys.readouterr().out)
    assert stats["calibration"]["usable"] is False
    assert "some-other-weights" in stats["calibration"]["note"]
    assert stats["m7_cost"]["drift"] is None


def test_a_calibrated_sidecar_reaches_the_drift_self_check(played, tmp_path, capsys):
    """The whole point of the wiring: on a mock log there are no calls, so drift is
    `not_evaluable` and not a green light; the constants arrived, and the missing denominator
    is what is reported."""
    _, path = played
    cal = _sidecar(tmp_path)
    assert cli.main(["audit", str(path), "--calibration", str(cal)]) == 0
    stats = _last_json_block(capsys.readouterr().out)
    assert stats["calibration"]["usable"] is True
    assert stats["m7_cost"]["n_calls"] == 0
    assert stats["m7_cost"]["drift"]["verdict"] == "not_evaluable"


def _replay(played, extra):
    out, path = played
    cap = _capture(["replay", str(path), *extra])
    return cap


def _capture(argv):
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cli.main(argv) == 0, f"{' '.join(argv)} exited non-zero"
    return buf.getvalue()


def _last_json_block(text: str) -> dict:
    """`audit` prints exactly one indented JSON object; the prose around it is for humans."""
    try:
        return json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError as e:
        raise AssertionError(f"audit printed no JSON object ({e}):\n{text}") from None


# ------------------------------------------------------------------ export / watch (M6)
def _private_rendered(path):
    """Private events as any renderer would print them — the probe for "this artifact is a
    spectator's artifact". Rendered rather than payload text, because a `seer_result` has no
    payload text and only exists as a line."""
    from wolfengine.compress import render_line

    events, _ = cli.EventLog.read_records(path)
    return [render_line(e) for e in events if e.visibility != "all" and len(render_line(e)) > 14]


def test_export_writes_one_file_a_spectator_can_open(played, tmp_path, no_network):
    """`export` is the deliverable you send: one HTML, no server, no endpoint, and nothing
    private in it. Asserted by enumerating the private lines rather than by trusting `--god`
    being off."""
    _, path = played
    out = tmp_path / "review.html"
    assert cli.main(["export", str(path), "-o", str(out)]) == 0
    assert no_network == []
    doc = out.read_text(encoding="utf-8")
    assert "第1天" in doc and "<script" not in doc and "100.87.65.60" not in doc
    for line in _private_rendered(path):
        assert html_mod.escape(line) not in doc, line


def test_export_god_is_the_same_file_with_the_private_channel(played, tmp_path):
    _, path = played
    out = tmp_path / "god.html"
    assert cli.main(["export", str(path), "--god", "-o", str(out)]) == 0
    doc = out.read_text(encoding="utf-8")
    lines = _private_rendered(path)
    assert lines and all(html_mod.escape(l) in doc for l in lines), "god view hides the channel"
    assert "心里想" in doc


def test_export_lands_beside_the_log_when_no_output_is_named(played):
    """The default matters for the demo: `wolf export <file>` should be the whole command."""
    _, path = played
    assert cli.main(["export", str(path)]) == 0
    made = path.with_suffix(".html")
    assert made.exists() and "第1天" in made.read_text(encoding="utf-8")


def test_watch_once_prints_a_frame_without_a_key_or_a_call(played, capsys, no_network):
    """`--once` is the mode CI and the docs use, and the only proof that `watch` draws with the
    same function the tests assert on."""
    _, path = played
    assert cli.main(["watch", str(path), "--once"]) == 0
    assert no_network == []
    out = capsys.readouterr().out
    assert "第1天" in out and "本局不可复现" in out
    assert "[e17]" not in out and "法官（私发）" not in out


def test_watch_starts_on_one_seat_when_asked(played, capsys):
    _, path = played
    assert cli.main(["watch", str(path), "--once", "--seat", "7"]) == 0
    out = capsys.readouterr().out
    assert "7号视角" in out
    assert "9号视角" not in out, "one seat's head at a time"


# ------------------------------------------------------------------------------ batch / compare
def _batch_cmd(tmp_path, *extra):
    return ["batch", "--out", str(tmp_path), "--configs", "A,B", "--games", "1",
            "--seed0", "3", "--mock", *extra]


def test_batch_then_compare_leaves_the_pair_and_a_readable_report(tmp_path, capsys):
    assert cli.main(_batch_cmd(tmp_path, "--set", "B.temperature=0.6")) == 0
    man = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert man["arms"]["B"]["overrides"] == ["temperature"]
    for arm in ("A", "B"):
        assert len(list((tmp_path / arm).glob("*.jsonl"))) == 1, arm
    assert cli.main(["compare", str(tmp_path), "--axis", "temperature"]) == 1
    md = (tmp_path / "comparison.md").read_text(encoding="utf-8")
    assert "合成桌" in md and "wolf compare" in md, md
    assert "SYNTHETIC_TABLE" in capsys.readouterr().out
    # 复现命令是要被粘回终端的：`batch <dir>` 那种位置参数写法直接跑不通，而少了 `--mock`
    # 的一句"复现"会把两局合成桌变成 20 局付费请求。
    repro = [l for l in md.splitlines() if l.startswith("复现")][0]
    assert f"wolf batch --out {tmp_path}" in repro, repro
    assert "--mock" in repro and "--set B.temperature=0.6" in repro, repro


def test_a_typo_in_set_stops_the_batch_before_anything_is_written(tmp_path, capsys):
    assert cli.main(_batch_cmd(tmp_path, "--set", "B.temp=0.6")) == 2
    assert not (tmp_path / "run_manifest.json").exists()
    err = capsys.readouterr().err
    assert "temperature" in err, "打错的名字旁边要给正确写法"
    assert "配置错误：B:" in err, "两臂在场时错误要说清是谁的拼写"


def test_an_arm_may_not_ask_for_a_board_the_engine_does_not_have(tmp_path, capsys):
    """`--set A.seat_count=5` 过了类型校验，然后在 `roles.board_for` 里炸出 43 行 traceback、
    rc 1，而批次目录已经建出来了（06:26:38Z 实测）。"只有 9 席的板子存在"这句话住在 `roles`
    里，所以这里问它、不抄第二个 9；而 rc 1 在契约里是"引擎拒绝出结论"，一次拼错的参数不该
    占用它。正向对照同一条用例里给：9 这一臂必须打得完，否则"永远拒绝"也绿。
    """
    out = tmp_path / "b"
    rc = cli.main(_batch_cmd(out, "--set", "A.seat_count=5"))
    cap = capsys.readouterr()
    assert rc == 2, f"rc={rc}：命令写错了不是引擎拒绝\nerr 尾部：{cap.err[-200:]!r}"
    assert "Traceback" not in cap.err, "到终端是一行报告，不是 43 行栈"
    assert cap.err.startswith("配置错误：") and "seat_count" in cap.err, cap.err
    assert "5" in cap.err, f"要把收到的那个数说回去：{cap.err!r}"
    assert not out.exists(), "地板站在建目录之前（#58 同一条理由）"
    ok = tmp_path / "ok"
    assert cli.main(_batch_cmd(ok, "--set", "A.seat_count=9")) == 0


def _nothing_cmd(kind: str, out: Path, games: str) -> list[str]:
    """两个都收 `--games` 的动词，取到同一个 0。"""
    if kind == "run":
        return ["run", "--mock", "--seed", "11", "--games", games, "--quiet", "--out", str(out)]
    return ["batch", "--mock", "--configs", "A,B", "--seed0", "3", "--games", games,
            "--out", str(out)]


@pytest.mark.parametrize("kind", ["run", "batch"])
@pytest.mark.parametrize("games", ["0", "-3"])
def test_a_batch_of_nothing_is_refused_before_anything_exists(kind, games, tmp_path, capsys,
                                                             no_network):
    """`--games` 的地板只有一个谓词、两个读者，而且必须站在落盘之前。

    修前实测（05:51:09Z）：`batch --games 0` 先把 `run_manifest.json` 写出去（`pair_keys`
    是空的），然后在摘要行读 `pair_keys[0]` 时 IndexError —— traceback 顶到终端，退出码 1，
    而 1 在这个仓库里是"引擎拒绝出结论"。`run --games 0` 更安静：rc 0、什么都不产。
    """
    out = tmp_path / "o"
    rc = cli.main(_nothing_cmd(kind, out, games))
    err = capsys.readouterr().err
    assert rc == 2, f"rc={rc} err={err!r}：0 局是命令写错了，不是引擎拒绝"
    assert err.startswith("配置错误：") and "--games" in err, err
    assert games in err, f"要把收到的那个数说回去：{err!r}"
    assert not out.exists(), (f"{kind} 在地板之前就把 {out} 建出来了——留下一个能被 "
                              "`compare` 读半天的空壳")
    assert capsys.readouterr().out == ""
    assert no_network == []


def test_a_set_for_an_arm_that_was_not_named_is_refused(tmp_path, capsys):
    assert cli.main(_batch_cmd(tmp_path, "--set", "C.temperature=0.6")) == 2
    err = capsys.readouterr().err
    assert "C" in err and "--configs" in err, err


def test_compare_on_a_directory_without_a_manifest_is_a_usage_error(tmp_path, capsys):
    assert cli.main(["compare", str(tmp_path / "nope")]) == 2
    assert "run_manifest" in capsys.readouterr().err


def test_a_real_batch_without_a_key_stops_before_the_endpoint(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(Config().api_key_env, raising=False)
    assert cli.main(["batch", "--out", str(tmp_path), "--configs", "A,B",
                     "--set", "B.temperature=0.6", "--games", "1"]) == 2
    assert Config().api_key_env in capsys.readouterr().err
    assert not (tmp_path / "run_manifest.json").exists()


def test_a_number_from_the_command_line_keeps_its_field_type(tmp_path):
    """`--set B.max_tokens_speech=200` 如果被读成 float，config_hash 就悄悄变了：两臂差在一根
    谁也没声明的类型轴上，而 compare 只会看见"值差不多、哈希不同"。"""
    assert cli.main(_batch_cmd(tmp_path, "--set", "B.temperature=0.6",
                               "--set", "B.max_tokens_speech=200")) == 0
    man = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    v = man["arms"]["B"]["config"]["max_tokens_speech"]
    assert v == 200 and isinstance(v, int), v
    assert man["arms"]["B"]["overrides"] == ["temperature", "max_tokens_speech"]


def test_a_tuple_field_can_be_the_axis(tmp_path, capsys):
    """`temperature_ladder` 正是 plan §7 的处理变量之一，而复现命令把它印成 `[0.9, 1.1]`：
    `--set` 读不回同一个值的话，"打印得出来"和"粘得回去"就是两回事。"""
    assert cli.main(_batch_cmd(tmp_path,
                               "--set", "B.temperature_ladder=[0.9,1.1]")) == 0
    man = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert man["arms"]["B"]["config"]["temperature_ladder"] == [0.9, 1.1]
    assert cli.main(["compare", str(tmp_path), "--axis", "temperature_ladder"]) == 1
    md = (tmp_path / "comparison.md").read_text(encoding="utf-8")
    assert "--set B.temperature_ladder=[0.9, 1.1]" in md, md


def test_a_malformed_list_value_is_a_usage_error_not_a_traceback(tmp_path, capsys):
    assert cli.main(_batch_cmd(tmp_path, "--set", "B.temperature_ladder=[0.9,")) == 2
    assert "读不出来" in capsys.readouterr().err


# --------------------------------------------- the region caps travel in the same log
def test_a_log_without_the_sub_block_sizes_reports_them_as_unmeasured(played, tmp_path, capsys):
    """老日志的 `region_tokens` 里没有 B0/C1–C4 五格：那五格必须报 None，不能报 0。

    和 `test_a_log_without_the_caps_in_meta_prints_null_not_zero` 是同一族错，只是方向不同：
    那边缺尺子，这边尺子在、被量的东西没落盘，读起来仍然是"每一格都舒舒服服待在上限以内"。
    出厂预算下这五格的观测是 71/57/328/111/115
    （11:12:16Z 三局 mock 实测），全都低于上限，所以"0"和"None"在这一局里数字不同、
    含义差别更大——把没测到读成测到了 0 超额，是一个永远不会自己变红的谎。
    """
    _, path = played

    def strip(rec):
        rt = (rec.get("request") or {}).get("region_tokens")
        if rt:
            for key in ("B0", "C1", "C2", "C3", "C4"):
                rt.pop(key, None)
        return rec

    old = _rewritten(path, tmp_path, "no-subblocks.jsonl", strip)
    assert cli.main(["audit", str(old)]) == 0
    check = _last_json_block(capsys.readouterr().out)["region_budget_check"]
    for key in ("B0", "C1", "C2", "C3", "C4"):
        assert check["worst_over"][key] is None, f"{key} 被读成了『测到了，没超』：{check}"
    assert check["worst_over"]["B2"] == 0, "别的那几格缺读数不该把 B2 已有的观测一起抹掉"


# --------------------------------------------- the region caps travel in the same log
def _rewritten(path, tmp_path, name, edit):
    lines = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        lines.append(json.dumps(edit(json.loads(ln)), ensure_ascii=False))
    out = tmp_path / name
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def test_audit_checks_region_sizes_against_the_caps_in_the_same_log(played, capsys):
    """`region_tokens` 是九段没有尺子的长度（`#63` 之前只有四段）。上限今天也落进 meta 了，所以一份文件自己能判。

    `worst_over` 从 `region_tokens` 减出来，`b2_witness_agrees` 再拿它去核对写盘那一刻记的
    `b2_over_cap`——同一份文件里的两份口径必须互相检验，对不上就是有一格被改过或来自别的代码
    版本，而不是"两份都信"。
    """
    _, path = played
    assert cli.main(["audit", str(path)]) == 0
    check = _last_json_block(capsys.readouterr().out)["region_budget_check"]
    rb = Config().regions
    assert check["caps"] == {"a_hard": rb.a_hard, "b1": rb.b1, "b2": rb.b2, "c_total": rb.c_total,
                             "b0": rb.b0, "c_persona": rb.c_persona, "c_belief": rb.c_belief,
                             "c_private": rb.c_private, "c_task": rb.c_task}, check
    assert set(check["worst_over"]) == {"A", "B1", "B2", "C", "B0", "C1", "C2", "C3", "C4"}, check
    assert check["worst_over"]["C"] == 0, "出厂预算下 C 的最大观测是 585，上限 1450"
    assert check["b2_witness_agrees"] is True, check


def test_a_forged_b2_witness_disagrees_with_the_sizes_it_should_come_from(played, tmp_path,
                                                                          capsys):
    """把**某一条** prompt 的 `b2_over_cap` 改大，交叉校验必须翻脸。

    只动一条是这一条用例的全部难点：一批全改的话，"所有格都对得上才算一致"和"有一条对得上
    就算一致"打印出同一个 `false`，后者那种写法就混过去了——而"这一份日志被人改过一格"恰恰
    就是几条坏、多数好的形状。反向对照在同一份文件里做：只动 `b2_over_cap`、不动
    `region_tokens.B2`，则除了这一格以外没有一个读数会变——`b2_worst_over_tokens`
    是读那一格自己算的，它跟着变正是"两份账本其中一份被人写过"的形状，而这一格单看永远看不出问题。
    """
    _, path = played
    seen = []

    def forge(rec):
        req = rec.get("request")
        if req and "b2_over_cap" in req and not seen:
            seen.append(1)
            req["b2_over_cap"] = 9001
        return rec

    forged = _rewritten(path, tmp_path, "forged-witness.jsonl", forge)
    assert cli.main(["audit", str(forged)]) == 0
    stats = _last_json_block(capsys.readouterr().out)
    assert stats["region_budget_check"]["b2_witness_agrees"] is False, stats
    assert stats["region_budget_check"]["worst_over"]["B2"] == 0, \
        "从 region_tokens 减出来的那格不该跟着伪造走"
    assert stats["compactions"]["b2_worst_over_tokens"] == 9001


def test_a_forked_act_moves_the_final_rate_and_leaves_the_first_try_one_alone(played, tmp_path,
                                                                              capsys):
    """`assigned_act` 在 audit 里有了读者，两格各自读一样东西，改动只该落在其中一格上。

    mock 桌由构造必然听指派（`actors.py` 就照 `legal.assigned_act` 出牌），所以真读数要等端点；
    这里能钉住的是**接线**与**分辨力**：把某一条 speech 的 `payload.act` 改成别的一个字，
    `obeyed_final` 必须掉下来，而 `obeyed_first_try` 不许跟着掉——它读的是打回原因，那条记录里
    从没出现过 `act_not_as_assigned`。两格一起动就说明其中一格读错了东西，而那正是"两个数读起来
    一样"最容易被放过去的形状。`by_assigned` 数的是**指派**的分布，与座位最后做了什么无关。
    """
    _, path = played
    assert cli.main(["audit", str(path)]) == 0
    base = _last_json_block(capsys.readouterr().out)["assignment"]
    assert base["turns"] > 0 and base["obeyed_first_try"] == 1.0 == base["obeyed_final"], base

    seen = []

    def fork(rec):
        pay = rec.get("payload") or {}
        if (rec.get("kind") == "speech" and (rec.get("request") or {}).get("assigned_act")
                and pay.get("act") and not seen):
            seen.append(1)
            pay["act"] = "listen" if pay["act"] != "listen" else "accuse"
        return rec

    forged = _rewritten(path, tmp_path, "forked-act.jsonl", fork)
    assert cli.main(["audit", str(forged)]) == 0
    out = _last_json_block(capsys.readouterr().out)["assignment"]
    assert seen, "这份 mock 局里一条带指派的 speech 都没有，上面那句断言就成了自证"
    assert out["turns"] == base["turns"] and out["by_assigned"] == base["by_assigned"], out
    assert out["obeyed_first_try"] == 1.0, out
    assert out["obeyed_final"] < 1.0, out


def test_audit_says_when_the_endpoint_never_answered_about_the_cache(played, tmp_path, capsys):
    """一次调用没落进"端点没说 cached"这一档，读出来必须是 `null`，不是 0.0。

    `--mock` 桌写出的 `response` 是空的（`{}`，量不出成本），所以这里补的是"一局真跑过、端点每次都
    报了 prompt_tokens 与延迟，唯独没报 `usage` 那一块"——13:19:26Z 直接读一份 mock 日志确认过形状。
    这一档要单独钉：把 null 印成 0.0，一份日志就会看起来像"跑过了、前缀一次都没复用"，而 plan §5 的
    经济性正是拿这个比值算折扣的——一个假的 0 比缺数更贵。
    """
    _, path = played

    def answered_without_cache(rec):
        if (rec.get("kind") == "speech" and isinstance(rec.get("response"), dict)
                and (rec.get("payload") or {}).get("meta")):
            rec["response"] = {"latency_s": 4.0, "prompt_tokens": 900, "completion_tokens": 30}
        return rec

    forged = _rewritten(path, tmp_path, "no-usage.jsonl", answered_without_cache)
    assert cli.main(["audit", str(forged)]) == 0
    out = _last_json_block(capsys.readouterr().out)["prefix_cache"]
    assert out["calls"] > 0 and out["reported"] == 0, out
    assert out["silent"] == out["calls"], out
    assert out["reuse_ratio"] is None and out["cached_tokens"] is None, out


def test_a_usage_block_written_into_the_log_moves_the_reuse_ratio(played, tmp_path, capsys):
    """接线之外还要有分辨力：给一半的调用补上 `usage`，比值就该从 `null` 变成一个数。

    端点此刻关着（13:10:28Z 探过：2 秒内没有 TCP 响应），真读数要等 M0 复跑。这条钉的是"日志里一旦
    出现这个字段，机器出口读得到"，并且只补 seq 为奇数的那一半——`reported` 与 `silent` 必须分开数，全算
    进分母的话，一个从一半调用里读出来的命中率会看起来像从整批读出来的。
    """
    _, path = played
    forged_n = 0

    def forge(rec):
        nonlocal forged_n
        if (rec.get("kind") == "speech" and isinstance(rec.get("response"), dict)
                and rec.get("seq") and (rec.get("payload") or {}).get("meta")):
            response = {"latency_s": 4.0, "prompt_tokens": 900, "completion_tokens": 30}
            if int(rec["seq"]) % 2:
                # 计数器跟着"写没写这块"走，不跟着"这条记录改没改"走：两条分支都改记录，
                # 取反的话 `reported` 会去对上一个补数一半、静默一半的假数字。
                response["usage"] = {"prompt_tokens": 900, "cached_tokens": 25}
                forged_n += 1
            rec["response"] = response
        return rec

    forged = _rewritten(path, tmp_path, "usage-block.jsonl", forge)
    assert cli.main(["audit", str(forged)]) == 0
    out = _last_json_block(capsys.readouterr().out)["prefix_cache"]
    assert out["reported"] == forged_n > 0, out
    assert 0 < out["reported"] < out["calls"], "一半补数、一半没补，两格却分不出来"
    assert out["cached_tokens"] == 25 * forged_n and out["prompt_tokens"] == 900 * forged_n, out
    assert out["reuse_ratio"] == round(25 / 900, 4) and out["unpairable"] == 0, out


def test_a_log_without_the_caps_in_meta_prints_null_not_zero(played, tmp_path, capsys):
    """`meta.regions` 缺席 = 这份日志落地时还没有这个字段，不是"上限是 0"。

    少一格就把整块判据读成 0，等于让老批次自己声明"每一段都舒舒服服待在上限以内"——那句话
    没人写过。`b2_worst_over_tokens` 是另一回事：它读的是每 prompt 的 `b2_over_cap`，字段还在，
    所以那一格必须照常打印数字，只有需要尺子的两格变 `null`。刀的读数同属"用不着尺子"那一族：
    它数的是砍掉的行数，没有上限可减，所以缺 `meta.regions` 时它必须照旧是 0。
    """
    _, path = played

    def strip(rec):
        if "regions" in (rec.get("meta") or {}):
            del rec["meta"]["regions"]
        return rec

    old = _rewritten(path, tmp_path, "no-caps.jsonl", strip)
    assert cli.main(["audit", str(old)]) == 0
    stats = _last_json_block(capsys.readouterr().out)
    check = stats["region_budget_check"]
    assert check["caps"] is None and check["worst_over"] is None, check
    assert check["b2_witness_agrees"] is None, check
    assert stats["compactions"]["b2_worst_over_tokens"] == 0, "这一格用不着尺子"
    assert check["card_prompts_thinned"] == 0, "缺尺子不该顺手把刀的读数一起读成 null"
    assert stats["compactions"]["card_worst_claims_dropped"] == 0, \
        "两出口共用同一格：这里 null、audit 里 0，就是同一件事的两个答案"


def test_an_inflated_b2_size_with_a_stale_witness_disagrees(played, tmp_path, capsys):
    """另一侧伪造：只抬高 `region_tokens.B2`、不动写盘那刻记的 `b2_over_cap`。

    "这一格超了 1100 tok"听起来是个关于字节的事实,而同一格的文件里还写着 0。两份读数对不上
    必须翻脸,所以比较不能写成单向宽容的 `>=`：1100 至少是比 0 大,那句"窗口被改写过、计数器
    忘了跟着改"恰好从 `>=` 底下溜过去。这一格的正数读数也顺手钉住每区用的是自己那把尺。
    """
    _, path = played

    def inflate(rec):
        rt = (rec.get("request") or {}).get("region_tokens")
        if rt:
            rt["B2"] = 2600
        return rec

    inflated = _rewritten(path, tmp_path, "inflated-b2.jsonl", inflate)
    assert cli.main(["audit", str(inflated)]) == 0
    check = _last_json_block(capsys.readouterr().out)["region_budget_check"]
    assert check["worst_over"]["B2"] == 1100, check
    assert check["worst_over"]["C"] == 0, "另一格不该跟着一起被抬高"
    assert check["b2_witness_agrees"] is False, check


def test_audit_names_the_prompts_that_went_over_a_cap_the_operator_set(tmp_path, capsys):
    """上限掐到地板以下（C 的地板是 335 tok），超预算的那一格才第一次有读数可读。

    这一条同时钉住两件事：尺子取自这一臂自己的 `meta.regions`（`--set` 覆盖过的那份），不是
    出厂 `Config()`；`worst_over` 取的是各 prompt 的**最大**观测——写盘那一刻的瘦身已经把 C 压
    到它所能达到的最低，所以只有 max 才看得见"压不下去"这件事，峰以外的每一格都是 0。
    """
    assert cli.main(["batch", "--out", str(tmp_path), "--configs", "A", "--games", "1",
                     "--seed0", "7", "--mock", "--set", "A.regions.c_total=250"]) == 0
    log = sorted((tmp_path / "A").glob("*.jsonl"))[0]
    peaks = [r["request"]["region_tokens"]["C"]
             for r in (json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines())
             if (r.get("request") or {}).get("region_tokens")]
    assert cli.main(["audit", str(log)]) == 0
    check = _last_json_block(capsys.readouterr().out)["region_budget_check"]
    assert check["caps"]["c_total"] == 250, "读的是这一臂的上限，不是出厂值"
    assert check["worst_over"]["C"] == max(peaks) - 250 > 0, (check, max(peaks))
    assert check["worst_over"]["B2"] == 0 and check["b2_witness_agrees"] is True, \
        "改 C 的上限不该动到 B2 的两份账"


def test_the_two_readers_of_the_region_budget_share_one_implementation(played, capsys):
    """`audit` 印的那块和批次报告印的那行必须来自同一个函数，不是两份算术。

    这份文件里"同一个率两处实现"已经栽过两次（`act`/`action` 两个字段名、fallback 两个分母）。
    本用例不管数字对不对（上面四条管），只管两个读取点是不是同一份代码：任何一处换成第二份
    实现——哪怕是"看起来一样"的第二份——它就得红。
    """
    _, path = played
    g = metrics.read_game(path)
    assert cli.main(["audit", str(path)]) == 0
    printed = _last_json_block(capsys.readouterr().out)["region_budget_check"]
    assert printed == metrics.region_budget_check(g), "audit 印的不是 metrics 那个函数的输出"
    arm = batch.region_budget_by_arm([g])
    assert arm["worst_over"] == printed["worst_over"], arm
    assert arm["games_over"] == {k: int(v > 0) for k, v in printed["worst_over"].items()}, arm
    assert arm["witness_disagreements"] == (0 if printed["b2_witness_agrees"] else 1), arm


# ----------------------------------------------------------- compare 的机器侧出口（#121）
def test_a_compare_verdict_is_readable_without_parsing_the_prose(tmp_path, capsys):
    """`audit` 一局一个 JSON 对象，`compare` 只有 `comparison.md`：臂级那九格读数（M1 胜率、
    M3★ 判定、degraded、编号破损、末行截断、两份 `fallback` 拷贝对账、区域预算、前缀缓存、
    配对分母）只活在散文里，机器要拿就得 grep 中文。

    `#120` 刚把"哪几个文件的 `fallback` 对不上"印进报告，而它的下一句话就是"那谁来自动查"——
    钉的不是"有没有 JSON"，是**这一格和 markdown 是不是同一份数**：断言的是 `compare()` 返回的
    键集，所以以后往报告里加一格，它就一起进 JSON，不需要谁来记得第二份名单。
    """
    from test_batch_paired import _as_real_table
    assert cli.main(_batch_cmd(tmp_path, "--set", "B.temperature=0.6")) == 0
    _as_real_table(tmp_path)
    assert cli.main(["compare", str(tmp_path), "--axis", "temperature", "--json"]) == 0
    js = json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8"))
    cells = batch.compare(tmp_path, axis=("temperature",))
    assert set(js) == set(cells) - {"markdown"}, (
        f"少了：{sorted(set(cells) - set(js) - {'markdown'})} 多了：{sorted(set(js) - set(cells))}")
    assert "markdown" not in js, (
        "同一份渲染落两个地方：改口的时候只有一个是真的。散文自有 `comparison.md`，JSON 只装读数")
    assert js["fallback_copies"]["A"]["n_compared"] > 0 and js["m1"]["A"]["n_games"] == 1, js
    assert "comparison.json" in capsys.readouterr().out, "写了盘却不说写到哪，等于没写"


def test_a_refused_compare_still_lands_on_disk_without_inventing_measurements(tmp_path):
    """拒绝也是一份报告（`cmd_compare` 对 markdown 就是这么定的），所以 `--json` 不能只在出结论时
    才落盘——否则"这一批没有 JSON"在脚本眼里既可能是"没敲 `--json`"，也可能是"被拒了"。

    这一条真正钉的是**缺席的写法**：被拒批次里那九格臂级读数必须**不存在**，而不是被填成 0。
    带臂名键空间（`{"A": …, "B": …}`）的格子全在合成桌闸门之后才算，所以这里量的就是"拒绝不许
    把没算出来的东西印成算出来了"。
    """
    assert cli.main(_batch_cmd(tmp_path, "--set", "B.temperature=0.6")) == 0
    assert cli.main(["compare", str(tmp_path), "--axis", "temperature", "--json"]) == 1
    js = json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8"))
    assert js["verdict"] == "SYNTHETIC_TABLE" and js["why"], js
    for cell in ("m1", "m3_verdict", "torn", "fallback_copies", "numbering", "degraded",
                 "region_budget", "prefix_cache", "n_pairs"):
        assert cell not in js, f"{cell} 在被拒批次里被印成了存在的样子"
    assert js.get("stats") is None or js["verdict"] != "OK", (
        "`stats` 是合成桌那一批留在盘上的逐条率（paired:305 有用例钉着它不许抹掉），"
        "它只属于拒绝路径：OK 也带它，就等于同一份率有两个落点")


def test_the_json_lands_beside_whichever_markdown_was_asked_for(tmp_path):
    """`-o` 改的是 markdown 落点，JSON 跟着它走而不是钉死在批次目录：一次比较要留档的人，两个
    产物分在两个目录里就一定会有一个被忘掉。默认（不敲 `--json`）则什么都不许多产。
    """
    assert cli.main(_batch_cmd(tmp_path, "--set", "B.temperature=0.6")) == 0
    out = tmp_path / "kept"
    out.mkdir()
    assert cli.main(["compare", str(tmp_path), "--axis", "temperature",
                     "-o", str(out / "my.md")]) == 1
    assert not list(out.glob("*.json")) and not (tmp_path / "comparison.json").exists(), (
        "没要机器侧的时候凭空多产一份，是把默认路径变成了第二条链")
    assert cli.main(["compare", str(tmp_path), "--axis", "temperature",
                     "-o", str(out / "my.md"), "--json"]) == 1
    assert (out / "my.json").exists(), "JSON 落在了批次目录，而 markdown 在 `-o` 指的地方"


# --------------------------------------------------- README 的演示块整块能跑（#136）
DEMO_HEADING = "## 三分钟离线演示"
# 一块一条：每条命令只留一句"别人替不了它"的读数。挑标记前先按 `head -3` 的那个窗口逐命令实测
# 过谁印了什么（`/tmp/percmd136.txt`，2026-09-26T07:10Z）——`[e1] 法官：开局座位` 那种三条命令
# 都会印的句子在这里没有归因力，K2 那一具刀（flag 打错、只剩另外两条 replay 在说话）就是它放过的。
# 一处限界要说清楚：`--seat 3` 那条的归因是**窗口相对**的——席位视图是上帝视图的子集，所以
# `[e4] 法官（私发）` 只在文档写着 `| head -3` 时才是那一行独有的。负控制 C2（把两处窗口放到 4 行）
# 量的正是这条限界：它照样绿，因为那时 `--god` 也印得出 e4。
DEMO_MARKERS = {
    '_g00000007.jsonl': "run 写下的是哪一份（`echo \"$LOG\"` 那一行）",
    "[e2] 法官（私发）": "replay --god：发牌行只有上帝视角看得见",
    "[e4] 法官（私发）": "replay --seat 3：这一席自己的那张牌",
    "复盘 -> ": "export 落的文件",
    "狼人杀直播": "watch 的那一帧",
    '"game_id"': "audit 的 JSON",
}


def _doc_block(doc: Path, heading: str) -> str:
    """那一节里那个 ```bash 块，原样，一个字不改——README 和 `docs/` 那几本同一个取法。

    测试不抄一份"等价"的命令清单：抄写就是第二份主张，而两份会在下一次改文档时各自演化
    ——`#136` 那一片的 bug 恰恰是文档里那句 shell 和目录的真实内容对不上。
    标题按整行相等找，不按前缀：`find()` 会把 `## 三分钟离线演示x` 也算命中（K4 那具刀量出来的）。
    `doc` 是相对仓库根的路径：`#197` 把同一套机械接到 `docs/comparison.md` 时，取块这一段
    本来可以照抄一份，那就是 `#153` 反对的那第二份实现。
    """
    text = (Path(__file__).resolve().parents[1] / doc).read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        at = next(i for i, l in enumerate(lines) if l.strip() == heading)
    except StopIteration:
        raise AssertionError(f"{doc} 里没有整行等于 {heading!r} 的标题") from None
    rest = lines[at + 1:]
    try:
        opened = next(i for i, l in enumerate(rest) if l.strip() == "```bash")
    except StopIteration:
        raise AssertionError(f"{heading} 这一节后面没有 ```bash 块") from None
    try:
        closed = next(i for i in range(opened + 1, len(rest)) if rest[i].strip() == "```")
    except StopIteration:
        raise AssertionError("bash 块没有收尾的围栏") from None
    block = "\n".join(rest[opened + 1:closed]) + "\n"
    assert block.strip(), "围栏里的块是空的——那这条用例就是在空转"
    return block


def _bash_the_block(tmp_path: Path, block: str, name: str, stdin: str | None):
    """把文档围栏里的文本原样写成脚本、交给真 bash 跑，返回那条子进程。

    这一跑**不在** `no_network` 的保护范围里：那个夹具 patch 的是本进程的 socket 与 transport，
    而这里发命令的是子进程。所以调用它之前，块里不许留有会拨端点的命令行——那道前置断言写在
    调用方，不在这里兜底：这里若悄悄替子进程挡网络，就等于把"这一块能不能整块粘贴"改成
    "这一块在人造的无网环境里能不能粘贴"，是两句不同的话。
    """
    import os
    import shutil
    import subprocess
    import sys

    bin_dir = str(Path(sys.executable).parent)
    assert shutil.which("wolf", path=bin_dir), (
        f"`{bin_dir}` 上没有 `wolf`，这一跑等于什么都没执行")
    script = tmp_path / f"{name}.sh"
    script.write_text(block, encoding="utf-8")
    kw = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    return subprocess.run(["bash", str(script)], cwd=tmp_path, text=True,
                          capture_output=True,
                          env={**os.environ,
                               "PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")},
                          **kw)


HUMAN_STDIN = "票 5 先听听\n票 5 我投他\n过 没想好\n"


def _block_statements(block: str) -> list[tuple[str, str]]:
    """把围栏文本切成 `(种类, 原文)`：`live` 是一条完整命令，`comment` 是整行注释。

    `live` 存的是**原样的那几行**（续行不合成一行）：反斜杠续行是 README 交给读者的形状，
    测试若拿合并后的单行去执行，证的就是"另一种粘贴方式"能跑，而不是这一块能跑。
    """
    statements, pending = [], []
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            assert not pending, "注释行夹在一条续行的中间，切分器读不懂这个形状"
            statements.append(("comment", line.strip()))
            continue
        pending.append(line)
        if not line.rstrip().endswith("\\"):
            statements.append(("live", "\n".join(pending)))
            pending = []
    assert not pending, "围栏末尾挂着一条没写完的续行"
    return statements


def _dialer_statements(live: list[str]) -> list[str]:
    """这一堆命令行里，哪些会把局域网判官拨起来。

    两处都要收着：判据得**看完一整条**逻辑命令再说话——README 的 `batch` 那条用反斜杠续行，
    `--mock` 落在第二行上，逐行判断会把这条干净的命令当成拨号的那一条（第一版就是这么误伤的）；
    判据又得把行内注释切掉——`wolf run --god   # 其实有 --mock` 里注释上的 `--mock`
    不是"这一条不碰端点"的理由。反过来 `LOG=$(wolf run …)` 包在赋值里的必须算，所以看的是
    这一条里**出现** `wolf run` / `wolf batch`，不是它开头是什么。
    """
    dialers = []
    for text in live:
        code = " ".join(re.split(r"[ \t]+#.*$", line, maxsplit=1)[0]
                        for line in text.splitlines())
        if (("wolf run" in code or "wolf batch" in code)
                and "--mock" not in code and "--dry-run" not in code):
            dialers.append(text.strip())
    return dialers


def test_the_readme_demo_block_runs_verbatim(tmp_path, no_network):
    """把人照着 README 敲的那一段整块交给 bash，看它有没有真打到一局日志上。

    现场是 README 上一节自己造出来的：`--dry-run --out data` 与 `run --out data` 共用一个
    目录，于是那个目录里合法地住着两种 `*.jsonl`。引擎的两个读取器都把转储按名字跳过
    （`metrics.read_dir` / `batch.read_arm`），演示块里那句 `LOG=` 是同一句判据的第三份
    抄写——而它此前那份抄错了：`ls data/*.jsonl | tail -1` 挑中的是转储，因为 `g` 排在任何
    时间戳后面（这条用例在修好前的第一次实跑里，六条命令各印了一句「这不是一局日志」）。
    """
    (tmp_path / "data").mkdir()
    assert cli.main(["run", "--dry-run", "--seed", "22", "--out", str(tmp_path / "data")]) == 0
    assert no_network == []
    dumps = sorted(p.name for p in (tmp_path / "data").glob("*.prompts.jsonl"))
    assert dumps, "夹具没能造出「同一目录里两种 jsonl」这个现场，用例就在空转"

    block = _doc_block(Path("README.md"), DEMO_HEADING)
    proc = _bash_the_block(tmp_path, block, "demo", None)
    evidence = (f"--- 块 ---\n{block}\n--- stdout ---\n{proc.stdout}\n"
                f"--- stderr ---\n{proc.stderr}")
    assert proc.returncode == 0, f"这一块的退出码是 {proc.returncode}：{evidence}"
    for marker, who in DEMO_MARKERS.items():
        assert marker in proc.stdout, f"{who} 没印出来（标记 {marker!r}）：{evidence}"
    assert "这不是一局日志" not in proc.stdout + proc.stderr, evidence


# ------------------------------------------- README〈人怎么上桌〉那一块整块能跑（#137）
HUMAN_HEADING = "## 人怎么上桌：`--human 座位`"
# 一块一条，和 `#136` 那块同一口径，但这里的"一条"要更严：`桌边坐着一个真人` 那一行 `run` 收尾
# 时也印（实测 `run.out:205`，2026-09-26T07:37Z），所以第一版拿它当 replay 的独证，被 K4 那具刀
# （把 replay 换成 `true`）当场放过——改成了数到 2：一局里只有"打完的那一屏"和"重看的那一屏"
# 各说一句。`〔私有〕` 这个后缀只有读取侧印，卡片上不落（`run.out` 里 grep 它是 0 命中）。
HUMAN_MARKERS = {
    "轮到你了：3 号": "run --mock --human 3：终端前的人真的拿到了一张卡片",
    "[e4] 法官（私发）：你的身份是 villager。〔私有〕": 'replay "$HLOG" --seat 3：3 号那一席自己的那张牌',
    "3号：我投他": "人打的那一行进了这一局，而不是被丢弃后又由引擎替答出一句别人的话",
}
# `--god` 的第一屏就是别人的发牌行（实测 seed 7 的 `[e2]/[e3]/[e5]` 三条 wolf），3 号看不到。
# 拿它的缺席当证人，比拿"3 号那一行出现了"更能钉住视图：出现这一行的视图有两个。
HUMAN_ABSENT = "[e2] 法官（私发）"
HUMAN_NOTICE = "桌边坐着一个真人"


def test_the_human_seat_block_runs_verbatim(tmp_path):
    """把人照着〈人怎么上桌〉敲的那一段整块交给 bash，stdin 递三行答案进去。

    这一块的修法是把需要端点的那一条挪进注释——和 `#136` 为 `--calibration` 立的同一条规矩，
    理由在这里更硬：`_bash_the_block` 跑的是子进程，`no_network` 那层夹具挡不住它，所以
    "块里没有会拨判官的命令行"必须是执行之前的前置断言，不能靠事后没报错来侥幸。

    夹具往 `data/human` 里先放一局**没有真人**的旧日志（文件名钉成 2000 年，好让 `tail -1`
    和 `head -1` 分出胜负）。没有这一格，`HLOG=` 那句"挑到你刚打的那一局"就没有对立面：
    目录里只有一个文件时，`tail -1` 换成 `head -1` 是同一句话（K7 那一具刀量出来的）。
    """
    import shutil

    block = _doc_block(Path("README.md"), HUMAN_HEADING)
    live = [text for kind, text in _block_statements(block) if kind == "live"]
    dialers = _dialer_statements(live)
    assert not dialers, (
        f"这一块是给人整块粘贴的，而这几条会去拨局域网判官：{dialers}")
    assert any("--human" in text for text in live), \
        "这一块里已经没有一条真把人放上桌的命令了，那这条用例在替一句空话作证"

    decoy_dir = tmp_path / "decoy"
    assert cli.main(["run", "--mock", "--seed", "7", "--quiet",
                     "--out", str(decoy_dir)]) == 0
    (tmp_path / "data" / "human").mkdir(parents=True)
    older = tmp_path / "data" / "human" / "20000101T000000Z_g00000007.jsonl"
    shutil.copyfile(next(iter(sorted(decoy_dir.glob("*_g00000007.jsonl")))), older)
    assert older.stat().st_size > 0

    proc = _bash_the_block(tmp_path, block, "human", HUMAN_STDIN)
    evidence = (f"--- 块 ---\n{block}\n--- stdout ---\n{proc.stdout}\n"
                f"--- stderr ---\n{proc.stderr}")
    assert proc.returncode == 0, f"这一块的退出码是 {proc.returncode}：{evidence}"
    for marker, who in HUMAN_MARKERS.items():
        assert marker in proc.stdout, f"{who} 没印出来（标记 {marker!r}）：{evidence}"
    n_notice = proc.stdout.count(HUMAN_NOTICE)
    assert n_notice == 2, (
        f"这一句该出现两次（打完的那一屏 + 重看的那一屏），实测 {n_notice} 次——"
        f"少了就是 HLOG 挑到的不是刚打的那一局（夹具里那局没有真人的旧日志在等着被选中）："
        f"{evidence}")
    assert HUMAN_ABSENT not in proc.stdout, (
        f"3 号那一席看不到别人的发牌行，这一屏里却出现了 {HUMAN_ABSENT!r}："
        f"replay 被指成了上帝视角：{evidence}")


# ------------------------------------------- README〈命令一览〉那一块逐条能跑（#138）
MENU_HEADING = "## 命令一览：只有 `run` 和 `batch` 需要端点"
# 一条一格，按 README 里出现的顺序对齐（所以条数变了就必须先改这里，见下面的等式断言）。
# 每格的读数与退出码都来自 2026-09-26T07:58Z 的一次逐条实跑（`/tmp/m138b`）：`run` 与
# `--dry-run` 那两条在同一秒内落到同一个 `--out data` 时，第二条的 rc 是 2 而不是 0——那
# 就是这一片要修的 bug，所以这里的 0 是**修完之后**的形状，见 docs/iterations.md 的 `#138`。
MENU_EXPECTATIONS = [
    ("_g00000007.jsonl", 0, "run --mock：替身打完的那一屏，末尾点名它写下的文件"),
    ("成本合计", 0, "run --dry-run：全部 prompt 装配完之后那本成本清单，零 API 调用"),
    ("draw_day_limit", 0, "run --max-days 2：打到日数上限即判平局（R10）"),
    ("轮到你了：3 号", 0, "run --human 3：坐在终端前的人拿到了一张卡片"),
    ("批次 -> ", 0, "batch --mock：配对批次落盘的那一行"),
    ("SYNTHETIC_TABLE", 1, "compare：合成桌只出拒绝语，退出码 1 是设计而不是失败"),
    ("NOT_EVALUABLE", 1, "gate：同一批日志再判一次闸门，替身桌不可评估，退出码 1 同上"),
]


def test_the_command_menu_lines_run_in_sequence(tmp_path):
    """把〈命令一览〉围栏里的每一条举例按 README 的顺序、一条一次 bash 敲进同一个空目录。

    为什么是"一条一次"而不是整块一次：这一节的最后一格是 `gate`，而它对合成批次**故意**返
    回 1。整块交给 bash 时进程的退出码就是最后那条的退出码，`returncode == 0` 这条断言因此
    永远不可能既真又有归因力。逐条执行还换来一件整块给不了的东西：每条命令自己的 rc 和自
    己的读数，谁没答话一眼可见（`#136` 那块整块跑时，六条命令糊成一屏「这不是一局日志」）。

    顺序是有主张的：`compare` 和 `gate` 读的路径正是上面 `batch` 那一条落下的目录。把这两
    条挪到 `batch` 之前，它们会在空目录里撞上「配置错误」而 rc 变 2——这一格的 1 因此同时
    在替"这一节的读法是从上往下"作证。
    """
    block = _doc_block(Path("README.md"), MENU_HEADING)
    statements = _block_statements(block)
    live = [text for kind, text in statements if kind == "live"]
    dialers = _dialer_statements(live)
    assert not dialers, (
        f"这一块是给人照着敲的，而这几条会去拨局域网判官：{dialers}")
    assert len(live) == len(MENU_EXPECTATIONS), (
        f"这一块现在活着的命令行有 {len(live)} 条，期望表只有 {len(MENU_EXPECTATIONS)} 格；"
        f"对齐是按顺序做的，增删一条必须先改期望表，否则每一格都在替别人作证：{live}")
    god_examples = [text for kind, text in statements
                    if kind == "comment" and "wolf run" in text and "--god" in text]
    assert len(god_examples) == 1, (
        f"这一节的表格里 `wolf run` 那一行写着「默认需要端点」，给得出这一句的例子只有注释里"
        f"那条 `--god`；现在它有 {len(god_examples)} 条。整块不许粘贴是对的，把例子删掉也不对："
        f"{god_examples}")

    for i, ((marker, want_rc, who), cmd) in enumerate(zip(MENU_EXPECTATIONS, live)):
        proc = _bash_the_block(tmp_path, cmd, f"menu{i}",
                               HUMAN_STDIN if "--human" in cmd else None)
        evidence = (f"--- 第 {i + 1} 条 ---\n{cmd}\n--- stdout ---\n{proc.stdout}\n"
                    f"--- stderr ---\n{proc.stderr}")
        assert proc.returncode == want_rc, (
            f"{who} 那一条退出码是 {proc.returncode}，期望 {want_rc}：{evidence}")
        assert marker in proc.stdout, f"{who} 没印出来（标记 {marker!r}）：{evidence}"

    landed: dict[tuple[str, str], list[str]] = {}
    for path in sorted(tmp_path.rglob("*.jsonl")):
        if (m := re.search(r"_g(\d{8})\.jsonl$", path.name)):
            landed.setdefault((str(path.parent.relative_to(tmp_path)), m.group(1)),
                              []).append(path.name)
    doubled = {k: v for k, v in landed.items() if len(v) > 1}
    assert not doubled, (
        f"这一节里有两条举例往同一个目录的同一个 seed 上写局日志：{doubled}。局号是「秒 + seed」，"
        f"所以它们只是**碰巧**没撞上——挨着敲的时候后一条会退回 rc 2（实测十次里八次，"
        f"2026-09-26T08:00Z）。逐条跑的那一圈因此量不到这一格：它过得去可能只是跨了一秒。")


# ------------------------------- docs/comparison.md〈两个命令〉那一块整块能跑（#197）
RECIPE_DOC = Path("docs/comparison.md")
RECIPE_HEADING = "## 两个命令"


def test_the_comparison_recipes_block_runs_verbatim(tmp_path):
    """把 `docs/comparison.md` 那一整块原样交给 bash，落在一个空目录里。

    README 的〈命令一览〉在 `#138` 就有了逐条执行证人，这一块一直没有；而它是**整块**粘得动的形状，
    这一块以前第一条就写着 `--games 20` 的真端点批次。按 `#136`/`#137` 为 README 立的同一条规矩，需要
    端点的那一条只能留在可粘贴区外面——跑块的是子进程，`no_network` 那层夹具挡不住它，所以"块里没有
    会拨判官的命令行"必须是执行**之前**的断言。

    期望退出码是 1 而不是 0，这一格不是宽容：整块交给 bash 时进程的退出码就是最后那条的退出码，
    而最后那条是 `compare`，它对合成桌**故意**返回 1。文档开头那段"复现方式"把这句话写成了主张
    （"退出码 1，verdict SYNTHETIC_TABLE"），所以这一跑就是把那句主张接进执行。
    """
    block = _doc_block(RECIPE_DOC, RECIPE_HEADING)
    live = [text for kind, text in _block_statements(block) if kind == "live"]
    dialers = _dialer_statements(live)
    assert not dialers, f"这一整块要离线粘进终端，块里却坐着会拨判官的命令行：{dialers}"

    proc = _bash_the_block(tmp_path, block, "recipes", None)
    evidence = (f"--- 块 ---\n{block}\n--- stdout ---\n{proc.stdout}\n"
                f"--- stderr ---\n{proc.stderr}")
    assert proc.returncode == 1, f"整块的退出码不是最后那条 compare 的 1：{evidence}"
    assert "批次 -> data/plumbing" in proc.stdout, evidence
    assert "SYNTHETIC_TABLE -> data/plumbing/comparison.md" in proc.stdout, evidence
    assert (tmp_path / "data/plumbing/comparison.md").exists(), evidence
    per_arm = {d.name: sorted(p.name for p in d.glob("*.jsonl"))
               for d in (tmp_path / "data/plumbing").iterdir() if d.is_dir()}
    assert {k: len(v) for k, v in per_arm.items()} == {"A": 2, "B": 2}, (
        f"文档那一块写的是 `--configs A,B --games 2`，落盘因此该是两臂各两局：{per_arm}")


# ------------------------------------------------------------------- replay 的出处四格（`#183`）
def test_the_replay_transcript_carries_the_build_that_wrote_the_file(played, capsys):
    """第三个给人看的出口以前不念那四格：`#180` 只接了两块屏幕，实录这一侧 06:46:45Z 实测没接。

    那一趟（seed 7 那局）的 `wolf replay` 印 80 行，`CONTRACT`、`RULES`、`COMPACT`、板名一个都不在
    里面——而这一个出口恰恰是唯一会被整段贴进问题报告的：页在浏览器里、画面在终端里，只有实录是字。
    这条断的是**整串**，不是四个名字各出现过：`#182` 那一课，名字读者不算值证人。
    """
    _, path = played
    assert cli.main(["replay", str(path)]) == 0
    printed = capsys.readouterr().out
    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[0])["meta"]
    want = " · ".join([raw["contract_version"], raw["rules_version"], raw["compress_version"],
                       f"板{raw['board']}"])
    assert want in printed, f"实录里没有「{want}」这一串：开头 {printed[:160]!r}／结尾 {printed[-160:]!r}"


def test_a_log_written_before_the_stamps_prints_no_invented_provenance(played, tmp_path, capsys):
    """反向：版本戳是后来加的字段，老文件上没有——不许替它造一个，也不许印 `None`。

    复盘页那一侧早就钉着这一格（`test_a_log_with_no_manifest_line_prints_no_invented_provenance`），
    实录这一侧要走同一口径才有意义，所以这条把同一份局日志的第一行改写成"没有那四格"再放给
    `replay`：时间线照印（`[e1]` 必须在），出处那一族字样一个都不许出现。
    """
    _, path = played
    lines = path.read_text(encoding="utf-8").splitlines()
    head = json.loads(lines[0])
    for cell in ("contract_version", "rules_version", "compress_version", "board"):
        head["meta"].pop(cell, None)
    old = tmp_path / "before_stamps.jsonl"
    old.write_text(json.dumps(head, ensure_ascii=False) + "\n" + "\n".join(lines[1:]),
                   encoding="utf-8")
    assert cli.main(["replay", str(old)]) == 0
    printed = capsys.readouterr().out
    assert "[e1] 法官：开局座位" in printed, "这份文件还是能打出一条时间线，别把断言让给空输出"
    for word in ("CONTRACT", "RULES", "COMPACT", "板"):
        assert word not in printed, f"没有版本戳的文件被印了「{word}」：{printed[-200:]!r}"

