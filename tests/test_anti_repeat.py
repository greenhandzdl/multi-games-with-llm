"""§7.3 的反重复回灌：从"本轮已经出现的说法"到 C4 那一行，整条链以前一个断言都没有。

plan §7 第 3 条把这条链列为 R1（模板塌陷）的四道防线之一，而 10:15:27Z 实测
`grep -rn "repeat_fragments|禁止复述" tests/*.py` 是**空的**：删掉 `phases.run_speeches` 里
那句 `repeat=repeat_fragments(said)`、或删掉 `assemble._region_c` 里印那一行的分支，全套
742 条照样绿。这条链的位置很尴尬——M3 的主判据 `passivity_rate` 量的正是**它失效之后**的
世界，所以"防线在不在"不该等到真端点上第一次跑批才知道。

钉的是四件事，各自对应链上的一只手术刀：

1. **门槛**：几个座位说过才算"出现过"（`min_count=3`）。这条决定了回灌最早在第 4 个座位
   生效，是设计的后果而不是巧合，写出来是为了让它下次改动时是**有意**改的。
2. **抓到的是整句**：最长片段而不是 6 字滑窗（`#65` 修的就是这一处），名额按"用得多"排，
   同一句话的近亲不占两格。
3. **这一行自己不能把 C 区吃掉**：最多 4 条、每条截到 24 字（`c_task` 那一格的预算），
   以及没人重复时那一句根本不出现。
4. **接上了没有**：一桌各自答同一句开场白，第 4 个座位起的提示词里必须有那句"禁止复述"。

`tests/test_golden_game.py:490` 那条只钉了空集合那一侧（"没有重复 ⇒ 返回空"），这一族钉的
是有内容的那一侧。
"""

from __future__ import annotations

import random

from wolfengine import assemble, belief, game, info, persona, phases, rules
from wolfengine.agent import Agent
from wolfengine.actors import MockActor
from wolfengine.config import Config
from wolfengine.events import Kind, seats

# 三个座位都说的开场白：片段本身要 ≥6 字（`template_top_fragments` 的 `min_len`），
# 而后半截各不相同，这样"最长公共片段"才是这一句而不是整条发言。
CHORUS = "我先听听大家的发言，"
SAID_A = CHORUS + "3号的话我保留意见。"
SAID_B = CHORUS + "2号说的有道理。"
SAID_C = CHORUS + "今晚我不投票。"


# --------------------------------------------------------------------- ① the threshold
def test_a_phrase_only_two_seats_used_is_not_handed_back_yet():
    """两个座位说过同一句：还不算"出现过"，所以第 3 个座位不会被警告。

    手术刀：把 `template_top_fragments` 的 `min_count` 改成 2，这一条必须红——那时黑名单
    会在第 3 个座位就开口，而它凭什么替一个只说过两次的句子定性为"模板"？
    """
    assert persona.repeat_fragments([SAID_A, SAID_B]) == ()


def test_a_phrase_three_seats_used_comes_back_as_a_fragment():
    """反过来：说过三次的那一句必须被抓到。

    手术刀：`min_count=4`（回灌永远晚一个座位）会让这一条红。
    """
    assert CHORUS in "".join(persona.repeat_fragments([SAID_A, SAID_B, SAID_C]))


def test_a_repeated_phrase_comes_back_as_the_whole_phrase_not_a_six_char_window():
    """抓到的是那句话，不是它的四个 6 字滑窗。

    修前实测（10:21:35Z）：三句共享 `我先听听大家的发言，`、后半句各不相同，
    `repeat_fragments` 回来的是 `('我先听听大家', '先听听大家的', '听听大家的发', '听大家的发言')`
    ——四个互相重叠的窗口。旧实现里"丢掉被包含的较短片段"那一步**从未生效**：所有候选都正好
    `min_len` 长，谁也包含不了谁。后果有两处，都不是风格问题：喂给 C4 的是一串读不通的碎片
    （`docs/metrics.md` 那句"专盯『我先听听大家的发言』这类 ≥6 字重复片段"当时是假的），
    而四个名额全花在同一句话上。
    """
    assert persona.repeat_fragments([SAID_A, SAID_B, SAID_C]) == (CHORUS,)


