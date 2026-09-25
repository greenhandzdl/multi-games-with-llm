# 指标口径（M1–M8）

计划 §8 的八组指标，每组都是 `src/wolfengine/metrics.py` 里的一个纯函数，输入只有**已经落盘的
事件列表**。这条约束是全部设计的地基：数字必须能在端点下线、被人换掉权重之后重新算出来，所以
没有任何指标会去问引擎"现在状态如何"——只问日志"当时发生了什么"。

> 复现方式：`wolf audit <file>` 打印 M2–M8 与一局的基本计数；M1 是跨局的，由 `wolf compare`
> 打在 `comparison.md` 的 `## 胜负与存活` 一节（每个配置臂一行），或者直接
> `python -c "from wolfengine import metrics; print(metrics.m1_win_rate(metrics.read_dir('data')))"`。
> M3★ 的**判定**同样是跨局的：`wolf gate <dir>` 对一目录里已有的日志出五格判定（不重打牌，`#98`）。

## 一局能算的、和只能跨局算的

| ID | 函数 | `audit` 里的键 | 一句话 |
|---|---|---|---|
| M1 | `m1_win_rate(games)` | —（在 `compare` 里按臂打印） | 阵营胜率 + Wilson 95% CI |
| M2 | `m2_illegal_rate(events)` | `m2_illegal` | 非法动作率，first-attempt 与 final 两个口径 |
| M3 | `m3_gate_pressure(events)` | `m3_gate` | 闸门压力：拒绝 / 重试 / fallback / 各修复档命中 |
| M3★ | `m3_gate_verdict(games)` | —（在 `compare` 里按臂打印） | 五条预注册判据的 PASS / FAIL / 没法判 |
| M4 | `m4_hallucination_rates(events)` | `m4_hallucination` | 四子率：无引用断言 / 错引 / 不可能感知 / 自相矛盾 |
| M5 | `m5_style_collapse(events)` | `m5_style` | 模板塌陷度，主判据是 `passivity_rate` |
| M6 | `m6_belief_action(events)` | `m6_belief_action` | 自报 belief 与实际投票的落差，分阵营 |
| M7 | `m7_cost_profile(events)` | `m7_cost` | 逐阶段的 token / 墙钟 / 兑现率 + 漂移自检 |
| M8 | `m8_strategy_proxies(events)` | `m8_strategy` | 策略代理指标（不需要统计力就能看出塌没塌） |

`audit` 的顶层还有 `meta`（含 `config_hash`、`actor_kinds`、`reproducible`）、`synthetic`、
`kinds`、`speech_acts`、`assignment`、`prompt_tokens_est`、`prefix_cache`、`compactions`、
`region_budget_check`、`fallback_copy_check`、`degraded_game`。这一串说的是"还有"，不是"只有"：顶层键的完整集合钉在
`test_audit_prints_metrics_and_nothing_else` 那条断言上，加一格删一格都会先让它红。

## 分母规则（每个数字都必须能被追问）

**M1 胜率**：分母只含**决出胜负**的局（`terminal ∈ {good_win, wolf_win}`），其余终局按名字单列在
`excluded` 里，绝不混进分母。合成桌（`actor_kinds` 含 `mock`）按 plan §十一 整批剔除——被点名的
那句是"必须真端点（mock 只会自证，这几项不许省）"，§十五 管的是含**真人**座位的那一种：替身桌的
胜负是编剧写好的，对胜率没有信息量。一桌全是 mock 时 `good_win_rate` 是 `None` 并带一句说明，
**不是 0%**——这是刻意的：`n_synthetic_excluded` 旁边的一个数字若把剔除的局算进去了，那只是个
标签，不是守卫。

第三种是**份**不是局：没有页眉、或只有页眉而没有一条事件的日志（`Game.hollow_notice`）。它不进
`n_games`，也不进 `excluded` 与 `n_synthetic_excluded`——那三个读数都是关于打牌的，而这些字节里
没打过牌；把 0 字节的文件读成"一桌替身"或"一局没分出胜负"都是替没人写过的事实编出处。它们在
`hollow` 那一格里按份数、各自形状与**哪几份**（`paths`，basename）单列（`n_files` 仍然数它们），
两个出口（`wolf gate` 与 `compare` 的臂级表）印的是同一行，点名与计数同源（`#101`）。那两只句子
来自 `events.py` 里互斥的 `meta_notice` / `empty_notice`，与 `wolf gate` 用的是同一次算术
（`#99`、`#100`）。

**M1 的区间**：`wilson_ci(k, n)` 是 score 检验的反解，不是 `p ± 1.96·sd`——`k=1, n=40` 时后者给
`[-0.0234, 0.0734]`，一个越出 [0,1] 的胜率区间正是这种小样本批次最容易被拿去下结论的地方（Wilson
给 `[0.0044, 0.1288]`）。这片之前它唯一的数值锚点是金样本里那条 `wilson95 == [0.207, 1.0]`：一个
点，而且落在 `p̂=1` 的角上——角上 `p(1-p)=0`，于是"只漏掉 `p(1-p)/n` 这一项"的实现（变异 W6）在
那个点上与正确解**逐位相同**。现在它有自己的锚：
`test_the_interval_is_the_score_test_inversion_at_the_batch_sizes_used_here` 把 7 组 `(k,n)` 各自
与**另一条独立路径**（对 score 统计量做二分反解）比到 1e-9；
`test_the_interval_stays_inside_the_unit_interval_where_the_normal_approximation_leaves` 同时钉住
"正态近似确实越界"这个前提，前提不成立时这条自己会红；
`test_an_empty_denominator_is_the_whole_interval_rather_than_a_division_error` 钉 `n=0`。9 具变异
照 `/tmp/mut_wilson.py`（2026-09-21，9/9 CAUGHT；W6 与 W8 只有新用例抓得到、旧锚点那栏 SURVIVED，
`src/wolfengine/metrics.py` 按字节还原）。

`descriptive` 这个旗子读的就是这条区间的半宽：`wilson_ci(20, 40)` 实测 ±14.8pp，48 局时 ±13.6pp。

`draw_day_limit` 是这一批里唯一一个"打完了但没有赢家"的终局（plan §12 R10 的预注册裁定），所以
它既不进分母、也不和 `aborted_*` 同格：中断是"没拿到答案"，平局是"答案就是没分胜负"，把两者并
成一格会让一批端点故障读成"模型打得很被动"。判定只有一处 `rules.day_limit_reached`，用的是
`>=`——上限 6 就是"第 6 天打完终局"，不是"还有第 7 天"。上限的来源只有 `Config.max_days`，它
因此进了 `config_hash`、是合法的处理轴（`--set A.max_days=4`）。为什么要把它做成可调：实测 30 局
mock（seed 1–30，`tests/test_day_cap.py` 的那个桌）在上限 6 下最远只打到第 5 天，终局分布
`{wolf_win: 21, good_win: 9}`、**0 局平局**；压到 3 天才有 12/30 局 `draw_day_limit`。也就是说
这条预注册规则在默认上限下**根本观察不到**，`--max-days` 就是让它可观察的那个旋钮。命令行上
`wolf run --mock --seed 3 --max-days 2` 打出的是 `draw_day_limit`、day=2，`wolf run` 对平局返回
0（端点断了才返回 1）。

**M2 非法动作率**：分母 = actor 真的做过决定的那些 turn（`decisions()`；引擎自己写的事件不算，
拒绝一条引擎写的事实不是模型说了谎），不是全部事件。两个口径同时报，因为只报一个都能骗人：
- `first_attempt`：第 1 次尝试带 `violations` 的比例（`attempts[0]`，修复之前）——这是**模型**
  的合法率，只有它能发现退化；
