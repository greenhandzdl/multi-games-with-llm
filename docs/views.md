# 视图层：一个渲染器，三种看法

计划 §9 把"能拿给人看"拆成两半：**直播**（`render_live.py`，rich 终端）和**复盘**
（`render_html.py`，单文件 HTML）。两半共用同一批判定，且都只读已经落盘的 JSONL——端点下线、
被人换了权重、模型换了版本，演示照常能跑。这是 plan §10 给 M6 定的通过判据。

## 为什么只有一个渲染器

三条理由，都来自 §9：JSONL 是真相源，UI 读它就**不可能**与游戏状态不同步；直播与复盘共用代码，
一份判定改一次就够；真流式和缓冲式只差观感不差功能——UI 的逻辑是"日志多了一条就重画"，那么
M0 量出什么都不用改这块代码。**把风险变成非风险**。

代价是这些规则只能有一份定义，于是 `tests/test_wiring.py` 直接扫源码钉住（下表 6 行 = 8 个函数名，
`test_the_two_views_share_one_definition_of_each_viewing_rule` 一条参数化用例钉一个）：

| 规则 | 唯一所有者 | 回答的问题 |
|---|---|---|
| `shown_events` | `render_html.py` | 观众能看见哪些事件（`visibility == "all"`） |
| `event_flags` / `markers` | `render_html.py` | 一条 turn 带哪些闸门标记 |
| `mind_pairs` | `render_html.py` | 哪些轮次算"说了心里的话" |
| `roles_by_seat` / `role_zh` | `render_html.py` | 谁是什么身份，中文叫什么 |
| `game_over_event` | `render_html.py` | 这局结没结束（`身份公开` 的唯一闸门） |
| `voting_waves` | `events.py` | 一轮投票从哪到哪（复盘的格子与指标的 mandate 共用，见下） |

`render_live.py` 全部 import，不许另起一份。两份定义意味着两块屏幕描述的不是同一局游戏。

钉法是**两侧**各一条断言，不是一句"提到过就行"：全工程只能有一个 `def 这个名字(`（扫
`src/wolfengine/**/*.py` 的源码文本），而每个消费者必须**真的从 owner 模块 import 它**（AST 里
`from .owner import ...` 的导入名）。第二侧早先是子串判断，注释里留个词就算通过——所以它现在
数的是导入语句，见下面"这些守卫是怎么验的"的 W1–W3。

## 三种看法，差别只在私有事件

```bash
wolf replay  <file>              # 观众：只有公开事件
wolf replay  <file> --seat 3     # 3号：公开事件 + 它自己看得见的
wolf replay  <file> --god        # 上帝：全部
wolf export  <file> [--god]      # 同样的两种模式，落一个能发出去的 HTML
wolf watch   <file> [--god] [--seat N]   # 同一批判定，终端里 tail
```

`export` 默认输出在日志旁边（同名 `.html`），默认是观众模式——**发出去的那份默认是安全的**，
上帝视角要显式 `--god`。HTML 自包含：无 JS、无外链、无字体、不含端点地址，只有
`string.Template` 加 `html.escape`（复盘文件会被打开在第三方的机器上，发言文本是不可信输入）。

`--seat` 与 `--god` 同时给时，坐进某一位优先，`--god` 不再往上抬。07:55:40Z 在同一份日志上敲了四种
给法（`/tmp/seat_precedence.out`）：只给 `--god` 的是 104 行、sha 前 12 位 `8918d0e775a1`；只要句子里
出现了 `--seat 3`——放在 `--god` 前、放在后面、或者根本不给 `--god`——输出都是同一份 81 行
`dd111ac69f24`。这一格只能这样量：判定点只有一处，`cli.py:209` 那句 `if as_seat is not None` 排在
`god` 那一支前面——给了座位就按那位玩家的可感知集合渲染，`god` 无从往上加。这句话钉在用例
`test_sitting_at_a_seat_wins_over_the_god_view_on_the_same_file`（`tests/test_cli.py`）上：把两支换序
只有它红（P1，08:03:54Z，`/tmp/mut60.out`）。在那之前这一支没有任何读者——仓库里 `god=True` 的十几处
全在别的文件、且只给一个参数，换序也不会红，而这条命令是**默认会被人在同一行里两个开关都给出去**的。

## 观众模式挡的是两种泄漏，`visibility` 只能看见一种

