# 两配置对比（M7）

计划 §8 第 2 条要求"两配置对比"必须说清六件事：配对方式、处理轴、检验选择、方差口径、
端点漂移、以及**什么时候根本不许出结论**。这六件事落在两个命令上：`wolf batch` 只负责产出
可配对的证据，`wolf compare` 负责判它能不能出结论——结论与证据分开，是因为配对一旦坏掉，
症状是 p 值变小而不是报错。

> 复现方式：仓库里**没有**现成产物——批次落在 `data/`，而 `data/` 整个在 `.gitignore` 里（日志
> 可能含真端点的文本，不进版本库）。下面那条 `--mock` 命令 0.3 秒重生成一份合成桌批次，
> `wolf compare` 对它的判定是稳定的：退出码 `1`，verdict `SYNTHETIC_TABLE`（被拒绝的那一类）。

## 两个命令

```bash
wolf batch --out data/plumbing --configs A,B --set B.temperature=0.6 \
           --games 2 --seed0 1000 --mock                            # 只验管线
wolf compare data/plumbing --axis temperature                       # → comparison.md，退出码 1
wolf compare data/plumbing --axis temperature --json -o data/plumbing/A-vs-B.md   # 落点可改名，机器侧另存一份
# 真端点那一条留在可粘贴区外面，规矩和 README〈命令一览〉那条一样：它要 `100.87.65.60:13000`
# 上那个判官，而照着这一块粘进终端的人不一定带着导出的 key——粘错了不是慢，是 401。
# wolf batch --out data/temp09-vs-06 --configs A,B \
#            --set B.temperature=0.6 --games 20 --seed0 1000
```

这一块是被**整块**执行过的：`test_the_comparison_recipes_block_runs_verbatim` 把围栏里的原文一个字
不改地写成脚本、交给真 bash、落在一个空目录里，然后要求末条的退出码是 `1`、两臂各落两局、
`comparison.md` 就在那个目录里。执行**之前**还有一道：块里不许留有会拨判官的命令行——跑这块的是
子进程，测试那层 `no_network` 夹具 patch 的是本进程的 socket，管不到它，所以"需要端点的那一条只能
留在注释里"必须是断言而不是提醒。

`--set` 只认 `<臂名>.<字段>`，字段名写错会停下并给出相近拼写；`model` / `base_url` /
`api_key_env` / `actor_kinds` 四个**根本不许当处理轴**（`config.FORBIDDEN_AXIS`）——命令行上
`--set` 直接拒，事后有人手改 manifest 也会被 `compare` 拒。两张名单（身份 `FORBIDDEN_AXIS`、记账
`INERT_FIELDS` + `INERT_LEAVES`）都住在 `config.py`，`batch.apply_overrides` 与 `report.axis_diff`
各读一次同一个常量：门口那次挡的是命令行，比较那次挡的是**没经过门口的批次**——旧版本跑出来的、
以及被人手改过的 manifest。

两根温度字段要分清：`temperature` 是全局那一格，`temperature_ladder` 非空时**逐座覆盖**它，这个
取舍只住在一个函数里（`config.py` 的 `temperature_for`）。拿 `--set B.temperature=0.6` 当处理轴就
别给两臂配梯子——梯子在的时候全局那一格永远读不到，那份对比会退回同一串字节（`#76` 之前它连
"读不到"都算不上：没有任何代码读它，两臂发出的是同一串请求）。

"两处读同一个常量"这句也不再只写在文档里：`tests/test_purity.py` 里的
`test_the_two_refusal_sites_do_not_keep_their_own_copy_of_the_lists` 按**名字**扫那两个文件
（docstring 与 `#` 注释剥掉，所以门口解释"为什么拒"的英文注释不算抄），谁抄了一份就走红。抄来的
名单值一样、行为等价，任何按值断言的用例都看不见它，所以只能这么扫。它独占的读数是**没有点号的
那一格**：只把 `enable_sheriff` 内联进 `report.py` 时，整套 742 条只红这一条；内联一份含点号路径的
完整名单时另有第二个读者——`test_the_no_reader_list_and_the_source_agree_in_both_directions`
（住在 `tests/test_batch_paired.py`）把 `"regions.b0"` 这样的字符串常量也算读取点，于是"零读者名单"
与源码对不上。四具变异、以及"行为套件不动"那句被打掉的预期，记在 README〈一根不存在的处理轴〉。

