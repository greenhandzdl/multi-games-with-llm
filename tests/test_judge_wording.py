"""法官印出来的那两句"结论"，引擎真的判到了那一步没有。

发现方式不是数计数，是读产物（#87）。两条都是**票型/终局的数字全对、句子是假的**。

① PK 前一句"无人出局"。`/tmp/e2e/g7.jsonl/20260923T103225Z_g00000007.jsonl`
（mock 单局，`wolf_win winner=wolf day=4 events=104 fallback=0`）的第 94、95、101 行原文：

```
seq 94  vote_result  {"summary": "票型：3号1票、9号1票。弃票1人。平票，无人出局。", "exiled": null}
seq 95  phase        {"text": "平票，3、9号进入PK。"}
seq 101 vote_result  {"summary": "票型：3号2票。弃票1人。3号被投票出局。", "exiled": 3}
```

94 宣布"无人出局"是一句**结论**，95 立刻把它收回——而 `compress.render_line` 把 `summary` 原样
拼进模型读到的编年史（`[e94] 法官：票型：…平票，无人出局。`），于是九个模型在 PK 发言前
都读到了一句法官自己不作数的话。这件事不用看 /tmp：**本机留存**的（`data/` 整目录在
`.gitignore` 里，不随仓库走，所以克隆下来的人打不开它；而这份日志记的是 #87 修**之前**的行为，
拿它当"现在还会这样"的证据是读反了）
`data/20260920T184536Z_g00000007.jsonl` 第 95、96 行（seq 94、95）就是同一对矛盾，而第 97 行
（3号的 PK 发言，`phase=day_pk_speech`）的 `request` 字段里，`[e94]`/`[e95]` 两行一字不差地躺在
它读到的编年史中——那天后面的三次投票和 3号的遗言（seq 98/99/100/103）带着同一对进 prompt。
全仓库没有一条断言钉过这个字符串（10:40Z grep "无人出局" 只有 src 命中），所以它是语义错而不是计数错。

修的方向是**一个谓词一个来源**：这个条件原本只写在 `phases.run_vote` 那一支 `and` 上，
句子（当时是 `phases._tally_text`，写在负载里）压根没读它，所以两边各说各话。现在两边都问
`rules.will_pk`——而那句话本身在 #113 里搬了家：负载不再存整句，只剩渲染侧的一个作者。
下面 ①② 两条钉的就是"句子跟着房规走"：

* ①第一张票把桌子送进 PK 时，票型句不许对"谁出局"下任何结论——它只能报票型。
* ②PK 复投再平票时，"平票，无人出局"是**真**结论（`pk_once_then_nobody` 的 once），
  必须照说，否则修法就退化成"永远不说无人出局"。

`tie_break="nobody"` 那一桌的反向对照在 `test_house_wired.py` 的第一条用例里（它现在钉的
就是完整那句"平票，无人出局"），这里不重复。夹具沿用 `test_vote_wave.py` 的那张桌。

② 平局一句"获胜"。同一套读法在日数上限那桌上撞出的第二处（`/tmp/e2e3` 的 seed 2、
`max_days=3`，引擎确实打出平局）：

```
[e85] 法官：draw阵营获胜（draw_day_limit）。
终局：draw_day_limit · 胜方：draw        ← 直播/复盘帧里同一件事的第二种说法
```

根因只有一处：`game.play` 收尾写 `winner=winner or "draw"`，把"没有阵营获胜"压成了阵营键空间
里的一个字符串 `"draw"`，于是渲染器分不清"平局"和"狼队赢了"，只能说"…阵营获胜"。而"哪个终局
算获胜"这件事仓库里**早就有唯一来源**——`metrics.DECISIVE`（batch 的分母就是拿它筛的）。所以
③④ 钉的是：payload 里没赢家就写 `null`，渲染器看见 `null` 就不许说"获胜"；反向对照是决定性
终局照旧要说"阵营获胜"，否则修法又退化成"永远不说获胜"。
"""

from __future__ import annotations

from wolfengine import compress, game, metrics, phases, render_live
from wolfengine.events import EventLog, Kind
from wolfengine.state import Phase

from test_day_cap import _draw_at, _play
from test_vote_wave import ALL_SEATS, _pass, _table, _vote