1. **私有事件**（狼队私聊、验人结果、`法官（私发）`）——字段级，`info.py` 让它结构上不该出现。
2. **公开事件里的私有内容**：模型自报的怀疑排序 `belief.suspects` 装在一条**公开发言**的
   payload 里。可见性字段看不见它，只有渲染层不去读它才不泄漏。

所以 `tests/test_render_html.py` 把金样本里 24 条自报文本逐条断言"不在观众文档里、在上帝文档里"，
`tests/test_render_live.py` 对 22 条渲染行做同一件事。两个方向都要断言，否则"什么都没打印"的
渲染器也能通过。

同类的一条：上帝视角里每个座位卡上的 `data-suspicion` 曲线是**该席自己视图**跑出来的
engine belief（`build_belief(seat, [e for e in events if e.visible_to(seat)])`），不是全量日志
重算——publishing 曲线就等于 publish 预言家的验人结果。

## `身份公开` 由时间放行，不由模式放行

终局事件一落，**两种模式都**印全桌身份；在那之前谁都不能说出角色名（观众、甚至上帝视角都不印
`身份公开` 这一段）。

理由：游戏已经结束了，没有剧透可损，而复盘的主交付物就是"最后是谁"。所以放行条件是日志里有没有
`game_over`，而不是 `god` 参数——`game_over_event()` 是唯一闸门，也是这个文件里最像"UI 层金丝雀"
的守卫：删掉它，观众 HTML 立刻变成 spoiler，没有别的症状。

角色名统一走 `roles.board_for()` 的 `name_zh`，两种视图同一份词表。有两处**故意**保留英文 id：
`data-role="wolf"` 这类机器可读钩子（与 `data-death="9:wolf_kill"` 一致），以及发牌那一行
`法官（私发）：你的身份是 wolf`——那是 `render_line` 渲染出的**模型当时被告诉的那句话**，
时间线渲染器全工程只有一个，不为了排版好看而改它。

## 直播这一屏

`watch` 读一个**还在被写**的文件，这是全工程唯一在"写一半"的时候去读的地方，于是有三条规则：

* **容忍 torn tail，但这份容忍不住在直播层**：末行 parse 不过就丢掉等下一次 poll——`run` 是逐行
  append 的，半个 JSON 对象是常态不是损坏。界是**一行**：`render_live` 曾经自己养一份 while 弹循环，
  它能一路弹掉好几行坏数据，比任何离线读法都宽，而"直播放得出来、`audit` 拒读同一份文件"正是这份
  分裂的产物。现在两条腿都走 `EventLog.read_split`，实现在 `events.py` 只有一份，由
  `test_the_torn_tail_bound_has_one_owner_and_every_reader_calls_it` 钉住。**中间**坏一行仍然直接抛，
  沉默是对它的错误回答；抛的是 `LogDamage`，消息带文件名和行号，因为"行内第 209 列"对一份 500 行的
  日志等于没说。丢掉的字节只数不印：那半行可能是一条狼队私聊。同一条句子也管拿错文件——`--dry-run`
  的提示词转储和那些局躺在同一个目录里，把它喂给 `watch` 报的是"第 1 行没有 seq/kind/day/visibility，
  它有的键是 messages/…"，不再是丢了文件名的 `KeyError`。这句话说给谁看也有讲究：`cli.main()` 里唯一
  那一处 `except LogDamage` 把它接成一行 stderr 加退出码 2，`audit`/`replay`/`export`/`watch` 四个读
  文件的出口走同一条腿（`export` 在这种输入上不落盘），终端上不再出现 traceback。路径**本身**用不了
  （不存在、是个目录、写不下去）是同一格边缘上的另一半：`main()` 里另一处 `except OSError` 说同一句
  `路径用不了：…`、同样 rc 2——因为 rc 1 在这仓库里是"引擎拒绝"，不该被一次拼错的文件名占用。