拒绝有两类，报错的话也分两类。上面那四个是**身份**：改了就不是同一批数据。另一类是**还没做**：
`Config._INERT`（顶层那一格是 `enable_sheriff`——plan §13 要求的就是"默认关的 flag"，不是"能开的
开关"）没有任何代码读它，改它只动 `config_hash`，两臂玩的是同一套规则，出来的报告却会写着
"警长局 vs 无警长局"。`axis_fields` 从 dataclass 派生（新增字段默认进对比，漏声明是响的），所以
"派生出来的字段一定有代码读它"这件事由一条用例双向钉着：轴名单里出现没人读的字段就红，`_INERT`
里出现有人读的字段也红（`tests/test_batch_paired.py`）。"读"的判据按**行**不按文件：`cfg.<字段>`
这样的属性访问算，注释行不算；读取点住在 `Config` 自己的方法里也算，但那个方法必须被别的模块
调用过——`token_budget_for` 正是这种"唯一的读取点在 `config.py`"的形状，而按文件排除整个
`config.py` 的旧判据把它误报成了标签（假红的代价不是报错，是逼人把名单**改回内联**好让套件变绿——
那正好是 `test_purity.py` 禁的那个形状）。5 具变异两向都证过：零读取的字段
要红、只在没人调的方法里读的也要红、在被调用的方法里读的不许红。

同一件事还有一层嵌套的（`config.INERT_LEAVES`，顶层那格是 `config.INERT_FIELDS`，`Config` 上留了
`_INERT` / `_INERT_LEAVES` 两个同名别名）：plan §5 的预算表把 B0 和 C1–C4 各写了一格，
装配器真正读的是 `c_total`/`b2`/`b1`/`a_hard`，所以那五格连 `tokens.warn`、`tokens.force_compact`
一起是记账格。顶层有挡的，往下走没有——`RegionBudget` 上没有 `axis_fields`，而 `report.axis_diff`
的覆盖判断是 `_axis_covers(declared, k)` 加 `k.split(".")[0] in FORBIDDEN_AXIS` 两个**前缀**式比较，
所以 `--set A.regions.b0=100` 一路穿过 `--axis regions`，两臂的 hash 差在一个谁都不会察觉的格子上，
报告照样印"区域预算 A vs B"（现量：`axis=["regions"]` 对 `regions.b0` 的差异回 `ok=True`、
`undeclared=[]`，`config_hash` 却是 `06ed754ac19e` 对 `082f481667f6`）。

门口那一半停在 `batch.apply_overrides` 的循环入口，不停在 `_set_path`：后者每层只看得见一个字段名，
`head == "regions"` 时分不清要改的是 `b0` 还是 `b2`，而这一格的全部意义就是两者不同。比较那一半停在
`report.axis_diff` 新加的第三个桶 `inert`（`#64`）：它**不能**并进 `rejected`，那一桶印的是"配对前提
已失效，改任何白名单都不会放行"，读了会让人去重跑批次；记账格的修法是回去把代码补上。两类的文案由
`test_the_refusal_for_an_empty_cell_names_the_empty_cell_not_the_paired_assumption` 双向钉着（记账那句
里不许出现"配对前提"，身份那句里不许出现"没有代码"）。嵌套名单的"读"还多认一种形状：`a_hard` 在
`src/` 里唯一的读者是
`REGION_CAP_KEYS = {"A": "a_hard", ...}` 这个字符串键，只按属性访问找就会把有人读的格子判成记账格。
这一轮的具名账、以及那具不合预期的为什么在这份语料里没有读者，记在归档
〈跨硬换行的判决读数搬进这一份〉一节。等价不是性质，是"此刻有没有断言在读"的读数；预期指不到具体
某条 assert 上，就先量。

