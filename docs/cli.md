# 命令行这一面：可粘贴的命令、离线演示、上桌与视图

这一本从 README 搬来，搬来的那几节一字未改（搬运那一趟记在 `docs/iterations.md` 的 `#352` 那一节）。
只有两句不是搬来的、是这一趟新写的：〈命令一览〉里那句讲「这一轮可以答什么」由谁给出，〈指标〉里那句
讲风格那两把尺子各住哪一座纯函数——它们新写是因为 README 瘦身时把那几枚代码名的出处摘走了，
窄形点名的那把尺得在新页上有住户。除那两句以外本页没有别的新内容。
README 只留给人照抄的那几条命令，这一本留全部：每一块都能原样粘进终端，`--mock` 与 `--dry-run` 那几条不发请求。
端点未标定时能跑通的都是离线演示；要真端点的那几条留在可粘贴区外面，并在原地写明它等的是谁。

## 命令一览：只有 `run` 和 `batch` 需要端点

| 命令 | 干什么 | 需要端点？ |
|---|---|---|
| `wolf run` | 打一局（`--games N` 从 `--seed` 递增；`--human 座位` 把那一席交给打字的人） | 默认需要；`--mock` / `--dry-run` 不需要 |
| `wolf replay <file>` | 复盘一个 JSONL | 否 |
| `wolf export <file>` | 落一个单文件 HTML 复盘 | 否 |
| `wolf watch <file>` | 终端直播（tail 那个还在被写的日志） | 否 |
| `wolf audit <file>` | 一局的质量与成本计数（JSON） | 否 |
| `wolf batch` | N 局 × K 配置的可配对批次（同一批 `deal_seed`） | 默认需要；`--mock` 不需要 |
| `wolf compare <dir>` | 两臂配对检验：要么出结论，要么写明为什么不出 | 否 |
| `wolf gate <dir>` | 对一目录**已经存在**的日志出 M3 闸门判定 | 否 |

表里只列了子命令。想知道某一个到底还有哪些旋钮、分别管什么，敲它的自述：
`wolf run --help`（把 `run` 换成表里任何一个都一样，`-h` 同义）——八个子命令各有一份，
有出厂默认值的那几枚连默认值一起印着，这些细节表格里放不下。
轮到某一席时"这一轮可以答什么"由 `rules.py` 里的 `legal_actions()` 给出，答的那一句落不落地由
`legality.py` 里的 `check_action()` 判，CLI 层不另写第三份。

```bash
wolf run --mock --seed 7 --out data --quiet     # 合成替身打牌，纯压状态机
wolf run --dry-run --games 3 --seed 21 --out data    # 装配全部 prompt 并落盘 + 每局成本清单，零 API 调用
# 这一条的 `--seed` 不是装饰：局号是「秒 + seed」，上面那条刚在 `data` 里写过 7 号，这一条若
# 沿用默认 seed，挨着敲就会撞上「路径用不了：… already holds a game log」。
# 真端点那一条留在可粘贴区外面：它要 `100.87.65.60:13000` 上那个判官，见上面〈配置：密钥的值永远不进文件〉。
# wolf run --seed 7 --games 1 --god             # --god 让打印的时间线含私有事件
wolf run --mock --seed 3 --max-days 2 --out data --quiet   # 压日数上限（R10）：打到上限即判平局
wolf run --mock --seed 7 --human 3 --out data/human   # 3 号席交给坐在终端前的人，其余八席仍是替身
wolf batch --out data/batch-demo/A-vs-B --configs A,B \
           --set B.temperature=0.6 --games 2 --seed0 1000 --mock
wolf compare data/batch-demo/A-vs-B --axis temperature   # 退出码：0 结论 / 1 拒绝 / 2 用法错
wolf gate data/batch-demo/A-vs-B/A                       # 同一批日志再判一次闸门：不重打牌
```