* **退出码也会被顶掉**：上面两句讲的都是"该说 2 的地方说了 1"，`#57` 是这一格的第三面——那个 1
  根本不是判据给的。修前 `run` 与 `batch` 在**第二个** event loop 里 `transport.aclose()`，
  `httpx.AsyncClient` 的 socket 绑在打开它的那个 loop 上，于是收尾抛 `RuntimeError: Event loop is
  closed`，而它站在 `finally` 里，把 `return rc` 顶掉：05:23:33Z 实测一局**打完了**的局（60 个事件、
  `draw_day_limit`）返回 1，05:25:14Z 一个跑完了的批次连 `批次 ->` 那一行都没印出来。按
  [comparison.md](comparison.md) 第 38 行那句契约，1 是"拒绝出结论"——脚本读到的是一句判决，实际发生的
  是一次崩溃。现在请求和关闭共用同一个 loop（`cli._run_and_close`，两个读者 `cli.py:151` / `cli.py:524`），
  退出码重新只来自判据。证人不能是进程内调用：`rc` 被 `SystemExit` 接住就看不出形状了，所以那条用例
  在子进程里跑真 `cli.main`、连的是 127.0.0.1 上的桩
  （`test_a_finished_game_exits_0_although_a_socket_was_opened`）。
* **永不写回**：日志是指标、复盘、批次比较共同的主体，一个会重写它的查看器在销毁自己的研究对象。
  `tests/test_render_live.py` 用 sha256 钉住"看完之后文件字节不变"。
* **manifest 只有一个读者**：页眉那句 `狼人杀直播 <game_id> · 第N天` 里的名字，和 `audit`、指标读
  的是同一行——判据是 **`seq == 0` 且跳过空行**，不是"文件第一行"。`render_live` 过去自己开一次文件、
  取第一行、解析失败就当作没有，于是文件开头多一个空行（手写或手改的夹具，正是 `events.py` 写明要容忍
  的那一类）时，离线读侧照常认出这一局、页眉静默少一格。2026-09-22T00:49Z 实测复现：同一份文件
  `read_records` 报 `game_id='g-header-7'`，页眉印成 `狼人杀直播  · 第1天`。修法不是给那半份判据补
  空行处理，是删掉它：整个模块现在只有 `_read` 一处开日志，事件和 manifest 从同一次 `read_split` 里
  出来。钉它的那条用例逐处点名——一帧里报了两次这局的名字（页眉、页脚），掉任何一处都红，只断言
  "输出里出现过"是抓不到的。
* **认不出是哪一局时，页眉说一句而不是留个空洞**：`meta_notice(meta)` 住在 `events.py`（和撕裂那句
  同一个待遇），manifest 缺席时三个出口各印一句"这个文件没有开局记录…"。`#48` 那条管的是"有那一行、
  页眉自己没读到"，这一条管"根本没有那一行"——同一个名槽，两种原因，而后一种以前是沉默的：`replay`
  一个字都不印、退出码还是 0。2026-09-22T02:38:54Z 实测三个出口各有一行，退出码**仍然**是 0，因为
  "没有可看的"就是这份文件的结论（2 归"命令本身不对"）。直播在这里说"没有开局记录"却不说"这里截断"：
  逐帧刷新的末行本来就在长，那是写进测试的决定（`test_a_torn_last_line_is_skipped_not_fatal`），
  不是漏掉的一格。
* **"没得看"的三句话：前两句互斥，第三句可以叠在它们上面**：认不出是哪一局（`meta_notice`）、这一局
  只有开局记录（`empty_notice`）、末尾被砍了一行（`torn_notice`），三只手都在 `events.py`，三个出口
  各调一遍。互斥的是前两句——同一份文件不能既"没有开局记录"又"开局记录后面是空的"，两个方向各有一条
  用例钉着。第三句讲的是**文件末尾**，不是"有没有内容"，所以它和前两句都能同时亮：manifest 在、后面
  只有一条写了一半的行，转录就是两句（先"这一局只有开局记录"、再"日志在这里截断"）——2026-09-22T04:00:41Z
  实测，钉在 `test_a_cut_file_that_never_got_past_the_opening_record_says_both`（这一句以前只活在散文里，
  而且是写反的散文）。中间那句读的是**文件里**的事件数，不是这一屏被允许看的事件数：一局只留下私有
  频道记录的日志在观众屏上是空的，说它"只有开局记录"是假话——那格由
  `test_a_frame_with_nothing_visible_does_not_claim_the_file_is_empty` 钉住。