退出码是给脚本看的：`0` 出结论、`1` 拒绝出结论（或探针跑不动导致整批中止）、`2` 命令本身写错了。

`2` 那条腿上的判据是"命令本身不对"，所以它必须**先于任何副作用**返回：重复的臂名、类型不符的
`--set` 值、配置里根本没有的字段名、`--games` 少于 1 局、越界的 `--seat`、要一块这份代码里没有的
板子，六者都在建目录、写 manifest 之前落到 stderr 的一行 `配置错误：…` 上。后两格是 2026-09-22 补的
——`batch --games 0` 以前先把批次目录写出去、再在摘要行上读那份空配对表崩掉，脚本于是拿到一个由崩溃
给的退出码；`--seat 10` 那一类则是"类型过了、范围没过"，印一份看着正常的时间线再退 0。判据与变异见
[README.md](../README.md) 的〈零局的批次先落了盘…〉与〈类型过了、范围没过…〉。

## compare 的七道闸门（按执行顺序）

| verdict | 何时触发 | 为什么它必须先于结论 |
|---|---|---|
| `NEEDS_TWO_ARMS` | 臂数不是 2 | 单臂没有配对，硬算会得到一个 p=1 的假检验 |
| `HASH_MISMATCH` | 日志里的 `config_hash` 与 manifest 不符 | 同一个 `--out` 跑过两次是常事，目录混批时配对表看着仍然整齐 |
| `AXIS_VIOLATION` | 两臂的实际差异超出 `--axis` 声明 | "只差一根轴"是这句报告的全部前提，多出来的差必须写成逐项清单 |
| `IDENTICAL_ARMS` | 两臂配置完全相同 | 那测的是端点噪声，把它报成处理效应是撒谎 |
| `INVALID_DRIFT` / `CANARY_LOST` | 批首/批尾探针答案变了、延迟比出 `1/1.5…1.5` 之外、或尾探针跑不动 | R7 的真实场景是"批跑到一半被人重启换了权重"。批尾探针失败不落 manifest 就只剩一堆孤儿日志 |
| `PAIRING_BROKEN` | 两臂 `deal_seed` 集合不同，或某臂内重复 | McNemar 比较的是逐对差值，少一局或多一局都不再成对 |
| `SYNTHETIC_TABLE` | 任一臂有非 `llm` 座位（mock 或真人） | 依据按种类挑，两张桌子不共用一条：mock 在 plan §十一（"必须真端点，mock 只会自证"），含真人在 §十五（真人不可 seed 控制）。顶层 `win` 与 nested `stats["win"]` 都被清空，读 JSON 的脚本也抄不走 |

每条拒绝都会落一份 `comparison.md`，里面写着原因和**可粘贴的复现命令**（合成桌的复现命令
一定带 `--mock`，否则粘回去就变成 20 局付费请求）。`INVALID_DRIFT`/`CANARY_LOST` 另外落一份
`drift.md`，把首尾五条探针的答案和延迟并排列出来。

## 通过闸门之后算什么

* **胜率**：McNemar 精确二项（`math.comb`，无 scipy 依赖），只对双方都有赢家（`good_win`/
  `wolf_win`）的局计入。中断和 `draw_day_limit` 都没有赢家，于是都被丢出分母，但报告分开数
  （`平局 N、中断或未完 M`，两桶加起来等于 `n_dropped`）：平局是**打完了**的结果，把它写成
  "未打完"就和把端点故障写成"模型打得很被动"是同一个错。日数上限本身也是一根合法的处理轴
  （`--set B.max_days=3`），这是唯一能预先看到平局长什么样的办法。
  `b+c < 6`（`report.MIN_DISCORDANT`）时"本批统计力不足"写在
  `verdict` 文本里，而不是只标一个 flag。被丢弃的对数 `n_dropped` 单独报，48 行表格里只比了
  31 对这种事必须看得见。