def test_four_repeated_phrases_get_four_slots_not_four_windows_of_one():
    """黑名单有四个名额，就得装得下四句不同的话——修前它只装得下一句的四个碎片。

    这一条是上一具的对照：同一份输入里放两组各三次重复，旧实现返回的四条全部来自第一组，
    第二组一个字都没进提示词。塌陷不止一个模板时，防线只盯其中一个。
    """
    y = "票就投给3号吧，"
    speeches = [SAID_A, SAID_B, SAID_C,
                y + "谁让我懒得想。", y + "反正也说不明白。", y + "就这么定了。"]
    frags = persona.repeat_fragments(speeches)
    assert len(frags) == 2, frags
    assert any(CHORUS in f for f in frags) and any(y in f for f in frags), frags
    assert len({f[:6] for f in frags}) == len(frags), "两条的开头 6 字相同：还是同一句话的窗口"


def test_the_slots_go_to_the_phrases_used_most_often_not_the_least():
    """名额只有四个、模板有五个时，先被回灌的得是**说得最多的**那个。

    手术刀：`frags.sort` 的次数键反向（`/tmp/mut66.py` W11，10:34:53Z 那具在 752 条里**零红**）。
    名额被最少见的四个占了，黑名单照样印、行数照样对，而真正塌陷的那一句——六个人都说的
    那句——一个字都没进提示词。这一条钉的不是"有没有那一行"，是那一行里**装的是哪四条**，
    所以只有断言到具体名额归属的用例能发现它。
    """
    a, b, c, d, e = ("我先听听大家的发言，", "票就投给3号吧，", "我是好人别投我，",
                     "今晚我守一个，", "大家跟我走，")
    speeches, seq = [], 0
    for phrase, used in ((a, 6), (b, 5), (c, 4), (d, 3), (e, 3)):
        for _ in range(used):
            speeches.append(phrase + f"{chr(0x4e00 + seq)}收尾。")  # 紧跟其后那个字就不同，长不出去
            seq += 1
    frags = persona.repeat_fragments(speeches)
    assert frags[0] == a, f"第一个名额给了 {frags[0]!r}，而 {a!r} 才是六个人都说的"
    assert len(frags) == 4 and {a, b, c} <= set(frags), frags


def test_a_near_miss_does_not_take_a_second_slot_off_the_same_phrase():
    """同一句话还以"差一个字"的近亲形式出现过时，回灌给的是整句，不是那半截。

    手术刀：删掉 `kept` 那一步的包含判断（`/tmp/mut66.py` W12，10:34:53Z 零红）。那时
    "我先听听大家的"（被近亲卡住、长不出去）和"我先听听大家的发言，"（三个座位共享的整句）
    各占一个名额——同一句话花掉两个格子，四个名额只剩两个给别的模板。这种近亲在真实输出里
    常见（"我先听听大家的发言" / "我先听听大家的话"），不是假想敌。
    """
    whole = "我先听听大家的发言，"
    speeches = [whole + "3号的话我保留意见。",
                whole + "2号说的有道理。我先听听大家的话说完了。",
                whole + "今晚我不投票。"]
    frags = persona.repeat_fragments(speeches)
    assert frags == (whole,), f"回灌了 {len(frags)} 条：{frags}"


# ------------------------------------------------------------------------ ② the C4 line
def _table(tmp_path, *, chorus: bool = False):
    """一桌停在第一个白天：真实的发牌、真实的 agent，座位上是会照指派答题的替身。

    `chorus` 换上只改发言、不改策略的替身——`MockActor._synthesize` 负责把 act 和 target
    对齐法官的指派（那是 §7 第 1 条的防线），这里要换的只是"说了什么"。
    """
    cfg = Config()
    state, personas, _ = game.build_game(cfg, 7, "g0000007")
    log = game.open_log(cfg, state, tmp_path / "g.jsonl", ("mock",), 7, "20260922T101700Z")
    agent = Agent(cfg=cfg, state=state, log=log, personas=personas,
                  known_ids=lambda: {f"e{e.seq}" for e in log.all()})
    actors = {s: (Chorus(s, synthesize=True) if chorus else MockActor(s, synthesize=True))
              for s in state.seats}
    table = phases.Table(cfg=cfg, state=state, log=log, agent=agent, actors=actors,
                         rng=random.Random(7))
    return cfg, state, log, agent, table