* **同一把尺用在页眉的三个计数上**：`_counts` 拿的是 `events`（整份文件），不是 `shown_events`
  的结果，所以观众页和上帝页的「发言N条 · 私有事件M条 · 闸门拒绝K次」必须是同一串数字，两页只差
  在视角两个字。这条不是设计洁癖，是从一份真产物里读出来的（`#90`）：一局真日志的观众页印着
  「私有事件0条」，同一份文件磁盘上有 26 条非公开记录，而**同一页**的页脚写着私有频道不在这份文件
  里——一句话说自己没有，另一句话说有。修法是把计数换成读文件，**不是**给页眉补一句"（本文件不显示）"：
  "这一屏给你看了哪些"已经有一个落脚点了，就是页脚，一个主张只许有一个来源，所以那句被第四、第三条
  用例分头钉住。四条：`test_the_audience_page_counts_the_private_events_the_file_holds`（数对不对）、
  `test_the_two_pages_print_one_number_for_each_of_the_three_counts`（两页是不是同一个数）、
  `test_a_refusal_in_a_private_channel_is_counted_by_both_pages_but_shown_by_one`（计数读文件而正文仍按视角
  过滤；那一轮私有频道被拒两次，为的是把"被拒几次"和"有几轮被拒"这两个读数分开——两者在每次只拒一轮
  的输入上恰好相等，第一刀的变异就是从这里漏过去的）、`test_the_audience_footer_owns_the_which_ones_shown_claim`
  （页脚那句被删就红）。
* **四句话同时亮时的先后也是内容**：转录与页面页眉的追加顺序固定为 认不出局号 → 没记下来 → 编号破损
  → 末尾截断，页面里那几个 ⚠ 就是同一个顺序（`render_html` 里那句注释说的就是这件事）。它不是排版
  偏好："页眉那个 ⚠ 和转录最后一行是同一句话"这条对账，只在输入只带一句时成立，两句同时亮时只有顺序
  能分辨。四具只打顺序、不改内容的变异（S11/S13 打转录、S12/S14 打页面）红在这两条用例上。直播那一屏
  只说前三句里它能说的（`meta_notice` + 一个 `(empty_notice, seq_notice)` 的元组，末尾撕裂不在这里说，
  见上面 `#52` 那格），而它那两个**构造上不会同时亮**：`empty_notice` 要 `events` 为空，`seq_damage`
  对空列表三个计数全为 0。所以那一格里"顺序"没有可打的靶子，这一条是声明，不是漏掉的一格。
* **第四句管的不是"有没有得看"，是"这是不是一局"**：`seq_damage(events)` 数缺号 / 重号 / 顺序倒挂，
  `seq_notice` 把非零的那几项拼成一句，`audit` 印的是同一个 dict（那只手算一次，两边各说一遍）。
  三个出口都**不替文件重排序**：印出来的锚点序列就是文件自己的序列，修过的文件得让人看得出来修过。
  而两条开局记录（`cat` 两局日志）不走这一句——读取侧在这里同意写入侧 `write_meta` 早已下过的判断，
  直接 `LogDamage`、退出码 2：那种文件里没有"一局"可展示，挑一份局号写页眉是撒谎而不是一句缺话。

重绘条件只有两个：来了新事件，或者**视图状态变了**。后者是演示现场的要害——对着昨天那局按 `g`，
日志早就不再增长了，如果只在新事件时重画，`g` 就是个死键，而演讲者正在观众面前按它。反过来，
被忽略的键**不**触发重绘：每次 poll 都重画会闪，长局没法看。

rich 有两条坑，都各有一条测试钉着：

* `[...]` 是样式标签。发言里满是 `[e17]` 这样的锚点，模型还会自己打出 `[/]`。所以**每一行**都
  `markup=False`，表格里放 `Text` 而不是 `str`：这个视图只**打印**日志内容，从不**服从**它。
  颜色只来自 `style=`，值是本模块挑的名字。
* 一行太长不能折。`frame_text` 用 `soft_wrap=True`：否则"这段文字不在屏幕上"的断言会因为被切成
  两行而假绿。

## 标记词表

`〔拒绝N次〕` 闸门打回并重试过 · `〔不可能感知〕` 对只有公开事件可见的窗口做了第一人称感知断言 ·
`〔发言超长〕` 说过 `SPEECH_SOFT_LIMIT`（`legality.py`，140 字符）· `〔引擎代打〕` fallback。

这四个标记是界面最重要的部分之一：一份干净的记录把一局里最有意思的两件事藏起来了。同一个 flag
可能同时出现在 `result.flags` 与 `payload.meta.flags` 里，所以 `event_flags()` 去重——观众数的是
标记个数，不是来源个数。

