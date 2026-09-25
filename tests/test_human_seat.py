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

import pytest

from test_actor_contract import SEED, _table  # 同一副牌、同一张桌，不重造现场
from wolfengine import human, phases, rules
from wolfengine.actors import HumanActor, Proposal
from wolfengine.cli import render_chronicle
from wolfengine.events import Kind, PUBLIC
from wolfengine.schema import ACT_SYNONYMS
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


def test_the_words_the_player_can_type_are_the_ones_the_ladder_already_knows():
    """可打的词**整张**来自 `ACT_SYNONYMS`：既不少一个（玩家打不出模型认得的词），也不多一个
    （那张表就成了两处）。少一条断言都拦不住"给真人单独加个词"这种顺手改。
    """
    wrong = [(zh, en) for zh, en in ACT_SYNONYMS.items()
             if (a := human.parse_human_line(zh)) is None or a.act != en]
    assert not wrong, f"这些词读懂的结果和词表不一致：{wrong[:5]}"


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
    身份的函数，写死一个座位号等于拿一副牌替全部牌说话。
    """
    cfg, state, _, _, _, _ = _table(_desk(tmp_path, "deal"))
    state.phase = Phase.DAY_SPEECH
    legal = rules.legal_actions(state, 3)
    if "accuse" not in legal.acts:
        pytest.skip(f"这一副牌（seed={SEED}）3 号本轮答不了 accuse，可答 {sorted(legal.acts)}")

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
    """指派 act 是**硬**的（`legality.py:85`），玩家不知道就是在被驳回之后才知道。

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