* **各臂绝对值（M1）**：`m1_win_rate` 每臂算一次，打在 `## 胜负与存活` 一节。McNemar 只回答
  "两臂有没有差别"，说不出任一臂自己的胜率是多少，而分母规则（只算决出胜负的局、Wilson 区间、
  `by_role_survival`）与 `audit` 无关，所以 M1 在这里而不是在单文件报告里。「局数」那一格只在有
  文件里没有局时才并排印两个数（`2（3 份）`：局数与份数，`#100`），相等时不带括号。表下面那两行
  note 是指标自己产出的原句：样本形状换了它说的话就换（全是平局时是"分母为空"而不是"仅作描述"），
  报告里另抄一句就会在其中一种形状下说谎；每臂有"没有局的文件"时在那两行之后各起一行点名它们，
  措辞与 `wolf gate` 那一条同一次算术。
* **退化局点名（`## 退化局`）**：每臂一行——几局、判定依据的阈值、以及**具体的 game_id**。plan
  §143 要的是"预先声明剔除规则，不做事后悄悄剔除"，所以这一节的动作只有点名：被点名的局仍然在
  上面每一个分母里（`test_a_degraded_game_is_named_in_the_report_and_kept_in_every_denominator`
  钉的就是"点名之后 `n_pairs` 和 `n_games` 不许动"）。只报计数不算点名：拿着 3/40 开不了那一局的
  转录，而这一节的用处就是让人去翻引擎替谁做了主。两处不并入好消息：日志里没有这一格的局单列
  `unrecorded N` 并写"未判定"（字段落地之前跑的批次不是 0 局退化）；一臂内部阈值不一致时那行改印
  "阈值不一致或未记录"，而不是挑一个数字印。`run_manifest.json` 的每一行也带着这一格
  （`rows[].degraded`，挨着它依据的 `rows[].fallbacks`），这样"批跑完时就声明过的规则"和"事后
  重新算出来的规则"在产物里分得开。
* **编号破损点名（`## 编号破损`）**：每臂一行——几局的 `seq` 锚点不是引擎写出来的、缺号/重号/倒挂
  各几处、以及**具体的文件名**。和退化局同一条规则（只点名，不剔除：编号破了不说明这局算不算数），
  点的东西不一样——退化是局的事，编号破损是这份文件的字节属性。用文件名不用 `game_id` 不是为了区分
  两臂（实测：同 seed 的两个文件连名字都相同，`A/` 与 `B/` 下都是
  `<utc>_g00000005.jsonl`，分开它们的是目录），而是因为 `game_id` 住在文件里面，而这一格报的恰恰是
  "这个文件被人改过"——拿被改对象内部的标签指它，就是 `#54` 那个"两局 `cat` 在一起、页眉照着后一条
  manifest 报错局号"的形状。
  算术不在这里：`batch.numbering_damage` 只经 `metrics.Game.seq_damage` 拿数，而那个 property 的身体
  就是 `events.seq_damage(self.events)` 一次调用。结构用例
  `test_the_batch_reads_the_numbering_arithmetic_instead_of_recounting_it` 钉的是"把整套算术抄进
  property 也照样红"，因为行为一致的第二份实现不改变任何一条数值断言：那两条
  （`test_a_game_carries_the_same_numbering_reading_the_transcript_prints` 与
  `test_the_numbering_tally_counts_files_not_kinds_of_damage`）本来就只会绿。
  `n` 按文件计、三类分开合计：一次编辑（把 6 改成 7）同时造出缺号和重号，把两项相加会把一份文件
  说成两局。