- `final`：修好的与 `引擎代打` 的都算进去之后的最终非法率——这是**这一局**的合法率，只有它说明
  这局还是不是模型自己在打。
外加 `by_phase_role`（`day_vote/wolf` 这种键），因为塌缩往往是某个阶段某个身份的事。

**M3 闸门压力**：`retry_rate` / `fallback_rate` 从 `payload.meta.attempt` 与 `fallback` 计数，
`repair_rungs` 是各修复档命中分布（`published:-1` = 第一次就直接发布）。M3 回答的是一个具体的
问题：**长 prompt 下还 100% JSON-clean 吗**——短 prompt 上测到的干净不算数。
`kinds` 把打回原因归到第一个 `:` 之前（`_vclass`）。其中的 `act_not_as_assigned` 就是计划 §7
第 1 条的落地：法官指派的发言相与实际 act 不符按**硬违规**处理，即使那是在软阶段的发言轮——这个
字段是对引擎说的话，不是对同桌说的话，重问一次不损失任何措辞。重问后仍不匹配时引擎保留模型的
原话和它自选的 act、只记 `fallback=1`，因为换成引擎的默认动作就等于让 `passivity_rate` 去量指派表。

**指派的读者（`assignment`，`#68`）**：`request.assigned_act` 从落盘那天起没有读者（12:19:14Z 实测：
两局 mock 的 43 条 speech 请求全部带着指派，`grep -rn assigned_act src/ tests/` 却只命中写它的那一行），
而 `speech_acts` 数的是座位**实际采取**的 act——只凭它答不出 §7 第 1 条那个问题："是不是只对指派表
说 listen"。新读数 `metrics.assignment_compliance(events)` 给的就是另一半：`by_assigned` 是指派自己的
分布，`obeyed_first_try` 读第一次尝试有没有带 `act_not_as_assigned`，`obeyed_final` 读最终 act 等不等于
指派。分母是 `turns`＝**真的被指派过**的轮，这一格不能从 `kinds` 推："没指派"、"指派了且听了"、
"指派了没听"在那里都是"没有这条码"。`recorded`（带这个键的轮数）与 `turns` 分开，是为了让 `#68` 之前的
日志说得出"这一格没人量过"而不是"这批桌没有指派轮"——后者是真行为，前者只是文件旧。

> 复现（离线）：`wolf batch --configs A --games 2 --seed0 3 --mock --out /tmp/assign68` 然后
> `wolf audit "$(ls /tmp/assign68/A/*.jsonl | head -1)" | jq '.assignment'`
> → 12:28:35Z 实测 `{"recorded":58,"turns":21,"by_assigned":{"accuse":5,"probe":4,"align":2,"listen":2,
> "defend":7,"pivot":1},"obeyed_first_try":1.0,"obeyed_final":1.0}`。
> **mock 桌上 `by_assigned` 与 `speech_acts` 逐格相同**不是巧合也不是 bug：替身座位就照
> `legal.assigned_act` 出牌（`src/wolfengine/actors.py:224`）。所以这一格的分辨力要等真端点，离线能钉
> 的是接线与两口径的分离——`test_a_forked_act_moves_the_final_rate_and_leaves_the_first_try_one_alone`
> 改一条 speech 的 `payload.act`，`obeyed_final` 掉下来而 `obeyed_first_try` 纹丝不动；两格一起动就说明
> 其中一格读错了东西。

**退化局判定（`degraded_game`，plan §143）**：`agent.fallbacks > cfg.degraded_game_fallbacks`
（出厂 12，`test_the_shipped_degraded_threshold_is_the_pre_registered_twelve` 钉着）这一条判定只
在 `game.py` 结束一局时算**一次**，连同它当时用的阈值一起写进 `game_over` 的 payload：
`{"winner","terminal","degraded_game","degraded_threshold"}`。三个后续读数点全部从这条记录读，
没有第二处比较：`metrics.Game.degraded_game`（读取端，`None` 表示**没记录**——字段落地前的日志、
以及根本没有 `game_over` 的半局，都不等于"没退化"）、`wolf audit` 的顶层同名键（`null` 与
`false` 是两句话）、`batch.compare` 的点名表（见
[docs/comparison.md](comparison.md)）。阈值要落盘而不只留在 `Config()`：一个判定吃掉的数要跟着这个
判定走，让后来人"用今天的默认值去复核昨天的判定"就是预先声明的反面——同一句话也是下一节那把尺子
（`meta.regions`）落盘的理由。
`audit` 只多这一个布尔键、不多一个 fallback 计数——`m3_gate.fallback_rate` 已经拥有那个数，
同一份文件里两份口径就是当初 `act`/`action` 那类错误的形状。

> 复现：`wolf audit <file> | jq '{degraded_game, fallbacks: .m3_gate.fallback_rate}'`
> ——判定和它所依据的率并排打印，两者不一致就是这一格写歪了。

**折叠账（`compactions`，plan §83）**：五个键是五个量，谁都不能替谁回答"这局折叠得厉害吗"。
前三格问"折了没有"，后两格问"折到不能再折之后还差多少"——这两件事不等价，因为下面那节的天地板会
让折叠在软预算没满足时就停手。

- `max_rounds`：单个 prompt 的折叠点往前推进过几天（读 `request.compactions`，每 prompt 一个数；
  按天锚定之后一步就是整天，旧说法"被对折了几次"已经不对应任何代码路径）；
- `prompts_folded`：有多少个 prompt 是带着折叠发出去的；
- `events`：日志里有几格 `Kind.COMPACTION`，即 B1 摘要被改写过几次。只有这个是缓存冲洗的账本；
- `b2_prompts_on_the_floor`：有多少个 prompt 的 B2 顶破了 `regions.b2` 这个**软**预算（数
  `request.b2_over_cap` > 0 的条数）；
- `b2_worst_over_tokens`：其中最狠的一条超了多少 tok。

后两格由 `assemble` 在写那条 prompt 的那一刻算。它不再是唯一能量出这把尺子的地方：`meta.regions`
（一局一格，2026-09-21 起落盘）带着同一份 `RegionBudget`，所以 audit 能从另一侧再减一遍，两边互相核对
（见下一节）。仍然逐 prompt 记，是因为踩地板的量每条都在变——一局一格的尺子只给得出"最坏超了多少"，
给不出"哪几条"。老日志（字段落地前写的批次）里这两格是 `null`，不是 0——"没有一个 prompt 踩过地板"在那里
是一句没人测过的宣称。

标记由 `agent._mark_fold` 落盘。plan §83 那行写的是"compress.py 写 compaction 事件"，落在**内容**
上而不是落盘上：`compress` 是纯函数模块（无 LLM 无 I/O），它交出 `fold_body()` 的字节，追加由持有
log 的一方做。去重复用 `log.append(idempotency_key=...)`，键是**B1 摘要的指纹**——九个座位读同一份
B，所以一种状态只记一格；把 `window`（逐条留下几条）并进键里是错的，因为按天锚定之后那个条数每回合
都在涨（涨 = append = 缓存还在），实测会把 2 次冲洗记成 15 条标记。只认 `folded_days` 今天是**等价**
的（被折的那天已经过完，摘要不会再长），实测两局里"不同的 B1 指纹数"与"不同的 `folded_days` 数"分别
是 2/2 与 3/3；仍然按指纹记，是因为指纹说的是因——它就是那段让缓存失效的字节，而 `folded_days` 只是
今天恰好跟着它走的一个代理（改回按条折叠的那个瞬间，代理就撒手了）。`over_ceiling`
的那版 prompt 从没进过网络，所以不写：标记的主语是"模型看到了折叠后的编年史"。