`〔发言超长〕` 到 2026-09-21 才有断言，而且**不在整局用例上**：authored 转录 17 条发言全部短于
140 字符（实测 0 条 `speech_too_long`），所以在那份日志上"把这条渲染分支整个删掉"是等价变异，
与 `弃票` 那一列同一困境。改成两侧各钉一条：`test_render_html.py` 直接喂 `markers()` 一手数据
（有 flag / 只有 `speech_empty` / 无 flag 三种，外加"标记里不许出现字符数"和"两处来源只印一次"），
`test_wiring.py` 钉**跨模块的拼写接缝**——`legality` 写 `speech_too_long:{len}`，`render_html` 读
`startswith("speech_too_long")`，两侧各自改名都没有别的症状，只会让屏幕上一局人人都很简短。
阈值从 `legality.SPEECH_SOFT_LIMIT` 读而不是在测试里写死 141，否则把限挪到 400 这条用例照样绿。

## 票型矩阵：一轮一张表

每一天里，投票不是一份"投票人 → 被投人"的两列清单，而是**行 = 投票人、列 = 该轮真的收到过票
的座位**的矩阵，外加一列 `弃票`——只在有人弃票时出现。格子只在"这张票确实存在"时打点，没投的
格子是**真空单元格**（不填 `·`、不填空格）。两列清单要读者自己逐行数完再在脑子里做交叉，而
`export` 产出的是一份**发出去**的文件，它不该向读者要作业。

分割单位是**轮**不是天：`phases._ballots` 写完一整波选票才落一条 `vote_result`，而平票时
`tie_break == "pk_once_then_nobody"` 会在**同一天**再写一波。所以矩阵按 `vote_result` 断波，
切波这件事只有一个定义——`events.voting_waves()`，指标的 `ballot_mandate` 也数它（口径见
[docs/metrics.md](metrics.md) 的 M8）——一张表带 `data-day` / `data-wave`，标题"第2天 第2轮投票"。
摊平成"一天一张表"会把 2 号那两次票挤进同一格——那正是这份文件最不该给错的数字。数字本身仍然
只从 `vote_result` 读，矩阵只负责把票铺成格子，不在这里重算一遍票数。

`弃票` 那一列不是边缘情况：`--mock` 的替身策略按 persona 的攻击性决定要不要废掉一张票
（`actors.py` 里投票分支的 `act="pass"`），2026-09-21 本机连跑 12 局实测 **248 张票里 91 张
弃票（36.7 %），且 12 局每一局都至少有一张**——而 authored 金样本转录里一张都没有。这两个数
朝相反方向偏，正是下面"把断言钉在 `_matrix` 上而不是钉在整局用例上"的理由。

> 复现：`wolf run --mock --seed 11 --games 12 --quiet --out /tmp/matrixchk`，然后数
> `kind == "vote"` 且 `payload.target is null` 的行（日志首行是 meta 记录，没有 `kind`，要跳过）。

## 页脚那句"本局不可复现"

两个渲染器都固定印这句话。这个端点没有确定性：同 request 同 seed 也会给不同输出，seed 只决定
发牌与座位序。所以这份文件是**当时落盘日志的渲染**，不是一次重跑——把 HTML 发给别人时，这句话
就是防止有人以为拿到的是一条能复现的轨迹。

## 还没做的

* **逐字流式出现**：计划 §9 的 MVP 清单里有一条"说话人文本逐格出现"。现在一条发言是**整条**
  出现的，因为 UI 只读日志，而日志只在一次 turn 完成时才落一行。真流式需要 UI 去读模型的 token
  流——那正是 §9 决定不做的解耦。**代价是观感，换来的是两种模式共享同一份判定。**