* **末行截断点名（`## 末行截断`）**：每臂一行——几局的末行没能读成事件、一共几行几字节、以及
  **具体的文件名**。同一份文件如果被砍掉的正是配对里那一局，上面"被丢弃 N 对"那一行还会就地补一句
  "其中若干对的日志末行被砍，不是这局没打完"，并点出是 `A/` 还是 `B/` 下的哪个文件。改口必须写在
  **那一句里**而不是留给下一节：读者先看到的是"中断或未完"，而那是一个描述模型行为的词——一局打完的
  日志末行就是 `game_over`，砍掉半行之后 `terminal` 退化成 `unfinished`，只看 `terminal` 的批次于是把
  文件被人生砍说成桌子没打完（与 `#39` 端点拒答冒充弃答同族，判据同一条：环境的条件不许印成被评测
  对象的性质）。分母一个都不动：`n_dropped`、`n_pairs`、各臂局数全部照旧，这一节只交代"没有赢家"那条
  理由是怎么来的（plan §8 的"预先声明、不做事后剔除"）。算术仍然不在这里：数来自 `Game.torn_extent`，
  而那只手是 `events.torn_extent`，所以这一节印的字节数和 `replay` 那句〔日志在这里截断〕里的是同一个数
  （实测同一份被砍的文件：两处都是 70）。结构用例
  `test_the_truncation_extent_has_one_arithmetic_and_three_readers` 钉的是"把算术抄进 property、或者让
  audit 那一格退回自己数行数和字节数，都照样红"——那两具的行为与真身一字不差，只有这一条读得到。
* **两份 `fallback` 拷贝对账**（报告里那一节的标题是 `` ## 两份 `fallback` 拷贝对账（只点名不剔除） ``）：
  每臂一行——比过多少条、几条的两份拷贝对不上、以及**具体的文件名**（`#120`）。它是 `audit` 那条单局
  对账的臂级影子：`audit` 一次只看一个文件，而"这一臂漂了几条"是关于臂的问题。算术不在批次这一层：
  `fallback_copies_by_arm` 逐局调 `metrics.fallback_copy_check` 再归约，键名原样搬（只多 `n`/`files`），
  `n` 按**文件**计、`divergent` 按**条**计，所以"一份文件漂了两条"不会被说成两局。干净那一臂照印，且
  `0 条对不上` 必须和"比过 N 条"同一行：单独一个 0 与"没人比过"在纸面上长得一样。找到不一致**不缩小
  分母**（与末行截断那一节同一条规矩）。离线取证（替身桌批 + 手工改标签，造的是
  文件而不是行为）：`- A：比过 115 条，2 条两份拷贝对不上（2 局）：…g00004242.jsonl、…g00004243.jsonl`，
  改前改后分母都是 115。真端点上这一格还没有读数——臂级要批次，而那三局真日志是 `wolf run` 出的单局。

* **机器侧出口（`#121`）**：`comparison.md` 给人读，`--json` 给脚本读，两个文件同目录同基名。
  JSON 就是 `compare()` 返回的字典挖掉 `markdown`，所以这一节里每一格在两边是同一份数——键集由
  用例按 `set(js) == set(compare()) - {"markdown"}` 钉住，新增一格自动进 JSON，不需要第二份名单。
  被拒批次照样落盘，但那九格臂级读数**键不存在**而不是 0；合成桌留在 `stats` 下的逐条率只出现在
  拒绝路径，OK 一批永远不带它。

* **各臂闸门判定（M3★）**：`m3_gate_verdict` 每臂算一次，排在 `## 逐条率` **之前**。理由是读序
  而不是算序：一臂 `FAIL` 或 `NOT_EVALUABLE` 时，它下面那四行 bootstrap 区间描述的是一个没过
  风险闸门的桌子，这句话必须在数字之前出现。两臂各算各的（`test_each_arm_gets_its_own_gate_verdict_rather_than_one_shared_answer`
  靠把 B 臂发言全换成同一句来破对称——配对批在两臂上做不出不对称）。每行 verdict 用 `臂｜判定`
  的写法，一份报告堆在目录里时 `grep -h '｜'` 只捞得到判定行。
  每臂的判定行下面另有一行 `- 截断：…`（`#92`）：它不是第六条判据，而是把"这一臂有多少回答被自家
  `max_tokens` 剪掉"放在它所扭曲的那两条措辞判据旁边，读数与 `audit` 的 `m7_cost_profile` 共用同一只
  谓词 `metrics.truncated_call`。**同一支渲染器还服务一个臂的批次**：`run_batch` 收尾时把这份表写成
  `<批次目录>/m3_gate.md`（`#94`），所以 `wolf batch --configs A` 那种没有对比对象的运行也拿得到判定，
  而两份产物里的那一段是同一个 `_m3_md` 吐出来的字节——`m3_gate.md` 只在它前面多两行页眉（批次名、
  "判定对象"那句带日志份数的）。`_m3_md` 收的是 `names` 序列而不是两个臂名，正是为了这件事：为单臂
  再写一份渲染，等于给那五条阈值第二处腐烂的地方。