**折叠按天，不按条（`compress.plan_fold`）**：B2 的下刀点只能落在某一天的第一条上。旧实现是"窗口对折
到 `min_window` 为止"，一旦 `regions.b2` 真的 binding，它给出的是一个**固定条数**的窗口，而编年史每
回合同步长一条 ⇒ `events[-window:]` 每回合往前挪一格 ⇒ 头部每回合改写一次，正是
`test_prefix_stability.py` 文档里写着"已经修掉"的那个 33 次/局故障。折整天之后：一天之内 B2 只往后
append，冲洗只随"又多折了一天"发生，上界就是 `max_days`（plan §11 要的"compaction ≤5 次/局"）。
只剩当天还不够放时，宁可 B2 超它的**软**区域预算——硬天花板由 `shrink` 那把刀管，而它是被计数的
（`res.shrinks`），因为一天之内下刀就是一次滑窗。这句"宁可"从 2026-09-21 起有读数了：超出量由
`assemble` 逐 prompt 写进 `request.b2_over_cap`，audit 侧打印成上面那两格。在此之前它只是这段散文里
的一个承诺，而散文驳不倒一个拿着一份日志说"这局 B2 没超"的人。

实测（`--mock`，seed 7/11/23，`regions.b2` 分别掐到 400/700/1500 tok，54·41 次调用/局）：
**九个格子里折叠次数与头部改写次数逐格相等**（2/2、1/1、0/0……），`shrink` 全 0；默认 `b2=1500`
三局都不折叠——这就是那六条前缀断言此前空转的原因，也是本文件现在多带一个 `squeezed` fixture 的原因。

> 复现（折叠压力两处都能进：`wolf run --dry-run --set regions.b2=200` 零 API 调用、直接印普查，
> `wolf batch --set` 才是把这张表写进批次报告的那一条——`compare` 这一侧没有 `--set`，覆盖项在批次
> 落盘时就定死了。正反两个方向由
> `test_the_negative_flag_claims_in_the_docs_are_negatives` 钉住，参数存在与否扫不出来）：
> `wolf batch --configs A --set A.regions.b2=200 --games 1 --seed0 7 --mock --out /tmp/f && wolf audit /tmp/f/A/*.jsonl | jq '.compactions'`
> → 2026-09-21 实测 `{"max_rounds": 3, "prompts_folded": 40, "events": 3,
> "b2_prompts_on_the_floor": 23, "b2_worst_over_tokens": 180}`。
> **换 `--seed0 1000` 前三格全 0，这不是故障**：那一局的编年史只有 2 天、27 条公开事件，折掉第 1 天
> 只剩 3 条逐字发言，低于 `min_window=4` 这天地板，于是按上面那段"宁可超软预算"停手。而停手这件事
> 只有后两格看得见：同一份文件里它们是 `13` 个 prompt 在地板上、最坏超 `191` tok——只看前三格的话，
> 这一局和"预算很宽松、根本不用折"的那种局打印出来一模一样。换 `--seed0 200` 是第三种形状
> `2 / 37 / 2 / 41 / 336`：折过 2 次之后仍然有 41 个 prompt 坐在地板上，"折了几次"和"够不够放"果然
> 不是同一个数。
> 逐回合的改写次数要读 prompt 字节，跑 `pytest tests/test_prefix_stability.py -k slide`。
> 本切片 11 具变异体全部被抓回：8 具改 `compress.plan_fold`（四个出口各删一次、天边界判定反了、
> `shrink` 空转、回到固定条数窗口、摘要不随 shrink 更新），3 具改 `agent._mark_fold` 的键与指纹
> （幂等键加回 window、指纹算上条数、状态没变也写标记）。
>
> B2 分量的落盘与上面那两格另算 8 具（2026-09-21 重跑：**8 具全部 CAUGHT**，具名红用例逐具记在
> `tests/test_prefix_stability.py::test_the_day_floor_breach_is_written_into_every_request` 和
> `tests/test_cli.py::test_audit_reads_the_fold_rounds_out_of_the_requests_it_writes_them_into`
> 这两个名字下面）：5 具改 `assemble`（分量算了不落盘、超额的尺子拿成 `rb.b1`、不 clip 把负差额印成
> 超额、`B1` 那格算成整个 B 正文、`B2` 那格算成整个 B），3 具改 `cli.cmd_audit`（字段缺失读成 0、
> 数成 prompt 总数而不是踩地板的条数、最坏超额印成恒定 0）。

**预算的尺子也落盘（`meta.regions` → `audit.region_budget_check`）**：`region_tokens` 是九段没有尺子的
长度，光有长度就只能问"多大"，问不了"超没超"。`game.open_log` 把这一臂自己的 `RegionBudget`（11 个数）
一局一格写进 `meta`，`metrics.region_budget_check` 拿其中九把尺去减各区的峰值观测（`A`/`B1`/`B2`/`C`
之外，`#63` 把 plan §5 那五格子预算也接了进来）。这一份实现有**两个
读取点**：`wolf audit` 原样印一局，`wolf compare` 的 `## 区域预算` 把它逐局减成一臂一行
（[comparison.md](comparison.md)）——本仓库里"同一个率两处实现"已经栽过两次，所以接线有专门的用例：
`test_the_two_readers_of_the_region_budget_share_one_implementation` 拿同一局同时喂两边并要求逐格相等：

- `caps`：真正用到的九把尺（`a_hard` / `b1` / `b2` / `c_total` / `b0` / `c_persona` / `c_belief` /
  `c_private` / `c_task`），取自这份日志而不是出厂 `Config()`——
  所以 `--set A.regions.c_total=250` 那种实验臂不需要有人记得当时写了什么。`B` 不在里面：它是 B1+B2，
  再给总和配一把更软的尺，就是给同一批字节放第二个权威。五格子预算里**只有 `C2` 后面有刀**（削主张卡），
  另外四格是警报尺：plan §5 的牺牲顺序 ⑤⑥ 明写私有信息与 A 永不压缩，`C1` 是反塌缩 P1/P3 的载体，
  `C4` 是 act 闸门的出口，那四格超了不许砍，只许在报告里红着。三局 mock 出厂实测峰值
  71/57/328/111/115（11:12:16Z，顺序 B0/C1/C2/C3/C4），都还没咬。
- `worst_over`：每区 `max(region_tokens) - cap`，夹在 0（只报超额；"还剩多少余量"是另一回事，而
  `region_tokens` 本身就读得到）。某区一次观测都没有时打印 `null`，不打印 0。
- `b2_witness_agrees`：逐 prompt 比 `request.b2_over_cap`（写盘那一刻算的）与
  `max(0, region_tokens.B2 - meta.regions.b2)`（现在从字节减出来的），有一条对不上就是 `false`。两份口径
  各自都可能说得通，没人核对过它们就是"同一份文件里两个答案"的形状——而伪造与版本错开恰好都长那样。
- `card_prompts_thinned` / `card_worst_claims_dropped`（`#67`）：读的是每 prompt 的
  `card_claims_dropped`——装配器写盘那一刻记下的"这条 prompt 的主张卡被削掉几条"。这两格是整个
  `region_budget_check` 里唯一**不跟尺子比**的：草稿那张卡不在日志里，没有任何东西能与它对账，而这正是
  装配器必须当场把它写下来的原因——`region_tokens.C2` 量的是砍完以后的长度，单看它分不出"这张卡本来
  11 条"和"本来 14 条被砍掉 3 条"，后一种是对模型的处理差异。它们也故意算在 `meta.regions` 那道守卫
  **之前**：一把没量过的尺子不该顺手抹掉两格不需要尺子的读数。