* **让一个人从命令行坐到桌边**：座位本体已经实现了（`#122`）——`human.py` 读一行字，
  `HumanActor.act()` 把它变成一个 `Proposal`，`agent.py` 用同一道合法性闸门过它。它的用例在
  `tests/test_human_seat.py`（10 条）：读懂的那一行进了哪个格子、读不懂时问第二遍而不烧模型的
  修复额度、输入关了要说清是谁答的、以及"等这个人打字时其余八座没有被挂住"。契约那三条"上桌前
  必须清掉的假设"（plan §15）另有一组测试兜着：`tests/test_actor_contract.py` 钉住 `timeout_for()`
  返回 `None` 的座位不能被 `Config` 的地板值判死、`wave_size()` 把 `blocking` 座位剔出 worker
  名额且无论如何不留 0、墙钟只对全模型桌生效（`aborted_wallclock` 正反各一条）；替身是 `_Seat`——
  kind / blocking / 截止时间 / 答题耗时四个旋钮各自独立，绑成"像不像人"就只剩人设可测，规则本身
  测不到。还缺的两件事都在 CLI 与这一层：`wolf run` 没有 `--human` 旋钮，所以生产代码里今天没有
  一处构造 `HumanActor`（`#123`）；而那一屏"给这个人看什么"还没有金丝雀——`decision_card()` 读的是
  `LegalSet` 而不是事件流，是一条**新的**输出通路，`test_info_isolation.py` 那套正反证的是
  `percept_for` 过滤得对，够不到它（`#124`）。
* **解说员 LLM**：它的 percept 只含公开事件，结构上不可能泄密，是后置清单里性价比最高的一个。
* 单文件 HTML 里的曲线是 `data-suspicion` 属性（无 JS），要画成图得引入脚本——与"自包含 +
  不可信输入"冲突，目前用 `wolf audit` 出数字，不画图。

## 这些守卫是怎么验的

渲染层 56 条用例（`test_render_html.py` 27 + `test_render_live.py` 29）里，每条都被"把被保护的
分支改坏"验过一次它会真的红，改完再按 sha256 校验还原成字节相同的文件。本轮重跑并确认被抓住的：

| 坏改动 | 谁变红了 |
|---|---|
| `shown_events` 不再过滤（观众看到全部事件） | 6 条，含两个模式的观众泄漏金丝雀 |
| `心里想 / 嘴上说` 面板不再限定上帝视角 | 2 条（自报 belief 精确集合 + 面板归属） |
| `身份公开` 的闸门删掉 / 改成看天数 / 只给上帝视角 | `test_roles_are_revealed_in_both_modes_once_the_game_ends` |
| `role_zh` 退回原始 id（两种视图语言漂移） | 3 条，跨 HTML 与 live |
| `event_flags` 不去重（一个 flag 印两遍） | `test_the_gate_is_visible_not_silently_applied` |
| rich 的 `markup=False` 去掉 | 3 条，含 `[/]` 注入那条 |
| `soft_wrap=True` 去掉（长行被折） | `test_a_long_line_reaches_the_frame_unbroken` |
| 按键不再触发重绘 / 每次 poll 都重绘 | 各自一条，互为反例 |
| `handle_key` 不再区分"这轮没有按键"（`None` 被当按键处理） | `test_a_poll_that_returned_no_key_is_not_a_key_press` |
| Enter 不再走席 / 改成收回面板 / 到 9 之后不回绕 | 三条各被 `test_enter_steps_through_the_seats_one_at_a_time` 与 `test_a_poll_that_returned_no_key_is_not_a_key_press` 抓住 |
| `""` 又被当成 esc | 2 条（单位 + 整局面板，后者看的是帧数） |
| 闭着的 stdin 继续被读成按键 | `test_a_closed_keyboard_stops_polling_and_leaves_when_the_log_is_done` |
| "没有键盘且日志已终局"这条退出删掉 | 同一条**挂起**，harness 判 `CAUGHT [hang after 25s]`（去掉退出后循环不再回到 `_read_key`，fake 的 50 次保险也触发不了） |
| `_read_key` 里 `""`→EOF、`\x1b`→escape 两条映射各删一次 | `test_the_reader_reports_a_closed_stdin_as_itself`（两个半边） |
| 票型矩阵的 `弃票` 列常驻 / 永不出现 | 整局的行列那条 + `test_the_abstain_column_appears_exactly_when_somebody_abstained`（常驻那侧两条都红） |
| 矩阵行按座位号重排（不是落票序） | `test_the_rows_are_in_the_order_the_ballots_were_written` |
| `data-wave` 写死成 1，PK 重投没有自己的格子 | `test_the_ballot_marks_are_the_ballots_in_the_log` |
| 票数不再从 `vote_result` 读，改成矩阵这里重算 | `test_the_day_one_columns_add_up_to_the_tally` |
| 空格填进占位符 / 每格都打点 | 前者 1 条、后者 4 条（整局 + 行列 + 两条单位用例） |
| 直播旁边再写一份 `role_zh`（两份观众口径） | W1 → `...share_one_definition_of_each_viewing_rule[role_zh]`（数 `def` 那一侧抓住） |
| `render_live.py` 不再 import `role_zh`，改成旁边一个 lambda，词留在注释里 | W2 → 同一条 `[role_zh]`。**这一具是改断言方式的理由**：旧的子串判断对它照样绿 |
| `metrics.py` 不用 `events.voting_waves`，自己切一份波 | W3 → `...[voting_waves]`（消费者那一侧，owner 是 `events.py` 不是 `render_html.py`） |
| `speech_too_long:{len}` 改名成 `speech_long` | T2 → `test_wiring.py::test_the_over_long_flag_the_gate_writes_is_the_one_the_renderer_reads`（改 `legality` 一侧） |
| 超长不记 flag / 渲染分支删掉 | T3、T5 → 同上那条 + `test_render_html.py::test_the_over_long_marker_needs_its_own_flag_and_prints_no_length` |
| `>` 写成 `>=`（到线即超长）/ 只留前缀不留长度 | T1、T4 → 接缝那条（边界断言读 `legality.SPEECH_SOFT_LIMIT` 现算，不写死 141） |
| 前缀放宽成 `speech`（弃票也说成超长）/ 把字符数印进标记 / 两处来源不去重 | T6、T7、T8 → `...markers_needs_its_own_flag...`（T8 另抓 `test_the_gate_is_visible_not_silently_applied`） |