这一整块也是被**逐条执行过**的：`test_the_command_menu_lines_run_in_sequence` 把围栏里的每一条
单独交给 bash、落在同一个空目录里，要求每条有自己的退出码和自己的读数——`compare` 与 `gate` 读的
就是上面 `batch` 落下的目录，所以"这一节的读法是从上往下"也算在断言里。两格因此是有意为之的：
需要端点的那一条只能留在注释里（跑它们的是子进程，不在测试的无网夹具保护范围内，所以"块里没有
拨号的那一条"必须是执行**之前**的断言），而每条举例都得占自己的那个局号。这条规矩不再只由几块各自的
前置断言守着：`test_no_pasteable_block_in_the_manual_pages_dials_the_endpoint` 扫手册里每一块围栏。

`--mock` 的桌子**不是数据**：替身按剧本说话，能压崩状态机、压不出模型行为，所有指标都会把
`actor_kinds` 含 mock 的局标成 `synthetic`（`m1_win_rate` 直接把它们从分母里剔掉并说明）。
含真人座位（`--human`）的局另有一条：真人不受 seed 控制，永不进配对评测语料——两种桌子各引用
管自己的那一条，被剔掉的局在每个**产得出它**的出口里都会被点名（`batch` 摘要、`compare` 的拒绝语、
M1 与 M3 各自的 note 共四处，而批次接不到 `--human`，所以含真人的局只有后三处；见〈人怎么上桌〉）。
`--dry-run` 是预算工具，价值恰恰在于**物理上发不出请求**：它拿替身把每一局打到终局，再为每一局落
一份 `data/g<seed>.prompts.jsonl`，prompt 大小和区域分配就在那里面离线审。它**不只**落转储——同一
个 `--out` 里还多出那几局的合成局日志，局号照旧是「秒 + seed」（上面那条举例因此换了一个 seed）。
末尾还印一行"成本合计"（每局调用次数 / prompt token / 完成预算），40 局的墙钟因此只剩端点吞吐
一个未知数，见〈每局的墙钟乘数〉一节。

## 三分钟离线演示