> 复现（两条都是 `--mock`，离线）：
> `wolf batch --configs A --games 1 --seed0 7 --mock --out /tmp/f27a && wolf audit /tmp/f27a/A/*.jsonl | jq '.region_budget_check'`
> → 2026-09-21 实测 `{"caps":{"a_hard":2000,"b1":1200,"b2":1500,"c_total":1450},"worst_over":{"A":0,"B1":0,"B2":0,"C":0},"b2_witness_agrees":true}`；
> 把上限掐到 C 的地板以下：`wolf batch --configs A --set A.regions.c_total=250 --games 1 --seed0 7 --mock --out /tmp/fc250 && wolf audit /tmp/fc250/A/*.jsonl | jq '.region_budget_check'`
> → `{"caps":{...,"c_total":250},"worst_over":{"A":0,"B1":0,"B2":0,"C":85},"b2_witness_agrees":true}`。
> 那 85 tok 就是下一节说的"砍不动的那部分"第一次成为读数：把 `A.regions.c_total` 压到 250 之后，
> 瘦身已经一条主张不留，C 仍然是 335，而这份文件自己说得出他要的是 250。
> 缺 `meta.regions` 的老日志三格（`caps`/`worst_over`/`b2_witness_agrees`）全是 `null`，而
> `card_prompts_thinned` 和 `compactions.b2_worst_over_tokens` 照常是数字——那两格读的
> 是每 prompt 的字段，用不着尺子。`card_*` 是 `#67` 新加的，落进来时就带着这条反向断言（缺尺子的臂必须
> 照旧给出削卡条数，见 `test_a_log_without_the_caps_in_meta_prints_null_not_zero`）。
> 本切片 9 具变异体**全部 CAUGHT**（2026-09-21 串行重跑，具名红用例逐具记在跑批输出里）：8 具改
> `metrics.region_budget_check`（这一格最初写在 `cli.py` 里，同日搬进 `metrics.py` 让两个读取点共用，
> 搬家后 9 具原地重跑一次照旧全 CAUGHT：老日志读出 0 而不是 `null`、`all` 换成 `any`、比较写成单向的
> `>=`、B2 的账拿
> C 的尺去核、超额不夹地板、`caps` 把整份 `RegionBudget` 都印出来、区域到上限的映射错一格、峰值取 `min`），
> 1 具改 `game.open_log`（`regions` 落成空字典）。
> **`all`→`any` 那一具第一版是 SURVIVED 的**：伪造用例把**每一条** prompt 的 `b2_over_cap` 都改成 9001，
> 于是"全都对得上才算一致"和"有一条对得上就算一致"打印出同一个 `false`，两句完全不同的话分辨不出来；
> 改成只伪造第一条（坏一条、其余都好）才抓回。上一节 A7 那个教训的另一种形状：交叉校验的价值全在
> "多数好、少数坏"这一档，只测"全都坏"等于把它降级成一条布尔或。

**C 区超预算时砍哪一半（`assemble._region_c`）**：C 里唯一可砍的是 belief 卡的逐条主张；座位行、
人格卡、本轮任务都砍不得（人格卡塌掉就是 §7 的 P1/P3 防线失效，任务块砍掉就是 act 闸门失效），
私有信息块也不动——那几句夜里的话在整份 prompt 里只有这一处。2026-09-21 之前那段 `slim[:8]`
的方向是**反的**：它留卡片头部（天数、名册、最旧的几条主张），砍掉最近的指控和"你自己的查验记录"
——而 B 区里永远没有第二处写着查验结果（夜里法官那句"他是狼人"只发给那一个座位，从不进公开编年史），
所以被砍掉的正是模型无处可查的那一样。现在按 `render_card(belief, max_claims=k)` 从最新的一条往前留，
`k` 从"装得下的最大条数"往下试，一条都放不下就一条不留（表头跟着主张行走，不留下指向空列表的标题）。

> 复现（离线，压的是 belief 卡不是模型）：出厂 `c_total=1450`，真实 mock 局里 C 的最大观测 **585 tok**
> （seed 7，104 个 prompt），所以这个分支出厂永不触发；要触发得从覆盖项进：
> `wolf batch --configs A --set A.regions.c_total=300 --games 1 --seed0 7 --mock --out /tmp/fc &&
> jq -s -c 'map(select(.request)|.request.region_tokens.C) | {prompts: length, max: max, over: (map(select(.>300))|length)}' /tmp/fc/A/*.jsonl`
> → 2026-09-21 实测：`c_total=1450` 是 `{"prompts":104,"max":585,"over":0}`，`400`/`350` 都是
> `max` 正好等于上限、`over:0`（瘦身收敛了），`300` 是 `{"max":335,"over":8}`，`200` 是
> `{"max":335,"over":37}`。335 就是这一桌 C 的**地板**——座位行 + 人格卡 + 任务块 + 查验记录里砍不动
> 的那部分，所以"越线 37 条"不是刀没落下，是已经没有东西可砍了。
> **地板以下的那个缺口已经关掉（2026-09-21，就是上一节那把尺子）**：C 比 `c_total` 还差多少现在读得出来
> 了——`A.regions.c_total` 压到 250 的那一臂，`region_budget_check.worst_over.C` 打印 **85**，而同一个
> 读数旁边的 `caps` 就写着 250，不需要谁记得当时 `--set` 过什么。
> **`k` 本身后来也有了读数（`#67`）**：这一格当时判的是"策略不是性质、不值一个 per-prompt 字段"，
> 判错了——被削掉的主张是模型**收到的处理**，而两臂的 `C` 长度本来就允许不同（§5 的预算宽紧是轴的一部分），
> 只有"这一条 prompt 少发了 3 条指控"能把"预算紧"和"喂给模型的事实少了一半"分开。落盘的是
> `request.card_claims_dropped`，读出来的是上一节那两格 `card_*`。（B2 那两格逐 prompt 记的理由不变：
> 默认预算下就会踩地板，23/41 个 prompt；C 要把 `c_total` 掐到 335 以下才踩得到，那是实验臂自己选的。）
> **`C2` 现在是一把单独下得动刀的尺子（`#63`）**：触发条件从"整段 C 超了 `c_total`"变成"整段超
> `c_total` **或**主张卡那一块超 `c_belief`"，缩卡的那一轮也拿 `c_belief` 判装没装下。同一份超预算草稿
> （20 条新主张）实测：整段 C 是 672 tok、离出厂 1450 差一倍多，`c_belief` 压到 400 却照样砍——草稿卡
> 459 tok，每砍一条省 29，keep=13/12/11 分别是 430/402/373（11:22:16Z、11:22:57Z），所以最少的一刀
> 恰好落在 11 条。把 `c_belief` 放宽到 9999、其余不动，则 14 条主张一条不少：两侧都有用例，才分得开
> "读了这把尺"和"任何让 C2 变小的改动都算通过"。
> 本切片 11 具变异体**全部 CAUGHT**（7 具改 `assemble._region_c`：闸门永不合、搜索方向反了、候选正文
> 从没换过、装没装下判反、地板那一轮什么都不砍、地板定在 1 条、上限拿错字段；4 具改
> `belief.render_card`：杠杆不认、切片方向反、砍空还留表头、瘦身时连查验记录一起交出去），具名红用例
> 逐具记在跑批输出里。**其中"上限拿错字段"第一版是 SURVIVED 的**：两条超预算的用例只要求"砍到装得下"，
> 把尺子换成 `c_persona`（250）恰好也砍得动，分辨不出来；补了反方向的
> `test_c_under_cap_hands_over_the_whole_card`（没超预算就不许动刀）才被抓回。一条守卫要两侧都有用例，
> 只测它触发的那一侧等于没测。
>
> `#63`/`#67` 两片另算 11 具（`Z1`–`Z11`，`/tmp/mut_run.py` 12:01:58Z→12:04:24Z 串行一次跑完；
> 每具跑完 `cmp` 逐字节还原，11/11 CAUGHT，且 11/11 先过 `import` 一关——没有一具是靠语法错误"被抓"的）。
> 分散在装配器 / 度量 /
> 批次三台机器上：`_region_c` 的触发条件不看 `c_belief`、缩卡循环把尺子拿成 `c_total`（最少那一刀的
> 算术失去读者）、`_accusation_lines` 恒 0、地板那一支不记刀数、`payload_for_log` 少写一行、
> `region_budget_check` 的无 caps 早退不带 `card_*`、削卡条数把写 0 的 prompt 也算成削过、
> `_region_md` 的 `all`→`any`。另三具是臂级聚合里同一句"这个臂没有一局量过才算没量过"的
> `all`→`any`（`witness_disagreements` / `card_prompts_thinned` / `card_worst_claims_dropped` 各一具）。
> `Z4`（白名单少写一行）一次红 8 条：装配器之外每一个读这一格的出口——audit 的 `compactions`、臂级表
> 那两格、三处"没有读数 vs 0"的形状——各自发现它没了，而不是只有白名单那条用例红。一格多个读者，
> 这一具的红集就是那张读者名单。
> **这后三具第一版是 SURVIVED 的**：`tests/test_batch_paired.py` + `tests/test_cli.py` 122 条全绿，
> 因为已有的臂级用例只有"全有"和"全无"两档，半缺（一臂里一局丢了证人、另一局还在）那一档没有读者——
> 补了 `test_half_an_arm_without_the_witness_keeps_the_reading_that_survived` 之后三具各自红在那一条上。
> 是这一族教训的第三个形状：`all`→`any` 上一节（逐 prompt 的证人）和本节（臂级聚合）第一次都 SURVIVED，
> 成因不同、判据同一句——交叉校验值钱的地方全在"多数有、少数没有"那一档，只测两端等于把它降级成布尔或。