def _tie_then_revote(*, second_ties: bool) -> dict[int, list]:
    """第一波三比三必然平票；第二波要么把 3 号投出去，要么再平一次。

    3、5 号是并列的高票座位。第二波只在他们俩之间投（`_ballots` 会把合法目标收窄成
    `pk_seats`），所以复投的形状是"4:4 再平"或"5:3 定人"两种。
    """
    first = {1: _vote(3), 2: _vote(3), 4: _vote(3),
             6: _vote(5), 7: _vote(5), 8: _vote(5),
             3: _pass(), 5: _pass(), 9: _pass()}
    if second_ties:
        second = {1: _vote(3), 2: _vote(3), 4: _vote(3), 9: _vote(3),
                  6: _vote(5), 7: _vote(5), 8: _vote(5), 3: _vote(5), 5: _pass()}
    else:
        second = {s: _vote(3 if s in (1, 2, 4, 5, 9) else 5) for s in ALL_SEATS}
    return {s: [first[s], second[s]] for s in ALL_SEATS}


def _tallies(log) -> list[dict]:
    return [e.payload for e in log.all() if e.kind == Kind.VOTE_RESULT]


def _pk_turns(actors) -> list:
    """被叫去 PK 发言的那些轮次：证明桌子真的走进了那条分支。"""
    return [t for a in actors.values() for t in a.turns if t.phase is Phase.DAY_PK_SPEECH]


# ---------------------------------------------------------------- ① 进 PK 的那张票不下结论
async def test_the_ballot_that_opens_a_pk_makes_no_claim_about_who_is_out(tmp_path):
    """第一波平票：票型句只能报票型。

    手术刀：把渲染的结论子句改回无条件的一句"平票，无人出局"，这一条立刻红——红在下一行的
    "出局"上，而不是红在一个我新造的短语上，所以断言不锁文案只锁语义。
    """
    _cfg, state, log, table, _actors = _table(tmp_path, _tie_then_revote(second_ties=False))
    await phases.run_vote(table)

    settled = [e for e in log.all() if e.kind == Kind.VOTE_RESULT]
    assert len(settled) == 2, f"这一桌没走 PK 复投，下面两条是空转：{len(settled)} 张票型"
    # 这一桌"下一波还要投"今天有字段承载（`pending_pk`），句子由它算出来：钉语义的两条因此各自
    # 站在两边——typed 在场，人话不许越权。
    assert settled[0].payload["pending_pk"] is True and settled[0].payload["exiled"] is None
    assert settled[1].payload["pending_pk"] is False and settled[1].payload["exiled"] == 3

    first_line = compress.render_line(settled[0])
    assert "票型" in first_line and "3号" in first_line, f"票型本身也没了：{first_line}"
    assert "出局" not in first_line, f"下一行就要宣布进 PK，这一行却对出局下了结论：{first_line}"

    # 正反两面都要有：第二张票型是真结论，不许被同一句"不许下结论"抹掉。
    assert "出局" in compress.render_line(settled[1]), compress.render_line(settled[1])

    # 上面钉的是渲染出来的那一行，不是负载里的字符串：`render_html._line`、直播帧和模型读到的
    # 编年史都走同一个 `compress.render_line`，所以这一行不说"出局"，三处就都不说。
    assert state.pk_seats == (), "PK 名单没清空，复投的目标集会漏进下一天"


# ------------------------------------------------------------------ ② 复投再平就是终局
async def test_a_second_tie_is_the_once_in_pk_once_then_nobody_and_says_so(tmp_path):
    """PK 复投再平票：这一次"无人出局"是真的，且后面没有第三波。

    这一条是①的反向对照：如果修法是"平票永远不说无人出局"，这里红。
    """
    _cfg, state, log, table, actors = _table(tmp_path, _tie_then_revote(second_ties=True))
    await phases.run_vote(table)

    tallies = _tallies(log)
    assert len(tallies) == 2
    second = [e for e in log.all() if e.kind == Kind.VOTE_RESULT][-1]
    # 第二波是终局：`pending_pk` 关了，于是"无人出局"这一次是真结论，必须照说。
    assert tallies[1]["exiled"] is None and tallies[1]["pending_pk"] is False, tallies[1]
    assert "平票，无人出局" in compress.render_line(second), compress.render_line(second)
    assert len(state.alive_seats) == len(ALL_SEATS), \
        f"房规说 once，还是有人在这桌上出局：{[s for s in ALL_SEATS if not state.is_alive(s)]}"
    asked = [len([t for t in a.turns if t.phase is Phase.DAY_VOTE]) for a in actors.values()]
    assert asked == [2] * len(ALL_SEATS), f"三次波次=两次 PK，脚本没造出 once：{asked}"
    spoke = sorted({t.seat for t in _pk_turns(actors)})
    assert spoke == [3, 5], f"再平一次票之后没有 PK 发言，上面两条是空转：{spoke}"