* **区域预算（`## 区域预算`）**：每臂一行——局数、证人分歧，再加九段各自最坏超了多少 token。
  列名由 `metrics.REGION_CAP_KEYS` 派生（`#63` 把尺子从 4 根涨到 9 根之后，手抄的表头就和行里的
  格子错位了：markdown 不抱怨列数，读者看到四个标题、下面却是九个数），所以有
  `test_the_budget_table_header_names_every_ruler_the_rows_carry` 逐格数表头、数据行和分隔行；
  表下另有一行"越界局数"，逐区用 `/` 隔开并报（共 N 局）。
  尺子取自这一臂自己的日志（`meta.regions`），报告不读当时的 `Config()`，所以离开那个 shell 也能
  重算。减法是 `metrics.region_budget_check` 那一套，`audit`（一局）和这里（一臂）共用同一个函数
  ——`test_the_two_readers_of_the_region_budget_share_one_implementation` 钉的就是这件事：把其中
  一处换成"看起来一样"的第二份算术，它就红。
  最坏值单独一列不够：`worst_over.C = 106` 在"两局都压不过地板"和"四十一局里有一局改过提示词"
  之间是同一条读数，前者是配置错了、后者是某次采样撞上了，只有局数分得开。
  两种"给不出数"分开印，也都不是 0：这一臂的日志里没有 `meta.regions` → 写"无上限读数"（老批次
  的形状）；一臂里出现**两份**不同的上限 → 写"两份上限…拒绝挑一个"（超额相对哪把尺算没有唯一
  答案，平均两个上限得到的那个数谁也不是）。正常批次打不出这两种形状——上限进了 `config_hash`，
  混批先被 hash 闸门拒掉——所以走到这一格的前提是**文件被改过**，那恰恰是最不能让报告假装没事的时候。
  证人那一格同理：一批从没有过 `b2_over_cap` 的日志打 `—`，不打 0，因为 0 的意思是"每条都对上了"。
  九列里少一格（某个区一次观测都没有）只抹那一格，不抹整行。
  **表底下那句汇总话的适用范围单独算**：`measured` 是"这一臂有证人读数"的臂，`silent` 是没有的，
  "本批两臂的证人全部与长度一致"只在两臂都有读数且零分歧时说得上；一臂缺时改说"只有 A 臂有证人
  读数；B 臂…管不着它"，两臂都缺时说"这一栏整列没有读数"。这是本节唯一一处跨臂的话，也是最容易
  把"没测到"合并成"测了且没问题"的地方。

  > 复现（全程离线：`--mock` 批 + 把 `actor_kinds` 那一格改成真桌标签的测试夹具
  > `tests/test_batch_paired.py::_as_real_table`；`OK` 路径要求非合成桌，真桌数据要等端点）：
  > `wolf batch --configs A,B --set B.regions.c_total=250 --games 2 --seed0 5 --mock --out /tmp/clipin`
  > 然后 `PYTHONPATH=src:tests .venv/bin/python -c "import pathlib,test_batch_paired as T;T._as_real_table(pathlib.Path('/tmp/clipin'))"`，
  > 再 `wolf compare /tmp/clipin --axis regions.c_total`
  > → 2026-09-21 实测（verdict `OK`）：`| A | 2 | 0 | 0 | 0 | 0 | 0 |`、
  > `| B | 2 | 0 | 0 | 0 | 0 | 106 |`，两行 note 是 `越界局数 0/0/0/0（共 2 局）` 与
  > `越界局数 0/0/0/2（共 2 局）`、`本批两臂的证人全部与长度一致。`
  > 同一形状放大到 8 局（`--games 8 --seed0 11`，其余照旧）：`| B | 8 | 0 | 0 | 0 | 0 | 135 |`
  > 配 `越界局数 0/0/0/8（共 8 局）`——最坏值随局数变（不同桌的 C 峰值不同），局数那一行才说得出
  > "这是配置的事，不是某一局的事"。
  > `#63` 把尺子涨到九根之后同一条命令重跑：表头 12 格（`臂｜局数｜证人分歧` + 九把尺），
  > `| A | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |`、
  > `| B | 2 | 0 | 0 | 0 | 0 | 106 | 0 | 0 | 0 | 0 | 0 |`，`越界局数` 两行也跟着涨到九格
  > （B 臂是 `0/0/0/2/0/0/0/0/0`），表尾多出一行 `削过主张卡的 prompt：A 臂 0 个、B 臂 109 个
  > （最狠的一条少发 14 条指控）`——`B.regions.c_total=250` 那一臂压在那一桌 335 的地板以下，所以"最狠的一条"就是
  > 砍到只剩查验记录的那一条。整批没有这一格时（`#67` 之前的日志）改说"主张卡被动过没有 = 没有读数，
  > 不是 0"，一臂有一臂没有时 `—` 只盖在缺的那一臂上（`test_only_one_arm_having_the_knife_count_is_named_rather_than_summed`）。
  > 这一片的逐具账——减法语五处、渲染与接线四处、跨臂汇总那五处，加上搬家后原地重跑的
  > `metrics.region_budget_check` 那一族——在 `docs/iterations.md`〈另外三本手册页里的逐片电池账搬进这一份〉一节。
  > **三条用例是补在分支之后的**（`no_witness_at_all`、`one_disagreement_prints`、以及
  > `nobody_cross_checked` 的 null 那一半）：写它们的时候分支已经在树上了，没有 RED 可看。
  > 强度由 A10/A12/A13/A14/A5 五具顶回来——各自点名的红用例就是上面那行输出，不是"跑过了"。