标记是 `visibility="all"` 却**不是编年史**，`compress.chronicle()` 把它挡在 B 之外——一段摘要不能去
摘要自己，而且它带的新 seq 会插在逐条那段前面，把已缓存的前缀改写掉。两侧各有一条用例钉着：整局侧
`test_a_marker_is_public_but_never_becomes_chronicle`，纯函数门口侧
`test_a_marker_is_not_chronicle_material_even_handed_to_the_primitives`。标记发射与编年史门口守卫
那一片另算 12 具（和上面那 11 具不重叠：上面改 `plan_fold`，下面改 `_mark_fold`/`chronicle` 以及
三个纯函数的门口）：发射点不调用、摘要改成转述、`window` 写死 0、键去掉、`folded_days` 不落 payload、
标记写成私有、`chronicle` 两道过滤各删一次、`chrono_bytes`/`plan_fold`/`fold_body` 门口不归一化
（各 1–3 具）。2026-09-21 重跑：**11 具 CAUGHT（具名红用例逐具记在跑批输出里），1 具按预期 SURVIVED**
——就是上面那句"键放松成只认 `folded_days`"，它今天测不出差别，因为两者一一对应。
`fold_body` 那道门口也是这一轮才补上的：先前没有断言守着它，而它的 COMPACTION 半边是等价变异
（`day_fold_lines` 本来就只数 SPEECH/VOTE/DEATH），所以那条断言打在建了功的可见性半边——一句夜里
私聊混进第 1 天，摘要就从"发言3人"变成"发言4人"。

> 复现：`wolf audit <file> | jq '.compactions'`。出厂预算下的 `--mock` 桌五键全 0（B 区没折过，
> B2 也没顶破软预算），`wolf audit … | jq '.kinds.compaction'` 在那里是 `null` 而不是 0——没有这类事件时字典里就没这个键。
> 把 B 自己的预算压小（`regions.b2=200, b1=200`，即 `tests/test_live_path.py::_squeezed`）走一遍
> 离线 oracle 局，实测 `{"max_rounds": 2, "prompts_folded": 28, "events": 2,
> "b2_prompts_on_the_floor": 22, "b2_worst_over_tokens": 188}`：28 个折叠 prompt
> 只对应 2 种前缀；`wolf replay`（加不加 `--god` 一样，它是公开事件）在这份日志上打出 2 行
> `法官：[折叠] …`。这两个读数都是 2026-09-21 在本机重跑得到的，来自 mock transport（没碰端点），
> 只算管线的量级，不算模型行为的结论。

**M4 幻觉证据率**：分子从**闸门修复之前的 `response.raw`** 里数，否则闸门自己把证据吃掉了，
量出来永远是 0。四个子率里 `impossible_percept_rate` 是这项产品最想要的那个：对只有公开事件
可见的窗口做第一人称感知断言（"昨晚我听到…"）。词典是 第一人称 × 感知动词 × 夜指时间词，
**纯词法**判定（plan §13 拒绝在这里让 LLM 当裁判）：没有"语义歧义"这一档，三张表同时命中才算、
少命中一类就不算，所以偏差朝漏报，`impossible_percept_rate` 读作**下界**。朝反方向的误报词典本身
给不出清单（"昨晚"既可以指夜里被刀也可以指白天公布的票型），测试里钉住的只有一例，见
`test_a_known_false_positive_is_recorded_as_one`（"注意到昨晚的票型"——票型是公开信息），文档里
因此不写"词典零误报"。
`self_contradiction_rate` 只算好人，且只与**该发言之后**的那一张票配对（`note` 字段里就带着这句）。