def _c_text(tmp_path, seat, *, repeat: tuple[str, ...]):
    cfg, state, log, agent, _ = _table(tmp_path)
    percept = info.percept_for(seat, log.all())
    pr = assemble.assemble(
        cfg=cfg, percept=percept, seat_role=state.role_of(seat),
        persona=agent.personas[seat], belief=belief.build_belief(seat, percept.events),
        legal=rules.legal_actions(state, seat), phase=state.phase,
        repeat_fragments=repeat)
    return pr.messages[2]["content"]


def test_repeat_fragments_hands_over_four_phrases_not_every_run_it_found():
    """名额在 `persona` 这一层也有一道：五个模板并存时，回灌只取四个。

    这是**第二道**名额：`assemble` 那边还有一句 `[:4]`（下一族用例钉的是那道）。两道
    各在自己的文件里，任何一道被拆掉都只有一条用例红——所以两道都得有名字。
    """
    five = ["我先听听大家的发言，3号的话我保留意见。",
            "票就投给3号吧，谁让我懒得想。",
            "我是好人别投我，信不信随你们。",
            "今晚我守着一个，押对了再说。",
            "大家跟我走一趟，别自己乱投票。"]
    speeches = [s for one in five for s in (one, one, one)]
    frags = persona.repeat_fragments(speeches)
    assert len(frags) == 4, f"回灌了 {len(frags)} 条：{frags}"
    assert len(set(frags)) == 4, f"四条里有重复：{frags}"


def test_the_blacklist_line_names_exactly_the_phrases_it_was_handed(tmp_path):
    """给装配器三条片段，C 区里就得出现这三条，一条不多一条不少。

    手术刀：`task.append(...)` 那一句删掉（②③两族用例一起红），或者把 `；".join(...)`
    换成只印第一条。
    """
    c = _c_text(tmp_path, 1, repeat=(CHORUS, "票就投给3号吧", "我是好人"))
    assert "禁止复述以下已经出现过的说法：" in c, c
    head, _, tail = c.partition("禁止复述以下已经出现过的说法：")
    named = tail.split("\n")[0]
    assert named.split("；") == [CHORUS, "票就投给3号吧", "我是好人"], named


def test_no_seat_is_warned_about_phrases_nobody_used_yet(tmp_path):
    """空黑名单必须**什么都不印**，而不是印一句"禁止复述："后面跟着空白。

    手术刀：把 `if repeat_fragments:` 去掉、无条件 append。反向对照在同一族里：上一条
    证明非空时那一行确实在。

    W4（无条件 append）的红集是**两条**而不是这一条：`the_fourth_speaker` 里"前三个座位不许
    有"那半边也跟着红（10:34:53Z 我先把它写成了一条，量出来是两条）。那条用例读的是门槛，
    这里读的是护栏，两半边各钉一头——写在这是为了下次别再把那一条当成只钉接线。
    """
    c = _c_text(tmp_path, 1, repeat=())
    assert "禁止复述" not in c, c


def test_the_blacklist_cannot_grow_past_four_phrases_of_twenty_four_chars(tmp_path):
    """防线自己不能变成新的预算问题：最多 4 条、每条 24 字封顶。

    这是 `regions.c_task` 那一格在这个函数里唯一的读者。手术刀：`[:4]` 或 `frag[:24]`
    任何一处去掉，6 条各 30 字（合计 180 字）就会把本轮任务那一块挤变形。
    """
    long_frags = tuple(f"这是第{i}段被反复使用的开场白，我先听听大家的发言" * 2
                       for i in range(6))
    c = _c_text(tmp_path, 1, repeat=long_frags)
    tail = c.partition("禁止复述以下已经出现过的说法：")[2].split("\n")[0]
    items = tail.split("；")
    assert len(items) == 4, f"黑名单吃进了 {len(items)} 条：{items}"
    assert all(len(it) <= 24 for it in items), [len(it) for it in items]