* **逐条率**（`uncited_speech` / `refused_turn` / `passive_turn`）：按**局**做 cluster
  bootstrap，B=2000、种子固定并写进输出，用 ratio-of-sums 而不是"率的均值"。同局 90 条发言
  共享一副牌和一个死亡顺序，naive binomial 因此不可信——所以表里同时报 `deff`（bootstrap 方差 /
  naive 方差）和 `deff_gain`（非配对区间宽度 / 配对区间宽度）。配对重采样是两臂同抽一局，
  非配对是各自抽局；后者只用来量出配值的收益。
  **两个数都是比值，不是判词，方向可以朝任何一边**：期望的形状是 `deff` > 1（同局相关 ⇒ naive
  区间过窄），但 2026-09-21 在 mock 配对批上（4 局、把 B 臂的逐条分子隔条翻面）实测
  `deff` 0.52–0.75、`配对收益` ×0.73–×0.88 —— 三到六局的批子里局数本身就压过一切，名字里写着
  "收益"的那列印出小于 1 是常态不是故障。报告正文为此带了一句：`×0.8` 是一条结论（这批上配对
  让区间变宽了），**不是缺数据，缺数据印 `—`**。
* **`—` 有两种，都不是 0**：① 可重采样的局数不足两局 → `ci` 是 `[null, null]`，点估计照给；
  ② 两臂逐位相同（同 seed 的 mock 批就是这个形状）→ 每一副重采样得到同一个差，`ci` 是
  `[0.0, 0.0]`、宽度 0，除法无从做下去，`deff_gain` 是 `null`。第一种是样本不够，第二种是
  根本没有差；两种都写成 `—`，但都不能写成 `×0`——`×0` 读起来是"配对一点没换来东西"，那是结论。
  一局也要能出报告：端点半夜挂掉时，一份写着"不足"的报告比一份 KeyError 有用。
  复现：`PYTHONPATH=src .venv/bin/pytest tests/test_batch_paired.py -k "pairing_gain or identical_arms"`
  ——比值身份、小于 1 时那句解释、以及 `—` 不含数字，三个方向各一条。这一片摘走的变异账连同它
  逐具的红用例，记在归档〈跨硬换行的判决读数搬进这一份〉一节。