**M5 风格塌缩**：按发言轮（`speech_rounds`）算，三把措辞尺子各取均值（`collapse_round_mean`、
`dup_exact6_mean`、`opening_distinct_mean`），`worst_round` 单独留全量数据。
**主判据不按轮取均值**：`passivity_pooled` 是"被动次数 ÷ 全部次数"，因为一个 2 人轮和一个 9 人轮
在均值里同权重——同一局里两者能对出两个数（`#102`：真日志 `g00000301` 三轮规模 9/2/7，audit 读
0.2037、闸门读 0.1111；构造的 9+1 两轮则是 0.2778 对 0.5，而阈值是 `<0.4`，正好一边一个判法）。
每轮的速率仍然逐轮留在 `rounds[]` 里，看最塌缩的那一轮要看 `worst_round`，不是看头条。
这一格旁边印的就是闸门那三条阈值，所以它必须与 `m3_gate_verdict` 出自同一次算术：三条判据的
每轮读数、按轮均值、按次合并分别只写在 `round_readings`、`round_mean`、`pooled_passivity` 一处。
`collapse_round` 是同轮两两 char-4-gram 多重集 Jaccard 的均值（阈值 0.35），
`opening_distinct_rate` 看前 8 字是否各不相同。`template_top1` 是那一句被反复念的**整句**文本，
`template_top1_share` 是它占**本轮开口人数**的比例（plan §8 M5 那格的"占全体比例"；分母以前是本轮
**条目数**，`#105` 换的）——`template_top_fragments` 给的是**最长**公共片段而不是 6 字滑窗（≥6 字、
被同轮 ≥3 份发言共享，按共享份数降序、同份数按长度降序）。分母取本轮而不是这一局：五份里三份重复
（0.6）和三份里三份重复（1.0）在份数上都是 3，而 M5 后面要按轮取均值；同样三句复读配上六张空手
以前读 `3/9`，与不配空手时的 `1.0` 是**同一行里的两个分母**（那一行的 `dup_exact6_rate` 已经读
`1.0` 了，21:12:57Z 现测）。
一行既然有两个分母，`rounds[]` 就把两个都印出来（`#107`）：`n` 是本轮条目数，`passivity_rate` 除的
正是它（沉默就是那一格要量的东西，一个 turn 也不能少算）；`n_spoken` 是真正开口的人数，也就是三把
措辞尺子共用的那道地板（`comparable_speeches` 数出来的）。以前只印前者，于是一行写着 `n` 为 2、
三条措辞尺子全 `None`，读者要自己想一次除法才知道"其中一张是空手"。这一格在今天的真数据上换不来
任何读数：三局九个发言轮里两格只有一轮分家（干净树上 22:01:48Z 现读，`g00000301` 的 day-1 PK，条目 2、开口 1；
改动前同一行只有条目 2 与三条 `None`，那个"开口 1"是这一格带来的），
而 20 局替身桌的 62 个发言轮里两格处处相等（21:42:12Z 现读，空手人数分布只有一个档：0）。它换到的是
"缺席在行里有没有解释"，判据只能长在构造的夹具上（`tests/test_m3_gate.py` 的
`test_the_row_prints_both_of_its_denominators`）。
同一份算术还喂给提示词的 C4 黑名单：前 4 条、每条截到 24 字，链路与判据见
`tests/test_anti_repeat.py`（那里也记着 12 具变异的账），读数本身的判据在
`tests/test_m3_gate.py`。
**开口人数不到两个的轮，三把措辞尺子都给 `None`，不给 0.0 也不给 1.0**：`collapse_round` 以前把"九条空文本"量成
`1.0`（空串两两的 4-gram 并集为空，`jaccard` 定义为 1.0），也就是一桌沉默在 `<0.35` 上是一张假 FAIL；
`opening_distinct_rate` 以前在全空的一轮上按"非空发言数"做分母，直接 `ZeroDivisionError`，而输入是
空列表（`[]`，"这一轮没发生"）时答 `1.0` = "开头全不同"，在 `>0.8` 上是一张免检通行证。
`#95` 那次只挡了"整轮没人说话"，漏掉了"**整轮只有一张话筒**"：一个人开口时两条尺子仍各给一个通过方向的
定义值（`0.0` 与 `1.0`），而那正是"没有一对可比"——plan §8 给 `collapse_round` 的定义就是"**两两** Jaccard 的
均值"，`[]` 与"一人开口"以前还不同答案（`0.0` 与 `None`），两兄弟对同一件事两个口径。
现在这两种都读作"这一轮没有措辞可量"：该轮从两条判据的池子里拿出来（于是批级的 `n` 只数有读数的轮），
一臂全是这种轮就走 `ok=None` → `NOT_EVALUABLE`。真日志上这不是假想敌：
`data/real-20260924/…g00000301` 的 day-1 PK 轮有两条发言、其中一条是空串，改动前闸门 `collapse_round` 读
`0.0089`（n=3），改动后读 `0.0134`（n=2）——那一半是从一次"没有可比对象"的投票换来的（20:18:29Z 现读）。
**沉默由 `passivity_rate` 说**，那是主判据，量的本来就是"没点名的听客比例"，一个 turn 也不该少算。
`#103` 那一轮还剩第三条尺子站在旧地板上：`shared_substring_rate`（`dup_exact6_rate`，plan §8 M5
点名的第三条，没有阈值、只出数）以前把分母取成"本轮条目数"而不是"本轮开口人数"，于是一轮里两个人
逐字复读、七个人空手读成 `0.2222` 而不是 `1.0`，一个人开口读成 `0.0` 而不是"没有读数"（20:45:51Z
改动前现测）。现在三把尺子的地板写在同一处（`comparable_speeches`）：它一次回答"开口的有哪几份"，
`None` 的判据因此只有一条，不会再有一处漏掉。真日志上同一格的账：`g00000301` 的 `dup_exact6_mean`
改动前 `0.5079`（n=3）、改动后 `0.7619`（n=2，20:43:00Z 现读），那三成的"更不重复"同样是那个只有一张
话筒的 PK 轮投出来的。**批级那一格今天有两个读者**：构造夹具（`tests/test_m3_gate.py` 的 `#104`
那一节）钉它跳过没有读数的轮，金样本（`tests/test_golden_game.py`）钉它的数值 `0.1111`——`#102`
电池里"`dup_exact6_mean` 取错列"那具变异当时是兑现的 MISSED，现在拿错列就会红。
判据在 `#95` 与 `#103` 那两段四条用例里（`test_a_round_of_nothing_said_gives_no_opening_reading`、
`test_a_silent_round_leaves_the_gate_without_a_reading_but_keeps_the_measured_ones`、
`test_a_round_with_one_microphone_gives_no_pairwise_reading`、
`test_a_one_microphone_round_is_left_out_of_the_style_denominators`）。
同一条地板第四次兑现的是**模板**这一把（`#105`）：它的地板不是 2 而是 3——`template_top_fragments`
要求一段片段被同轮至少三份发言共享才算模板，所以两份发言的轮压根不可能有候选。那种轮以前交回
`""` 加 `0.0`，于是同一行里既写着"开口的人全在复读"（`dup_exact6_rate` 读 1.0）又写着"本轮没有
模板"（21:12:57Z 现测）。今天 `template_top1` 与 `template_top1_share` 一起缺席，两个都是 `None`；
而三个人以上开口、确实没有共享片段的一轮仍然读 `""` 加 `0.0`——那是量出来的干净，不是缺席，
`#66` 那句"`None` 会被均值吞掉"在这里照旧成立。两把尺子的 `min_len` 与 `min_count` 由
`template_top_share` 传给 `template_top_fragments`，默认值同源由
`test_the_template_share_and_the_c4_miner_share_one_floor` 钉住：C4 黑名单拿的是后者的输出，两边的
地板一旦分开，读数就在解释一份和它不同源的提示词。真日志上这一格一格都没动：三局九个发言轮里，
条目数不等于开口人数的只有一轮（`g00000301` 的 day-1 PK，两条记录、一人开口），而那一轮本来就没有
候选（21:11:50Z 现读）——所以 `#105` 今天买到的是同一行里不再有两个分母，不是一个新的读数。
**主判据是 `passivity_rate`**：`act ∈ {listen, align}` 且发言里不含任何座位编号的比例。
理由已在端点上实测过两次：单靠调温度就能拿到 8/8 各不相同的开头，行为却还是死的——相似度指标
会被措辞骗过，被动骗不过。`gate` 字段随输出一起落，只报这一层按轮算得出的那三条，
数字从 `metrics.M3_GATE` 派生——阈值抄第二份的话，迟早有一处先被改，而报告不会为这种分歧变红。