W 那三行是 2026-09-21 把守卫从 4 个名字扩到 8 个、并把"确实在用那一份"从子串改成 AST 导入名
之后跑的（`/tmp/mut_wiring.py`，只跑 `tests/test_wiring.py -k share_one_definition` 这一条参数化
用例，三具全部 CAUGHT 且具名如上；跑完按 `cmp` 校验 `render_live.py` 与 `metrics.py` 字节还原）。

T 那四行是同一轮给 `〔发言超长〕` 补断言时跑的（`/tmp/mut_toolong.py`，8 具全部 CAUGHT，具名如上；
跑完 `cmp` 校验 `legality.py` 与 `render_html.py` 字节还原）。

harness 自己也有一条**必须算红**的教训：`/tmp/mut_toolong.py` 第一跑报了 5 具 `SURVIVED`，看着像
断言没牙，实际是我把单条测试集写成了平铺的 list（`["tests/x.py", "-k", "…"]` 被当成三个测试集逐个
跑），pytest 每次都在 `pytest -k` 这种残缺命令上退出码 4、一行用例都没跑。**退出码非零被当成了红、
没跑起来被当成了绿**，两个方向都错。现在 harness 先识别 `no tests ran` / 缺文件这类输出并判
`INVALID-RUN — 不采信`，改完重跑才是上面那 8/8。

只报"绿"不算证明。

**等价变异**要单独处理：把"按座位过滤"改成"传整个日志"在这份金样本上**测不出差别**，因为
`build_belief` 自己会再挡一次（`belief.py` 里 `seer_result` 那条 `observer == actor`）。这种
情况不再追变异体，改成把断言钉在调用点上（`test_the_curve_is_folded_from_each_seat_view_not_the_loaded_log`
用一个 spy 记住 `build_belief` 实际收到了哪些 seq）。同理，`_pointer` 的"私有事件不点名"在
观众模式下够不到（事件早被过滤了），所以另有一条直接调 `_pointer` 的单位测试。

矩阵这一片新添了**两条**金样本本身够不到的断言，取证得换载体：整份 authored 转录 21 张票里
**0 张弃票**，所以"把 `弃票` 列整个删掉"在整局用例上是等价变异（第一跑实测 `SURVIVED`）；而
`_ballots` 按 `voters` 顺序落票、`voters` 就是座位升序，于是"把行重排成座位序"在真实日志上
改动为零。两条都改成直接调 `_matrix` 喂一手数据（一张含两张弃票的波、一张 7→2→9 的乱序波），
重跑才各自拿到具名红。

顺带修掉一种**误记**：`N7`（空格填占位符）在七连跑时报的是 `CAUGHT [hang after 60s]`，看着像
抓到了，单跑却是 12.7s + 一条具名红——那是七次连跑把套件拖过了 60s 的**负载超时**，不是这具
变异体的性质。harness 现在遇到超时先按 4× 上限重跑一次，仍然超时才记 hang；真空转（上面
"退出条件删掉"那条）照样会被记下来，只是要多过这一道。