## 现在还缺什么（要端点）

* 真桌的 `OK` 路径**没有跑过一次真数据**。测试里那条路径是靠把 mock 批的 `actor_kinds` 改
  成 `["llm"]` 走通的（`tests/test_batch_paired.py::_as_real_table`），它证明的是配对算术与
  markdown 渲染不是 KeyError，不是任何模型结论。
* 五条 canary 探针（`batch.CANARY_PROMPTS`）在真端点上的**基线答案**还没采过：漂移检测是
  首尾自比，不需要金答案，但没有基线就分不清"权重变了"和"这题它一向答不稳"。等 M0 复跑时
  一起采。
* 40 局（20 局 × 2 臂）的实际墙钟现在只有**一半**是估的：乘数量出来了，未知只剩端点吞吐。
  `.venv/bin/wolf run --dry-run --games 20 --out /tmp/wolfcensus2`（实跑，
  10.8 秒、零 API 调用）末尾印的"成本合计"：**53.8 次调用/局**（23–75，随天数走：mock 桌均值
  3.2 天 ⇒ **17.1 次/天**，11.5–22.0）、**prompt 97,379 tok/局**、**完成预算 5,053 tok/局**
  （2,180–6,900）。括号里那句必须连着读：完成预算是"每次调用都问满 `max_tokens`"的**上限**，
  不是实测生成长度。
* 那句折扣现在是一个读数而不是一次手算：`m7_cost_profile` 把 `request.max_tokens` 与
  `response.completion_tokens` 成对求和，报 `fill_rate`（外加 `asked_calls`/`n_calls` 说覆盖面）。
  三局 mock-HTTP 替身桌：全局 0.4242 / 0.4296 / 0.4314，逐阶段
  `day_speech` 0.2857、动作档 0.6667，`truncated` 全 0。**这些数说的是夹具的说话长度，不是模型的**
  （Oracle 只说法官给过的短答案，兑现率对它近乎常数）——买到的是"真桌跑完一局，这个格子就有值"，
  墙钟从此不必等人记得去除。日志里没有 `max_tokens` 时它报 `null` 加一句 `fill_rate_note`，
  不报 0.0（`tests/test_golden_game.py::test_m7_invents_no_fill_rate_for_a_log_that_never_asked`），
  落盘日志两头对账由 `tests/test_live_path.py::test_the_log_alone_yields_the_budget_to_used_ratio_the_census_is_waiting_on`
  钉住。
* 由此两件事顺带定了性。其一，plan §187 那笔账的分档假设（110/30/15）与引擎不符：真实的分档是
  `max_tokens_speech=140` / `max_tokens_action=60`，由 `Config.token_budget_for` 一处决定，
  发请求的人和普查的人读同一个函数（`tests/test_purity.py::test_the_token_budget_enumeration_is_only_ever_read_through_one_function`
  钉住"只此一处"，`tests/test_live_path.py::test_every_turn_sends_the_max_tokens_its_phase_is_priced_at`
  钉住发出去的值）。其二，`max_game_completion_tokens=20000` 那根顶按 mock 节奏（实测最大
  2,013 tok/天 × `max_days=6` ≈ 12.1k）够不着，先把局掐停的是天数上限——它是备用的，不是日常的。
* 仍然未知的就是三个乘数之外的东西：`docs/calibration.md` 的 `D_decode_tok_s` 与
  `per_call_fixed_overhead_s` 两格（任务 #1/#5）。补齐之前，本节上面那些次数只能乘、不能替换
  那两格的位置。