# ------------------------------------------------------------------ ③ the wiring itself
class Chorus(MockActor):
    """只改"说什么"的替身：act、target、evidence 仍由 `MockActor._synthesize` 照指派给。"""

    def _line(self, act: str, target: int | None) -> str:
        return f"{CHORUS}{target if target is not None else '大家'}号的票我心里有数。"


async def test_the_fourth_speaker_is_asked_not_to_repeat_the_three_before_it(tmp_path):
    """整条链的证人：回灌必须**真的**在第 4 个座位起出现在提示词里。

    这一条是唯一能发现"函数都对、就是没人调用"的：`metrics.template_top_fragments` 和
    `persona.repeat_fragments` 各自可以被单测钉死，`assemble` 那一行也可以，而把它们串起来的
    只有 `run_speeches` 里那个 `repeat=`。手术刀：把 `repeat=repeat_fragments(said)` 删掉
    （或写成 `repeat=()`），前四族用例**全部照绿**，只有这一条红。

    门槛那半边同时在这里显形：前三个座位没有，第四个起才有——不是它们说话不算数，
    是"三个座位说过"这件事要到第四个人开口之前才成立。
    """
    _, _, log, agent, table = _table(tmp_path, chorus=True)
    speakers = [1, 2, 3, 4, 5]
    await phases.run_speeches(table, speakers=speakers)

    said = [e for e in log.all() if e.kind == Kind.SPEECH and e.payload.get("text")]
    assert len(said) == len(speakers), f"这一轮只落盘 {len(said)} 条发言，回灌无从谈起"
    assert agent.fallbacks == 0, "座位答的话被闸门拒过，那下面的断言测的是收尾默认值"

    asked = {s: table.actors[s].turns[0].prompt.messages[2]["content"] for s in speakers}
    for seat in speakers[:3]:
        assert "禁止复述" not in asked[seat], f"第 {seat} 个座位就被警告了，门槛不是 3 个座位"
    for seat in speakers[3:]:
        assert "禁止复述" in asked[seat], f"第 {seat} 个座位的提示词里没有那句防线"
        assert CHORUS in asked[seat].partition("禁止复述")[2], asked[seat]


async def test_a_private_sentence_never_reaches_the_blacklist(tmp_path):
    """夜里只对两个座位说过的一句话，不得出现在给全桌的黑名单里。

    黑名单是从"这一轮已经说出口的发言"里长的（`run_speeches` 里那个 `said`），而 C 区是
    每个座位都要读的。手术刀：把 `said` 换成 `t.log.all()` 的 text 汇总——那时私有事件的
    字面量会被回灌进**无关座位**的提示词，是 §11 的隔离最容易被一次"顺手改进"撞坏的地方。
    """
    cfg, state, log, agent, table = _table(tmp_path, chorus=True)
    wolf = next(s for s in state.seats if state.team_of(s) == "wolf")
    other = next(s for s in state.seats if state.team_of(s) == "wolf" and s != wolf)
    secret = "今晚这一刀我们一起落到5号身上，别声张。"
    # 三条，不是一条乘三：门槛数的是**几条发言**共享这段字，一条里重复三遍不算。
    # 手术刀要把 `said` 换成"日志里所有 text"时，只有三条才够它触发。
    for _ in range(3):
        log.append(Kind.WOLF_CHAT, day=state.day, phase=state.phase.value,
                   visibility=seats(wolf, other), actor=wolf, text=secret)

    await phases.run_speeches(table, speakers=[s for s in (other, 1, 2, 3, 4, 5)
                                               if s != wolf][:5])
    for seat, actor in table.actors.items():
        if not actor.turns:
            continue
        c = actor.turns[0].prompt.messages[2]["content"]
        # 只读黑名单那一行：私有 block 里出现狼话是**对的**（那是它自己的私有信息），
        # 整段 C 一起断言会把"该看到的"和"不该转述的"混成一条红。
        line = next((ln for ln in c.splitlines() if ln.startswith("禁止复述")), "")
        assert "今晚这一刀" not in line, f"{seat}号的黑名单里出现了只有狼听得见的话：{line}"