# ------------------------------------------------- ③④ 平局不是"某个阵营获胜"
def _terminal_of(path):
    events, meta = EventLog.read_records(path)
    return events, meta, [e for e in events if e.kind == Kind.GAME_OVER][-1]


def test_a_draw_writes_no_winner_rather_than_a_made_up_camp(tmp_path):
    """日数上限到了：payload 里就没有赢家，不许拿一个字符串去顶那个位置。

    手术刀 A：把 `game.py` 收尾那行改回 `winner or "draw"` —— 第一句红（平局带着
    `"winner": "draw"` 落盘），第三句也红（渲染行里出现了"阵营名"）。
    手术刀 B：删掉 `compress` 里 `camp is None` 那一支 —— 第三句红，因为渲染会退回
    `TEAM_ZH.get(None)` 那条路，印出 `None阵营获胜`。断言只看"有没有点名阵营"，不锁文案，
    所以改措辞不会红、把平局说成获胜一定红。
    """
    drawn = _draw_at(tmp_path, 3, range(1, 12))
    _events, _meta, over = _terminal_of(drawn.path)
    assert over.payload["terminal"] == game.DRAW_DAY_LIMIT
    assert over.payload["winner"] is None, \
        f"平局被写成了一个阵营名，读者无从分辨'没赢家'和'某阵营赢了'：{over.payload}"
    line = compress.render_line(over)
    named = [zh for zh in compress.TEAM_ZH.values() if zh in line]
    assert not named, f"编年史给平局点了一个获胜阵营：{line}"
    assert "None" not in line, f"洞换了个写法而已：{line}"
    assert game.DRAW_DAY_LIMIT in line, f"终局名丢了，读者对不上 metrics 的桶：{line}"
    # 反向对照：决定性终局必须照旧点名阵营并说"获胜"，否则修法只是"永远不说获胜"。
    won = _play(tmp_path, 7, 6)
    _e2, _m2, over2 = _terminal_of(won.path)
    assert over2.payload["terminal"] in metrics.DECISIVE
    line2 = compress.render_line(over2)
    assert any(zh in line2 for zh in compress.TEAM_ZH.values()) and "获胜" in line2, line2


def test_the_live_frame_shows_no_camp_as_the_winner_of_a_draw(tmp_path):
    """同一件事的第二块屏幕：直播/复盘帧上那格"胜方"。

    这一条钉的是渲染器本身——`winner` 变成 `null` 之后，`payload.get('winner', '')` 会印出
    `None`，那是把一个洞换了个写法。所以断言三样：不是阵营名、不是"None"、终局名还在。
    """
    drawn = _draw_at(tmp_path, 3, range(1, 12))
    events, meta, _over = _terminal_of(drawn.path)
    rows = [ln for ln in render_live.frame_text(events, meta).splitlines() if "胜方" in ln]
    assert len(rows) == 1, f"'胜方'那一行没了或多了：{rows}"
    who = rows[0].split("胜方：")[-1].strip()
    assert who not in {"wolf", "good", "draw", "None"}, f"平局在直播帧上成了：{rows[0]}"
    assert game.DRAW_DAY_LIMIT in rows[0], rows[0]
    won = _play(tmp_path, 7, 6)
    e2, m2, _o2 = _terminal_of(won.path)
    row2 = [ln for ln in render_live.frame_text(e2, m2).splitlines() if "胜方" in ln]
    assert row2 and row2[0].split("胜方：")[-1].strip() == won.winner, row2