**M3★ 闸门判定（`m3_gate_verdict(games)`）**：计划 §十 那行预注册了五条
（`passivity_rate<0.4` 主判据、`collapse_round<0.35`、`opening_distinct_rate>0.8`、单轮
p95<20s、0 次 context 400），在此之前这些数字只被**打印**过，比大小留给人做。现在批级判定
是一个函数返回值，`compare` 按臂各算一份并落在胜率表下面——一臂没过闸门时，它下面的 bootstrap
区间描述的是一个不值得对比的东西，这句话要在数字之前出现。**单臂批次也印**：`run_batch` 收尾时调
`batch.emit_gate()` 写 `<批次目录>/m3_gate.md`，渲染走的是与 `comparison.md` 那一份同一个 `_m3_md`，
终端摘要那句 `…；M3 闸门判定见 m3_gate.md` 指的是这个文件（`#94`：在此之前 `--configs A` 那种批次跑完，
闸门对谁都没说过话）。**没有 manifest 的一目录日志也判得动**：`wolf gate <dir>` 读盘上已有的日志、按
`meta.config_hash` 认臂，调的还是同一个 `emit_gate`，判定落在被读的那个目录里（`#98`：三次 `wolf run`
攒出来的真桌此前够不着任何判定，而验收第 3 条要的是"全部达标或有明确失败记录"）。反过来，一个文件
**不配**有一份判定：单局的最近秩 p95 就是它最慢的那一次，per-game 的 PASS 离 FAIL 只差一个离群点，
所以 `audit` 那一格停在 `m3_gate_pressure` 的原始计数层。

四件事是这条函数新带来的，都值得追问：

- **分母合并而不是均值取平均**：`passivity_rate` 本身是按条数取的均值，批级就用全部条数；
  按局取平均会让一局 3 条发言的桌和一局 90 条的桌各占一半权重。轮次可以合并着平均，
  但**不能跨局并轮**（`speech_rounds` 是按局调的，按天分组会把两局的第 1 天当成一轮）。
- **没读数记成"没法判"，不是记成通过**：`passivity_rate([])` 的定义值是 `0.0`（一轮总有话，
  所以这个默认值在按轮调用时永远用不到），端上批级判定就等于"一批什么都没跑上的切片拿了个合格"。
  延迟同理，两种形态都要挡：替身 transport 写 0.0 秒，替身**座位**根本不经 transport、
  日志里连 `response` 字段都没有（实测）。端点不可能 0 秒回九张座位。
- **测出来的失败排在缺读数前面**：一批既没有延迟又确实被动时，答案是 FAIL。
  §十二 验收第 3 条要的是"全部达标**或有明确失败记录**"，把坏消息藏进缺数据里两头都不满足。
- **闸门旁边写着"有多少回答是被我们自己剪短的"**：判定字典多一块 `truncation`（`n_calls` /
  `n_recorded` / `n_cut` / `rate` / `worst_phase` / `note`），报告每臂多一行 `- 截断：…`。它**不是**
  第六条判据——`M3_GATE` 那五条是 §十 预注册的，这一格只是把读数放在它所扭曲的那两条措辞判据旁边
  （真桌实测 120/173 被 `max_tokens` 截断、`day_vote` 最重，而被切的那些最长正好停在 `cap-1`：139/140
  与 59/60）。谓词只有一只：`metrics.truncated_call`（`#92`），`m7_cost_profile` 的两格与这块读数都
  从它取，`None`（端点没报 `finish_reason`）与"报了、没被切"永远分得开。

阈值表 `M3_GATE` 是唯一来源（`m5_style` 的 `gate`、判定函数、报告里的"需 < 0.35"三处都读它）。

**M6 belief→action**：**必须分阵营**。好人 `consistency_good = mean(1[target ∈ 自报 suspects[:2]])`
（k=2 预注册），狼 `divergence_wolf = mean(1[target ∉ :2])`——**狼的 divergence 高才是玩得好**，
混成一个指标会得出"狼牌一致性差，得修"的错误结论。旁边永远带着
`stated_vs_engine_agreement`：这个值趋近 1.0 说明模型自报的排序就是引擎算的排序，那么上面两个
数都是假的（`interpret` 字段把这句话直接打进了 JSON）。

**M7 成本**：逐调用读两侧记账——发出去的 `request.max_tokens` / `total_tokens_est`，回来的
`response.latency_s` / `completion_tokens` / `prompt_tokens` / `finish_reason` / `attempts`——按阶段
汇总 p50/p95 + Σ + **兑现率**（`fill_rate` = 同一批调用的 Σ生成 ÷ Σ问价，`asked_calls` 对 `n_calls`
说覆盖面；日志里没记 `max_tokens` 时它报 `null` 并附 `fill_rate_note`，绝不报 0.0，因为 0.0 是一句
关于模型行为的结论），外加漂移自检——用拟合常数按
`fixed + pt/P + ct/D` 预测每次调用，实测/预测 >1.5× 说明端点变了或常数过期了。常数的入口是
`metrics.load_calibration()`，命令行上是 `wolf audit --calibration data/calibration.json`：
体检脚本把 `constants` 块连同 `model` 一起写进 sidecar，读的一侧只认 `CALIBRATION_KEYS`
那三个数（名单只写在 `metrics.py` 一处，`calibrate.py` 从它 import）。

不预测的几种情况各说各的话，且 `drift_note` 原样打印 loader 的 `note`（同一个理由在两处各自
组装，迟早一处说"缺 D"、另一处说"文件不存在"）：文件不存在 / 不是 JSON / **根本没有 constants
块**（仓库里现存的那份 sidecar 就是这一类：一次跑断在半路的体检，对它报"某键为空"是假的）/
某键未测得或不为正 / 常数拟合于**另一个 model** / **端点自己的 `/v1/models` 清单不承认这个 model**
（清单在 `metrics.declared_models()` 一处解析，读侧先看 sidecar 的 `model_declared`、没有这个字段的
旧文件回落到同一次跑留下的 `features`——两处若各读一半，就是"§0 报了、audit 放行"）。规则是三个数一起用、或不一起用：把缺失的
`per_call_fixed_overhead_s` 当 0 会让每一个预测都偏短、每一个比值都偏大，自检于是报出一个假的
suspect。常数齐了而一局里没有可比调用时 verdict 是 `not_evaluable`，不是 `ok`——"没读数"
从来不算通过，M3 闸门同理。不给 `--calibration` 时 `audit` 不碰文件系统：那份 JSON 必须仍然
只是日志的函数，换台机器、换天再跑都是同一个数。

**前缀缓存复用（`prefix_cache`，plan §5）**：整根链条上只有一个字段能回答"共享前缀到底复用了没有"——
端点在 `usage` 里报回的 `cached_tokens`。§5 拿整段区域几何换来的那笔折扣，此前在产物上一个读数都没有：
落盘白名单只放行三个平铺计数，把带这个数的嵌套块整个丢掉，于是 `response["usage"]` 在日志里唯一的读者
是 `tests/test_no_secrets.py`——它证明的是脱敏有效，不是复用率。现在白名单多放行一格（`usage_from()`：
三个平铺计数 + 一个摊平的 `cached_tokens`），audit 把它聚成 `prefix_cache`。

分母规则和上面那个 `fill_rate` 同一条：**同一次调用**的两侧才相除。报了 `cached_tokens` 却没报
`prompt_tokens` 的调用两边都不进，单独数在 `unpairable` 里——把这种调用从分母里悄悄丢掉，比率会因为
端点少写一格而变好，而"少写一格"和"没复用"在成本上是两件事。

- `calls`：有多少次"模型自己做的决定"带着延迟被记下来，是分母的总闸；
- `reported` / `silent`：报过这个字段的与一次没报的。两格之和恒等于 `calls`，所以"端点从这批开始
  回答了没有"是一道减法，不必读代码就能问；
- `reuse_ratio`：Σ`cached_tokens` ÷ Σ`prompt_tokens`，无配对可除时是 `null`；
- `by_phase`：上面三格按阶段各算一遍。§5 的波次模型是"同一波九个座位被问同一份前缀"，而一次 B1
  冲洗只改写它之后的 prompt——哪个波次丢了前缀，看这一格里哪一行掉下来。

