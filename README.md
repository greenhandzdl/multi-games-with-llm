# 多智能体博弈数据引擎（狼人杀）

九个 LLM 座位自己打一局中文狼人杀，引擎判定一切合法动作、把每个决策落成一条 append-only
JSONL 事件，然后离线复盘给其他人看。**产品形态是数据引擎，不是聊天机器人**：日志是唯一真相
源，指标和界面都只是它的纯函数——所以端点明天被人重启、换权重、下线，演示照样能跑。

引擎拥有真值（发牌、合法性、结算、胜负），模型只拥有语言（发言文本、它自报的怀疑排序）。
这条分工决定了本仓库几乎所有代码形状。

## 安装

```bash
uv sync --extra dev          # 或 pip install -e ".[dev]"
```

需要 Python ≥ 3.12。运行期依赖只有 `httpx` / `pydantic` / `rich`，统计层用 `math.comb` 不引
scipy。

## 配置：密钥的值永远不进文件

```bash
export WOLF_LLM_API_KEY=<key>      # 只在 shell 里
```

`WOLF_LLM_BASE_URL`、`WOLF_LLM_MODEL` 非密，可以直接提交（`.env.example` 就是样例）。代码里
只出现**环境变量名**：`Config.api_key_env = "WOLF_LLM_API_KEY"`，transport 在发请求那一刻才读
`os.environ`。写盘的 `request` / `response` 字段走白名单，`headers` 连键名都不落盘——这条主张
由 `tests/test_no_secrets.py` 扫 `src/`、`tests/fixtures/`、`docs/` 和一份新生成的日志来兜底。
查提交历史里有没有混进过 key（本仓库目前**还没有任何提交**，这两条要等第一次 `git commit` 之后
才有对象可查；工作树的扫描已经在测试里了）：

```bash
git log -S'WOLF_LLM_API_KEY' --oneline      # 谁动过这个键
git log -p | rg -n 'sk-[A-Za-z0-9]{20,}'    # 值本身
```

## 七个动词，其中四个不碰端点

| 命令 | 干什么 | 需要端点？ |
|---|---|---|
| `wolf run` | 打一局（`--games N` 从 `--seed` 递增） | 默认需要；`--mock` / `--dry-run` 不需要 |
| `wolf replay <file>` | 复盘一个 JSONL | 否 |
| `wolf export <file>` | 落一个单文件 HTML 复盘 | 否 |
| `wolf watch <file>` | 终端直播（tail 那个还在被写的日志） | 否 |
| `wolf audit <file>` | 一局的质量与成本计数（JSON） | 否 |
| `wolf batch` | N 局 × K 配置的可配对批次（同一批 `deal_seed`） | 默认需要；`--mock` 不需要 |
| `wolf compare <dir>` | 两臂配对检验：要么出结论，要么写明为什么不出 | 否 |

```bash
wolf run --mock --seed 7 --out data --quiet     # 合成替身打牌，纯压状态机
wolf run --dry-run --games 3 --out data         # 装配全部 prompt 并落盘 + 每局成本清单，零 API 调用
wolf run --seed 7 --games 1 --god               # 真端点；--god 让打印的时间线含私有事件
wolf run --mock --seed 3 --max-days 2 --out data --quiet   # 压日数上限（R10）：打到上限即判平局
wolf batch --out data/batch-demo/A-vs-B --configs A,B \
           --set B.temperature=0.6 --games 2 --seed0 1000 --mock
wolf compare data/batch-demo/A-vs-B --axis temperature   # 退出码：0 结论 / 1 拒绝 / 2 用法错
```

`--mock` 的桌子**不是数据**：替身按剧本说话，能压崩状态机、压不出模型行为，所有指标都会把
`actor_kinds` 含 mock 的局标成 `synthetic`（`m1_win_rate` 直接把它们从分母里剔掉并说明）。
`--dry-run` 落的是 `data/g<seed>.prompts.jsonl`，那是预算工具——它的价值恰恰在于**物理上发不出
请求**，所以 prompt 大小和区域分配可以离线审。它末尾还印一行"成本合计"（每局调用次数 / prompt
token / 完成预算），40 局的墙钟因此只剩端点吞吐一个未知数，见〈每局的墙钟乘数〉一节。

## 三分钟离线演示

```bash
wolf run --mock --seed 7 --out data --quiet
# [g00000007] wolf_win winner=wolf day=4 events=104 fallback=0 ... -> data/..._g00000007.jsonl
LOG=$(ls data/*.jsonl | tail -1)

wolf replay "$LOG" --god | head -3      # 上帝视角：连私发发牌都看得见
wolf replay "$LOG" --seat 3 | head -3   # 3号的视图：看不到别人的身份
wolf export "$LOG"                      # 观众模式 HTML（不写 -o 就落在日志旁边）
wolf watch "$LOG" --once                # 打一帧终端画面就退出（截图/脚本用）
wolf audit "$LOG" | head -20
wolf audit "$LOG" --calibration data/calibration.json | grep -A7 '"calibration"'
#   顺带跑 M7 的漂移自检；这份 sidecar 是跑断在半路的那次体检，所以它印的是"为什么用不上"
```

四个视图的差别**只在私有事件**，同一份日志、同一个渲染器：

```
[e1] 法官：开局座位 1、2、3、4、5、6、7、8、9号。
[e2] 法官（私发）：你的身份是 wolf。〔私有〕      ← 只有 --god 看得见
[e4] 法官（私发）：你的身份是 villager。〔私有〕  ← --seat 3 看得见这条（它自己的）
```

`watch` 的键盘：`g` 切上帝视角，`1`–`9` 看某一席的私有频道，`enter` 逐席往下走（9 之后回到 1，
演示者控速），`esc` 收回，`q` 退出。没有键盘可读时（stdin 是管道或 `/dev/null`）它不再读键、
只继续 tail，日志出现终局事件就退出——不然那是一台 100% CPU 的空转机。它**只读**日志，被 tail
的那个文件字节不变（`tests/test_render_live.py` 用 sha256 钉这条）。

## 界面层三条规则

1. **一个渲染器两种模式**。哪些事件观众能看、一条发言带哪些标记、哪些轮次算"说了心里的
   话"、角色 id 叫什么中文名、这局结没结束——这五句话背后是八个函数名，每个只有一份定义
   （七个在 `render_html.py`，切票波的 `voting_waves` 在 `events.py` 供复盘与指标同读），
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
的出处（路径、model、实测时间）和"为什么没用上"一起印在 `calibration` 键里。口径、分母、以及
"哪些数字现在还测不出来"写在
**[docs/metrics.md](docs/metrics.md)**。渲染层的详细取舍在
**[docs/views.md](docs/views.md)**，延迟常数的唯一合法来源在
[docs/calibration.md](docs/calibration.md)。

## 测试

```bash
.venv/bin/pytest                 # 791 passed in 46.39s（14:58:40Z），全程离线；上一跑 779/48.59s（14:24:19Z）
.venv/bin/pytest -k render       # 只跑两个渲染器
```

系统 `python3` 里没有 pytest，用 `.venv/bin/pytest`。套件的总数是要重数的：数
`.venv/bin/pytest --collect-only` 末行的 `N tests collected`，08:06:23Z 数到 **704**、09:00:43Z 数到
**709**（中间那五格全是 `#61` 这一族在 `tests/test_doc_citations.py` 里补的）、09:36:24Z 数到 **724**
（`#62` 的 15 格）、09:54:07Z 数到 **741**（`#64` 的 17 格：`tests/test_report_stats.py` 三只用例名
展开成 16 格，加 `tests/test_batch_paired.py` 里走 `compare` 的那一只）、10:09:25Z 数到 **742**
（`#64` 收尾那条按名字的守卫，`tests/test_purity.py` 23→24）、10:41:33Z 数到 **754**（`#65` 全在一个新文件里：`tests/test_anti_repeat.py` 12 条，其中两条是 10:34:53Z 那两具 SURVIVED
之后补的）、10:54:19Z 数到 **755**（`#66` 的一条读数用例，落点是 `tests/test_m3_gate.py`）、
11:51:54Z 数到 **759**（`#63`/`#67` 两片：C2 那把尺子现在单独下得动刀，刀落了几条也落进了日志）、
12:40:56Z 数到 **763**（`#68` 一片：法官指派了什么，终于有了读它的那一格——`tests/test_m3_gate.py` 三条、
`tests/test_cli.py` 一条，四个名字钉的是同一格 `request.assigned_act`）、13:00:19Z 数到 **767**（`#69`
那四条全在同一个新方向上：写侧的幂等守卫，两个失败方向各要有证人）、13:30:20Z 数到 **775**（`#70`
那八格也在一根链条上：白名单放行摊平后的那一格、聚合算术四格、机器出口两格）、13:40:38Z 数到
**776**（`#70` 的电池量出来的那一格：分母那句话原本写了两份、谁都没读，现在是一处实现加一个绝对值）、14:06:11Z 数到 **777**（`#72` 那格：行号闸门的窗口从 ±2 收成被点名的那一行，多出来的那条断言是变异体 E2 先活下来、之后要回来的）、14:24:19Z 数到 **779**（`#71` 那两格：体检脚本里第二份 `cached_tokens` 读法收进 `usage_from()`，一条管行为一条管"只有一个读取点"，后者由变异 M2 单独证明它不是重复）、14:59:14Z 数到 **791**（`#73` 那十二格落在四个新文件里：`tests/test_vote_wave.py` 4 条、`tests/test_night_guards.py` 4 条、`tests/test_last_words.py` 2 条、`tests/test_house_wired.py` 2 条；这一片没改产品代码，改的是"哪句散文有读者"）；
墙钟不是账：同一份 767 在 12:55:31Z 那跑 47.04s、13:06:15Z 那跑 114.47s，差的是机器负载（套件里有两条
在真实时间里等完退避），所以末行那个秒数只用来判断"跑完了没有"，不用来比快慢；
不要再往这行加 `-q`
——`pyproject.toml` 的 addopts 已经带了一个 `-q`，两个 `-q` 会把末行本身吃掉（09:01:45Z 那次就是这么
把 `709 passed in 49.95s` 弄没的，只能从进度行的百分比反推）。
"哪个文件有几条"那一类主张由 `test_a_case_count_written_next_to_a_module_name_matches_that_module`
自己核（这一轮它就抓红了 `tests/test_cli.py` 的三处旧数），套件总数那一格仍然只能靠上面这句"要重数"。
分布：日志读不下去的用例住在 `tests/test_log_recovery.py`（现 46 条，
其中四条是参数化的——三条各 4 个参数、一条 3 个，跑起来 57 个用例）；
钉"末行只宽一行"的和钉"manifest 只有一个读者"的住在
`test_wiring.py`。`#57` 又新写了一个文件 `tests/test_loopback_endpoint.py`（四条，全仓库只有它
和 `tests/test_calibrate_rehearsal.py` 真的开 socket，开的是 127.0.0.1 上自己起的桩）；`#58`–`#60`
那几格（`--games` 地板的参数化与空普查的退出码、越界的 `--seat`、要一块不存在的板子的 `--set`、
只有开局记录的文件上的座位视图、`--god` 与 `--seat` 同时给时的次序）住在 `tests/test_cli.py`
（现 52 条、跑起来 61 个用例，三条各参数化为 4/6/2 个）。往上数这条链的账：691 是
2026-09-22T06:02:58Z 数的、701 是 06:49:10Z、704 是 08:06:23Z、759 是 11:51:54Z（`#63`/`#67`
把 C2 那把尺子和刀的读数接进日志之后），前几格都被后续新增的用例顶掉了，留着
是因为**它们各自是那一刻的账**，不是抄来的。
这一整套**一条都不发给那台私有端点**（渲染器
测试会 monkeypatch 掉 transport 并在被调用时直接失败）；上面那两个文件发字节，发的是
**127.0.0.1 上自己起的桩**——桩回 401、或者回 200 但内容不合法，为的是让真 `cli.main` 在子进程里
跑到"端点说不之后"的那一步，`#57` 那个缺陷只存在于进程退出码这一层，进程内调用抓不到它。
它们钉的都是"端点说不之后我这侧怎么记、怎么退"，没有一个常数是从桩上取的。
`network` 这个 marker 已经注册，是给 M2/M3 真端点测试预留的，目前还没有测试挂它。

套件里有两条在真实时间里等完了 `1.5 + 3.0` 两次退避：`test_live_path.py` 的端点不可用局（socket
直接被拒）和它的 401 姊妹局（端点答了，答的是"拒绝你"）。只有前者把等待本身写成了断言
（`took >= 4.4`），所以它仍是全仓库唯一能分辨"真的睡了"和"默认值被换成了空操作"的一条——其余重试
测试都注入记录器、只断言**退避数值**，那些绿用例对 `sleep` 被换成空操作毫无感知，而零延迟重试风暴
恰好就是把一个抖动端点变成九十次拒答的东西。（另有几条为了制造"慢"这个前提而 `asyncio.sleep`，比如
`test_actor_contract.py` 那两条 50ms，但它们不断言耗时——断言耗时只会把机器负载变成红用例。）

文档点名的用例也在扫描范围内：`docs/*.md` 和 `README.md` 用 `` `test_名字` `` 的形式指认"是哪条
用例钉住这个断言"，`tests/test_doc_citations.py` 把这些串对照 `tests/` 的 AST 函数名，点不到就
红。补这个闸门时抓到的第一条就出在文档自己身上：`docs/comparison.md` 点名
`test_each_arm_gets_its_own_gate_verdict_rather_than_one_shared_answer` 时被截掉了一半，读者按截
短的样子复核会点空。截短的那个串本身不能写进这份文档——闸门连 README 一起扫，它同样算一次点空；
占位符同理，ASCII 的 `test_` 后面跟一串字母就被当成一次引用，所以写成 `test_名字` 这种非 ASCII、
正则接不住的形式。

同一个文件还扫第二类"读者会照抄的东西"：**文档里印出来的命令**。围栏代码块和行内代码串里以
`wolf ` 开头的片段被取出参数，逐个对照 `cli.build_parser()` 活对象上的 `option_strings`——这个
函数是这一片顺手从 `main()` 里拆出来的，拆之前想知道"batch 认哪些参数"只能按引号扫 `cli.py`
源码，那是典型的第二处实现。散文不参与：`metrics.md` 有一句"`wolf run` 没有 `--set`"，按行扫
`wolf` 这个词会把它读成一条命令、报出一个并不存在的过期参数。这种**反向主张**由一张表钉住
（`test_the_negative_flag_claims_in_the_docs_are_negatives`），因为"某参数不存在"没法从引用里扫出来。

一条只写"assert 没有过期引用"的守卫分不清"文档干净"和"扫描器坏了"，所以四类引用各配一条喂假
数据的对照用例（`_stale` / `_stale_flags` / `_stale_values` 都是纯函数，语料由调用方给）和一条规模
断言（扫到的文档数 / 引用数 / 命中率 / 子命令数）。三十四座变异照 `/tmp/mut_docs.py`、
`/tmp/mut_cmd.py`、`/tmp/mut_counts.py` 与 `/tmp/mut_values.py`（前三轮 7/7 + 7/7 + 7/7 CAUGHT；
出厂值那一轮 2026-09-21T23:32Z 重跑过一遍（对着最终字节），13/13 全部按预期、0 次无效运行，
`docs/comparison.md` 与 `tests/test_doc_citations.py` 按字节还原），红用例具名如下——括号里是"只有这一条能看见它"：

| 变异 | 红用例 |
| --- | --- |
| D1 名字集只收同步 `def`，不收协程 | `test_every_test_named_in_the_docs_resolves`（误报 `test_live_path` 里的真用例） |
| D2 `_stale` 改瞎：坏名字不再进结果 | `test_a_renamed_case_is_reported_rather_than_waved_through`（只有这条，主断言空转） |
| D3 文档范围 glob 打错，扫不到文件 | `test_the_guard_itself_can_fail`（同上） |
| D4 测试文件 glob 打错，名字集为空 | 三条全红 |
| D5 引用正则在第一个下划线前就停 | 三条全红 |
| D6 不再收测试模块名（裸 `test_wiring` 成过期） | `test_every_test_named_in_the_docs_resolves` + 规模 |
| D7 引用正则退化成永远匹配不上 | 对照 + 规模（主断言空转变绿） |
| D8 管道之后的参数不再切掉 | `test_every_flag_the_docs_show_is_offered_by_that_subcommand` + 对照（README 那条 `grep -A7` 被算成 audit 的参数） |
| D9 `#` 注释不再切掉 | 对照（真实语料此刻没有注释带参数，只有合成探针看得见它） |
| D10 `_stale_flags` 改瞎 | 对照 |
| D11 围栏只取首行，续行丢光 | 对照 + 规模 |
| D12 行内代码串那一层整个丢掉 | 对照 |
| D13 反向主张指错子命令 | `test_the_negative_flag_claims_in_the_docs_are_negatives` |
| D14 真 parser 改掉 `--calibration` 的名字 | `test_every_flag_the_docs_show_is_offered_by_that_subcommand` + 对照（证明文档↔parser 真的接上了） |
| D15 计数落点判据整个拿掉（什么"N 条"都算用例数） | `test_a_case_count_written_next_to_a_module_name_matches_that_module`（误伤 `24 条自报文本`）+ 对照 |
| D16 `_case_claims` 改瞎 | 主断言（规模那条）+ 对照 |
| D17 枚举只认 `+` 左边那个值 | 对照（真实语料仍全对，只有合成探针看得见少了一半） |
| D18 总数与枚举的对账改瞎 | `test_a_stated_total_has_to_add_up_to_its_own_enumeration` |
| D19 **文档**把 15 条写成 16 条（代码一个字不动） | `test_a_case_count_written_next_to_a_module_name_matches_that_module` |
| D20 落点判据丢掉行末一支 | 对照（探针里以"里 15 条"收尾的那句不再算） |
| D21 落点判据取反（后面是词才算） | 主断言 + 对照 |
| D22 出厂值主张丢掉闭合反引号（`temperature=0.7` 被读成 0） | `test_the_value_scanner_reads_claims_and_not_every_equal_sign` |
| D23 两个锚点都换成 `\b`（围栏里那条 `--set A.regions.c_total=250` 成了主张） | 主断言 + 对照（真实语料当场多出 `calibration.md` / `comparison.md` 两处假引用） |
| D24 键不存在那一支不再报 | 对照（只有它钉得住） |
| D25 数不符那一支永不报 | 对照 |
| D26 `_src_int_literals` 丢掉 dataclass 字段那一支 | 主断言（红在"核到字面量"那道地板：只剩 1 处）+ 对照 |
| D27 位置参数默认值不再右对齐 | 对照（`min_len` 的 6 记到了签名第一个参数头上） |
| D28 `_src_names` 丢掉"字符串键"那一支 | 对照（日志字段被误报成点空） |
| D31 把"核到字面量"的地板从 8 抬到 99 | 主断言（证明那道地板是活的） |
| D32 扫描范围缩到 `docs/`（README 不再算语料） | 主断言（红在"至少 10 处"那道地板）+ 另两条共享 DOCS 的守卫 |
| D33 **文档**把 `max_days` 的出厂值写成一个更早的合法值 4（代码一个字不动） | 主断言 |
| D34 `_src_int_literals` 丢掉模块级赋值那一支 | 对照 |
| D35 收名字时不再统一大小写（`SPEECH_SOFT_LIMIT` 对不上小写主张） | 对照 |
| D36 `_src_names` 只收 `ast.Name`（丢掉参数名）——**预期活下来** | 见下面那段：它防的是误报，不是漏报 |

重跑这一片：`PYTHONPATH=src .venv/bin/pytest tests/test_doc_citations.py`（21 条、跑起来 21 个用例，
两秒内，不发请求）。D1 不是凭空设计的——第一次写这个闸门时确实只走了 `ast.FunctionDef`，于是把
`test_a_marker_is_public_but_never_becomes_chronicle`（`test_live_path.py` 里的协程）误报成了
文档过期。**误报也是这个闸门的输出**，所以它的红用例名要留在这里。

D15–D21 数的是第三类可核对的主张：**"某个测试文件有几条用例"**。它抓到的第一条红不在代码里，在
文档自己身上——`README.md` 里 `pytest tests/test_calibrate_rehearsal.py` 后面那句注释写的比实际
少一条（那个文件在 `#38` 那轮长了读侧对账，注释没跟着数）。同类腐烂还有一处顺手清掉：同一段里
"`test_live_path.py` 的协程"原来带着一个行号，而行号早就漂了——删掉行号只留用例名，名字由
`test_every_test_named_in_the_docs_resolves` 钉，行号没人钉，留着就是一句迟早假掉的话。

这三处红里有一处值得单说：闸门把**引用**也当成了主张。上面这两段本来是把写错的旧句子贴出来给
读者看错处长什么样，而扫描器分不清"我在转述一句错话"和"我在说一句对话"——它是对的，转述里的数
同样会被读者照抄。所以现在错的样子只有描述，没有代码串：这一条规矩在这个文件里不是新加的，
`tests/test_doc_citations.py` 自己的 docstring 倒数第二段就写着它，只是这轮换我自己撞上去才算真
的被执行过。

计数的判据有两道闸：数必须**绑在模块名上**（同一行那句套件总数不绑，它归"改测试后要重数"那句人看
的规矩），而 `条` 后面不能再接词（`24 条自报文本`、`22 条渲染行` 数的是转录行）。第二道闸最初是一
张白名单字符表 `[用例，）、。：\s]`，一具"预期活下来"的变异（删掉表里的 `用`）当场活了：`条用例`
那种写法里模块名在计数**后面**，白名单收它收得没有一条断言读得到。换成否定式（`(?=\W|$)`）之后每
一支都有人读——D20 丢掉行末那一支会红，因为探针文件里专门写了一句：模块名在前、计数紧跟，整行到
"15 条"就收住，后面一个字也没有。

这条闸门的边界也写在这儿：它只对**没有参数化**的模块成立。`test_purity.py` 收集到 23 条而模块级
`def test_*` 只有 7 个，所以文档一旦给它写条数就会红——那不是误伤，是在说"这个数用离线自足的口径
核不出来"（对照 `--collect-only` 要起子进程，而这个文件的取舍是不起）。套件总数同理不在核对范围内，
它仍然靠"改测试后要重数"那句人看的规矩。

第四类主张是**出厂值**：`` `max_days=6` `` 这种"反引号整住 key=整数"的写法，说的就是 `src/` 里那个
键的那个字面量。取数走 AST，三种绑法都收——dataclass 字段与模块级常量、关键字参数默认值、位置参数
默认值（默认值从签名右侧对齐；D27 那具"不对齐"的变异把 `min_len` 的 6 记到了签名第一个参数头上，
被 `test_the_value_scanner_reads_claims_and_not_every_equal_sign` 逮住）。判据只认反引号整住的那一
种形状，`>=`、小数、大写、过短的键名都不算。D23 证明这不是洁癖：锚点一松，围栏里那条压覆盖值的
命令就被读成一句出厂值主张，`calibration.md` 与 `comparison.md` 当场各多出一处假引用。

于是写文档多了一条规矩：**裸的 `key=数字` 只用来报出厂值**。要提实验臂压到的那个数，把键名和数字
分开写——`docs/metrics.md` 里那两处"臂值写成 key=数"的句子这轮改成了"`A.regions.c_total` 压到 250
的那一臂"。这不是扫描器不会聪明，是它拒绝猜：同一个 `c_total` 在代码里是 1450、在某一臂里是 250，
两句都是真话，而 `key=数` 这个形状里没有哪个上下文能让机器分辨"这句说的哪一个"。写清的人比猜的人
便宜，而猜错一次的代价是读者照抄一个不存在的配置。

另一种"核不动但也不报错"的是**日志字段**：像 `fallback` 这类名字在代码里确实存在（作为字典键的字
符串常量），值却是运行时算出来的，AST 里没有对应的整数字面量——扫描器认它"存在"，因此不报点空，同
时不去核数。"存在"比"值"宽是故意的：一个从 `src/` 里**消失**的名字必须红（那正是改字段名会留下的
洞），而一个只是没有字面量可对的键不该红。上一段那两处 `metrics.md` 的臂值，就是这条规矩落地时改的。

`_src_names` 收的是**能被赋值的名字**：赋值目标、参数名、被当键用的字符串常量。属性名和调用点的
实参名不收——那是使用处，不携带任何值绑定。这一轮为此删过两支：属性名与关键字实参名各能多收进
170 / 31 个名字，但**没有任何一条断言读得到它们**，两具"删掉那一支"的变异都活了，于是那两支当场
删掉，跟上一轮白名单里那个没人读的 `用` 同一个处置。留着的字符串键那一支不一样：`src/` 里有 282
个名字只以字符串键的形式存在，探针里专门挑了一个（`b2_worst_over_tokens`）来读它，删掉那一支它就
被误报成点空。而 `fallback` 读不到它——`fallback` 同时也是个变量名，所以第一轮那具"删掉字符串键
分支"的变异活了，看着像"这一支没用"，其实是探针挑错了样本：换一个**只**活在字符串键里的名字，同一
支立刻有人读。

D36（只收 `ast.Name`、丢掉参数名）也活着，但活的原因不同：`src/` 今天每个参数恰好也在函数体里被当
名字引用过，两条路都到得了。参数名那一支还是留着，因为文档引用的默认值本来就多半是参数（`min_window=4`
就是），少收一类绑定处会在下一个"只出现在签名里"的参数上误报。同样是"变异活了"，**一种说明那支不携
带信息，另一种只说明它被另一支顺带覆盖了**——只有前者该删。

还有一类守卫不在文档里，管的是**事件类型的拼写**：`tests/test_wiring.py` 把 `src/wolfengine` 下的
每个 `.py` 和 `tests/*.py` 过一遍 AST，找出"把事件 kind 和裸字符串比较"的位置。分两档，因为两种写法
含义不同——`e.kind == "…"` 比的是装载后的 `Event`，字面量等于给一个已声明的事实发明第二个名字，
一律禁止；`rec["kind"] == "…"` 比的是 `json.loads` 出来的字典，读原始 JSONL 的那条用例是**故意按
文件读**、好让它不和 `metrics` 共享实现，所以那里的字面量是线上契约，允许写，但必须是 `Kind` 里
声明过的名字（这样重命名事件类型时会响，而不是让过滤器静默变成永真或永假）。走 AST 不走正则：正则会把
守卫自己的源码行标红，`kind` 加比较符那个模式就写在这个文件的文本里。

把禁令从 src/ 扩到 tests/ 当场抓出五处（`test_prefix_stability.py` 三处折叠、`test_render_html.py`
与 `test_soak.py` 各一处终局）。理由是后果不对称：src/ 里写错字面量的后果是一局坏游戏，tests/ 里
写错的后果是**一条通过的测试**。而 `Kind.COMPACTION` 的取值就是 `"compaction"`，所以改完行为不变、
套件照绿——要钉住的正是这种"绿"，K1 就是把一处语料退回字面量。

| 变异 | 红用例（红在哪条断言上） |
| --- | --- |
| K1 一处已改好的语料退回字面量 | `test_no_module_compares_an_event_kind_to_a_bare_string`（违规清单；那条行为测试照常绿） |
| K2 守卫不再扫 `tests/` | 同上，但红在**规模断言**：范围缩回 src/ 一个违规都不留，只留下一个空扫描器 |
| K3 原始形式不再被收集 | 两条：主断言红在规模，`test_the_kind_guard_sees_both_tiers_on_a_synthetic_file` 红在原始档没收到东西 |
| K4 第二档"未声明"判定改瞎 | 只有对照那条（真实语料是干净的） |
| K5 第一档收集改瞎 | 同上 |

K4 第一版**活了下来**：判定写在守卫体内，对照用例于是自己复算了一遍 `[… for s in raw if s[2] not
in KINDS]`，两遍实现里被改瞎的那遍恰好没人调用。把规则抽成 `_undeclared_kinds`、守卫和对照用例都
调它之后 5/5 CAUGHT。**对照用例必须调用规则，不能复算规则**——这句现在写在 `_undeclared_kinds` 的
docstring 里，因为它是这条规则的来路。

重跑这一片（2 条，一秒内，不发请求）：

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_wiring.py -k "kind_to_a_bare or kind_guard_sees_both"
```

变异照 `/tmp/mut_kind.py`（2026-09-21 对最终树跑过一轮，5/5 CAUGHT，两个被改文件按字节还原）。

同一轮里还补了一片**已实现但 0 覆盖**的东西：plan §15 要求上桌前必须从编排层清掉的三条隐含假设
——超时按 actor 取而不是按阶段、并发度表要能容忍"某座位阻塞"、墙钟不能杀含真人座位的局——代码里
三条都在（`agent.py` 的 `if limit is None`、`phases.py` 的 `wave_size`、`game.py` 的
`enforce_clock`），仓库里一条测试都没有。这个缺口比通常的缺测试更贵：`HumanActor.act()` 是一期
刻意留的 `NotImplementedError`，所以真出事的时候没有替身能复现，只能等人坐进去。
`tests/test_actor_contract.py` 用同一个 `_Seat` 把四个旋钮（`kind` / `blocking` / 截止时间 /
答题耗时）各自独立地拧给三条规则看。8 具变异照 `/tmp/mut_contract.py`（2026-09-21，8/8 CAUGHT，
三个被改文件按字节还原）：

| 变异 | 红用例 |
| --- | --- |
| A1 给 `None` 顺手兜个地板（`or cfg.llm_timeout_floor_s`） | `test_a_seat_that_declares_no_deadline_is_not_given_one_by_the_floor` |
| A2 截止时间改回按配置取 | 同上 |
| B1 `wave_size` 不再剔 `blocking` 座位 | `test_the_seat_being_waited_on_is_not_a_worker_slot` + 八座阻塞那条 |
| B2 允许给出 0 个 worker 名额 | `test_the_seat_being_waited_on_is_not_a_worker_slot`（`Semaphore(0)` 会把那一波锁死） |
| B3 忽略前缀降档表 | `test_the_wave_is_capped_by_the_prefix_ladder_not_by_the_seat_count` |
| C1 墙钟无条件生效 | `test_one_seat_of_flesh_takes_the_wallclock_off_the_game` |
| C2 墙钟整个失效 | `test_the_wallclock_does_kill_a_table_of_nothing_but_models`（正向对照：没有它，上一条分不清规则和死代码） |
| C3 `all`→`any`：一个模型座位就够格杀局 | `test_one_seat_of_flesh_takes_the_wallclock_off_the_game` |

A 组有个**等价变异**要写清楚：删掉 `if limit is None` 那一支测不出来，因为
`asyncio.wait_for(coro, None)` 本来就是无限等。这一条不为分支存在作证，只为"截止时间不从 `Config`
里来"作证——所以能抓住的恰好是最自然的那种回归写法（A1）。

重跑：`.venv/bin/pytest tests/test_actor_contract.py`（9 条，半秒内，不发请求）。

同一条找缺口的方法（"public 函数里没有一个测试点过名的"）还带出一片统计：`metrics.wilson_ci` 是
胜率区间的实现，之前**唯一**的数值锚点是金样本那条 `wilson95 == [0.207, 1.0]`——一个点，而且挂在
一条名字讲的是"替身桌不计分"的用例里（所以公式一坏，红的是那条名字不相干的用例，谁都会被误导去
查剔除逻辑）。`tests/test_report_stats.py` 现在给它三个自己的锚：7 组 `(k,n)` 各自对**另一条独立
路径**（对 score 统计量二分反解，不复用那条闭式解）比到 1e-9、一条同时钉住"正态近似确实越界"这个
前提的区间外性质、一条钉 `n=0`。

这一片值得记的是**我最初写的理由是错的**。我当时的论点是"那个点落在 `p̂=1`，`p(1-p)=0` 把根号项
整个清零，所以闭式解里一半的算术没被看过"——跑 W1–W5 时它们**全部**被旧锚点抓住了，因为
`half = z·√(z²/4n²) = z²/2n` 并不是 0。真正测不出来的是只错在 `p(1-p)/n` 这一项的实现：角上该项
本来就是 0，`wilson_ci(1,1)` 与错误实现在**六位小数上完全相同**（实测 `0.206549, 1.0`），只有内点
分得开。W6 就是这么一具变异，跑出来 golden 那栏 `SURVIVED`、新用例那栏 `CAUGHT`，`_wilson_by_inversion`
的 docstring 现在写的是这个版本。**跑变异不只是为了证明测试能红，也是为了给写在文档里的那句理由
做证**——这轮如果
只跑到 8/8 CAUGHT 就收工，README 里会留下一句错的论证。

| 变异（`src/wolfengine/metrics.py`） | 新用例 | 旧锚点 `test_m1_refuses_to_score_a_stand_in_table` |
| --- | --- | --- |
| W1 丢掉 `+z²/2n` | CAUGHT（7 组 + 区间外那条） | CAUGHT |
| W2 分母用 `z` 不用 `z²` | CAUGHT（同上） | CAUGHT |
| W3 根号里丢掉 `z²/4n²` | CAUGHT（7 组） | CAUGHT |
| W4 根号里忘记除 `n` | CAUGHT（7 组 + 区间外） | CAUGHT |
| W5 下界夹逼方向写反 | CAUGHT（6 组 + 区间外） | CAUGHT |
| **W6 根号里只留 `z²/4n²`（漏掉 `p(1-p)/n`）** | **CAUGHT（5 组内点）** | **SURVIVED** |
| W7 整式退回正态近似（Wald） | CAUGHT（7 组 + 区间外） | CAUGHT |
| W8 空分母给成一个点 | CAUGHT（只有 `n=0` 那条） | SURVIVED |
| W9 置信水平 95% → 90% | CAUGHT（7 组） | CAUGHT |

9 具照 `/tmp/mut_wilson.py`（2026-09-21，9/9 CAUGHT，`metrics.py` 按字节还原）。已知等价变异：
把 `min(1.0, …)` 那个上界夹逼整个删掉——Wilson 区间按构造不超过 1，那条 clamp 在正确实现上是恒等
的，测不出差别，所以不列入。重跑：`PYTHONPATH=src .venv/bin/pytest tests/test_report_stats.py`。

新增守卫的验证习惯是**变异自检**：把被保护的分支改坏（删掉可见性过滤、去掉 `markup=False`、
把 reveal 的闸门改成按天数、把票型数字就地重算……），确认测试真的红，再按 sha256 校验还原。
只报"绿"不算证明。还原要校验**整个文件的字节**：一次中途 `kill` 会让 `finally` 来不及跑，
只 grep 被改动的那一行看不出来（这一条是踩过之后写进来的）。
备份本身也会反过来咬人：`--verify` 拿的是**备份那一刻**的字节，如果那次备份之后又手工改过这个
文件，"修复漂移"就成了用旧版覆盖新改动，而且全绿——本次真实发生过，事后补了
`test_the_committed_report_is_still_what_the_code_renders` 盯着报告与 `render_md` 的分叉。
所以每轮变异自检结束都要把 `/tmp/mutbackups/*` 按当前字节刷一遍。

还有两条是同一次跑废之后写进来的。**变异跑期间不要动测试文件**：往正在被反复收集的测试目录里
加几条会红的用例，等于给每个变异体凭空添上几条红——被抓住的判决看着像真的，归属却全是假的。
本轮 F5–F11 就这么污染过一次，整套 11 具带着 `--ignore=<在写的测试>` 重跑了一遍才敢引用。
另一条：**会让测试挂起的变异体也是"被抓住"**，但 harness 必须给 pytest 调用带 `timeout`，
否则它自己不退出，红名单也拿不到；判词写成 `CAUGHT [hang after Ns]` 并记下最后一行开始跑的
测试文件名。**但超时本身不是判词**：连跑多具变异体时套件会被拖慢，一次负载超时就会被记成
一条并不存在的"抓住"。本轮 N7 单跑 12.7s 就红在具名用例上，连跑时却报了 hang——harness
现在遇到超时先按 4× 上限重跑，仍然超时才写 `[hang after Ns]`。

第三条，本轮最贵的一条：**同一时刻只跑一个变异 harness**。两个进程都在改 `assemble.py`/`cli.py` 的
同一批行，结果不是"两份输出"而是**变异体留在工作树里**（本轮是 M4 和 M8 两具），而且后启动的那具
会先 `backup()`——它备份到的是**前一个进程已经改坏的字节**，于是"`cmp` 工作树 vs 备份"这道完整性
检查报的是 SAME，看着像还原成功。真正发现污染的不是任何校验，是下一次跑套件时那两条断言红了。
所以：变异跑完先跑一遍套件再信工作树，`cmp` 备份只能证明"没被改坏过"，改坏前就没刷过备份的话它什么都
不证明；而一次后台跑的 harness 不会挡住前台再启一个，日志会互相截断（本轮 `/tmp/mut_b2floor.log`
第一次是空的，因为两个进程 `>` 同一个路径）。

### 端点说不的时候，谁替九个座位答的话

`transport.py` 里过去那一行写的是 `is_upstream_error = status >= 500 or "<html" in body
or "upstream error" in body`——一句关于**状态码**的话，但它承担的是另一句：**这个答案能不能
当成模型的行为记账**。两句话不等价，差价是一整局数据。401（凭证被拒）、404（路径写错）、以及
任何我们没见过的 4xx 都不在那三个条件里，于是它们被送回 `llm.py` 重试两次、交回一个带 error 的
`CallResult`、闸门用 `legality.default_action` 补位，最后落成一局有正常 `terminal`、九个座位
集体"弃权"的实测记录。这台端点最后一次给出响应恰恰就是 401（2026-09-21T18:21:44Z，一次不带
`Authorization` 的 `GET /v1/models`），所以这不是假想敌。

现在的判据是**默认拒绝**，一句话讲完：非 200 里只有两类按轮处理——`TIME_DEPENDENT_STATUSES
= (408, 429)`，答案依赖你什么时候问，所以等待是解药；以及"400 且 body 提到 length/context/
maximum/token"，那是一条压缩令，解药在 assembler 手里。其余一律是端点或配置的事实：`llm.py`
花完两次重试就抛 `EndpointUnavailable`，`game.py` 收在 `aborted_endpoint`。**宁可丢一局，
不可造九十个假行为。** 我第一版写的还是正面清单 `(401, 403, 404, 405, 410)`，是 `422` 让我改成
默认拒绝：一个还没见过的状态，没有理由默认它能进语料。

写这段的时候我自己犯了一次，值得记：把正面清单换成默认拒绝时，我机械地把 `"<html" in low or
"upstream error" in low` 一起搬进了"按轮处理"那一侧的括号里，方向正好反了——于是 502 变成"这一轮
的问题"。抓住它的不是新用例，是**旧**的那条 `test_a_5xx_and_a_gateway_html_page_are_both_unhealthy`
（0.5 秒，具名红）。改完的非 200 分支里那两个 body 形状判断是多余的：网关页和 upstream error 都
带一个既不是 408/429 也不是压缩令的状态码，默认拒绝自然把它们收在端点这一侧。

7 具变异照 `/tmp/mut_refusal.py`：`--fast` 那一组 7/7 CAUGHT（2026-09-22，0 次无效运行），F3
另外单独跑了整局那一组、也是 CAUGHT；`src/wolfengine/transport.py` 与 `src/wolfengine/llm.py`
两轮都按字节还原。红用例：

| 变异 | 红用例 |
| --- | --- |
| F1 "看时间才变的答案"清单清空（连限速也判死） | `test_a_rate_limit_and_a_deadline_stay_this_turns_problem` |
| F2 清单吞掉 400–599（什么都不判死） | `test_a_refusal_that_would_come_back_identically_is_not_this_turns_problem` + 旧锚点两条（5xx、非 400 的 length 回显） |
| F3 退回改动前的正面规则（就是这次修的那个 bug） | `test_a_refusal_that_would_come_back_identically_is_not_this_turns_problem`；整局那一侧见下 |
| F4 压缩令不再往上抛（`is_length_error` 恒 False） | `test_a_400_about_length_is_an_order_to_compress_not_to_resend` |
| F5 丢掉 400 那道闸（任何状态带 length 字样都算压缩令） | `test_length_wording_echoed_by_a_non_400_is_not_an_order_to_compress` |
| F6 连不上不再算端点故障（异常分支的旗子拿掉） | `test_an_unreachable_endpoint_is_the_endpoints_fault_not_the_models` |
| F7 判据算出来了但 `llm.py` 不作为 | `test_a_persistent_outage_raises_rather_than_becoming_a_fallback` + `test_a_failed_call_consumes_no_completion_tokens` |

整局那一侧的用例（`test_a_rejected_key_aborts_the_game_instead_of_answering_for_nine_seats`）在 F3
下也红了，这一点单独值得记：**它红得慢是缺陷的形状，不是测试的问题**。判据一坏，整局就带着
fallback 一路跑到底，每个座位都要付满 `1.5 + 3.0` 的两次退避，单跑那一组实测 5 分钟；我第一轮
只给它 240s，于是 harness 报的是 `TIMEOUT`——那不是判词，是我把预算给错了（harness 现在按组给
超时，慢的那组 900s）。所以：判断据有没有守住先看快的那组（0.5s），整局那一组是"愿意等就有第二
份证据"，不是唯一证据。

### 每局的墙钟乘数：从一句估算变成一行读数

计划 §187 那句"每局 4–8 分钟、8–12 局/小时，所以一夜 100 局可行、250 局不可行"是整个对比链的
前置条件——它决定哪些指标来得及当结论。而仓库里**没有一个读数为它兜底**：它假设每档调用的生成长度
是 110/30/15，引擎实际问的是 140/60；它假设一局 4.5 天，mock 桌实测 3.2 天。更糟的是"哪些阶段按
发言计价"此前只存在于 `actors.py` 一个内联三元组里，第二个想算这笔账的人只能重抄一遍名单——于是
漂移的代价不是红用例，是一句悄悄变假的散文。

现在判据在一处（`Config.token_budget_for`），发请求的人和普查的人都问它，`--dry-run` 末尾多印一行：

```bash
.venv/bin/wolf run --dry-run --games 20 --out /tmp/wolfcensus2   # 10.8 秒，零 API 调用
#   成本合计（20 局 · 53.8 次/局）：1075 次调用 · prompt 1947582 tok · 完成预算 101060 tok
#   （按 max_tokens 的上限算，非实测生成长度；每局硬顶 20000）
```

2026-09-21T21:23Z 的这 20 局替身桌：**53.8 次调用/局**（23–75）、**97,379 tok prompt/局**、
**5,053 tok 完成预算/局**（2,180–6,900）。次数随天数走，去掉这个混杂变量后是 **17.1 次/天**
（11.5–22.0）。剩下未知的只有端点那两个常数（`docs/calibration.md` 的 `D_decode_tok_s` 与
`per_call_fixed_overhead_s`，任务 #1/#5），乘数这一半不用再估。顺带定了两根顶的性：按 mock 节奏
最大 2,013 tok/天 × `max_days=6` ≈ 12.1k，够不着 `max_game_completion_tokens=20000`，先把局掐停
的是天数上限——那根 token 顶是备用的（真桌若把每次调用都问满，才可能撞上它）。

7 具变异照 `/tmp/mut_budget.py`（2026-09-22，7/7 CAUGHT，0 次无效运行，三个被改文件按字节还原；
第一轮 G2 因我拼锚点少切一个字符而 BROKEN-IMPORT，那不是判词，修好重跑才算）：

| 变异 | 红用例 |
| --- | --- |
| G1 计价函数不再分档（一律按动作预算） | `test_only_the_three_prose_phases_are_billed_the_speech_budget` + 普查那条 + 整局那条 |
| G2 名单多收一个夜间动作（狼刀按发言计价） | 同 G1，三条 |
| G3 名单少收一个发言阶段（遗言按动作计价） | 同 G1，三条 |
| G4 普查把 prompt token 当成完成预算印 | `test_the_census_prices_a_game_in_calls_tokens_and_completion_budget` |
| G5 普查把"这是上限不是实测"那句免责拿掉 | 同上（那条同时钉住免责句与硬顶读数） |
| G6 演员不看阶段，每次都要满发言预算 | `test_the_token_budget_enumeration_is_only_ever_read_through_one_function` + `test_every_turn_sends_the_max_tokens_its_phase_is_priced_at` |
| G7 演员把名单**重抄回**内联三元组（值完全不变） | 只有前一条：整局那组 `SURVIVED`——见下 |

G7 是这轮唯一一具**按值查不出来**的变异，值得单说：把正确的名单再抄一遍，发出去的和印出来的都还
是对的，任何断言都读不出差别——差别只在"下一次改名单时有几处要同步"。所以钉它的不是值断言而是
字面量出处（和 `test_purity.py` 里那几条同一手法）。这也说明 G6/G7 两具为什么都要跑：一具证明
值对不上会红，一具证明**抄一份**这件事本身会红。

#### 收成一处之后，另一条守卫变红了

7/7 CAUGHT 之后我跑了一次全量，套件给出的不是 606 绿而是 **1 failed, 605 passed**：
`test_an_axis_field_no_code_reads_is_a_lie_not_a_knob` 报 `['max_tokens_speech', 'max_tokens_action']`
"被当作处理轴暴露给 `--set`，但 src/ 里没人读它"。这两句都成立，而且正是这一轮改动造成的：那条
守卫要防的是"一个字段只是标签"（改它只动 `config_hash`、九个人玩的还是同一套规则），它的判据是
"看 `cfg.<字段>` 这样的属性访问，**整个 `config.py` 不算证据**"——排除整文件的理由是"每个字段在那儿
都有一行声明"。可 `token_budget_for` 让那两个字段唯一的读取点住进了 `config.py`，于是"被调用点读走的
字段"被读成了"没人读"。

为什么 7 具变异没提前发现：每轮变异只跑它点名的那几组文件（这轮是 `test_purity.py`+`test_cli.py` 和
`test_live_path.py`），`test_batch_paired.py` 不在任何一组里。**G1–G7 改的都是计价的结果，没有一具改的是
"读取点住在哪个文件"**，所以这个形状只有全量跑得出来。

没有回退成内联三元组（那是 G7，`test_purity.py` 那条字面量出处守卫会立刻红），而是把排除从**文件级**
降到**理由级**：声明行本来就不含 `.字段`，所以不需要排除整文件；`config.py` 里的读取点算证据，但要求
那个方法被别的模块调过（`_reachable_config_reader` 用 `ast` 找 `self.<字段>` 的宿主方法，再去别的模块
里找 `.方法名`）。于是"只在没人调的方法里读过"仍然是一个标签，不是旋钮。

5 具变异照 `/tmp/mut_axisguard.py`（2026-09-22，5/5 与预期一致，0 次无效运行，三个被改文件按字节还原）。
每具都写明**预期是红还是绿**，因为收窄一条守卫的代价必须两向都证：

| 变异 | 预期 | 结果 |
| --- | --- | --- |
| H1 新增一个零读取的轴字段 | 红 | `CAUGHT`（`test_an_axis_field_no_code_reads_is_a_lie_not_a_knob`） |
| H2 该字段只在**没人调**的 `Config` 方法里读 | 红 | `CAUGHT` 同上——可达性判据在说话 |
| H3 该字段在**被调用**的 `Config` 方法里读（就是本轮的读法） | 绿 | 不红——否则 `#41` 又被误伤一次 |
| H4 = H2 的形状 + 把可达性判据拆掉 | 绿 | 不红——反证 H2 那具靠的是判据而不是运气 |
| H5 退回"整个 `config.py` 不算证据"，跑当前代码 | 红 | `CAUGHT`——这颗雷就是本轮踩到的那颗 |

H3/H4 是"预期不红"的两具：它们要钉的不是缺陷被抓住，而是**抓住缺陷的那句话真的有承重**。H4 尤其
要紧——如果拆掉可达性判据后 H2 仍然红，那条判据就是装饰，而它读起来很像安全。

### 上限要变成期望：一局跑完之后，兑现率只从日志里算

普查那一行印的是**上限**（每档 `max_tokens` 之和），每局墙钟也是乘它乘出来的。上限要变成期望，
需要"问了它多少"和"它实际答了多少"在同一份产物里对账——此前两头都在日志里（`request.max_tokens`
与 `response.completion_tokens`），但没有任何一条读数把它们除一除。于是那句"上限大概是实际的两倍"
只能由人记住去算，而算的两个数住在两个文件里。

现在 `m7_cost_profile` 顶层多 `asked_total` / `asked_calls` / `fill_rate`，逐阶段多
`asked_sum` / `asked_n` / `fill_rate` / `truncated`，`wolf audit` 的 JSON 原样带出去。2026-09-21T22:06Z
三局走 `HttpTransport`（mock 桩）的替身桌：

| seed | 终局 | 调用 | 问过的预算 | 实际生成 | 兑现率 | 截断 |
| --- | --- | --- | --- | --- | --- | --- |
| 7 | `good_win` | 56 | 5,280 | 2,240 | 0.4242 | 0 |
| 11 | `wolf_win` | 58 | 5,400 | 2,320 | 0.4296 | 0 |
| 23 | `wolf_win` | 44 | 4,080 | 1,760 | 0.4314 | 0 |

逐阶段看是 `day_speech` 0.2857（140 里用了 40）、其余动作档 0.6667（60 里用了 40）。**这三个数是
替身桌的说话长度，不是 gemma 的**：Oracle 只法官给过的短答案，所以它的兑现率是一根刻在测试夹具里
的常数。这一轮买到的不是那个 0.42，而是"一局真跑完之后这个格子自动有值"——墙钟估算从此不需要
人手算折扣，而 `max_tokens_speech=140` 到底是宽了还是已经顶到天花板，也有了一个读数来回答（顶满
和简洁分得开，靠的就是同一条里的 `truncated`）。

两条不做外推的规矩：日志里没有 `max_tokens` 时报 `null` 并附 `fill_rate_note`，**不报 0.0**（0.0
是一句关于行为的结论——"模型一个 token 也没用完"——而这里唯一的事实是这份日志没记问过多少）；
比值只覆盖记了预算的那几次调用，覆盖面写成 `asked_calls` 对 `n_calls`，所以一份只记了一半的日志
不会伪装成一局的结论。

9 具变异照 `/tmp/mut_fillrate.py`（2026-09-22，第二轮 9/9 与预期一致、0 次无效运行，
`actors.py`/`metrics.py` 按字节还原；第一轮 K5 活了，见下）。K1–K6、K8、K9 改的都是"从已有事件里
怎么算"，只跑快的那组（`test_golden_game.py` + `test_cli.py`）；K7 两组都跑，因为它改的是**记账**。

| 变异 | 结果 | 见证 |
| --- | --- | --- |
| K1 逐阶段：没问过就报 `0.0` | `CAUGHT` | `test_m7_invents_no_fill_rate_for_a_log_that_never_asked` + 金样本那条 |
| K2 分子把没记预算的调用也算进来（两侧不同集合） | `CAUGHT` | 同上——半覆盖工况表就是为它摆的 |
| K3 分母读错键（拿 prompt 估算当问过的预算） | `CAUGHT` | 三条一起红，含 `test_m7_reports_the_share_of_the_asked_budget_the_model_actually_used` |
| K4 逐阶段截断数恒 0（顶满与简洁分不开） | `CAUGHT` | `test_m7_reports_the_share_of_the_asked_budget_the_model_actually_used` |
| K5 兑现率不落地成四舍五入的读数 | 第一轮 `SURVIVED` → 补夹具后 `CAUGHT` | 同上，见下面那段 |
| K6 `asked_calls` 报的是全部调用（覆盖率成了假的全覆盖） | `CAUGHT` | 金样本那条的精确字典，键多一个也红 |
| K7 演员照发预算，但不再把它记进日志 | 快组 `SURVIVED`、整局组 `CAUGHT` | `test_the_log_alone_yields_the_budget_to_used_ratio_the_census_is_waiting_on`（预期不红的那具：手工构造的工况表看不见记账） |
| K8 顶层兑现率：没问过就报 `0.0` | `CAUGHT` | 含命令行那条 `test_audit_carries_no_fill_rate_for_a_table_that_never_called` |
| K9 `null` 不再带理由（"这格为什么空"回到散文里） | `CAUGHT` | `test_m7_invents_no_fill_rate_for_a_log_that_never_asked` 直接读 `fill_rate_note` |

K5 第一轮活下来是一次真实的漏，而且漏在夹具而不是代码。K5 那具改的是**逐阶段**那处取整（顶层的
`null` 分支归 K8 管），而用例当时的逐阶段比值全是整除：140 里用 70 → `0.5`，60 里用 60 → `1.0`，
去掉 `round(...,4)` 后 `assert == 0.5` 照样成立——**整除的比值钉不住取整**，能钉住它的顶层那条
断言当时恰好没被这具变异摸到。把其中一次说话改成 71 个 token（140 里用 71 → `0.5036`）之后，
同一具变异立刻红了。这是"测试强度两向都要证"在夹具这一侧的样子：断言的数值本身得对缺陷有区分力。

### 体检工具自己也要被跑过一遍：一台 loopback 桩端点

`scripts/calibrate.py` 是唯一有权产出延迟常数的程序，而它此前离线覆盖到的只有**渲染**那一半。
`main()` 自己的三个决策点一次都没被执行过：没导出 key 就不该开始、端点不通就不该留下任何文件、
跑完之后报告和 sidecar 必须互相承认同一件事。这三条都只有"真端点在场"时才有第二次机会，而端口
开着的时间窗口很短——第一通真电话不该拿去调试工具本身。

`tests/test_calibrate_rehearsal.py` 因此用 stdlib 的 `ThreadingHTTPServer` 在 `127.0.0.1:0` 上起
一台真插座上的桩，把 `main()` 从预检跑到落盘。`httpx.MockTransport`（`test_live_path.py` 那批用
的就是它）在这里顶不上：`main()` 自己建 client，注入点只在实际的 TCP 对端。桩是**规格形状**的
服务器，不是**速度形状**的：它按 OpenAI 兼容的字段回答，所以"六段探针能不能跑完"在这儿有答案；
它不假装自己有延迟，所以**任何一个常数都不许从这次排练里读走**。零延迟反而露出一个真实的角——
`per_call_fixed_overhead_s` 在这里被拟合成了**负数**，而一台合法的 OpenAI 兼容服务器本来就可以
是这个速度。同一台桩有两个朝向：正面桩兑现 `stop`/`logprobs`/确定性，反面桩三样都收单不兑现，
报告里每个判定都被两头钉过一次，写死任何一个结论都会红。

```bash
.venv/bin/pytest tests/test_calibrate_rehearsal.py   # 15 条，只碰 loopback，不需要 key
```

它抓到的不是测试里的 bug，是产物里的四条，外加两条本可以永远留在散文里的对账：

| 缺陷 | 症状 | 见证 |
| --- | --- | --- |
| §0 的"未测得"和 loader 的"不可用"是两把尺 | 页眉自称构成常数来源，`load_calibration` 却判同一份数据不可用（两边读的都是同一个 `derive_constants()` 的输出） | `test_the_report_only_claims_to_be_a_source_when_the_loader_agrees`，另有结构侧 `cal.fitted_constant is metrics.fitted_constant` |
| sidecar 不记 `base_url` | `--from-json` 重渲染时拿**当前配置**给那次测量盖章 | `test_a_re_render_carries_the_measured_endpoint_not_the_current_config` |
| `render_md` 收了 `model` 参数却从不读它 | 比"没有这个参数"更糟：一条已经修好的假象 | `test_the_re_rendered_model_comes_from_the_record_too` |
| 字段缺失被静默补成配置值 | 仓库里现存的那份 sidecar 两个字段都没有，页眉于是凭空多出一个它没测过的出处 | `test_a_sidecar_that_never_recorded_the_endpoint_says_so` |
| `/v1/models` 列出的 id 与请求体里的 model 从不比对 | R7（服务被人重启换了权重）只活在 §1 那坨 JSON 里等读者心算 | `test_a_model_the_endpoint_does_not_advertise_is_flagged`（对不上要报、对得上不许报，两头） |
| 比对出来的否认，读侧读不到 | 报告 §0 报了"端点不承认这个 model"，`load_calibration` 却对同一份 sidecar 放行：它只认 `model_declared`，而这个字段是这一轮才加的，旧文件里根本没有——同一份数据，两个读者 | `test_a_denial_alone_stops_both_products_from_claiming_a_source`（伪造一份常数与行都齐全、只有清单不认账的记录，两头都得拒绝），另有结构侧 `cal.declared_models is metrics.declared_models` |

26 具变异照 `/tmp/mut_calib.py`、`/tmp/mut_c3.py`、`/tmp/mut_header.py`、`/tmp/mut_denial.py`
（2026-09-22 各跑一轮：11/11、1/1、6/6、8/8 CAUGHT，0 次无效运行，`scripts/calibrate.py` 与
`src/wolfengine/metrics.py` 按字节还原）。红用例具名：

| 变异 | 红用例 |
| --- | --- |
| C1 §0 退回只看 `is None` 的松尺 | `test_the_report_only_claims_to_be_a_source_when_the_loader_agrees` |
| C2 sidecar 不再记 `base_url` | `test_a_re_render_carries_the_measured_endpoint_not_the_current_config` + `test_the_sidecar_a_run_writes_contains_the_block_the_loader_reads` + `test_redact_leaves_the_constants_block_alone`（后者顺带顶住了"redact 把局域网地址一起吃掉"那具等价变异） |
| C3 重渲染不读 sidecar 的地址 | `test_a_re_render_carries_the_measured_endpoint_not_the_current_config` |
| C4 预检失败照样往下跑 | `test_a_dead_endpoint_writes_no_report`（这具会真跑完那张死掉的矩阵，单给它 480s） |
| C5 没导出 key 也允许开跑 | `test_no_key_exported_means_the_script_never_starts` |
| C6 400 被当成可重试的错 | `test_a_400_on_the_wrong_request_is_not_resent` |
| C7/C8/C9 `stop`/`logprobs`/确定性三个判定写死 | 三条同红在 `test_a_server_that_disagrees_is_recorded_as_disagreeing` |
| C10 sidecar 把 `throughput` 改了名 | `test_every_phase_ran_and_no_probe_died_on_the_way` + `test_the_sidecar_and_the_report_come_from_one_run_and_one_fit` |
| C11 `fitted_constant` 放开到负数 | `test_a_zero_constant_is_refused_because_it_sits_in_a_denominator` + `test_m7_refuses_a_zero_overhead_rather_than_a_free_first_token` |
| D1 缺字段时静默拿配置补齐（不标注） | `test_a_sidecar_that_never_recorded_the_endpoint_says_so` + `test_the_committed_report_is_still_what_the_code_renders` |
| D2 整块删掉 model 对账 / D6 `declared_models` 找错键读空 | 两具同红在 `test_a_model_the_endpoint_does_not_advertise_is_flagged` |
| D3 对账不分正反（列出了就报警） | `test_a_model_the_endpoint_does_not_advertise_is_flagged` 的反面那一半 |
| D4 对账不看 model 是否真的记录过 | `test_a_sidecar_that_never_recorded_the_endpoint_says_so`（拿活配置比出来的是一条关于 `None` 的假结论） |
| D5 `main()` 不再把测得的出处交给报告 | `test_the_sidecar_and_the_report_come_from_one_run_and_one_fit` + 上面两条 |
| E1 `model_denial` 永远返回 `None`（整条守卫空转） | `test_constants_from_a_model_the_endpoint_denies_are_refused`、`test_a_model_the_endpoint_does_not_advertise_is_flagged`、`test_the_sidecar_carries_the_evidence_that_discredits_itself`、`test_a_denial_alone_stops_both_products_from_claiming_a_source` |
| E2 空清单当成否认（把"没证据"读成"有反证"） | `test_an_absent_or_empty_listing_is_no_evidence_not_a_contradiction` + 另四条同红：三份清单缺位的合格 sidecar 被自己否决，`test_constants_fit_for_another_model_are_refused` 则是否决来得太早、挤掉了原本那句"拟合于另一个 model"的理由 |
| E3 不看 model 是否真的记录过 | `test_an_absent_or_empty_listing_is_no_evidence_not_a_contradiction`、`test_a_sidecar_that_never_recorded_the_endpoint_says_so`、`test_the_committed_report_is_still_what_the_code_renders`（凭空多出一行，仓库里那份报告立刻对不上） |
| E4 读侧算出否认但不作为（不毁常数、不进 note） | `test_constants_from_a_model_the_endpoint_denies_are_refused`、`test_the_sidecar_carries_the_evidence_that_discredits_itself`、`test_a_denial_alone_stops_both_products_from_claiming_a_source` |
| E5 读侧丢掉 `declared_models(features)` 兜底 | **唯一见证** `test_a_denial_alone_stops_both_products_from_claiming_a_source`（loader 自己那批全绿：它没有一份"没有 `model_declared` 字段但清单打脸"的用例，这条断言只在伪造记录里造得出来） |
| E6 页眉的拒绝理由里不含否认（§0 仍单独报警） | 同上，唯一见证 |
| E7 sidecar 不再落盘 `model_declared` | `test_the_sidecar_a_run_writes_contains_the_block_the_loader_reads`、`test_the_sidecar_carries_the_evidence_that_discredits_itself` |
| E8 `declared_models` 找错键，永远读空 | `test_the_sidecar_a_run_writes_contains_the_block_the_loader_reads`、`test_a_model_the_endpoint_does_not_advertise_is_flagged`、`test_the_sidecar_carries_the_evidence_that_discredits_itself`、`test_a_denial_alone_stops_both_products_from_claiming_a_source` |

上一轮点名"改不动"的三具等价变异，这一轮两具变成了观察（`main()` 显式交出处、字段缺失必须标注），
第三具（redact 顺手吃掉 `base_url`）早在补 loader 断言时就已经不是等价的了。D1–D4 里那几具的判据
都不是"页眉好不好看"，而是**同一条陈述有没有第二个来源**：`stamp()` 现在只可能从"那次测量记下的
值"或"标着出处的一句"里二选一。

E5 与 E6 各自只有**一条**见证，而且那条不是真跑出来的文件：旧 sidecar 永远不会有 `model_declared`
（这个字段本轮才加），"清单只存在于 `features` 里"这一支因此只有伪造一份"常数齐全、行齐全、只有
端点自己不认账"的记录才进得去。实测就是这样——E5 下 `tests/test_calibration_loader.py` 与
`tests/test_calibrate_guard.py` 整批全绿，红的那一条在排练文件里。真跑的那一份两边都写满，永远
分辨不出"读了字段"和"只读了字段"。E3 则反过来证明 `test_the_committed_report_is_still_what_the_code_renders`
不是形式主义：仓库里现存的那份记录 `model` 是空的、清单却列着一个权重，所以"拿配置去比清单"会
凭空给页眉加一行关于 `None` 的结论，而那条测试当场发现报告不再是代码渲染出来的样子。

### 日志读不下去的时候：砍断的末行与拿错的文件

端点在半路拒答、进程在半路被杀，留下的都是同一个形状：最后一行只写了一半。`run` 是**一行一开一关**
地 append 的，所以一次 kill 最多只会留下一行半句话——这个事实就是整条判据的界：

* **末尾一行** parse 不过 = 撕裂的尾巴。丢掉它，前面读成完整的一局。
* **两行** parse 不过，或者坏行后面还有好行 = 损坏。那不是一次没写完，是一次以上，或者是一刀切在
  中间；宽到能吞下它的容忍，也宽到能吞掉一整天。报 `LogDamage`，消息里带**文件名和第几行**——
  以前甩出来的是 `JSONDecodeError`，它报的是"行内第 209 列"，对一份 500 行的文件等于没说。
* 丢掉的那几十字节**只数、不印**。那半行完全可能是一条 `wolf_chat`，把"读不出来的原文"贴进观众
  看得见的产物里，泄的就是它本要保住的东西。

**光有容忍不够，必须说**：一局最后那条事件是 `GAME_OVER`，把它丢掉，一局 `wolf_win` 就读成
`unfinished`。看不见截断的读者会把"分母短了"读成"这批没跑到那儿"——这是一句关于产品失败的结论，
而事实只是"文件被砍了一刀"。于是三个出口都带这句话，而且是同一句：`wolf audit` 的 JSON 多一个
`torn_tail`（`{"lines","chars"}`，完整文件为 `null`，免得 `1` 被读成常态），终端转录末尾一行 `〔…〕`
（座位视角也带，那才是有人据以行动的那份），复盘 HTML 页眉 `⚠ …` 挂在它本来那句终局旁边。
`split_torn_tail` 与 `torn_notice` 各只有一份实现在 `events.py`，四个读侧（`audit`/`replay`/HTML/
`metrics.read_dir`）与直播尾巴统统走 `EventLog.read_split`——`render_live` 原来自己那一份弹循环比
谁都宽，正是这一轮拆掉的东西。

`compare` 没有配"这批有几局被砍"的读数，是故意的：跑断的批次根本没有 `run_manifest.json`，在门口就被
拒了，而砍一局产生的 `unfinished` 早就在决定分母之外、被 `n_dropped_other` 数着了。加一个没人读的字段
不如不加。

14 具变异照 `/tmp/mut_torn.py`（2026-09-22T00:18Z 对着最终字节重跑，14/14 与预期一致、0 次无效运行，
四个被改的 src 文件 `cmp` 逐字节还原；开跑前的前置条件是先把被碰的五个测试文件跑成全绿，见该文件
末尾那段），红用例具名：

| 变异 | 红用例 |
| --- | --- |
| D37 空行跳过丢掉（文件末尾多一个换行就报成截断） | `test_a_trailing_blank_line_is_not_reported_as_a_cut` |
| D38 末行容忍永不生效（回到全线崩溃） | 读侧十条一起红，含 `test_a_torn_last_line_reads_as_the_complete_prefix` |
| D39 一行宽的上界丢掉（弹到底——修复前直播的行为） | `test_only_one_torn_line_is_a_tail_two_is_damage` + 直播那条 |
| D40 丢掉的那一行不报告 | `test_read_split_names_the_lines_it_had_to_drop` + 三个出口各一条 |
| D41 损坏行的行号丢掉 | `test_a_damaged_line_mid_file_says_which_line` |
| D42 行号从 0 开始数 | 同上 + `test_a_line_that_is_json_but_not_a_record_is_damage_too` |
| D43 文件名前缀丢掉（一目录日志里猜哪份坏了） | `test_the_damage_message_names_the_file_being_read` |
| D44 非对象行不当损坏（`123` 走回 `AttributeError`） | `test_a_line_that_is_json_but_not_a_record_is_damage_too` |
| D45 `audit` 把丢掉的字节一起印出来 | `test_audit_reports_a_torn_tail_without_printing_its_text`（唯一见证：`test_cli` 那份日志是完整的，`torn_tail` 恒 `null`，往里面塞键在那一侧根本看不见） |
| D46 `audit` 永远报撕裂 | 同上，加上 `test_a_clean_log_reports_no_torn_tail` |
| D47 通知只挂观众视角 | `test_a_chronicle_handed_to_a_human_says_it_was_cut` |
| D48 HTML 页眉不印通知 | `test_the_html_page_says_the_file_was_cut` |
| D49 直播尾巴绕开 `read_split` 自己解析 | `test_the_live_tail_drops_no_more_than_the_offline_reader_does` + 结构那条 |
| D50 `cli` 在旁边另写一句截断话 | `test_the_html_page_says_the_file_was_cut`（唯一见证，见下） |

D39 与 D49 是同一件事的两面：一行宽这个**数**必须由断言读着，"两条腿同一个答案"这句**结构**也不能
只写在散文里，所以 `test_the_torn_tail_bound_has_one_owner_and_every_reader_calls_it` 钉的是实现只有一
份、四个读侧都调用它。

D50 第一轮**活了**，而它正是那条断言该抓的东西：跨产比对账原本写的是 `cli 那句 in html`，变异体另写
的那句"日志在这里截断：末 1 行"是共享那句的**前缀**，`in` 于是永远绿。改成两句一字不差之后当场红。
教训写在这儿而不是删掉：**containment 断言在"只会变短"这一类漂移上等于没断**，`in` 要配长度或相等。

#### 拿错了文件：同一条判据的另一种失败

日志读不下去还有第二种：文件没被砍，它**根本是另一种文件**。`--dry-run` 把提示词转储
`g<seed>.prompts.jsonl` 写在它用来换那些状态的**同一个目录**里，名字只差一个后缀，tab 补全就在那
里挑。2026-09-22T00:31Z 拿一份真转储喂三个入口（`audit`、`watch --once`、`export`），三处都死在
`d["visibility"]` 上，甩出来的是 `KeyError: 'visibility'`——没有文件名、没有行号、也没有"这是别的
文件"这半句。

现在它走 `LogDamage` 那条同一的出口，句子把三件事都说清：**第几行**、缺哪几个事件必需的键、以及
**它自己有哪些键**（`attempt/messages/over_ceiling/…`，读者一眼认出这是转储）。第三件不是修辞：
只报缺的键，认不出**是哪一个文件**。

被要求的键就是 `records_from_lines` 用下标读的那五个，写成一张 `EVENT_KEYS`。这张表是本轮变异集中
挨打的地方（9 具 F1–F9 全 CAUGHT，见 `/tmp/mut_kind.py`）：

| 变异 | 红用例 |
| --- | --- |
| F1 缺键判据永不触发（回到裸 `KeyError`） | 转储那两条 + 逐键那条 |
| F2–F4、F6 键表抽掉 `seq`/`kind`/`day`/`visibility` | `test_each_key_the_loader_subscripts_has_a_line_that_demands_it`（F2/F6 另带一条） |
| F5 键表抽掉 `phase` | **只有**逐键那条抓得到，见下 |
| F7 消息丢掉"它有的键是…"那一半 | `test_a_line_without_the_event_keys_says_which_ones_are_missing` |
| F8 判据挪到 manifest 分支之前 | 每份正常日志的第 1 行就被拒，读侧整批红 |
| F9 判据只管第 1 行 | 逐键那条（它的样本行在第 2 行），红回 `KeyError: 'seq'` |

F5 是这一小段真正买到的东西：转储样例**自带 `phase`**，所以"表里少了 phase"这种半成品在四条
转储用例上全绿——只有"每一个键各喂一行就缺它"的循环看得见它。一张五个元素的表被四个元素读着，
第五个就是装饰品，而它看起来完全像一条判据。循环因此用自己写死的五个键名，不读生产侧那张表（读了
就等于让变异体自己决定测什么），表与用例的脱钩由最后那条断言兜。

边界也记在这儿：**按名字跳过**转储的那条判据（`metrics` 与 `batch` 里的 `endswith(".prompts.jsonl")`）
和这条**按内容拒绝**的判据是两回事，前者还在。一份转储被改名成 `g007.jsonl` 时，名字那条放行、
内容这条接住——这一轮补的就是名字那条永远管不到的那一半。

"前者还在"这四个字当时是散文，不是断言：那两份按名字跳过的判据，修前只有一份有测试读它。这件事
记在下一小节，因为它需要一具变异才看得出来。

重跑这一片：`.venv/bin/pytest tests/test_log_recovery.py`（46 条、跑起来 57 个用例，一秒内，不发请求）。

#### 页眉空了一格：manifest 的判据也有第二份

这一条不是设想出来的，是接着 #46/#47 那份读取清单往下数数出来的。`watch` 的页眉写着
`狼人杀直播 <game_id> · 第N天`，那个 `game_id` 过去由模块自己的 `meta_of()` 提供：开文件、拿
**第一行**、`json.loads`、取 `meta` 键、解析不动就 `return {}`。共用读取器认的却是另一件事：
**`seq == META_SEQ` 那一行**，而且跳过空行——`events.py` 明写为什么要容忍空行（手写夹具会有，
判它撕裂等于每次忘了空行都报一次错）。两套规则撞上需要的东西小到一个空行：文件开头多一行空白
（手写或手改的夹具、编辑器、脚本头）时，离线读侧照常认出 manifest，直播页眉静默少一格。少的那
一格恰好是"这一屏说的是哪一局"。

复现于 2026-09-22T00:49Z：同一份文件，`read_records` 报 `game_id='g-header-7'`，页眉印出
`狼人杀直播  · 第1天`。修法不是给 `meta_of` 补一句空行处理，是**删掉它**：`render_live` 现在
只有一处开文件（`_read`），事件和 manifest 从同一次 `read_split` 里出来，顺带少读一遍整个文件。

6 具变异照 `/tmp/mut_manifest.py`（2026-09-22T00:56:59Z 对着最终字节，6/6 与预期一致、0 次无效
运行，`src/wolfengine/render_live.py` 按字节还原）：

| 变异 | 红用例 |
| --- | --- |
| H1 `_read` 拿到事件、manifest 换回 `{}` | `test_the_live_header_shows_the_manifest_the_shared_reader_found` |
| H2 旧的"第一行"规则原样搬回 `_read` | 同上，红在行为上（不靠结构那条腿） |
| H3 改走 `read_records`（行为等价） | `test_the_torn_tail_bound_has_one_owner_and_every_reader_calls_it`，见下 |
| H4 `tail_events` 的 `after` 从 `>` 变 `>=` | `test_tail_events_resumes_after_the_last_seq_seen` |
| H5 页眉那一行不再写 `game_id` | 新用例，红在页眉那一处 |
| H6 页脚那一行不再写 `game_id` | 新用例，红在页脚那一处 |

H3 的 CAUGHT 不算测试强度：`read_records` 就是 `read_split` 去掉第三条腿，行为逐字相同，它红是
因为 #46 那轮的守卫扫的是调用名。它一开始被我声明成 SURVIVED，于是报了一次 WRONG——改的是声明，
不是断言，和 D45 同一类错。**红有两类，只有红在行为上的那类算数**，这一轮起表里标出来。

H5/H6 才是这轮买到的东西。`game_id` 在一帧里印两处（页眉、页脚），而新用例最初只断言它出现在
输出里：第一跑（00:54:45Z）两具在三套测试上全绿，因为掉一处另一处还在。改成逐处点名（把带页眉/
页脚前缀的行都捞出来，每行都得带着名字）之后才红。这是 D50 那条教训第三次以同一种形状出现：
`in` 是"存在"，不是"每处"。

再记一条流程事故：这一轮的 harness 最初写进了 `/tmp/mut_header.py`，而那正是 #37 那轮 D1–D6 的
文件名、上面的正文还在引用它——`Write` 不区分创建与覆盖，一次写就把六具变异体的定义没了。已按
会话记录里的原始字节还原（3949 字符，`ast.parse` 过），本轮 harness 改叫 `/tmp/mut_manifest.py`。
往 `/tmp` 落 harness 之前，先 grep 一遍文档里的 `/tmp/mut_` 引用。

重跑这一片：`PYTHONPATH=src .venv/bin/pytest tests/test_wiring.py -k live_header`（不发请求）。

#### 抄了两遍的跳过，只有一遍有人测

上面那句"`metrics` 与 `batch` 里的 `endswith(".prompts.jsonl")`"写出来的时候是散文，而散文里出现
"两份"就是一个可以被追问的问题：**这两份各有没有测试读它**。答案要一具变异才拿得出来
（`/tmp/mut_predup.py`，修前一跑 2026-09-22T01:14:10Z，每具只把一份判据拧成永不匹配）：

- **P1** `src/wolfengine/metrics.py:296` 那份（`read_dir` 里的 `endswith`）：`tests/test_cli.py` 红，具名一条
  （`test_the_batch_loader_ignores_the_prompt_dumps`），另两套绿。有断言。（号码在 10:24:31Z 重指过：
  `#65` 那一族在同文件里插了 29 行，把它顶歪了一格，行号闸门当场报红——这就是那套闸门要防的事。）
- **P2** `src/wolfengine/batch.py:263` 那份：**三套测试全部 SURVIVED**。零条断言读过它。
- **P3** 改**写入方**那个 f-string（`src/wolfengine/cli.py:243`，`.prompts.jsonl` →
  `.prompt-dump.jsonl`）：红 4 条。也就是说这份约定从写入方到 `read_dir` 是连上的，到 `read_arm`
  根本没连——两份判据长得一模一样，其中一份是装饰品。

`read_arm` 走的是 `--out` 那个目录，而 `--dry-run` 把转储落在它自己那个 `--out` 里：同一个参数的
同一个目录，两种文件。这台机器上的 `data/` 就是那个形状（三份日志躺着一份 `.html` 和两份
`g000000NN.prompts.jsonl`，2026-09-22T01:12Z `ls` 的）。所以缺的那条断言不是假想场景的保险。

要说清这一处的代价不是什么"数字错了"：#47 之后，把跳过的文件真去读会撞上内容判据，响亮地抛
`LogDamage` 并报出文件名。代价是**这条分支在 CI 里不可见**——删掉它、改窄它、把它写成永不匹配，
没有任何一条用例会红，而它的反面（一个把读不下去的文件直接跳过的批次读取器）正是 #46/#47 一路在
反对的那种沉默。

修法是两条用例，不是重构：

* `test_the_batch_reader_skips_the_dump_by_the_same_name_as_the_writer`（`tests/test_cli.py`）跑
  **真实写入方**（`run --dry-run` 落到 tmp 目录），再要 `read_arm` 返回"除了转储的那一份"。先钉
  目录里确实有转储（否则用例空转），再钉读到了什么。手写文件名的测试只能证明两个字面量还互相
  点头，证不了这个工具真产出的目录加载得起来。
* `test_a_renamed_dump_stops_the_batch_reader_naming_the_folder_entry`（`tests/test_log_recovery.py`）
  钉反方向：转储改了名，就必须由内容判据响亮接住，消息里带目录项的名字。那句散文边界当时对
  `watch`/`audit` 有断言，对唯一那份"按名字跳过"的批次读取器没有。

5 具变异照 `/tmp/mut_predup.py`（2026-09-22T01:20:36Z 对着最终字节，5/5 与预期一致、0 次无效运行，
三个被改文件按字节还原）：

| 变异 | 红用例 |
| --- | --- |
| P1 `read_dir` 的跳过永不匹配 | `test_the_batch_loader_ignores_the_prompt_dumps`（修前修后同一条） |
| P2 `read_arm` 的跳过永不匹配 | 新用例，红在 `LogDamage`：跳过没了，转储就被当日志读 |
| P3 写入方改名 | 新用例加进来之后 4 条 → **5 条**：批次读取器现在也在这条链上 |
| P4 `read_arm` 什么都跳过（返回空表） | 新用例（set0）+ 配对那批（set1）+ 改名那条（set2） |
| P5 `read_arm` 把读不下去的文件静默吞掉 | 只有改名那条红（`DID NOT RAISE LogDamage`） |

P4/P5 是这两条新用例各自的反面：一条管"跳过只许作用于转储"，一条管"不是转储又读不动的必须响"。
只补其中一条，另一条腿仍然是零断言——这也是为什么这轮加了两个文件而不是把断言堆进一条用例。

**明确不做**：不把这三份字面量收成一个共享常量。理由是取证出来的，不是审美的——两条新用例落地后，
"把字面量重新拆回三份"是行为等价的变异，没有任何一具杀得动它；共享常量只在编辑时防漂移上有价值，
而它要防的那种漂移（写入方改名）P3 已经用 5 条具名红用例接住了。收成一处会把"三处必须一致"变成
"一处必须正确"，前者现在有断言、后者没有额外的东西可测。

重跑这一片：`.venv/bin/python /tmp/mut_predup.py`（不发请求；跑它的时候别同时跑套件）。

#### 那一句话掉在 traceback 的最后一行

这一条不是读代码读出来的，是**跑真实产物**跑出来的（2026-09-22T01:29Z）：`data/` 里躺着 9 月 20
那三局日志和两份 `--dry-run` 转储，三份日志 `audit`/`replay`/`export` 全部 rc=0，两份转储全部
rc=1 外加一叠 traceback——#47 那句话确实说了，说完就掉在栈的最后一段：

```
$ wolf audit data/g00000021.prompts.jsonl
Traceback (most recent call last):
  … 15 行 …
wolfengine.events.LogDamage: data/g00000021.prompts.jsonl: 第 1 行没有一条事件必有的
seq/kind/day/visibility（它有的键是 attempt/messages/…），这不是一局日志
```

`main()` 当时是 `return ns.func(ns)`，一个 `except` 都没有。而"你给工具的是它不能用的东西"这个
仓库早就有另一种形状：`cmd_run`/`cmd_batch` 把 `ConfigError`/`BadOverride` 收成一句
`配置错误：…` 加 rc 2，`cmd_compare` 对没有 manifest 的目录也是 rc 2，本模块 docstring 写着
"2 = the command itself was wrong，脚本可以不解析散文就分流"。`LogDamage` 是唯一没有这层处理的
期望错误类——修前 src/ 里只有一处 `except LogDamage`，在 `events.py:410`，那是给自己包文件名的手，
不在终端这一侧；接住它的那一处 `cli.py:623` 是这一轮的修法。

修法是一处，不是四处：`main()` 接住 `LogDamage`，印一句 `日志读不下去：…` 到 stderr，返回 2。
四个读文件的出口（`audit`/`replay`/`export`/`watch`）因此共用同一句话，而用例也按四个出口逐个
点名（`test_every_file_reading_command_reports_a_wrong_kind_file_instead_of_crashing`，四条参数）：
退出码是 2、报告里有文件名、#47 那半句"缺哪些键"还在、没有 `Traceback`、`export` 不许在读不动的
输入上落一份 HTML。原先那条只盯 `audit` 的用例当时断言的是 `pytest.raises(LogDamage)` 从
`cli.main` 里冒出来——**它把崩溃本身钉成了期望行为**，所以套件全绿而终端在吐栈，这句 docstring
里写着"这是人真正看到的东西"的用例看到的其实是异常对象。

4 具变异照 `/tmp/mut_handler.py`（2026-09-22T01:37:56Z 对着最终字节，4/4 与预期一致、
`src/wolfengine/cli.py` 按字节还原）：

| 变异 | 红用例 | 红的类型 |
| --- | --- | --- |
| T1 拿掉 handler（回到不接） | 四条参数 + 结构那条，共 5 条 | 行为 |
| T2 退出码 2 → 0 | 四条参数（`assert 0 == 2`） | 行为 |
| T3 报告印到 stdout | 四条参数（`err` 是空串） | 行为 |
| T4 `export` 自己再接一遍 | `test_exactly_one_place_turns_log_damage_into_an_exit_code` | **结构** |

T4 单独说：它行为逐字不变，四条参数全绿，只有那条结构断言红（`cli.py` 里 `except LogDamage`
数到 2）。这就是 #48 的 H3 记过的分界——红有两类，只有红在行为上的那类算测试强度，结构那条买的是
"下一次有人私加一处 handler 会被发现"。T4 第一次写坏过一回（替换串的缩进停在 4 格，`try` 底下
没有体，报 `IndentationError`），算 BROKEN 不算 CAUGHT，修好才重跑。

重跑这一片：`.venv/bin/python /tmp/mut_handler.py`，或者只看终端那一屏——
`PYTHONPATH=src .venv/bin/python -m wolfengine.cli audit data/g00000021.prompts.jsonl`（一行，rc=2）。

#### 路径本身不对的时候，另一半也掉在栈里

`#50` 接住的是"文件在、读不动"。同一格边缘上还留着另一类：给它的路径根本用不了。
2026-09-22T01:48:52Z 拿真实 CLI 逐个试，一律 traceback + **rc 1**，而 rc 1 在这仓库里不是空的一格：
docstring 写的是 0=有结论、1=引擎拒绝（`compare` 判定这批不可比就走 1）。于是"你把文件名打错了"和
"这批数据要人去查 manifest"挤进同一个码，脚本按码分流时把一次拼错读成了一次科学结论。

| 敲错的东西 | 冒出来的类 | 现在终端上 |
| --- | --- | --- |
| 文件名多打一个字符（`audit data/nope.jsonl`） | `FileNotFoundError` | `路径用不了：[Errno 2] No such file or directory: 'data/nope.jsonl'`，rc 2 |
| 把目录当日志（`audit data`） | `IsADirectoryError` | 同一句话，rc 2 |
| `-o` 指向一个目录 | 写侧同一个类 | 同一句话，rc 2 |
| 把 `--out` 的父路径填成文件 | `NotADirectoryError` | `… [Errno 20] Not a directory: '/tmp/notadir_parent.txt/sub'`，rc 2 |

后两行是 2026-09-22T01:59:39Z 真跑的；第四行"修前也是栈"从"`main()` 里当时一个 `OSError` 都不接"
推得，那一格没单独跑过（第一、二行的修前形状是 01:48:52Z 那次实测，写侧的由 U1 在测试里复现）。
修法和 `#50` 同形——一处，不是四处：`main()` 里 `except OSError` → 一行 stderr 加 rc 2，
`str(e)` 自带 `[Errno N] 理由: '路径'`，不重抄一遍 errno 表。

代价也说清：`OSError` 是个大类，代码自己把路径拼错（某处 `open` 拿到一个不该拿到的名字）从此也长成
"路径用不了"这一句，不再留下栈可看。收下它是因为两个码不够用——把 IO 失败单列一个 rc 3 会让每个
脚本多一条分支，而这一句里带着 errno 和路径，且 **rc 2 ≠ rc 0**：一次崩溃不会被读成一份结论。
要栈的话直接调 `cmd_audit(...)` 那一层——那条路不经过 `main()`，handler 拦不到。

为什么表里第二行值得单独占一行：**只接 `FileNotFoundError` 的 handler 看着像修完了**，姊妹类照旧顶穿，
而"文件不存在"那四条参数会全绿。这是 U5 当场演示的，不是设想——所以四个出口点名点了两遍（少一遍
就少一具靶子）。

5 具变异照 `/tmp/mut_paths.py`（2026-09-22T01:58:31Z 对着最终字节，5/5 与预期一致、
`src/wolfengine/cli.py` 按字节还原）：

| 变异 | 红用例 | 红的类型 |
| --- | --- | --- |
| U1 拿掉 handler | 修前那 10 条一起红（9 行为 + 1 结构） | 行为 |
| U2 退出码 2 → 0 | 9 条（`assert 0 == 2`） | 行为 |
| U3 印到 stdout | 9 条（`err` 是空串） | 行为 |
| U4 `export` 自己再接一遍 | 只有结构那条（`except OSError` 数到 2） | **结构** |
| U5 只接 `FileNotFoundError` | 4 条目录 + 写侧那条 + 结构那条 | 行为 |

U4 与 `#50` 的 T4 同一类，红在结构上不算测试强度（`#48` 的 H3 那条分界）。另外两套
（`tests/test_cli.py`、`tests/test_render_live.py`）五具全 SURVIVED，其中 `test_compare_on_a_directory_without_a_manifest_is_a_usage_error` 那条
`compare <不存在的目录>` 拿到 rc 2 走的是 manifest 检查那只手、不经过这条腿——**"另一个地方也返回 2"
不是这条 handler 的证据**，这正是四个出口要逐个点名的理由。

重跑这一片：`.venv/bin/python /tmp/mut_paths.py`，或只看终端那一屏——
`PYTHONPATH=src .venv/bin/python -m wolfengine.cli audit data`（一行，rc=2）。

#### 里面什么都没有的那个文件，在三个出口前是沉默的

`#50`/`#51` 管的是"读不下去"。这一格相反：**读得动，但里面什么都没有**。`read_split` 对一份没有
manifest 的文件返回 `({}, [], …)` 而不是报错——那是有意的（只接受自己输出的解析器没法检查自己的输出），
缺的只是把这件事**说给人听**的那一句。2026-09-22T02:11:57Z 逐个数过四个出口，三个人给的出口都把
"我认不出这是哪一局"演成了"这一局发生过"：

| 出口 | 修后终端上（2026-09-22T02:38:54Z 实测，都还是 rc 0） | 修前缺的是哪一半 |
| --- | --- | --- |
| `replay /dev/null` | `〔这个文件没有开局记录（manifest 那一行）：说不出它是哪一局〕` | 一个字都不印（V1 复现：`转录一个字都没印：''`） |
| `watch --once /dev/null` | 页眉 `狼人杀直播 — · 第1天 · 视角：观众`，下一行就是那句话 | 那句解释没有（V1 红在直播那条）。名槽那格修前是**两个空格**：`狼人杀直播  · 第1天`——这一串 `#48` 那节实测过（`docs/views.md:96`），同一处插值、同一格空着，本轮只是给它配了一句解释 |
| `export /dev/null` | 页眉 `第0天结束 · 未结束 · 发言0条 · … · ⚠ 这个文件没有开局记录…` | ⚠ 那半句没有，"第0天结束 · 未结束"照旧——它本来就在，这格修前也照样报天数 |
| `audit /dev/null` | `"game_id": null` | 机器出口本来就说实话（`null` 是个字段），所以这一轮没动它 |

退出码**故意留 0**：这不是失败，这份文件的结论就是"没有可看的"，而 2 在 #50/#51 之后已经归给"命令
本身不对"。用例把这件事钉在断言里（`assert cli.main(["replay", str(path)]) == 0, "读得动的空文件不是
失败，别改退出码"`），因为最顺手"修好"它的方式是让空文件报错——那是把一句真话换成一次拒答。

判据住在 `events.py` 的 `meta_notice(meta)`，和 `torn_notice` 一个待遇，三个出口各调一次。转录里两句
排成同一个循环（`for notice in (meta_notice(meta), torn_notice(torn))`）：两种"没有可看的"是同一类
事实，各写一遍迟早长成两种措辞。两句话刻意不同（"没有开局记录" vs "…之后没有记录"），因为它们说的
是两件事，而复盘页眉把两句拼在同一条 `⚠` 上——`test_the_html_page_says_the_file_was_cut` 里那句
"页里那句和转录那句一字不差"正好卡住拼接的顺序。

5 具变异照 `/tmp/mut_notice.py`（2026-09-22T02:29:10Z **一次跑完五具**、对着最终字节，
`events.py` / `cli.py` / `render_html.py` / `render_live.py` 四只文件按字节还原）：

| 变异 | 红用例 | 红的类型 |
| --- | --- | --- |
| V1 判据永假（回到修前） | 三条出口各一条：转录、页面、页眉 | 行为 |
| V2 去掉 guard（通知永远亮） | 本文件的反向用例 + 页眉/转录那句对比那条 + `tests/test_cli.py` 的 `test_replay_needs_nothing_but_the_file` | 行为 |
| V3 直播出口自己抄一遍这句话 | 只有"单一只"那条（字面量数到两个文件） | **结构** |
| V4 页面把 `unnamed` 写成 `cut` | HTML 那条（"页面把空文件当成了一局正常结束的牌局"） | 行为 |
| V5 转录的循环里少接一句 | replay 那条（另三套全 SURVIVED） | 行为 |

V2 是本轮唯一跑前把预期写错的地方，值得单独记：我原先声明"其余三套全 SURVIVED，因为它们的夹具都带
manifest"——那是凭空假设，方向也错了（抽掉 guard 之后通知对**任何**文件都亮，与有没有 manifest 无关）。
`test_replay_needs_nothing_but_the_file` 的夹具确实带 manifest（它自己断言 `events[0]["seq"] == 0`），
它红的是另一件事：这句话是**追加成转录最后一行**的，而那条用例拿 `lines[-1]` 当"最后一行是终局判定"
的锚。规矩由此留下：**往尾部加一句话，就会把所有读 `lines[-1]` 的用例卷进来**——这是设计的代价，不是
那套测试写坏了。改的是声明，不是测试。同一次跑里 V5 第一跑 BROKEN-IMPORT（替换串丢了 `for` 行末尾的
冒号，BROKEN 不算 CAUGHT），补上之后与 V2 一起重跑，才成上表。

一句"这格为什么留白"的账：`render_live` 逐帧刷新不打印**撕裂**那句（它的 `_read` 丢掉 `_torn`）。那不
是没发现的沉默分支，是写进测试的决定——`tests/test_render_live.py` 开头那条性质与
`test_a_torn_last_line_is_skipped_not_fatal`；直播要说"没有开局记录"（这一轮补上），因为那是"这一屏
说的是哪一局"，而撕裂不用说，因为末行本来就在长。

重跑这一片：`.venv/bin/python /tmp/mut_notice.py`，或只看终端那一屏——
`PYTHONPATH=src .venv/bin/python -m wolfengine.cli replay /dev/null`（一行，rc=0）。

#### 只有开局记录的那一种空：上一轮的判据在这里是反的

`#52` 那句管的是"文件里没有 manifest"。同一格边缘上还留着相反的一种：**manifest 在，后面一条事件
都没有**。2026-09-22T02:51:43Z 实测（`/tmp/probe_seq.py` 的 G 例）：`replay` 对着这样的文件印出
**一个空行**、退出码 0；导出的页面写"第0天结束 · 未结束"；直播页眉带着真名字 `g-stub` 立在一片空板面
上面。三处都没有说话，而 `#52` 的那句这次**不该**说话——`game_id` 明明在。

这一种空不是手改文件的产物，是这个仓库现在每天的形状：`write_meta()` 在第一条事件之前就落盘了，
所以端点在第一次调用里挂掉的局，留在磁盘上的就是这几行字节。机器侧早就说了（没有 `GAME_OVER` 的半局
被 `test_m1_keeps_an_unfinished_game_out_of_the_denominator` 挡在分母外面，`audit` 印 `events: 0`），
本轮补的仍然是"给人看的那三个出口"这一半。

第三只手：`events.py` 的 `empty_notice(events, meta)`，判据是"有开局记录且一条事件都没有"。它和
第一句**不会同时亮**——两条用例一边一句钉住这个不变式（`test_an_empty_file_gets_one_sentence_not_two`
钉"没 manifest 的文件只说第一句"，`test_a_file_that_only_reached_the_opening_record_says_so` 的最后一行
钉反方向）。三句话当时是三件不同的事：认不出是哪一局 / 这一局没记下来 / 末尾被砍了一行；下一小节
给这个家族添了第四句，管的是另一件事（编号破损＝这不是一局写出来的），它和前三句**可以**同时出现，
因为说的不是同一个毛病。**"和三句话"那半句后来被自己写的文档推翻了一次**：`docs/views.md` 当时把三句
一起写成"不会同时亮"，而 2026-09-22T04:00:41Z 实测（manifest + 一条写了一半的行）说的是两句——
"这一局只有开局记录"和"日志在这里截断"同时亮，转录、页面都是。互斥的只有前两句，第三句讲的是末尾、
不是"有没有内容"。这句散文从写下到被推翻不到一小时（`#53` 的表跑完是 03:08:36Z，实测打脸是 04:00:41Z），
修法是把它改成断言在读：
`test_a_cut_file_that_never_got_past_the_opening_record_says_both`，并给它配了两具只打顺序的变异
（S13/S14，见下面那张表）。

`draw` 里那句"读的是 `events` 不是 `evs`"一开始只是注释：观众屏可以因为可见性而空，那时候说"这一局
只有开局记录"是**假话**。注释不算证据——没有断言读的分支就是假装存在的分支，所以补了
`test_a_frame_with_nothing_visible_does_not_claim_the_file_is_empty`（一局只留一条狼队密语的日志，
观众模式下这一屏是空的，两句都不许出现）。W3 就是照着这格打的：把 `events` 换成 `evs`，全套只有
这条新用例红。

5 具变异照 `/tmp/mut_empty.py`（2026-09-22T03:08:36Z 一次跑完、对着最终字节，四只文件按字节还原，
5/5 与预期一致）：

| 变异 | 红用例 | 红的类型 |
| --- | --- | --- |
| W1 判据永假（回到修前） | 转录、页面、页眉各一条 | 行为 |
| W2 去掉 guard（永远亮） | 3 条反向/不变式 + 页眉与转录那句的对比那条 + `test_replay_needs_nothing_but_the_file` | 行为 |
| W3 直播读 `evs` 而不是 `events` | 只有"观众屏不许冒充空文件"那条 | 行为 |
| W4 页面少接一句 | HTML 那条 | 行为 |
| W5 转录的循环里少接一句 | 转录那条 | 行为 |

W2 那格 set1/set2 的 SURVIVED 是**看过断言**声明的（上一轮 V2 在这里凭空猜错，改的是声明不是测试）：
`grep "⚠|lines\[-1\]|not in page"` 在这两套里只命中 `tests/test_cli.py` 的上面那条，而
`test_render_live.py:126/139` 数的是夹具行数与自报文本条数、212/216 按 `正在：` 过滤，多印一行动不了
它们。同一轮也要记下没做的：**这一轮没有一具去打"单一只"那条结构断言**
（`test_the_recorded_nothing_sentence_has_one_owner` 在本表里五具全绿），它的形状与 `#52` 的 V3 相同、
由那一具代过，但它自己这一轮没有靶子，别把它的强度算进上表。

重跑这一片：`.venv/bin/python /tmp/mut_empty.py`，或只看终端那一屏——
`PYTHONPATH=src .venv/bin/python -m wolfengine.cli replay /tmp/probe_seq_G.jsonl`（一行，rc=0；
那个文件由 `/tmp/probe_seq.py` 生成）。

#### 编号破损的文件：三句"没得看"之外，还有一句"这不是一局"

同一份探针（`/tmp/probe_seq.py`，2026-09-22T03:20:16Z）里剩下的四格是另一类东西：文件读得动、事件
一条不少，但**编号不是 1,2,3**。B 中间少一行（`[e1] [e3] [e4]`）、C 同一 seq 两次（`[e2]` 出现两遍）、
D 整份倒过来（`[e4]` 开头）、F 两份日志 `cat` 到一起。四格在 `replay`、`export`、`watch` 三个出口
全部沉默，`audit` 连一个编号读数都没有。

F 最尖锐，而且它不是"少说一句"，是**多说了一句假话**：读取侧遇到 `seq==0` 那行就
`meta = dict(...)`，后一条开局记录静默覆盖前一条，于是 `cat` 出来的文件导出页眉只报第二局的局号——
实测 `/tmp/probe_seq_F.html` 里 `g-two` 出现 2 次、`g-probe` 0 次，而文件前两条事件属于 `g-probe`。

两档 severities 的分界线不在我的口味上，在写入侧已有的契约上：`write_meta` 早就拒绝第二次调用，
理由写在它自己的 docstring 里（两局共享一个文件 id 会把两套编号不可逆地交错）。所以

* **两条开局记录** → `LogDamage`，到终端是一行报告加退出码 2。文件里没有"一局"可看，选哪一局的
  局号写页眉都是撒谎；
* **缺号 / 重号 / 顺序倒挂** → 一句话，退出码 0、内容照给。每一行都读得动，缺的是说明，不是拒绝。

**不排序、不"修复"**：印出来的锚点序列必须是文件自己的序列。替它重排之后，读者拿着 `[e4] → [e3]`
的原件对上一份 `[e1] → [e2]` 的输出，就再也分不出"文件被修过"和"渲染器在编"。

第四只手：`events.py` 的 `seq_damage(events)` 出三个各自独立的计数（缺号按 min..max 之间缺席的编号数、
重号按出现两次以上的 seq 个数、倒挂按相邻变小的次数），`seq_notice` 只是把非零的那几项拼成一句。
三个计数互不合并是有意的：手删中间一行和手抄多一份是两个事故，一个"坏了/没坏"的标志位会把是哪个
抹掉。`audit` 这次也得改——它是唯一从来不说的那一面，于是它印 `seq_damage` 那个 dict 本身，
和 `replay` 那句共用同一次算术。顺带记下一条实测：C 例（`1,2,2,4`）说的是"缺号 1 处、重号 1 个"，
两句同时成立不是判据打架，3 号那条事件**真的**不在这个文件里。

15 具变异照 `/tmp/mut_seq.py`（2026-09-22T04:12:10Z 一次跑完、对着最终字节，四只文件按字节还原，
15/15 与预期一致；同一份脚本 12 具时是 03:55:49Z 跑的，S13–S15 是后面那两条新用例带来的）：

| 变异 | 红用例 | 红的类型 |
| --- | --- | --- |
| S1 缺号恒为 0 | 转录"缺号 1 处"那条 + audit 那条 | 行为 |
| S2 每个不同 seq 都算重号 | 5 条（含页眉与转录对比那条）+ `test_replay_needs_nothing_but_the_file` | 行为 |
| S3 重号被也算成倒挂 | 只有整句相等那条（`[0,1,2,2,3]` 那格） | 行为 |
| S4 去掉 guard（永远亮） | 干净文件的反向对照 + 页眉对比那条 + `test_replay_needs_nothing_but_the_file` | 行为 |
| S5 `manifests > 2`（两局不再拒绝） | 两条两局用例（`DID NOT RAISE` / `assert 0 == 2`） | 行为 |
| S6 转录少接一句 | 三条 parametrize + 页面与转录那句的对比 | 行为 |
| S7 页面少接一句 | HTML 那条 | 行为 |
| S8 直播少接一句 | 页眉那条 | 行为 |
| S9 audit 那格写死成三个 0 | audit 那条 | 行为 |
| S10 字面量被抄进 `render_live.py` | 只有"这句话只有一个 owner"那条 | 结构 |
| S11 转录里编号句与截断句对调 | 只有"两句同时亮"那条（它钉 `tail[0]` 是整句编号句） | 行为 |
| S12 页面里两者对调 | 同一条的后半（页眉里两个 ⚠ 必须相邻且同序） | 行为 |
| S13 转录里"没记下来"与"截断"对调 | 新那条（`test_a_cut_file_that_never_got_past_the_opening_record_says_both`）+ 编号句同亮那条 | 行为 |
| S14 页面里两者对调 | 同上两条（各自读页眉的那半） | 行为 |
| S15 `seq_damage` 的空列表 guard 摘掉 | set0 红（`ValueError: min() iterable argument is empty`，具名列表被脚本截到 6 条）；那条空列表用例单独跑过一遍，红在同一条 `E ` | 行为 |

四点要记下。其一，S10 是上一轮欠的账：`#53` 那张表里"单一只"那条结构断言五具全绿，等于它的强度
没被证明过，这一轮专门造了一具只动字面量、行为一字不变的靶子去打它。其二，parametrize 那三条钉的是
**整句相等**（`lines[-1] == "〔…：重号 1 个〕"`）而不是"含这段"，S3 才因此有得红——只断言含片段的话，
多报一项、少报一项都照样绿，那句话就没分量了。其三，S11–S14 打的格子前几轮谁都没钉：四句话各自
"在不在"有断言，**同时亮时谁先说**没有——所以这四具只有在同时带两句的输入上才读得出区别，而那两条
用例就是为它们写的（先把顺序写进断言，再造打顺序的变异）。写那条"两句同亮"的用例时顺带撞出一个
发现：`docs/views.md` 说"没得看的三句话不会同时亮"，实测（04:00:41Z）是"没记下来"+"截断"同亮——
文档在那格上写反了；同亮这一事实由那条用例自己钉，S13/S14 钉的是它俩的先后。set1/set2 的 SURVIVED
依旧是看过断言说的：
`grep "⚠"` 在 `test_render_html.py`、`test_render_live.py` 里 0 命中，而那两个文件的计数式断言数的都是
具体子串（`✕`、`<td`、`〔不可能感知〕`、`〔拒绝1次〕`），多印一行〔…〕动不了它们。set3 对 S11–S14
也 SURVIVED，读的是 `tests/test_cli.py` 的 `test_replay_needs_nothing_but_the_file`：金样本一局编号连续、末尾完好，两句都是空串，对调空串的
顺序不改变任何一行输出。其四，S15 打的是新那条空列表用例的强度：`views.md` 那句"直播的两句构造上
不会同时亮"靠 `seq_damage` 里 `if seqs else 0` 撑着，而写用例之前没有任何断言喂过空列表——摘掉 guard
后 set1/2/3 三套全绿（它们的夹具都是完整一局，`grep "_stub_log|read_split"` 在那三个测试文件里 0 命中），
只有 set0 红，这条才是那格被钉住的证据。

重跑这一片：`.venv/bin/python /tmp/mut_seq.py`，或看那四格现在说什么——
`PYTHONPATH=src .venv/bin/python -m wolfengine.cli replay /tmp/probe_seq_D.jsonl`（末尾多一句"顺序倒挂
3 处"，rc=0）与 `... replay /tmp/probe_seq_F.jsonl`（一行"这个文件里混进了两局"，rc=2）；两个文件都由
`/tmp/probe_seq.py` 生成。

#### 编号破损走进了批次目录：三个出口会说话，进结论的那一条一句没有

上一小节钉的是**给人看的出口**。修前状态是两探针实测的（2026-09-22T04:17:49Z / 04:18:00Z）：把两局
日志 `cat` 到一起，批次侧和读取侧口径一致地拒收（`read_arm`、`read_game` 一路抛 `LogDamage`，说得出
第几行）；把其中一局改掉一个编号，`read_arm` 照样列出它、`read_game` 照样给得出 `terminal`、
`game_rates` 分子分母算得妥妥当当——**没有一个字段、没有一行报告说这份文件的锚点破了**。同一份文件
拿到 `replay` 前面却多一句〔这份日志的编号不是连续递增的：缺号 1 处、重号 1 个〕（04:32:36Z 又跑了
一遍，还在）。

也就是说：`#54` 修完之后，破损只剩一条通路是沉默的，而那条恰恰是**进结论的那一条**。批次报告里每个
数字都会被引用成 `[e77]`（转录、信念报告、`comparison.md` 自己都用这个地址），一份有洞或有重号的
日志就让那些地址悄悄含糊或悄悄不存在，而报告继续若无其事地往下算。

补的是三处，一只手都没有多：

* `metrics.Game.seq_damage` —— 身体就一行 `return seq_damage(self.events)`，转交 `events.py` 那套
  算术。批次的数和转录那句措辞因此出自同一次计算。
* `batch.numbering_damage(games)` —— 归约：`n` 按**文件**计，缺号 / 重号 / 倒挂三类各自合计，外加
  `files`。键名从 `events.DAMAGE_WORDS` 推，不在批次里再抄一份名单。
* `compare` 里那一格 + 报告里 `## 编号破损` 那一节 —— 每臂一行，点名。

**点文件名而不是点 `game_id`**，理由一度被我写错、被实测改回来了（04:33:11Z）：我以为"两臂同 seed 的
`game_id` 相同、换成文件名就分得开"，实测是**文件名也一样**（`A/` 和 `B/` 下都是
`20260922T043229Z_g00000005.jsonl`，分开两臂的从来是目录名）。成立的理由是另一条：`game_id` 住在文件
里面，而这一格要报的恰恰是"这个文件被人改过"——拿被改对象内部的标签去指它，正是 `#54` 那个"两局拼在
一起、页眉照着后一条 manifest 报错局号"的形状。文件名另外带着这一次运行的 utc，指得动"是哪一批里
哪一个字节"。

**只报数，不删局。** 分母是预注册协议（plan §8）在跑之前就定下的东西：剔除如果跟着结果走，同一批
数据就能被算出两个方向的结论。所以那一节的收尾句写死了这件事，而且它必须是**这一节自己的**一句
（"这些局没有被从任何分母里拿走：编号是文件的问题，不是这一局算不算数的问题"）——测试若只断言
"不剔除"三个字，退化局那一节永远印着它，编号这一节整节删掉都照样绿。

8 具变异照 `/tmp/mut_batch55.py`（2026-09-22T04:31:07Z 一次跑完，`batch.py` 与 `metrics.py` 跑完
`cmp` 按字节还原，8/8 与预期一致）：

| 变异 | 红用例 | 红的类型 |
| --- | --- | --- |
| N1 `Game.seq_damage` 恒为 0 | set0 三条全红 + set1 那条结构用例（逐字比 property 身体） | 行为 + 结构 |
| N2 只有"重号"才算破损 | 只有归约那条（夹具第二局只有倒挂，被漏掉：`n` 2→1） | 行为 |
| N3 `files` 换成 `game_id` | 归约那条 + 报告那条（`name in md` 找不到文件名） | 行为 |
| N4 报告里删掉那截括号 | 报告那条的 `assert "缺号 1 处" in md and "重号 1 个" in md` | 行为 |
| N5 `compare` 里整节不接 | 报告那条的 `"## 编号破损" in md`（dict 腿照样绿） | 行为 |
| N6 `compare` 里两臂对调 | 报告那条（A 臂期望 `n=1`、拿到全 0） | 行为 |
| N7 `n` 数的是损伤处数 | 归约那条（3 vs 2）+ 报告那条 | 行为 |
| N8 整套算术抄进 property（行为一字不变） | **只有** set1 那条结构用例 | 结构 |

四点要记下。其一，N8 是这一轮唯一有结构价值的靶子：它与真身行为一字不差，set0/2/3 三套全绿，只有
逐字比 property 身体的那条腿红——这证明那条腿不是装饰。而那条结构用例自己先以另一种形状红过一次：
`"def seq_damage(" in text` 把 `Game.seq_damage` 也当成了第二套算术（`['events.py', 'metrics.py']`），
所以"只有一个 owner"的判据必须区分**算术**和**转交**。其二，N4/N5 存在的理由是修测试而不是修代码：
最初那条报告用例写的是 `assert "不剔除" in md`，而这一句退化局那一节永远印着——那是一具打不中任何
东西的空腿，先把断言换成编号这一节自己的话，再造打它的两具。set2/set3 对 N2–N7 全 SURVIVED 也是
看过断言说的（04:28:19Z `grep "\.seq_damage|numbering"`：那两个文件里出现的名字全是
`events.seq_damage` 与 audit 自己那一格，没有一条读批次报告）——算术是一只手，报告是两个各说各话的
出口，谁也不比对谁的措辞，这一格今天仍然没人钉。其三，明确**不打**的一具写在脚本头部：把
`numbering_damage` 里从 `DAMAGE_WORDS` 推键名换成写死那三个键名。它在今天与真身等价，差别只在
"`DAMAGE_WORDS` 加第四类"时才出现，而那要同时改两个文件才造得出来（harness 一具只动一个文件）。
归约那条用例末尾留了一条预备腿（`set(numbering_damage([])) == {"n", "files"} | set(events.seq_damage([]))`），
但它**没有**变异证据，不算进"钉住"。其四，这一节的下方那份复现命令自己把文档闸门红了一次（04:36:14Z）：
我写的是"把 A 臂第一局里 `seq` 为 6 的那一条改成 7"，而 `test_a_stated_default_value_matches_the_literal_in_src`
认的是 `` `键=整数` `` 这一种形状——它把这句话读成了"`seq` 的出厂值是 6"（`src/` 里是 0）并报红。改的是
**散文**不是判据（换成"编号为 6 的那一条"），理由和 #45 当年给臂值消歧是同一条：这一种形状在文档里已经
被占用为"报一个出厂值"，用它报"某一条记录的编号"就是在借用一个有主的名字，下一次真要写 `seq` 的出厂值时
就分不出哪句是主张了。（这一段写完又红了一次，04:37:39Z：把那个形状**引**在句子里和**主张**它在判据
眼里长一个样——所以这里只能用"`seq` 为 6"这种写法转述，没有例外通道。）

重跑这一片：`.venv/bin/python /tmp/mut_batch55.py`；或看那两句话现在说不说同一件事——

```bash
PYTHONPATH=src .venv/bin/python -m wolfengine.cli replay /tmp/n55b/A/20260922T043229Z_g00000005.jsonl | tail -1
# 〔这份日志的编号不是连续递增的：缺号 1 处、重号 1 个〕                            （04:32:36Z）
PYTHONPATH=src .venv/bin/python -c "from wolfengine import batch;print(batch.compare('/tmp/n55b',axis=('temperature',))['numbering'])"
# {'A': {'n': 1, 'files': ['20260922T043229Z_g00000005.jsonl'], 'gaps': 1, 'duplicates': 1, …}} （04:32:29Z）
```

`/tmp/n55b` 用 `tests/test_batch_paired.py` 里那三只手造：`_paired`（2 局 × 2 臂替身桌）→
`_as_real_table`（把 `actor_kinds` 那一格改成真桌标签）→ `_damage_seq`（把 A 臂第一局里编号为 6
的那一条就地改成 7，于是同时缺一个号、多一个重号）。全程离线。

#### 同一份被砍的日志，批次报告以前说"这局没打完"

上一片之后，编号破损在批次里有了读数，末行截断没有——而它比编号更容易和"没有赢家"撞在一起。一局
**打完了**的日志，末行就是 `game_over`，`game_over` 带着 `terminal`；把那一行砍掉半行，读取侧照旧
容忍（只宽一行），可 `terminal` 从 `good_win` 变成 `unfinished`（实测 04:40:29Z，同一份文件砍前砍后
各读一遍）。批次只读 `terminal`，于是报告里那一行是这样印出去的：

```
- usable 对 1/2，被丢弃 1 对（平局 0、中断或未完 1：没有赢家的局不进分母，两种原因分开数）
```

这一句是从 K2 那具变异的红输出里抓下来的原样（05:01:04Z），而那一具的动作恰好是"把这次补上的那截
改口删掉"——所以它就是修前状态。**文件被人生砍过**被写成了**模型的行为**。这和 `#39`（端点拒答不许
冒充弃答）、`#47`（把 prompt 转储读成日志时，崩出来的是 KeyError 而不是一句说清文件的报告）是同一族错：
环境或文件的条件印成了被评测对象的性质，而 `[e77]` 那一类引用会把这句话当成批次的自述继续往下引。

补的还是三处，一只手都没有多：

* `batch.truncated_tails(games)` —— 归约。第一行就是 `per = [g.torn_extent for g in games]`，
  键名 `n` / `files` / `lines` / `chars`，`n` 按**文件**计。批次不 import `events` 的算术，只经
  `Game.torn_extent` 拿数，所以这里的字节数和 `replay` 那句话里的是同一次计算。
* `win["n_dropped_torn"]` 与 `win["dropped_torn_files"]` —— 只在"这一对被丢弃"那个分支里数：两臂
  里任何一臂的 `torn_extent` 有行，这一对就算（`kill -9`、盘满、同步中途被砍都是这一类）。
* 那一行的改口 + `## 末行截断（只点名不剔除）` —— 改口写在**被丢弃那一句里**，不推到两节之后：读者
  是先在这里看到"没打完"的。那一节每臂一行，点文件名、带行数与字节数，原文一个字不印（被砍的半行
  可能是狼聊）。

**只给理由，不开新桶。** 预注册的分母（plan §8）一个都不许动，断言就写在四条上：`n_dropped` 仍为 1、
`n_dropped_other` 仍为 1、`n_pairs` 仍为 2、`m1` 的 A 臂仍为 2 局。K9 那具把
`n_dropped -= len(torn)` 注进去——"被砍"看着更该有自己的桶，也更容易被引用——红在
`test_a_cut_last_line_is_blamed_on_the_file_and_not_on_the_model` 自己那句 message（"不许新开一个桶：
它本来就是『没有赢家』的一种"）。

9 具变异照 `/tmp/mut_batch56.py`（2026-09-22T05:00:06Z 起跑、05:02:02Z 四个被改文件 `cmp` 逐字节
还原，9/9 与预期一致；`batch.py`、`metrics.py`、`events.py`、`cli.py` 各挨过）：

| 变异 | 红用例 | 红的类型 |
| --- | --- | --- |
| K1 `truncated_tails` 的 `n` 恒为 0 | set0 两条：归约那条 + 报告那条（`files`/`lines` 都还在，只有 `n` 说 0） | 行为 |
| K2 被丢弃那一行不改口 | 报告那条的 `"其中 1 对的日志末行被砍"`（那一节照旧印着，dict 腿全绿） | 行为 |
| K3 报告里那一节不接 | 报告那条的 section-local 腿（`sec` 空了：`assert 0 == 1`） | 行为 |
| K4 那一节只报数不点名 | 报告那条同一腿的 `name in sec[0]` | 行为 |
| K5 那一节的字节数写死 0 | 报告那条同一节里的 `"70 字节"`（对 `0 字节`） | 行为 |
| K6 整套算术抄进 property（行为一字不变） | **只有** set1 那条结构用例，红在逐字比 property 身体 | 结构 |
| K7 `cli` 那一格退回自己数行数字节数（行为一字不变） | **只有** set1 那条，红在 `touched` | 结构 |
| K8 `torn_notice` 的字节数差一 | **只有** set2 那条转录用例（209 对 208） | 行为 |
| K9 "被砍"新开一个桶 | 报告那条，红在它自己的 message | 行为 |

三点要记下。其一，K7 的红输出给出的正是 `#56` 之前的形状：碰到被砍原文的地方从 `cli.py` 一处变回三处
（那句 `is not` 之外还自己数了一遍行数和字节数），而它的**行为**与真身一字不差——`_audit` 那一格数得
对，所以 set2/set3 全绿，只能按"谁能碰原文"来查。这就是
`test_the_truncation_extent_has_one_arithmetic_and_three_readers` 存在的理由。其二，K4/K5 是**修测试**
修出来的两具：最初的报告用例写 `name in md`，而丢弃句和那一节两处都印文件名，删掉任意一处都还绿；
先把断言换成那一节自己的行，再造这两具（`#55` 的 N4/N5 是同一课的第二次）。其三，K8 只有 set2 红是
量过的，不是推断：04:56:11Z `grep -n torn tests/*.py` 里 set3 只有一个断键集合的出现点（第 274 行），
04:57:08Z `grep -n torn_notice src/wolfengine/batch.py` 只命中第 363 行的 docstring——批次那一侧从来
不说这句话。批次读数和转录措辞今天是同一只手、两个出口，谁也不比对谁的数，这一格仍然没人钉。

明确**不打**的一具写在脚本头部：把 `truncated_tails` 的 `n` 从"几个文件"改成"几行"。它在今天与真身
等价（末行只容忍一行，`n == lines` 恒成立），差别只在容忍被放宽时才出现。归约那条用例末尾留了一条
预备腿（`a2["n"] == a2["lines"]`），但它**没有**变异证据，不算进"钉住"。

重跑这一片：`.venv/bin/python /tmp/mut_batch56.py`；或看那两个出口现在是不是同一个数——

```bash
.venv/bin/python /tmp/n56.py     # 造 /tmp/n56b 并打印批次读数 + replay/audit 那两句（离线，约 20s）
# - usable 对 1/2，被丢弃 1 对（…；其中 1 对的日志末行被砍，不是这局没打完：A/20260922T050406Z_g00000005.jsonl)
# - A：1 局的末行没能读成事件（1 行、70 字节）：20260922T050406Z_g00000005.jsonl   （05:04:06Z）
# 〔日志在这里截断：末 1 行、70 字节没能读成事件，这一局在这一行之后没有记录〕        （同一次运行）
```

`/tmp/n56b` 还是那三只手：`_paired` → `_as_real_table` → `_cut_tail`（把 A 臂第一局的末行只留下前
70 个字节）。全程离线，`torn` 那一格里 70 这个数在批次、`replay`、`audit` 三处出现的是同一次计算。

#### 一局打完了、退出码却是 1：收尾时在第二个 event loop 里关 client

这一片不是读代码读出来的，是端点开之后第一次真跑撞出来的。前 56 片都在钉"端点说不之后我这侧怎么
记"，而这一片的缺陷在**记完之后**：`httpx.AsyncClient` 把 socket 绑在打开它的那个 loop 上，修前的
`cmd_run` 却是`asyncio.run(_run_many(...))` 跑完一局、再在 `finally` 里另起一个 `asyncio.run`
去 `transport.aclose()`——第二个 loop 往已经关掉的 loop 里 `call_soon`，抛 `RuntimeError: Event loop
is closed`。而它在 `finally` 里，所以它**顶掉**了 `return rc`。三个读数，实测的：05:19:13Z 真端点
一次拒答，stdout 那句 `aborted_endpoint` 是对的、退出码也是 1，但那个 1 是 traceback 给的，不是判据
给的（stderr 54 行）；05:23:33Z 桩上一局**打完了**的局（`draw_day_limit`、60 个事件）返回 1；
05:25:14Z 批次连 `批次 -> …` 那一行都没有了——它印在 `finally` 后面，永远到不了。后两条才是真损伤：
`docs/comparison.md` 第 38 行把 0/1/2 写成了契约（1 是"拒绝出结论"），一次拼错的文件名刚刚在
`#51` 里被从 1 手上拿走，一个**成功**的批次却由崩溃把它按回 1 上。脚本和无头评测读的就是这一格。

修的是"在哪个 loop 关"，不是"要不要关"：`cli._run_and_close`（`cli.py:71`）让请求和 `aclose()`
共用同一个 loop，`finally` 留着，两个读者各改一处（`cli.py:151` 的 `run`、`cli.py:522` 的
`batch`）。修后拿真端点复验过一次（05:29:08Z）：退出码 1、stderr 0 行、stdout 那一句一模一样，
`/tmp/live401` 那份 14 行日志的末行还是 `terminal=aborted_endpoint` 且 `fallback` 兜底 0 次——
这一轮**没有**动归因，那是 `#39` 已经钉住的部分，它继续绿着才是这次复验的意义。

测试只能起真 socket、跑真子进程：`tests/test_loopback_endpoint.py`（四条）。前三条用
`sys.executable -c` 跑真 `cli.main`，因为缺陷住在"进程退出码 + 谁印了 stderr"这一层，进程内调用
`cli.main` 抓不到它（`rc` 被 `SystemExit` 接住了）。桩住在 127.0.0.1，回的是真形状的两种答：401
的 new-api 错误体，和 200 但内容不合法。`network` 标记照旧不挂——这些字节没离开这台机器，plan §11
那句"离线"指的是那台私有端点。桩里那 20ms 定长 sleep 不是装饰：canary 的 `latency_ratio` 是头尾两次
探针对同一个端的比，没有延迟地板时它比的是建连抖动（05:25Z 那版实测 0.50，直接 `INVALID_DRIFT`，
阈值 1.5）；加了地板之后测到 1.056。反过来也没有把任何延迟写进断言——那会把时间读数钉成套件的一部分。
第四条 `test_the_client_is_closed_even_when_the_run_raises` 不起子进程，直接问那只手：协程抛
`RuntimeError` 之后仍要 `closed == [1]`。

6 具变异照 `/tmp/mut_batch57.py`（05:44:34Z 起跑、05:46:07Z 收尾，输出在 `/tmp/mut57b.out`，
两套 preflight 各自全绿，6/6 与预期一致，跑完 `cmp` 整文件字节还原；只改 `cli.py` 一个文件）：

| 变异 | 红用例 | 说明 |
| --- | --- | --- |
| M1 `cmd_run` 退回第二个 loop 关闭 | set0 两条：拒答那条 + 打完的那条，红在 `assert r.stderr == ""`（输出里第一条 `E` 就是 traceback 首行） | set1 全绿：mock 路径 `transport is None`，那一格根本不执行 |
| M2 `cmd_batch` 退回第二个 loop 关闭 | set0 只有批次那条 | **只修一处不会全绿**——这一具就是"两个读者各要一条用例"的理由 |
| M3 去掉 `finally` | set0 只有抛异常那条（`assert [] == [1]`） | 成功路径一字不变，所以三条子进程用例全绿：它们跑的都不抛异常的局 |
| M4 去掉 `transport is not None` 守卫 | set0 **SURVIVED**，set1 红四条，全是 `AttributeError: 'NoneType' object has no attribute 'aclose'` | 四条红名字看着不相干，它们其实只有一个红：`tests/test_cli.py` 的模块级 `played` fixture 挂了。"成片"是被 `reds()` 截到 6 条以内的有界说法 |
| M5 同一个 loop 但写成嵌套 `asyncio.run` | set0 四条全红（三条 traceback，第四条是 `pytest.raises` 的 message 没匹配上） | set1 绿（守卫在前）。这一具证明三条红问的不是**有没有**叫 `aclose`，是**怎么**叫 |
| M6 跑完不看返回值、恒 `return 0` | set0 红两条：拒答那条（退出码腿）+ 批次那条；set1 也红 | `asyncio.run(body())` 换成丢掉返回值，崩溃就不发生了，于是"rc 是读数"这一格没人钉 |

M6 的预期要披露一次写错：05:33:37Z 那版（`/tmp/mut57.out` 第 10 行）把 set1 写成"预期 SURVIVED"，
脚本报 `WRONG`。我漏读的是——`cmd_batch` 拿走的不只是一个整数，`cli.py:530` 还要 `res.out_dir`，
所以"恒 return 0"在那一处不是丢掉读数、是换掉了类型，红的是 `'int' object has no attribute 'out_dir'`。
这和这一路反复撞到的那条是同一条：**变异预期只能来自读过的断言**，不能来自"哪些断言在比 0"这种
分类。改完声明重跑，AS-EXPECTED。

明确不做三件。其一，**不加结构守卫**：这一只手两个读者，两处各自有行为用例（M1/M2 就是分工的证明），
再钉一条 AST"只有一个地方调用 `asyncio.run`"是没有分支要读的断言——`#56` 那两条结构用例成立是因为
当时真的藏着第二份算术，这里没有。其二，**不为测试引入 `WOLF_BASE_URL`** 这类新环境变量，测试改的
是 `cli.build_config`，产品代码不该为测试长一个入口。其三，**不把 `aborted_endpoint` 的语义扶正**：
它今天的含义"端点不配合所以这局不算"是对的，只是它以前被 traceback 冒充过。

重跑这一片：

```bash
.venv/bin/python /tmp/mut_batch57.py          # 6 具，约 2.5 分钟，全程离线（只连 127.0.0.1）
.venv/bin/python /tmp/n57.py                 # N57_MODE=refuse|garbage|batch：起桩、跑真 cli、打印 rc/stdout/stderr 与末行事件
# REAL-RC=1 / --- stderr lines: 0            （05:29:08Z，真端点，修后）
```

`/tmp/n57.py` 里那三个模式就是上面三个读数：拒答的、答了但不合法的、批次。它打印的是退出码、墙钟、
stdout 与 stderr 的首末三行，以及产物最后一行的 `terminal`——修前那两处 crash 在这三格里各自留下
痕迹（stderr 有行、`批次 ->` 缺席），修后都在。全程只连 127.0.0.1。

#### 零局的批次先落了盘，再在摘要行上读一个空列表

`#57` 之后手里多了一份能真跑 `cli.main` 的探针，就用它把 CLI 的参数边界挨个敲了一遍（全程离线）。
重复的臂名、类型不符的 `--set` 值、配置里根本没有的字段名——都在既有那一条腿上
报 `配置错误：…` 加退出码 2。敲到 `--games 0` 才撞出 `#58`，而它有三个头，实测在 05:51:09Z 与
05:52:24Z：

```
wolf batch --configs A,B --games 0 --out b5 --mock   → b5/run_manifest.json 已经写出去了
                                                       （games: 0、pair_keys: []、臂目录一个都没有）
                                                       然后 traceback：IndexError: list index out
                                                       of range（摘要行读 pair_keys[0]），rc 1
wolf run --mock --games 0        → 什么都不产，rc 0
wolf run --mock --dry-run --games 0 → 印"没有捕获到任何 prompt —— 装配钩子失效了，这本身就是失败"，
                                       rc 0
```

三个头是同一种错的三种形状：**"这批/这局什么都没有"没有被当成一件事**。批次那侧它甚至留下了产物——
一个 `compare` 会去开的目录；`run` 那侧它是沉默；`--dry-run` 那侧它一边自报失败一边交出"出结论"的那
个码。还有一格旧证据：`_print_census` 里一直写着 `total / max(games, 1)`——除法防的就是这个 0，
只是防的位置错了，防在算式里而不是参数上。

修的是两格，都没有新增分支：

* **一个地板谓词、两个读者**：`cli._games_error`（`cli.py:88`）说"不足 1 局什么都不产，比较也没有
  分母"，`cmd_run` 的第一行（`cli.py:121`）和 `cmd_batch` 紧挨臂名检查那一处（`cli.py:491`）各自读
  它，走的还是既有的 `配置错误：…` + rc 2 那条腿。关键是**站在 `mkdir` 与落盘之前**——地板如果
  放在 `run_batch` 里面，`run_manifest.json` 就已经在盘上了。
* **普查自己说失败的时候，退出码跟着说失败**：`_print_census` 从"印一句话然后返回 None"改成返回
  这次普查能不能用（`cli.py:266`），`_cmd_dry_run` 是唯一读者（`cli.py:262`）——不能用就 rc 1。

`compare` 那一侧**没有改**，因为不需要：05:51:58Z 拿修前那个 `b5` 直接跑 `compare`，它报的是
`IDENTICAL_ARMS`、一份写好的 `comparison.md`、rc 1，连复现命令都印对了。读侧一直站得住；这一片
最容易被加出来、而又不该加的东西，就是"给空批次再写一句专门的解释"。

7 具变异照 `/tmp/mut_batch58.py`（06:01:12Z 起跑、06:02:47Z 收尾，输出在 `/tmp/mut58.out`；
set0 `tests/test_cli.py`、set1 `tests/test_loopback_endpoint.py`，两套 preflight 全绿，7/7 与预期
一致，`cmp` 整文件字节还原；只改 `cli.py`）：

| 变异 | 红用例 | 说明 |
| --- | --- | --- |
| V1 删掉 `cmd_run` 那一处地板 | set0：参数化的 run 两条，红在 `assert 0 == 2` | set1 绿：它传的是 1 局 |
| V2 删掉 `cmd_batch` 那一处地板 | set0：参数化的 batch 两条，红在 `IndexError`（就是修前那句） | **两个读者各自要有一条用例**——V1/V2 各只红一半 |
| V3 地板挪成"大于 1"（1 局也被拒） | set0 成片红（`assert 2 == 0`）；set1 三条子进程用例全红 | set1 的红输出里直接印着那句"要至少 1 局（收到 1）"：边界往右移一格，最外层那道墙先响 |
| V4 错误消息丢掉收到的那个数 | set0 四条参数化全红，红在 `assert games in err` | 那句"收到多少"不是修辞，是断言读的一格 |
| V5 空普查那句改 `return True` | set0 普查那条，红在 `assert 0 == 1` | 谓词算错 |
| V6 留着谓词、把读者换成恒 0 | 同一条、同一格 | 与 V5 打在相邻两行上：一条腿防"算错"，一条防"没人读" |
| V7 `cmd_batch` 那处换成**逐字相同**的内联判断 | 两套都 SURVIVED | 这是**声明出来的缺席**：行为一字不差，行为用例看不见第二次实现 |

V7 是这一片留下的一格，写在脚本头部而不是藏起来。本轮**不加**结构守卫——不钉"这个函数只有两个读者"，
也不钉"这个比较不许在别处出现"：现在只有一份实现，钉形状等于钉一个还没有人违反的约定。
另外两件明确不做：不删 `cli.py:284` 的 `max(games, 1)`——它护的是同一个 0，删了等于把
ZeroDivisionError 换回来，有了地板它是冗余但不是错误；不顺手去管 `--seed0` 的取值范围，那是另一个参数。

重跑这一片：

```bash
.venv/bin/python /tmp/mut_batch58.py    # 7 具，约 1.5 分钟，全程离线
.venv/bin/pytest tests/test_cli.py      # 52 条、跑起来 61 个用例（三条参数化），1 秒
```

修后同一个探针（05:58:20Z）：三个头都变成一行 `配置错误：--games 要至少 1 局（收到 0）…`、rc 2，
`ls -d b5 r1 r2` 三个目录一个都不存在——地板站在落盘之前这件事，只有在这儿是可核对的。06:12:31Z
把同一只手上另外三道判据一起探针过（重名臂、配置里没有的字段名、类型不符的 `--set` 值），加上
`run --games 0`：五个都是 rc 2、stderr 一行、stdout 空、指定的输出目录不存在。

这一片自己也被文档闸门抓过一次：那句重跑注释先按收集数写的"四十五条"，而闸门核的是文件里的定义数。
按 `<模块名> N 条 = def 数` 的既有写法改口才对上——这正是 `#44` 补那道闸门时立下的用途。（同一格在
`#59`/`#60` 之后重数过一次，`#63`/`#67`（C2 的刀与刀的读数）又给这个文件加了两条、`#68`
（指派的读者）再加一条、`#70`（前缀缓存的读者）再加两条：
现在 `tests/test_cli.py` 52 条、跑起来 61 个用例。）

#### 类型过了、范围没过：越界的 `--seat` 印出一份观众视图，要一块不存在的板子留下一个空目录

`--seat` 和 `--set` 在校验上是同一类：argparse 只问"这能不能读成一个整数"，没有人问过"这个数在这一局
里是不是一个座位"、"这块板子在这份代码里存不存在"。07:36:16Z 把两处范围校验各拆掉一次、跑真命令量回
修前（`/tmp/probe59_prefix.py`，跑完 `cmp` 整文件字节还原）：

* `replay <g00000007> --seat 10`：rc 0、stdout 1725 字节、stderr 空。屏幕上是一份**观众视图**——座位
  10 不在名册上，`percept_for` 于是一私有事件都不多给，输出看着完全正常。
* `batch --set A.seat_count=5 --mock`：rc 1、stderr 41 行（12 帧，最里层是
  `ValueError: only the 9-seat board exists; got 5`），而**批次目录已经建出来了**。命令写错了却拿到一
  个由崩溃给的退出码，外加一个等着被 `compare` 打开的空目录——归因错到引擎头上，是 `#57` 那一族。

修的是三格，都没有新增分支：

* **一个谓词、两个读者**：`cli._seat_error`（`cli.py:98`）说"越界的座位号是命令写错了"，`cmd_replay`
  （`cli.py:392`）和 `cmd_watch`（`cli.py:412`）各自读它，走的还是既有的 `配置错误：…` + rc 2 那条腿。
  名册**从文件里读**（`render_html.seats_of`，和票型矩阵的行是同一个读者），不再抄第二份 9；文件里
  根本没有名册时它让路——那种文件该拿的是 `#53` 那句"没有开局记录"，而不是"这一局没有 42 号"。
* **板子只有 `roles` 知道**：`batch._set_path` 在类型校验之后、`replace()` 之前问一次 `roles.board_for`
  （`batch.py:117`），把它那句 `ValueError` 拼进 `BadOverride`。于是 `--set A.seat_count=5` 落回 rc 2
  这一侧，且站在建目录之前。
* **读侧补一格**：`info.percept_for` 在没有事件时不再取 `src[-1]`（`info.py:111` 的 `at_seq` 那行）——
  只有 CLI 的座位分支会递给它空列表，而读侧的 IndexError 不是判决。

`--seat` 与 `--god` 同时给的时候谁说话，是实测的而不是推的：07:34:44Z `replay --god --seat 3` 与
`replay --seat 3` 的 stdout sha 同为 `dd111ac69f24`，与 `--god` 单独的 `8918d0e775a1` 不同——座位优先，
写进 [views.md](docs/views.md) 那张表旁边那句。

10 具变异照 `/tmp/mut59.py`（07:28:51Z 重跑过 W2，输出在 `/tmp/mut59.out`；set0 `tests/test_cli.py`、
set1 `tests/test_info_isolation.py` + `tests/test_batch_paired.py`，两套 preflight 全绿，10/10 与预期
一致，`cmp` 整文件字节还原）：

| 变异 | 红用例 | 说明 |
| --- | --- | --- |
| W1 谓词永不拒绝 | set0 六条越界用例，红在 `assert 0 == 2`，红的输出里就是那份观众视图 | 修前的形状 |
| W2 去掉"名册为空让路" | 只有 manifest-only 那两条，红在 `IndexError` | 见下面那段：红的形状和声明不一样 |
| W3 成员判断换成上界（`seat <= seats[-1]`） | `--seat 0` 与 `--seat -1` 的四条 | 专打"数在 1..9 里"≠"在不在这张桌子的名册上" |
| W4 报错消息丢掉名册 | 六条，红在 `'1-9' in err` | "共几席、范围多少"是断言读的一格，不是修辞 |
| W5 删掉 `cmd_replay` 那一处读者 | verb=replay 的三条 | 与 W6 各只红一半：**两个读者各自要有一条用例** |
| W6 删掉 `cmd_watch` 那一处读者 | verb=watch 的三条 | 同上 |
| W7 删掉 `batch` 问板子的那块 | 板子那条，红在最里层的 `ValueError` | set1 绿：它不建臂 |
| W8 字段名比较反号（把 `seat_count` 当成别的字段） | 两套**都**成片红 | 本轮唯一一具"两套都要"的：`--set` 那条腿的读者不止 CLI |
| W9 `percept_for` 的空列表守卫退回 | manifest-only 那两条，红在 `IndexError` | 读侧那一格单独有一具，防"只有 CLI 改了就全绿" |
| W10 消息里的名册换成写死的 `1-9`/`9 席` | 两套都 SURVIVED | **声明出来的缺席**：这份代码里只有 9 人板，抄第二份 9 在今天产不出任何可观测差别 |

W2 这一具要披露一次声明写错：跑前写的是"两条红在 `rc == 0`（实得 2）"，实跑是
`IndexError: list index out of range`——让步被拆掉之后，那句 f-string 仍然去取 `seats[0]`。两者都证明
"这一格有读者"，但不是同一件事，所以声明按实测改正（`/tmp/mut59.out` 第 6 行是证据），07:28:51Z 重跑
一次确认改后的声明能复现。同一格里还有一件账要还：`#53` 那条钉"文件里没有名册时不该拒绝座位号"的用例
本轮被并进参数化那条（同形状的断言、多两个座位号），所以 W2 只红两条而不是三条；旧用例名连同它在文档
里的引用一起删掉了——谁要再把这个名字写回文档，名字闸门当场红（这一轮它就是这么抓住我这句话的）。

W10 是这一片留下的一格，和 `#58` 的 V7 同族：行为看不见第二次实现。本轮同样**不加**结构守卫，理由
比上一轮更硬——不是"还没有人违反"，而是**这份代码只有一块板子**，任何行为用例都无法区分"从名册算出来
的 1-9"和"抄下来的 1-9"。要钉只能钉字面量，而那属于〈文档里的行号〉下面那张"能钉字面量的闸门"清单。

修后同一个探针（07:34:44Z，全程离线）：`replay --seat 42`、`replay --seat 0`、`watch --seat 42` 三个
都是 rc 2、stdout 0 字节、stderr 一行（`配置错误：--seat 要在这局的名册里（1-9，共 9 席），收到 42：…`）；
`--seat 1` 与 `--seat 9` 都是 rc 0、1805/1756 字节——**名册两端仍然得是座位**，这一格由
`test_the_two_ends_of_the_roster_are_still_seats` 钉住；`--set A.seat_count=5` 不加 `--mock` 也是 rc 2、
一行、`/tmp/b59probe` 不存在，说明它站在缺 key 那道腿**之前**。

同一次探针顺带量出来的第二格是次序：`--god` 与 `--seat` 同时给，输出与只给 `--seat` 逐字节相同。这一支
次序以前**没有读者**（`cli.py:209` 那句排在 `god` 那一支前面，把两支换序不会有任何用例红），而
[docs/views.md](docs/views.md) 要写它。补的特征化用例
`test_sitting_at_a_seat_wins_over_the_god_view_on_the_same_file` 第一遍就绿（08:00:19Z）——它钉的行为
本来就在，所以它的强度不靠"红过"证明，靠三具变异：P1 换序只有它红，P2 拿掉座位那一支它和另外两条一起
红，P3 并掉公开那一支红在另外两条（08:03:54Z，`/tmp/mut60.out`，3 具 × 两套 6/6 与预期一致、按字节
还原；两套都不 import `cli` 的那一套三具全 SURVIVED，这一支只有 `test_cli.py` 在读）。预期里写错过
一格：跑前声明"P2 之下新用例仍然绿，因为三方都退成公开视图"，实测它红了——`--god --seat 3` 在座位那
一支死掉之后落进的是 `god` 那一支，不是公开那一支。

重跑这一片：

```bash
.venv/bin/python /tmp/mut59.py           # 10 具，约 2 分钟，全程离线
.venv/bin/python /tmp/mut60.py           # 3 具 × 两套（次序那一支），约 20 秒
.venv/bin/python /tmp/probe59_prefix.py  # 修前取证：拆两处、量、还原
```

#### 文档里的行号也是一句主张：一次插入把同一份文件下游 15 处引用顶到错位上

这一轮往 `cli.py` 里插进 `_seat_error` 之后，同文件下游所有行号整体后移。这份文档今天点 `cli.py` 的
18 处引用里，有 15 处的号落在那次插入的下游（复敲：在 `README.md` 与 `docs/*.md` 里数 `cli.py:` 后面跟
数字、且号不小于 120 的条数），另外 3 处在插入点上游、号不会动——这 15 处就是本轮的错位面。名字有闸门
（`#32`/`#44`/`#45` 那几套扫的是用例名、CLI 参数、条数、出厂值），行号没有，于是补了第四套扫描：
`tests/test_doc_citations.py` 里 `_line_citations` 收 `([\w.-]+\.py):(\d+)`，要求**同一句话里出现一个
≥4 字符的 ASCII 名字**，并且那个名字真的落在被点那一行上。最后半句今天才收紧（`#72`，见下面那一节；
当年写的是"±2 行内"，而那个窗口收得比这句话说的松）。修之前那一遍报出 17 处坏引用、另有
3 处是碰巧过关（±2 行里确实有个同名标识符，但不是这句话要指的东西）——那一次的命令行输出没落盘，这两个
数只能算口账，所以不拿它当证据；能复敲的是修完之后的账：25 处在用（README 23 + views.md 2，点 `cli.py`
的 18 处），闸门报 `bad = []`（07:47:28Z）。修的时候连文档里那句"src/ 里唯一那一处 `except LogDamage`"
一起改：它连号带主张两处都错，真号在 `events.py:410`，而 `cli.py:623` 是第二处。

两向都证过（`/tmp/mutline.py`，07:33:24Z，6/6 AS-EXPECTED、字节还原，输出 `/tmp/mutline.out`）：
探针那条用例（`test_the_line_citation_probe_fires_on_a_number_that_moved_and_only_on_that`）故意抄一个
挪后 40 行的号、一个不存在的文件、一句只有中文的话，三格必须红、就地写对的那一格必须绿。

| 变异 | 红用例 | 说明 |
| --- | --- | --- |
| L1 拆掉"那五行里有没有名字"的比较 | 探针（`assert 2 == 3`） | 挪号那一格不再被报 |
| L2 源侧窗口从 ±2 行放大成整份文件 | 探针 | `_games_error` 在整份文件里出现过，放大窗口就等于不比对 |
| L3 解析器丢掉 `src/wolfengine` 那一站 | 探针 + 真的文档那条 | 去掉一站，才分得开"找不到文件"和"找错了地方" |
| L4 文档侧段落窗口收到只剩点号那一行 | 真的文档那条 | 07:33:24Z 那一跑的误报清单在日志里被截断（看得见 4 条，总数没落盘）；同一条判据在今天这份文档上复敲得 **2 处**（把 `para` 换成"只有点号那一行"再跑这一条即可）——Markdown 会折行，名字常在相邻物理行上，所以窗口必须跨行 |
| L5 从 STOP 里删掉 `prompts` | 两套都 SURVIVED | **声明出来的缺席**：STOP 减一项只放宽闸门，眼下没有一处主张靠它活着 |
| L6 标识符阈值 ≥4 放宽成 ≥2 | 探针，红在措辞换了（`那五行里没有一个 ['py']`） | 探针按 reason 比对，所以"红是红但说的不是那件事"也看得见 |

这条闸门有一格明确的取舍：它不判断"这一行是不是那句话唯一想指的东西"，只判断"被点那一行上有没有
这句话点过的名字"。所以它防得住**漂移**（插一行就烂）和**错号**（号挪到邻居那行也不放行），防不住**号
对得上但指错语句**——同一个标识符可以出现在五处，那种腐烂要靠名字闸门，两套各管一段。文档里凡是一个号
配不上名字的（例如上一轮那句只说"数的是夹具行数"的引用），一律改成点用例名——账面不报"改了几处"，报的
是改完之后那 25 处全绿、`bad` 为空。

07:33:24Z 那一跑钉的是当时那版判据（窗口 ±2、reason 文案"那五行里没有一个"）。`#72` 把窗口收成整行之后
同一族判据在 14:01:26Z 重跑过一遍，7/7 按预期，L6 那句 reason 变成"那一行没有一个 `['py']`"。


#### 那道行号闸门收得比它自己写的还松：`#72`

上面那句话写着"落在被点那一行的 ±2 行内"，而 ±2 行是 5 行源代码、"同一句话"是 ±2 行文档、一段话通常
带 5~11 个标识符。两边都宽，于是"这段里有个词在那五行里出现过"几乎总能成立——**这句话当时就是假的**，
不是不够严。缺陷是 `#70` 那轮撞上的（13:38Z）：`cli.py` 被插入 3 行之后，README 里两处指向同一个
`except LogDamage` 的引用漂到了 `cli.py` 的 620 行与 616 行，而真实那一行是 623。改完号之后闸门仍然全
绿，也就是说这两处**不是被闸拦下来的，是被"我去核了一下"发现的**。同一段里还点着 `events.py:410`，那
个 `events` 因此成了一个万能标识符。

于是量了而不是猜：把三份窗口规则摆在全语料上过一遍（`/tmp/linewin4.py`，13:49:37Z，50 处引用）——

| 窗口 | 用全部标识符 | 去掉全仓库文件名 |
| --- | --- | --- |
| 整行 | 5 处红 | 6 处红 |
| ±1 行 | 2 处红 | 3 处红 |
| ±2 行（当时） | **0 处红** | 1 处红 |

13:48:25Z 另测两个错号：`cli.py` 的 620 行（`parse_args` 那一行）与 624 行（一句提到 `events.py` 的注释）
在当时的判据下**都是绿的**。13:51:02Z 再测"`文件名算不算证据`"这一半：词干表如果取全仓库那一份，
`assemble.py` 里 `render_persona_card(persona)` 那一处会被误伤（它整行只有 `persona` 一个词，而 `persona` 恰好也是仓库里的文件名）。

取舍因此是两条，都是一行代码：

* **窗口收成整行**。代价实测为 0——收成 ±1 时全语料仍有 2 处红、收成整行 5 处红，而那 5 处红的引用
  号本身就是错的（下表），修号而不是放宽窗口才是对的方向。
* **同一段里点过名的文件，它的词干不算证据**（`stems = {Path(p).stem for p in PY_CITED.findall(para)}`）。
  只取这一段点过名的那些，不取全仓库的，`persona` 那种重合就伤不着。

修掉的 5 处号（13:54:48Z 逐处读过上下文才定的，不是把数字 ±1）：

| 文档里写的 | 指的是什么 | 真号 | 当时为什么绿 |
| --- | --- | --- | --- |
| `cli.py` 的 484 | `_games_error` 在 `cmd_batch` 里的那个读者 | 491 | **484 是空行** |
| `cli.py` 的 390 | `cmd_replay` 的 def | 392 | **390 是空行** |
| `batch.py` 的 110 | `roles.board_for(value)` 那一问 | 117 | 110 是它上面的类型校验 |
| `batch.py` 的 451 | 门口读 `run_manifest.json` | 450 | 451 是下一句 |
| `metrics.py` 的 735 | `round(frags[0][1] / len(r), 4)` | 768 | 735 如今是段 docstring 的中间 |

两前两后各一格是**空白行**：读者照着敲 `sed -n 484p src/wolfengine/cli.py` 会读到一行空气，而闸门当时
说它是对的。

7 具变异照 `/tmp/mut72.py`（14:00:40Z 全跑、14:01:26Z 补跑 E2；那一跑时这个文件是二十条——**那是历史
读数**，现行条数由 `test_a_case_count_written_next_to_a_module_name_matches_that_module` 每天核，别拿这
个数当今天的账；baseline 0 红；`cmp` 整文件字节还原）。预期来自读过的断言或先测过的数量，E2 那一格是**先活下来、再补
断言**的：

| 变异 | 结果 | 证人 |
| --- | --- | --- |
| E1 窗口退回 ±2 行 | CAUGHT | `test_a_number_one_line_off_is_a_bad_pointer_and_a_file_name_is_not_evidence`（`assert probe(n + 2)`） |
| E2 窗口只往下多伸一行 | CAUGHT（补断言之后） | 同一条的 `assert probe(n - 1)` ——14:00:40Z 那一跑它是 SURVIVED，缺的就是这一格 |
| E3 文件名照样算证据 | CAUGHT | 同一条的 `assert probe(n + 1)` |
| E4 "没写出可核对的名字"那一格不再报 | CAUGHT | `test_the_line_citation_probe_fires_on_a_number_that_moved_and_only_on_that` 第四格 |
| E5 越界的号静默跳过 | CAUGHT | 新增的越界那一格（`超出文件长度`）——这条分支此前**没有任何断言走过** |
| E6 标识符阈值放宽成 ≥2 字符 | CAUGHT | 探针两条，红在 reason 换了：`那一行没有一个 ['py']` |
| E7 词干表换成全仓库那一份 | CAUGHT | 真文档那条，红在 `assemble.py` 的 `persona` 那一行（正是 13:51:02Z 预测的误伤） |

还留着的那半件，说的是写法而不是判据：闸门分不出"这是一句 live 引用"和"这是在复述一个已经漂走的旧
号"——`file.py:NNN` 这个形状本身就等于"我保证这个号今天还对"。所以约定是：**复述旧号不许用这个形状**，
要写成"`cli.py` 的 616 行"。这一条本轮自己踩过（13:42:28Z 写 `#70` 那一节时把两个历史号写成了
`.py:NNN`，闸门把它当 live 引用报了红），它靠"误报会逼你改写法"生效，不靠机器识别语境——机器识别不了
这一点，写在 `tests/test_doc_citations.py` 的模块 docstring 里。


#### 条数和收集数是两句真话：`@pytest.mark.parametrize` 展开掉的那一半以前没人读

这一族是本轮改文档时自己冒出来的：同一份 `tests/test_log_recovery.py` 在 README 里一处写 57、另一处写
56（两处都是"跑起来 … 个用例"那个句式），而〈条数〉那道闸门只数模块级的 `def test_`——两句都不归它管。
参数化展开后的
条数不在 AST 里（`@pytest.mark.parametrize` 的字面量列表要由 pytest 自己展开才能数），所以只能把套件
**收集**一遍：`_collected_counts` 跑 `--collect-only`，数 `tests/` 下以 `.py::` 开头的行数。这一步里
`-o addopts=` 是必需的，`pyproject.toml` 的 addopts 自带一个 `-q`，命令行再给一个就把 nodeid 清单压成
"每文件计数"两行——08:20:08Z 在同一台机器上量过：带着覆盖是 706 行、不带是 0 行。

归属规则和行号那一族同构：一句话里的数字要有一个主人，主人取**那句话上方**最近的一个测试模块名，窗口
±2 行（Markdown 折行会把名字留在上一行）。没有主人的 `mod=None` 直接报红。
这一族现在有五条闸在跑（`tests/test_doc_citations.py`）：前四条是合成语料，最后一条才对真文档。
`test_the_collected_count_scanner_fires_on_the_wrong_one_and_names_the_ownerless` 拿四格钉"错的要报、
没主人的要报"（对的、错的、名字在上一行的、没主人的各一格），
`test_a_collected_claim_takes_the_nearest_name_above_and_never_one_from_below` 钉"两个名字取最近的那
个、下一行的名字不许替上一句背书"——README〈测试〉一节里真有这样一格，它的窗口上方同时出现
`test_cli.py` 和 `test_log_recovery.py`，08:56:37Z 数出来全语料窗口含两个名字的格子就这 1 个，
`test_a_claim_folded_in_the_middle_of_the_number_is_still_a_claim` 钉折行（数字住在两行之间那一格），
`test_a_suite_total_is_not_a_collected_claim` 钉 `跑起来 ` 这个前缀有读者（整套的总数不是哪个文件的
收集数），
`test_a_collected_count_written_in_the_docs_matches_what_pytest_collects` 才是拿真文档对真收集数的那条。

预期全部来自 08:48:51Z 的一次先测（`/tmp/premeasure61b.py` → `/tmp/premeasure61b.out`）。这一步换了
办法：**不再手搓复刻**，而是把真的闸门文件逐具改成 `/tmp/gate_mut_C*.py` 再 import 回来扫三份语料
（四格探针 / 三格探针 / 真 docs）。旧办法（08:25:47Z 那份）把扫描器抄在 /tmp 里，实现一改它的预期就
成了假账——下面三处改账就是这么冒出来的。import 之后必须把 `mod.ROOT / DOCS / TEST_FILES` 钉回仓库：
模块级常量按 `__file__` 的父目录算，不钉就会拿 `/` 当仓库根去收集（08:39 那次真把 `--collect-only`
跑到了根目录上，两分钟没回来）。跑完 16/16 与声明一致（`/tmp/mut61b.py`，08:51:17→08:51:45Z，
8 具 × 两套，改完按字节还原；set1 是 `tests/test_cli.py`，它不 import 闸门，所以八具在那里全
SURVIVED 也是账的一部分）。基线账：真实语料 6 处主张、29 个模块、709 个用例（08:55:12Z 现量，
`_collected_counts` 求和；先测那一步的 `bad = []` 是 08:48:51Z 的读数，当时总数还是 707）。

| 变异 | 红在哪 | 说明 |
| --- | --- | --- |
| C5 窗口上下都看（变成"下面最近的名字"） | 三格探针 1 条（12 那格被记到 `test_wiring` 头上） | 四格探针**看不出差别**——那一格的模块名就在同一行上；**真实语料也不报**（见下面第二处账）。方向这一半只有合成语料在读 |
| C6 没有模块名就跳过 | 两条探针（条数 4→3、2→1） | 真实语料不报：6 处主张全有主人，所以"漏掉无主人那一支"只能靠合成语料抓 |
| C7 窗口收成同一行（丢掉上一行） | 4 条（四格 + 三格 + 折行 + 真文档） | 折行那一格当场散架，README 里 57 与 56 两处一起变成没主人 |
| C9 丢掉 `-o addopts=` | 真文档那条，红在 `收集这一步没跑起来` | 收集数=0，地板先响——不是"结论是零条"，是"这一步没跑成" |
| C10 正则放宽成 `N 个用例` | 真文档那条 + 钉前缀的合成那条 | **从等价体变成了有读者的一具**，见下面第一处账 |
| C11 取窗口里最上的名字（`mods[0]`） | 真文档那条 + 三格探针（57 被记到 `test_cli` 的 56 上） | 和 C5 一样，四格探针看不见——它的窗口里只有一个名字 |
| C12 地板从 `>= 15` 抬到 `>= 999` | 真文档那条，红在 `收集这一步没跑起来` | 29 < 999。不抬一次，谁也不知道那句 assert 是不是早就在空转 |
| C13 折回按行扫（丢掉折成空格那一步） | 折行那一条（`assert [] == [(2, 'test_log_recovery', 57)]`） | 真实语料**也不报**：此刻没有一处主张折在数字中间（08:38:17Z 量过，按行扫与折行扫的差集为空）。钉的是"以后折叠了别安静地看不见" |

三处与上一版报告不同的账，按〈变化〉和〈缺席〉记，不按〈更强的覆盖〉记：

- **C10 从等价体变成有读者的一具。** 08:25:47Z 那份复刻里三份语料逐字节相同，它当时确实钉不住；这一轮
  它的真语料读者就是本节自己那句"29 个模块、709 个用例"——放宽正则把这一格的整套总数记到了 `test_cli`
  的 56 上（08:48:51Z 量到的是 707 对 56，今天换成 709，同一个错位）。
  换句话说 `跑起来 ` 分隔的正是"某个文件收集了几条"与"整套一共几条"，后者没有模块可对，所以不该落进
  闸门。主钉是合成那一格，真语料这一格算白捡。
- **C5 的真语料读者没了。** 上一版说 README 那一格会被记到 `test_loopback_endpoint` 头上，那是复刻件的
  窗口形状（比现实现宽）算出来的；现实现里那一格窗口上方只有 `test_cli`/`test_log_recovery` 两个名字，
  往下看的变异换不动它（`real 6 处 bad=[]`）。所以"只看上方"这半判据的可信度只来自三格探针，仓库里
  现存的某句话证明不了它。
- **C13 同理**，折行这一半也只有合成读者。差集为空是个**此刻**的读数：真文档里出现第一处折行主张那天，
  这条闸门才第一次在语料上起作用。

这一族的取舍和行号那族一样：它不判断"这个数是不是那句话唯一想说的"，只判断"这句话有没有主人、主人的
真数是多少"。所以它防得住**改了一个文件的用例数没改文档**，防不住"两个模块的数恰好写反、而两个名字都
在同一行里"——那种腐烂要靠把句子拆短。另外它只认 `跑起来 N 个用例` 这一个句式（换写法就不落进闸门，
而放宽正则的代价由 C10 钉着），所以这一族的**前缀**本身也是被读过的那一半。

重跑这一片：

```bash
.venv/bin/python /tmp/premeasure61b.py # 先测：改真文件→import 副本，三份语料各扫一遍
.venv/bin/python /tmp/mut61b.py        # 8 具 × 两套，约 30 秒，全程离线
```

#### 一根不存在的处理轴：`--set A.regions.b0=100` 走完了全链，只有 hash 知道它变了

`--axis` 的白名单管的是"不许改身份"，`_INERT` 管的是"改了没有对应行为"。顶层那一格（`enable_sheriff`）
从 `#19` 起就有人守着；这一格查的是**嵌套层**：plan §5 的预算表里 B0 和 C1–C4 各占一格，装配器真正
读的是 `c_total`/`b2`/`b1`/`a_hard`，那五格加 `tokens.warn`、`tokens.force_compact` 是记账格。

先测（09:40:35Z，`report.flatten` + `report.axis_diff` 直接调用，绕开 `--set`）量到的是一条通路：
`regions.b0` 在 `flatten` 里就是一格，改它 `config_hash` 从 `06ed754ac19e` 变成 `082f481667f6`，而
`axis_diff(a, b, axis=["regions"])` 回的是 `ok=True`、`undeclared=[]`——报告印"除声明轴外无差异"。
`axis_diff` 的两处判断（`k.split(".")[0] in FORBIDDEN_AXIS` 和 `_axis_covers`）都是**前缀**式比较，
所以整块预算表声明成轴，就等于把表下每一格都声明成轴。

修在两个地方，理由不同：

1. `batch.apply_overrides` 的循环入口（`#62`）：顶层的同类拒绝住在 `_set_path`，那里每层只看得见一个
   字段名，`head == "regions"` 时分不清 `b0` 还是 `b2`，而这一格的全部意义就是两者不同。
2. `report.axis_diff` 的第三个桶 `inert`（`#64`）：`compare` 读的是 manifest 里记下来的配置
   （`batch.py:460`），手改过的那一份、以及**旧版本跑出来的那一批**都不经过门口。这一处是补文档
   的时候翻出来的——`docs/comparison.md` 写着"名单只有一张，两处读同一个常量"，而 09:41:02Z 的实测是
   `model` 两处都拒、`enable_sheriff` 与 `regions.b0` 只在门口拒：那句话对**身份**成立，对**记账**
   这一类一直是假的。

`inert` 没有并进 `rejected`：那一桶的文案是"配对前提已失效，改任何白名单都不会放行"，指的是重跑批次
也没用；记账格的修法是**把代码补上**。两类的修法不同，混说一次就把人送去跑一整个批次。

用例 4 只名字 / 17 格（10:10:40Z 从 `--collect-only` 的 742 条里按名字数出来的：8 + 7 + 1 + 1；
同一句里先前写的 24 不对，那份数没有对得上任何一条名字。口径：`tests/test_report_stats.py`
收 43、`tests/test_batch_paired.py` 收 60），名单本身是参数化的来源（`Config().inert_fields + Config().inert_leaves`），所以 `#63` 每实现
一格、参数就自己缩一格。`compare` 那一只的差异是**手工造臂**而不是改 manifest 造的：改 manifest 会先
撞上 `_hash_integrity`（日志记的 hash 与 manifest 不符），那条路要测的是另一件事。

`/tmp/mut64.py`（09:51:29Z→09:52:34Z，8 具 × 两套，`report.py` 还原后 `byte-identical`）16/16
合预期。set0 = `tests/test_report_stats.py` + `tests/test_batch_paired.py`；set1 = `tests/test_cli.py`
**八具全 SURVIVED**，这是先测过的（09:50:32Z grep：那套里没有一行读 `AXIS_VIOLATION` / `axis_diff` /
"没有代码"），留着只为了说明"另一套也绿"不是覆盖。

| 具 | 改法 | 结果 | 具名证人 |
|---|---|---|---|
| R1 | `inert[k] = v` → `pass`（分支还在，哪个桶都不进） | CAUGHT 红 17 | 声明 8 + 父轴 7 + 文案 1 + `compare` 1 |
| R2 | 并进 `rejected`（结论照样不出，**文案错了**） | CAUGHT 红 17 | 同上，红在 `out.rejected == {}` 那半句 |
| R3 | 只读顶层名单 | CAUGHT | 嵌套 7 格放行 |
| R4 | 只读嵌套名单 | CAUGHT 红 1 | `[…declared_as_the_axis[enable_sheriff]]`——方向反过来，两张表都得读 |
| R5 | `ok` 忘了 `inert`（桶填对、结论照出） | CAUGHT 红 16 | 15 格参数 + `compare`；**文案那条绿**，它不断言 `ok` |
| R6 | 声明了就放行 | CAUGHT 红 17 | 同 R1 |
| R7 | 两桶文案互换 | CAUGHT 红 2 | 只剩文案用例和 `compare` 用例——报告不出结论、也点名了格子，坏的是"知道去哪儿修" |
| R8 | 内联一份 `k.startswith("regions.")`（**过拦**） | CAUGHT | 两条先就在的用例：`test_a_nested_budget_counts_as_its_own_axis`、`test_declaring_a_parent_axis_covers_every_leaf_under_it` |

R1 与 R2 的红集**逐条同名**（17 对 17），分开它们的不是红了哪些用例，而是断言里的哪半句——所以这张表
记的是"红集"加"红在哪半句"，只记前者会以为两具是同一具。R5 的绿证人（文案那条）同样是一条读数：
它说明那一处坏掉时，报告里字都对、结论却出得去。红数取自 `/tmp/count64.py`（09:53:28Z→09:53:52Z，
`--tb=no` 全量数，不是 `mut64.py` 那份截在 14 条的输出）。

`#62` 那五具（Q1–Q5，门口那半边）账记在 [docs/comparison.md](docs/comparison.md)：9/10 合预期，
Q2 声明为等价体而预期写错——那一族的教训是"预期指不到具体某条 assert 就先量"。这一族八具没有猜的。

第三只手：名单只有一张，那两处拒绝就不许各自抄一份。`test_the_two_refusal_sites_do_not_keep_their_own_copy_of_the_lists`
按**名字**扫 `batch.py` 与 `report.py`（docstring 和 `#` 注释剥掉，所以门口那句解释"为什么拒 `enable_sheriff`"
的英文注释不算抄）。抄来的名单值一样、行为等价，任何按值断言的用例都看不见它，所以只能这么扫——但
"只有这条守卫看得见"这句话本身是一次预期，得量：

| 具 | 改法 | 实测 |
|---|---|---|
| R9 | `report.py` 内联一份**完整且正确**的名单 | set0 红 1（新守卫）；set1 也红 1（`test_the_no_reader_list_and_the_source_agree_in_both_directions`）——预期写的 SURVIVED，**WRONG**（09:59:44Z） |
| R10 | `batch.py` 内联同一份 | 同 R9 两红 |
| R11 | 只内联**顶层**那格，嵌套半边仍 `in INERT_LEAVES` | 整套 742 条**只红这一条**：新守卫红、对账守卫绿 |
| R12 | 门口只认 `("enable_sheriff", "regions.b0")` 两个名字 | 整套红 8：新守卫 + 对账 + `--set` 那 6 格的参数用例（`734 passed`） |

R9/R10 那一具为什么会被对账守卫也抓到：它的判据是"读取点 = `src/`（除 `config.py`）里有 `cfg.b0`
这样的属性访问**或** `"b0"` 这样的字符串常量"，而内联名单里的 `"regions.b0"` 正是后一种——于是"零读者
名单"与源码对不上。分工因此是干净的两半：**点号路径归对账守卫管，没有点号的那一格（`enable_sheriff`）
归这条新守卫管**（R11 是这条的存在理由，也是它唯一的独占读数）。R12 顺带量到门口那半边的另一件事：
只认两个名字时其余 6 格的 `--set` 真的放行，红的是 6 只参数用例而不是文案。

重跑这三片：

```bash
.venv/bin/python /tmp/mut62.py    # 门口那半边：5 具 × 两套，约 40 秒
.venv/bin/python /tmp/mut64.py    # 比较这一半边：8 具 × 两套，约 65 秒
.venv/bin/python /tmp/count64.py  # 只数 R1/R2/R5/R6 的完整红集（上面那张表的红数来自这里）
.venv/bin/python /tmp/mut65.py    # R9/R10：内联一份完整名单，两套（09:59:44Z，两具都 WRONG）
.venv/bin/python /tmp/mut65b.py   # R11/R12：收窄到顶层那格，两组文件（10:02:53Z）
.venv/bin/python /tmp/mut65c.py   # 同样两具打在**整套**上，"只有它红"这句的证据（10:07:22Z，约 2 分半）
```

#### 反重复回灌的四个名额全花在同一句话上：`#65`

plan §7 第 3 条要求把"本轮已经出现的说法"回灌进 C4，让第 N 个座位别再念别人念过的模板。这条链住得
很散（`metrics.template_top_fragments` → `persona.repeat_fragments` → `src/wolfengine/phases.py:212`
→ `src/wolfengine/assemble.py:198`），而 10:15:27Z 实测 `grep -rn "repeat_fragments|禁止复述" tests/*.py`
是**空的**：整条链一个断言都没有，拆掉任何一环，742 条照样绿。

链上真正的缺陷不在"接线在不在"，在**回灌的是什么**。旧实现枚举的是 6 字滑窗，而紧跟其后那段
"丢掉被包含的较短片段"从来没生效过——所有候选都正好 `min_len` 长，长度相同谁也包含不了谁。实测
（10:22:33Z，修前）三句话共享"我先听听大家的发言，"这十个字、后半句各不相同，回到 C4 的是
`我先听听大家；先听听大家的；听听大家的发；听大家的发言`：四个滑窗把四个名额全花光，第二个模板一个字
都没进提示词。`docs/metrics.md` 那句"专盯『我先听听大家的发言』这类 ≥6 字重复片段"在修前是假的。

修法是 `_grow`（`src/wolfengine/metrics.py:120`）：每个达标的窗口左右各长，长到"所有出现位置的邻居
不再一致"为止，撞到文本边缘就停。增长不会把门槛放低——延长后的片段出现在**恰好**同样的那几份发言里
——所以 `min_count` 的语义一点没动。

新增 `tests/test_anti_repeat.py`（现 12 条），钉四件事：门槛（`min_count=3` ⇒ 回灌最早在第 4 个座位
生效）、抓到的是整句而不是窗口、那一行自己不能把 C 区吃掉（最多 4 条 × 每条 24 字，`regions.c_task`
那一格在这个函数里唯一的读者），以及整条链接上了没有（一桌各自答同一句开场白，第 4 个座位起的提示词
里必须有那句"禁止复述"；只有两个座位听得见的狼话不得出现在给全桌的黑名单里）。
`tests/test_golden_game.py:490` 那条只钉了 `template_top_fragments` 返回空集合那一侧。

`/tmp/mut66.py`（10:34:40Z→10:39:36Z，12 具 × 两套，约 13 秒跑完；四只被改的文件还原后
`byte-identical`）12/12 CAUGHT。set0 = 本族 12 条，set1 = `tests/test_golden_game.py` +
`tests/test_wiring.py` + `tests/test_prefix_stability.py`——**set1 一具都没红**，这就是"全链零测试"
那句话的账：先就在的三套里没有一条读这条链。

| 具 | 改法 | 结果 | 具名证人（set0） |
|---|---|---|---|
| W1 | 拆掉接线：`run_speeches` 不再算黑名单 | CAUGHT | `test_the_fourth_speaker_is_asked_not_to_repeat_the_three_before_it`（1 条） |
| W2 | 接线留着、传空元组 | CAUGHT | 同 W1，红集逐条同名 |
| W3 | `if repeat_fragments:` → `if False:`，那一行永远不印 | CAUGHT 3 条 | 同 W1，加 `test_the_blacklist_line_names_exactly_the_phrases_it_was_handed`、`test_the_blacklist_cannot_grow_past_four_phrases_of_twenty_four_chars` |
| W4 | 无条件 append（空黑名单也印一行） | CAUGHT 2 条 | `test_no_seat_is_warned_about_phrases_nobody_used_yet`、`test_the_fourth_speaker_is_asked_not_to_repeat_the_three_before_it` 的门槛半边 |
| W5 | 去掉 `assemble` 侧的 `[:4]` | CAUGHT | `test_the_blacklist_cannot_grow_past_four_phrases_of_twenty_four_chars`，红在条数那半句 |
| W6 | 去掉每条 24 字的截断 | CAUGHT | 同 W5 那一条，红在 `len<=24` 那半句（同名不同半句） |
| W7 | 去掉 `persona` 侧的 `[:top]` | CAUGHT | `test_repeat_fragments_hands_over_four_phrases_not_every_run_it_found` |
| W8 | 门槛 `min_count` 3→2 | CAUGHT 2 条 | `test_a_phrase_only_two_seats_used_is_not_handed_back_yet`、`test_the_fourth_speaker_is_asked_not_to_repeat_the_three_before_it`（第 3 个座位就被警告） |
| W9 | 门槛 3→4 | CAUGHT 5 条 | `test_a_phrase_three_seats_used_comes_back_as_a_fragment`、`test_a_repeated_phrase_comes_back_as_the_whole_phrase_not_a_six_char_window`、`test_four_repeated_phrases_get_four_slots_not_four_windows_of_one`、`test_repeat_fragments_hands_over_four_phrases_not_every_run_it_found`、`test_the_fourth_speaker_is_asked_not_to_repeat_the_three_before_it` |
| W10 | `_grow` 退回恒等，滑窗重新冒充最长片段 | CAUGHT 4 条 | W9 那五条里的四条（除去名额那一条）：`test_a_phrase_three_seats_used_comes_back_as_a_fragment`、`test_a_repeated_phrase_comes_back_as_the_whole_phrase_not_a_six_char_window`、`test_four_repeated_phrases_get_four_slots_not_four_windows_of_one`、`test_the_fourth_speaker_is_asked_not_to_repeat_the_three_before_it`；`test_repeat_fragments_hands_over_four_phrases_not_every_run_it_found` **绿**——它钉名额不钉最长 |
| W11 | 排序改成"最少见的先回灌" | 10:34:53Z **SURVIVED** → 补用例后 CAUGHT | `test_the_slots_go_to_the_phrases_used_most_often_not_the_least` |
| W12 | 丢掉"被包含的较短片段"那一步 | 10:34:53Z **SURVIVED** → 补用例后 CAUGHT | `test_a_near_miss_does_not_take_a_second_slot_off_the_same_phrase` |

证人一律写全名，一处缩写都不用：`test_every_test_named_in_the_docs_resolves` 只认完整的
`test_` 开头那一串，前缀后面接省略号的缩写在它眼里是一条**不存在**的用例名，会直接报红；而不带
前缀的缩写它又看不见——坏了不会报。两个方向都不划算，所以这张表宁可宽。

W11 与 W12 是这一族唯一的**新增覆盖**，不是回归证明：名额只有四个、模板有五个时，`sort` 的次数键
反向会把真正塌陷那一句留在门外，而"那一行在不在""几条""每条多长"三个读数全部照绿——看得见它的只有
断言**哪个名额归哪句**的用例。W12 那个形状在真实输出里不罕见：同一桌同时出现"我先听听大家的发言"和
"我先听听大家的话"，前者三个座位共享、后者是它的近亲，而近亲把整句卡住长不出去——不丢掉它就白占一格。

W4 的预期写错了一次：我按"只有 `test_no_seat_is_warned_about_phrases_nobody_used_yet` 红"下刀，
量出来是两条。`test_the_fourth_speaker_is_asked_not_to_repeat_the_three_before_it` 里"前三个座位不许
有"那半边读的不是接线，是护栏——那一句同时钉着门槛和这一行的出现条件。红集比预期**大**，不是假绿，
但那句断言住在哪条用例里容易记错，所以写进用例自己的 docstring 了。

重跑：`.venv/bin/python /tmp/mut66.py`（12 具 × 两套），后缀 `W4 W11 W12` 只跑指定的几具。动手前它
自己跑一遍 set0 确认树上没有上一轮的残体——`mutlib.verify` 那句"clean"不作为证据，它比的是
`Path(t).name`（`metrics.py`）而备份名是 `src__wolfengine__metrics.py`，两边永不相等，所以它既不会
还原任何 `src/` 下的文件，报的"clean"也不是那四个文件的账。


#### 一个名字承诺比例、值却是份数、且没有读者的读数：`#66`

`#65` 收尾时翻出来的下一格。plan §8 M5 那格写的是"`template_top1_freq`（≥6 字且出现 ≥3 次的片段
**占全体比例**）"，实现给的是 `frags[0][1]`——被说的**份数**，一行都没有除以分母（修后长什么样见
`src/wolfengine/metrics.py:768`，那一行现在是 `round(frags[0][1] / len(r), 4)`）。两半都是问题，
而第二半更严重：10:48:41Z 实测 `grep -rn "template_top1" src/ tests/ scripts/` 只命中
产出它的那两行，**全仓库没有一个读者、测试里零断言**。也就是说这个数从 M5 长出来之后，从来没有一
条断言说过它等于几，改它没人拦，读它的人只能猜。

按"名字与值必须一致"修，不按"补一句文档说明它是份数"修：M5 是按轮算再取均值，均值底下混着 `n=9`
和 `n=2` 两种轮，五份里三份重复（0.6）和三份里三份重复（1.0，全桌塌完）在份数上都是 3——不可比的
数取均值，取出来的东西没法解释。字段更名 `template_top1_freq` → `template_top1_share`，值除以本轮
份数，没有模板的轮给 `0.0` 而不是 `None`（`None` 会被 `mean()` 悄悄吞掉，"没测到"和"测出来干净"
就分不开了）。这条链的旧读数不进版本库（`data/` 除外，而那里没有真端点的数据），所以不留兼容键。

判据住在 `tests/test_m3_gate.py`（现 15 条）新增的一条里，那里有全仓库唯一的 `_round(...)` 轮构造器；
`tests/test_golden_game.py` 的 M5 用例走的是金样本，而金样本里没有任何一句话被 ≥3 份发言共享，钉不住
非零读数——这是 `set1 零红` 的原因，见下。

`/tmp/mut67.py`（10:52:46Z→10:52:49Z，4 具 × 两套，`metrics.py` 还原后 `byte-identical`）4/4 CAUGHT，
四具的具名证人**都是同一条** `test_m5_template_top1_is_the_share_of_a_round_not_a_head_count`，
set1（`tests/test_golden_game.py` + `tests/test_anti_repeat.py`）一具都没红：

| 具 | 改法 | 红在哪半句 |
|---|---|---|
| T1 | 分子不再除以本轮份数（退回修前的语义） | `== pytest.approx(3 / 5)` |
| T2 | 分母从 `len(r)` 换成 `len(events)`（整局而不是本轮） | 那句"分母是本轮"的双轮断言 `[0.6, 1.0]` |
| T3 | 没有模板时给 `None` 而不是 `0.0` | `quiet[...] == 0.0` |
| T4 | 空轮的片段文本给占位字符串 `"无"` | `quiet["template_top1"] == ""` |

T2 是这一族唯一"值看起来差不多、形状却错了"的一具：单轮输入下 `len(events) == len(r)`，四具里只有它
需要**两轮**的 fixture 才抓得住——所以那条用例里写了第二轮，不是顺手多写。T3/T4 钉的是"没测到"与
"测出来干净"必须能分开，这一对在 `#20` 那几族里已经出现过几次，这里只是同一味药下在 M5 这一格。

重跑：`.venv/bin/python /tmp/mut67.py`。


#### 预算表里五行没有数的尺子，和一句只有装配器知道的话："这条 prompt 少发了几行"：`#63` + `#67`

plan §5 那张表列着 B0 和 C1–C4 五格，`#62` 之后 `--set` 只肯接受代码里真有键的格子——可"有键"和
"有人读"是两件事：装配器到这一轮之前只量得出 A/B/C 三个总数加 B1/B2（`#25`），**那五格连"量出来是
多少"都没有**。这一族静默和 `#62` 同形：不是算错，是压根没算，所以没有任何一条已存在的断言会红。

`#63` 把尺子从五根涨到九根。逐格的长度由 `src/wolfengine/assemble.py:86` 的 `block_tokens` 从发出去的
字节上量，名单与顺序只有一处：`src/wolfengine/metrics.py:956` 的 `REGION_CAP_KEYS`——批次表头那 12 格
由它派生，不是有人另数了一遍（`test_the_budget_table_header_names_every_ruler_the_rows_carry`）。
`B` 故意不在名单里：B1+B2 量的是同一批字节的两种问法，再记一遍等于给同一件东西发第二份权威。

**九根尺子里只有一把后面有刀。** 触发条件从"整段超 `c_total`"变成"整段超 **或** 主张卡那一块超
`c_belief`"（`src/wolfengine/assemble.py:225`），缩卡那一轮也拿 `c_belief` 判装没装下：两把尺拉同一个
杠杆，所以只有一把超的时候也不多砍。另外四格改的是"越界局数"里的红字，不动发出去的字节，这条分工
就写在 `src/wolfengine/config.py:77` 那段注释里——人格卡砍薄是 §7 的 P1/P3 防线静默失效，任务块砍掉
是 act 闸门失效，私有信息块是夜里那几句话在整份 prompt 里唯一的副本。三局 mock 的出厂峰值
71/57/328/111/115（11:12:16Z）：四把警报尺一声没响，最紧的 C2 也还在 450 以下。响得上的那一种在
合成夹具里——20 条新主张的预言家草稿卡单块 459 tok，而整段 C 才 672，离 1450 差一倍多，所以
`#63` 之前那把尺子对这一格是瞎的（`test_the_c2_ruler_bites_on_its_own_while_the_total_stays_shut`
压的就是这一格，放宽 `c_belief` 到 9999 的对照是 `test_a_c_belief_loose_enough_to_hold_the_card_hands_it_over_whole`）。

`#67` 补另一半：装配器知道自己砍了几刀，日志里只留着"砍完还剩多长"。`region_tokens.C2` 是砍**以后**
的长度，单看它分不出"这张卡本来 11 条"和"本来 14 条被削掉 3 条"——后者才是模型收到的处理差异，而预算
宽紧本来就是轴的一部分，两臂的 C 长度允许不同。这一条链走通了五站：`Prompt.card_claims_dropped`
（`src/wolfengine/assemble.py:57`）→ 落盘白名单（`src/wolfengine/assemble.py:310`）→
`metrics.region_budget_check` 那两格 `card_*`（`src/wolfengine/metrics.py:981`）→ 臂级聚合
（`src/wolfengine/batch.py:432`）→ 表尾"削过主张卡的 prompt：A 臂 0 个、B 臂 109 个（最狠的一条少发
14 条指控）"。两格 `card_*` 故意算在 `meta.regions` 那道守卫**之前**：一把没量过的尺子不该顺手抹掉
两格不需要尺子的读数（`test_a_log_without_the_caps_in_meta_prints_null_not_zero`）。判据落点：
`tests/test_wiring.py`（现 58 条、跑起来 79 个用例）里那六条，和
`tests/test_batch_paired.py` 里那四条，另有一条在 `test_cli.py` 的 audit 出口。

11 具变异体**全部 CAUGHT**（12:01:58Z→12:04:24Z 串行，`/tmp/mut_run.py`，每具跑完 `cmp` 逐字节还原、
跑前先 `import` 一遍证明它编译得过）：

| 具 | 改法 | 具名证人 |
|---|---|---|
| Z1 | 触发条件不再读 `c_belief`：这把尺退化成只记账不下刀 | `test_the_c2_ruler_bites_on_its_own_while_the_total_stays_shut` |
| Z2 | 刀数恒 0：读数和文本少掉的行数脱钩，日志说"没砍过" | 四条一起红（含 `test_c_thinning_keeps_the_newest_claims_and_the_seer_record`） |
| Z3 | 地板那一支不记刀数：砍到只剩查验记录时写 0 | `test_c_thinning_stops_at_the_floor_without_eating_the_seer_record` |
| Z4 | 落盘白名单少一行：Prompt 上活得好好的字段进不了日志 | 红 8 条，等于这一格的那张读者名单 |
| Z5 | 无 caps 的早退不带 `card_*`：没量过的尺子抹掉两格不需要尺子的读数 | `test_a_log_without_the_caps_in_meta_prints_null_not_zero` |
| Z6 | 一臂有读数一臂没有时，`any` 把整句说成"两臂都没有" | `test_only_one_arm_having_the_knife_count_is_named_rather_than_summed` |
| Z7 | 削卡 prompt 数不再要求真的削过：写了 0 的也算一个 | `test_audit_reads_the_fold_rounds_out_of_the_requests_it_writes_them_into` |
| Z8 | 缩卡循环拿 `c_total` 当尺子：最少那一刀的算术失去读者 | `test_the_c2_ruler_bites_on_its_own_while_the_total_stays_shut` |
| Z9–Z11 | 臂级聚合三格各一具 `all`→`any`：一臂里只有一局丢了证人也算整臂没量过 | `test_half_an_arm_without_the_witness_keeps_the_reading_that_survived` |

Z9–Z11 第一版是 **SURVIVED** 的：`tests/test_batch_paired.py` + `tests/test_cli.py` 122 条全绿，因为
已有的臂级用例只有"全有"和"全无"两档，半缺（一臂里一局丢了读数、另一局还在）那一档没有读者——
`_drop_request_field` 补上 `limit` 之后三具各自红在那一条新用例上。这是同一族教训的第三个形状：
`None` 的判据写的是"一局都没量过"，只测两端等于没测这条判据。Z4 一次红 8 条不是"太敏感"，是那一格
在 audit、臂级表、两处"没有读数 vs 0"里各自有一个读者，这一具的红集就是那张名单。

重跑：`.venv/bin/python /tmp/mut_run.py`。


#### 法官这一轮要谁做什么，以前只有写它的那一行知道：`#68`

`request.assigned_act` 从落盘那天起零读者。12:19:14Z 实测：两局 mock 的 43 条 speech 请求**全部**带着
指派，`grep -rn assigned_act src/ tests/` 却只命中写它的那一行。audit 里已经有 `speech_acts`，但它数的
是座位**采取**的 act；§7 第 1 条要防的那句话——"是不是只对指派表说 listen"——问的是指派与采取之间的
差，只数后一半就答不了。这不是"多加一个读数"，是把已经每请求都在落盘的一格接回可读的地方。

分母借不来。`soft_flags` 里的 `act_not_as_assigned` 只记**被打回过**的轮，"没指派"、"指派了且听了"、
"指派了没听"三者在它那里都是"没有这条码"，所以分母只能来自 `request` 本身。于是
`assignment_compliance`（`metrics.py:499`）读的就是 `e.request`，而 `cli.py:347` 把它挂在
`speech_acts` 旁边：两格并排，一格是行为，一格是行为对指派的符合度，谁也不替代谁。

"0 与缺席"在这一格上要逐字段重判，不能继承别处的结论：`recorded`（这个键在不在记录里）与 `turns`
（值不是 null）分开印，两格比率在没有指派轮时是 `null` 而不是 0.0。老日志读出的 `recorded` 为 0 说的是
"没人量过这件事"，新日志读出的 58 与 21 说的是"量过了，这一局有 21 个指派轮"——把前者印成后者，报告就
会把"升级之前跑的批"说成"那批桌没有指派需求"。

12:28:35Z 那份 audit 读数里两格比率都是 1.0，`by_assigned` 与 `speech_acts` 逐键相同。这不是闸门通过，
是 mock 桌由构造就听指派（`actors.py:224` 直接取 `legal.assigned_act`，真读数要等端点）。分辨力另有钉：
`test_a_forked_act_moves_the_final_rate_and_leaves_the_first_try_one_alone` 只改一条 `payload.act`，
要求 `obeyed_final` 掉下来而 `obeyed_first_try` 不动——两格一起动就说明其中一格读错了东西。

基准先自证：跑电池之前 `tests/test_m3_gate.py` 与 `tests/test_cli.py` 74 条全绿（12:40:15Z），任何红才
算得在变异体头上。10 具（`/tmp/mut_assign68.py`，每具先 `import` 证明编译得过、跑完 `cmp` 逐字节还原）：

| 具 | 改法 | 结果与具名证人 |
|---|---|---|
| Y1 | 分母数成"带这个键的轮"：明确没指派的那些轮被算进分母 | CAUGHT 3 条，含 `test_the_assignment_denominator_counts_only_turns_that_were_assigned` |
| Y2 | `recorded` 塌进 `turns`："没人量过"与"这批桌没有指派轮"又成了同一个数 | CAUGHT，`test_a_log_from_before_the_assignment_field_says_it_has_no_reading` |
| Y3 | `obeyed_first_try` 改读最终 act：两格成为一个东西的两个名字 | CAUGHT，只有 `test_a_forked_act_moves_the_final_rate_and_leaves_the_first_try_one_alone` 一条——正是那条用例存在的理由 |
| Y4 | `by_assigned` 数座位做了什么而非法官要它做什么：与 `speech_acts` 同义 | CAUGHT 2 条，含 `test_an_unrelated_refusal_is_not_the_same_as_ignoring_the_assignment` |
| Y5 | `recorded` 用真值判断而非键存在：`assigned_act: null` 被当成缺键 | CAUGHT 2 条（Y2 的两条同名，代码处不同） |
| Y6 | `obeyed_final` 的比较对象换成 payload 自身：恒真 | CAUGHT 2 条，同 Y4 那对 |
| Y7 | `obeyed_first_try` 只看 attempts 非空（即 M2 `refused_turns` 的算法） | CAUGHT，`test_an_unrelated_refusal_is_not_the_same_as_ignoring_the_assignment` 一条 |
| Y8 | audit 不打印这一格：写侧照旧落盘，读侧回到零读者 | CAUGHT 2 条，含守出口名单的 `test_audit_prints_metrics_and_nothing_else` |
| Y9 | 去掉 `isinstance(e.request, dict)` 这块防御 | **SURVIVED**，74 条全绿 |
| Y10 | `(a.get("violations") or ())` 换成 `a.get("violations", ())`：null 与缺失不分 | **SURVIVED**，74 条全绿 |

八具 CAUGHT、零"多红的"（每具的红集就是事先点名的那份）。Y9 与 Y10 是**事先赌它活**的对照组，赌的是
"这两块防御没有读者"，赢了的处理方式各不一样：`request` 在整个模块里被所有读者当作 dict 直接用
（`cli.py` 的 audit、`m7_cost_profile` 都是 `e.request.get(...)`），只在这一格额外设防就是假装存在第三种
形状——所以删掉；`violations` 那一支防的是 JSON 里显式的 `null`，而"读进来可能是 null 就当缺省"是本模块
的既有写法（同一个 audit 函数里 `int(e.request.get(...) or 0)` 就是它），所以留下。同一族判据，两种处理，
差别在别的读者怎么做，不在这一个函数怕什么。

重跑：`.venv/bin/python /tmp/mut_assign68.py`。


#### 写侧自述"防的是一个真实的失败模式"，而这条守卫一条证人都没有：`#69`

`events.py:332` 那个 `idempotency_key` 早退分支，在 docstring 里给自己写了一句很硬的话："guards a
genuine failure mode, not a theoretical one"——客户端超时、服务端其实已经完成、重试路径再记一次，于是
一条不可逆的事实被写两遍。这句话是真是假不由写它的人说了算：12:48:47Z 先把那四行整块删掉跑了一遍全
仓库，**只红 3 条 / 760 绿**，而 3 条里只有 2 条是真证人（`test_one_marker_per_distinct_fold_state`
与它的直播版，量的都是折叠标记那个调用点），第 3 条是文档行号引用被这次删除顶错位——不是行为。
VOTE 与 NIGHT_ACTION 这一路一声不响。守卫留不留，在最不可逆的那两类事实上没有读者。

另一头先量了一次反向的：12:48:36Z 从 `agent.py` 里那条 `idempotency_key` 的键里去掉 `as_of` 那一截，跑金丝雀局
10 红 / 28 绿。所以这一格的两半不是一回事——"键里带轮次标记"早有 10 位证人（PK 复投不被第一张票
吞掉，钉它的是 `test_a_tie_opens_a_pk_and_the_revote_is_its_own_ballot_wave`），缺的只有"同键的第二
次提交不落第二行"这一个方向。四条新用例因此只钉这一格（都在 `tests/test_agent_turns.py`，都走真
`agent.take_turn` 出口，不手写键串）。

`hard` 那个元组的两个成员各自要有证人：`test_a_repeated_submission_of_the_same_kill_leaves_one_fact`
钉 `Kind.NIGHT_ACTION` 那一半，`test_a_repeated_submission_of_the_same_ballot_leaves_one_fact` 钉
`Kind.VOTE` 那一半——W4、W5 各只红一条，正是"少锁一种事件"这种写法的形状：看起来只是窄了一格，测试
不会红，而票是这一局里最不可逆的那类事实。第一条里还写着 `len(actor.ctxs) == 2`，为的是不让下一个读者
以为守卫顺手省了一次模型调用——它在写侧，不在问侧。两条都读字节
（`(tmp_path / "t.jsonl").read_bytes() == after_first`），因为"没写第二遍"本来就是关于文件的主张：
内存里的计数可以对，而文件已经多了半行。

反方向另钉一条。`test_todays_ballot_does_not_swallow_tomorrows` 在两次提交之间把 `day` 加一：今天的
票不许把明天的票吞掉。写这条的理由是它比"往回吞"更便宜地溜过去——把键算成
`phase:seat:as_of` 这种"看起来更安全"的写法可以一路绿灯通过原有全部测试，代价是每一局第二天起没人投
得出票。还有一条是划界的：`test_the_same_words_twice_in_one_day_are_two_facts` 要求同一座位同一天
说两句照样落两行，并且 `not any("_idem" in e.payload ...)`——软事件不许挂键。一个写侧的守卫一旦放宽
到发言，改掉的是读侧的整个行为分布（`speech_acts`、`passivity_rate`、折叠窗口都按条数算），而观众看到
的只是"这人一天只说了一句"。

顺着键去读的时候，`events.py` 的自述另外露了一处：它说调用方传的是 `f"{phase}:{day}:{actor}"`。两个
字段都不对——真号是 `f"{phase}:{day}:{seat}:{as_of}"`，`actor` 从不参与，而 `as_of` 恰恰是唯一的轮次
标记。留着这句错话的代价不是"文档不准"：照着"键里没有轮次"去理解，PK 复投被第一张票吞掉就成了设计
如此，而 W2 证明那 10 位读者要的正是轮次。已改成与代码一致（行内改、**行数不变**——文档里点 `events.py`
行号的那几处引用，一行都不许被这次编辑顶错位）。`agent.py:376` 那条讲 `as_of` 的注释本来就写着"没有
轮次标记，复投就会从日志里消失"——写注释的手知道，测试的名单里没有人。

基准先自证：跑电池之前全仓库 0 红（12:55:31Z，`767 passed in 47.04s`）。7 具（`/tmp/mut_idem69.py`，
每具先 `import` 证明编译得过、跑完 `cmp` 逐字节还原）：

| 具 | 改法 | 结果与具名证人 |
|---|---|---|
| W1 | `idempotency_key` 的早退四行整块删掉 | CAUGHT 4，红集与事先点名的四条逐一对齐（含折叠标记那两条） |
| W2 | 键里去掉轮次标记那一截 | CAUGHT，金丝雀局 10 红，含 `test_a_tie_opens_a_pk_and_the_revote_is_its_own_ballot_wave` |
| W3 | 键里去掉"哪一天" | CAUGHT，全仓库 26 红 / 741 绿 |
| W4 | `hard` 收缩成只剩 `Kind.VOTE` | CAUGHT 1，只有那一刀的用例红 |
| W5 | `hard` 收缩成只剩 `Kind.NIGHT_ACTION` | CAUGHT 1，只有那张票的用例红 |
| W6 | 落盘的 `_idem` 写成常量 | CAUGHT 5：点名的四条 + `test_region_b_is_identical_across_seats_in_the_same_wave` |
| W7 | （对照）扫描顺序 `reversed` 改正序 | **SURVIVED**，49 绿 |

W2 与 W3 那份 `expect` 是空的，这是纪律不是疏漏：变异预期只能来自读过的断言，金丝雀局那 10 条、那 26
条各自钉的是什么我没逐条读，所以那两跑读的是"这一格有几个读者"，不是"我猜有几个"。W3 的读数反过来
改变了这片要不要再补用例的决定：夜间那一支 `as_of` 传的是 `None`（`phases.py` 里只有投票与 PK 复投给
它赋值），同一个座位第 1 夜与第 2 夜那两刀之间唯一的分隔就是日号——这一支不需要新写一条"夜间跨天"，
26 位证人比一条新断言说得更多。

W6 多红的那一条不是误伤，是这一轮量出来的新事实：幂等键同时是**缓存波次的身份**。
`test_region_b_is_identical_across_seats_in_the_same_wave`（`test_prefix_stability.py:93`）把每次请求
的 `as_of` 当分组依据，断言"同组内 A+B 字节只有一套"；键被拉平之后两波并成一组，组里出现两套字节。
plan §5 的 batch prefill 模型靠的就是"同一波九个座位被问的是同一份前缀"——这件事以前只在那条用例的
docstring 里说过，没人在代码里写下"键就是波次标签"。

W7 是事先赌它活的对照组，而它的活是**可证的**：守卫维持"每个键至多一条记录"这个不变式，那么正序扫还
是倒序扫都只会撞上同一条。所以这一支不是"没测到的分支"，是"两种写法都对到无法观察"——留着原样，不为
它单设断言。除 W6 那一位事先没点名的证人之外，每具的红集就是点名的那份。

重跑：`.venv/bin/python /tmp/mut_idem69.py`。


#### §5 整段区域几何买的那笔折扣，在产物上没有一个读数：`#70`

plan §5 的 A/B/C 分段、字节稳定、同波共享前缀，全部是为了让端点省掉 prefill。而"省掉了没有"这件事
在链条上只有一个地方能说清：端点在 `usage.prompt_tokens_details.cached_tokens` 里报回的那一格。
`transport` 的落盘白名单当时只放行三个**平铺**计数，那个嵌套块整个丢掉——于是日志里
`response["usage"]` 唯一的读者是 `tests/test_no_secrets.py`，它证明的是"密钥没漏进去"，不是
"缓存命中了"。换句话说：这一格不是没测到，是**测到了之后被自己扔掉的**，而扔掉的地方只有一行。

修法在三处，每处各有一个形状要钉：

- `transport.usage_from()`：白名单多放行一格，把嵌套的 `cached_tokens` 摊平带走。0 与缺席分开判：
  显式的 `0` 必须落盘（那是"回答了：一次都没复用"），没报、或 `prompt_tokens_details` 是个字符串
  这样的坏形状，就不许写成 0（那是"没问过它"）。这条白名单同时是**安全边界**：上游哪天加一个带
  服务器日志的字段，只有名字在名单上的能进语料。
- `metrics.prefix_cache_reuse()`：分子分母取自**同一批调用**（和 `m7_cost_profile` 的 `asked`/`used`
  同一条纪律），只报分子没报分母的那些单列在 `unpairable`，按相切开的 `by_phase` 用来定位"哪一波
  把前缀弄丢了"。
- `audit` 顶层多一格 `prefix_cache`，`docs/metrics.md` 里给了它的 `jq` 复敲命令。

两处量出来的事实，都不是从代码读出来的：

1. `--mock` 桌**当不了这一格的证人**。13:19:26Z 直接读一份现写的 mock 日志：`response` 是空的
   `{}`，于是第一版两条 audit 用例拿着它跑出 `calls: 0`、断言当场红。改成在测试里伪造
   `response`（一半补 `usage`、一半不补）之后才有分辨力——而 `forged_n` 那次取错了奇偶，"补数"
   与"计数"落在相反的那一半上，这种错不会红在实现上，只会红在自己的计数器上。
2. 分母那一行**存在两份**。13:34:34Z 那跑电池，P5（摘掉延迟过滤）报的是 `ANCHOR(AMB)`——锚点不
   唯一，因为 `m7_cost_profile` 里有一行逐字相同的 `calls = [...]`。把变异限定到 `prefix_cache_reuse`
   那一份之后它 **SURVIVED**：全套 0 红，"这批调用"这个判定没人读得住。这一格于是先补断言
   （`test_the_cache_denominator_is_the_same_batch_of_calls_m7_bills`，钉的是绝对值 2，不是两格互等），
   再把两份实现收成一处 `metrics.timed_decisions()`。收成一处之后电池反过来更狠：P5' 现在一次动
   两个读者，set1 红 21 条。

电池（`/tmp/mut70.py`，13:37:51Z 与 13:38:21Z，基准 13:37:44Z 五文件 127 passed 0 红；三个文件
`cmp` 字节还原）：

| 具 | 改动 | 结果 | 具名证人 |
|---|---|---|---|
| U1 | 摊平那步类型判错（`dict` 写成 `list`） | CAUGHT | `test_the_nested_cached_token_count_survives_the_usage_whitelist`、`test_zero_and_silence_are_two_different_answers_in_the_usage_block` |
| U2 | 真值判断代替 `is not None`（0 被当成没说） | CAUGHT | 后者单抓 |
| U3 | 白名单放宽成原样透传 | CAUGHT | `test_a_successful_answer_is_parsed_into_the_fields_metrics_reads`（钉的就是"指纹字段不许落盘"） |
| U4 | 去掉类型守卫 | CAUGHT | `test_zero_and_silence_are_two_different_answers_in_the_usage_block`，以 `AttributeError` 的形式——**这一具是崩溃而不是判错**，记在这里是因为它和"红"长得一样 |
| P1 | 分母换成自己估的 `total_tokens_est` | CAUGHT | 三条 + `test_a_usage_block_written_into_the_log_moves_the_reuse_ratio` |
| P2 | 只报分子的那次补 0 进分母 | CAUGHT | `test_a_cached_count_with_no_prompt_count_is_left_out_of_both_sides`（`ZeroDivisionError`） |
| P3 | 无配对时印 0.0 而不是 `null` | CAUGHT | `test_never_measured_and_measured_zero_are_not_the_same_answer` + audit 那条 null 用例 |
| P4 | 按相切开是假的 | CAUGHT | `test_the_per_phase_split_locates_the_wave_that_lost_its_prefix` |
| P5 | 共用的分母不再要求记过延迟 | CAUGHT | `test_the_cache_denominator_is_the_same_batch_of_calls_m7_bills` |
| P6 | 分母放宽成整份日志（丢掉 `decisions()` 那半边） | **SURVIVED，且可证等价** | 见下 |
| C1 | audit 那一格接了但是空的 | CAUGHT | 两条新 audit 用例（键集合那条**抓不到**它：键还在） |

P6 不是没测到，是**今天测不出来**：全仓库只有一处往记录里写 `response`（`agent.take_turn`，
`src/wolfengine/agent.py:383`），它追加的 kind 恰是 `DECISION_KINDS` 那五个，所以"有延迟"今天蕴含
"是决定"。两半合起来才是一句话，冗余的那半边留在原地并在 docstring 里说明它是**当前调用点的不变量、
不是日志格式的性质**——第六种带答案的 kind 出现那天，它就是拦住账单的那半边。

这一轮连带被文档闸门抓红的账（都是真红，不是误伤）：`test_cli` 的条数三处（50/59 → 52/61）、
套件总数（767 → 775 → 776）、`cli.py` 的 5 处行号引用。其中一处值得单独记：README 里
`#50` 与 `#57` 两格对**同一行**写了两个号（`cli.py` 的 616 与 620，真相在 623），而闸门一直是
绿的——它只要求"那个号 ±2 行里出现文档旁边那个 ≥4 字符的标识符"，而 `except`、`events` 这种词在
谁附近都会出现。行号闸门的这一处弱点是 `#72`：它既放过一个错号，也会把一句**引用旧号**的历史陈述
当成活的引用读红（这一小节自己就撞上了，所以那两句改成了不带冒号的写法）。

还没有的那半件：真实的命中率。端点 13:10:28Z 仍探不到（2 秒内没有 TCP 响应），而仓库里现存的三份
真日志（`data/2026092*_g*.jsonl`，13:38:52Z 逐行读过）连一个 `response` 块都没有——那是 09-20
上游 500 打断的那几局。所以这块现在的每一格形状都来自伪造日志，`prefix_cache` 上真数要等 M0 复跑。

**这一句的后半到 2026-09-22T15:25:58Z 起只对一半**：`tests/test_live_path.py` 新增的两条用例让
`cached_tokens` 第一次走完了"从一根线到 `wolf audit` 那一格"的全程，所以"每一格形状都来自手搭
日志"不再成立——线仍然是桩的，报的仍是测试写死的 700，不是端点报的数。真数照旧等 M0，
账见〈端点上那一格到 audit 之间还有两跳没有人读：`#74`〉。


#### 体检脚本自己抄了一份 usage 读法，于是一个 200 被说成端点故障：`#71`

`#70` 把"这一格有没有值"收进 `transport.usage_from()` 的时候，`scripts/calibrate.py` 的
`Client.complete` 里还留着第二份实现——一份只做 `(u.get("prompt_tokens_details") or {}).get(...)`
的链。它认得 dict 和缺席，认不得**别的形状**：那一格是个字符串、是个列表，或者整块 `usage` 为 null
时，`.get` 抛 AttributeError，而 `complete()` 末尾有一个把一切都吞成 `ok: False` 的兜底 except。

后果不是"少一格读数"，是**归因错到端点头上**。`apc_visibility` 那一段把 `r["ok"]` 原样写成
`probe_ok`，于是"端点回了 200 和一份我们没解析的 usage"与"端点不健康"在报告里是同一格；而 M0 体检
是全工程唯一有权说"这个端点怎么样"的地方，它的默认 `tries` 是 3，所以这句话还要被重试三遍才写下。
一条只存在于体检里的判据，第二次被用在了真日志的白名单上——这两处对同一个形状说不同的话，就是
§5 那笔折扣将来读错的方式。

修法只有一处：`complete()` 现在问 `usage_from()` 要那三格平铺后的计数，脚本里不再出现嵌套读法。
两条守卫的分工是量出来的，不是安排的（`/tmp/mut71.py`，基准 0 红 14:19:57Z，六具跑完 14:21:25Z
（同一套改动 14:17:13Z 先跑过一轮，逐具结论一致），`scripts/calibrate.py` `cmp` 字节还原）：

| 具 | 改动 | 结果 | 具名证人 |
|---|---|---|---|
| M1 | 退回本轮修掉的那一份抄写 | CAUGHT 2 | `test_an_unparsed_usage_shape_stays_a_shape_not_an_outage`（红在"一个字符串"那一格，`AttributeError: 'str' object has no attribute 'get'`）、`test_the_script_asks_transport_for_the_usage_block_and_keeps_no_copy` |
| M2 | **正确地**内联（isinstance 齐全，四种形状都答对） | CAUGHT 1 | 只有结构那条：`脚本里还留着一份嵌套读法`。这一具是那条字面量守卫存在的全部理由——行为用例对它完全无感 |
| M3 | 丢掉 `or {}`，`usage` 整块为 null 时抛 | CAUGHT 1 | 行为那条，红在"整块 usage 为 null"那一格（`'NoneType' object has no attribute 'items'`） |
| M4 | 把缺席写成 0 | CAUGHT 1 | 行为那条的 `got["cached"] is None`；`good` 那一格不受影响（真值照过） |
| M5 | 字面量干净但读错地方（拿顶层 `cached_tokens`） | CAUGHT 3 | 行为那条 + 结构那条 + 排练里的 `test_the_verdicts_are_read_off_the_wire_not_written_into_the_prose` |
| M6 | 删掉 import，调用点剩一个会 NameError 的名字 | CAUGHT 3 | 行为那条 + 排练两条（预检直接 `SystemExit`） |

**M5、M6 两具的预期是我写错的**：我以为它们只红行为那一条，因为结构那条"只看字面量"。它确实以
字面量开头，但末尾还有一格真调用作正控制，所以任何把这一格读坏的做法它都会跟着红。这条更正记在这里
是因为它改了两条守卫的分工描述：能单独区分"行为对、但仍分家"的是 **M2** 那一具，不是"M5 那一类"。

两份报告的形状没动：`test_the_committed_report_is_still_what_the_code_renders` 在这六具里一直是绿的，
因为这次改的是"遇到没见过的形状时说哪句话"，而现存那份报告来自一个形状正常的端点。

还没有的那半件仍然是端点：排练桩（`tests/test_calibrate_rehearsal.py`）喂的是 dict 那一支，所以
"列表形状的 usage 真在场吗"这个问题只有端口开着才有答案。改动的意义在于**那一天红的是哪一格**——
以前会是"端点不健康"，现在会是"这一格没说"。

#### `phases.py` 的 docstring 写了七八句"这里曾经错过"，其中六句当时没有读者：`#73`

这一片不找 bug，找**读者**。`phases.py` 里有一串"这件事是故意的，因为错过"的散文：封票靠 `as_of`
而不是靠写入顺序、改票那一轮要接管 phase、猎人的合法集必须在弹出队列**之前**算、两瓶药用完的女巫
不该再被问、遗言只属于今天早上倒下的人且每人一段。"修好了"的证据应该是**改动它会有名字红**，
而不是那段话还留在文件里。于是给每一句配一具变异体（`/tmp/mut73.py`，目标 `src/wolfengine/phases.py`，
12 具），先在焦点集上跑（14:29:09Z 基线 0 红 → 14:30:20Z 判决：10 具活），再把 10 具幸存者对整套
复核（14:31:39Z 基线 0 红 → 14:41:29Z 判决：9 具真活）。**这一片没改过一行产品代码**：九个幸存者
没有一个意味着行为错，全部意味着"那句话只有散文在替它作证"。补完 12 格之后（14:57:06Z 基线 0 红
→ 14:57:28Z 总账：只剩 P1、P4，`phases.py` 每次 `cmp` 字节不变）：

| 具 | 改掉的是哪一句 | 焦点集 | 全量复核 | 现在的证人 |
|----|----------------|--------|----------|------------|
| P1 | `_ballots` 把 `act != "vote"` 也当成一张票 | SURVIVED | SURVIVED | 等价变异，见下 |
| P2 | `_ballots` 不接管 `state.phase`，沿用调用方留下的发言相位 | CAUGHT 10 | — | 已有：`test_a_tie_opens_a_pk_and_the_revote_is_its_own_ballot_wave` |
| P3 | 封票的 `as_of` 不再切在开局那一刻 | SURVIVED | SURVIVED | `test_every_voter_is_asked_about_the_world_at_the_moment_the_phase_opened`、`test_the_pk_revote_gets_its_own_cut_after_the_first_tally_goes_public` |
| P4 | 静态前缀表退化成常数 9 | SURVIVED | CAUGHT 1 | 已有：`test_the_wave_is_capped_by_the_prefix_ladder_not_by_the_seat_count` |
| P5 | 弃票连击不再累计 | SURVIVED | SURVIVED | `test_two_waves_of_abstention_is_a_streak_and_a_vote_breaks_it` 等三条 |
| P6 | PK 轮不看 `house.tie_break`，平票就一定再来一轮 | SURVIVED | SURVIVED | `test_a_house_that_says_nobody_on_a_tie_never_opens_a_pk` |
| P7 | 两瓶都用完的女巫还被问 | SURVIVED | SURVIVED | `test_a_witch_out_of_both_potions_is_not_asked_again` |
| P8 | 预言家的一句 `pass` 也带出一个 target | SURVIVED | SURVIVED | `test_a_seer_with_nothing_left_to_check_reports_no_check` |
| P9 | 猎人开枪不看 `house.hunter_shoots_on` 的授权名单 | SURVIVED | SURVIVED | `test_a_hunter_exiled_by_a_house_that_only_allows_night_deaths_keeps_his_gun` |
| P10 | 合法集在弹出 `pending_shots` **之后**才算 | CAUGHT 16 | — | 已有 |
| P11 | 遗言不再限定"今晚死的" | SURVIVED | SURVIVED | `test_last_words_belong_to_tonight_not_to_an_older_undelivered_death` |
| P12 | 遗言"说过没有"改由调用方保证 | SURVIVED | SURVIVED | `test_a_seat_asked_twice_still_gets_one_set_of_last_words` |

表里"现在的证人"那一列不是抄来的：15:03:20Z 把 P5–P12 八具单独重跑了一遍，15:03:32Z 又单跑 P3，
红掉的用例名与格数一一对上（P5 红 3 格、P3 红 2 格，其余六具各红 1 格），`phases.py` 依旧 `cmp` 字节不变。

**焦点集上的 SURVIVED 不是缺口的证明**：P4 就是假阳性——它被
`tests/test_actor_contract.py` 里那条钉得死死的，而那个文件不在焦点集内（14:41:29Z 那次全量复核
唯一的 CAUGHT 就是它）。所以"某几个文件跑完没红"这句话的强度只到"这几个文件没读者"，
往"整套没有读者"跨的那一步必须靠全量复核补，账在这里记着。

**P3 为什么能活过两条看着最像的守卫**：`test_info_isolation.py` 里那条钉的是
`info.percept_for` 会按 `as_of` 过滤——过滤函数自己没错，错的是"有没有人把开局那一刻递进去"。
`test_prefix_stability.py` 那条更值得记：它说"同一波九个座位拿到字节相同的 A+B"，看着就是在测密封，
其实它是**单向**的——把 `as_of` 换成一个大常数（比如 `1 << 40`），九个人照样拿到同一份字节，
因为它测的是"一致"而不是"切在开局那一刻"。密封的两半各有读者才叫密封，这次补的两条分别钉两半。

**P1 是等价变异，不补测试**：`DAY_VOTE` 在 `legality.HARD_PHASES` 里，闸门只放
`legal.acts | {pass}` 过（`test_a_seat_that_never_answers_legally_is_recorded_as_the_engine_choosing`
已经钉住"回到调用方的 act 一定在合法集里"），所以 `_ballots` 那一行拿到的 `act` 只可能是 `vote`
或 `pass`——两种写法在任何一张真桌上给出同一个票型。这一具活下来不说明断言缺一格，说明那句
docstring 描述的 bug 早就被上游挡住了；把它记在这里而不是悄悄删掉，是因为下一个读那段散文的人
会问同样的问题。

**P8 是双保险，所以断言写在另一侧**：`rules.resolve_night` 本来就有一条 `already_checked` 兜底，
所以这具变异活下来的表现不是"预言家收到一份假查验结果"，而是 `t.blocked` 里多出一条记录。新用例
同时断"没有 SEER_RESULT"和"blocked 是空的"：只断前一条，会放过一个靠兜底而不是靠接线的答案。

**P6/P9 要披露的是入口，不是断言**：`HouseRules` 是冻结的、`GameState.house` 是只读属性，
CLI 今天不接受房规参数，出厂桌永远是 `pk_once_then_nobody` 加 `{wolf_kill, exiled}`。
`test_rules.py` 早就把这两条开关喂到规则层，唯独编排层这一路没人走过。翻开关的人不该先撞上
"读了也没用"，所以两条还是钉了——但它们是"这条支路接好了线"的证明，不是"产品能选它"的证明。

剩下那半件仍然不在这片的能力范围内：这 12 具全在替身桌上跑，P5 那条新用例钉到"提示行发给了
连投两轮的人"为止；一个真模型收到那行提示之后会不会改变弃票，是 M3★ 的问题。

#### 端点上那一格到 audit 之间还有两跳没有人读：`#74`

`#70` 修的是"读侧能不能把 `cached_tokens` 带到产物上"，三处各钉了一格形状，但那三处的证人测的都是
**手搭的日志**：`tests/test_prefix_cache.py` 里的 `Event` 是一行行拼出来的，`tests/test_cli.py`
那一格是往一份 mock 日志里塞了个 `response` 块。链上从"字节回到进程"到"那一格印进 `wolf audit`"
中间还有两跳——`HttpTransport` 把数往上交给 `LLM`、`CallResult` 把它落成盘上那行 JSON——这两跳当时
一条断言都没走过。

补的两条用例长在 `tests/test_live_path.py`（那一页的既有设定就是"一条链跑一局真桌，线用
`httpx.MockTransport` 桩"）：一根桩线每次回答都报 `cached_tokens: 700`，另一根一个字都不提。前者钉
`reported == calls == len(timed_decisions(...))` 的绝对值、`reuse_ratio == round(700/900, 4)`，以及
`wolf audit` 打出来的那一格与 `metrics.prefix_cache_reuse(events)` 逐键相同；后者钉的是 `null` 与 `0`
的分别能一路走到终端。**两条都没有改一行产品代码**——它们找不到的缺陷不在测试这一侧，那两跳本来
就是对的。

而"本来就是对的"不是一句证词，得有人读过才算。于是两向取证（`/tmp/mut74.py`，9 具，每具跑两遍）：
`before` 是**摘掉这两条**跑整套（当时 791 条全绿），`after` 是**只跑这两条**。两遍基线都 rc=0 红=0
（2026-09-22T15:15:54Z），跑完 15:24:53Z，被改过的四个文件 `cmp` 全部字节还原，之后整套 793 passed
（15:25:58Z）。

| 具 | 改动 | before（整套摘掉新两条） | after（只跑新两条） |
|---|---|---|---|
| C1 | `usage_from` 整块丢掉嵌套那一支 | CAUGHT 红 5 | CAUGHT 红 1 |
| C2 | 把"端点没说"写成 0 | CAUGHT 红 3 | CAUGHT 红 1 |
| C3 | `HttpTransport` 读对了但不往上传 | CAUGHT 红 3 | CAUGHT 红 2 |
| C4 | `as_response_dict` 往盘上写 `"usage": {}` | **SURVIVED 红 0** | CAUGHT 红 2 |
| C5 | `complete` 不往 `CallResult` 上搬 `raw_usage` | **SURVIVED 红 0** | CAUGHT 红 2 |
| C6 | `prefix_cache_reuse` 到错的地方读 cached | CAUGHT 红 6 | CAUGHT 红 1 |
| C7 | 分母缺角的那几次不数了（蒸发而不是披露） | CAUGHT 红 1 | **SURVIVED 红 0** |
| C8 | `audit` 的记录里没有这一格 | CAUGHT 红 3 | CAUGHT 红 2 |
| C9 | 缓存的分母自己另起一套 | CAUGHT 红 1 | **SURVIVED 红 0** |

C4 与 C5 就是这一节的理由：改动都落在 `transport` 之后、落盘之前，791 条用例**一条都不红**，
而它们在 `after` 列红得很具体——`KeyError: 'cached_tokens'`。旧用例再多，证明的都是"函数拿到数
之后怎么办"，没有一个在问"数到没到日志"。这是 `#70` 那三处形状之外的一条独立主张：安全白名单
放行了一格，不等于那一格走得完。

反方向也点名，免得这张表被读成"补两条就全线有保险"：C7 与 C9 只有旧读者抓得住，新两条抓不到
（`after` 列 SURVIVED）。抓它们的是 `test_a_cached_count_with_no_prompt_count_is_left_out_of_both_sides`
和 `test_the_cache_denominator_is_the_same_batch_of_calls_m7_bills`，两条的判据都建在手搭 `Event`
的绝对值上——那正是它们能盯住分母、却看不见"数有没有从线上进来"的原因。

另有一处顺带量到的耦合，记下来是因为它反直觉：C1 与 C2 在 `before` 列红的第一格出自
`tests/test_calibrate_guard.py`（`test_an_unparsed_usage_shape_stays_a_shape_not_an_outage`、
`test_the_script_asks_transport_for_the_usage_block_and_keeps_no_copy`）。那是 `#71` 让体检脚本改走
`transport.usage_from()` 之后**多出来**的读者——现在给日志侧白名单作证的人里有一个是体检脚本的守卫。
没有错，但下次那页用例被改，红的会是 `transport`，这层关系不该等到那时才发现。

还没的那半件照旧：桩线的 700 说的是"端点若报，我们带得到"，不是"端点报了 700"。真数仍然等 M0
复跑，而 `#70` 末尾那句"每一格形状都来自伪造日志"从今天起只对一半——见那一节末尾的更正。

#### §5 那笔折扣走到批这一级就断了：`#75`

`#70` 把 `cached_tokens` 做成了单局的一格，`#74` 证明它从线上走得进 `wolf audit`。但 M7 要回答的
从来不是"这一局复用了多少"，而是"**这一臂**比那一臂多复用了没有"。两臂对比的出口 `compare()`
当时一个相关的键都没有：批把十局日志读成一份报告，报告里凡是涉及前缀缓存的位置全是空的，读者
只能自己把各局 `audit` 的数字加起来——而"读者自己加"正是这份产物一直拒绝的事。

补的东西分两层。算术在 `batch.prefix_cache_by_arm`：把一个臂里每一局、每一次带延迟的调用汇成一个
池，比值是 **Σcached ÷ Σprompt**，不是"各局比值取平均"（八局的池和两局的池不该同样重），也不是
"第一局的数"；另附两格可披露的覆盖面——`n_games` 与 `n_games_silent`（有几局端点一个字都没报），
判据是 `reported == 0` 而不是 `calls == 0`：**问过而端点没说**与**压根没问**是两回事。出口在
`_prefix_md`，拼进 `comparison.md` 的「前缀缓存」一节：九列（臂/局数/调用/报了/没说/配不上/cached/
prompt/复用率），缺席的分子分母印 `—` 而不是 `0`，沉默的臂每臂跟一句"那是『没问过它』不是『一次
都没复用』"。

证人长在 `tests/test_batch_live.py`（四条，整局整批地跑，线仍然是 `httpx.MockTransport` 桩）：一根
线每答都报 `cached_tokens`，另一根从不提 `prompt_tokens_details`，于是同一个批里天然有一臂有数、
一臂沉默。四条钉的是池内相除（先自证夹具没退化：两臂的池比值与"各局平均"在这个夹具下**必须**不
相等，相等就说明夹具本身分不出这两种算术）、沉默臂的 `null` 一路到终端、`by_phase` 逐相加起来等于
臂级那一格，以及 markdown 里两臂各有一行、`—` 印得出来而 `0.0000` 印不出来。分子的绝对值**故意**
不走 `metrics` 而直接读原始 JSONL：让算术自己当自己的证人是最容易写的一种假绿。

两向取证（`/tmp/mut75.py`，八具，每具跑两遍）。`before` = 整套但摘掉新那四条（793 格全绿），
`after` = 只跑那四条。两遍基线都 rc=0 红=0（02:59:58Z），跑完 03:09:04Z，被改的
`src/wolfengine/batch.py` `cmp` 字节还原；整套 797 passed（45.85s，03:15:06Z 读到）。预期写在
脚本开头：八具在 `before` 全 SURVIVED。

| 具 | 改动 | before（整套摘掉新四条） | after（只跑新四条） |
|---|---|---|---|
| D1 | 臂级比值改成各局比值取平均 | CAUGHT 红 1（假证人，见下） | CAUGHT 红 1 |
| D2 | 池子只装第一局 | **SURVIVED 红 0** | CAUGHT 红 1 |
| D3 | 「没说」的判据换成「没问」（`calls == 0`） | **SURVIVED 红 0** | CAUGHT 红 2 |
| D4 | `n_games_silent` 写死 0（蒸发而不是披露） | CAUGHT 红 1（假证人，见下） | CAUGHT 红 2 |
| D5 | 沉默臂的复用率印成 `0.0000` | **SURVIVED 红 0** | CAUGHT 红 1 |
| D6 | 沉默臂的分子分母印成裸 `0` | CAUGHT 红 28（崩，不是读） | CAUGHT 红 4 |
| D7 | 那一节根本不拼进 `comparison.md` | **SURVIVED 红 0** | CAUGHT 红 1 |
| D8 | 两臂共用同一条臂的事件（复制粘贴的那一类错） | **SURVIVED 红 0** | CAUGHT 红 3 |

预期错在三具上，三具都要点名，因为三种红是三种不同的东西。D1 与 D4 红的是
`test_doc_citations.py` 那道行号闸门：变异是在 `prefix_cache_by_arm` 里插行，把文档中一处指向
`def compare` 的行号引用顶到了别的内容上——它读到的是**行数**，不是行为。D6 红 28 格更不值钱：那具
把 `_cell(...)` 换成裸整数，`"\n".join` 当场 `TypeError`，28 格全从同一处崩出来，它没有"被读过"，
它根本没跑完。所以这张表真正说的是：**批侧这一片在补用例之前没有任何行为读者**，五具（D2/D3/D5/
D7/D8）在 `before` 列安静存活、在 `after` 列各有具名证人，这五条才是这一节的理由。

D3 是这一族里最值得单独留着的一具，因为它抓的不是"数错"而是"说法错"：把判据从 `reported == 0`
换成 `calls == 0` 之后，一份两臂都跑完、每次都问了端点的批，`n_games_silent` 依然是 0、依然一句
"没问过它"的披露都不出——沉默被吞成了"没有沉默"。它红的两格都指向 markdown 那一格，也就是说，
这个缺陷只有从报告那一侧才看得见。

修的过程中撞开了一件不属于这一节的事，记在这里因为它是量出来的而不是读出来的：夹具原本拿
`Config.temperature` 当处理轴，跑起来发现两臂发出的字节一模一样——86 次调用、86 个 `temperature`
全是 0.9，线上读不出这个字段。改轴为 `temperature_ladder`（`LlmActor.act` 真正取的那一格）之后夹具
才有效，但那根假轴本身还躺在文档和 `--set` 的帮助文本里，于是登记 `#76`。

另两处是这次改动自己带出来的后果，都改在测试而不是产品上：新表格接在「区域预算」后面，把
`test_batch_paired.py` 里那个按节切行的辅助函数撑破了六格（它原来只切不收尾），补上"切到下一个
`## ` 为止"；`def compare` 因为新函数插入而下移，文档里那一处行号引用跟着改了号。

---

#### 轴守卫把帮助文本当成了读者，于是一根从没上过线的字段当了招牌处理轴：`#76`

`#75` 的夹具本来要拿 `Config.temperature` 当处理轴——它就是干这件事的：两臂只差一个温度，看前缀
缓存的复用率动不动。跑起来量到的却是 86 次调用、86 个 `temperature` 全是 0.9，两臂发出的字节一模
一样。字段没坏，`config.py` 里就写着 `temperature: float = 0.9`；坏的是**没有任何代码读它**。
改之前 `src/` 里那个名字的属性访问是零处，而它在 `cli.py` 的散文里出现过四次：`--set` 那条 docstring
举了两次例（`--set B.temperature=0.6` 与 `--set temperature=0.6`）、`--set` 的帮助文本一次、`--axis`
的"例如 temperature"一次。`docs/comparison.md` 的招牌例子（`data/temp09-vs-06`，
`--set B.temperature=0.6`）教的也是这一根。

守卫为什么不红？因为 `_readers` 是**按行扫正则**判"有没有人读这个字段"的：`\.{field}\b` 匹配到
`--set B.temperature=0.6` 这一串字符就算有读者。它当时唯一的排除项是注释行，而 docstring 和帮助
字符串都是代码——只不过里面写的是散文。于是"读取点"这个判据问的其实是"这个名字被提起过没有"，
两臂差一个标签也能出两份 `config_hash`、生成一份"低温臂 vs 高温臂"的对比报告，而九个人玩的是同
一套温度。这一格和 `#70` 那句"活在散文里的主张是缺陷"是同一族，只是这次说谎的不是产物，是
**守卫自己**。

改法是把手法对齐主张：读没读是一个 AST 上的事实，就去看 `ast.Attribute` 的 `attr`。同一个判据只留
一处实现（`_reads_attribute`），因为 `config.py` 那一侧的"这个方法被外面 calls 过没有"问的是同一个
问题——留一份正则、一份 AST，就是 `#42` 那两条守卫对打的翻版。

字段这一侧不删它，而是把它接上：`Config.temperature_for(seat)` 成为"这一座发什么温度"的唯一答案，
与 `token_budget_for` 同族——一个谓词只住一处。今天它只有一个读者（`LlmActor.act`），不像
`token_budget_for` 那样"同一处决定、两处读者"；住在一处的理由是一样的：第二份实现当下等价、没有
任何断言会红，直到有人改了梯子语义（`#73`/`#42` 那一族的老毛病）。语义是梯子优先——非空就逐座取、
短了就夹在最后一档；**空梯子**才是"没有逐座覆盖这回事"，每座取全局 `temperature`。出厂值跟着从
`temperature_ladder=(0.9,)` 改成 `()`：那个 `(0.9,)` 与 `temperature=0.9` 是同一个数写两遍，而它一旦
非空就把全局字段永远遮住，正是"字段是标签"的构造方式。顺带修掉一个崩溃：以前
`temperature_ladder=()` 会让 `LlmActor` 下标 `-1` 当场 `IndexError`，一局都开不了——所以"空梯子"
这个形状此前连试都没人试过。

三条新用例，两种角色要分清楚：
- `test_a_field_named_only_inside_prose_has_no_reader`（`tests/test_batch_paired.py`）钉判据本身：
  `cli.py` 里那句话确实出现过两次，而 AST 不认它是读者。它在改 `_readers` 之前是红的
  （`_readers("temperature", src)` 返回 `['cli.py']`），这条红是这一节的起点。
- `test_an_empty_ladder_ships_the_global_temperature`（`tests/test_live_path.py`）是**驱动**产品代码
  那一条：写它的时候红在 `IndexError`，修完红变绿，且它钉的是线上字节
  （`json.loads(request.content)["temperature"]`），不是函数返回值。
- `test_a_full_ladder_is_the_temperature_that_ships_seat_by_seat`（同文件）是**证人**：梯子逐座生效
  这件事今天就成立，所以它一写出来就绿——它补的不是缺陷，是"这一格从来没人读过"。座位号取自落盘
  日志的 `Event.actor`：区域 A 的说明书里有一句固定的"你是4号"，按提示词文本归座会把整局五十六次
  调用全记到 4 号头上（这一格另记 `#78`）。它同时钉"记下来的温度"与"发出去的温度"逐次同多重集，
  这是 `#74` 那两跳的同类主张。

两向取证（`/tmp/mut76.py`，七具，每具跑两遍）：`before` = 整套但摘掉这三条，`after` = 只跑这三条。
基线两遍都 rc=0 红=0（04:22:31Z），跑完 04:28:53Z，被改的三处 `src/wolfengine/config.py`、
`src/wolfengine/actors.py`、`tests/test_batch_paired.py` 各自 `cmp` 字节还原，末尾再跑一次**不加任何
变异**的对照（04:28:53Z，rc=0 红=0）——这一格是 `#79` 逼出来的，理由在下面。

| 具 | 改动 | before（整套摘掉新三条） | after（只跑新三条） |
|---|---|---|---|
| E1 | 梯子被忽略，每座都发全局值 | CAUGHT 红3（`tests/test_batch_live.py` 的池内相除、`null` 与 `0`、每臂一行那三格） | CAUGHT 红1（`test_a_full_ladder_is_the_temperature_that_ships_seat_by_seat`） |
| E2 | 空梯子回落到写死的 0.9 | CAUGHT 红1（`test_an_axis_field_no_code_reads_is_a_lie_not_a_knob`） | CAUGHT 红1（`test_an_empty_ladder_ships_the_global_temperature`） |
| E3 | 不夹紧，梯子短于座位数就越界 | CAUGHT 红24（**崩**：`config.py` 里那一格 `IndexError`，不是行为读者） | CAUGHT 红2（新两条都红） |
| E4 | 夹紧取反（`min` 换成 `max`） | CAUGHT 红24（同样崩） | CAUGHT 红2 |
| E5 | 演员重抄一遍内联下标，绕开那个函数 | CAUGHT 红24（崩在 `actors.py` 自己那格）＋两条守卫红，其中**行号闸门那条是假证人** | CAUGHT 红1（`test_an_empty_ladder_ships_the_global_temperature`） |
| E6 | 守卫退回按行扫正则（散文又变成读者） | **SURVIVED 红0** | CAUGHT 红1（`test_a_field_named_only_inside_prose_has_no_reader`） |
| E7 | 判据恒真：谁都算读者 | CAUGHT 红1（老那条轴守卫：inert 名单被说成"已经有人读了"） | CAUGHT 红1（同 E6 那条，红在 `assert True is False`） |

预注册（写在 `/tmp/mut76.py` 的页眉里，不事后改）兑现了四条、没兑现三条，没兑现的这三条更值钱：
E1 猜"before 就会红、由 `tests/test_batch_live.py` 抓住"——中了，而那三格是 `#75` 为了**另一件事**
写的夹具，`#76` 的处理轴是靠上一节的证人现抓的；E2 猜"只在 after 红"——before 也红了，红的是轴
守卫自己（回落写死 0.9 之后 `temperature_for` 不再取那个字段，AST 扫 `src/` 又找不到读取点，于是
它诚实报"`temperature` 没人读"，也就是**这一具把缺陷原样复现了一遍**）；E5 猜"before 全绿，因为
内联与函数当下等价"——**没中**：出厂梯子已经改成 `()`，内联版本在空梯子上当场 `IndexError`，
等价性被我自己那处改动破坏了，这一具因此没能演出它本来要演的那一出（"当下等价、断言不红"），
只有 `#73` 那一族还钉着它；E7 的 before 多红了老的那条轴守卫。E5 的 before 里那条
`test_doc_citations.py` 的红是那一处指向 `actors.py` 同一格的行号引用被插行顶偏（旧读数 224，那一
具下偏一行），**读的是行数不是行为**，与 `#75` 的
D1/D4 同一类，账记在 `#77`。

**这一跑之前还作废过一整跑（`#79`）**：首跑（03:50:04Z→03:55:40Z）里 E6/E7 两具只改测试文件，
却量到 24 红外加 `config.py` 那一格的 `IndexError`。对照跑（**不加任何变异**、同一条命令）印出同样
的 24 红，才把它从变异身上摘开：真凶是 E4 留下的 `src/wolfengine/__pycache__` 里那份 `config` 的
pyc。E4 把 `min` 改成 `max`，**改后文件与原文逐字节等长**，而 pyc 的有效性键只有 `(源文件 mtime
整秒, 源文件字节数)`——整文件还原之后这两项重新对上，Python 从此再不重编译：磁盘是干净的，进程里
跑的是变异体。`cmp` 报"byte-identical"却拦不住它，因为它比对的是文件不是执行。处置三条：删掉
`src`/`tests`/`scripts` 下四个 `__pycache__`（`.venv` 不动）、harness 的每个子进程带一次性
`PYTHONPYCACHEPREFIX`（变异窗口内的编译产物既不落进仓库缓存、也不复用仓库缓存）、电池末尾加一次
无变异对照（非 0 红即整跑作废）。上表只引用重测那一遍；首跑的 E5/E6/E7 三行数字作废。顺带核过
`#75` 的表没有同一污染：`/tmp/mut75.py` 八具里只有 D8 改后等长，而 D8 是最后一具、其后没有别的行
要记账，且那一跑末尾全套 797 passed（03:15:06Z）——坏字节码会让那次全套红，所以它没坏。

后果三处，都在测试与文档这一侧：`tests/test_batch_live.py` 的轴注释和页眉里那句"`actors.py` 真正
发上线的那一格"改了口径（现在由 `temperature_for` 决定）；`README.md` 与 `docs/metrics.md` 里那两处
指向 `actors.py` 同一格的行号引用（旧读数 225、新 224）因为演员那两行并成一行而改了号；
`README.md` 里一处对 `tests/test_batch_paired.py` 用例数与收集数的括号读数随那个文件多一条而失真
——**这次选择删掉那对括号**而不是顶成本天的数，因为那句话讲的是"判据落在哪个文件的哪几条里"，
而"现 N 条"是一个会被闸门每天核对的主张。

#### 行号闸门把"顶偏"和"点错东西"报成同一句话，于是变异电池拿它当行为证人：`#77`

`#75` 的表里有两具"before 就红"，红的其实不是行为：变异体往 `src/` 里插了几行，文档中指向该文件下游
的行号引用整体后移，于是这条闸门红了。同一件事在 `#76` 的表里又来了一遍（一具把某一处引用顶偏一行）。
两处当时都**如实披露成"不是行为读者"**，但那句话是我事后读红用例的正文读出来的——闸门自己只印
`那一行没有一个 [...]`，它既不说那个名字搬去了哪一行，也不说它是不是整个文件里都没有了。电池只 grep
`rc != 0` 的话，这一格和"这句话指错了东西"长得一模一样。

不改判据。号漂了**确实是缺陷**：`#72` 那轮实测过，读者照着敲 `sed -n 484p src/wolfengine/cli.py` 会读到
一行空气，而当时两个错号都绿。把窗口放宽回 ±2 更是刚收掉的那条路（13:48:25Z 量出来的宽窗口放过
`cli.py` 的两个错号）。所以这一格只能改**措辞**：红/绿判据一字未动，报错的那句话自带归因——

* 名字还在文件里：`那一行没有 [...]；它在第 N 行（差 ±k 行）`，N 取**离被点那一行最近**的一处，
  差带符号（往上漂和往下漂不是一回事）。
* 名字整个文件都没有：`那一行没有 [...]，整个 cli.py 里也找不到`——不编一个出处。
* 另外三类（文件不存在、号越界、句子里没写可核对的名字）原样保留。

**故意没有阈值**。一个"差 ≤3 行算漂移"的判起来更省事，但那个数是我拍的，而且没有任何断言会去核对它
——它就变成这条闸门自己最反对的那种东西：活在散文里的主张。要区分的人/脚本读那个符号差就行。

四具变异（`/tmp/mut77.py`，两向，目标只有 `tests/test_doc_citations.py`），`before` = 整套但摘掉新那条，
`after` = 只跑新那条：

| 具 | 改动 | before | after（只跑新用例） |
|---|---|---|---|
| H1 | 退回旧的那句混报 | SURVIVED | CAUGHT：`那一行没有一个 ['_games_error']` |
| H2 | 名字不在文件里时也编一个"它在第 N 行" | SURVIVED | CAUGHT：编出来的那句 `它在第 92 行（差 +3 行）` |
| H3 | 取第一处出现而不是最近一处 | SURVIVED | CAUGHT：报 `第 88 行（差 -40 行）`，而最近的是 121 |
| H4 | 差多少取绝对值 | SURVIVED | CAUGHT：`它在第 121 行（差 7 行）`，符号丢了 |

预注册四条全兑现。`before` 那半边尤其要兑现：老的两条探针只断言 `"那一行" in msg` 和另外三个分支的
关键词，措辞换成什么样它们都不在乎——所以这一格在补用例之前是**零读者**。基线两遍与末端对照都 0 红、
`cmp` 字节还原一致（05:08:10Z / 05:15:18Z）。H3 那一顺带量到一件事：`_games_error` 在被点的这个文件里
有定义加两处读者，所以"它想去第几行"必须取最近的那一处，取第一处会把读者支使到 40 行开外。

限界两条，都写在这里而不是 footnote。① 措辞带上了归因，但**只看 `rc` 的电池还是分不开**——这一格之后
电池引用行号闸门的红时必须把那句括号抄进表里（`/tmp/mut*.py` 已经在打红的正文行，抄是记账的动作）。
② 这条闸门验到底仍是"那个名字落在被点那一行上"：一行里同时有这个名字、但不是这句话要指的东西时，
它照样绿。名字与语义之间的这一格没有机器版，只能靠写的人把名字选准。

后果一笔，属于 `#72` 登记的第四类触发（**叙述里"某个 py 文件冒号带数字"那种写法会被当成 live 引用**，
这一轮我自己又撞了一次：那半句原本照形状写了一个举例的号，闸门当场报"没有这个文件"）：这一轮往
`tests/test_doc_citations.py` 里加了一条用例之后，README 里两处条数主张被条数闸门点了名。一处是
讲怎么重跑这一片的（现行数，改成 21 是对的）；另一处是 `#72` 那节里"那一跑时这个文件有几条"的
**历史读数**——顶成新数等于宣称 mut72 当时跑的是 21 条，那是假话。所以那一句改成了中文数词并当场
点名"那是历史读数、现行条数由那条闸门每天核"：既保住那一刻的账，也保住闸门不被绕。

#### 桩判官九座全自报 4 号，而摘掉那条新用例之后没有一句断言读得出：`#78`

先说清这一格住在哪里：**产品代码一行没改**。`src/` 里没有任何从提示词反推座位的代码（04:38Z 现查
`grep -rn '你是(' src/` 零命中），被问的座位是 `assemble.py` 那一行写的（`你是{percept.seat}号`），
用的是引擎自己知道的数。有毛病的是**端点还没上线这段时间顶替它的那只桩**：`tests/test_live_path.py`
里的 `oracle_answer` 要像一个读过说明书的模型那样答话，于是它得从提示词里读"我是谁"，手法是
`re.search` 配 `你是(\d+)号` 取第一个命中。而区域 A 的说明书里钉着一句教学用的
`假设你是4号，法官指派 act=accuse……`（`templates.py`），先撞上的正是它。九座于是全部自报 4 号：
`33/39` 份发言的归属是错的，`我是4号，` 成了九个玩家共享的前缀。

这一格不只是难看。`#66` 刚把 `template_top1_share` 的口径收成"那一句模板占本轮发言的比例"，而九个
共享前缀恰好是它会被当成行为去量的东西——桩把这句话喂给指标，读出来的塌缩就不是模型的。更要紧的是
**同一个文件里早就写着这一族的教训**：`CITE` 那条正则旁边就注释着"不要用 `\be\d+\b`，它会先撞上
区域 A 说明书里的两个例子 id"。教训记在相邻十行之内，后写的那条正则照样按第一个命中取。所以这一格
是 `#70`/`#76` 那一族的又一面：**散文是对的，代码没照它写**。

改法是把那句裸 `re.search` 提成一个具名常量 `SEAT`，锚到 `assemble.py` 真正落盘的那句话
（`你是(\d+)号。你的身份是`）；失配时**不写自我归属**，而不是猜一个——编出来的归属比缺失更糟，缺失
会被下面那道地板抓住，编造的不会。

新增的那一条 `test_the_stub_oracle_speaks_as_the_seat_it_is_asked_to_be` 的参照是落盘日志的
`Event.actor`，不是从提示词里再推一遍的座位——拿被检的那条判据当参照，它永远绿。它有两道地板：先数
自报座位的发言够不够多，再要求**九座每一座都至少有一份进了断言**，最后逐份比对 `speech` 的前缀是不是
`我是{actor}号`。限界写清楚：它验"答话的归属跟着被问的那一座走"，不验"引擎问的是不是对的座"——后
一句住在 `assemble.py` 那一行自己的断言里。

四具变异（`/tmp/mut78.py`，每具跑两遍），`before` = 整套但摘掉这一条，`after` = 只跑这一条：

| 具 | 改动 | before（整套摘掉新用例） | after（只跑新用例） |
|---|---|---|---|
| G1 | 退回"取第一个命中"（缺陷本体） | SURVIVED | CAUGHT，红在 `wrong`：`33/39 份…[(3, '我是4号'), (9, '我是4号')]` |
| G2 | 完全不自我归属（也是产品措辞漂移的替身：`SEAT` 一失配就是这个行为） | SURVIVED | CAUGHT，红在第一道地板：`只有 0 份自报座位的发言` |
| G3 | `SEAT` 收窄成 `[1-8]`，9 号那一整座掉出断言 | SURVIVED | 首跑 SURVIVED → 补第二道地板后 CAUGHT，红在 `silent`：`…等于没测：[9]` |
| G4 | 每座都往后挪一座（归属在变但全错） | SURVIVED | CAUGHT，红在 `wrong`：`39/39 份…[(9, '我是1号'), (6, '我是7号')]` |

预注册四条全兑现，包括**唯一一条预期活下来的**：G3 在脚本 docstring 里就写着"预期两半都绿，因为
`>= 20` 是聚合数、抓不住某一整座掉出去"。它兑现了，于是那条限界当场变成一条断言：第二轮（04:48Z）
在地板后面加"九座都要有份"，重跑四具——G3 转红，且红的必须是 `silent` 而不是聚合地板（归因也要对），
其余三具读数不变，`before` 仍四具全绿。两遍的基线与末端对照都是 0 红、`cmp` 字节还原一致
（04:47Z / 04:56Z），全套 **801 passed**（04:56:35Z，86.43s）。

后果：这一格没动任何产物口径，也没有一个已发布的数字来自这只桩的 `speech`（04:39Z 现查
`grep -rn template_top1 README.md docs/`，命中的全是字段名与口径叙述，没有实测读数），所以没有文档
需要改口。真端点接上之后这只桩退出舞台，但那条判据不换：**谁的发言就得归到谁那座**，参照是日志里的
`Event.actor`。

#### 胜负判据的参数名叫 `alive_roles`，而它读的是阵营；旁边那个按职业建键的孪生会把整局当场判狼赢：`#80`

先说这一格是从哪翻出来的。端点还起不来（05:36Z 现查：不带 key 打 `/v1/models` 是 401，鉴权那层活着，
推理那层没起），所以本轮换了条离线证据来源——`/tmp/deadscan.py` 用 AST 扫 `src/**`，找"除了自己的定义
行以外从没被点过名"的函数。九处命中里有一处不是"多余代码"，是一个**能把一局游戏打死**的陷阱。

`rules.py` 里 `winner_for(board, alive_roles)` 的第二个参数叫 `alive_roles`，docstring 第一句是
"Decided purely by which roles remain alive"，而它取的键是 `wolf` / `villager` / `god` —— 那是
`RoleSpec.team` 的取值，不是发到九个人手里的那五种职业 id。紧挨着它下面站着一个
`alive_role_counts(state)`，按 `role_of` 建键、全仓库零读者。两个都活着，名字一个比一个像调用说明，
这就是缺陷本体。

后果不是假设：按职业建键的 Counter 里根本没有 `god` 这个键，于是屠边那条 `get("god", 0) == 0` 恒真——
九口人一个没死也返回 `wolf`，`check_win` 顺手把 `phase` 拧成 OVER，整局在第一次判定就塌缩成一条开局
记录。新增的 `test_rules.py::test_a_role_keyed_counter_ends_the_game_before_it_starts` 把这个数钉成
读数：五键的职业表 → `"wolf"`，三键的阵营表 → `None`。

改法四处，产品行为一行没变：`roles.py` 里把 `team` 那格内联 `Literal` 提成具名别名 `Team`（它现在是
有读者的——`rules.py` 拿它做注解，守卫从源码把它读回来，于是测试里不必留第二份阵营表）；参数改名
`alive_teams`，注解从 `Counter[str]` 收成 `Counter[Team]`（类型检查器从此能拒掉职业版实参）；docstring
改说阵营，并把"为什么不能按职业接"写在它原来待的位置；删掉 `alive_role_counts`。

守卫落在 `tests/test_wiring.py::test_the_win_check_names_its_key_space_as_teams_and_has_no_role_twin`，
五条腿：取的键必须落在 `Team` 里、参数名带 team 而不带 role、注解逐字等于 `Counter[Team]`、docstring
得提阵营、`rules.py` 里不许再出现"用 `role_of` 建 Counter"这个**形状**（不是"那个名字不许多一处"——换
个名重抄一份照样是把入口留着），最后 `check_win` 喂进去的那一手要逐字对得上。阵营表不抄第二份：那条
腿从 `roles.py` 把 `Team` 的取值读回来，所以别名本身也被钉住。

八具变异（`/tmp/mut80.py`），`before` = 整套但摘掉新写的那两条，`after` = 只跑那两条：

| 具 | 改动 | before（整套摘掉新那两条） | after（只跑新那两条） |
|---|---|---|---|
| M1 | 参数名退回 `alive_roles`（连身体一起改，键空间不动） | SURVIVED | CAUGHT，红在参数名那条断言 |
| M2 | 注解退回 `Counter[str]`（名字仍叫 `alive_teams`） | SURVIVED | CAUGHT，红在注解那条 |
| M3 | docstring 退回"哪些职业活着" | SURVIVED | CAUGHT，红在 docstring 那条 |
| M4 | 换个名字（`alive_job_counts`）把按职业建键的孪生复活 | SURVIVED | CAUGHT，红在孪生那条——证明它钉的是形状不是名字 |
| M5 | 屠边分支里混进一个职业键 `seer`（`god` 那条保留） | CAUGHT，红八十五处 | CAUGHT，红在键空间那条，同场后果那条也红 |
| M6 | `check_win` 改喂按职业建的 Counter | CAUGHT，红八十三处（mock 批次里每局都成 `wolf_win`） | 首跑红在**孪生**那条，不是预期的 wiring 那条 → 见下一段 |
| M7 | `roles.Team` 多出第四个取值 `"neutral"`，行为零变化 | SURVIVED | CAUGHT，红在守卫自己的前提行 |
| M8（第二轮补） | `check_win` 就地重算一份 `Counter(state.team_of(...))`，与现状逐字同值 | SURVIVED | CAUGHT，红在 wiring 那条 |

预注册（写在脚本页眉，05:34Z，不事后改）说"before 只允许 M5/M6 红"——七具的结果全兑现了，**M6 的归因
没兑现**：它的 `after` 红在孪生那条，因为 `check_win` 的身体里出现了 `Counter(...role_of...)` 这个形状，
而那条腿排在 wiring 前面先执行。也就是说这两条腿在这个形状上重叠，wiring 腿第一轮没有自己的证人。
第二轮（05:42Z）补 M8 才把它钉住，而 M8 特意挑一具**行为完全相同**的改动：把 `alive_team_counts(state)`
就地展开成它的函数体。老套件看不见"两份算术"，这一族的立论（`#27`/`#28`/`#48`）恰恰是"行为相同也要拆"，
所以一具同值变异比一具改坏行为的变异更配这条腿。M5/M6 在 before 打成一片红是好事不是坏事：它说明
"真把职业键接上"这一种错老套件本来就抓得住，新守卫的增量全在 M1/M2/M3/M4/M7/M8 这六具 before 全绿的
上面——那六件事（名字、注解、docstring、形状、别名、单一定义）在改之前没有任何读者。

两遍的基线与末端对照都是 0 红（05:35:27Z / 05:41:38Z，第二轮 05:42:24Z / 05:44:37Z），`rules.py` 与
`roles.py` 跑完各自 `cmp` 字节还原一致，两半都在一次性 `PYTHONPYCACHEPREFIX` 目录里跑（`#79` 的教训）。
全套 **804 passed**（05:47:06Z，42.76s）。

改文档一处：〈削过主张卡的 prompt〉那一节里 `tests/test_wiring.py` 的活账顶了一次数（原来是五十条、
跑起来七十一个用例，那轮顶成五十二条、七十二个用例）——条数闸门和收集数闸门各红一次，同一句话。那一节里"那六条"指的是 views.md 承诺的
六条单一定义守卫，与本轮新加的这条无关，没动。取证与 15 具变异见〈胜负判据的参数名叫 `alive_roles`…〉；同一轮 AST 扫描出来的另外两处——一处 docstring 认领了
一个不存在的读者、一处"Feeds M6"其实是另一个判据——记在 `#81`/`#82`，本轮没有动。

#### 八个零读者函数里有两个的 docstring 在认领不存在的读者，而性格卡少印一个它自己抽的数：`#81`

`#80` 那一轮扫描印出的名单是九个名字，`rules.alive_role_counts` 归它，剩下八个归这一节：
`agent.was_retried`、`assemble.text_for_debug`、`belief.mean_agreement`、`info.others_speech`、
`persona.bias_for`、`roles.role_ids`、`state.kill_target_last_night`、`transport.last_prompt_text`。
一起删的还有两处不在那张名单上的：`belief.agreement_with_action`（它的读者只有测试里一条为它自己写的用例，
那三条断言全在为它作证，删函数就得连用例一起删）和 `state.LegalSet.allows`——后者的腐烂形状不一样，它是
**判定接受的第二份实现**（真正在线的是 `legality.check_action` 那句 `set(legal.acts) | ({"pass"} if …)`，
而 `default_action` 里的 `may_pass()` 是同一条规则的第四种拼法）。外加 `persona` 的两个字段
`target_bias`、`must_mention_target`，和删完后空掉的 `from statistics import mean`。06:25:23Z 重跑同一支
扫描：全树零读者的函数 **0** 处。

**那两句假话**。`kill_target_last_night` 的 docstring 写着 "Read by the witch prompt only"——女巫看到的
是 `phases.py` 里那条 `Kind.NOTICE`，提示词从来没有点过它的名；`mean_agreement` 的"Feeds M6"更是把 M6
指到了别人身上（`#82` 的读码：`metrics.m6_belief_action` 自己在原地算 `len(set(stated) & set(engine))/k`，
那是"模型说出来的怀疑集合 vs 引擎真值"，不是"信念与行动的吻合度"）。这不是注释 sloppy 那么简单：**一句只
活在散文里的认领，就是一个没有读者的判据**，而它比没有注释更坏，因为下一个人会信它。

为什么不"接上"就算修好（`#82` 摆过的那两个落点）：§8 的对比协议是预注册的，M6 的口径已经写进
[docs/metrics.md](docs/metrics.md) 并跑过 mock 批次——把 `agreement_with_action` 塞进 M6 等于事后改判据；
留作新的 M9 则要它自己先有一份预注册（口径、分母、与 M6 的分别）。所以选了"删掉函数与那句假主张"，而它
自己的模块 docstring 早就写过这次删除该读到的那句话：如果注入的卡片就是 stated belief，"M6 would measure
its own copy and mean nothing"。

**闸门**：`tests/test_wiring.py::test_no_named_helper_in_the_engine_is_left_without_a_reader`。判据取"零读者"
这个形状，不取那份清单——清单要有第二个读者才不作弊，而"引用次数 == 0"不需要。范围两端都钉死：被定义的一侧
只数引擎，读者那一侧数全树（引擎 + 测试 + 脚本）。这一条是本节里唯一当场写错过的：第一版只数了引擎目录，
于是名单里一半名字其实住在测试里，那是我的尺错、不是它们没了读者。限界也写进它自己的 docstring：它**只数名
字**，一个签名与调用都对、却从没被接进产物链的函数它看不见（那是 `#74`/`#75` 那一族管的事）。

**第二格：那张卡**。`PersonaParams` 抽四个数，`render_persona_card` 只印三个——漏掉的是 `verbosity`：
`sample_persona` 为它花了一次 `rng.uniform`，值落进 dataclass 之后再没有任何一处读过。它既不改变发出去的
字节，也不改变任何权重，而类 docstring 写着 "four numbers and a style tag"：那句话对 dataclass 成立、对
产品不成立，因为座位看得到的是卡片，不是字段表。判据不写死"四"，而是从 dataclass 的字段表上取全部 `float`
逐个注入，所以以后再加一个数而忘了印，红的会是那一个的名字。

**为什么印出去而不是删掉**：删字段会挪动 RNG 流，金样本那批钉住的数就得重钉——那是把测试修剪到产物上，而
这一族（`#27`/`#28`/`#48`）的立论恰恰相反。反过来，把 `verbosity` 印进提示词是**改发出去的字节**，而这种
改动只有在端点还起不来的时候是便宜的（现在就是：06:16:44Z 不带 key 打 `/v1/models` 得 401，
`WOLF_LLM_API_KEY` 仍未 export）。于是留下读数、补进卡片，并按 R12 把版本拧进 `config_hash`：
`contract_version` v1.1 → v1.2，`config_hash` 从 `f51d96750a16` 变到 `fb3e99579b7e`（各取十二位十六进制）。
不拧这一格的话，两批 A 区字节不同的档案会被 `compare` 当成同一臂——卡片内容版本进 `config_hash` 就是为这个。

余量现查（06:29:24Z，用引擎自己的 `_est` 与出厂 `TokenBudget`，默认 `PersonaParams()`）：那张卡 55 → 69
个估算 token，增量十四；C1 那一格的预算是 250，还剩 181。绝对值随风格标签长短逐人浮动，增量不浮。

同一处改动顺手接上了一个假断言：`must_mention_target` 以前的唯一活读者是装配器里那个 `or`，它让"你连续两轮
没有实质表态了"这句话在**从没连躲过**的座位上也出现。现在条件只剩 `legal.reason_if_empty == "forced_nominate"`，
而 `tests/test_vote_wave.py` 里那条既有用例补了反向断言（刚投过票的那个人读不到这句）。

**六具变异**（`/tmp/mut81.py`，06:15:28Z 起）。这一格与 `#80` 不同：**被删掉的死代码不会有证人**（按定义
没有读者），所以电池不问"删了之后谁红"，问"新写的三条判据各自抓不抓得住它声称的那件事"。`before` = 整套但
摘掉本轮新写的三个证人，`after` = 只跑那三条。预注册写在脚本页眉：

| 具 | 改动 | before | after（只跑新三条） | 预注册兑现？ |
|---|---|---|---|---|
| N1 | 往引擎里塞一个零读者函数 | SURVIVED | CAUGHT，红在闸门且**点名那个名字** | 是 |
| N2 | 同样的函数，另留一处**同名字符串常量**当读者 | SURVIVED | SURVIVED | 是——**故意披露的限界**：`getattr(obj, "name")` 只能这样接 |
| P1 | 卡片退回只印三个数（缺陷本体） | SURVIVED | CAUGHT，红在卡片用例且点名 `verbosity` | 是 |
| P2 | 印四个数但那一格填的是别人的值 | SURVIVED | CAUGHT，红在同一条用例 | 是——P1 的红不是"只数得出现不出现" |
| F1 | 那句点名提示无条件出现 | SURVIVED | CAUGHT，红的是**新写那半条**（消息带"没被标记的人不该读到它"） | 是——两半在同一函数里，只看红名会把归因写错 |
| W1 | 把闸门的读者范围收窄成只数引擎目录 | SURVIVED | CAUGHT，名单里冒出住在测试里的 helper | 是——"范围两端钉死"是承重墙 |

两遍基线 0 红（06:15:28Z）、末端对照 0 红（06:21:28Z）、四个被改文件跑完各自 `cmp` 字节还原一致，两半都在
一次性 `PYTHONPYCACHEPREFIX` 目录里跑（`#79` 那条：字节相同只证明文件，不证明执行）。

**电池自己的两格修理**（`/tmp/mutlib.py`，这两格比六具结果更值钱）。一是 `verify()` 的键**从来没对上过**：
它拿 `Path(target).name` 去比 `目录/文件.py` 这种形态的备份名，于是永远判"没有漂移"、永远打印 clean——
一个只在失败时才需要兑现的守卫，静默地不兑现。二是备份目录**按轮共用**过：上一轮留下的字节是旧快照，一旦
键修好，`--verify` 就会拿旧快照盖掉这一轮的真实编辑（`tests/test_golden_game.py` 真被这样盖过一次一小时后
的改动，看上去像测试挂了）。现在每轮一个 `tag` 子目录，且**只有本轮要动的文件**才允许被还原，无关漂移照旧
报告、不动手。

改文档：删函数顶动了行号，`README.md` 里七处活行号引用按实测改号，另有三处**带时间戳的历史复述**不顶号、
改成不带号的说法（"那一行"而不是"第几行"）——闸门把历史读数顶成新数是 `#77` 之后立过的规矩。本轮往
`tests/test_wiring.py` 加判据，条数与收集数那两道闸门各红一次（同一句话），活账现值是五十三条定义、
跑起来七十四用例（06:28:37Z 现查）。全套 **805 passed**（06:24:31Z，44.29s）。

#### 一个模块不再依赖的东西，仍然写在它的导入行上：`#83`

`#81` 那条闸门只数 `def`，而它自己删出来的腐烂正好落在它看不见的地方。06:25:43Z 用一支独立脚本
（`/tmp/importscan.py`，同一套 AST 判据、只是把"被定义的东西"换成导入名）量到四处：`batch.py` 的
`asyncio`、`events.py` 的 `asdict`、`info.py` 与 `persona.py` 的 `field`。`scripts/` 是零。

代价不是崩，是**说明书说谎**：导入行就是一个模块对外声明的依赖清单，一个早就不碰 event loop 的
批次模块继续写着 `import asyncio`，下一个人就照那份清单理解它。这跟"docstring 认领一个不存在的读者"
是同一种病，所以判据也照同一套写法来——AST 数真引用，不拿子串搜索冒充。

**先红再绿**：闸门写出来第一跑（06:36:06Z）红的就是那四个名字，与那支独立脚本现测的名单逐条对上。
两份实现互相不是复用关系，这是故意的：判据只有一个来源，但**"有没有人读"这件事**允许被两把尺量——
只有一把尺时，尺子写错了也没人知道（`#81` 那把第一版就错在只数了 `src/`）。

三处豁免，每一处都有变异或披露背书，不是注释：

| 豁免 | 为什么 | 证人 |
|---|---|---|
| `from __future__ import annotations` | 编译期指令，按定义不会在文件里被点一次 | I5 摘掉它 → 红，名单里冒出每个模块的 `annotations` |
| `__init__.py` 整个文件 | 那个位置的导入是**再导出**（对外 API），不是本文件的读者 | I6 只摘掉跳过 → 两半都不红（今天那文件一个导入都没有，**这是声明出来的缺席**）；I7 摘掉跳过**并**插一条真再导出 → 红，点名那条 `Config` |
| 名字只出现在**字符串注解**里 | `from __future__ import annotations` 之下真注解就以字符串生效，当没读会逼人往产品代码里塞没用的东西 | I2 负控制：只用在 `"_BatteryDecimal"` 里 → 两半都不红 |

限界也写进判据自己的 docstring：它只认"整串就是一个标识符"的注解，带运算符的串（`"Config | None"`
这种）它不解析——今天全仓库没有这种写法，所以这一格同样没有证人。函数体内的延迟导入**不**豁免
（I4 塞一个 → 红，点名 `textwrap`），而"只用在属性链根部"也不算没读（I3 负控制）。

七具变异（`/tmp/mut83.py`，基线 06:45:22Z 两半各 0 红；`before` = 整套摘掉这条新判据，`after` = 只跑它）：

| 具 | 改动 | before | after | 预注册兑现？ |
|---|---|---|---|---|
| I1 | 塞一个没读的顶层导入 | CAUGHT——**但红的不是行为读者**，是行号闸门被插行顶偏（同 `#75`/`#77` 那一格） | CAUGHT，点名 `info.py` 那个 `count` | 是，且 `before` 的归因按预注册说明处理 |
| I2 | 只用在字符串注解里 | SURVIVED | SURVIVED | 是（负控制） |
| I3 | 只用在属性链根部 | SURVIVED | SURVIVED | 是（负控制） |
| I4 | 函数体内的延迟导入没人读 | SURVIVED | CAUGHT，点名 `textwrap` | 是——"延迟导入不豁免"有牙 |
| I5 | 摘掉闸门的 `__future__` 豁免 | SURVIVED | CAUGHT，满树的 `annotations` | 是 |
| I6 | 摘掉闸门的 `__init__.py` 跳过 | SURVIVED | SURVIVED | 是——声明的缺席 |
| I7 | 复合：摘掉跳过 **且** 插一条真再导出 | SURVIVED | CAUGHT，点名 `__init__.py` 里那条 `Config` | 是——I6/I7 合起来才说得出"跳过在挡一件事，只是今天没东西可挡" |

I4 那个函数名带 `__` 前缀，是为了不被**同族那条函数闸门**顶红（那是另一件事的读者），不是为了骗过
本闸门——本闸门数的是导入名。I7 要两个文件同时改才成立，所以电池为它加了"多文件一次落、一次还原"
的通道，任何一处锚点不唯一就一个文件都不动。末端对照 06:56:35Z 也是 0 红，四个被改文件 `cmp` 全部
字节还原一致，两半都在一次性 `PYTHONPYCACHEPREFIX` 目录里跑（`#79`）。

改文档：`batch.py` 少了一行，于是 README 里点它的三处活行号各退一格（第 264、461、433 行顶到 263、
460、432），`tests/test_wiring.py` 的活账从五十三/七十四顶到五十四/七十五——条数闸门与收集数闸门
各红一次，同一句话。全套 **806 passed**（06:40:01Z，41.69s）。同一种腐烂在 `tests/` 里还有十五处
（06:48:17Z 现测，`scripts/` 干净）——那一轮的范围只到引擎侧，那十五处它当时看不见；加宽与清扫是
`#84`，写在下一节。

#### 同一把尺量到测试侧，十五处里有一处不是腐烂，是框架在按名字读：`#84`

`#83` 那句"同一种腐烂在 `tests/` 里还有十五处"落地了：判据从 `src/wolfengine` 加宽到 `tests` +
`scripts`。算术没有第二份——两条用例共用同一个 `_unread_imports(roots)`，加宽只是多传两个根，这与
`#71`/`#75` 那一族的立论是同一件事（一个判据一个来源，宁可多一条用例也不复制一份尺）。红的那一步
（07:05:14Z）报出的名单与 06:48:17Z 那把独立扫描器数出来的十五处**逐条对上**，所以"对不上就是尺错"
这一句也顺手兑现了一次。扫描器自己也修了一处同族缺陷：它的扫描根原先写死成 `src`——正是 `#81` 那把
尺第一版犯过的错——现在根从命令行取，07:10:44Z 以 `tests scripts src` 三根重测，名单与闸门一致。

十五处里十四处是普通腐烂，删完 0 红：`tests/test_belief.py` 的 `random`、`EventLog`，以及那一行
`from wolfengine import belief, info, roles, rules, state` 里除 `belief` 之外的四个模块（这一页只问
`belief` 一家）；两页渲染测试各自带着的 `asyncio`、`game`、`MockActor`，加 `test_render_live.py` 的
`json`；`tests/test_transport.py` 的 `pytest`（`asyncio_mode = auto` 之后那一页不再需要它）。

第十五处不是腐烂，是**这把尺的盲点**：`tests/test_batch_live.py` 从 `test_live_path` 再导出的 `key`
是一个 pytest 夹具，它的读者按**名字**在模块命名空间里找它，AST 里永远不会出现那一次点名。它后面挂着
一句 `# noqa: F401  \`key\` 是夹具`——那句散文从来没替任何人挡过东西（这把尺不认 `noqa`，T3 就是钉
这一点的），它只是把一件事写给人看而没有代码替它作证，正是这一族一贯的毛病。

两条路里选了搬，不是豁免：夹具搬进新的 `tests/conftest.py`（框架自己给跨模块夹具留的地址），于是那行
导入里再没有死名字，而闸门**一行豁免都不必开**。豁免是要有证人的洞，搬走是把洞填上。代价写在判据自己
的 docstring 里，不只写在这里：将来谁再把夹具按名字再导出，这条会（正确地）红，而修法还是同一句——
搬进 conftest。

T4 是这一格变成证据而不是主张的地方：把 `conftest.py` 里那个夹具改名，二十七条按名字要它的用例当场
报错（AST 现查：`test_live_path.py` 二十三、`test_batch_live.py` 四），而只跑新判据的那一半**照绿**
——分工本来如此，那条判据管"这个文件点没点它的名"，注入归框架。**预注册没兑现的那半也记在这**：
脚本页眉里我写的是二十八，那是改动前用 `grep` 数 `key` 数出来的，多算了一处 `key=lambda` 与两处散文；
改动前的计数不等于读数，按 AST 现查的二十七记（`#73` 之后这条限界一直是这一族的公共账单）。

第二个盲点是**我的改法自己造出来的**，也被同一把尺抓到：搬走夹具之后，`tests/test_live_path.py` 的
`import pytest` 失去了它唯一的读者，独立扫描器从 0 变 1。如果这一格要靠人对表，它就会带着一个假声明
留在树上。

四具变异（`/tmp/mut84.py`，基线 07:55:25Z 两半各 0 红；`before` = 整套摘掉新写的那条判据，
`after` = 只跑它）：

| 具 | 改动 | 结果 | 预注册兑现？ |
|---|---|---|---|
| T1 | 往 `tests/test_belief.py` 塞一个没读的顶层导入 | before SURVIVED → after CAUGHT，点名 `textwrap` | 是——**加宽是承重的**：老套件里没有任何读者数过测试侧的导入名 |
| T2 | 往 `scripts/calibrate.py` 塞一个没读的顶层导入 | before SURVIVED → after CAUGHT，点名 `base64` | 是——`scripts` 那个根不是装饰。限界随之写清：今天那一侧本来就是 0，所以这一具是它唯一的证人 |
| T3 | 塞一条带 `# noqa: F401` 的没读导入 | before SURVIVED → after CAUGHT，点名 `pytest` | 是——闸门没开豁免，那句夹具注释当初挡不住任何东西 |
| T4 | 把 `conftest.py` 里的夹具改名 | 行为半 CAUGHT（红二十七条具名用例）/ 新判据半 SURVIVED | 方向兑现；数字 28→27 没兑现，见上面那段 |

末端对照 07:56:57Z 也是 0 红，四个被改文件 `cmp` 全部字节还原一致，两半都在一次性
`PYTHONPYCACHEPREFIX` 目录里跑（`#79`）。

跑这具电池的时候还抓出**我自己工具里的两处缺陷**，都记下来因为它们都会伪装成结论：
① 汇总用的是 `-rf`，而"夹具找不到"是 **ERROR** 不是 FAILED，于是 T4 第一跑报成"rc 非零、名单为空"
——一具抓不住名字的 CAUGHT 按这一族的规矩不算证人（`#77` 那条教训的又一个形状），改成 `-rfE` 之后
二十七条名字全在；② 从节点 id 上剥前缀的那条正则落盘时反斜杠多了一层（`r"\\S*"` 要匹配的是一个真
反斜杠），所以它的 `red` 名单一直报成字面量 `"FAILED"`。**这一条要回头核对已发表的账**：`#83` 那节的
点名全部来自断言文本那几行（"点名 `count` / `textwrap` / `__init__.py` 里那条 `Config`"），不来自那个
坏字段，所以没有一个已发表的主张改变；但字段本身坏着跑过了两轮，只有把消息文本一起打印才看得见。

限界（与 `#83` 相同的那几条不在这里重复）：闸门看不见任何框架级注入，这是选择不是疏忽，修法写在
docstring 里；`scripts/` 那一侧今天本来就是干净的，所以那根新加的路径只有 T2 一具**植进去的**证人，
没有自然证人——与 `#83` 的 I6 是同一形状的限界：根在挡一件事，此刻没有东西可挡。

改文档：`tests/test_render_live.py` 少了三行，README 里点它的第 129、142、215、219 行顶到第 126、
139、212、216 行——**只有带文件名前缀的那一处被闸门抓到了**，另外三个是散文里的裸数字，闸门看不见，
它们跟着改是因为改的那一处把它们钉在同一个句子里。`tests/test_wiring.py` 的活账从五十四/七十五顶到
五十五/七十六，条数闸门与收集数闸门各红一次。全套 **807 passed**（08:04:54Z，42.79s）。

#### 一个从不构造的类，被第三方的同名类养着：`#85`

`#81` 那把零读者的尺只数 `def`，所以**模块级 `class` 整个不在扫面上**。补上这一档之后它立刻红了，
名单两项（扫描面是引擎的六十一个模块级类），而两具的死法不一样——第二种的形状才是这一轮的收获。

- `transport.MockTransport`：全树没有一处构造它，可它并不"零读者"。它的读者是
  `httpx.MockTransport(handler)`（三处测试在用，那是 httpx 自己的类）。`_py_refs` 按**名字**全局
  匹配，而且在 `ast.Attribute` 上记账，于是**第三方的同名成员替一具死具付了账**。它的 docstring 还
  写着 "the fixture source for `tests/test_golden_game.py`"——那局金样本是手写的，没有一处代码向它
  喂过剧本。
- `actors.HumanActor`：连同名属性都没有，纯粹是 `#81` 看不见类。它是 plan §15 留的上桌契约，而
  "一期只留契约"这件事只活在 docstring 和 `docs/views.md` 里。

第一版尺子冒出十五个候选。逐个看属性读数落在谁身上之后只剩一个真的：另外十四处走的是
`batch.run_batch()`、`render_live.frame_text()` 这种**模块对象**上的调用（`cli.py` 和各测试文件都这么
用），它们不是腐烂。所以这条判据不放宽窄，只改一处认定：`x.Foo` 要算读者，`x` 必须是本包自己的模块名
——那张表从磁盘上取，不抄名单，仓库里没有叫 `httpx.py` 的文件所以第三方永远进不来。C6 钉的就是这一句：
种一具**只被本包模块对象的属性**读到的类，两半都不许红。

两处修法方向不同，理由是同一个：

- `MockTransport` **删**。离线演示早就不走它（走 `MockActor`，它根本不碰 transport），上游错误分支的
  测试走 `httpx.MockTransport`，因为那能连 `HttpTransport` 的解析一起跑到。留着它不是"以后可能用"，
  是把"这条链有替身"一句假话挂在树上。
- `HumanActor` **给它读者**，不给它豁免：`tests/test_actor_contract.py` 末尾加的两条把它真的构造出来，
  钉住它自己声明的座位号、种类、`blocking`、`timeout_for()` 的返回值，以及 `act()` 里那句
  `NotImplementedError`。C5 证明这条命是测试给的：把闸门的读者面从 `src+tests+scripts` 收成只剩
  `src`，红名单里第一个名字就是它。

顺带清掉三处只活在散文里的假认领：`llm.py` 里说 `MockTransport.fail_every` 能把重试分支"按需打开"
（没有任何东西打得开它，真端点故障时才走那条路），`src/wolfengine/metrics.py` 与
`tests/test_m3_gate.py` 里把"时钟没走过"的一格归给 `MockTransport` 那个从没生效过的默认值——真正的
单处是 `TransportResult` 的 `latency_s` 默认值，两处都改指它。`tests/test_transport.py` 开头那句
"`MockTransport` 也不曾被实例化"原本是一句**披露**，现在它连主语都没了，改成记录这次删除。

这一条**一个豁免都没开**：不跳 `__init__.py`，也不放过 dunder 名——那两个 `__init__.py` 今天一个类都
没有。`#83` 的三处豁免各自在挡一件真事，所以各自配了证人；这里开洞只是把扫面偷偷变小。

六具变异（`/tmp/mut85.py`，基线 09:03:37Z 两半各 0 红；`before` = 整套摘掉这条新判据，`after` = 只跑它）：

| 具 | 改动 | before | after | 预注册兑现？ |
|---|---|---|---|---|
| C1 | 种一具只被外来同名属性读的类 | SURVIVED | CAUGHT，点名 `_BatteryDead` | 是 |
| C2 | C1 且摘掉"根名须是本包模块"这一档 | SURVIVED | SURVIVED | 是（负控制：那一档就是 C1 的牙） |
| C3 | 种一具只被自己那条注解读到的类 | SURVIVED | CAUGHT，点名 `_BatterySelf` | 是——自指不是有人拿它 |
| C4 | C3 且摘掉"体内自指读数要减掉"这一档 | SURVIVED | SURVIVED | 是（负控制） |
| C5 | 读者面收成只剩 `src` | SURVIVED | CAUGHT，点名 `HumanActor` | 是——它的命是测试给的 |
| C6 | 种一具只被本包模块对象读到的类 | SURVIVED | SURVIVED | 是——最重要的负控制，加严的尺没把十四处合法读数抓成腐烂 |

末端对照 09:10:03Z 也是 0 红，三个被改文件 `cmp` 全部字节还原一致，两半都在一次性
`PYTHONPYCACHEPREFIX` 目录里跑（`#79`）。`reds()` 这次住进了 `/tmp/mutlib.py`：`#84` 记下的那两处解析
缺陷（`-rf` 看不见 ERROR、剥节点 id 的正则多一层反斜杠）不该在每具电池里复发一遍。

限界：这条仍按名字匹配，方法名与活的兄弟同名时它不区分（`chat` 就是），它靠"类不可达则成员一起
不可达"这一层往上兜；定义面只数引擎，测试与脚本里的类它不看——那两侧今天由 `#83`/`#84` 那把导入尺
管着。签名与调用都对、却没接进产物链的，还是 #74/#75 那一族。

改文档：`src/wolfengine/metrics.py` 多了一行，README 点它的那处行号从 955 顶到 956（行号闸门抓到的，
它同时报"它在第 956 行（差 +1 行）"）；`tests/test_wiring.py` 的活账从五十五/七十六顶到五十六/七十七，
条数闸门与收集数闸门各红一次；`tests/test_actor_contract.py` 的文档计数从七顶到九。全套
**810 passed**（09:11:40Z，51.68s）。

#### 签名要求你递、函数体从不看的入参：`#86`

`#81` 数函数的名字、`#83`/`#84` 数导入的名字、`#85` 数类的名字——这条链上**签名本身**没人管过。
补上之后它第一次跑就是红的，五处，五处都在替调用方编一个不存在的约定：

- `compress.plan_fold(budget=...)`：六个调用点老老实实递进 `cfg.tokens`，而决定折叠多少的是 `b2_cap`
  和 `est`。它的 docstring 通篇在讲"预算杠杆"——改 `TokenBudget` 改不动折叠深度，那条路径上没有任何
  东西读它。所以那句散文一起改了：现在写明"搬得动那条前缀的是 `b2_cap`，是用 `est` 量出来的那个数"。
- `agent._write(legal=...)`：写日志的函数收下了合法动作集，又没往日志里写过一个字。
- `cli._llm_actors(cfg, seed, transport)`：`make_actors` 用 seed 抽人格，这个孪生签名不用，于是"活
  Actor 也按 seed 变化"看着成立，实则换 seed 换不出任何差别。收 seed 的是发牌和 mock 座位。
- `rules.resolve_tie(state, first, second)`：house rule 是"复投再平就没人出局"，只看 `second` 就够，
  `first` 是留着的。
- `persona._top_accuser(b, state, seat)`：最吵的指控者是信念状态的性质，与 `GameState` 无关。

五处一律**删参数**而不是"想办法读一下"：读一次就得把它写进日志或产物，那是在给一个没被要求的字段
找读者——`#85` 那条"接上 vs 删掉"的取舍，在这里的答案是删，因为没有一个下游指标需要它们。删完连
调用点一起收：装配器一处、日流程一处、`tests/test_prefix_stability.py` 五处、`tests/test_rules.py`
两条 PK 用例（顺手少了 2 行）、`tests/test_live_path.py` 的 `_audit` 两处。

**豁免从磁盘上取，两条，说的是同一件事：这个签名不是写函数的人选的。** ① 引擎里**只签名**的函数
（Protocol 桩、`raise` 桩、`...`/`pass` 桩）声明的 `(函数名, 参数名)` 对；② 某个父类收过的参数。
干净树上的账（10:16Z 数）：`stub_pairs` 十个名字对放过 **18** 处——引擎侧十处全在这里（`Actor` 的
`timeout_for`/`act`、`MockActor.timeout_for`、`HumanActor` 那两处，加 `LLMTransport.chat` 的五个参数），
测试侧八处（契约桩的鸭子实现 `_Seat` 两处、`Scripted.timeout_for`，和 `boom`×3/`slow`/`dead` 那五个
"整个 body 只有一个 `raise`"的回调）。**继承那一档全仓库只挡着一处**：`Chorus._line(act, target)`
覆盖了 `MockActor._line`，改行为不改签名。测试侧未开框架豁免时 63 处，框架那一档吃掉 54（`test_`
前缀 40 + 嵌在别的函数里 14），加上面那 9 处正好归零；`scripts/` 零处，它与 `tests/` 同一条尺。
`_` 前缀**没有**豁免：全引擎今天零个下划线参数，开了只是把扫面变小（`#85` 的教训）。

十四具变异（`/tmp/mut86.py`，两半：`before` = 整套摘掉这两条新判据，`after` = 只跑这两条），
但**第一轮只交出一半的账**，因为种具本身是腐烂：

| 具 | 改动 | before | after | 预注册兑现？ |
|---|---|---|---|---|
| P1 | 种一具**有读者**、参数 `value` 从不读的函数 | SURVIVED | CAUGHT，点名 `_battery_unread(value)` | 是 |
| P2 | 参数只活在那具自己的 docstring 里 | SURVIVED | CAUGHT，点名 `_battery_docstring_only(needle)` | 是 |
| P3 | = P2 且"字符串里的词也算读者" | SURVIVED | SURVIVED | 是（负控制：那一档就是 P2 的牙） |
| P4 | 种一具**只签名**的模块级函数 | SURVIVED | SURVIVED | 是——声明出来的限界：桩按名字豁免自己 |
| P5 | 参数只在 f-string 里被读 | SURVIVED | SURVIVED | 是——尺不窄到把真读者判成谎言 |
| P6 | 参数只在嵌套函数里被读（闭包） | SURVIVED | SURVIVED | 是（同上） |
| P7 | 参数叫 `_unused` | SURVIVED | CAUGHT，点名 `_battery_underscore(_unused)` | 是——"`_` 没开洞"唯一的证人 |
| P8 | 测试侧种一具鸭子类型实现 `Actor` 的类 | SURVIVED | SURVIVED | 是（契约那一档放行） |
| P9 | = P8 且摘掉契约桩那一档 | SURVIVED | CAUGHT 两条 | 是（负控制：那一档是 P8 的牙） |
| P10 | 测试侧种一具模块级、非 `test_`、非嵌套的 helper | SURVIVED | CAUGHT，点名 `_battery_test_helper(unread)` | 是 |
| P11 | 只摘契约桩那一档（不种东西） | SURVIVED | CAUGHT 两条：引擎十处桩 + 测试侧八处全回来 | 是 |
| P12 | 只摘"父类收了这个参数"那一档 | SURVIVED | CAUGHT 一条，且**只剩一处** `Chorus._line(act)` | 是——那一档全仓库只挡一具 |
| P13 | 只摘框架豁免 | SURVIVED | CAUGHT 一条，夹具参数一大片 | 是 |
| P14 | 解析面收成只剩 `src` | SURVIVED | CAUGHT 一条，红在"tests/ 里一处都没了就该加宽范围"那句 | 是——测试侧的扫面是活的 |

第一轮（基线 09:46:23Z 两半各 0 红，末端对照 09:59:38Z 0 红）里 P1–P7 的 `before` 半**全是红的**，
红的却不是新判据，是 `#81`：我种进 `compress.py` 的是"零读者的具名函数"，那正好是 `#81` 的猎物。
于是那几具只证到**充分性**（新判据能点名它），没证到**必要性**（摘掉它这具就活了）。第二轮给每具
补一行 `_BATTERY_READER = <那具>`：它是 `Name`/Load，`#81` 的读者计数因此 ≥1，而它自己不是 `def`，
不在 `#81` 的定义面上——加这一行之后 P1/P2/P7 的 `before` 才真的 SURVIVED（第二轮基线 10:04:43Z
两半各 0 红，末端对照 10:13:46Z 0 红，同一轮顺手重取了 P9 的 `before`：0 红，第一轮那次红是
`test_a_finished_batch_prints_its_line_and_exits_0` 那类起子进程的时序抖动）。

第二处电池缺陷在 P3：那一档第一轮写成 `reads | {str(n.value) for ... if isinstance(n, ast.Constant)}`,
往集合里塞的是**整条字符串**，比较却是 `p in reads`——`needle` 永远不等于那整句 docstring，这一档是
一个 no-op，而它照抄出"after CAUGHT"，看起来像"负控制没兑现"。第二轮改成先把字符串按字母数字切词
再入集合。改完的 P3 在电池里仍报了一次 CAUGHT，而 traceback 落在 `tests/test_wiring.py` 第 751 行——
那是**未改动**文件里断言的位置（改动在它下面三行，会把断言顶到 754）。拎出来单独重跑（只跑 after
半，跑之前先把"改动在不在文件里"打出来：`isalnum` 在、种具在、断言在 754）→ after SURVIVED 0 红；
整具电池再跑一次 P3 → 两半都不红（10:19:18Z，末端对照 10:20:01Z 0 红）。所以那两次 CAUGHT 是电池的
账不是判据的账。**这一条我不给它编机制**：现场只证明了"执行的是没改过的文件"，至于怎么读到的，
没有证人。三个被改文件在三段跑完各自 `cmp` 字节还原一致，两半都在一次性 `PYTHONPYCACHEPREFIX`
目录里跑（`#79`）。

限界三条，写在判据自己的 docstring 里而不是藏在实现里：① 豁免按**名字**配对，所以与契约同名的
普通方法可以借它藏一个真死的参数（`timeout_for` 就是这种名字），而"`raise` 也算只签名"这一档今天
在测试侧被蹭到五具（`boom`/`slow`/`dead`）——它们同时是嵌套回调，即框架那一档也盖着，所以不构成
漏判，但它说明那档豁免认的是"名字 + 只有签名"，不是"真的实现了契约"。② "参数被读了、读到的值
没用"（传进去又原样返回）这条尺看不见。③ 定义面只有引擎；测试与脚本侧由第二条用例的三条豁免
管着，那是范围决定，不是遗漏——`assert raw` 那一句就是钉它的。签名与调用都对、却没接进产物链的，
还是 #74/#75 那一族。

改文档：`tests/test_wiring.py` 的活账从五十六/七十七顶到五十八/七十九，条数闸门与收集数闸门各红
一次；`compress.py` 那处 docstring 少了一句不成立的杠杆指向，`docs/metrics.md` 点 `plan_fold` 的那句
不带 arity 所以没被顶到。全套 **812 passed**（42.88s，10:26:47Z；README 本身是文档闸门的输入，所以
本节落盘之后整套又重跑过一遍，仍是 812 passed）。

#### 数字全对、句子是假的：法官在自己嘴里收回自己：`#87`

`#81`–`#86` 那六条数的都是**名字**——函数、导入、类、参数。补完之后四个计数器同时归零，这条链自查
是空的。所以这一轮换眼睛：拿 mock 把单局和批次跑出来，沿"人看的三个出口"（编年史、直播帧、复盘页）
逐行读产物，专找**票型、token、编号全对但那句话是假的**那种错。两处，都在法官自己印出去的"结论"里。

**① 平票那一行，下一行就把它收回**。`data/` 里留着的那局（`20260920T184536Z_g00000007.jsonl`，
`wolf_win day=4`，`data/` 整目录在 `.gitignore` 里，所以它是本机留存不是版本库内容）第 95、96、102 行，
也就是 seq 94/95/101 的原文：

```
seq 94  vote_result  {"summary": "票型：3号1票、9号1票。弃票1人。平票，无人出局。", "exiled": null}
seq 95  phase        {"text": "平票，3、9号进入PK。"}
seq 101 vote_result  {"summary": "票型：3号2票。弃票1人。3号被投票出局。", "exiled": 3}
```

94 号把"无人出局"当**结论**印出去，95 号立刻收回，101 号再把它推翻。这不只是给人看的那一栏错了：
`summary` 是模型读到的那一句话（`compress.py:72` 把它原样拼进编年史），同一局的第 97 行（3号的 PK 发言，
`phase=day_pk_speech`）的 `request` 字段里，`[e94]` 与 `[e95]` 就一字不差地挨着躺在 prompt 中，那天后面
三次投票（seq 98/99/100）和 3号的遗言（seq 103）带着同一对进上下文。**发现时**全仓库没有一条断言钉过这个
字符串（10:40Z grep `无人出局`，只有 src 命中）。三份留存日志里只有这一局的 `vote_result` 说过这句话，
且只有一处。

根因是**一个谓词两处写**：这个条件原本只写在 `phases.run_vote` 的那一支 `and` 上
（`res.tied and res.pk_seats and house.tie_break == "pk_once_then_nobody"`），而句子
（`phases._tally_text`）根本没读房规，于是分支和文案各说各话。收成一条 `rules.will_pk(state, res)`
（`src/wolfengine/rules.py:293`），两边同问它：分支在 `phases.py:229`，句子经 `_publish` 的
`pending_pk` 旗标（`phases.py:230/276`）走到 `phases.py:301`。**旗标而不是在 `_tally_text` 里重算**：
一句话该不该说"先不定人"取决于**这是今天第几波**，这个事实只有调用方知道——第二波再平是终局
（`pk_once_then_nobody` 里那个 once），那时"平票，无人出局"是**真**结论，必须照说。所以复投那次不传旗标。

**② 平局被写成了一个阵营**。同一套读法在日数上限那桌上撞出第二处（`/tmp/e2e3`，seed 2、把
`max_days` 压到 3 天，引擎确实打出平局）：

```
[e85] 法官：draw阵营获胜（draw_day_limit）。
终局：draw_day_limit · 胜方：draw        ← 同一件事在直播/复盘帧里的第二种说法
```

根因只有一处：`game.play` 收尾写的是 `winner=winner or "draw"`，把"没有阵营获胜"压成了阵营键空间里的
一个字符串，渲染器因此无从分辨平局和狼赢。修法**不是**给渲染器加一张 `"draw" → "平局"` 的表（那是给一个
不该存在的值找翻译），而是让它回到 `null`：写侧 `src/wolfengine/game.py:225`，`compress.py:81` 补
`camp is None` 那一支，`render_live.py:222` 那格改成 `or '无'`。"哪些终局算获胜"这件事仓库里**早就有唯一
来源**——`metrics.DECISIVE`（`src/wolfengine/metrics.py:194`，batch 的分母就是拿它筛的），它 key 在
`terminal` 上，所以 `winner` 里不需要任何占位符。事件 schema 那行注释（`src/wolfengine/events.py:82`）
跟着写成 `str|null`，并且**刻意保持行数不变**：那次注释多了一行，README 里 4 处 `events.py:NNN` 引文
当场被顶成错位（`#77` 那一族抓的正是这种事）。

新用例 `tests/test_judge_wording.py` 四条：① `test_the_ballot_that_opens_a_pk_makes_no_claim_about_who_is_out`
（进 PK 的那张票型句里不许出现"出局"，同一句渲染后也不许，反向对照是第二张票型必须照说"被投票出局"）；
② `test_a_second_tie_is_the_once_in_pk_once_then_nobody_and_says_so`（复投再平：那句是真的，且只有两波、
PK 发言确实是 3、5 号，防止"永远不说无人出局"这种退化修法）；③
`test_a_draw_writes_no_winner_rather_than_a_made_up_camp`（payload 里 `winner is None`，渲染行不点阵营名、
不印 `None`、终局名还在；决定性终局照旧点名阵营 + "获胜"）；④
`test_the_live_frame_shows_no_camp_as_the_winner_of_a_draw`（同一件事的第二块屏幕）。③④ 自己用 mock
重放一局到 `max_days` 上限（`test_day_cap._draw_at`），所以不再依赖 `/tmp`。另把
`tests/test_house_wired.py:60` 从 `"平票" in summary` 加强成整句 `endswith("平票，无人出局。")`——那张桌上
这句话就是**结论**，没有下一行来收回它。

八具手术刀（`/tmp/mut87.py`，八具的预期写在它自己的 docstring 里、先落盘再跑；基线 11:09:17Z
`rc=0 red=[]`，同一套 11:01:45Z 跑过一次，逐字相同）。`mutlib.check()` 只报 CAUGHT/SURVIVED，这一轮的
账要具名断言，所以每具都留红名单：

| 具 | 改动 | 红了谁 |
|---|---|---|
| K1 | `phases.py:301` 的 `elif pending_pk:` → `elif False:` | CAUGHT ①（句子不再读 PK） |
| K2 | 第二波那次 `_publish(t, second)`（`phases.py:239`）也传 `pending_pk=True` | CAUGHT ②（终局平票改口成"先不定人"） |
| K3 | `phases.py:230` 第一波不传旗标 | CAUGHT ① |
| K4 | `rules.will_pk` 恒 `False` | CAUGHT ①② + `test_vote_wave.py` 的 `test_the_pk_revote_gets_its_own_cut_after_the_first_tally_goes_public`（PK 整条链没了） |
| K5 | `will_pk` 少读房规那一支 | CAUGHT `test_a_house_that_says_nobody_on_a_tie_never_opens_a_pk` |
| K6 | `game.py` 的 `winner or "draw"` 装回去 | CAUGHT ③④（压平回来了） |
| K7 | `compress` 的 `camp is None` 那一支关掉 | CAUGHT ③（印成 `None阵营获胜`） |
| K8 | `render_live` 退回 `.get('winner', '')` | CAUGHT ④（印成 `胜方：None`） |

`total=8 survived=0 anchor-broken=0`。八具落在五个源文件上（`phases.py` 三具、`rules.py` 两具，其余
各一具），每次还原后 `cmp` 全部 BYTE-IDENTICAL，两半都在一次性 `PYTHONPYCACHEPREFIX` 里跑（`#79`），
`cmp` 在还原之后做（它只证明树干净，不证明跑的是哪份代码，证据是红名单）。

**K5 这一具要如实写**：它红在 `tests/test_house_wired.py:52`（`asked == [1] * 9`，"平票后又开了一轮复投：
每座被问 [2, 2, …] 次"），而不是红在我这一轮加强的那半句上。单独钉住 K5 取样两次（`/tmp/mut87_k5.py`，
11:09:42Z）：用现在这版整句断言跑，红在 :52；把那半句退回改前的 `"平票" in summary` 再跑，**还是红在 :52**。
所以那处加强不是 K5 的证人，它只是把句子也钉住了——K5 的证人一直是"复投波没开"那一条。写在这里是因为
上一轮（`#86`）刚记下过"种具照抄出 CAUGHT"这种账。

两份留存产物**故意不改**：`data/20260920T184536Z_g00000007.jsonl` 和它的 `.html` 带着旧文案。它们是那一次
运行的记录，改它们等于把证据改成结论的样子；而这一节引用的恰恰是它。**没有任何断言为了这个字符串去读它**
（11:27Z 在全仓 `*.py` 里 grep 那个文件名：只有 `tests/test_judge_wording.py` 的 docstring 命中一处——
散文读者，不是断言）。修好的是以后的日志。

第三处发现**没有修**，因为它的代价不是这一条该付的：`rules.NightResult.peace`（平安夜，`rules.py:146`，
`rules.py:213` 算出来）在产物链上**零读者**，只有 `tests/test_rules.py` 和 `tests/test_night_guards.py` 读它。
于是女巫救人的那一夜什么公开句子都不发——上面那份留存日志的第一夜正是这一种：seq 13 狼刀 3号，seq 15
5号交出解药（`"potion": "save"`，可见域只有 `[5]`），seq 17 验人，seq 18 直接"天亮了，第1天开始。"，中间
既没有 `death` 也没有一句"昨晚平安夜"。那瓶药花掉了，而这在九个座位的公开视图里等于没发生，"昨晚是平安夜"
只活在没人读的布尔值里。补一句公告要**新增事件**→ seq 整体后移 → 金样本重钉（
`tests/test_golden_game.py:309` 的 `_key(events) == GOLDEN_KEYS`、`:454` 的 `"平票，2、3号进入PK" in
_ev(events, 63)`）+ metrics 钉值连带改，而且它改变模型读到的输入分布，那是**改处理**不是修句子。所以另立一条
任务（`#88`），不塞进这一节。

这一轮自己的产物也错了一处，顺手记下：① 的 docstring 最初写的是"第 96、97 行"，那是**行数与 seq 差一**
（真身在第 95、96 行 = seq 94、95）。行号闸门（`#72`/`#77`）只扫 `docs/*.md` + `README.md`，测试 docstring
里的行号没有任何东西在读，所以它错了没人知道——改成引用时把行号与 seq 一起写清，并在这节留下这笔账。
限界另两条：① 的修法钉住的是"这句话跟着房规走"，它不保证文案唯一（改措辞不红，把该报的不下报才红）；
② 的 `null` 只覆盖了平局与三种 `aborted_*` 共用的那一个 `log.append`，`aborted_*` 今天造不出离线局
（把 `max_game_completion_tokens` 压到 1、把墙钟上限压到零，在 mock 下都仍以 `wolf_win` 收尾），
所以那一支只有代码同源这一层保证，没有产物级证人。

全套 **816 passed**（43.18s，11:26:40Z 那一次，读的正是含这一段的 README）。这一节的文本本身是文档
闸门的输入，所以它落盘后整套重跑过：
第一次红两处，都是这一节自己写下的主张——K2 那一格的号点到了 `_publish(t, second)` 的上一行
（行号闸门），而两处把上限写成键值形式的主张被值闸门当成"文档写死的出厂值"核对（src 里是 6 与 20000，
我写的 3 与 1 是那一次跑压出来的临时值，不是出厂值）。按 `#45` 定下的写法改成"把 `max_days` 压到 3 天"
这一类不带等号的说法、并把 K2 那格改成点名那一行真有的名字，改完再跑就是上面这个数；本节最后一次改动
之后又整套跑过一遍，仍是 **816 passed**（44.35s，12:47:44Z）。#86 一节里 10:26:47Z 的 `812 passed` 是带
时间戳的历史读数，不顶。

#### 一句被三个出口共用的假出处：替身桌被拒时，报告指着管真人的那条条款：`#89`

`#87` 的方法（把产物通读一遍，找"计数全对但句子是假的"）在机器侧出口上又撞到一处。这一处不是法官
说的话，是**报告**说的话。两条命令：

```bash
wolf batch --configs A,B --mock --games 3 --seed0 909 --out /tmp/m88batch2 --set B.temperature=0.6
wolf compare /tmp/m88batch2 --axis temperature
```

印出来的是（`/tmp/m88batch2/comparison.md` 第 5 行，改前落盘的那一份，原样）：

    合成桌（含 mock 座位）只验证管线，不产出结论：plan §十五 规定它永不进评测语料。本批 actor_kinds=['mock']。

数字没有一处不对：3 局 × 2 臂、种类就是 mock、`win` 被清空。假的是**出处**。plan §十五 第 358 行的
原话是「**含真人座位的局永远不得进入配对评测语料**」，两个理由都关于人（真人不可 seed 控制、直接污染
McNemar 的配对前提）；第 362 行的落实方式才是要求局文件与 manifest 都记 `actor_kinds`。§十五 全文没有
一处提到 mock——把整份计划 grep 一遍 `mock`，命中的是 §十 的里程碑表和 §十一。mock 那条约束在 §十一
第 287 行：「**必须真端点（mock 只会自证，这几项不许省）**」。所以读者照着印出来的那一格去找条款，只会
找到一条关于**玩家**的规定，然后得出恰恰相反的结论：替身 transport 没人管。

同一句假出处写了三遍，加上文档两遍：`batch.py:509` 那句印 `actor_kinds` 的拒绝语、`metrics.py:400`
的 note、`metrics.py:617` 的 note，以及 `docs/comparison.md` 的拒绝表、`docs/metrics.md` 的分母规则。这与 `#87`
的"一个谓词两处写"同形，所以修法也同类——一张表 `metrics.SYNTHETIC_CLAUSE`（两个键，mock 指 §十一、
human 指 §十五）加一只 `metrics.synthetic_basis`，三个出口都从它取：`batch.py:510` 的
`metrics.synthetic_basis(synth)`、`metrics.py:401` 的 `synthetic_basis(synthetic)`、
`metrics.py:618` 同名的那一处。

同一行里还藏着第二句假话，而且它比出处更贵：note 写死「actor_kinds 含 mock/human」，但批次顶层那一格
只有两种取值（`batch.py:207` 的 `kinds = ["mock" if mock else "llm"]`，由 `--mock` 推出来），它
**写不出**真人座位。于是"含真人的那一批被拒"会印出一句 `actor_kinds=['mock']`——计数仍然全对，句子
自相矛盾。因此种类也不再取自 manifest：`metrics.seat_kinds` 从**触发这次拒绝的那些局的页眉**取。
`actor_kinds` 缺格的日志照样被 `is_synthetic` 关门拒掉，但它不属于表里任何一种，所以它不借最近的一条来
说——那句说明里不出现任何条款号。

六条新用例在 `tests/test_synthetic_attribution.py`，先写失败再动手（第一次跑 5 红 1 绿，而那唯一绿的
一条是**拼写**放过的：它只查 `§十五`，旧 note 写的是 `§15`，补上另一种写法才红成 6 条）。八具手术刀
（`/tmp/mut89.py`，预注册写在脚本自己的 docstring 里）：

| 刀 | 改动 | 红的是谁 |
|---|---|---|
| K1 | 表里 mock 那一格换成真人的条款 | mock、两条、同字、compare 四条，另加 `test_a_batch_of_stand_in_tables_is_not_evaluable_rather_than_a_pass` |
| K2 | human 那一格换成 mock 的条款 | `test_a_human_seat_table_cites_the_clause_that_rules_on_humans`、两条、compare |
| K3 | 两条条款无条件一起印 | mock、human、缺格、同字、compare 五条都红（"不许在场"那半句是证人） |
| K4 | 缺格时借用 mock 那条 | 只有 `test_an_unregistered_table_borrows_no_clause` |
| K5 | compare 的种类退回取 manifest 那一格 | 只有 compare 那一条的真人半边（manifest 全程 `['mock']`） |
| K6 | m1 的种类退回写死"含 mock/human" | mock 与 human 两条（各有一句"另一种不许在场"） |
| K7 | m1 干脆不印种类 | mock、human、两条 |
| K8 | `if synth:` 短路，拒绝整个消失 | 五条：`test_a_synthetic_batch_is_usable_for_plumbing_and_not_for_conclusions`、`test_a_tuple_field_can_be_the_axis`、`test_batch_then_compare_leaves_the_pair_and_a_readable_report`、`test_compare_refuses_an_undeclared_second_difference`、compare 那条 |

baseline（任何刀之前）`rc=0 red=[]`；`total=8 survived=0 anchor-broken=0`，八次还原后 `cmp` 全部
BYTE-IDENTICAL（字节码投毒的防护照 `#79`：每子进程一次性 `PYTHONPYCACHEPREFIX`）。

如实记两笔。① K6 的预注册只点名了 mock 那一侧，实际红两条——human 那条也有一句
`"mock" not in note`，是我把证人写少了，不是测试多红。② K4 只有一条用例读，而那条用例的输入（页眉里
没有 `actor_kinds` 的日志）不是任何产品路径产出的，是手工改出来的：所以 K4 证明的是"这句话不会去借
出处"，不证明有任何真实日志需要它。

限界一条，是这一节的软肋：**计划条款号没有任何闸门在读**。计划不在仓库里，把它接进闸门就是给测试钉
一条本机路径，那是 `#77` 那类假证人。这里唯一承重的是六条用例把 `§十一`/`§十五` 两个字符串钉死了——
将来谁把条款挪去别的小节，红的是这些断言；而"挪过去之后指针对不对"仍然只有人来看。

全套 **822 passed**（48.92s，13:27:32Z 那一次读的正是含这一段的 README；文档闸门单独那一次 21 passed，
13:26:17Z）。这句话之后又改过一次、也再跑了一遍：**822 passed**（50.48s，收尾 13:29:30Z），那一次与上一
次的差别只有这一句里的时刻。上面 `#87` 那节的 816 是带时间戳的历史读数，不顶。13:23:13Z 重跑第二份产物，印出来的是：

    合成桌只验证管线，不产出结论：本批 actor_kinds=['mock']，依据 plan §十一（mock 只会自证，这几项不许省），永不进评测语料。

#### 页眉数的是这一屏，不是这份文件：观众页说"私有事件0条"，同一页页脚说它们在这份文件之外：`#90`

`#87` 读的是三个给人看的出口，`#89` 读的是拒绝语，这一轮回到最后一个出口，读到的是它自己的页眉。
两份页面来自同一份日志（`wolf export /tmp/m89batch/A/20260923T131418Z_g00000909.jsonl -o /tmp/h_a.html`
与同一条命令加 `--god -o /tmp/h_g.html`）：

    第3天结束 · wolf_win · 发言21条 · 私有事件0条 · 闸门拒绝0次 · 视角：观众
    第3天结束 · wolf_win · 发言21条 · 私有事件26条 · 闸门拒绝0次 · 视角：上帝

三格里两页确实不同的那一格，恰恰是唯一一句**关于这局游戏**的主张。磁盘上那份日志的非公开记录就是 26
条（deal 9、night_action 9、wolf_chat 3、seer_result 3、notice 2），所以 26 是文件里的事实，而 0 是
"这一屏被允许看到几条"——后一句被印成了前一句。同一份文档隔两行自己反驳自己：观众页的页脚写着
「观众模式只含公开事件：模型的自报排序与私有频道均不在本文件中」。而这条尺本模块早就立过，
`docs/views.md:133` 那句「读的是**文件里**的事件数，不是这一屏被允许看的事件数」（钉在
`test_a_frame_with_nothing_visible_does_not_claim_the_file_is_empty`）——只不过它当时管的是"这一局没内容"
那一句，没人把它顺手用到页眉的计数上。而**孪生的那一屏早就做对了**：`render_live.py` 里就写着一条注释
『`events`, not `evs`: these sentences are about what the file holds, not about what this view is allowed
to show』，两个渲染器一条规则，这次是 HTML 这一半掉队——它掉的还是那条规则在页面这一侧唯一没被任何
用例覆盖的一格：直播那三句各有用例，页眉这三个计数一条都没有，所以第一次跑才有三条同时红。

第二处同源，而且更贵：`_counts` 的 `refused` 读的也是被过滤后的列表，而 `metrics.py:189` 的
`DECISION_KINDS` 里含 `wolf_chat` 与 `night_action`——`wolf audit` 的 `refused_turns` 把私有频道上的被拒
算进去，观众页把它擦掉。一局狼聊被闸门打回的日志，观众页会说「闸门拒绝0次」，而这句在这项产品里读起来
是"模型一次都没被赶回去重说"：闸门自己的战绩不见了。

修法一行：`render_html.py:329` 的 `counts = _counts(events)`（送进去的是 `evs`，也就是过滤后的那份）。
**没有**顺手给页眉补一句"（本文件不显示）"——"这一页给你看了哪些"已经在页脚有一个来源，一个谓词一个
来源；页眉只回答"有多少"，两页于是同一个数。

四条用例在 `tests/test_render_html.py`，先写失败再动手。第一次跑三红，红在三句不同的话上：
①`test_the_audience_page_counts_the_private_events_the_file_holds` 红在观众页印 0，
②`test_the_two_pages_print_one_number_for_each_of_the_three_counts` 红在
`one file, two numbers: …私有事件0条… vs …私有事件22条…`（夹具的那一份有 22 条非公开记录），
③`test_a_refusal_in_a_private_channel_is_counted_by_both_pages_but_shown_by_one` 红在
`assert '闸门拒绝2次' in …`。第四条 ④ 是**注册下面那把 K7 之后**才补的，见"如实记"第②笔。

| 刀 | 改动 | 红的是谁 |
|---|---|---|
| K1 | 把 shipped 的 bug 放回去：`_counts(evs)` | ①②③ |
| K2 | 半个修法：只有观众页仍读过滤后的（`evs if not god else events`） | ①②③（②买的就是这一具：它钉的是"两页同一个数"，不是"观众页也得写 22"） |
| K3 | 谓词反了：`visibility != "all"` 改成 `== "all"` | 只有 ①（②只比两页是否相等，两边一起错它不响——这是②的限界，写在它自己的 docstring 里） |
| K4 | `refused` 从"被拒几次"改成"有几轮被拒" | 只有 ③ |
| K5 | 页眉删掉 私有事件 那一格 | ①②（③不响：它没断言那一格，注册时就这么写） |
| K6 | `shown_events` 不再过滤 | ③的**正文**半边 + 老的观众泄漏金丝雀（页眉此时已经读文件了，所以红只能来自"观众页多显示了私有频道那一行"） |
| K7 | 删掉页脚那句"观众模式只含公开事件…" | 只有 ④ |

baseline（任何刀之前）`rc=0 red=[]`；`/tmp/mut90.py` 第二跑 `total=7 survived=0 anchor-broken=0`，
七次还原后 `cmp` 全部 BYTE-IDENTICAL（字节码投毒的防护照 `#79`：每子进程一次性 `PYTHONPYCACHEPREFIX`）。

如实记两笔。① **第一跑 K4 活了**（七具里活了一具）。我给它预注册的期望是"私有那次被并成 1 轮所以红"，
而这句算术没核对过：夹具里那条公开发言本来就带 1 次被拒，我又注入了 1 次，于是"被拒 2 次"与"2 轮被拒"
是同一个数 2，两个读数恰好相等，刀落在水上。红的是我的输入，不是产品代码——把注入改成**同一轮两次**
被拒，"次"与"轮"才分得开，K4 第二跑红在 ③。注册表 `/tmp/mut90.py` 的 docstring 里两跑都在，第一跑那行
没有改写。② ④ 写出来的那一刻就是绿的（页脚那句本来就写对了），它没有"红过"这段经历，唯一证明它不是
装饰的是 K7。而 K7 是**先注册了这具刀、再回去查那句话有没有读者**才发现它零读者：全仓库只有页眉那三个
计数在读数字，"这一屏显示了哪些"这一句没有任何测试碰过。修完重导的两份页眉都在盘上（`/tmp/h_a.html`
是改前那一份，`/tmp/h2_a.html` 与 `/tmp/h2_g.html` 是改后的两页），改后的两页印的是：

    第3天结束 · wolf_win · 发言21条 · 私有事件26条 · 闸门拒绝0次 · 视角：观众
    第3天结束 · wolf_win · 发言21条 · 私有事件26条 · 闸门拒绝0次 · 视角：上帝

限界两条。③ 的私有那一半是造出来的输入：现存产物里没有任何一份在私有频道上带被拒记录（那两份真日志
`attempts` 全为 0，authored 夹具 77 条事件里只有 1 条带 1 次被拒、且在公开发言上），它钉的是"引擎走到
那条路上时句子不许撒谎"（狼聊与发言走同一只 `ask()`，被拒记录由 `agent.py` 写进事件），不是一份已经
印错过的产物。④ 页眉的"次"与 `audit` 的 `refused_turns` 是两个分母（被拒输出的条数 vs 有被拒的轮数），
没有任何闸门把它们对上，这一对只有人来读——本节能自动的都自动了，不能的那条写在这里。

数一遍：**826 passed**。最近的一次全绿是 13:52:53Z 起、13:53:45Z 收尾那一跑（51.21s，读的是还没有
这一节的 README）；这一节写进去之后又跑了一遍，还是 826 passed，中间源码一行没动——所以这一句不去引用
它自己写进的那一次的时刻，只引用它确实读过的那一次。文档侧跟着改了一格：`docs/views.md` 里"渲染层 ……
条用例（两个模块各几条）"那一格的两个数从 23 与 52 变成 27 与 56，因为这一节给那个模块加了四条，而
计数闸门（`#44`、`#61`）当场把没改的那一版顶红了两次。上面 `#89` 那节的 822 是带时间戳的历史读数，不顶。

## 这个仓库现在能做什么、不能做什么



- ✅ 一整局自玩（mock）、完整判定链（夜间能力、用药、验人、放逐、遗言、猎人开枪、胜负、
  日数上限的预注册平局）、离线复盘与直播、八组指标。
- ✅ 胜负判据只认阵营键空间：参数名、注解、docstring、"不许存在按职业建键的孪生"、`check_win`
  喂的是哪一手，五件事一起钉在一条守卫上（`#80`，八具变异见上一节）。
- ✅ 引擎里挂着"没有读者的具名函数"这件事有闸门了：AST 数真引用，定义侧只数引擎、读者侧数全树，
  判据是"引用次数为零"这个形状而不是那份清单（`#81`，六具变异，其中 N2 是声明出来的限界）。同轮删掉
  十处函数（八处全树零读者、一处只有一条为它自己写的用例、一处是判定接受的第二份实现）与两个字段，
  其中两处 docstring 在认领不存在的读者（女巫提示、"Feeds M6"）。
- ✅ 死**类**也进了这把尺的扫面，而且它不再让第三方的同名成员替一具从不构造的类付账（`#85`：
  `httpx.MockTransport` 就这样养着 `transport.MockTransport`）。删掉的那具连带清掉三处只活在散文里的
  假认领；留下的那具（plan §15 的上桌契约）是靠给它真读者活下来的，不是靠豁免。六具变异里三具是
  负控制，专门证明这条加严的认定没把 `batch.run_batch()` 那一族走模块对象的合法读数抓成腐烂
  （账见 `#85` 那一节）。
- ✅ **签名本身**也进了那把"要有读者"的尺：声明了却从不读的入参，第一次跑就逮到五处，五处一律删参数
  而不是给字段找读者（`#86`：`plan_fold` 的 `budget` 让"改 token 常数能加深折叠"看着成立，`_llm_actors`
  的 `seed` 让"活 Actor 随 seed 变"看着成立）。豁免只有两条且都从磁盘上取——只签名函数声明的
  `(函数名, 参数名)` 对、父类收过的参数（后者全仓库只挡一具，所以它的证人是断言不是名单）。十四具
  变异里五具是负控制；第一轮有七具的 `before` 被 `#81` 顶红、只证到充分性没证到必要性，第二轮给种具
  补了读者才把必要性读出来（账见 `#86` 那一节，含一处没编出机制的电池缺陷）。
- ✅ 法官嘴上那句"结论"与它下一步真的做过的事现在是同一个谓词说了算：平票要进 PK 的那一张票型不再
  先宣布"无人出局"、下一行收回（那句话是原样进 prompt 的，`#87` 用留存日志的 `request` 字段证到了），
  而复投再平那句"平票，无人出局"作为**终局结论**照说；平局也不再被压成阵营键空间里的 `"draw"`，
  `winner` 写 `null`、渲染器看见 `null` 就不说"获胜"，"哪些终局算获胜"仍只有 `metrics.DECISIVE` 一个来源。
  四条新用例 + 八具手术刀全部具名兑现（其中 K5 如实记着：它的证人不是本轮加强的那半句断言）。
- ✅ 报告替一张替身桌说话时，指的那条计划条款真是管这种桌的那条：mock 走 plan §十一（"必须真端点，
  mock 只会自证"），含真人走 §十五；三个出口共用一张两键的表，种类也从**被拒的那些局的页眉**取而不是
  从批次 manifest 取（后者写不出真人）。缺 `actor_kinds` 的日志照样拒，但它不借最近的条款来解释自己
  （`#89`：六条新用例、八具手术刀，含一处"我先把证人写少了"的账与一条没有闸门的限界）。
- ✅ 座位上读得到自己的四个性格数：`verbosity` 从"抽出来没人看"变成印进 C1 卡片，并按 R12 把
  `contract_version` 拧到 v1.2 让 `config_hash` 跟着变（不拧就会把两批不同字节当成同一臂）。
- ✅ 引擎与测试/脚本两侧"声明的依赖"都与真的依赖对上了：未用导入有一条 AST 闸门在读，两条用例共用
  同一把尺（`#83` 引擎侧四处、`#84` 测试侧十五处，共十一具变异）。豁免只有 `#83` 那三条，而测试侧
  唯一的框架级读法（pytest 按名字注入夹具）是靠**把夹具搬进 `tests/conftest.py`** 消掉的，不是靠
  开洞：那条尺不认 `# noqa`，也不给再导出的夹具留位置（账见 `#84` 那一节）。
- ✅ 端点没标定完也能演示：`run --mock` → `replay` → `export` → `watch --once` 全程不发请求。
- ✅ 一局被砍在半路也读得出来：离线读侧容忍末尾一行撕裂（只宽一行，两行就是损坏），三个出口用同一句
  话报告它，且只报行数与字节数、不印原文。拿错文件（把 `--dry-run` 的提示词转储当日志）说的也是同一
  条句子，带文件名、行号、缺哪些键，到终端是一行报告加退出码 2 而不是 traceback；路径本身用不了
  （不存在、是目录、写不下去）也是同一处接住的一句话。**连开局记录都没有**的文件过去在这三个出口是
  沉默的（`replay` 一个字都不印、退出码还是 0），现在由同一只手补一句"这个文件没有开局记录"；编号
  破损（缺号 / 重号 / 顺序倒挂）的文件说的是第四句，而**两局拼在一起**的文件不再被允许顶着一份
  写错局号的页眉出门——读取侧现在和写入侧一样拒收两条开局记录。判据与
  68 具变异见〈日志读不下去的时候：砍断的末行与拿错的文件〉（九批：14 具撕裂末行、9 具拿错文件、
  6 具页眉的 manifest、5 具"按名字跳过转储"、4 具 CLI 那句报告、5 具路径用不了、5 具空文件的沉默、
  5 具"只有开局记录"的沉默、15 具编号破损与两局拼接）；批次侧另有一节，读数走的是同一只手写出的
  同一份算术，8 具变异见〈编号破损走进了批次目录〉；同一份文件被砍掉末行时，批次以前只看得见
  "中断或未完"，现在那句改口说得出是哪个文件、第几个字节起读不下去，9 具变异见〈同一份被砍的日志，
  批次报告以前说"这局没打完"〉。直播页眉和离线读侧现在共用同一个 manifest 读者，
  而 `read_dir` 与 `read_arm` 那两份按名字跳过的判据各自有一条断言在读。
- ✅ 体检工具的 `main()` 也被离线跑过一遍了（预检 → 六段探针 → 落盘，含真 SSE）——但**这次排练
  不产出任何常数**：桩没有延迟，任何数从它身上读走都是假的。它兑现的是"第一通真电话不必用来调试
  工具"，见上一节。
- ✅ 端点拒答伪装不成模型行为：非 200 里只有"答案取决于你什么时候问"的那两类（408/429）和一条
  压缩令（400 且提到 length）按轮处理，其余（401、404、任何没见过的 4xx）连一局都不留——
  `aborted_endpoint`。判据与 7 具变异见〈端点说不的时候，谁替九个座位答的话〉一节。
- ✅ 退出码来自判据，不来自收尾崩溃：一局打完的 `run` 与 `batch` 现在请求和关 client 共用同一个
  event loop，异常路径仍在 `finally` 里关。这一格以前只有真端点能暴露，现在有了离线证人——
  `tests/test_loopback_endpoint.py` 起 127.0.0.1 上的桩、在子进程里跑真 `cli.main`，钉的是
  "stderr 空、退出码是判据给的那个、`批次 ->` 那一行印得出来"。6 具变异见〈一局打完了、退出码却是
  1：收尾时在第二个 event loop 里关 client〉。全仓库真开 socket 的测试文件只有它和
  `tests/test_calibrate_rehearsal.py`，两者都不碰那台私有端点。
- ✅ 什么都不产的命令不再冒充结论：`--games` 少于 1 局（0 与负数都算）现在由 `wolf run` 与
  `wolf batch` 各自在**建目录、写 manifest 之前**拒掉，一行 `配置错误：…` 加退出码 2；`--dry-run`
  的普查自己说出"这本身就是失败"的那一刻，退出码跟着从 0 变 1。修前实测（05:51:09Z）：
  `batch --games 0` 先把批次目录落盘、再在摘要行上读一份空配对表崩掉，`run --games 0` 则静默退 0。
  判据与 7 具变异见〈零局的批次先落了盘，再在摘要行上读一个空列表〉，其中 V7 那一格（内联一份逐字
  相同的判断，两套用例都不红）是声明出来的缺席，不是遗漏。同一只手上另外五道 rc 2 的判据（重名臂、
  配置里没有的字段名、类型不符的 `--set` 值、越界的 `--seat`、要一块代码里不存在的板子）与它一起被
  探针核对过"不落盘"，见同一节的 06:12:31Z 与下面那一格。
- ✅ 类型过了、范围也得过才算参数对：`--seat` 不在 1..9 或不在这张桌子的名册上、`--set` 要一块这份
  代码里没有的板子，都在读文件、建目录之前一句 `配置错误：…` 加退出码 2。修前实测（07:53:44Z，
  `/tmp/probe59_prefix.py` 临时拆掉两处读者再逐字节还原，输出 `/tmp/probe59_prefix.out`）：`--seat 10`
  印出 1725 字节一份看着正常的观众视图并退 0，`--set A.seat_count=5` 把批次目录留在盘上然后抛 41 行
  stderr（12 帧）。判据与 10 具变异见〈类型过了、范围没过：越界的 --seat 印出一份观众视图，要一块不存在
  的板子留下一个空目录〉，其中 W10 是声明出来的缺席（这份代码只有一块板子，写死的 1-9 与查名册的行为
  永远分不开）。
- ✅ 每局的墙钟乘数是**量出来的**，不是 plan §187 那句估算：`--dry-run` 末尾印每局调用次数、
  prompt token、完成预算（20 局替身桌：53.8 次/局、17.1 次/天），发请求的人和普查的人读同一个
  计价函数；一局真跑完之后 `m7_cost` 还报**兑现率**（Σ生成 ÷ Σ问价，随覆盖面一起出），上限于是
  有配套的折扣而不是等人去算。还未知的那半只有 `docs/calibration.md` 的两格吞吐常数。
- ✅ 文档里"哪个测试文件有几条用例""这一句点的是第几行"都是可核对的主张，不再只靠人重数：
  `tests/test_doc_citations.py` 四套扫描 + 出厂值扫描 + 收集数扫描（用例名、CLI 参数、条数、行号、
  代码里的字面量、pytest 收集数），共 48 具变异（〈测试〉末尾那张 D 表 34 具 + 行号这一族 L1–L6 六具
  + `#61` 那一族 C5–C13 八具）。08:28:40Z 那一轮把 **C10 报成等价体**，08:48:51Z 换掉先测办法之后它
  有了读者——等价与否不是性质，是"此刻有没有断言在读"的读数，那笔账记在〈条数和收集数〉一节）。
  补计数那道闸门的当场就抓到两处自己写错的数
  （`# 14 条` 对 15 条，和一个早就漂走的 `test_live_path.py` 行号——后者直接删掉：那时名字有闸门、
  行号没有，`#59` 之后行号也有了）。取舍与边界写在那两张表下面。
- ✅ 那道行号闸门自己曾经比它写的句子还松，现在收成了**被点名的那一行**：`file.py:NNN` 若点的是空行
  或隔壁那行不相干的代码，以前靠 ±2 行窗口和整段标识符照样绿。13:38Z 实测语料里就有两处把同一个
  `except LogDamage` 指到两个不同的错号上；三档候选判据（整行 / ±1 / ±2）先按假红代价量过再选，
  收口后暴露的 5 处引用改了号（其中两处原本指着空行）。判据自己由 7 具变异钉（`/tmp/mut72.py`，
  14:01:26Z 7/7 按预期，E2 先活下来、补了 `probe(n - 1)` 那条断言才回去），账见
  〈那道行号闸门收得比它自己写的还松：`#72`〉。
- ✅ 体检脚本不再自己抄一份 usage 读法：`prompt_tokens_details` 是字符串、是列表或整块 `usage` 为
  null 时，以前 `.get` 抛异常、被 `complete()` 的兜底 except 吞成 `ok: False`，于是"端点回了一个
  我们没解析的形状"在报告里写成"端点不健康"（还要按默认重试次数再来一遍）。现在三格计数都问
  `transport.usage_from()` 要，与日志侧共用同一个判据；6 具变异里 M2（正确地内联、行为全对）
  是那条字面量守卫唯一的证人。账见〈体检脚本自己抄了一份 usage 读法，于是一个 200 被说成端点故障：`#71`〉。
- ✅ `phases.py` 里那串"这里曾经错过"的散文不再只有散文作证：12 处改动各配一具变异（`/tmp/mut73.py`），
  先焦点集后全量各复核一遍（14:29:09Z、14:31:39Z 基线 0 红），九个幸存者补成四个新测试文件
  （`tests/test_vote_wave.py` 4 条、`tests/test_night_guards.py` 4 条、`tests/test_last_words.py` 2 条、
  `tests/test_house_wired.py` 2 条），10 具有了具名证人、2 具披露（P1 是等价变异，P4 的读者本来就在
  焦点集外）。**这一片没改过一行产品代码**：幸存者没有一个意味着行为错。P6/P9 那两格要披露的是入口
  ——CLI 今天不接受房规参数，钉住的是"支路接好了线"。账见
  〈`phases.py` 的 docstring 写了七八句"这里曾经错过"，其中六句当时没有读者：`#73`〉。
- ✅ `cached_tokens` 从线上到 `wolf audit` 那一格现在有一次真跑过的记录：`tests/test_live_path.py`
  新增两条用例，让一局真桌在桩线上打完，一根线报 `cached_tokens: 700`、另一根不提，钉的是比值、
  audit 那一格，以及 `null` 与 `0` 的分别能一路走到终端。**产品代码一行没改**，因为那两跳本来是对的；
  改的是"有没有人读过"——9 具变异两向取证（`/tmp/mut74.py`，摘掉新两条跑整套 vs 只跑新两条）显示
  `as_response_dict` 与 `complete` 那两跳上任何一处丢数，**791 条旧用例一条都不红**，而 C7/C9 反过来
  只有旧读者抓得住。基线与还原证据在〈端点上那一格到 audit 之间还有两跳没有人读：`#74`〉；`#70`
  末尾那句"每一格形状都来自伪造日志"同日更正为只对一半。
- ✅ §5 那笔折扣现在在**臂**这一级读得出来：`compare()` 的产物里多了一节「前缀缓存」，两臂各一行，
  比值是 Σcached ÷ Σprompt（池内相除，不是各局比值取平均），端点没报过的那一臂印 `—` 而不是
  `0.0000`，并逐臂说明有几局是"没问过它"。证人在 `tests/test_batch_live.py`（整局整批跑，线仍是
  桩），八具变异两向取证（`/tmp/mut75.py`）里五具在补用例前安静存活。**三具在 `before` 就红的都不
  是行为读者**：两具是行号闸门被插行顶红、一具是把报告跑到 `TypeError` 的坏变异，账见
  〈§5 那笔折扣走到批这一级就断了：`#75`〉。同一节还登记了 `#76`：一根被文档当成处理轴的字段
  （`Config.temperature`）在补 `#76` 之前实测从没上过线，两臂发的是同一串字节。
- ✅ 一根处理轴从"标签"变成了"上线的数"：`Config.temperature` 现在有读者了——`temperature_for`
  在梯子为空时取它当全局默认，梯子非空时逐座覆盖它；轴守卫判"这个字段有没有人读"也从按行扫正则
  改成看 AST，帮助文本里举过例子不再算读者。三条新用例两种角色：**驱动**产品代码的那条写出来时红
  在 `IndexError`，**证人**那条一写出来就绿（它补的是"这一格从来没人读过"，不是缺陷）。七具两向
  取证、外加"字节还原 `cmp` 通过而跑的仍是变异体"那一格，见〈轴守卫把帮助文本当成了读者：`#76`〉。
- ✅ 行号闸门把"号顶偏了"和"这句话点错东西"分成两句话报（**红/绿判据一字未改**，不引入我拍的漂移阈值）：
  名字还在文件里就说在第几行、差几行且带符号；整个文件都没有就直说找不到，不编出处。一条新用例 +
  四具两向取证（`/tmp/mut77.py`）：**四具在 `before` 全绿**——老的两条探针只断言关键词，措辞这一格
  补之前零读者。账见〈行号闸门把"顶偏"和"点错东西"报成同一句话：`#77`〉。
- ✅ 顶替端点的那只桩现在按**被问的那一座**报自己的座位：`oracle_answer` 从"取第一个命中"改成锚住
  `assemble.py` 真正落盘的那句话，失配就不写归属。一条新用例、两道地板（份数 + 九座都要有份），四具
  变异两向取证（`/tmp/mut78.py`）里**四具在 `before` 全绿**——摘掉这条新用例，老套件对这一格零读者，
  连"九个共享前缀喂进 `template_top1_share`"都没人读；预期活下来的那具（G3）兑现了，于是限界改成了
  断言。账见〈桩判官九座全自报 4 号：`#78`〉。
- ✅ `wolf batch` / `wolf compare`（M7：配对 `deal_seed`、处理轴守卫、McNemar exact、per-utterance
  cluster bootstrap、首尾 canary）代码已落地并跑过，全套离线。协议与判据口径在
  [docs/comparison.md](docs/comparison.md)。
- ✅ 反塌缩第 1 条（法官指派发言相）是**规则**，不是提示词里的一句建议：act 与指派不符按硬违规
  打回、重问一次（违规分类 `act_not_as_assigned`，口径见 [docs/metrics.md](docs/metrics.md) 的
  M3 一节）；重问后仍不遵守就发布模型的原话和它自选的 act，只记 `fallback=1`。这样
  `passivity_rate` 量的始终是玩家的行为，而不是引擎的指派表。
- ✅ 反塌缩第 3 条（把本轮已经出现的说法回灌进 C4）整条链有断言了，而且回灌的是**整句**不是 6 字
  滑窗：`tests/test_anti_repeat.py` 12 条 + 12 具变异见〈反重复回灌的四个名额全花在同一句话上〉。
  它钉的是"防线在不在、给的是哪四条"，**不是**"模板率有没有因此降下来"——那是 M3 在真端点上的账。
  同一份算术在指标侧的那格读数也顺手治了：`template_top1_freq` 名字承诺比例、值却是份数、且全仓库
  没有一条断言读过它，现在叫 `template_top1_share` 且是占本轮的比例，账见
  〈一个名字承诺比例、值却是份数、且没有读者的读数〉。
- ✅ 处理轴的两类拒绝各有**两个**读者，而"名单只有一张"这件事本身有闸门：命令行上 `--set` 拒
  （`batch.apply_overrides`），拿 manifest 出结论时也拒（`report.axis_diff`：身份落 `rejected`、
  后面没有代码的落 `inert`，两句文案不同，因为修法不同）。09:41:02Z 实测过第二半边以前只有一半：
  `model` 两处都拒，`enable_sheriff` 和 `regions.b0` 只在门口拒，声明成轴就放行。判据与 17 具变异
  （门口 5 具 `/tmp/mut62.py` + 比较这一半 8 具 `/tmp/mut64.py` + 不许各自抄名单 4 具 `/tmp/mut65*.py`，
  后一族整套跑过）见〈一根不存在的处理轴〉，口径在
  [docs/comparison.md](docs/comparison.md)。
- ⛔ 真端点的一局（M2 单座位、M3★ 反塌缩切片）还没跑过：需要 `WOLF_LLM_API_KEY`，而端点上
  一次体检有 8 个延迟单元格是 520 上游错误。**M3 是这项产品的存在性闸门**（`passivity_rate
  < 0.4` 是主判据），它不过，后面的批量对比没有意义。
- ⛔ 对比链的**结论路径**一次真数据都没走过：上面那两条 `--mock` 命令产的是替身桌，按 plan
  §十一 设计成必然被拒（`SYNTHETIC_TABLE`）；`data/` 不进版本库，所以那份批次也得自己重生成。
  真桌的 `OK` 要等 M3 的切片过了再跑。
- ⛔ 没有真人入座的界面。`Actor` 协议一期就预留了（`MockActor` / `LlmActor` 之外只需要再一个
  实现），但界面本身按计划后置。
- ⛔ 女巫用药救下的那一夜在公开产物里**不留痕迹**：`rules.NightResult.peace`（平安夜）算出来之后
  产品链上零读者，只有两条测试读它，所以"昨晚没人死"这件事九个座位谁都读不到（`#87` 的第三处发现，
  账见那一节）。修法要给公告新增一个事件，那会挪动 seq、逼金样本重钉、并改变模型读到的输入分布——
  那是**改处理**不是修句子，另立任务（`#88`）单独权衡，不在离线加固这一批里顺手做。
