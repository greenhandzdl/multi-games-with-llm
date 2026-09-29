"""真人座位的输入侧（`#122`）。

`test_actor_contract.py` 钉的是**编排层**为"坐着一个人在想"prepared 到什么程度：超时按 actor 取、
并发度表剔掉 blocking、墙钟只对全模型桌生效。那三条在 `HumanActor.act()` 还是
`NotImplementedError` 的时候就能测，因为测的是"别人拿到 `None`/`blocking=True` 之后怎么办"。

这一份测的是另一半：**一个真人怎么把那三样东西用起来**——一行字变成 `Action`、一屏字变成他该答
什么。这半边之前测不了，因为根本没有实现；现在它测得到，而且必须在这儿测：真人上桌是全仓库唯一
没有替身的一条链，红在这里比红在演示现场便宜。

三条口径写在这里，因为它们是"测试为什么长这样"的一部分：

* **词表只有一个来源**。玩家能打的词直接来自 `schema.ACT_SYNONYMS`（模型那条解析梯子用的同一张
  表），所以这一片不新建第二份动作词典，也不允许"这里顺手多认一个词"。
* **闸门不在这儿**。本模块只回答"这一行字我读懂了没有"；"这个动作这轮能不能做"仍然只有
  `legality.check_action` 说了算（`agent.py` 对每种座位都调它，包括这一席）。
* **看不懂就问第二遍，但不烧修复重试**。玩家打错字不等于模型输出不合契约：前者在本座位内循环，
  不占 `cfg.max_repair_retries`，也不往 `attempts[]` 里塞一条假偏好对。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import get_args

from test_actor_contract import SEED, _table  # 同一副牌、同一张桌，不重造现场
from wolfengine import compress, human, phases, rules
from wolfengine.actors import HumanActor, Proposal
from wolfengine.cli import render_chronicle
from wolfengine.events import Kind, PUBLIC
from wolfengine.schema import ACT_SYNONYMS, ActName
from wolfengine.state import LegalSet, Phase


def _desk(tmp_path: Path, name: str) -> Path:
    """一块干净的目录。`_table` 写死的日志名是 `t.jsonl`，所以两副现场不能落在同一个 `tmp_path`
    里——`open_log` 会当场拒绝复用文件（那是它对的方向，我这边错）。好几条用例要先偷看一次发牌
    再让真座位答一轮，于是同一局里确实需要两张桌子。
    """
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return d


class _Console:
    """测试侧的终端：读的是队列，写的是列表。

    刻意不放进 `human.py`：那会把"给测试用的替身"长进生产类里，而实现者改 `Console` 时这条队列
    会跟着一起被改——到那时下面这些断言读的就只是自己的替身了。
    """

    def __init__(self, lines=()):
        self.lines = list(lines)
        self.shown: list[str] = []
        self.reads = 0

    def show(self, text: str) -> None:
        self.shown.append(text)

    def read(self, prompt: str) -> str | None:
        self.reads += 1
        self.shown.append(prompt)  # 真终端把提示符印在同一行，替身没有理由不照做
        return self.lines.pop(0) if self.lines else None

    @property
    def screen(self) -> str:
        return "\n".join(self.shown)


class _Capture:
    """答一句"捕获_only"的座位：存在的理由是把 `agent.py` 装配出来的真 `TurnContext` 交出来。

    手搓一个 `TurnContext` 等于测我自己造的东西；`decision_card()` 要钉的是**玩家真会看到的那一屏**，
    所以现场必须从 `take_turn` 里拿。
    """

    kind = "human"
    blocking = True

    def __init__(self) -> None:
        self.ctxs: list = []

    def timeout_for(self, phase):
        return None

    async def act(self, ctx) -> Proposal:
        self.ctxs.append(ctx)
        return Proposal(failure="capture_only")


def _offer_block(card: str) -> str:
    """卡片上"这一轮可以答"那一段，缩进的两格就是它的边界。

    为什么不让它继续扫整张卡片：`#124` 之后屏上有局况，而局况是中文散文——"出局""发言""听"
    都在这张词表里，扫全文等于让法官的一句话替一个动作背书。Offer 段可辨认之后，那条断言
    才真的在说"列出来的动作 = 闸门答得上的动作"。
    """
    lines = card.splitlines()
    start = lines.index(human.OFFER_HEADER)
    body = []
    for line in lines[start + 1:]:
        if not line.startswith("  "):
            break
        body.append(line)
    return "\n".join(body)


async def _turn(tmp_path, lines, *, seat=3, phase=Phase.DAY_VOTE, kind=Kind.VOTE,
                legal: LegalSet | None = None):
    """让真 `HumanActor` 坐在 3 号位答一轮，返回 (agent, log, console, outcome)。"""
    cfg, state, log, agent, _, _ = _table(_desk(tmp_path, "turn"))
    state.phase = phase
    ls = rules.legal_actions(state, seat) if legal is None else legal
    console = _Console(lines)
    actor = HumanActor(seat, console=console)
    outcome = await agent.take_turn(seat=seat, actor=actor, legal=ls, phase=phase, kind=kind,
                                    visibility=PUBLIC)
    return agent, log, console, outcome


async def _ctx(tmp_path, *, seat=3, phase=Phase.DAY_VOTE, legal: LegalSet | None = None,
               heard: tuple[str, ...] = ()):
    """玩家那一席真会被递给的一张 `TurnContext`。

    `heard` 是先落进日志的公开发言（4 号说的）：`#124` 之前这一屏只读 `LegalSet`，递进去的
    `percept` 没有读者，所以"这一席听到了什么"这件事在卡片上一格都没有。
    """
    cfg, state, log, agent, _, _ = _table(_desk(tmp_path, "ctx"))
    for text in heard:
        log.append(Kind.SPEECH, day=state.day, phase=str(phase), actor=4, text=text)
    state.phase = phase
    ls = rules.legal_actions(state, seat) if legal is None else legal
    cap = _Capture()
    await agent.take_turn(seat=seat, actor=cap, legal=ls, phase=phase, kind=Kind.VOTE,
                          visibility=PUBLIC)
    return cap.ctxs[0]


# ------------------------------------------------------------------------- 一行字 → Action
def test_a_typed_line_names_the_act_then_the_seat_then_the_words():
    a = human.parse_human_line("指控 3 他昨晚的沉默比发言说明问题")
    assert a is not None and a.act == "accuse" and a.target == 3
    assert a.speech == "他昨晚的沉默比发言说明问题"
    # 座位号怎么打都认：这条不是慷慨，是 `coerce_seat` 本来就有的读法，模型那一侧同样在用。
    b = human.parse_human_line("怀疑 三号 你先说")
    assert b is not None and b.act == "accuse" and b.target == 3 and b.speech == "你先说"


def test_a_seat_number_may_be_typed_with_a_space_before_the_unit():
    """「5 号」和「5号」是同一个座位引用，中间那个空格不属于这句话。

    2026-09-25T13:18Z 实测：这一行读出来 `target=5` 却把「号」留在了 `speech` 里
    （`'号 他昨晚那一刀没有道理'`），而那串字要落进日志、进复盘页——人看见的是自己话开头多了一个
    字。前一条用例已经钉住 `5` 和 `三号` 两种写法，这一条钉第三种。
    """
    a = human.parse_human_line("票 5 号 他昨晚那一刀没有道理")
    assert a is not None and a.act == "vote" and a.target == 5
    assert a.speech == "他昨晚那一刀没有道理", a.speech
    assert human.parse_human_line("怀疑 三 号 你先说").speech == "你先说"
    # 表里另外两个单位写法先前一个字都没被测过（`_CN_SUFFIXES` 换顺序谁都发现不了），补在这里：
    # 它们和「号」同一条路，同一种空格也照样犯。
    assert human.parse_human_line("票 5 号位 你先说").speech == "你先说"
    assert human.parse_human_line("票 5 位 你先说").speech == "你先说"
    # 只有单位、没有话：那一个字同样不许冒充发言。
    assert human.parse_human_line("票 5 号").speech == ""


def test_a_number_that_only_looks_like_a_seat_reference_still_owns_its_word():
    """上一条的修法如果写成"数字后面只要跳得过一个空格就吞掉单位"，会把「号码」两个字拆给座位号。

    这一条在修法落地**之前**就是绿的（现在的代码根本不吞隔着空格的后缀），它钉的是那把刀：
    「号」后面还接得上字，就不是单位而是下一句的开头。
    """
    a = human.parse_human_line("票 5 号码是我的，先记着")
    assert a is not None and a.act == "vote" and a.target == 5
    assert a.speech == "号码是我的，先记着", a.speech


def test_punctuation_between_the_seat_number_and_the_sentence_is_not_part_of_the_sentence():
    """「票 5，他昨晚那一刀」现在读出 `speech='，他昨晚那一刀'`（2026-09-25T13:19Z 实测）。

    和「号」那一格是同一个缺口的两面：座位号到正文之间那段**边界**没人负责切。写在一行是因为
    两条断言各自红过一次——去掉句读的修法会让上一条的「号码」红，吞后缀的修法会让这一条的逗号
    原地不动，两把刀各打中一半。
    """
    b = human.parse_human_line("票 5，号外的事回头说")
    assert b is not None and b.target == 5 and b.speech == "号外的事回头说", b.speech
    c = human.parse_human_line("指控 3。他昨晚的沉默说明问题")
    assert c is not None and c.target == 3 and c.speech == "他昨晚的沉默说明问题", c.speech


def test_a_bracket_that_opens_the_sentence_is_not_boundary_punctuation():
    """`_SEPARATORS` 那张表同时干两件事：判"这里是不是边界"和"从哪儿开始切"。引号只配当后一件的反例。

    2026-09-25T13:39Z 实测三态。「指控 3 「他是狼」」在 HEAD（`e06ac73`）上读出 `'「他是狼'`（丢的是**后**引号——
    调用点 `rest.strip` 从行尾啃字的老毛病，与本条无关，记在 `#129`）；上一条用例的修法把它顶成
    `'他是狼'`，两个引号一起没了。那不是把边界切干净，是多啃了那个人打的一个字，所以这一条按
    不带后引号的写法断言（`「他是狼`），免得把 `#129` 的账算到这条头上。
    """
    a = human.parse_human_line("指控 3 「他是狼")
    assert a is not None and a.target == 3
    assert a.speech == "「他是狼", a.speech
    b = human.parse_human_line("票 5 （他昨晚没动手")
    assert b is not None and b.speech == "（他昨晚没动手", b.speech
    # 逗号句号照旧切走：停下来的是"成对的开括号"，不是句读本身。
    assert human.parse_human_line("票 5，先听").speech == "先听"


def test_the_last_character_of_what_a_person_typed_stays_in_his_sentence():
    """每个人打的句子末尾那个标点，从来没进过日志（2026-09-25T14:11Z 实测，`#128` 之前之后一样）。

    `parse_human_line` 在把整行交给 `_lead_seat` 之前做的是 `rest.strip(_SEPARATORS)`，那一刀也从**行尾**
    啃。前三条读起来无害（少一个句号、少一个感叹号），第四条说明它其实是在编辑那个人说的话：丢掉的是
    引号，成对符号的另一半还留在正文里。修法只有一处：整行的**开头**可以切，结尾归打字的人自己负责。
    """
    for line, want in [("票 3 他昨晚没动手。", "他昨晚没动手。"),
                       ("投票 5 先听听吧！", "先听听吧！"),
                       ("弃票 就这样。", "就这样。")]:
        a = human.parse_human_line(line)
        assert a is not None and a.speech == want, f"{line!r} 读出了 {a.speech if a else None!r}"
    b = human.parse_human_line("指控 3 「他是狼」")
    assert b is not None and b.speech == "「他是狼」", b.speech


def test_a_seat_number_that_runs_straight_into_the_sentence_is_refused():
    """`#128` 的 A7 探针实测那一句红 0 条＝这一支没有证人。补的就是那一条证人。

    2026-09-25T14:11Z 实测：`票3他说得对` 与 `票5号他说得对` 在改前改后都是 `None`。这一支守的是
    "座位号必须在一个边界上收尾"，它挡掉的是一个字面串有两种读法：紧挨着的「5号他」既可能是 5 号 +
    「他…」，也可能是「号码」那种另一个词的开头（上一条反例就是那种）。**读错了要落进日志、永远留在
    那条发言里；读不出来只是再问一遍**，所以这里选再问一遍。

    这条在今天的代码上是绿的，它的红望在电池那一侧：A7 那具刀（删掉这一支）现在必须弄红它。
    """
    assert human.parse_human_line("票3他说得对") is None
    assert human.parse_human_line("票5号他说得对") is None
    assert human.parse_human_line("票 3 他说得对").speech == "他说得对"


def test_the_words_the_player_can_type_are_the_ones_the_ladder_already_knows():
    """可打的词**整张**来自 `ACT_SYNONYMS`：既不少一个（玩家打不出模型认得的词），也不多一个
    （那张表就成了两处）。少一条断言都拦不住"给真人单独加个词"这种顺手改。
    """
    wrong = [(zh, en) for zh, en in ACT_SYNONYMS.items()
             if (a := human.parse_human_line(zh)) is None or a.act != en]
    assert not wrong, f"这些词读懂的结果和词表不一致：{wrong[:5]}"


def test_every_act_the_engine_can_ask_for_has_a_word_the_player_can_type():
    """词表可以有多余的词（模型那条梯子认得更多说法），**不可以有缺词的 act**。

    缺一个 act 的坏处不在解析侧——那一行永远读不懂，坏处发生在卡片上：`decision_card` 给这个 act
    印出一个空词，玩家对着一个空格子打字，最后被引擎代答。这一格的口径是"全不全"，和上面那条
    "词都对不对"是两件事，所以各钉一条。

    名单取 `get_args(ActName)` 而不是抄一份：`schema.py` 加 act 时抄的那一份不会跟着长。
    """
    missing = sorted(set(get_args(ActName)) - set(ACT_SYNONYMS.values()))
    assert not missing, f"这些 act 玩家一个词都打不出来：{missing}"


def test_a_sentence_that_merely_contains_an_act_word_is_not_an_instruction():
    """`normalize_act` 有"整句里含词就算"的那一支，是给模型输出的容错；对打字的人不能这么容。

    两处收紧各有一把刀，因为它们挡的是两种不同的松法：

    * **K1** 把"动作词在行首"放宽成"句中含"——差别只出现在「先票 3」这种**动作词前面还有字、
      后面紧跟空格和座位号**的行上。少了这条断言，K1 谁都弄不红（「我票了3号」在 K1 下仍然读不懂，
      因为它的座位号前面是"了"），这一支因此就成了没有证人的一支。
    * **K2** 删掉"动作词后面紧跟非座位号 ⇒ 整行不算指令"那一支——「指控他昨晚不说话」会从
      "没有指令"变成"一条没有目标的指控"。

    两种松法叠加才是 docstring 里那句「我票了3号」变成一张票；单独任何一把都到不了那一步，
    所以两把分开下、两条断言分开钉。
    """
    assert human.parse_human_line("我票了3号") is None
    assert human.parse_human_line("先票 3") is None
    assert human.parse_human_line("指控他昨晚不说话") is None
    assert human.parse_human_line("今天天气不错 3") is None
    assert human.parse_human_line("") is None


# ------------------------------------------------------------------------- 座位答一轮
async def test_the_seat_answers_with_the_words_the_player_typed(tmp_path):
    cfg, state, _, _, _, _ = _table(_desk(tmp_path, "deal"))
    state.phase = Phase.DAY_VOTE
    target = sorted(rules.legal_actions(state, 3).targets)[0]
    agent, log, console, outcome = await _turn(tmp_path, [f"票 {target}"])

    assert not outcome.fell_back, "这一轮是引擎替玩家答的，那下面的断言全都是假的"
    # 算对了卡片还不等于玩家看见了它：`decision_card` 的返回值必须真被印出来，否则下一节那两条
    # 关于"卡片上有什么"的断言只是在测一个没人调用的函数。
    assert "可点名的座位" in console.screen, console.screen
    ev = log.all()[-1]
    assert ev.kind == Kind.VOTE and ev.actor == 3
    assert ev.payload["act"] == "vote" and ev.payload["target"] == target
    assert ev.result["fallback"] == 0, "落了盘却没人说这是引擎落的"
    # rung 是 JSON 梯子的档位：这个座位从来没被要求吐 JSON，所以它必须是"不在梯子上"那个值。
    assert ev.payload["meta"]["rung"] == -1, ev.payload["meta"]


async def test_the_players_sentence_lands_in_the_same_cell_the_models_do(tmp_path):
    """发言进的是 `payload.text`，和模型那条路同一格；渲染也只由 `render_line` 那一只手负责。

    这一条要在发牌之后判一次"这一轮到底答得了 `accuse` 吗"：`legal_actions` 的 act 名单是阶段×
    身份的函数，写死一个座位号等于拿一副牌替全部牌说话。判出来答不了就**红**，不是跳开——
    原来那一格写的是 `pytest.skip`，量过 400 副牌它一次没落进（这一支的名单与牌局无关，
    永远是那六枚），所以它唯一会开火的场合正是"`accuse` 从白天发言里被拿掉"那次：那次这条
    用例的整个前提就没了，跳开等于把这件事咽下去（`#153`、`#171` 那一课的两个方向之一）。
    """
    cfg, state, _, _, _, _ = _table(_desk(tmp_path, "deal"))
    state.phase = Phase.DAY_SPEECH
    legal = rules.legal_actions(state, 3)
    assert "accuse" in legal.acts, (
        f"这一条拿 `accuse` 当例子，而 3 号本轮答不了它（seed={SEED}，可答 {sorted(legal.acts)}）——"
        "白天发言的动作名单变了，这条用例的前提要跟着改，不许跳开了当没事")

    agent, log, console, outcome = await _turn(
        tmp_path, ["指控 8 他那句话前后对不上"], phase=Phase.DAY_SPEECH, kind=Kind.SPEECH)
    assert not outcome.fell_back
    ev = log.all()[-1]
    assert ev.payload["text"] == "他那句话前后对不上", ev.payload
    # 读侧不另起一只手：同一句话从 `cli.render_chronicle` 里出来才算落了盘。
    assert "他那句话前后对不上" in "\n".join(render_chronicle(log.all(), as_seat=3))


async def test_an_unintelligible_line_is_asked_again_without_burning_a_repair_retry(tmp_path):
    cfg, state, _, _, _, _ = _table(_desk(tmp_path, "deal"))
    state.phase = Phase.DAY_VOTE
    target = sorted(rules.legal_actions(state, 3).targets)[0]
    agent, log, console, outcome = await _turn(tmp_path, ["今天天气不错", f"票 {target}"])

    assert console.reads == 2, "第一次没读懂就该问第二遍"
    assert "没读懂" in console.screen, (
        "问了第二遍却没说为什么：玩家只看到同一张卡片重复了一次，会以为自己答对了")
    assert not outcome.fell_back and agent.retries == 0, (
        f"玩家打错字被记成了修复重试（retries={agent.retries}）：那是模型的账")
    assert not log.all()[-1].attempts, "被本座位挡回去的一行不该进偏好对"


async def test_a_closed_input_ends_the_turn_and_says_which_hand_answered(tmp_path):
    agent, log, console, outcome = await _turn(tmp_path, [])

    assert outcome.fell_back, "输入关了却没被说出来，这一轮就成了玩家的决定"
    ev = log.all()[-1]
    assert ev.result["fallback"] == 1
    assert "human_input_closed" in str(ev.attempts), ev.attempts
    # 引擎替投票座位做的是"不投"，不是替谁编一票（`default_action` 的口径）。
    assert ev.payload["act"] == "pass" and ev.payload["target"] is None


# ------------------------------------------------------------------------- 玩家看到的那一屏
async def test_the_card_offers_only_the_acts_this_turn_can_answer(tmp_path):
    """`heard` 里那句发言带着一个**这一轮答不上**的动作词（"出局"=accuse，投票轮闸门不收），
    且整句不含数字——否则下面那条"每个可点名的座位都上了卡片"就会由着法官的散文蒙对。

    这句发言同时是"局况必须落在 Offer 段之外"的证人：那一段一旦被挪进两格缩进里，
    `offered` 就会多出一个 accuse，这条立刻红。
    """
    ctx = await _ctx(tmp_path, phase=Phase.DAY_VOTE, heard=("有人喊先出局一个再说",))
    offered_words = _offer_block(human.decision_card(ctx))
    allowed = set(ctx.legal.acts) | ({"pass"} if ctx.legal.allow_pass else set())
    offered = {en for zh, en in ACT_SYNONYMS.items() if zh in offered_words}
    assert offered == allowed, (
        f"卡片列的与闸门答得上的不是一张表：多 {sorted(offered - allowed)}、"
        f"少 {sorted(allowed - offered)}")
    assert "vote" in offered, f"投票轮没给『票』这个字：{offered_words}"
    for seat in sorted(ctx.legal.targets):
        assert str(seat) in human.decision_card(ctx), f"可点名的 {seat} 号没出现在卡片上"


async def test_the_card_prints_one_example_and_that_example_parses(tmp_path):
    """卡片上「后面可以跟你要说的话」是一句关于**怎么打**的话，所以它得有一个真打得通的例子。

    这条不去匹配字符串（那种断言改个说法就红，改错了行为却不红），它把屏上那行示例原样交给解析器：
    示例一旦被写成「票3他说」这种被解析器打回去的紧挨着法，红的就是这一条。`#129` 那格里"卡片与
    解析器之间没有读者"从此有了一个。例子**不缩进**，所以它不在"这一轮可以答"那一段里：那一段的
    边界是两格缩进，例子里那句人话含着一个动作字，进了那段就等于给卡片加了一条没被法官允许的答法。
    """
    ctx = await _ctx(tmp_path, phase=Phase.DAY_VOTE, heard=("我同意先听他说完再决定",))
    card = human.decision_card(ctx)
    ex = [ln.strip().split("：", 1)[1] for ln in card.splitlines() if ln.strip().startswith("示例：")]
    assert len(ex) == 1, f"卡片上该有一行示例，实际 {len(ex)} 行：\n{card}"
    a = human.parse_human_line(ex[0])
    assert a is not None, f"卡片印的示例被解析器打回去了：{ex[0]!r}"
    assert a.act in set(ctx.legal.acts) | {"pass"}, f"示例答了一个本轮答不上的动作：{a.act}"
    assert a.speech, f"示例只教了怎么点名、没教怎么把话接在后面：{ex[0]!r}"
    assert a.target in set(ctx.legal.targets) | {None}


async def test_a_wolf_chat_turn_offers_a_word_and_an_example_that_answer_it(tmp_path):
    """`#129` 撞到的那一格：狼队夜里只有 `discuss` 一个 act，而它是词表**漏掉**的那一个。

    两格断言的顺序是有意的，这条用例的存在理由就在那个区别里。漏词时示例那一格不是断言失败而是
    `RuntimeError: coroutine raised StopIteration`（崩在 `agent.py` 里，整桌跟着倒），也就是卡片
    自己长出了一只没人接的刀；修法是把示例的词接到上面那个循环算好的 `words` 上，缺词就只是没印
    出这一行。于是"这一轮给的是什么词"必须**先**断言：空词在这里红得起来，而它一旦红，后面的
    示例那一格也就同时从"崩"变成了"红"。两格都留着，因为将来漏一个 act 时这两格说的是同一件事
    的两半——屏上没词、也就没例子。

    `LegalSet` 照 `phases.py` 狼聊那一轮的形状手搭（单 act + 可点名同伴），不去跑真相位：真相位
    得让狼坐在人类席上，那是 `test_run_with_human.py` 那一族的现场，这里只欠一张卡片。
    """
    ls = LegalSet(acts=("discuss",), targets=frozenset({4, 5}))
    ctx = await _ctx(tmp_path, phase=Phase.NIGHT_WOLF, legal=ls)
    card = human.decision_card(ctx)
    words = _offer_block(card).split(" 后面可以跟")[0]
    assert words.strip(), f"卡片给这一轮印了一个空词：\n{card}"
    for zh in words.split("/"):
        a = human.parse_human_line(zh)
        assert a is not None and a.act == "discuss", f"屏上这个词答不上本轮：{zh!r} → {a}"
    ex = [ln.split("：", 1)[1] for ln in card.splitlines() if ln.startswith("示例：")]
    assert len(ex) == 1, f"卡片上该有一行示例，实际 {len(ex)} 行：\n{card}"
    assert human.parse_human_line(ex[0]) is not None, f"示例打不通：{ex[0]!r}"


async def test_the_screen_says_what_this_seat_was_allowed_to_hear(tmp_path):
    """`#124` 的前半：那一屏不再只回答"这一轮能答什么"，也回答"发生过什么"。

    读的是 `ctx.percept`——和模型同一只手（`agent.py` 给每种座位都调 `percept_for`），而**不**是
    自己再去翻日志。后半句（"只到他有权的那部分"）在 `test_info_isolation.py` 里用金丝雀钉，
    两句话各自可失败，所以不并成一条。
    """
    heard = ("4 号昨夜整晚没出声，我想先问他", "我同意先听他说完再决定")
    ctx = await _ctx(tmp_path, phase=Phase.DAY_SPEECH, heard=heard)
    card = human.decision_card(ctx)
    for text in heard:
        assert text in card, f"他听到的那句话没上屏：{text}\n{card}"
    assert "4号" in card, f"上了屏却没说是谁说的：{card}"


async def test_the_screen_block_is_a_window_not_the_whole_transcript(tmp_path):
    """窗口是**有意的**，所以它也得有一条断言：一个人面前不该堆一百行。

    这条同时是 `SCREEN_TAIL` 这个常数的第二个读者：它若被改成一个不存在的数，上面那两条
    "该在的在"仍然全绿，只有这一条会问"为什么第 1 条不在了"。
    """
    heard = tuple(f"这是第 {i} 句公开发言内容" for i in range(1, human.SCREEN_TAIL + 3))
    ctx = await _ctx(tmp_path, phase=Phase.DAY_SPEECH, heard=heard)
    card = human.decision_card(ctx)
    shown = [t for t in heard if t in card]
    assert shown, "一屏里一条局况都没有"
    assert len(shown) < len(heard), "整份记录都上了屏：这一屏该是窗口，不是复盘"
    assert heard[-1] in card, "窗口砍掉的是最近的发言，方向反了"
    # 标题上那个"最近 N 条"是要给人看的数，所以它也得和屏上真的条数一致：改成一个不匹配的数
    # 就等于在卡片上写了一句假话，而这句话目前只有这一条读者。
    assert f"最近 {len(shown)} 条" in card, f"标题说了一个数，屏上是另一个数：{card.splitlines()[1]}"


async def test_the_card_says_what_the_judge_assigned_and_what_refusing_costs(tmp_path):
    """指派 act 是**硬**的（`legality.py:85` 的 `assigned_act` 门），玩家不知道就是在被驳回之后才知道。

    第二句不是装饰：换别的 act 会被记一条 `fallback=1`，而这一席的话仍然算他说的
    （`agent.py` 的 `_only_the_label_was_refused`）。卡片上说清楚，玩家才是在知情下选的。
    """
    cfg, state, _, _, _, _ = _table(_desk(tmp_path, "deal"))
    state.phase = Phase.DAY_SPEECH
    legal = rules.legal_actions(state, 3)
    ctx = await _ctx(tmp_path, phase=Phase.DAY_SPEECH,
                     legal=LegalSet(acts=legal.acts, targets=legal.targets,
                                    allow_pass=legal.allow_pass, assigned_act="probe"))
    card = human.decision_card(ctx)
    assert "probe" in card or any(zh in card for zh, en in ACT_SYNONYMS.items() if en == "probe")
    assert "指派" in card, card


class _PatientConsole(_Console):
    """一个"要等一会儿才答"的玩家，等的是别人给的信号，不是时钟。

    自旋而不是 `await`：这条测的就是"读输入有没有把事件循环占住"，用 `await asyncio.sleep` 会把
    被测的那一侧换掉。上限 2 秒后当作 EOF 返回 `None`，所以它最坏是红，不会挂住整条测试。
    """

    def __init__(self, lines, open_when: asyncio.Event):
        super().__init__(lines)
        self.open_when = open_when

    def read(self, prompt: str) -> str | None:
        for _ in range(2000):
            if self.open_when.is_set():
                return super().read(prompt)
            time.sleep(0.001)
        return None


async def test_reading_a_human_does_not_hold_the_event_loop(tmp_path):
    """§十五第 2 条在**实现之后**才第一次真的被走到。

    之前 `wave_size()` 把 blocking 座位剔出去是对的，但那一席的 `act()` 是 `NotImplementedError`，
    所以"读输入会不会把整桌卡住"这件事从来没有现场。这一条不测时长（时长测的是这台机器），只测
    一个二值：等一个人的时候，别的协程有没有被让出来跑过。
    """
    cfg, state, log, agent, table, _ = _table(tmp_path)
    state.phase = Phase.DAY_VOTE
    target = sorted(rules.legal_actions(state, 3).targets)[0]
    open_when = asyncio.Event()

    async def watcher():
        await asyncio.sleep(0.05)
        open_when.set()

    table.actors[3] = HumanActor(3, console=_PatientConsole([f"票 {target}"], open_when))
    await asyncio.gather(phases.run_vote(table), watcher())

    assert open_when.is_set(), (
        "有人在输入的时候没有任何别的协程被让出来跑过：读输入占住了事件循环，"
        "其余八座会被这一席卡死")
    mine = [e for e in log.all() if e.kind == Kind.VOTE and e.actor == 3]
    assert len(mine) == 1 and mine[0].payload["target"] == target, mine


# ------------------------------------------------------------------------- 打回之后的那一屏
async def test_the_second_screen_names_the_word_the_gate_sent_back(tmp_path):
    """法官把一行字打回之后，第二屏必须说出**哪个词**不对（`#127`）。

    这一席的 `attempts[]` 从来不缺——`agent.py` 的循环对每种座位都记一条拒绝，人打的也一样记。
    缺的是人这一侧：`retry_note` 今天唯一的读者是 `assemble.py` 的 C5 区，也就是只进模型的
    prompt，于是他被再问一次时看到的是**同一张卡片**，只能自己猜刚才哪里错了。这两格必须同时
    成立才谈得上"手打的一票和模型生成的一票在同一处失败、为同一个理由"：机器侧有账，人这一侧
    得有一句读得到的话。

    那一行「毒 5 …」是**读得通**的（`parse_human_line` 会给它 `act="poison"`），所以这一屏不会
    走"没读懂"那条本座位内的循环；它是在共享闸门上被 `act_not_allowed` 打回的，因此占的是
    `cfg.max_repair_retries` 那一格预算——这正是要说清的理由从哪来的地方。
    """
    _, _, console, outcome = await _turn(
        tmp_path, ["毒 5 我先把他毒掉", "票 5 就这么定"],
        legal=LegalSet(acts=("vote",), targets=frozenset({5}), allow_pass=True))
    cards = [s for s in console.shown if s.startswith("轮到你了")]
    assert len(cards) == 2, f"这一行该被打回再问一次，实得 {len(cards)} 屏：{console.shown}"
    assert len(outcome.attempts) == 1, (
        f"机器侧该有一条拒绝记录，实得 {outcome.attempts}")
    assert "act_not_allowed" in str(outcome.attempts[0]), outcome.attempts
    assert not outcome.fell_back, "第二行是他自己打的，这一轮不该由引擎替答"
    assert outcome.action.act == "vote", outcome.action
    assert "打回" in cards[1] and "毒" in cards[1], (
        f"第二屏没有说出被打回的是哪个词：\n{cards[1]}")
    assert "打回" not in cards[0], "第一屏无账可报，出现「打回」就是假话"


async def test_an_out_of_set_act_in_a_soft_phase_is_taken_as_typed(tmp_path):
    """软的那一轮根本不查 act 集合：越权的词照收，落盘的就是他打的那个动作（`#127` 第三行）。

    这一条今天**是绿的**，它不是缺陷报告而是**口径登记**：`legality.py` 的 `act_not_allowed`
    只在 `strict` 下进 `violations`，软相位把它放进 `flags` 而 `ok` 保持 True。于是"打一个这一
    轮没被 grant 的动作"有两种结局，取决于法官当时在哪个相位——卡片上那句承诺对这两种都许了同
    一个诺，所以对两种都不准。把它钉住是因为这是**选择**（软相位放行是 M4 那条测量留下的证据
    通道），不是疏忽；电池里对应的那把刀是把 `strict` 变成恒真。
    """
    _, _, console, outcome = await _turn(
        tmp_path, ["毒 5 我先把他毒掉"], phase=Phase.DAY_SPEECH, kind=Kind.SPEECH,
        legal=LegalSet(acts=("discuss",), targets=frozenset({5}), allow_pass=True))
    cards = [s for s in console.shown if s.startswith("轮到你了")]
    assert len(cards) == 1, f"软相位没有任何东西该被打回，却问了 {len(cards)} 遍"
    assert not outcome.fell_back, outcome
    assert outcome.action.act == "poison", (
        f"这一轮该照他打的收进日志，实得 {outcome.action}")


async def test_the_assigned_line_tells_the_truth_about_a_soft_phase(tmp_path):
    """软相位里换掉指派动作：他坚持的话**按他打的记**，卡片那一行必须这么写（`#127`）。

    `agent.py` 走的是 `_only_the_label_was_refused` 那一格：唯一没过账的是"标签"，句子是他的、
    动作也是他打的，于是 `action = last.action` 而不是 `default_action`——但 `fallback=1` 照写，
    服从率仍然读得到这一格。旧卡片那句「换成别的会被引擎代答一次」在这里是假的：没有东西被代答，
    被换掉的只有"这一轮算不算他服从指派"。留在全局名册（`#117`）里的那个 `fallback` 才是这条
    分支的记号，卡片不该把一个"标一下"说成"替他说"。
    """
    _, _, console, outcome = await _turn(
        tmp_path, ["毒 5 我就毒他", "毒 5 我不改"], phase=Phase.DAY_SPEECH, kind=Kind.SPEECH,
        legal=LegalSet(acts=("discuss", "last_words"), targets=frozenset({5}),
                       allow_pass=True, assigned_act="last_words"))
    cards = [s for s in console.shown if s.startswith("轮到你了")]
    assert len(cards) == 2, f"这一行该被打回再问一次，实得 {len(cards)} 屏"
    assert "act_not_as_assigned" in str(outcome.attempts[0]), outcome.attempts
    assert outcome.action.act == "poison", "留下的该是他自己打的那个动作"
    assert outcome.fell_back, "保留他的动作也要在账上标一次，否则服从率看不见这一格"
    assert "按你打的记" in cards[0], f"第一屏没说清这一格的代价：\n{cards[0]}"
    assert "被引擎代答一次" not in cards[0], "那句承诺在这一格是假话"
    assert "打回" in cards[1] and "毒" in cards[1], cards[1]


async def test_the_assigned_line_tells_the_truth_about_a_hard_phase(tmp_path):
    """硬相位里答一个本轮没有的动作：这一轮**真的**由引擎替他答，卡片得换成另一句（`#127`）。

    同一个判据在两把账下翻面：`act_not_allowed` 这时进了 `violations`，`_only_the_label_was_refused`
    因此为假，走的是 `default_action`——他打的那句话不进日志（它进 `attempts[]`，那里是数据集）。
    这一桌的 default 是弃票，所以落盘的动作连"票"都不是；上一条例外和这一条例外不是同一句话能
    许的诺，所以卡片要按相位分岔；分岔的依据就是闸门自己的 `HARD_PHASES`，不是第二份口径。
    """
    _, _, console, outcome = await _turn(
        tmp_path, ["毒 5 我就毒他", "毒 5 我不改"],
        legal=LegalSet(acts=("vote",), targets=frozenset({5}), allow_pass=True,
                       assigned_act="vote"))
    cards = [s for s in console.shown if s.startswith("轮到你了")]
    assert len(cards) == 2, cards
    assert "act_not_allowed" in str(outcome.attempts[0]), outcome.attempts
    assert outcome.fell_back
    assert outcome.action.act != "poison", (
        f"这一轮该由引擎替他答，落盘的不该还是他打的那个动作：{outcome.action}")
    assert "由引擎替你答" in cards[0], f"第一屏许了个不实的诺：\n{cards[0]}"
    assert "按你打的记" not in cards[0], "这一格他的句子不进日志，那句话是假话"
    assert "打回" in cards[1] and "毒" in cards[1], cards[1]


def test_a_code_the_card_has_no_words_for_is_still_shown():
    """闸门哪天多出一个卡片不认识的码，那一屏不许缩成一句空话（`#127`）。

    `refusal_lines` 只认识四种码，其余的原样印出去：一条看不见的拒绝等于一次没有理由的再问，
    而"有理由"正是这一片存在的目的。`target_not_legal` 走的是另一格——它点名的是座位不是动作。
    这条是分支覆盖的证人，落笔时就绿了，所以它的杀伤力由电池里 M3 那具（删掉 `else`）来还。
    """
    lines = human.refusal_lines(("target_not_legal:7 (只能 [3, 5])",
                                 "invented_event_ids:['e9']",
                                 "something_new_the_gate_invented"))
    assert lines[0] == "7 号这一轮点不到", lines
    assert "invented_event_ids" in lines[1], lines
    assert "something_new_the_gate_invented" in lines[2], lines


async def test_the_wolf_seat_reads_his_team_above_the_recap(tmp_path):
    """坐在狼位上的人，卡片得告诉他队友是谁——而且不能只靠局况里那一行发牌（`#133`）。

    发牌行现在会印名册了（`compress.render_line` 的 DEAL 分支），可这一屏的局况只有
    `SCREEN_TAIL = 12` 条：打到第 13 条事件之后，那张牌就从人眼前滚走了，而他的队友不会因此
    变少。所以这一格要在页眉上独立成立一次。测试读的是真 `TurnContext`（`_turn` 走的是
    `agent.take_turn`），座位号从发牌里偷看，不是测试自己指定的。
    """
    wolf = _table(_desk(tmp_path, "peek"))[5]
    _, log, console, _ = await _turn(
        tmp_path, ["讨论 5 今晚刀他。"], seat=wolf, phase=Phase.NIGHT_WOLF,
        kind=Kind.WOLF_CHAT, legal=LegalSet(acts=("discuss",), targets=frozenset({5})))
    deal = [e for e in log.all() if e.kind == Kind.DEAL and e.actor == wolf][0]
    mates = sorted(deal.payload["teammates"])
    assert mates, f"这副牌里 {wolf} 号狼没有队友，正证就无从谈起"
    card = [s for s in console.shown if s.startswith("轮到你了")][0]
    head = card.splitlines()
    line = next((ln for ln in head if "队友" in ln), "")
    assert line, f"卡片上找不到队友：\n{card}"
    assert head.index(line) <= 1, f"队友该在页眉，不是滚在局况里：\n{card}"
    for m in mates:
        assert f"{m}号" in line, f"{m} 号是他队友，那一行没点名：{line}"
    assert f"{wolf}号" not in line, f"自己不算自己的队友：{line}"


async def test_a_villager_seat_is_not_handed_a_team(tmp_path):
    """反证：平民那一屏不能多出"队友"两个字（`#133`）。

    写侧给非狼座位落的是一空 `teammates`（`game.py:131`），所以这一格的红不是"忘了写"而是"写错了对象"——
    一旦名册无条件印出去，平民就会看到 `你的队友是 。`，那比空白更糟：它是一张骗人的身份卡。
    `#124` 的金丝雀管的是"看不到的不许印"，这一条管的是"没有的不许编"。
    """
    _, _, console, _ = await _turn(tmp_path, ["投票 5"], seat=3)
    card = [s for s in console.shown if s.startswith("轮到你了")][0]
    assert "3 号" in card.splitlines()[0], card.splitlines()[0]
    assert "队友" not in card, f"平民没有队友：\n{card}"


async def test_the_seat_number_typed_with_a_targetless_act_reaches_a_reader(tmp_path):
    """真人打「讨论 5 今晚刀他」，那个 5 不能只活在日志里（`#130`）。

    写侧一直是通的：`agent.py` 给每种决策都带 `target`，形状表也给 `wolf_chat` 声明了这一格。
    断的是后半段——渲染层只印文本，于是那一格落盘之后没有读者。这一条从键盘一路走到模型和复盘
    共用的那条渲染梯子，中间不换数据源。
    """
    agent, log, console, outcome = await _turn(
        tmp_path, ["讨论 5 今晚刀他。"], seat=3, phase=Phase.NIGHT_WOLF, kind=Kind.WOLF_CHAT,
        legal=LegalSet(acts=("discuss",), targets=frozenset({2, 5})))
    assert not outcome.fell_back, outcome
    ev = log.all()[-1]
    assert ev.kind == Kind.WOLF_CHAT and ev.payload.get("target") == 5, ev.payload
    assert "（指 5号）" in compress.render_line(ev), compress.render_line(ev)


async def test_a_seat_the_judge_never_granted_is_named_on_the_second_screen(tmp_path):
    """「讨论 9 …」里那个 9 不在名单里：硬相位打回一次，第二屏要说清点不到的是谁（`#130`）。

    闸门从前对 targetless 的动作根本不看 target，所以这一格在真人那一侧的形状不是"一次拒绝"而是
    "一句没有理由的再问"——他甚至不知道自己哪里不对。现在它走的是 `#127` 那条路：码进
    `TurnContext.refusal`，人话由 `refusal_lines` 印成「9 号这一轮点不到」。
    """
    agent, log, console, outcome = await _turn(
        tmp_path, ["讨论 9 今晚刀他。", "讨论 5 那就他。"], seat=3, phase=Phase.NIGHT_WOLF,
        kind=Kind.WOLF_CHAT, legal=LegalSet(acts=("discuss",), targets=frozenset({2, 5})))
    cards = [s for s in console.shown if s.startswith("轮到你了")]
    assert len(cards) == 2, f"点了个不存在的座位，该问第二遍：{len(cards)} 屏"
    assert "9 号这一轮点不到" in cards[1], cards[1]
    assert "打回" in cards[1], cards[1]
    assert not outcome.fell_back, outcome
    assert log.all()[-1].payload.get("target") == 5, log.all()[-1].payload