`null` 与 `0.0` 在这块里是两个相反的结论：前者是"端点从没回答过"，后者是"回答了，一次都没复用"。
`--mock` 桌写出的 `response` 是空的（`{}`，13:19:26Z 直接读一份 mock 日志确认过形状），所以一份纯替身
日志上这块 `calls` 为 0、比值 `null`——替身桌没有成本账本，这是它本来就该说的话。真读数要等端点开
（13:10:28Z 探过：2 秒内没有 TCP 响应）；在此之前这块的形状由伪造日志钉住：
`test_audit_says_when_the_endpoint_never_answered_about_the_cache` 钉"全没报"那一档必须印 `null`，
`test_a_usage_block_written_into_the_log_moves_the_reuse_ratio` 钉"只补一半"之后 `reported` 与 `silent`
还分得开。

> 复现：`wolf audit <file> | jq '.prefix_cache | {calls, reported, silent, reuse_ratio}'`
> ——`reported` 为 0 而 `reuse_ratio` 为 `null` 是"没问过它"，`reported` 有数而比值为 0.0 才是"复用了个
> 寂寞"，这两句话在这一格里必须长得不一样。

**M8 策略代理**：`seer_death_day`（狼越早杀掉预言家越强）、`witch_save_rate_on_seer`、
`vote_split_entropy`、`wolf_kill_hit_rate`（刀中好人里 suspicion 最高者的比例 = 有没有在做
"刀查杀"这类真策略）。这一组不需要统计力，一局就能看出行为有没有塌，所以它是 M3 切片的即时
判据。分母为 0 时（例如女巫整局没遇到预言家被刀）输出 `null` 而不是 0。

`vote_split_entropy` 有一个它自己不会说的盲区：熵是**归一化之后**算的，所以"9 个人里 6 个投
1 号、3 个弃票"和"只有 3 个投 1 号、6 个弃票"是同一个数（都是 0.0，因为票只落在一个人身上），
满桌同票与满桌弃票也是同一个数。归一化是它的本职不是缺陷，缺的是另一半事实，于是补两个字段：

* **`abstention_rate`**：弃票 / **被问到的票数**（分母是落盘的 `vote` 事件条数，不是投出的条数）。
  它是 M3 主判据在**行动**侧的对应物：`passivity_rate` 只数发言（`listen`/`align` 且未点名），
  一张票都没投出去的桌子在它那里可以完全无声。
  两个分母一起印出来，不留"只有比率"的那一格：`n_ballots_asked` 是落盘的票数、`n_ballots_cast` 是
  其中点了名的票数，比率就是 (asked − cast) / asked。改之前这两个数只活在 `m8_strategy_proxies`
  的函数体里，读到 0.28 的人无从知道它是 7/25 还是 7/250（`#105`、`#107` 同族：分母要能从自己那一行复原）。
* **`ballot_mandate`**（逐轮一串，与 `vote_split_entropy` 同长同序）：`最高票 / 该轮被问到的人数`。
  它回答复盘时真正被问的那句"人是多大比例投出去的"。全员弃票的那轮是 `0.0`（plan §12 R10 的
  平局前兆必须是个数不是空缺），整局没有票时是 `null`。

两串都从 `events.voting_waves()` 来（与复盘矩阵共用同一个切波器，`test_wiring.py` 钉"只有一个
定义、两边都调用"——这一句 2026-09-21 才真的钉上：那条守卫的参数化列表当初只有 4 个名字，
`voting_waves` 不在里面，之前它是散文承诺），所以"同长"是结构性的而不是运气——一条没有选票在前的
`vote_result` 进不了任何一串，否则它就是一串长一格。

2026-09-24 现读 14 份日志（真端点三局 + 20260920 那三份 + 本轮 `wolf batch --configs A --games 8 --mock`
生成的八局），两条分母各自有**独立复算路径**，不是自证：`n_ballots_asked` / `n_ballots_cast` 对
"自己数 `vote` 事件、其中 `target` 非空"是 14/14 一致，而 `n_ballots_cast` 对"把所有 `vote_result`
的 tally 值加起来"也是 14/14 一致。真端点三局的问票/投票是 21/20、25/18、21/21（弃票率 4.76 %、28 %、
0 %），合成八局落在 18.75 %–53.85 %。同一轮还量到：这 14 份里 asked **恰好等于**"有结算的那几波的票数"，
也就没有一份日志留有"投了票却没落 `vote_result` 的尾波"——那一格口径差异（比率的分母数它、逐轮两串不数它）
在今天的真数据上仍无证人，它的牙长在构造的波上
（`test_a_wave_that_never_settled_is_in_the_rate_and_out_of_the_per_wave_lists`），与 `#105`、`#108`
同一形状：新判据在真日志上换不来新读数，它买的是"这一格从此可以由读的人自己复算"。

mock 基线（2026-09-21 本机，`wolf run --mock --seed 11 --games 12`）：**弃票率按局均值 0.381，
每局 0.238–0.643；mandate 均值 0.523，每局 0.354–0.632**；换成池化口径（91/248）是 36.7 %。
这三个数都不等于 `passivity_rate`，也不与它比较——一个数票，一个数话。authored 金样本 21 张票
零弃票、三轮 mandate `[0.6667, 0.5, 0.6667]`：它太干净了，所以这两个字段的可测性只能靠合成波
（`test_entropy_cannot_tell_a_unanimous_table_from_an_abstention_flood`）。这一片 11 具变异体
全部被抓回并逐具记下红名：分母写成投出的票数、空局给 0.0、`cast` 判反、mandate 取最低票 /
分母写成轮数 / 空 tally 给 1/n、只数没结算单的轮，加上切波器的删 `and cur`（那会让 mandate
除以 0）、丢尾波、波内倒序，以及"熵退回自己扫 `vote_result`"。

> 复现：`wolf audit <file> | jq '.m8_strategy | {abstention_rate, n_ballots_asked, n_ballots_cast, ballot_mandate, vote_split_entropy}'`。

## 两配置对比（M7：代码已落地，真数据还没有）

计划 §8 末尾那套协议——配对 `deal_seed`、treatment-axis 守卫（`config_hash` 差异超出 `--axis`
白名单就拒绝比较）、McNemar exact（`b+c<6` 强制打"统计力不足"）、per-utterance 率按局
cluster bootstrap（B=2000，并报告实测 `deff`）、首尾 5 条探针的端点漂移 canary——
现在都在 `batch.py` / `report.py` / `cli.py` 里，口径与拒绝条件写在
**[comparison.md](comparison.md)**。

它比 M3 先落地是**有意打乱了里程碑顺序**：这一层的正确性不依赖端点（"探针不一致就整批作废"
这件事必须在端点下线时也验证得了，否则最需要它的那一刻恰好没测过），而它一旦要用真数据跑，
花钱的是端点不是代码。所以离线能证的部分先证完，剩下"真桌 OK 路径没跑过一次"这件事，
在 comparison.md 末尾的缺项清单里点名留着。

## 现在这些数字能信到什么程度

`data/` 不进版本库，而开发期在它下面的每一局都是**替身桌**（`wolf run --mock`、`wolf batch
--mock`，各 0.3 秒重生成一批，后者被 `compare` 拒绝出结论）。所以上面每一个率目前都在证明三件
事：判定链不崩、日志读回来还是同一份事实、指标函数是纯函数。**它们没有一件事能证明模型玩得好
不好**——那需要 M3 的真实切片。
用 `wolf audit` 看到 `passivity_pooled: 0.1154` 请记得：MockActor 是照剧本说话的，
这个数说明的是剧本不被动，不是模型不被动（19:57:44Z 一局 `--mock` 的现读）。