```bash
LOG=$(wolf run --mock --seed 7 --out data --quiet | sed -n 's/.* -> //p')
# 摘要行的末尾就是刚写下的那一份：`[g00000007] wolf_win … 0.0s -> data/2026…_g00000007.jsonl`。
# 别用 `ls data/*.jsonl | tail -1` 挑：上面〈命令一览〉那条 `--dry-run` 也写 `--out data`，于是
# 同一个目录里住着两种 `*.jsonl`，而 `g…prompts.jsonl` 按名字永远排在时间戳后面——`tail -1` 选中的就是它。
echo "$LOG"

wolf replay "$LOG" --god | head -3      # 上帝视角：连私发发牌都看得见
wolf replay "$LOG" --seat 3 | head -3   # 3号的视图：看不到别人的身份
wolf export "$LOG"                      # 观众模式 HTML（不写 -o 就落在日志旁边）
wolf watch "$LOG" --once                # 打一帧终端画面就退出（截图/脚本用）
wolf audit "$LOG" | head -20            # 质量与成本计数（JSON）
# M7 的漂移自检要用到日志之外的拟合常数，所以那一条得显式递 sidecar。`data/calibration.json`
# 是 `scripts/calibrate.py` 体检真端点的产物，那一份是有意留在仓库里的（`data/` 里只有它入库）：
# wolf audit "$LOG" --calibration data/calibration.json | grep -A7 '"calibration"'
```

这一整块是被**执行过**的：`test_the_readme_demo_block_runs_verbatim` 把这一节围栏里的文本原样
交给 bash，先在旁边造好「同一个目录里两种 `*.jsonl`」的现场，再要求退出码为 0、且每条命令各自
印出自己那句读数。所以这一块里的命令不许依赖仓库之外的文件——上面那条被注释掉的 `--calibration`
就是因此从可粘贴区里挪出来的。

四个视图的差别**只在私有事件**，同一份日志、同一个渲染器：

```
[e1] 法官：开局座位 1、2、3、4、5、6、7、8、9号。
[e2] 法官（私发）：你的身份是 wolf，队友是 2号、4号。〔私有〕 ← 只有 --god 看得见
[e4] 法官（私发）：你的身份是 villager。〔私有〕  ← --seat 3 看得见这条（它自己的）
```

`watch` 的键盘：`g` 切上帝视角，`1`–`9` 看某一席的私有频道，`enter` 逐席往下走（9 之后回到 1，
演示者控速），`esc` 收回，`q` 退出。没有键盘可读时（stdin 是管道或 `/dev/null`）它不再读键、
只继续 tail，日志出现终局事件就退出——不然那是一台 100% CPU 的空转机。它**只读**日志，被 tail
的那个文件字节不变（`tests/test_render_live.py` 用 sha256 钉这条）。

## 人怎么上桌：`--human 座位`

上面那一局九席全是替身，那是 AI vs AI。加一个 `--human` 就把其中一席交给坐在终端前的人，其余
各席照旧（替身或真模型），那一席只是不经过端点：

```bash
wolf run --mock --seed 7 --human 3 --out data/human   # 3号是你，对手是替身，不碰端点
# 这一条不捕进变量：`HLOG=$(wolf run … --human 3)` 会把你该读的那一屏吞进 shell 变量里。
# 落盘的目录是 `data/human`，为的是和上一节那两个文件隔开——上一节的 `data/` 里同时住着
# 两种 `*.jsonl`，而 `g…prompts.jsonl` 按名字永远排在时间戳后面。
HLOG=$(ls data/human/*_g00000007.jsonl | tail -1)
wolf replay "$HLOG" --seat 3          # 打完了，从你那一席的视图重看一遍（最后一行说这一桌坐着真人）
# 把同一席交给真模型、其余八席也换成真模型，要端点，所以这条留在可粘贴区外面：
# wolf run --seed 7 --human 3
```

这一块和上面那三分钟那块一样，是被**整块执行过**的：`test_the_human_seat_block_runs_verbatim` 把它
原样交给 bash，stdin 里递三行答案进去，要求退出码为 0，并要求"打完的那一屏"和"重看的那一屏"
各自印出自己那句读数。所以块里不许留有会拨端点的命令行，`--human` 那一条也不许靠 `$()` 捕走——
你该读的那一屏会被吞进变量里。

轮到你的那一屏长这样（实测：`--mock --human 3 --seed 7`，中间九条略）：

```
轮到你了：3 号（villager）
局况（你看得见的最近 12 条）：
  [e18] 法官：天亮了，第1天开始。
  [e19] 3号：先听听
  ……（其余九条略）
这一轮可以答：
  票/投票 加座位号 后面可以跟你要说的话
  弃票/过/跳过 后面可以跟你要说的话
可点名的座位：1、2、4、5、6、7、8、9
你的选择 >
```

答的方式是**一行**：动作词开头，座位号紧贴着它，后面想说什么说什么。

```
投票 5号 他昨晚那一刀没有道理
观望 先听听 7 号怎么说
弃票
```

动作词不许藏在句子里：「我票了3号」会被拒收并**再问一遍**——把一句关于票的话读成一张谁都没投的
票，比读不出来贵得多。座位号认 `5`、`5号`、`五号` 三种写法。卡片上"这一轮可以答"列出的就是本轮
的合法动作；答了没列出的那个，投票和夜里那几轮会被**引擎替你答**（那一轮落盘的是引擎选的默认
动作，你那句话只留在同一格的 `attempts[]` 里），发言那一轮则**照你打的收**、不改你的动作。
被再问的那一屏顶上印一行
「法官打回：…」，说清是哪一个词没过闸门。Ctrl-D 是
"这个人离开了"而不是崩溃：那一席剩下的回合由引擎代打，日志里逐回合的 `result.fallback` 记得住。

这一局在读侧不会被当成普通的模型自玩局：页眉落的是 `actor_kinds=['human', 'mock']`，`replay`、
`watch`、`export` 三个出口各印一句「桌边坐着一个真人（`actor_kinds=['human', 'mock']`）：他答的
那几席不经过端点，这一屏不是九个模型自玩的那一局」，`compare` 与指标层则按 plan §十五 把这种局
挡在配对语料外面。一桌只有一个键盘，所以 `--human` 只给一个座位（给两个是命令写错，rc 2）；
`wolf batch` 的参数表里根本没有 `--human`，那种桌在批次侧构造不出来。

## 界面层三条规则

1. **一个渲染器两种模式**。哪些事件观众能看、一条发言带哪些标记、哪些轮次算"说了心里的
   话"、角色 id 叫什么中文名、这局结没结束——回答这几问的函数每枚只有一份定义；整份
   名册 12 枚，9 枚住在 `render_html.py`、3 枚住在 `events.py`（切票波与平安夜那两问归后者），
   消费者全部 import 而不是另写一份（`tests/test_wiring.py` 扫源码钉住，逐行对应关系见
   [docs/views.md](docs/views.md)）。
2. **观众模式挡的是两种泄漏，只有一种能被 `visibility` 看见**。私有事件（狼队私聊、验人
   结果）是字段级的；模型**自报的怀疑排序**装在一条公开发言的 payload 里，任何可见性字段都
   看不见它——只有渲染层不调用它才不泄漏。所以 `tests/test_render_html.py` 把 24 条自报文本
   逐条断言"不在观众文档里、在上帝文档里"。
3. **`身份公开` 由时间放行，不由模式放行**。终局事件一落，两种模式都印全桌身份——游戏已经
   结束了，没有剧透可损；在那之前谁都不能说出角色名（`game_over_event` 是唯一闸门）。

标记词表（`〔〕`，渲染层与 `audit` 口径一致）：`〔拒绝N次〕` 闸门打回过、`〔不可能感知〕`
对只有公开事件可见的窗口做了第一人称感知断言、`〔发言超长〕`、`〔引擎代打〕`。页脚固定一句
**本局不可复现**：这个端点没有确定性，seed 只决定发牌和座位序，不决定输出。

## 指标

`wolf audit <file>` 输出 `m2`–`m8` 七组指标，全部是日志的纯函数。M1 的分母是**一批**局，
单个文件给不出，所以它出现在 `wolf compare` 的 `comparison.md`（`## 胜负与存活`）而不是
`audit` 里；M3★ 的五条预注册判据同理，`compare` 按臂各给一句
`PASS / FAIL / NOT_EVALUABLE`（`## M3 闸门`），缺读数的判据不给通过、测出来的失败不被缺读数
盖掉。M7 的漂移自检要用到日志之外的拟合常数，所以它是 `audit` 里唯一一处**要显式递进去**的外部
输入：`wolf audit "$LOG" --calibration data/calibration.json`，不给就不碰文件系统，给了就把读到
的出处（路径、model、实测时间）和"为什么没用上"一起印在 `calibration` 键里。风格那两把尺子在
`metrics.py` 里各有一座纯函数：一轮发言像不像照抄模板走 `collapse_round()`，开场是不是各说各的走
`opening_distinct_rate()`，读侧不许再算第二遍。口径、分母、以及
"哪些数字现在还测不出来"写在
**[docs/metrics.md](docs/metrics.md)**。渲染层的详细取舍在
**[docs/views.md](docs/views.md)**，延迟常数的唯一合法来源在
[docs/calibration.md](docs/calibration.md)。

代码与文档里那些 `plan §N` 的编号指向引擎之外的一份规划笔记，它不随仓库发布，所以在 GitHub 上点不开。
它们回答的是"这一格的口径是谁定的"，要核对仍然得回到代码、测试与上面那几本页——那三样才是发布出去的东西。
