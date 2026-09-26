# 逐片取证记录

这份文件是 README〈测试〉一节的下游：每一片（任务号 `#NNN`）落地时把**当时量到的**过程写在这里——
缺口是怎么发现的、刀落在哪一行、电池几具咬住几具活、半径多少条、哪一次归因归错了。

三条规矩，都是被这些记录自己教出来的：

* **数字是历史读数，不是现值。** 每一条都带自己的时间戳，改代码不会回来改这里；想知道今天是多少，
  照 README 那条命令重跑一遍再另起一条。这里点名的 `/tmp/mut_NN.py` 与它们的 `.out` 同理：都是当轮的一次性工件，主机一重启就消失、指不到，留下的账是紧跟其后的那张表。
* **一句假话的代价由读者决定。** 下面有多条记录的内容是"某句散文当时是假的"，包括写它的那一轮自己
  收回去的那条（`#134`）——所以这一份也是 `test_no_secrets.py`、`test_doc_citations.py`、
  `test_doc_tables.py` 三份闸门的扫描对象，写在里面的每个 `file.py:NNN` 都要落在那一行上。
* **它不该长在手册里。** README 是给要用这套引擎的人看的那三十来页；取证是给改它的人看的账本。
  2026-09-26 06:55Z 搬出来的那一刀：README 5678 行里 5000 行是这里的内容，而密钥扫描当时只读
  `docs/`，不读 README。

---

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

但这一句里"抛 `EndpointUnavailable`、收在 `aborted_endpoint`"当时只在**端点答了话**的形状上量过
（401 那一支有真日志）。"连不上、也答不出"那一支要到 2026-09-25 才有第一次真测量，而它当时走的
不是这条路：席位 deadline 先响，一局照打，账见〈端点断了 45 秒一次，但它一次也没说"断"〉（`#116`）。

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
.venv/bin/pytest tests/test_calibrate_rehearsal.py   # 16 条，只碰 loopback，不需要 key
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

- **P1** `src/wolfengine/metrics.py:367` 那份（`read_dir` 里的 `endswith`）：`tests/test_cli.py` 红，具名一条
  （`test_the_batch_loader_ignores_the_prompt_dumps`），另两套绿。有断言。（号码在 10:24:31Z 重指过：
  `#65` 那一族在同文件里插了 29 行，把它顶歪了一格，行号闸门当场报红——这就是那套闸门要防的事。）
- **P2** `src/wolfengine/batch.py:264` 那份（`endswith` 那一支）：**三套测试全部 SURVIVED**。零条断言读过它。
- **P3** 改**写入方**那个 f-string（`src/wolfengine/cli.py:288`，`.prompts.jsonl` →
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
不在终端这一侧；接住它的那一处是 `main()` 里 `except LogDamage` 的那个分支，是这一轮的修法。

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

修的是"在哪个 loop 关"，不是"要不要关"：`cli._run_and_close`（`cli.py:78`）让请求和 `aclose()`
共用同一个 loop，`finally` 留着，两个读者各改一处（`cli.py:191` 的 `run`、`cli.py:569` 的
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
脚本报 `WRONG`。我漏读的是——`cmd_batch` 拿走的不只是一个整数，`cli.py:577` 还要 `res.out_dir`，
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

* **一个地板谓词、两个读者**：`cli._games_error`（`cli.py:95`）说"不足 1 局什么都不产，比较也没有
  分母"，`cmd_run` 的第一行（`cli.py:156`）和 `cmd_batch` 紧挨臂名检查那一处（`cli.py:538`）各自读
  它，走的还是既有的 `配置错误：…` + rc 2 那条腿。关键是**站在 `mkdir` 与落盘之前**——地板如果
  放在 `run_batch` 里面，`run_manifest.json` 就已经在盘上了。
* **普查自己说失败的时候，退出码跟着说失败**：`_print_census` 从"印一句话然后返回 None"改成返回
  这次普查能不能用（`cli.py:311`），`_cmd_dry_run` 是唯一读者（`cli.py:307`）——不能用就 rc 1。

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
另外两件明确不做：不删 `cli.py:329` 的 `max(games, 1)`——它护的是同一个 0，删了等于把
ZeroDivisionError 换回来，有了地板它是冗余但不是错误；不顺手去管 `--seed0` 的取值范围，那是另一个参数。

重跑这一片：

```bash
.venv/bin/python /tmp/mut_batch58.py    # 7 具，约 1.5 分钟，全程离线
.venv/bin/pytest tests/test_cli.py      # 63 条、跑起来 72 个用例（三条参数化），32 秒（从 1 秒涨上来的：README 的三块现在被整块执行，走的是子进程）
```

修后同一个探针（05:58:20Z）：三个头都变成一行 `配置错误：--games 要至少 1 局（收到 0）…`、rc 2，
`ls -d b5 r1 r2` 三个目录一个都不存在——地板站在落盘之前这件事，只有在这儿是可核对的。06:12:31Z
把同一只手上另外三道判据一起探针过（重名臂、配置里没有的字段名、类型不符的 `--set` 值），加上
`run --games 0`：五个都是 rc 2、stderr 一行、stdout 空、指定的输出目录不存在。

这一片自己也被文档闸门抓过一次：那句重跑注释先按收集数写的"四十五条"，而闸门核的是文件里的定义数。
按 `<模块名> N 条 = def 数` 的既有写法改口才对上——这正是 `#44` 补那道闸门时立下的用途。（同一格在
`#59`/`#60` 之后重数过一次，`#63`/`#67`（C2 的刀与刀的读数）又给这个文件加了两条、`#68`
（指派的读者）再加一条、`#70`（前缀缓存的读者）再加两条、`run --set` 这一轮再加五条（下面〈把前缀
长度拧到两档〉那节）：
现在 `tests/test_cli.py` 63 条、跑起来 72 个用例，含最下面 `#121` 那三条）

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

* **一个谓词、两个读者**：`cli._seat_error`（`cli.py:105`）说"越界的座位号是命令写错了"，`cmd_replay`
  （`cli.py:440`）和 `cmd_watch`（`cli.py:462`）各自读它，走的还是既有的 `配置错误：…` + rc 2 那条腿。
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
次序以前**没有读者**（`cli.py:254` 那句 `if as_seat is not None:` 排在 `god` 那一支前面，把两支换序不会有任何用例红），而
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
一起改：它连号带主张两处都错，真号在 `events.py:410`，而 `main()` 里那个 `except LogDamage` 分支是第二处。

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
绿，也就是说这两处**不是被闸拦下来的，是被"我去核了一下"发现的**。同一段里还点着 `events.py:410` 那句 `except LogDamage`，那
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

三处与上一版报告不同的账，按「变化」和「缺席」记，不按「更强的覆盖」记：

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
   （`batch.py:480`），手改过的那一份、以及**旧版本跑出来的那一批**都不经过门口。这一处是补文档
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
很散（`metrics.template_top_fragments` → `persona.repeat_fragments` → `src/wolfengine/phases.py:211`
→ `src/wolfengine/assemble.py:186`），而 10:15:27Z 实测 `grep -rn "repeat_fragments|禁止复述" tests/*.py`
是**空的**：整条链一个断言都没有，拆掉任何一环，742 条照样绿。

链上真正的缺陷不在"接线在不在"，在**回灌的是什么**。旧实现枚举的是 6 字滑窗，而紧跟其后那段
"丢掉被包含的较短片段"从来没生效过——所有候选都正好 `min_len` 长，长度相同谁也包含不了谁。实测
（10:22:33Z，修前）三句话共享"我先听听大家的发言，"这十个字、后半句各不相同，回到 C4 的是
`我先听听大家；先听听大家的；听听大家的发；听大家的发言`：四个滑窗把四个名额全花光，第二个模板一个字
都没进提示词。`docs/metrics.md` 那句"专盯『我先听听大家的发言』这类 ≥6 字重复片段"在修前是假的。

修法是 `_grow`（`src/wolfengine/metrics.py:179`）：每个达标的窗口左右各长，长到"所有出现位置的邻居
不再一致"为止，撞到文本边缘就停。增长不会把门槛放低——延长后的片段出现在**恰好**同样的那几份发言里
——所以 `min_count` 的语义一点没动。

新增 `tests/test_anti_repeat.py`（现 12 条），钉四件事：门槛（`min_count=3` ⇒ 回灌最早在第 4 个座位
生效）、抓到的是整句而不是窗口、那一行自己不能把 C 区吃掉（最多 4 条 × 每条 24 字，`regions.c_task`
那一格在这个函数里唯一的读者），以及整条链接上了没有（一桌各自答同一句开场白，第 4 个座位起的提示词
里必须有那句"禁止复述"；只有两个座位听得见的狼话不得出现在给全桌的黑名单里）。
`tests/test_golden_game.py:511` 那条只钉了 `template_top_fragments` 返回空集合那一侧。

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
**占全体比例**）"，实现给的是 `frags[0][1]`——被说的**份数**，一行都没有除以分母。两半都是问题，
而第二半更严重：10:48:41Z 实测 `grep -rn "template_top1" src/ tests/ scripts/` 只命中
产出它的那两行，**全仓库没有一个读者、测试里零断言**。也就是说这个数从 M5 长出来之后，从来没有一
条断言说过它等于几，改它没人拦，读它的人只能猜。

按"名字与值必须一致"修，不按"补一句文档说明它是份数"修：M5 是按轮算再取均值，均值底下混着 `n=9`
和 `n=2` 两种轮，五份里三份重复（0.6）和三份里三份重复（1.0，全桌塌完）在份数上都是 3——不可比的
数取均值，取出来的东西没法解释。字段更名 `template_top1_freq` → `template_top1_share`，值除以本轮
份数，没有模板的轮给 `0.0` 而不是 `None`（`None` 会被 `mean()` 悄悄吞掉，"没测到"和"测出来干净"
就分不开了）。这条链的旧读数不进版本库（`data/` 除外，而那里没有真端点的数据），所以不留兼容键。

那一格今天的算术在 `src/wolfengine/metrics.py:159` 的 `template_top_share` 里（`#105` 搬的：分母从
本轮条目数换成开口人数，并且给"话筒不足三面"的轮发 `None`）。所以上面那句"没有模板的轮给 `0.0`
而不是 `None`"从今天起只对**量过**的轮成立：三个人以上开口、确实没有共享片段 → `0.0`；两份发言的轮
压根不可能有 ≥3 的候选 → `None`。这一条改动是 `#95`/`#103`/`#104` 那条规矩的第四次兑现，见本页末尾。

判据住在 `tests/test_m3_gate.py`（现 52 条）新增的一条里，那里有全仓库唯一的 `_round(...)` 轮构造器；
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
字节上量，名单与顺序只有一处：`src/wolfengine/metrics.py:1195` 的 `REGION_CAP_KEYS`——批次表头那 12 格
由它派生，不是有人另数了一遍（`test_the_budget_table_header_names_every_ruler_the_rows_carry`）。
`B` 故意不在名单里：B1+B2 量的是同一批字节的两种问法，再记一遍等于给同一件东西发第二份权威。

**九根尺子里只有一把后面有刀。** 触发条件从"整段超 `c_total`"变成"整段超 **或** 主张卡那一块超
`c_belief`"（`src/wolfengine/assemble.py:233`），缩卡那一轮也拿 `c_belief` 判装没装下：两把尺拉同一个
杠杆，所以只有一把超的时候也不多砍。另外四格改的是"越界局数"里的红字，不动发出去的字节，这条分工
就写在 `src/wolfengine/config.py:77` 那段注释里（那里点名只有 `c_belief` 后面真有一刀）——人格卡砍薄是 §7 的 P1/P3 防线静默失效，任务块砍掉
是 act 闸门失效，私有信息块是夜里那几句话在整份 prompt 里唯一的副本。三局 mock 的出厂峰值
71/57/328/111/115（11:12:16Z）：四把警报尺一声没响，最紧的 C2 也还在 450 以下。响得上的那一种在
合成夹具里——20 条新主张的预言家草稿卡单块 459 tok，而整段 C 才 672，离 1450 差一倍多，所以
`#63` 之前那把尺子对这一格是瞎的（`test_the_c2_ruler_bites_on_its_own_while_the_total_stays_shut`
压的就是这一格，放宽 `c_belief` 到 9999 的对照是 `test_a_c_belief_loose_enough_to_hold_the_card_hands_it_over_whole`）。

`#67` 补另一半：装配器知道自己砍了几刀，日志里只留着"砍完还剩多长"。`region_tokens.C2` 是砍**以后**
的长度，单看它分不出"这张卡本来 11 条"和"本来 14 条被削掉 3 条"——后者才是模型收到的处理差异，而预算
宽紧本来就是轴的一部分，两臂的 C 长度允许不同。这一条链走通了五站：`Prompt.card_claims_dropped`
（`src/wolfengine/assemble.py:57`）→ 落盘白名单（`src/wolfengine/assemble.py:318`）→
`metrics.region_budget_check` 那两格 `card_*`（`src/wolfengine/metrics.py:1200`）→ 臂级聚合
（`src/wolfengine/batch.py:452`）→ 表尾"削过主张卡的 prompt：A 臂 0 个、B 臂 109 个（最狠的一条少发
14 条指控）"。两格 `card_*` 故意算在 `meta.regions` 那道守卫**之前**：一把没量过的尺子不该顺手抹掉
两格不需要尺子的读数（`test_a_log_without_the_caps_in_meta_prints_null_not_zero`）。判据落点：
`tests/test_wiring.py`（现 63 条、跑起来 84 个用例）里那六条，和
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
`assignment_compliance`（`metrics.py:604`）读的就是 `e.request`，而 `cli.py:355` 把它挂在
`speech_acts` 旁边：两格并排，一格是行为，一格是行为对指派的符合度，谁也不替代谁。

"0 与缺席"在这一格上要逐字段重判，不能继承别处的结论：`recorded`（这个键在不在记录里）与 `turns`
（值不是 null）分开印，两格比率在没有指派轮时是 `null` 而不是 0.0。老日志读出的 `recorded` 为 0 说的是
"没人量过这件事"，新日志读出的 58 与 21 说的是"量过了，这一局有 21 个指派轮"——把前者印成后者，报告就
会把"升级之前跑的批"说成"那批桌没有指派需求"。

12:28:35Z 那份 audit 读数里两格比率都是 1.0，`by_assigned` 与 `speech_acts` 逐键相同。这不是闸门通过，
是 mock 桌由构造就听指派（`actors.py:236` 直接取 `legal.assigned_act`，真读数要等端点）。分辨力另有钉：
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

**"真数照旧等 M0"这一句到 2026-09-24T16:47Z 只对一半了。** 三局真桌的日志逐行扫过（173 次带回答的
调用）：每一次的 `usage` 都只有 `prompt_tokens`、`completion_tokens`、`total_tokens` 三个键，整份文件里
`cached_tokens` 出现 **0 次**——不是报了 0，是压根没有这个键。于是读侧那条 `null` 第一次有了真证人：
`reuse_ratio: null` 现在说的是"这批真调用里没一次报过这件事"（`src/wolfengine/cli.py:397` 那句话），
而不是"还没跑过真桌"。**剩下那半句仍要等 M0**：为什么不报（端点不支持 / 要显式开关 / 版本没带这个字段）
只有体检里那一格能答，而它恰好住在这份报告承认被自家 `redact` 遮掉的 7 格里
（`features.apc_visibility.has_cached_tokens`）。遮它的守卫已经收窄（`scripts/calibrate.py:53` 的
`CRED_KEYS` 里不再有裸 `token`），所以重跑一次 M0 就能把这格读回来——但那是"为什么"，不是"有没有"。

后果得说全：plan §5 的前缀缓存经济性**目前没有可证的读法**。这不是"还没量"，是量它的那根线不通。
要么让端点把这个字段报出来，要么另找证据（同一份 prefix 的字节跨调用相同，只能证明"该被复用"，不能证明
"被复用了"）——那是新的一票（`#96`），不在这里替它下结论。


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

改文档一处：〈预算表里五行没有数的尺子…〉（`#63` + `#67`）那一节里 `tests/test_wiring.py` 的活账顶了一次数（原来是五十条、
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
`summary` 是模型读到的那一句话（`compress.py:88` 那一句：存过 `summary` 就照抄，没有才现拼），同一局的第 97 行（3号的 PK 发言，
`phase=day_pk_speech`）的 `request` 字段里，`[e94]` 与 `[e95]` 就一字不差地挨着躺在 prompt 中，那天后面
三次投票（seq 98/99/100）和 3号的遗言（seq 103）带着同一对进上下文。**发现时**全仓库没有一条断言钉过这个
字符串（10:40Z grep `无人出局`，只有 src 命中）。三份留存日志里只有这一局的 `vote_result` 说过这句话，
且只有一处。

根因是**一个谓词两处写**：这个条件原本只写在 `phases.run_vote` 的那一支 `and` 上
（`res.tied and res.pk_seats and house.tie_break == "pk_once_then_nobody"`），而句子（当天在 `phases.py`
里拼，`#113` 起搬进 `compress._vote_summary`）根本没读房规，于是分支和文案各说各话。收成一条 `rules.will_pk(state, res)`
（`src/wolfengine/rules.py:293`），两边同问它：分支在 `phases.py:229`，句子经 `_publish` 的
`pending_pk` 旗标（`phases.py:229/275`）随结算记录一起落盘，如今由 `compress.py:151` 的 `_vote_summary`
读它决定说哪一句。
**旗标而不是在渲染侧重算**：
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
不该存在的值找翻译），而是让它回到 `null`：写侧 `src/wolfengine/game.py:230`（`winner=winner` 那一行，上面 229 是 `log.append(Kind.GAME_OVER,`），`compress.py:97` 补
`camp is None` 那一支，`render_live.py:222` 那格改成 `or '无'`。"哪些终局算获胜"这件事仓库里**早就有唯一
来源**——`metrics.DECISIVE`（`src/wolfengine/metrics.py:253`，batch 的分母就是拿它筛的），它 key 在
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
`tests/test_house_wired.py:61` 从 `"平票" in summary` 加强成整句 `endswith("平票，无人出局。")`——那张桌上
这句话就是**结论**，没有下一行来收回它。（`#113` 之后这句人话不再躺在事件里，所以那一行改成从
`compress.render_line` 拿句子，并在它上面补了一条 `pending_pk is False`：钉的还是同一句结论。）

八具手术刀（`/tmp/mut87.py`，八具的预期写在它自己的 docstring 里、先落盘再跑；基线 11:09:17Z
`rc=0 red=[]`，同一套 11:01:45Z 跑过一次，逐字相同）。`mutlib.check()` 只报 CAUGHT/SURVIVED，这一轮的
账要具名断言，所以每具都留红名单：

| 具 | 改动 | 红了谁 |
|---|---|---|
| K1 | `phases.py` 的 304 行（当天）里那句 `elif pending_pk:` → `elif False:` | CAUGHT ①（句子不再读 PK）。`#113` 起写句子的手搬进了 `compress.py`，同一支刀现在砍那里，证人还是 ① |
| K2 | 第二波那次 `_publish(t, second)`（`phases.py:238`）也传 `pending_pk=True` | CAUGHT ②（终局平票改口成"先不定人"） |
| K3 | `phases.py:229` 第一波不传旗标 | CAUGHT ① |
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
5号交出解药（`"potion": "save"`，可见域只有 `[5]`；`#114` 之后的日志不再有这一格，那一瓶由 `act` 说），seq 17 验人，seq 18 直接"天亮了，第1天开始。"，中间
既没有 `death` 也没有一句"昨晚平安夜"。那瓶药花掉了，而这在九个座位的公开视图里等于没发生，"昨晚是平安夜"
只活在没人读的布尔值里。补一句公告要**新增事件**→ seq 整体后移 → 金样本重钉（
`tests/test_golden_game.py:310` 的 `_key(events) == GOLDEN_KEYS`、`:455` 的 `"平票，2、3号进入PK" in
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

同一句假出处写了三遍，加上文档两遍：`batch.py:529` 那句印 `actor_kinds` 的拒绝语、`metrics.py:488`
的 note、`metrics.py:762` 的 note，以及 `docs/comparison.md` 的拒绝表、`docs/metrics.md` 的分母规则。这与 `#87`
的"一个谓词两处写"同形，所以修法也同类——一张表 `metrics.SYNTHETIC_CLAUSE`（两个键，mock 指 §十一、
human 指 §十五）加一只 `metrics.synthetic_basis`，三个出口都从它取：`batch.py:530` 的
`metrics.synthetic_basis(synth)`、`metrics.py:489` 的 `synthetic_basis(synthetic)`、
`metrics.py:763` 的 `synthetic_basis(played)` 那一处。

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

第二处同源，而且更贵：`_counts` 的 `refused` 读的也是被过滤后的列表，而 `metrics.py:248` 的
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

#### 六成九的真回答撞在自家 `max_tokens` 上，而闸门那一屏看不见这件事：`#92`

端点恢复之后跑掉的三局真桌（`data/real-20260924/`，`actor_kinds=['llm']`，`config_hash` 三局同一个
`fb3e99579b7e`，两局在第 3 天、第三局在第 4 天的夜里判 `wolf_win`——那局狼人刀掉最后一名好人时还没走到
白天，所以它全天只有 2 次调用）从 `m7_cost_profile` 身上读到的第一格是
`truncations=120` 对上 `n_calls=173`：**120/173 = 69.4%** 的回答是被我们自己的预算切掉的。逐相位
（同一只函数的 `by_phase`，各格相加 120、与顶层那一格对得上；16:34:11Z）：

    day_vote 62/67 · day_speech 30/58 · night_wolf 12/18 · last_words 5/8
    night_witch 5/8 · night_seer 4/10 · hunter_shot 2/2 · day_pk_speech 0/2

不是"差一点撞上"：被切的那些回答里最长的正好停在 `cap-1`——`day_speech` 与 `last_words` 是 139，
其余相位是 59，而价目表只有两个数（`max_tokens_speech=140`、`max_tokens_action=60`，取价的那只手在
`src/wolfengine/config.py:196`）。全局兑现率 `fill_rate=0.9123`（Σ 生成 14433 ÷ Σ 问价 15820，173 次调用
全部报了 `max_tokens`，覆盖面满格；16:34:29Z）。逐局：33/57、41/58、46/58。

缺陷不在算术，在**位置**。这一格以前只有 `wolf audit` 的 `m7_cost_profile` 读得到，而它扭曲的那两条判据
住在另一屏：M3★ 对这三局给的是 `collapse_round=0.0191`（n=9 轮）、`passivity_rate=0.0333`（n=60 次）
——两个数都读作"没塌缩"。可被剪掉后半句的发言在 `collapse_round` 眼里像模板、在 `passivity_rate` 眼里像
"没点任何人的名"，而**读闸门的人没有一个字告诉他句子是半截的**。看不见天花板的人分不出"塌缩的桌子"和
"被剪的桌子"，这正是 `#87` 那一族的形状：数字全对，句子少了它自己需要的那半句。

修法是**一个谓词、三个读者**：`src/wolfengine/metrics.py:1235` 的 `truncated_call`（`None` 表示端点压根
没报 `finish_reason`，与"报了、没被切"是两件事），`m7_cost_profile` 的两格照旧走它（数值一字未变），
`m3_gate_verdict` 在 `criteria` 旁边多带一块 `truncation`，`_m3_md` 于是给每一臂加一行
`  - 截断：…`。**没有**加第六条判据：`M3_GATE` 那五条是 plan §十 预注册的，新读数是**报告**不是**阈值**——
塞进同一张表等于事后改判据。也没有另开一节：判据和它适用的对象隔一屏，早晚有一屏会把它丢掉。

真数据上印出来的是这一句（16:29:51Z，三局一起进 `m3_gate_verdict`）：

    120/173 次回答被 `max_tokens` 截断（120/173 = 69.4%），最多的是 `day_vote`：
    上面的风格判据量的是截断后的文本，不是模型想说完的那句。

四条判据在 `tests/test_m3_gate.py`（`#92` 那一段，先写失败：三条红在 `KeyError: 'truncation'`、一条红在
闸门文件里少那一行）。它们各钉一格：`n_recorded` 与 `n_calls` 分得开（9 条被切断的发言 + 3 次投票，
投票里 1 次报 `length`、2 次报 `stop` → `(12, 12, 10)`，率的分母是"报了这件事的"）；一次都没报的批次说
"读不出来（不是 0）"；`worst_phase` 取被切最多的那个相位；一刀没切过的批次说"无一被截断"，而后半句要在
**盘上的** `m3_gate.md` 里读到（`clean` 与 `cut` 两个批次目录各一份）。

上面那张逐相位表和 120/173 在 16:50Z 又用一份**不 import 引擎**的逐行统计复算过（直接读 `jsonl` 的
`response.finish_reason`，按行上的 `phase` 分组）：173 次带回答的调用、120 次 `length`、
`day_vote 62/67`、`day_speech 30/58`、`night_wolf 12/18`、`night_seer 4/10`、`night_witch 5/8`、
`last_words 5/8`、`hunter_shot 2/2`、`day_pk_speech 0/2`——八行全部对上，`p95=12.23` 与
`context_overflows=0` 也是同一次复算读出来的。

这一票的手术刀四具（M1–M4，与 `#93`/`#94`/`#95` 共 14 具一起跑，16:51Z，基线三跑全绿）。M1、M2、M4
第一轮就红，且都只在摘掉新用例之后才绿（`仅新用例抓得住`）。**M3 第一轮活了下来**：把
`worst_phase` 的 `max` 换成 `min`，`tests/test_m3_gate.py` 24 条全绿、整套也绿。它不是"没人读的字段"——
那条用例里明写 `cut["worst_phase"] == "day_speech"`——是**等价变异**：夹具里那三次投票没带
`finish_reason`，被 `truncated_call` 判成 `None` 而进不了 `per_phase`，那个字典只剩一个键，max 和 min
是同一个值。给投票补上 `"length"`/`"stop"`（就是上面那句 `(12, 12, 10)`）之后，同一把刀红在指名它的
那一条上，还原 `cmp` 字节相同、再跑绿。**夹具的强度是一个要量的量，不是写完就成立的性质**——一条断言
里出现字段名，不等于那个字段的算法有证人。

限界三条。① 69.4% 是三局、九个发言轮量出来的，**不是** §十 预注册的那片 M3★ 切片（两种前缀长度各 5 次），
所以它是"这一格从此有了读数"的证据，不是"M3 过了"的证据。② 抬 `max_tokens` 不是免费的：同一批真桌
`latency_p95_s=12.23` 对着 `< 20.0` 只剩 7.8 秒余量，所以那次改动必须**量**出来（跑 1–2 局真桌再读
`truncation` 与 `latency_p95_s`），不是拍一个更大的数——`#92` 报的那半格（读数进报告）已经闭环，抬到哪里
这半格交给 `#112`（下面 ⑤ 说明为什么它不能从这批日志外推）。③ 逐相位的细节只在
`audit` 的 `m7_cost_profile` 里，闸门那一行只报 `worst_phase`：两边共用同一只谓词，但一份产物一行。

补两条今天量出来的东西，都是给"下一次该怎么量"用的。④ **天花板比 69.4% 更挤**：把 173 次真调用按
`token_budget_for` 分成两档看生成长度（23:54:11Z 现读），发言档 68 次里 35 次、动作档 105 次里 85 次的
`completion_tokens` **全部正好停在 cap-1**（139 与 59），也就是发言档的中位数就是撞顶的那个值；而未切的那
些里也各有 2 次停在同一个 139 / 59 上、只是 `finish_reason` 写的是 `stop`——贴着边界的回答用计数分不开，
只有端点那一句 `length` 能分，所以 69.4% 是端点自己的口径，不是我们复算出来的一个更严的数。
⑤ **抬多少不能从这 173 次外推**：拿 `latency_s ~ prompt_tokens + completion_tokens` 做最小二乘（同分钟现读），
整体拟合给出负的 decode 斜率，分档拟合给出发言档 0.016 s/token、动作档 0.36 s/token 且该档 prefill 系数为负、
中位残差 2.6 秒——这不是"算出了代价"，是这批日志里两个解释变量根本不同步变化（每次调用几乎都打到顶），
延迟主要由端点侧的排队与批量决定。所以那一格的下一步是一次**专门的探针**（同一份提示词、只换
`max_tokens_speech` 与 `max_tokens_action` 各跑几次，读 `truncation.rate` 与 `latency_p95_s`），
任务记在 `#112`，端口开着才能做。预算那一侧倒是**不用端点就有读数**：同一张桌、同一 seed 的 `--dry-run`
普查（零 API 调用；出厂档 23:57:17Z、抬档 23:56:53Z）每局完成预算从 6480 tok 变 8820 tok，仍然低于每局硬顶
`max_game_completion_tokens` 那道 20000——所以卡着这一格的是延迟，不是预算，探针只需要盯 `latency_p95_s`。

#### 预算天花板挂在 `play()` 的一个本地变量上，而两个出货入口都不走那条构造路径：`#93`

`plan §6` 的全局预算是 `max_game_completion_tokens`（出厂 20000，`src/wolfengine/config.py:119`），执行它的
那一句读的是 `spent()`。修前 `spent()` 读的是 `play()` 自己的本地 `llm`，而那只 `LLM` 只在
`actors is None`（引擎自造九座）时才存在——`cli._llm_actors` 与 `batch` **都是**把 `actors` 交进来的，
于是真桌上 `llm` 恒为 `None`：文件里记着几千个生成 token，`spent()` 答 0，天花板是一个永远不会响的警报。

拿同一只桩、同一份 `actors` 交付路径、上限拧到 100，两版引擎各跑一局（16:33:04Z，`/tmp/probe93.py`，
旧版是 `git archive HEAD` 出来的整包副本）：

    pre-fix  : res.completion_tokens=0    terminal=good_win       winner=good | 文件里 Σ=2240
    shipped  : res.completion_tokens=920  terminal=aborted_budget winner=None | 文件里 Σ=920

修前那一局**打完了**（还判了好赢），比修后多烧 1320 个 token 的预算——上限 100 对它没说过一个字。
三局真桌的 Σ 生成是 4889（`…153245Z_g00000300`）/ 4483（`…153509Z_g00000301`）/ 5061（`…153715Z_g00000302`）
（16:30:26Z 逐局从 `timed_decisions` 相加，16:50Z 又逐行加过一遍 `response.completion_tokens`，两遍同值；
局号各自标出，因为按文件顺序读它们是**递减再增**的，不是一列升序），离 20000 还差四倍，
所以这三局本来也撞不上；要紧的是**"它撞不上"这件事从来不是判据给的**，是计数没接上给的。

修法是把计数从**真正在答题的那只 `LLM`** 身上取：`src/wolfengine/game.py:166` 的 `counters` 按 `id` 去重
（九座共用一只 `LLM`，不去重就是把同一份账记九遍），`src/wolfengine/game.py:179` 的 `spent()` 求和。
〔预算耗尽〕那条公告里的数字跟着变成真数（`src/wolfengine/game.py:200`），汇总行的 `completion=` 也是
（`src/wolfengine/game.py:239`）。墙钟那一半不受影响，它本来就不读 `llm`。

两条用例在 `tests/test_live_path.py`（`#93` 那一段）：一条钉"汇总行印的数 == 文件里 Σ 的数"（先断言
`recorded > 0`，否则这条断言是空的），一条钉"上限 100 在出货路径上真的把桌子停下、`winner` 是 `None`、
公告里带着那个真数"。两条都走 `cli._llm_actors`——**这就是它们照见这一格的原因**，老用例走的是
`actors is None` 那半边。

这一票的手术刀两具：M12 把 `counters` 退回 `play()` 的本地 `llm`（就是原缺陷本身），M13 把公告里的
数字写死成上限。两具都第一轮红，红的集合正是上面点名的那两条（一具不多、一具不少），摘掉新用例后回到
绿（`仅新用例抓得住`），还原 `cmp` 字节相同。

限界：替身座位不经 transport，mock 批次的 `rows[].completion_tokens` 就是 0（16:31:20Z 那批实测），所以
这一节只谈 LLM 路径；上限本身该设多少仍归 `#41`/`#43` 那两套读数管，不是这里。

#### M3★ 的判定只有两臂对比时才印得出来，单臂批次跑完是沉默的：`#94`

`m3_gate_verdict` 是 `#15` 落地的，发射口当时只有一处：`compare()`。而 `compare` 进门就要**两个配置臂**——
`wolf batch --configs A` 那种"先跑一臂看看桌子塌不塌"的批次，跑完落一堆日志加一份 `run_manifest.json`，
**没有一个字**说闸门判了什么。§十 M3★ 问的恰恰是"这一臂的桌子塌不塌缩"：一个臂就是一次判定，两臂对比是
下一次。`stats["m3_verdict"]`（`src/wolfengine/batch.py:540`）住在 `compare` 的产物里，单臂批够不着它。

修法是**同一个渲染器多一个调用点**，不是第二份渲染：发射口挂在 `run_batch` 收尾（`src/wolfengine/batch.py:229`
的 `emit_gate`），写 `<批次目录>/m3_gate.md`；`_m3_md` 那份渲染逻辑原封不动被两处共用，所以 `comparison.md`
里的那一节和这个文件是同一段字节。判据仍然只从 `m3_gate_verdict` 取，闸门表仍然只有 `M3_GATE` 一张。
终端那句跟着改了（`src/wolfengine/cli.py:578` 尾部那半句印 `m3_gate.md`）：

    批次 -> /tmp/gate94（1 局日志，seed0=21，canary SKIPPED，终态 ok）；M3 闸门判定见 m3_gate.md

盘上那份（16:31:20Z，一臂一局的替身批；中间五条判据行以 `…` 略去，余下逐字）：

    # M3 闸门判定（批次 `gate94`）

    - 判定对象：A 1 局（1 份日志）；阈值表 `metrics.M3_GATE` 单一来源，与 `comparison.md` 里那一节同一段渲染。

    ## M3 闸门（各臂预注册判据，plan §十 M3★）

    - A｜NOT_EVALUABLE
      - passivity_rate = —（需 < 0.4，n=0）无读数
      …
      - 本批 1 局全为替身桌（actor_kinds=['mock']），依据 plan §十一（mock 只会自证，这几项不许省）：
        不入评测语料，闸门不给判定，不是给通过。
      - 截断：本臂的可用局里没有一次带回答的调用，这一格读不出来（不是 0）。

三条用例在 `tests/test_m3_gate.py`（`#94` 那一段）：第一条钉文件真的落在批次目录里，第二条钉 `--mock` 批
**不许**印 PASS（它是 `NOT_EVALUABLE`，理由那句说的是"全为替身桌"而不是"没数据"），第三条钉终端那句话
指得到这个文件。

这一票的手术刀四具：M9 单臂批次不写文件、M10 闸门那一屏丢掉`截断`行、M11 丢掉`依据`行、M14 摘要行不再
指那个文件。第一轮全部红，`仅新用例抓得住`，还原字节相同。**M9 的预注册证人有一半没兑现，如实记**：期待
它顶红三条，实测红的是"单臂留判定文件""替身批不给 PASS"两条**加上 `#92` 的 `m3_gate.md` 读取那条**
（它从盘上读，文件消失它先炸），而点名它的 `test_the_batch_summary_line_points_at_the_gate` **没红**——
那条只钉终端那句话里有没有文件名，不查盘上的文件在不在。M14 才是它的证人。四具里没有一具是白造的，
但"M9 该由谁来抓"这件事我预先写错了一格。

限界：`emit_gate` 只在批次收尾时写一次，中途 `BatchAborted`（金丝没跑过、目录建到一半）不补一份**半成品**
闸门——`drift.md` 那一路同理。

#### 一轮全桌沉默，旧的两把尺子一个报"彻底塌缩"、一个当场炸：`#95`

`collapse_round` 与 `opening_distinct_rate` 都是"这一轮说了什么"的尺，可它们从没被问过"**这一轮有人说话吗**"。
拿 HEAD 那一版（`git archive` 副本）与现在这一版各量一遍同样的五种输入（16:30:54Z）：

| 这一轮的发言 | `collapse_round` 修前 → 修后 | `opening_distinct_rate` 修前 → 修后 |
|---|---|---|
| 9 条全空 | `1.0` → `None` | **`ZeroDivisionError`** → `None` |
| 2 条全空 | `1.0` → `None` | **`ZeroDivisionError`** → `None` |
| 1 条空 | `0.0` → `None` | **`ZeroDivisionError`** → `None` |
| 这一轮没发生（`[]`） | `0.0` → `0.0` | `1.0` → `None` |
| 8 空 + 1 条真话 | `0.7778` → `0.0` | `1.0` → `1.0` |

两半都是谎，方向还相反。空话两两之间的 4-gram 集合交并比是 `1.0`（并集为空时 `jaccard` 返回 1.0），所以
一桌沉默在 `collapse_round<0.35` 上是一张**假 FAIL**；`opening_distinct_rate` 的分母写的是"非空发言数"，
全空时是 0，于是 `wolf audit` 走到 `m5_style_collapse` 就抛 `ZeroDivisionError`——一个**读侧崩溃**，触发
条件是"某一轮的文本全空"（三局真桌里这种轮一次都没有：空 `content` 的答复 0 条，所以它是可达但没被走到
的路径，定性仍压在 `#40`）。`[]` 那一行是另一个方向：这一轮压根没发生，旧尺答 `1.0` = "所有开头各不
相同"，在 `>0.8` 上是一张**免检通行证**。

修法是"没量过就不要冒充量过"：两把尺子全空时给 `None`（`src/wolfengine/metrics.py:73` 与
`src/wolfengine/metrics.py:91`），`m3_gate_verdict` 的两条风格判据把没有读数的轮**从池子里拿出来**而不是
投一票（`src/wolfengine/metrics.py:727` 的 `n_collapsed`，于是 `n` 只数有读数的轮），`m5_style_collapse` 的均值（走 `round_mean`）与
`worst_round` 同一条规则（`src/wolfengine/metrics.py:974` 的 `round_mean` 与 `src/wolfengine/metrics.py:1016` 的 `worst_round`）。一臂全是
沉默轮 → `ok=None` → `NOT_EVALUABLE`，走的是空分母那条**已经存在**的规则，没有新增阈值。"沉默"这件事由
`passivity_rate` 说：它是主判据，量的本来就是"没点名的听客比例"，不是措辞。

`8 空 + 1 条真话` 那一行留个限界：修后 `collapse_round=0.0` 是"< 2 条可比发言"的**定义值**（沿用旧约定，
不是"测出来零塌缩"），这一轮的真相在 `passivity_rate` 那一格里。两条判据在 `tests/test_m3_gate.py`
（`#95` 那一段：一条钉"全空的轮不给 opening 读数"，一条钉"闸门其余四格照旧量得出，只有这一格是 `None`"）。

这一票的手术刀四具（M5 全空轮把 collapse 报成 `0.0`、M6 把 opening 报成 `1.0`、M7 闸门池子不摘 `None`、
M8 `m5` 的均值不摘 `None`）第一轮全部红，`仅新用例抓得住`，还原字节相同。要记的一笔：**三具（M5、M7、M8）
的证人是同一条用例**——`test_a_silent_round_leaves_the_gate_without_a_reading_but_keeps_the_measured_ones`
是闸门口那一格，三种手术都改它的读数，所以它天然一夫三职；反过来说，摘掉那一条（同一次摘除跑里就是这么做的）
三具同时活。这不是四具各有各的钉子。

数一遍：**837 passed**（17:02:22Z 起、65.89s 收尾那一跑，读的就是上面这四节写进来之后的 README 与
`docs/`，含被 `#93`/`#94`/`#95` 那三节改过的 `docs/metrics.md`、`docs/comparison.md` 和重渲染的
`docs/calibration.md`）。这一批的账：M3 那一页（`tests/test_m3_gate.py`）从 20 涨到 24、`tests/test_live_path.py` 从
24 涨到 26 条。

还有一笔要记的：**这一节里有一处改动是被闸门顶回来的**。我先把"遮掉的那 7 格下次重跑能读回来"两句
手写进了 `docs/calibration.md`，`tests/test_calibrate_guard.py::test_the_committed_report_is_still_what_the_code_renders`
当场红——那份报告是 `scripts/calibrate.py` 的**输出**，不是一篇可以手写的散文（这条测试本身就是为一次
真实分叉事故写的）。改动因此搬进了渲染器（`blind` 那一支），再用 `--from-json` 离线重渲染落盘，零 API
调用。顺带把渲染串里两处接缝丢掉的空格修了。**一个文件长得像文档不代表它是文档**：判据是"谁写它"。

#### M3 判定读不到"独立 run 出来的日志"，三局真数据因此答不了验收第 3 条：`#98`

`compare` 进门就要 `run_manifest.json`，`batch` 要重新打牌才有臂。于是三次 `wolf run` 攒出来的一个
目录（2026-09-24 那三局真桌正是这个形状）在链条上**任何地方都拿不到闸门判定**，而 §十四 验收第 3
条要的是"全部达标**或有明确失败记录**"。缺的从来不是算术，是第三个读取点。

先否掉的接法是"让 `wolf audit` 顺手印一块 verdict"。`batch.py` 里那段写着的理由成立：闸门是
**关于一批**的主张，单局的最近秩 p95 就是它最慢的那一次，一个 per-game 的 PASS 离 FAIL 只差一个
离群点。`audit` 停在 `m3_gate_pressure` 那一格的原始计数层是对的，不该替一批人背书。

落地的是 `wolf gate <dir>`（`cmd_gate`）：读一个目录里**已有**的日志，按 `meta.config_hash` 认臂，
走同一份 `emit_gate` + `_m3_md` 渲染，判定落在被读的那个目录里。阈值仍然只有 `M3_GATE` 一张，
算术仍然只有 `m3_gate_verdict` 一处，退出码跟着读数走（0 只在 PASS，与 `compare` 同一条约定）。
两张配置表混在一个目录里它**拒判**：rc 2、点名两个 hash、一份半成品都不落——并成一个分母就说不清
塌的是哪张桌子，那是 `compare` 的轴守卫在门口拦的同一件事。

四条用例住在 `tests/test_m3_gate.py` 的 `#98` 那一节（先写失败：四条一起红在
`invalid choice: 'gate'`）：`test_loose_logs_get_a_gate_without_a_batch_manifest` 钉"读得到"并且
退出码跟着读数走，`test_a_gate_over_loose_stand_in_logs_refuses_to_say_pass` 是反面对照（替身桌挑平
了也还是不给判定，且 rc 非 0），`test_the_loose_gate_prints_the_same_numbers_as_the_batch_gate` 逐字
比对五格数字与 `m3_gate_verdict` 的输出（新命令最容易犯的错是抄一份更顺手的渲染），
`test_a_folder_of_two_config_tables_gets_no_gate` 钉那句拒绝。第五条是给闸门自己补的牙：
`tests/test_doc_citations.py::test_the_scanner_covers_every_subcommand_the_parser_offers`——
`SUBCOMMANDS` 那张名单落后于 `build_parser()` 时，新命令在文档里的参数是扫不到的，而"扫不到"不会
自己报警（这条是在加 `gate` 的那一秒先红给我看的）。这一轮收口那一格的账：`tests/test_m3_gate.py` 那一页
从 24 涨到 28，整套 847 个用例——本轮之后的现读，见下面 `#99` 那一节。

真端点那三局现在第一次有了判定（18:07:15Z 现跑现读：`wolf gate data/real-20260924`。那个目录是
gitignored 的本机留存，克隆仓库的人要复敲得先有自己的真日志；余下逐字，五条判据行照盘上原样）：

    # M3 闸门判定（日志目录 `real-20260924`）

    - 判定对象：cfg=fb3e99579b7e 3 局（3 份日志）；阈值表 `metrics.M3_GATE` 单一来源，与 `comparison.md` 里那一节同一段渲染。

    - cfg=fb3e99579b7e｜PASS
      - passivity_rate = 0.0333（需 < 0.4，n=60）达标
      - collapse_round = 0.0191（需 < 0.35，n=9）达标
      - opening_distinct_rate = 1.0（需 > 0.8，n=9）达标
      - latency_p95_s = 12.23（需 < 20.0，n=173）达标
      - context_overflows = 0（需 <= 0，n=173）达标
      - 截断：120/173 次回答被 `max_tokens` 截断（120/173 = 69.4%），最多的是 `day_vote`

两处限界跟着这段读数一起写，不然它会被引用成"M3★ 通过"。① 分母是 3 局 / 9 个发言轮 / 60 次发言 /
173 次带回答的调用，而 §十 预注册的切片是"两种前缀长度各 5 次"——这一格既不够 5 局，也没有第二档
前缀（~5k 那一档按上面的外推根本不在当前几何里，见下一节）。② 同一份文件最后一行写着 69.4% 的回答
被自家 `max_tokens` 切掉，也就是 `collapse_round` 与 `opening_distinct_rate` 量的是截断后的文本，不是
模型想说完的那句。所以这一格读得出来的只有："闸门第一次在真数据上印出来，五条判据**都有读数**且都
达标，没有一个分母是空的"——这正是 §十四 第 3 条要的那句"有记录"，而它不是"风险已排除"。

#### 把前缀长度拧到两档：入口是真的，~5k 那一档不在当前几何里

plan §十 的 M3★ 切片写着"在两种前缀长度（~2k 与 ~5k）各跑 5 次"，而这份 README 在此之前一直写着
"缺的是一个能把前缀长度拧到 ~2k / ~5k 的入口"。**那句话是假的**，2026-09-24 量过之后搬掉了：入口
就是 `run` 的一根旋钮，零 API 调用，从新克隆的仓库就能复敲。

> `wolf run --dry-run --seed 300 --out /tmp/pfx1500`（出厂表）
> `wolf run --dry-run --seed 300 --set regions.b2=400 --out /tmp/pfx400`（把 B 段的软预算掐紧）
> `wolf run --dry-run --seed 300 --set regions.b2=1000000 --out /tmp/pfx_nocap`（当作没有折叠）

三格的读数（同一局 mock 桌的 56 次装配，`*.prompts.jsonl` 里逐条数出来的）：A 段始终 945 tok，
它是模板、没有旋钮；B 段峰值 946 → 449（掐到 400 那格，449 而不是 400 是日子地板
`min_window=4` 在拦，见 [docs/metrics.md](docs/metrics.md) 那段"宁可超软预算"）；抬到一百万那一格
与出厂那一格**逐字节相同**（`cmp` 过），因为 mock 桌的编年史峰值本来就够不着 1500——折叠没触发时
这根旋钮是空转的。这条链由五条用例钉住：`test_the_budget_tool_can_vary_the_budget_table`（拧得动、
meta 里落的是拧过的那张表、两次 `config_hash` 不同）、
`test_the_run_override_meets_the_same_refusals_as_the_batch`（inert 格子与 `actor_kinds` 在 `run`
门口同样拒，名单不另抄一份）、`test_two_spellings_of_one_knob_do_not_silently_pick_a_winner`
（`--max-days` 与 `--set max_days=` 同时给则停，不由字典写入顺序裁决），加上"值读不进来"的两条：
`test_a_run_set_pair_that_cannot_be_read_stops_with_a_hint`（五种敲错的拼法各留一句人话、rc 2、
不打完桌子）和 `test_the_batch_path_refuses_an_unreadable_value_with_the_same_hint`（同一句
"值读不出来"在 `cli.py` 的两个解析器里各抄了一遍，而 `batch` 那一处此前零读者——`#97`）。

这五条的牙口是一具一具变异量出来的（`/tmp/mut_runset.py`，2026-09-24T17:56Z，9 座，全程离线）。
**两半基线各自先绿才开工**：快的那半（五条）与整套那半各跑一次"什么都不改"，任一不绿就不开电池——
上一轮我只给快的那半量了基线，整套里两条一直红着的文档计数闸门（我加了用例没同步改上面那三个数）
就被印成了两具变异体的 "CAUGHT(仅整套)"。补上这道守卫之后它当场又拦了一次。9 座里 7 座红在点名的
用例上：M1 换表算了不用、M2 两个拼法的冲突、M3 inert 名单、M5 没有这一格、M6 少 `=` 的那句提示、
M7a 与 M7b（同一句话的两处，各红在自己那一侧——`#97` 就是这样现形的：第 5 条用例写出来之前，
M7a 那一处改成 traceback 也没人红）。剩下 2 座只有整套抓得住，且证人都在 `batch` 侧：
M4（`_set_path` 的身份/处理那道门）红在 `test_a_derived_field_is_not_a_treatment_axis` 与
`test_an_unimplemented_knob_is_refused_for_the_right_reason`，说明 `run` 门口那条只验到
`apply_overrides` 的名单、没验到再往下一层；M8（`--max-days` 不再转交）红在
`test_run_exposes_the_cap_on_the_command_line` 等三条。每座改完按 sha256 整文件还原并核对，
收尾复跑五条与整套都绿（那一跑 842 个用例；`#98` 之后是 847，见下面那一节的账）。

真端点那三局的形状就只能离线读了（2026-09-24T17:09Z 现查：TCP 通、不带 key 打 `/v1/models` 得 401、
`WOLF_LLM_API_KEY` 仍未 export——鉴权那层活着，缺的是凭证，不是代码；17:48Z 再查是同一个 401，
没有翻面也没有新变量可取）：

| 读数 | 值 | 从哪读的 |
|---|---|---|
| 带回答的调用数 | 173（57/58/58） | 三份日志各自的 `request` 行 |
| A 段 | 945，三局同一个数 | `request.region_tokens.A` |
| B 段 | 65 → 1506（1506 出在一次**没**折叠的调用上，正是它把折叠顶起来的那一格） | 同上，`request.region_tokens.B` |
| 端点自己数的 prompt | min 1232 / p50 2456 / max 3354 | `response.prompt_tokens`，173 次全在 |
| 折叠发生过的调用 | 65 / 173，这一族里 B 最大 1483，`b2_over_cap` 173 次全 0 | `request.compactions`、`request.b2_over_cap` |

把这三局的公开编年史**不折叠**地逐字装配（离线，`chrono_bytes(pub, (), len(pub))`），三局分别是
61 / 61 / 63 条公开事件、2381 / 2134 / 2503 tok，加 A 段就是 A+B 天花板 3326 / 3079 / 3448——这就是
当前几何下"整局编年史全量上桌"最贵的样子。于是 ~2k 那一档根本不用拧：出厂的表就在真桌上量出
p50 2456；而 **~5k 那一档不是旋钮能到的地方**。按实测每条公开事件 ≈35–40 tok（上表三局各自的
条数与总 tok）外推，要凑到 5k 前缀需要约 102–116 条公开事件，这三局打完（3/3/4 天）只有 61–63 条。
外推就是外推：它假设发言长度不变，而 #92 那格说明真模型的发言长度还没定下来。

所以 `#4` 剩的不是缺键，是一个口径决定：要么把预注册切片改成"更长的一局"（`--max-days` 抬到 6–7 天，
每次调用同时多付约 1.5k tok），要么把 ~5k 这一档改写成"当前几何的天花板 ~3.4k"。这个决定归
人类伙伴，不在离线加固这一批里替它拍。

#### 一份 0 字节的文件被闸门数成一局，还被认成"又一桌替身"：`#99`

`#98` 把 `wolf gate` 立起来的头一天，它就印了假话。形状有两种"里面没有一局"：**连页眉都没有**
（0 字节，`write_meta()` 没跑成）和**只有页眉**（开局记录落了盘，第一条事实没落）。这两句话在仓库里
早就有唯一出处——`events.py` 的 `meta_notice` 与 `empty_notice`，互斥，replay / export / watch 三个
出口都在用。闸门这一侧一个读者都没有：它拿的是 `len(games)`，于是字节数也进了"局"这个字。

18:40:24Z 拿一份真目录量了四种文件（两局真端点日志，加上 `head -1` 造出的只剩页眉的一份、`: >` 造出的
0 字节一份），两道门各自说什么：

| 文件 | `is_synthetic` | `seat_kinds([g])` | 事件数 | 旧的分臂守卫看见的配置表 |
|---|---|---|---|---|
| 两局真日志 | False | `['llm']` | 88 / 90 | `fb3e99579b7e` |
| 只剩页眉（抄真日志的页眉） | **False** | `['llm']` | 0 | `fb3e99579b7e` |
| 0 字节 | **True** | `[]` | 0 | `None` ← 被当成第二张表 |

四句假话都从这一格里出来：

1. 标题"本批 3 局"，而里面只有两局打过牌（18:20Z 那份更早的读数里，它连"全为替身桌"都说了出来）。
2. 0 字节那份被并进"替身桌"那一堆。`is_synthetic` 是一道**关门**（没登记 `actor_kinds` 就不许进结论），
   反过来当成"登记成了替身"就是替一份 0 字节的文件编了一张座位表——`#89` 刚为同一类假出处收过口。
3. `cmd_gate` 的分臂守卫取 `meta.config_hash` 的集合，0 字节贡献一个 `None`，于是两局好日志被拒成
   "混着 2 张配置表（None、fb3e…）"、rc 2、一份判定都不落：**一个坏字节让整目录读起来像数据不可比**，
   那是 `#51` 那一族。
4. 修完前三条、18:40:38Z 再量，又冒出一格新的：`n_games_usable=3` 而 `n_games=2`、
   `n_synthetic_excluded=-1`。算术是 `len(played) - len(usable)`，而 `usable` 还从 `games` 里选——
   两个不同底的集合相减，减出来的不是任何数量；这一格若被谁拿去印"剔了几局"，读者看到的是负数。

修法是让"这份文件里没有一局"成为闸门也读的一句判据：`Game.hollow_notice`（`src/wolfengine/metrics.py:336`）
把 `meta_notice(...) or empty_notice(...)` 原样请过来（一个字都不重写，出处仍然只有一处），
`m3_gate_verdict` 先分 `hollow` / `played`，`usable` 再从 `played` 里选；读数分成 `n_games`（局）与
`n_files`（份）两个键，不等时标题里两个数同时印，另起一行点名少掉的那几份各是什么形状。`cmd_gate`
那边只数**登记了** hash 的文件，没登记的那一份不再是一张表，回到「不是局的文件」那一行。现在的逐字
读数（18:44:57Z，`wolf gate /tmp/hollow99`，rc 0；那两份坏文件用上面表里的两条命令就能在任意日志目录
里造出来，真日志目录本身 gitignored）：

    - 判定对象：cfg=fb3e99579b7e 2 局（4 份日志）；阈值表 `metrics.M3_GATE` 单一来源，与 `comparison.md` 里那一节同一段渲染。
    - cfg=fb3e99579b7e｜PASS
      - passivity_rate = 0.0513（需 < 0.4，n=39）达标
      - 不是局的文件：2 份（这一局只有开局记录：后面一条事件都没有；这个文件没有开局记录（manifest 那一行）：说不出它是哪一局）——它们没有事件，上面五条判据的 n 里没有它们一笔。

五条判据的 n（39 / 6 / 6 / 115 / 115）全部来自那两局真的——多出来的两份文件挪不动任何一个分母，
这正是第一条用例钉的东西。八条用例在 `tests/test_m3_gate.py` 的 `#99` 那一节（现 52 条、整套 874 个
用例，22:35:16Z 与 22:39:25Z 两次现读同为 874，跑法见下面 `#108` 那一节）：

- `test_a_file_without_a_manifest_is_not_counted_as_a_game`：份数涨了，`criteria` 那五格一个字都不许变。
- `test_a_log_that_only_opened_is_not_a_second_stand_in_table`：两个 `*_notice` 互斥，"本批 N 局全为
  替身桌"里的 N 只数真的那一桌替身。
- `test_a_folder_of_such_files_says_there_is_no_game_rather_than_a_stand_in_table`：全是坏文件时说的
  是"没有一局"，不是"这些桌子都是替身"。
- `test_a_hollow_llm_log_does_not_join_the_usable_pool`：第四条假话，那个 `-1`。
- `test_a_log_without_a_config_table_is_not_a_second_config_table` 与
  `test_two_real_config_tables_still_refuse_even_with_a_tableless_file_there` 在 CLI 门口成对照：
  前者放好日志过去，后者仍然拒两张真表——两边都在，"忽略无表文件"才不是一句把守卫改宽的借口。
- `test_the_headline_counts_the_files_it_judged_not_the_files_it_was_handed`：给最后一格补的读者，
  见下。
- `test_a_log_with_events_and_no_manifest_line_is_still_a_game`：判据自己的边界——页眉被删掉、事件
  还在的文件不是"没有一局"。这一格是量 `#100` 时现出来的，见下一节。

M4 那一具变异顺手抓出了第二个缺陷：`n_files` 是个**没有读者的字段**——把标题里的份数改成任何数，
整套都不红，因为 `emit_gate` 自己数了一遍 `len(rows)`。这一族的规矩是"只在散文里活着的主张就是缺陷"
（`#20` 同形），所以接线到 `sum(v["n_files"])`：同一句话只有一处算术，而标题里那两个数说的是同一批
文件。牙口是 9 具变异、18:52:17Z 全程离线（`tests/test_m3_gate.py` 那一页当时从 34 涨到 35，基线先绿才开工，每座
按 sha256 还原并核对）：M1 判据恒空、M2 `usable` 回到从 `games` 里选、M3 极性对调、M4 份数塌成局数、
M5 无表文件算一张表、M6 分臂守卫整个不拦、M7 那一行不印、M8 局数改用份数、M9 接线退回 `len(rows)`
——9/9 CAUGHT，全部红在点名那一页。M9 在接线**之前**跑过一次，那时它 MISSED（整套 853 全绿），
这才有上面那条用例：把一份行的臂名改成不在名单里的，两处数法才被掰开（`A 2 局（3 份日志）` 是接线前
的读数）。

两处限界跟着这一格写。① 当时同一条判据只接到了闸门这一侧：`m1_win_rate` 的 `n_games` 数的还是文件，
同一个目录它报 4（`n_decisive` 走 `DECISIVE` 筛过的分母，所以胜率本身没错，错的只是"本批 N 局"那一格，
而 `batch.py:658` 那张臂级表头的"局"列拿的就是它）。这一句"还没有读者"由下面 `#100` 收掉，那一节
量的是它在胜率层漏出来的两句新假话。② 本轮往 `metrics.py` 里插了两段，把 README 点它的
行号引用顶偏 26 处（第一次 17、修完第四句假话后 9），逐条 grep 到目标语句才敢改号。这一族的成本就是这样：
名字有闸门兜底，行号只能靠改的时候手上有 grep。

#### 胜率层把两份没有局的文件数成了五局：`#100`

`#99` 那条限界①不是"还没接"那么轻：同一条判据接进了闸门，胜率层仍然从传进来的列表开头选，于是那
两份没有局的字节在那里照样是局。拿真端点的三份日志（`data/real-20260924/`，gitignored）加上 `#99`
表里那两条命令造出的坏字节（只剩页眉的一份、0 字节一份）放进同一目录，19:19:08Z 量两种代码各说什么
（末列是同一批真日志去掉坏字节的对照）：

| 读数 | 胜率层没有这条判据 | 修完之后 | 只剩三份真日志 |
|---|---|---|---|
| `n_games` | 5 | 3 | 3 |
| `n_files` | 5 | 5 | 3 |
| `n_synthetic_excluded` | 1 | 0 | 0 |
| `excluded` | `{"unfinished": 1}` | `{}` | `{}` |
| `hollow.n` | 0 | 2 | 0 |
| `good_win_rate` / `wilson95` / `mean_days` | 0.0 / [0.0, 0.561] / 3.33 | 同左 | 同左 |

最后一行是这个缺陷的形状：**胜率那三个数从头到尾没错**（`is_synthetic` 与 `DECISIVE` 那两道筛本来
就把它们挡在区间外面），错的只是"本批 N 局"这一格和拿它渲染的臂级表头。中间两行才是新东西，它们是
`#99` 那四句假话在胜率层的孪生：一份 0 字节的文件被算成"剔了一桌替身"（替没人登记的字节编了一张
座位表），一份只有页眉的被算成"打完了没分出胜负"（把"后面没人写下事实"说成模型的行为）。

修法与 `#99` 同形：`m1_win_rate` 先分 `hollow` / `played`（`src/wolfengine/metrics.py:449`），
`synthetic` / `usable` / `decisive` 三条链全部从 `played` 出发，`n_games` 只数有局的份、`n_files` 数
交进来的份。"不是局的文件"那一格抽成一次算术（`_hollow_files`，`src/wolfengine/metrics.py:507`）：
共享的是谓词（几份、每份什么形状），结论那半句仍各出口自带——胜率点名它的分子分母，闸门点名五条
判据的 n。整句共享会把 `#99` 里那条 18:44:57Z 的逐字读数改掉，而散文里钉过的历史不该为了少写一个
参数就重钉。渲染侧 `src/wolfengine/batch.py:644` 的 `_m1_md` 只在两个数不等时并排印 `2（3 份）`，
相等时括号是噪声，两个数都不许渲染器自己数第三遍。同一目录修完后的逐字读数（19:19:08Z）：

    hollow.note: 不是局的文件：2 份（这一局只有开局记录：后面一条事件都没有；这个文件没有开局记录（manifest 那一行）：说不出它是哪一局）——它们没有事件，上面胜率的分子与分母里没有它们一笔。
    gate: PASS n_games 3 n_files 5 hollow 2

量这一格的时候顺手抓出第二个缺陷，这次在判据自己身上：`hollow_notice` 拿的是
`meta_notice(...) or empty_notice(...)`，而 `meta_notice` 说的是**叫不出这是哪一局**，不是**这里没有
一局**。19:03:35Z 现测：一份页眉被删、9 条发言还在的日志，在 `#99` 之后的闸门里报 n_games=0、
NOT_EVALUABLE——那一局真打了，事件就在字节里；页眉缺失从 `#52` 起有自己的读者和自己那句话。改成
`events` 先判（`src/wolfengine/metrics.py:351`）。这一条先有用例再有代码，正反两向都在：有事件、没
页眉＝一局且五条判据的 n 照旧；把同样的事件删干净＝零局。

四条新用例：

- `test_m1_counts_a_file_with_no_game_in_the_files_not_in_the_games`：3 份里有 1 局，`n_files` 是 3、
  `hollow.n` 是 2，而胜率、区间、平均天数、角色存活、分层与 `excluded`、`n_synthetic_excluded`
  逐字段等于只喂那一局时的值——多加两个坏字节，一格数字都不许动。
- `test_m1_over_a_folder_of_such_files_says_there_is_no_game`：整目录都是坏文件时说的是"没有一局"，
  胜率给 `None`、区间给满格，既不写"全为替身桌"也不沿用那句"仅作描述"（后者假设有一批局被采样了）。
- `test_the_m1_table_shows_the_files_that_held_no_game`：那一格有读者了——臂级表里 A 印 `2（3 份）`、
  B 印 `2`，另起的一行带着 `meta_notice` 的原句。它不绕 `compare`，因为往一臂多塞一份文件会先撞上
  配对守卫（`#49` 那一族）。
- `tests/test_synthetic_attribution.py` 里走 `m1_win_rate` 的那五格，夹具从"只有页眉的文件"换成"真的
  替身桌"（`played=True`，`#99` 加的开关）。断言一个字没松：它们测的是那道门，而门现在只管有局的
  那几份。

牙口是 11 具变异、19:20:40–19:23:52Z 全程离线（证人四页：金样本、配对批次、替身归因、闸门；基线先绿
才开工，每具按 sha256 还原并核对）：M1 判据退回 `#99` 那一版、M2 胜率层不拆（本体）、M3 `usable`
回到从 `games` 里选、M4 `n_games` 数文件、M5 `n_files` 塌成局数（`#99` 那格死字段的探针）、
M6「没有一局」那一支恒不触发、M7 表格恒带括号、M8 表格不印那一行、M9 共享措辞里丢掉"每份是什么
形状"、M10 胜率层那一格恒空——10/10 CAUGHT，全部红在点名页里，"这一具被哪条用例抓住"是可指的。
牙口开的是那四页而不是整套，所以"其余全绿"不由这次电池担保；M11 是唯一另跑了整套的一具，而它正是
这次唯一的 MISSED，预注册在先：把闸门那一格结论的尾巴（"……上面五条判据的 n 里没有它们一笔"）删掉，
858 个用例仍全绿。渲染器把 `hollow` 那一格的原句搬进 `comparison.md`（`batch.py:691`），但没人断言
那半句——它和 `#99` 的 M4 同族（那里是"发出去的字段没有读者"，这里是"印出来的句子没有断言"，同一
件事的两个方向：改它没人红），差别是这次明知代价才选：共享谓词、不共享整句。要收掉它就得把那整句
钉进用例，而 `#99` 那条历史引文就会从读数变成断言的俘虏。

另一处限界跟着写：这一格修的是读侧聚合，字节层没有新东西——造成那两份文件的仍是半截落盘（`#46`、
`#53` 各自已有名字），读侧的义务是把半截说成半截，这一条尽到了；写侧的落盘顺序不在本轮决定里。

#### 「不是局的文件：2 份」说得出份数，说不出是哪两份：`#101`

`#100` 收尾时那一格里还剩最后一格没接上：`_hollow_files` 除了份数和形状，还算了一份
`paths`（每份文件的 basename），而**两个出口都只印 `note`**。19:34:39Z 拿这条探了一遍：把
`paths` 改成恒空列表，整套 858 条一个字都不红——那正是 `#99` 的 M4 抓过的形状（发出去的字段没有
读者）。这一族在本仓库有现成的口径，`docs/comparison.md` 的退化局那一节早就写了"**只报计数不算
点名**：拿着 3/40 开不了那一局的转录"；份数同理，拿着"2 份"删不掉那一份字节，而它留在目录里会
继续污染下一批的局数。

修法是给那一行补一个读者，不新写句子：`_hollow_line`（`src/wolfengine/batch.py:634`）把指标的
`note` 和 `paths` 拼成出口那一行，闸门与胜率两处共用它；指标里那句 `note` 一个字不动，`#99` 与
`#100` 各自钉着的两条逐字读数因此仍然是对当时输出的真实复述。真日志目录 + 两份坏字节的现读
（19:41:20Z，`wolf gate /tmp/hollow101`，rc 0，行尾就是新加的那半句）：

      - 不是局的文件：2 份（这一局只有开局记录：后面一条事件都没有；这个文件没有开局记录（manifest 那一行）：说不出它是哪一局）——它们没有事件，上面五条判据的 n 里没有它们一笔。（文件：20260924T888888Z_zero-byte.jsonl、20260924T999999Z_opened-only.jsonl）

牙口两条：新用例 `test_the_gate_names_which_files_held_no_game`（走 `cli.main(["gate", …])`，
从落盘的 `m3_gate.md` 里读那一行），和给既有的
`test_the_m1_table_shows_the_files_that_held_no_game` 补的一格（胜率那张表）。断言写的是**整段**
`（文件：interrupted.jsonl）`，不是"包含文件名"：第一版用包含写法时，把 `paths` 换成全路径的变异
M2 会绿——路径里当然也含那个名字，而一整条路径会把那一屏挤掉，这正是 basename 的理由。
6 具变异、19:38:50–19:41:10Z 全程离线（证人还是那四页，基线先绿，每具按 sha256 还原并核对）：
M1 点名整个不加、M2 印全路径、M3 只胜率出口点名、M4 只闸门出口点名、M5 `paths` 恒空（就是
19:34:39Z 那具 MISSED，现在红了）、M6 名字由渲染器自己造一遍（第二处算术）——6/6 CAUGHT，
M3/M4 各自只红在对应那一个出口的用例上，说明两处读者是真的两处。

一处限界跟着写：那半句没有上限。`compare` 的 `HASH_MISMATCH` 那一族印前 5 个（`bad[:5]`），这里
一份没截：坏文件上几十份时那一行会很长。先长着的理由是这一族的方向反了——"这一批里有 40 份字节
不是局"本身就是要响的那件事，把它折成"……等 40 份"等于把规模重新藏回数字里。真要截，得同时说清
去哪里看全表，那是下一个决定。

#### 一个主判据、两个分母：`#102`

`#101` 那条限界（"分母"这一族还有别的洞）在收尾的现读里就露出来了。闸门那一侧的轮次来自
`src/wolfengine/metrics.py:694`，单局视图那一侧来自 `m5_style_collapse` 的第一行——**同一个
`speech_rounds`、同一份事件**，可对主判据给出两个数：`wolf gate` 按次合并（被动次数 ÷ 全部次数），
`wolf audit` 的头条 `passivity_mean` 是"每轮速率再取均值"。19:49:01Z 在
`data/real-20260924/20260924T153509Z_g00000301.jsonl` 上现读，那一局三个发言轮规模 9/2/7、逐轮速率
0.1111、0.5、0.0：

      按轮取均值   (0.1111 + 0.5 + 0.0) / 3 = 0.2037   ← 改动前 `wolf audit` 的头条
      按次合并     2 / 18                  = 0.1111   ← 闸门一直读的，也是现在两边的数

（0.2037 是拿现在的逐轮读数复算出来的，与 19:49:01Z 改动前 audit 直接印出来的那格一致；0.1111 是
20:08:50Z 两个出口各自的现读。）1.83 倍，同一局、同一判据、隔一个命令行。

构造桌把它顶到阈值上（19:51:04Z）：一轮九个人、五个人被动，加一轮只有一个人开口，共十个 turn，
被动五次。按轮均值是 **0.2778**（看着达标），按次合并是 **0.5**（闸门判 FAIL），而阈值是
`< 0.4`——两个数正好一边一个判法。这不是"两个视图各说各话"可以放过去的理由，因为
`m5_style_collapse` 的返回值里就带着 `gate` 那一格（把闸门阈值原样搬过来给人比大小）：拿着一个
不同分母的数去比那个阈值，就是把一桌沉默读成达标。

修法是让一个算术只有一处，不是把闸门拉低。抽出三把尺子：`round_readings`（每轮一把，两个出口共用）、
`round_mean`（按轮均值）、`pooled_passivity`（按次合并，连同分母一起返回）。`m3_gate_verdict` 里那三行
自己写的 list-comprehension 全部删掉，`m5_style_collapse` 的头条改名 `passivity_pooled`——把分母写进
名字里，因为 `#102` 之后仓库里同时存在两种聚合口径，靠名字区分比靠记忆可靠。另外两条判据**口径没动**
（两处本来就相等，动它是改预注册判据的语义，不是修 bug），但它们也一并走 `round_readings`/`round_mean`：
以前闸门平均的是**未取整**的值、单局视图平均的是取整到 4 位的值，同一批轮在两边的均值能差 0.0001，
正好够在 `< 0.35` 那一格改判——取整次序也是"两个分母"的一种。

      - passivity_rate = 0.1111（需 < 0.4，n=18）达标     ← 20:08:50Z，`wolf gate` 只放那一局真日志

牙口两条：`test_the_passivity_headline_pools_turns_instead_of_averaging_rounds` 钉住 0.5 并写明
"每轮均值会把这一格读成 0.2778"；`test_the_audit_view_and_the_gate_report_the_same_primary_criterion`
把两个出口的三条判据逐个对齐（后两条今天相等，放进这一条是钉子：谁再把它们拆开就该红）。金样本
`test_golden_game` 那格跟着重钉 `1/9/3` → `1/17`（那一局三轮 9/6/2 共 17 个 turn、一次被动），
`docs/metrics.md` 的替身桌读数跟着重测 `0.1175` → `0.1154`（同一批 mock 字节，两个口径）。

6 具变异、20:00:10–20:07:20Z 全程离线（基线先绿：861 passed，每具按 sha256 还原并核对）：
M1 `pooled_passivity` 退回每轮均值（CAUGHT，行为侧证人两条）、M2 闸门再自己算一遍（CAUGHT，唯一
证人是对齐那一条，正是它该红的地方）、M3 `round_mean` 把没有读数的轮当成 0（CAUGHT，`#95` 那条
沉默轮用例）、M4 判据的 `n` 数全部轮而不是有读数的轮、M5 `dup_exact6_mean` 取错列（预注册 MISSED，
兑现）、M6 每轮的 `passivity_rate` 恒 0（**预注册 MISSED，实际 CAUGHT**——被本轮新加的那条按轮断言
抓住了，预注册失效，如实记在这里）。

M4 那一具名义上 CAUGHT，实际不算：它唯一的红是
`test_a_line_number_written_in_the_docs_still_points_at_the_thing_named_beside_it`，也就是 `#77`
点过名的那类假证人（它比的是行号两侧的 token，不看 `n` 是多少）。读 `n` 的断言有两条，但两条都不
带沉默轮——`criteria["collapse_round"]["n"] == 4` 那条的夹具四轮全有读数，`len(per)` 和它有读数的
轮数本来就是同一个数；有沉默轮的那条只断言 `value is None`，没数分母。所以"M4 被抓住了"这句不能
当作这一格的担保。

一处限界跟着写，并且它就是 `#103`：闸门两条风格判据的**分母**没有行为读者（上段），`dup_exact6_mean`
是审计输出里的一格而全仓库没有一条断言读过它（20:08:45Z 现读 `wolf audit` 的 `m5_style` 键：
`collapse_round_mean`、`dup_exact6_mean`、`opening_distinct_mean`、`passivity_pooled` 四格，而
`docs/metrics.md` 的 M5 那节写的是"两条措辞尺子取均值"，第三把尺子从来没在文档里出现过名字）。
另一处：主判据换成按次合并之后，`#102` 之前所有印在文档与提交里的 `passivity_mean` 读数都是按轮
口径的历史值，本仓库不重述它们，也不拿新数去覆盖那些带时间戳的复述。

#### 一轮只有一张话筒时，两条尺子发的是通行证：`#103`

`#102` 那具只红了行号闸门的 M4，值得回头查清它凭什么没红在行为上。读 `n` 的断言其实有两条：
`criteria["collapse_round"]["n"] == 4` 那一条的夹具四轮全有读数，`len(per)` 与"有读数的轮数"当时是
同一个数；有沉默轮的那一条只断言 `value is None`，没数分母。顺着这条线把 `collapse_round` 的分支读
一遍，撞见了比 `n` 更大的东西——`if len(spoken) < 2: return 0.0`。

一轮里只有一个人开口时，两条措辞尺子各交回一个**通过方向**的定义值：`collapse_round` 给 `0.0`
（`< 0.35` 达标）、`opening_distinct_rate` 给 `1.0`（`> 0.8` 达标），而 plan §8 给前者的定义是"同轮内
**两两** char-4-gram Jaccard 的均值"——一个人没有可比的一对。`#95` 立的规矩（"沉默不是塌缩，也不是
一切各不相同"）当时只落在"整轮全空"那一档，`[]` 还留在旧的 `0.0` 上，于是两兄弟对"没有可比对象"
这件事给两个答案（`collapse_round([])` 是 `0.0`、`opening_distinct_rate([])` 是 `None`）。

真日志不是假想敌。20:12:07Z 现读 `data/real-20260924/…g00000301`：day-1 那个 PK 轮有两条发言记录，
其中一条是空串，所以开口人数是 1。

      轮（day/phase）        开口人数   collapse_round        opening_distinct_rate
      1 / day_speech            9       0.0127                     1.0
      1 / day_pk_speech         1       None（改前 0.0）     None（改前 1.0）
      2 / day_speech            7       0.0140                     1.0
      闸门那一格                        0.0089 → 0.0134        n=3 → n=2

`0.0089` 是 `(0.0127 + 0.0 + 0.0140) / 3`，`0.0134` 是 `(0.0127 + 0.0140) / 2`：那 34% 的"更不塌缩"
是一次没有可比对象的投票换来的。构造桌把它顶到整一半（20:13:16Z）：九人轮加一个"一人开口、一人空手"
的轮，闸门 `collapse_round` 读 `0.0216`（n=2），而九人轮自己那格是 `0.0433`。

修法是两条尺子的地板一起抬到"两个开口的"，`[]` 收进同一条规则（不再有"这一轮没发生"和"这一轮一个人
说话"两个口径），批级 `n` 只数有读数的轮。主判据一个字不动：空手那一条也是一个 turn，`passivity_rate`
量的本来就是行为，一个都不该少算——新用例里 `n` 那一格因此钉的是 11，不是 10。

连带一处：`scripts/calibrate.py` 的温度扫描以前写的是 `round(collapse_round(speeches), 3)`，某个温度下
若干次采样只成 1 次时，`None` 会直接被 `round()` 接住而 `TypeError`——工具在真端点上花完钱之后才炸，
正是这一族该防的位置。现在 `None` 原样进表，那一格是空格，旁边一格就是 `n_speech`，读表的人自己看得
见为什么没数。渲染侧不用改：`render_md` 的 `table()` 本来就把 `None` 画成空格，这是仓库里"没有读数"
的既有画法。

牙口两条，RED 是干净失败而不是报错（20:13:55Z）：`assert 0.0 is None` 和
`assert 2 == 1`——前者是定义值冒充测量，后者是分母收留了没有读数的轮。

6 具变异、20:19–20:26Z 全程离线（基线先绿：863 passed，每具跑完按 sha256 还原并核对）：M1 把
`collapse_round` 退回 `#95` 的口径（只挡全空那一档）、M2 对 `opening_distinct_rate` 做同一件事、
M3 把闸门的 collapse 分母数成全部轮、M4 对 opening 分母做同一件事、M5 让 `collapse_round` 数条目
而不是数开口的人（"一人开口 + 一人空手"会被当成可比的一对）。五具全部 CAUGHT，两个细节值得记：
M1 的证人有三条，第三条是 `#95` 那条沉默轮用例——新规矩没有把旧规矩顶掉；M3 的证人两条，其中一条
是行为侧的 `test_a_one_microphone_round_is_left_out_of_the_style_denominators`，也就是说行号闸门这次
只是**第二个**读者而不是唯一的读者，`#102` 点名的那类假证人不再单独担保这一格。M5 有 4 个证人，最后
那一个要单独点名它红的方式：`test_a_finished_batch_prints_its_line_and_exits_0` 单独重跑（20:39:47Z）
看到的是 `ZeroDivisionError`——守卫改成看条目之后，替身桌那轮"两条发言都是空串"过了守卫、
`combinations(spoken, 2)` 给空、`sum / len` 当场炸，批次那一行连同 traceback 一起沉进 stderr。这是一只
崩溃证人，不等于"批次出口读着这个数字"；它顺带证实替身桌真会产出全空的轮，也就是 `#95` 与本轮立地板时
说的那件事。

M6（`calibrate.py` 不接 `None`）预注册 MISSED，兑现：863 条全绿、证人为空，那一格改完当时无人读。
补上的读者是 `tests/test_calibrate_rehearsal.py` 最后一条
`test_a_single_success_in_the_diversity_scan_is_reported_as_no_reading`——桩在这个温度下只让一次采样
成功，表里两格必须是 `None`，而旁边的 `seat_mention_rate` 仍要有数，因为点名率是逐条算的，一份发言
也量得到。同一具变异重跑、20:35–20:38Z（基线先绿：864 passed）：CAUGHT，唯一证人就是它。模块因此
15 → 16 条，整套 863 → 864 个用例。

一处限界跟着写：地板抬到"两个开口的"之后，第三条尺子还在旧口径上。`shared_substring_rate`
（`dup_exact6_rate`，plan §8 M5 那格点名的第三条）对同一个 PK 轮交回的仍是 `0.0`（20:12:07Z 现读），
理由是它的判据是"有没有 ≥2 份共享的 6 字窗口"，一份发言在算术上确实没有共享者——但"确实没有共享"
和"没有可比对象"在这一点上读起来是同一件事，而它连一个读者都没有：`dup_exact6_mean` 在 `wolf audit`
的输出里是一格（20:08:45Z 现读那四个键之一），全仓库没有一条断言读过它，`docs/metrics.md` 的 M5
那一节写的是"两条措辞尺子取均值"，第三把尺子从来没在文档里出现过名字。这一族交给 `#104`。

#### 第三条尺子站在旧地板上，而且没有读者：`#104`

`#103` 收尾时把那半句留给了这里。三把措辞尺子里，`collapse_round` 和 `opening_distinct_rate` 是闸门
的判据（有阈值，所以它们红得起来），`dup_exact6_rate` 不是——plan §8 只点了它的名字，没给阈值，它
只出数。"只出数"的字段最容易在两件事上同时失守：口径跟着旧规矩走，而且没人读它。

20:45:51Z 现测（改动前）这一族的两个洞：`shared_substring_rate([solo])`、`([solo, ""])`、`([])`
全是 `0.0`——`#103` 抬的地板（开口人数不到两个 → 没有读数）没抬到它；更要紧的是它的分母取的是
"本轮条目数"而不是"本轮开口人数"，所以**两个人逐字复读、七个人空手**的一轮读成 `0.2222`，而这句话
本该读 `1.0`。空手在这里不是"没重复"，它是把分母撑大的一票，正是 `#95` 拒过的东西。

真日志同一格的账（20:43:00Z 现读，实现落地后 20:51:53Z 复核）：

      局                每轮 `dup_exact6_rate`        `dup_exact6_mean`
      g00000301    0.6667 / None（改前 0.0）/ 0.8571    0.5079 → 0.7619（n=3 → n=2）
      g00000300    0.8889 / 1.0 / 1.0                   0.9630（无变化）
      g00000302    1.0 / 0.8571 / 0.6                   0.8190（无变化）

`g00000301` 那三成的"更不重复"和 `#103` 里那半场"更不塌缩"是同一个 PK 轮投出来的：两条发言记录，
一条是空串。另外两局没有这种轮，所以数字一格没动——这也再次说明为什么要拿真日志量，而不是按
"最坏情况会怎样"写文档。

修法不是再抄一遍守卫。地板搬到一处（`src/wolfengine/metrics.py:63` 的 `comparable_speeches`），
它一次回答"本轮开口的有哪几份"，三把尺子各自的那句 `if len(spoken) < 2: return None` 都从它取数；
`round_readings` 里那一格跟着改成 `None if dup is None else round(dup, 4)`，批级均值因此自动跳过它
（`round_mean` 早就两端都判 `None`）。三处各写一遍正是这一族的来路：`#95` 写了两次，`#103` 补了
一次，`#104` 再漏一次——漏的那处从来不是算术错，是没人把第三条尺子当回事。

读者的那一半更要紧：`dup_exact6_mean` 在 `wolf audit` 的 `m5_style` 里是一格，而 `#102` 电池里
"把它换成 `collapse_round_mean` 的列"那具变异是**当场兑现的 MISSED**（863 条全绿）。今天它有两个
读者：`test_dup_exact6_mean_has_a_reader_and_skips_the_round_it_cannot_measure` 钉"跳过没有读数的轮"
（夹具是九个人里两个逐字复读 + 一个单人 PK 轮，均值必须是 `0.2222`，改动前现测是 `0.1111`），
金样本 `test_m5_treats_a_pk_round_as_its_own_round` 钉数值 `0.1111`（那一局三轮 `0.0 / 0.3333 / 0.0`，
取错列时两格不可能同时绿）。

顺带一条真读数，值得记在 M5 旁边而不是埋在表里：三局真日志上这把尺子每轮读 `0.6`–`1.0`
（`g00000300` 的均值 `0.9630`），而同一批轮的 `collapse_round` 只读 `0.013`–`0.026`。逐字复用的
片段到处都是，整体 n-gram 相似度却很低——这正是 plan §8 同时放两把尺子的理由，也说明为什么"只出数"
的那一把不能被删：删掉它就只剩一把量不到复读的尺子。

6 具变异、20:53–21:00Z 全程离线（基线先绿：866 passed，每具跑完按 sha256 还原并核对，
`fc5f05c887fe`），预注册 6/6 CAUGHT、兑现 6/6：M1 把地板退回"只挡全空"（CAUGHT，2 个证人）、
M2 把分母退回条目数（CAUGHT，唯一证人就是量稀释那一条）、M3 让地板不筛空——三把尺子一起塌
（CAUGHT，6 个证人，`#95`、`#103`、`#104` 三代用例同时红，这就是"地板只写一处"的担保形状）、
M4 每轮读数不接 `None`（CAUGHT，10 条红，而且它不是断言是崩溃：崩在 `round_readings` 里那格取 `None` 的除法
抛 `TypeError: type NoneType doesn't define __round__`，21:05:55Z 单独复跑现读）、M5 取错列
（**`#102` 的 MISSED 在这里翻过来**，CAUGHT，两个读者各自红一次）、M6 那一格整个不发（CAUGHT，
3 个证人，其中行号闸门是 `#77` 点过名的假证人，另两条是真正的读者）。

M4 那 10 条红里有一条脚本没报出名字，值得单独记：`test_a_clean_log_reports_no_torn_tail`。电池抓
证人用的是 `^FAILED (\S+)$`，而 pytest 把过长的那行摘要截成了 `… - Ty...`，尾巴一接，`\S+$` 就匹配
不上，名字被静默丢掉。也就是说**电池打印的证人名册是"被抓到的那些"，不是"红了的那些"**——计数
（`10 failed`）才是分母。这只眼睛是 `#79` 那类"字节相同≠执行的是磁盘上的代码"的姊妹：一个只影响
读侧报告的缺陷，不影响判定（那 10 条红照样让 M4 判 CAUGHT），但会让人以为证人比实际少。修法记在
`#106`（电池脚本在 /tmp，不是产物）：正则换成 `^FAILED (\S+?)(?: - .*)?$`，并把"N failed"计数与
名册长度并列打印，不等就点名。这一条改法已在那行真实输出上验过（21:08:42Z）：旧式 0 命中，新式抓到
`test_a_clean_log_reports_no_torn_tail`，未截断的行照样一次到位。

牙口两条是干净失败（20:46:30Z）：`assert 0.0 is None` 和 `[0.2222, 0.0] == [0.2222, None]`。
`docs/metrics.md` 的 M5 那一节跟着改了口径：以前写"两条措辞尺子取均值"，现在写三把并把
`dup_exact6_rate` 点了名——`#102`、`#103` 两节限界里引的那句"两条措辞尺子取均值"是 20:08:45Z
的文档现读，留作历史，不回头改。

一处限界：同一把"分母取条目数还是开口人数"的尺子还有一格没抬。`template_top1_share` 的分母是
`len(r)`，也就是本轮的**条目数**，所以一轮里两个人念同一句、七个人空手时它读 `2/9`；这一格喂给
C4 黑名单的不是它（黑名单直接拿 `template_top_fragments` 的片段），它只是给人看的比例，所以危害
比 `dup_exact6_rate` 小一档。要不要把它一起抬到"开口人数"，是一个会改动已发布读数的决定（`#66`
那轮就是按"占本轮份数"钉的），先记在这里，交给 `#105`。

#### 同一行里不许有两个分母：`#105`

`#104` 收尾时那句"还有一格没抬"量的就是这里。这一格比前三把尺子安静：三局真日志上它几乎**一个数字都
没动**（21:11:50Z 现读，见下），所以它不会被任何一次报告推翻——但它坏的方式和前三把一模一样，而且它
坏在**同一行内部**。

21:12:57Z 现测（改动前）三行读数：

      夹具（同一批句子）                      n   template_top1_share   同一行的 dup_exact6_rate
      三句复读 + 六张空手                     9   0.3333                1.0
      同样三句，没有空手                      3   1.0                   1.0
      两份逐字相同的发言                      2   0.0（文本那一格 "")    1.0

第一行与第二行是**同一批开口的人、同一句模板**，比例却差三倍：分母一个是本轮条目数、一个是本轮开口
人数。而 `dup_exact6_rate`（`#104` 抬过的那一把）在两行里都读 `1.0`——所以这不是"两种都合理的口径"，
是 `#102` 那个形状搬进了一行之内：同一个量在同一个出口有两个分母，人拿其中一个去解释另一个。第三行更要
紧：只有两个人开口的轮，`min_count` 是 3，按定义不可能有候选，可它交回的仍是 `0.0` 加空文本，于是同一
行里同时写着"开口的人全在复读"和"本轮没有模板"。

地板搬到一处这件事，第四次兑现的还是 `comparable_speeches`（`src/wolfengine/metrics.py:63`），但这把
尺子的门不是 2 而是 3：它数的就是"几个人共享那一句"，两份发言凑不出一个模板。算术从 `round_readings`
里搬出来住进 `src/wolfengine/metrics.py:159` 的 `template_top_share`，两格（文本与比例）**一起**缺席，
不再一个说"没有"一个说"没测"。反向也钉住了：三个人以上开口、确实没有共享片段的一轮仍读 `""` 加 `0.0`
——`#66` 那句"None 会被均值吞掉"照旧成立，把"没测到"和"测出来干净"混成一个数是这一族从一开始就在拒的
东西，矫枉不必过正。

还有一条是这一族以前没碰过的：这把尺子的地板与 C4 黑名单的地板必须是同一个数。C4 拿的是
`template_top_fragments` 的输出（`src/wolfengine/persona.py:158` 那条链，账在
`tests/test_anti_repeat.py`），读数是 `template_top_share` 算的；两者各有默认值的那一天起，报告就在
解释一份和它不同源的提示词。所以 `template_top_share` 把它的 `min_len` 与 `min_count` 原样传给挖片段
的函数，另有一条守卫用例专门盯两个签名里的默认值。

读者这一轮有三个：`test_the_template_share_divides_by_the_seats_that_spoke`（稀释，改动前红在
`assert 0.3333 == 1.0`，21:14:25Z）、`test_a_round_without_three_microphones_gives_no_template_reading`
（地板，改动前红在 `('', 0.0) == (None, None)`，21:14:13Z，同一行末尾还反钉了 0.0 那一档）、
`test_the_template_share_and_the_c4_miner_share_one_floor`（默认值同源）。`tests/test_m3_gate.py` 因此
43 → 46 条，整套 866 → 869 个用例。

真日志那一头，这次的账要如实说清楚（21:11:50Z 现读，改动前后的每轮读数）：

      局            发言轮   条目数≠开口人数的轮        template_top1_share 每轮（改前 → 改后）
      g00000300       3     0                          0.3333 / 0.0 / 0.6   一字没动
      g00000301       3     1（day-1 PK：2 条 1 人）    0.3333 / 0.0 / 0.0 → 0.3333 / None / 0.0
      g00000302       3     0                          0.3333 / 0.4286 / 0.0   一字没动

九个发言轮里只有一轮条目与开口不等，而那一轮本来就没有 ≥3 的候选，所以稀释那半**在今天的真数据上买不到
新读数**——它买到的是"同一行不再有两个分母"。地板那半只动了一格：`g00000301` 的 PK 轮以前报"本轮没有
模板"，现在报"这一轮量不了模板"。前三轮（`#95`、`#103`、`#104`）都能拿真日志指出一个被挪动的均值，这一
轮不能；它的依据是形状，不是数量级。

7 具变异、21:20:46Z–21:29:19Z 全程离线（基线先绿：869 passed，每具跑完按 sha256 还原并核对，
`837a0653d91e`），预注册与兑现逐具对账一致：M1 分母退回条目数（CAUGHT，1 个证人，就是量稀释那条）、
M2 地板退回"只挡全空"（CAUGHT，1 个）、M3 地板错用另两把尺子的 2（CAUGHT，1 个）、M4 反向过度修正、
"没有模板"也发 `None`（CAUGHT，2 个，第二条正是 `#66` 那一轮的用例——它替旧口径守住了不该改的部分）、
M5 `share` 的默认 min_count 与 miner 分家（CAUGHT，3 个）、M6 两格接反（CAUGHT，3 个）、
M7 把不筛空的列表交给 miner（**预注册 MISSED、兑现 MISSED**：空串产生不出任何 6 字窗口，也含不住
非空片段，所以这一具按构造是等价变异，不是漏网）。这一轮的脚本用了 `#106` 修好的证人正则，七具里
"N failed" 与名册长度每具都相等（1/1、1/1、1/1、2/2、3/3、3/3、0/0），旧式正则在这七具上没有再吞名字。

一处限界，交给下一轮：这一行里仍然只有 `n` 这一格是条目数。`#105` 之后，一张 `n=9` 配上
`template_top1_share=1.0` 的行讲的是"九个人里三个开口、那三个全在复读"，但行里没有哪一格写着"三个"——
读表的人要自己想得出除法。把它补成一格（或者给 `n` 换个不撒谎的名字），是 `#107`。

#### 一行既然有两个分母，就两个都印出来：`#107`

`#105` 收尾那句"行里没有哪一格写着三个"量的就是这里。这一轮没有换掉任何一个读数，也没有推翻任何一句
判据——它补的是行里缺的那两格规模。

`round_readings`（`src/wolfengine/metrics.py:942`）以前只交回一格 `n`，那是本轮的**条目数**；三把措辞
尺子的分母却是**开口人数**（`comparable_speeches` 数出来的，`#95`/`#103`/`#104`/`#105` 四轮抬的同一道
地板）。两个分母都按设计成立：`passivity_rate` 除的就是条目数，沉默正是它要量的东西，一个 turn 也不能
少算——所以这不是 `#102` 那种"同一根尺子两处算"，而是一行里两个都真的分母只印了一个。后果落在那四种
缺席上：改动前 21:38:12Z 现读真日志 `g00000301` 的 day-1 PK 那一行，面上只有条目 2、三条措辞尺子全
`None` 加 `passivity_rate` 那一格的 0.5，读者看不出这个 2 是"两个人都说了话"还是"一个人说话一个人空手"
——前者是量不出，后者是没得量，行里没有一个格子替 `#95`/`#103` 那两轮的决定说话。现在多一格 `n_spoken`，
同一行在干净树上重读（22:01:48Z）是条目 2、开口 1：缺席从此有出处，而那个 0.5 一个字没变。

另一格在局这一层：`passivity_pooled` 是这一族里唯一一个没有分母陪着印出来的比例（21:38:12Z 现读：真日志
`g00000301` 那一局印 0.1111，旁边就是视图自己的 `gate` 那句 `<0.4`，而 18 这个数只在函数里待过一秒）。
`#102` 挣来的决定是"头条按次合并"，
`pooled_passivity` 因此本来就连分母一起算好了（三局合起来 60 个 turn，22:02:01Z 现读逐局 21/18/21、合并
`passivity_pooled` 0.0333），只是出口把它当第二个返回值扔给了
`_`。2/18 与 20/180 是同一个读数，而阈值只比大小——现在它叫 `n_turns`，与 `n_rounds` 并排。

读者两条：`test_the_row_prints_both_of_its_denominators`（改动前 21:39:02Z 红在 `KeyError: 'n_spoken'`，
钉的是稀释轮 9/3、单话筒轮 2/1、全空轮 9/0，并且**印出来的那格必须就是缺席服从的那道地板**——不然它只是
一格好看的装饰）、`test_the_pooled_headline_prints_its_own_denominator`（改动前 21:43:11Z 红在
`KeyError: 'n_turns'`，钉 `n_turns` 等于逐轮 `n` 之和、也等于头条真正除的那个数）。
`test_m3_gate` 那个文件那一次从 46 涨到 48，整套从 869 涨到 871。

牙口是 5 具变异，21:47:56Z 从基线起全程离线（基线 871 passed、`metrics.py` sha `92cbc9a3d485`，每具跑完
按 sha256 还原并核对）：M1 `n_spoken` 退回条目数 → CAUGHT，2 证人（两条新用例同时红，21:49:39Z）；
M2 `n_spoken` 改成数空手 → CAUGHT，1 证人；M3 整格删掉、回到改动前 → CAUGHT，3 红里两条是 `KeyError`，
第三条是行号闸门被这次删行顶偏——它读的是行数不是行为（`#77` 那只眼睛），所以不算这格分母的读者；
M4 `n_turns` 印成轮数 → CAUGHT，1 证人；M5 `n_turns` 取成开口人数 → CAUGHT，1 证人，红在第二条用例里
那句"两格不许相等"——那句断言就是为这一具加的。M4/M5 的落地时间是 21:55:11Z 与 21:57:58Z，收尾再跑一次
无变异：871 passed。

第一版电池跑到 M4 时自己停了，这件事值得单独记：锚点 `"n_turns": n_turns,` 在 `metrics.py` 里出现两次
（M3 压力视图与 M5 视图各一处），脚本开头那道"锚点必须唯一"的断言因此拒绝落地。它救的不是文件而是**归因**——
`replace(...,1)` 会把变异打到前一个视图上，那一具照样会红、照样印得出名册，而名册里每一条都在指认别处的代码。
补跑把锚点扩成两行，并另加一条哨兵断言钉"短锚仍然出现两次"：这个数哪天变了，说明视图形状动过，脚本的前提作废。

这一轮还撞见 `#77` 记过的那只眼睛第二次没闭上：`metrics.py` 一天里被插了两回，第二次行号闸门报了 3 处
漂移、放过了第 4 处——`truncated_call` 那个号已经偏了 3 行，闸门照样绿（21:44:40Z 我按名字逐条核才发现）。
它的判据是"那一行出现了句子旁边那批 token 里的任意一个"，而 token 是**整段**取的、`None` 与 `batch`
这类弱词在邻近几行到处都是，于是错号能靠邻居蒙过去。`#108` 这一节记的是把这只眼睛闭上的两半：取词收进
引用自己的那句话，以及落在注释行上的号只认"写成名字"的证据。本轮动 `metrics.py` 之前，先按名字核过自己
动到的那 6 个号。

这一格在今天的真数据上换不来任何新读数，这一点要和 `#105` 一样写清楚：三局九个发言轮里两格只有一轮分家
（22:01:48Z 干净树重读），
而 20 局替身桌的 62 个发言轮里两格**处处相等**（21:42:12Z 现读，空手人数分布只有 0 一个档——合成替身每次都
交回一句非空的话）。所以它的牙只能长在构造的夹具上，真日志能给的只有"那一轮的缺席从此有解释了"。

#### 名字要写在被指的那一行上：把行号闸门的取词收进句子，`#108`

`#77` 那只眼睛这轮闭上了。旧判据是"那一行出现了句子旁边那批 token 里的任意一个"，而 token 取自**整段**
（±2 行）——`#72` 把窗口收到整行，取词范围却没跟着收，于是"这句话旁边"实际还是"这段话里"。两半各自
先有红的探针（22:21:57Z 现读）。

**第一半：取词收进引用自己的那句话**（以空行和句末标点为界，Markdown 的硬换行仍然接成一句）。语料的账
按名册说（22:10:17Z）：107 处 `文件:行号` 引用在旧判据下全绿，收紧后红 9 处。其中 2 处是号本身错了：
一处写着 `metrics.py` 的第 487 行，而 `_hollow_files` 的定义在 507 行（487 是一句 `elif usable and not
decisive:`，靠段里的"不是局的文件"这几个字蒙绿的）；另一处把 note 写在 751，而 `note =` 起在 750。剩下
7 处号没错、句子里没写名字，改的是句子：补上 `endswith`、`if as_seat is not None:`、`except LogDamage`、
`c_belief`、`winner=winner`、`m3_gate.md`、`round_mean`。

**第二半：落在注释行上的号只认"写成名字"的证据**（反引号里的内容、引号里的字面量），散文词不算。这一半
是第一半装完之后仍然存在的洞，22:17:28Z 逐 token 核出来的：README 指 `DECISIVE` 的那个号写着第 251 行，
那是它**上面**那行注释（定义在 253），而 251 里正好有 "a batch of outages"，句子里又写着"batch 的分母"，
于是错号靠 `batch` 蒙绿——收紧到句子也救不了，因为那个词就在句子里。范围量在 22:18:41Z：107 处里落在注释
行上一共 4 处，3 处反引号里就写着那行出现的名字（`c_belief`、`as_of`、`reuse_ratio: null`），只有
`DECISIVE` 这处没有。所以这条规则的代价实测为零，而它逮到的正是剩下那一处；号已改成 253。

读者三条：邻居句的名字不算证据（`test_a_name_from_the_neighbouring_sentence_is_not_evidence_for_this_pointer`）、
句里的字面量算证据（`test_a_literal_named_in_the_same_sentence_is_evidence_too`）、注释行上的号要有名字
（`test_a_pointer_at_a_comment_line_needs_a_name_written_on_that_line`）。`tests/test_doc_citations.py`
22 → 25 条。

**电池**（22:35:16Z 起，改完 README 重跑一遍，跑的都是整套测试）。基线：整套 874 个用例
0 红，被改文件 sha 前缀 a57eff8f3507。三具变异各只改一处，锚点先以 `count() == 1` 证唯一，每具跑完立刻
还原并按 sha 比对；预注册写死"每具恰好 1 条证人、且是本闸门自己的新用例"：

- M-A 把取词退回整段 → 22:36:11Z 红 1，证人是邻居句那条。
- M-B 把引号字面量从证据里去掉 → 22:37:06Z 红 1，证人字面量那条。
- M-C 把注释行那半退回"任意 ASCII 词" → 22:38:01Z 红 1，证人注释行那条。
- 收尾再跑一次无变异：22:39:25Z 整套 874 个 0 红。

三具都符合预注册，而读全语料的那条（
`test_a_line_number_written_in_the_docs_still_points_at_the_thing_named_beside_it`）三具都不红——
这正是 22:15:16Z 与 22:18:41Z 两次量数（"只靠字面量站着的" 0 处、"注释行上没名字的" 0 处）预告的结果：
新规则在真语料上不换来新读数，只把 251 那一处已经错着的号咬住。

第一版电池（22:16:09Z）有两处偏差，都是脚本自己的错，记在这儿免得再犯：

- 它的 M-A 把两条规则混进一具（既退回整段取词、又去掉字面量），跑出来 2 个证人，对不上预注册的 1 个。
  混了两条规则的变异等于没预注册——分不清是哪一条把它咬红的，拆成一具一条才有上面那三行。
- 它给出过一个假证人 `test_a_finished_batch_prints_its_line_and_exits_0`：那一具跑了 98.72s，而另两具
  53.01s / 80.91s；单独重跑全绿，而红的时候是 22:20:53Z 我在电池期间并发跑探针。桩端点的计时用例在负载下
  会红，而电池跑的就是整套测试。第二版起，电池跑期间不再并发任何东西。

#### 弃票率的分母从此在自己那一行上：`m8_strategy` 补两格，`#109`

`abstention_rate` 一直是 (问到的票数 − 点了名的票数) / 问到的票数，而那两个数过去只活在
`m8_strategy_proxies` 的函数体里。同族的前一轮是 `#107`（一行有两个分母就都印出来）、再前面是
`#105`（口径改了而读数不变，没人会发现），但这一格更容易被误读：0.28 既可以是一桌 25 张票里弃 7 张，
也可以是一桌 250 张里弃 70 张——前者是"有人不肯投"，后者只是"桌子更大"。现在 `n_ballots_asked` 与
`n_ballots_cast` 跟着比率一起进 audit JSON，口径与逐份读数写在 `docs/metrics.md` 的 M8 那一节。

钉的不是"字段在"，而是"由那一行自己就能复原出那个率"，另加一条不对称：比率的分母数**所有**投票波，
而逐轮那两串（`ballot_mandate` 与熵）只数落了结算记录的波。真数据的账（22:47:37Z 现读 14 份日志：
真端点三局 + 20260920 那三份 + 本轮 `--mock` 八局）：两条分母各自都能由独立的数法复原，14 份全一致；
把每次结算的 tally 值加起来等于 `n_ballots_cast`，也是 14 份全一致。而"asked 恰好等于有结算那几波的
票数"在 14 份里全部成立——**今天没有一份日志留有"投了票却没落 `vote_result`"的尾波**，所以那条不对称
（`test_a_wave_that_never_settled_is_in_the_rate_and_out_of_the_per_wave_lists`）的牙只能长在构造的波上，
与 `#105`、`#108` 同一形状：新判据在真日志上换不来新读数，它买的是"这一格从此可以被读的人自己复算"。

电池（22:49:11Z 起，三具都改 `metrics.py` 里的一行、不换行数，免得把文档里指向该文件的号顶偏）：
基线整套 876 个用例 0 红，sha 前缀 aded7a6bdee5。N1 把 asked 写成轮数 → 22:50:06Z 红 2；
N2 把 asked 退回"只数有结算的波" → 22:51:05Z **只红 1**，证人正是那条不对称用例——这一具是两条新用例的
分水岭：另一条的夹具三轮全有结算，两串分母在那儿恰好相等，它必须活着；N3 把 cast 写成 asked
（等于宣称零弃票）→ 22:52:22Z 红 2。收尾无变异 22:53:25Z 又是 876 个 0 红。三条都符合预注册。

#### 弃票有两个写者，第二个没有读者：`vote_result.abstainers` 删掉，`#110`

每一张弃票在票面上就写着 `target: null`，而 `phases._publish` 另外把 `abstainers=list(res.abstainers)`
抄进结算记录——同一件事两份实现。两份今天说的是同一件事，所以它一直没被抓到：把 14 份日志的 43 次结算
逐轮摊开，用票面复原出的弃票**名单**（不只张数）与那份字段名单比，
43/43 完全相同（23:08:28Z 现读；张数口径 22:57:52Z、23:08:15Z 两次都是 14/14 一致）。所以删它不需要
先等到哪份日志出错；需要的是确认没有人在读它，而 grep 给出的答案是零（22:57:35Z 与改完后 23:07:40Z 各
一次）：`src/`、`scripts/`、`tests/`、`docs/`、`README.md` 里剩下的 `abstainers` 只有 `rules.VoteResult`
那个字段——它当天由 `phases.py` 里的一句拼接当场算成人能读的"弃票 N 人"，那句有读者（`test_rules.py` 钉的是
`VoteResult` 本身，不是事件）。`#113` 把这一路拆开了：名单仍只在票面上，写进结算记录的是**张数** `abstained`
（`phases.py:296`），句子改由 `compress.py:151` 的 `_vote_summary` 拼——读者没变少，只是"同一件事两份实现"少了一份。两份里的哪一份该活下来，判据不是"谁先写的"，是"谁有读者"。

已有的日志不受影响：那一格还在旧文件里躺着，而读的人一个也没少（事件流是 append-only 的，这次改动
不回改任何已落盘的日志）。删的是抄出来的那一份，同一条处理在**同一张**结算记录上早就走过一次：
当时钉"弃票 N 人"那句话由 `tally` 现算、不另存文本的那条用例，`#113` 把它拆成了三条（名字见
〈投票结算的人话有两个写者〉）。新的结算记录不再写弃票名单。钉法是一条用例而不是电池：
`test_the_settlement_keeps_no_second_copy_of_the_abstainers`
（`tests/test_vote_wave.py` 现 6 条）三句连在一起才算数——事件里没有那个键、票面**真的**写着 3/5/9 三个名字、
"弃票3人"照旧（那一句当天从事件的 `summary` 里读，`#113` 起从 `compress.render_line` 里读，钉的还是同一句话）。
只有第一句会被一张没人弃票的桌满足，第二、三句就是它的反证；这一具刀是
"把那个 kwarg 加回去"，证人正是这条用例（改之前看着它红：`e20 又把弃票名单抄进结算记录`），所以不必再跑
一整套变异。改完跑整套 877 个用例 0 红（23:08:35Z），这一节写完又跑了一次仍是 877（23:14:36Z）——README
自己就是测试输入，所以引用改完必须再来一遍。

这一片顺带把两处文档账清了，两处都是 `#108` 那条教训现世的：删掉那一行让 `phases.py` 净长三行，指向
PK 旗标的两个 README 号因此从 301 漂了，闸门建议改成 298——而 298 行是当时那句拼接的 `def`，真正的
`elif pending_pk:` 在 304 行，**按名字 grep 定位、没跟着建议号走**。另一处是计数闸门把 `#73` 那天的历史条数顶成了今天的数：
`tests/test_vote_wave.py` 添了一条、当天数到五条（今天现 6 条：`#113` 又添了一条），于是把 `#73` 那四个数改写成汉字（四条、四条、二条、二条）并在旁边
点名"这是历史读数、现行条数归闸门核"，与〈那道行号闸门收得比它自己写的还松：`#72`〉末尾那句"别拿这个数
当今天的账"同一口径；没有把 791 那格历史总数改成 792，也没有为了迎合闸门把新用例挪去别的文件。

#### 死因的人话有两个写者：`cause_zh` 删掉，`#111`

`phases.py` 的三处 `t.say(Kind.DEATH, ...)` 每写完 `cause` 都顺手再抄一份 `cause_zh=CAUSE_ZH.get(...)`，
而 `events.py:77` 声明的形状里 `DEATH` 只有 `seat` 与 `cause` 两个键——多出来那一份不在任何一处契约上。
判据不是"谁先写的"，是"`cause` 是游戏事实、译文是散文"：`compress.py` 里 `VERDICT_ZH` 上面那两行注释早就
写过同一个道理（查验结果作为枚举入库，翻译归渲染侧），死因只是没照着办。`#110` 删的是没有读者的第二份事实，
这一片删的是没有契约的第二种文体。

它一直没被抓到，因为两份今天说的是同一件事：6 份日志（`data/` 里 20260920 的三份 + 真端点三局）的 37 条
DEATH 逐条比 `cause_zh == CAUSE_ZH[cause]`，37/37 相同、且没有一条死法在表外（23:47:12Z 现读）。要看分岔
得造第五种死法，而"把这句死法说成人话"的地方有四处，回退值各不相同：两处去读那个字段（`render_html._axis`、
`assemble._status_card`），两处根本不读、自己现算（`compress.render_line`、`compress.day_fold_lines`）。
把一条 `cause="old_witch_curse"` 的死亡递给改前的四个出口（23:47:50Z 现读，两列分别是字段在和字段缺失）：

| 读者 | 字段在（写手给不出译文时抄的是枚举原文） | 字段缺失（也就是声明里那个形状） |
|---|---|---|
| `compress.render_line` 时间线 | 出局（死亡） | 出局（死亡） |
| `compress.day_fold_lines` 当日摘要 | 出局：5号。 | 出局：5号。 |
| `render_html._axis` 出局顺序 | `5号 old_witch_curse` | `5号 `（后面什么都没有） |
| `assemble._status_card` 进提示词的局况 | `5号(第2天old_witch_curse)` | 同左 |

改后四格在两列下同为"死亡"（23:47:46Z，同一条命令、只换 `sys.path`）。这一片唯一的可见故障在右列第三格：
**复盘 HTML 的出局顺序轴把死因整个丢掉，印成 `5号 ` 后面一片空白**；而状态卡那一格更贵——英文枚举原文进了
中文提示词，模型被要求把它当中文读，前者是给人看的一格漏字，后者会写进下一句话的分布里。

钉法两条，另加一处要翻的旧账。`test_the_death_is_stored_as_an_enum_not_as_a_chinese_sentence`
（`tests/test_golden_game.py` 现 43 条）先要求这一桌 5 条死亡一条不少、再逐条断言 payload 里没有 `cause_zh`，
最后仍然断言"被狼刀"出现在渲染出的一行里——删的是存的那一份，不是这句人话。
`test_one_death_says_the_same_sentence_to_every_reader_of_it`（`tests/test_wiring.py` 现 63 条、跑起来 84 个用例）
拿表外死法当压力测试：先从时间线里正则抠出那句死法，再要求另外三个出口都含它，并单独断言枚举原文没进状态卡。
旧日志不受影响：它们带着 `cause`，而读的人现在统一只读 `cause`。测试这边翻的旧账是——`test_golden_game.py`
里整条 payload 的钉值原本赫然写着 `"cause_zh": "被狼刀"`，也就是**有一条用例在替这份双写作证**，先改它才动得了。
RED 23:35:10Z 与 23:36:31Z 各一次（三条红：两条新用例 + 那个钉值），改完全套 879 个用例 0 红（23:43:58Z）；
`day_fold_lines` 那一格写完就是绿的，所以按老规矩变异自证：把本地的 `CAUSE_ZH.get(..., '')` 塞回去，
23:43:47Z 立刻红，`cmp` 证明还原后字节相同。

这一片最值钱的是前半段量错的那件事，记下来给后面同类的字段审计用：**行为探测的出口集合会偷偷定义"读者集合"**。
我拿一份日志驱动所有离线出口去比对，探测报"这个字段没人读"，而 `batch.py:349` 就在读 `degraded_threshold`。
三个原因叠在一起：`compare` 只认带 `run_manifest.json` 的真批次目录，缺它 CLI 直接 rc 2 报"不是 wolf batch
产出的目录"，批次侧的读法从单份日志出发根本走不到；提示词装配链（`info.py`、`actors.py`）和引擎机制（幂等守卫、
`legality.py`）不是"给人看的出口"，探测不会替它们说话；而 `gate` 的产物 `comparison.md` 必须在 CLI 跑完之后
重读一次，否则读到的是上一轮留在原地的文件。所以读者名单只能来自 grep 符号、装配路径、引擎机制三份并集，
探测脚本负责回答的是"删掉它哪个出口的输出会变"，不是"谁在读"。

#### 投票结算的人话有两个写者：`summary` 删掉，`#113`

`#111` 那条"读者名单只能来自三份并集"的教训留下的下一步是**把每个事件的 payload 键摊出来数一遍**，
这一格就是这么撞出来的：`vote_result` 里同时躺着 `{"tally", "exiled", "abstainers"|"—", "summary"}`——
前三样是事实，第四样是这些事实的**中文说法**，而说法在 `compress._vote_summary` 里还有第二份实现。

量出来的数（`data/**/*.jsonl`，8 份有结算的日志 / 11 份日志，30 次结算，21 句不同的 `summary`；
两套键形按 20 与 10 分）：**两份实现从来没打过照面**。`render_line` 写的是 `p.get('summary') or
_vote_summary(p)`，凡是落了 `summary` 的记录就走前者，于是那份派生实现在这 30 次里一次都没被读到——
而它拼出来的句子和存进去的那句**在 30/30 上都不一样**（00:03:32Z）：它只拼"票型：…"，既不带弃票张数，
也不带"谁出局/平票/先不定人"那半句。空票型那一支更糟：旧的派生写"无人被投票出局。"，而发射侧写
"全员弃票，无人出局。"。这就是 `#110` 那一族的形状——两份实现对同一件事各说一遍，日志今天只念其中一份，
另一份是等着出错的那一份。

**把笔从 `phases` 手里拿走不是免费的**：`summary` 是模型读到的那一句话（编年史进 prompt），所以换作者
必须逐字不变，否则这就是一次处理变更。因此改法是"删存的那一份 + 让新的那一份会拼全部的四种说法"：
发射侧只写 `tally` / `exiled` / `abstained` / `pending_pk`（`src/wolfengine/events.py:76` 的形状表跟着改），
`compress._vote_summary` 成为那句话唯一的作者，旧日志仍照抄自己存的那句（`p.get('summary') or …` 留着，
注释点名它是 `#113` 之前的写法）。`abstained` 存**张数**而不是名单，不是把 `#110` 删掉的东西搬回来：
`rules.tally_votes` 有个兜底分支（投给不在场的人、投给自己、空票都算弃票），所以"这张桌有几个人没出手"
和"票面上有几张空票"是两个谓据，前者只有结算这一处能回答；30 次里两者每次都相等，因为兜底从没触发过
（00:31:11Z 按"上一次结算到这一次结算之间"配对复算，弃票名单 30/30 与票面一致）。

钉法三条（`tests/test_wiring.py`）+ 一条形状（`tests/test_vote_wave.py` ⑤）：
`test_the_settlement_sentence_has_one_author_and_four_shapes` 四种结局各钉一句（出局 / 平票 / 先不定人 /
全员弃票），其中出局那一支比的是**整句相等**而不是 `endswith`；
`test_an_unknown_abstention_count_is_not_rendered_as_zero` 钉住"`abstained` 缺失时整句不写弃票，而不是
写 弃票0人"（0 是一个主张，不是一张空白）；`test_every_real_settlement_renders_the_very_sentence_it_was_written_with`
把 30 句真话里的 29 句逐字回放一遍（那一条排除的行见下面的限界）——这是"换作者而模型读到的字不变"
这句话唯一的证法。
`test_the_settlement_stores_facts_and_leaves_the_sentence_to_the_renderer` 钉 payload 的键集合恰好是那四个。

**30/30 那句红过一次**：第一遍普查把 `(day, phase)` 当成波次的键，同一天的两波票混进同一格，量出
17/30 一致——错的不是日志，是我的配对，重跑按"上一次结算到这一次结算之间"归集才对上
（00:30:46Z→00:31:11Z）。写在这里是因为这类错只会红在**报告里**，不会红在用例里。

四具手术刀（`/tmp/mut113.py`，基线 00:32:48Z `red=[]`，每具跑完 `sha256` 比回原文件，各自一次性
`PYTHONPYCACHEPREFIX`）：

| 具 | 改动 | 红了谁 |
|---|---|---|
| N1 | `_vote_summary` 不再读 `pending_pk`，一律"平票，无人出局。" | CAUGHT 形状钉 + 29 句回放 + `test_judge_wording` ① |
| N2 | `_vote_summary` 丢掉弃票那一子句 | CAUGHT 形状钉 + 29 句回放 + `test_vote_wave` ④⑤（① 不红：它钉的是"不许说出局"，与弃票子句同色） |
| N3 | 发射侧把 `summary` 再写一份，**内容就是渲染器会拼的那句**（逐字相同的第二写者） | CAUGHT 只有 ⑤（键集合） |
| N4 | 发射侧丢掉 `abstained=` | CAUGHT ④⑤ |

N3 是这一片最想要的证据：**一份字节相同的第二实现，行为探测全瞎**——渲染出来的句子一模一样，30 句回放
一模一样，只有"payload 的键恰好是这四个"这一条结构断言抓得到它。这也解释了为什么"缺键不许写成 0"
那条用例（`test_an_unknown_abstention_count_is_not_rendered_as_zero`）在 N2 下不红：N2 无条件删子句，
缺键那条路本来就不写它，两者在这条用例的观察面上同色——它钉的是"缺键不许写成 0"，不是"子句不许丢"。
N3、N4 各还带出一条 `test_a_line_number_written_in_the_docs_still_points_at_the_thing_named_beside_it`
的红，那是插删行数把 README 的号顶偏（`#77` 记过的假证人），**不算证人**。

两处限界如实写下：`全员弃票，无人出局。` 那一支在真日志里**没有证人**（30 次的 `tally` 全非空），它只有
形状钉那一条用例撑着；`20260920T184536Z_g00000007.jsonl` 的 seq 94 那一行**故意排除**在回放之外——它写于
`#87` 之前，那时"这是不是要复投的平局"根本不在 payload 里，而它存的"平票，无人出局。"和同一份日志紧接着
发生的 PK 复投相反（`tests/fixtures/vote_settlements.json` 里标了 `prefixed_prose`，那条的 `pending_pk`
是从波次结构推的，不是从那句话读的）。这份夹具为什么要入库、为什么不是 `.jsonl`，`#91` 那条已经答过：
`data/` 是 gitignored，`.jsonl` 会在 clone 之后凭空消失，所以它以 `.json` 的形式 checked-in。

RED 00:14:28Z（三条新用例先红，红的都是"句子少了一半"那一类）。改完全套 00:19:19Z：878 passed、
4 failed（收集 882 个用例，`#111` 那天的 879 加这轮的三条），那 4 处全是文档闸门——用例名一条、条数与
收集数各一条、行号一条，因为删掉一条用例、添上三条，README 里点它的地方就同时烂了。本节定稿后复跑：
00:40:10Z 起的一跑 66.63s，882 passed、0 红，文档闸门（`test_doc_citations.py`）在那一跑里跟着
走了一遍——README 自己就是 `test_doc_citations.py` 和 `test_no_secrets.py` 两个用例文件的输入，所以
"账清完了"这句话只能是跑出来的。

#### 形状表说的是它自己的谎：`#114`

`events.Kind` 的 docstring 早就写着"payload 形状就地记在这张表里、不记在设计文档里，因为形状和代码
漂移就是上面那个分裂大脑换顶帽子"。这句话到 `#114` 为止没有任何东西在执行。而漂移不是假设：这个仓库
删掉的三个双写者——`vote_result.summary`、`vote_result.abstainers`、`death.cause_zh`——**全都是**形状表
从没点过名的键。一支与 `act` 逐字节相同的第二笔抄写对任何行为探针都是隐形的（`#113` 的 N3 那一格就是
这个形状：四把刀里只有"键集合"那把看见它），能拦住这一类的只有结构断言。

量的过程先量错了一次。00:47:40Z 第一版普查脚本把键名正则锚在行尾（`(\{.*\})\s*$`），于是 `vote`、
`notice`、`game_over` 三行——声明写在 `}` 后面还有尾巴话的三行——被读成"这张表没声明任何东西"，脚本
报出四格"未声明的 `game_over` 键"。**那张表本来是对的，是我读错了**：照着这份输出补表就会把三行历史
改成三行假话。改成整行扫描以后（00:47:45Z，11 份日志、14 个 kind）真正的漂移是这几格：作者侧五个 kind
（`speech`、`vote`、`night_action`、`wolf_chat`、`last_words`）落盘的 `evidence`/`belief`/`meta`/`_idem`
一个都不在表上，`vote` 连 `text`、`act` 都没写，`night_action` 连它真正的主键 `act` 都没写。

`night_action` 那三个键要先分清楚，别把不是病的格子砍了：`action` 是**法官问的是哪一格**（女巫那一格
叫 `save_or_poison`），`act` 是**她答的是什么**，74 格里 53 格相同、女巫那 21 格不同——两个都有读者，
留着。`potion` 才是那一笔重复：真日志上存着的 7 格 `potion`，7/7 与 `act` 逐字节相同（00:48:53Z 逐行
打的），而它按定义不可能和 `act` 不同——`legality.py:105` 就是拿这条不一致判 strict 退回答复的。所以
落盘那一份是"校验之后必然相等"的复制品，零个第二个读者（`metrics.py` 数的是 `act`，渲染链不读它）。

处理是把笔收回来、把证据留在原地：`legality` 那道自洽检查照旧（`schema.Action.potion` 还是模型答复上的
字段，`potion_act_mismatch`/`potion_unavailable` 照退），但 `agent.py:357` 起那两行写入被两行注释顶替，
行数一根没动——被校验过、不可能再和 `act` 不同的东西不必再抄一遍。被退回的那份原文一直都在 `attempts`
里，`#114` 起这才是"模型自相矛盾"的唯一证物，
`test_a_self_inconsistent_potion_is_kept_as_the_refused_answer_not_as_a_fact` 把那一整串 JSON 逐字钉住。
表这边把五个 kind 的作者侧键点名（`events.py:70` 起那五行，`evidence`/`belief`/`meta`/`_idem` 全写出来）。`evidence` 那一格后来被 `#132` 收了回去：普查的口径收紧之后它露出原形——五份 payload 里那串编号和同一格里的 `meta.citation_stats` 是同一件事的两支笔，删掉的是抄的那一份。

闸门新文件 `tests/test_payload_shape.py`（现 9 条：`#114` 那 4 条之后 `#115` 又添 2 条、`#119` 再添 1 条、`#132` 再添 2 条），语料是**当前
代码写出来的一整局**（复用 `test_golden_game.play_authored`，从盘上读回来），不是旧日志：历史里
`summary` 那三格是合法存在过的，拿它当反例会让闸门永远红、然后永远被人跳过。那四条的分工是
"落盘的键都得在表上"、"表上的键都得有人写"、"语料不能空"（13/14 个 kind 在这一局里出现过，缺的那个
点名缺）、"`potion` 不许回来"。`compaction` 需要把 B 区预算拧瘪才发得出来，就让它落在唯一有那套设定的
地方：`tests/test_live_path.py` 的折叠标记用例现在调同一个谓词 `undeclared_keys`，一条尺子两个语料。

顺带捡到 `#113` 的第一次落盘证人：`abstained` 与 `pending_pk` 在 00:43:30Z 的普查里是"声明了、还没有一份
真日志写过它们"（`#113` 之后没再跑过局），00:49:48Z 这一局 mock 落盘的就是形状表那四个键，一个不多一个
不少。

| 刀 | 砍在哪 | 结果 |
|---|---|---|
| M1 | 把 `agent.py:357` 的两行注释换回 `legality` 之后的那份 `action.potion` 抄写 | CAUGHT：`test_the_table_names_every_key_that_lands_on_disk`、`test_the_chosen_act_is_the_only_record_of_which_potion_was_spent`、`test_a_self_inconsistent_potion_is_kept_as_the_refused_answer_not_as_a_fact`、`test_the_save_is_recorded_as_a_potion_not_as_a_resurrection`（金样本那格也读这一列） |
| M2 | 作者侧冒出一个表上没有的新键 | CAUGHT：只有形状闸门红 |
| M3 | 表上少写一个真在落盘的键（`_idem`） | CAUGHT：只有形状闸门红 |
| M4 | 表上多写一个没人产的键 | CAUGHT：只有反向那条红——它专治这个方向 |

00:57:02Z→00:57:17Z 四具全 CAUGHT，每具一把 pycache 前缀、还原按 sha256 复核；`git diff --numstat` 是
`2 2`（`agent.py`）与 `8 8`（`events.py`），行数一根没动，所以 README 里 `events.py` 的 5 处和 `agent.py`
的 2 处行号引用不需要搬家。限界两条：反向闸门只看得见那一局写过的键，`compaction` 那格没人检查"声明了
却没写"；`_idem` 与 `meta` 只是被点名，它们各自的语义由 `test_prefix_stability.py` 与 `#67` 那一片管。

RED 00:52:13Z（两条先红，红的正是"表漏了五行"与"mock 局写出两格 potion"）。另两条要诚实分开：反向那条
（"表上不许有没人写的键"）落笔时表已经补全，它**当场是绿的**，它的证词只有 M4 那一具刀给得出；夜里那条
`attempts` 用例是删除之后的取证，不是行为规格。收集数从 882 涨到 887 是新增的 4 条加夜里那 1 条。
验证的次序：RED 00:52:13Z 两条先红 → 全套 00:57:24Z（887、0 红）→ 文档两页单独 01:01:41Z（35 passed）→
全套 01:03:28Z（67.62s、887、0 红）→ 补上 `COMPACTION` 那一格之后再单跑两页 01:07:17Z（35 passed）。
README 自己就是 `test_doc_citations.py` 与 `test_no_secrets.py` 的输入，所以本节再动一笔就得把这两页重跑一次。

普查剩下的那一格（`COMPACTION` 的 `window` 与 `folded_days` 有没有第二个读者）量下来**不是病**，理由是
两格各自的答案不同：`agent.py:342` 写下的 `folded_days` 与同一格里的 `summary` 确实是"同一件事的两份
说法"——`tests/test_live_path.py` 那条交叉校验就是拿正则从 `summary` 里抠出天数再对列表——但那一份是
**可查询的投影**，另一份是**模型真看到的字节**，`#111`/`#113` 删的是"散文抄结构"的方向，这里方向反过来：
`compress.py:94` 渲染读的是 `summary`，没人想再解析一遍散文去数天。`window` 在**产品链上零读者**——只有
`tests/test_live_path.py` 那两条用例读它（`>= 4` 与"它是整数"）——但它不是第二支笔：盘上再没有第二个地方
存着这个数（`request`/`meta` 都不带），删掉就没人知道那一刻热窗口有几天。这一格当时的结论是"不动"——
`#114` 只收双写、不添读者，所以它问的那个问题（有没有第二个写者）确实答完了；写在这里是为了下次不必
再量一遍。零读者那一半留给下一节。

#### 形状表的另一半：每一格都得有人读（`#115`）

上一节结尾那格"不动"到这一节为止仍然不是双写者缺陷——`window` 与 `folded_days` 全仓库只有一个写点
（`agent.py:342`）。不对的是它另一层意思：形状表承诺的是"这一格里有这些键"，而"有键没人读"是同一族
缺陷的另一半（`#88` 的字段只活在返回值里、`#101` 的 `hollow.paths` 只活在文档里）。零读者的落盘键不会
自己烂成假话，它会一直是一句"作者记得写过"。

量的过程这一轮先错了两次，两次都是把"没人读"说得太容易：

* 01:11:46Z 第一版普查脚本的下标正则写成 `\[\s*"window\s*\]`——收尾引号漏在字符类外面，于是"扫遍所有
  `["k"]`"那半边整轮匹配 0 个，只剩 `.get("k")` 那半边在干活。它报"连测试都没人读 `window`"，而那句
  测试读它是我前一天亲手写进本节的。**假阴性不是少报，是把已经点名的证人抹掉。**
* 引号修好以后（01:12:56Z→01:14:46Z）朝反方向错：`_night_text` 的 docstring 里有一句 `payload["action"]`，
  方括号、键名、字面量全对，于是 `action` 被记上一个读者。那不是一句代码。
* 第三条不进代码：这轮我给自己配的两处"MISS 控制点"是凭记忆写的，不是读来的，删掉不给任何结论用——
  只有真看过的那几行才有资格当对照。

判据最后换成 AST：`test_payload_shape.py:181` 的 `src_load_sites` 只认 `ast.Load` 位置上的下标和
`.get`/`.pop`，写侧（`payload[k] = …`、字典字面量的键）与 docstring 都进不来。01:14:53Z 量出
`night_action.action` 在 src 里 0 个读点；01:15:55Z 量出 `window` 与 `folded_days` 同样 0——三局真日志
分别折到了 9 条、7 条、9→15 条逐字窗口，产物链上没有任何一句话说得出这件事，`audit` 那块只数标记的
**个数**。

处理是**只往读侧补**，写侧一支笔都没动：

| 落点 | 干什么 | 为什么这一半便宜 |
|---|---|---|
| `metrics.py:1462` `last_fold_state` | 取**文件顺序里最后一格** `Kind.COMPACTION`，把 `window`/`folded_days` 抄出来 | 抄，不重算：窗口是 `plan_fold` 在发那一刻定的，`shrink` 还会逐 prompt 折半，离线重算就是第二支笔写另一个数 |
| `cli.py:420` `compactions.last_fold` | 那一块里唯一读**标记 payload** 的格子 | 零新事件、零 seq 挪动、零金样本重钉、零模型输入变化——这是它和 `#88`（平安夜要留痕就得发公告、加事件、重钉样本）的分别 |

真日志上这一格现在印得出来（01:28:58Z，`wolf audit`）：`g00000300` 是 `{"seq": 55, "window": 9,
"folded_days": [1]}`，`g00000301` 是 seq 66 / 窗口 7，`g00000302` 有两格标记（seq 55 窗口 9、seq 87
窗口 15），印的是后写入那一格：15 条逐字、折掉第 1、2 天。

闸门从 4 条长到 6 条。`test_every_declared_key_is_read_by_somebody_or_named_here` 要
`unread == set(UNREAD)`，**两个方向都钉**：冒出第四个没人读的键红，给 `action` 接上读者却还把它挂在
豁免表上也红。豁免必须带理由（`assert all(reason.strip() …)`），不然它只是表上的另一个键。
`test_the_reader_gate_sees_code_that_a_grep_misses_and_refuses_prose_that_a_grep_invents` 是上面那两次
读错的取证：`summary` 必须量得出 `compress.py` 的读者（单引号、还在 f-string 里，双引号 grep 量不到），
`action` 必须量出 0。`UNREAD` 现在只剩一个名字，理由写全了——`action` 是**法官问的那一格**，与 `act`
（她答的）不是一件事，`#114` 因此两格都留，但它确实没人读：唯一的读点在 `tests/test_golden_game.py:337`，
测试拿它挑狼人那一格，不是产物。

| 刀 | 砍在哪 | 结果 |
|---|---|---|
| M1 | audit 里删掉 `last_fold` 那一格（读者消失） | CAUGHT：`test_audit_reads_the_fold_rounds_out_of_the_requests_it_writes_them_into`、`test_no_named_helper_in_the_engine_is_left_without_a_reader`——**读者闸门自己没红** |
| M2 | `last = marks[-1]` 改成 `marks[0]`（取第一格） | CAUGHT：运行集三页里只有 audit 那条红 |
| M3 | 挑错事件种类（`VOTE_RESULT` 而不是 `COMPACTION`） | CAUGHT：同上，也只有 audit 那条 |
| M4 | 闸门的键全集塌成 `set()`（空语料假绿） | CAUGHT：只有双向那条红 |
| M5 | 把"这个字符串在文件里出现过"当成读者（我这轮真的这样被骗过一次） | CAUGHT：双向那条 + 控制那条一起红 |

01:25:58Z→01:26:27Z 五具全 CAUGHT，每具一把 pycache 前缀、还原按 sha256 复核；01:29:17Z→01:29:47Z 为了
把证人名册原样落进文档又跑了一遍，五具的名单一字未变。`#77` 那条老规矩照办：行号闸门不进电池的运行集，
运行集是形状、CLI、接线三页。

限界三条，按 M1 那条最疼的顺序写：

1. 闸门数的是**字面量键在 src 里的 Load 点**，不看那个字典是不是 payload。一个同名的别的字典能把
   "零读者"洗成"有读者"。0 结果安全，非 0 结果不安全：01:34:07Z 的普查是 25 个声明键里 24 个有读点、
   只有 `action` 是那个 0，而"有读点"这一步它不检查落点是不是同一个字典。本轮这两个新落点我逐个点开过：
   `metrics.py:1478` 取的是 `window`，`metrics.py:1479` 取的是 `folded_days`。老读者 `summary` 落在
   `compress.py:94`，也点开过。
2. 闸门证明"这个键被下标读过"，不证明**那条读法可达**。M1 就是证据：把唯一的读者整格删掉，红的是 audit
   用例和 `#81` 那道死函数守卫，读者闸门仍然绿——它看的是 `last_fold_state` 里那两个 `.get`，而它们一行
   没动。这一格由 `#81` 补，不由它补。
3. 豁免表"已有人读却还挂着"的那一半只在漂移**真的发生**那一刻才有证词。要是永远没人给 `action` 接读者，
   它就是一条永真的登记，逼它写理由是这条登记不烂掉的唯一约束。

RED 01:19:55Z（两条先红：读者闸门报的就是 `['action', 'folded_days', 'window']` 这三格，另一条是 audit
那一格还不存在）。GREEN 01:22:12Z（形状、CLI、直播三页 98 passed）。全套 01:23:43Z：888 passed、1 failed，
唯一那格红是文档闸门逮住上面那句"现 4 条"没跟着长——它该干的活。次序：普查 01:11:46Z→01:15:55Z（三次脚本、
一次换判据，中间读数都在上文）→ RED → GREEN → 全套 → 电池 01:25:58Z→01:26:27Z（重跑 01:29:17Z→01:29:47Z）
→ 真日志 audit 01:28:58Z → 夹具 `_idem` 改成生产形状后 `tests/test_cli.py` 01:32:59Z（66 passed）→
文档两页 01:38:31Z 与 01:40:49Z（各 35 passed）→ 全套 01:39:28Z（56.45s）与 01:41:45Z（54.72s），两次都是
889 passed、0 红（上一节那个 887 加上本节两条新用例）。规矩沿用上一节末尾那条：README 是这两页的输入，
本节之后再多一笔就得再跑一次。

#### 端点断了 45 秒一次，但它一次也没说"断"：`#116`

这批离线加固跑到 01:44:20Z 时真端点仍然在黑洞里（TCP SYN 无应答，不是 `ConnectError` 那种立刻被拒），
于是第一次拿**产品**去撞那一支形状。撞出来的是：一局打了 585s 走到第 2 天，13 个回合**全部**记成
`timeout_after_45s`、`fallback: 1`、`result.ok: true`，43 行日志里 13 条带 `request` 的记录而 `response`
全空，audit 那侧 `prefix_cache.calls=0`、`m7_cost.n_calls=0`、`degraded_game: null`。按这个速度整局要
~37 分钟——而它是一段都不必打的。

`test_transport.py:78` 那条 `test_an_unreachable_endpoint_is_the_endpoints_fault_not_the_models` 早就承诺过
这一支（"没有回答回来 ⇒ 端点不健康 ⇒ abort"），它量不到是因为夹具没有连接可超时：
`httpx.MockTransport` 根本不建连（同一件事在 `test_loopback_endpoint.py` 的开头写过一遍，那次是为了
收尾的 `RuntimeError`）。真因是一串算术，四格各自都对：

| 号 | 那一格在做什么 | 数值 |
|---|---|---|
| `agent.py:302` | 席位 deadline 取 `actor.timeout_for(ctx.phase)` | 冷启动 45s |
| `llm.py:146` | 交给 transport 的 `timeout_s = self.latency.timeout_for(...)` | 同一个 45s |
| `transport.py` 改前 | `timeout=timeout_s`：裸 float 被 httpx 摊给 connect/read/write/pool 四格 | connect 也是 45s |
| `agent.py:306` | `asyncio.wait_for` 与 httpx 的 connect 超时同时响 | 抢先进 `except` 的是前者 |

于是 `is_upstream_error` 那条分类**到不了**：`EndpointUnavailable`（`llm.py:178`）与
`aborted_endpoint`（`game.py:214`）在这形状下不可达，`max_retries_total` 那一整格预算也没人花。
`transport.py:146` 那句 "the alternative is a full transcript" 写的正是这一具，而它在修之前只是一句
注释，不是被量过的行为。

一刀：`transport.py:134` 发出 `httpx.Timeout(timeout_s, connect=self.cfg.connect_timeout_s)`，新字段
`config.py:117` 出厂 5.0。否掉的两条备选留在账上：抬 `llm_timeout_floor_s` 等于把 plan §6 的 3×median
语义换成"够容纳三次建连"，而且 abort 要等到 ~139.5s；让 transport 自己算上限，就是把
`max_retries_per_call`（`llm.py:150`）那格重试预算抄成第二支笔。

三条新用例（`tests/test_transport.py` 那时十六，写成汉字是不把历史读数顶成新数的口径，见 `#73`）：

* `test_transport.py:196` 的 `test_the_connect_phase_gets_its_own_bound_below_the_seat_deadline` 钉**接线**。
  看得见它的地方只有一处：`request.extensions["timeout"]`，httpx 收到 `timeout=` 之后真正交给 transport
  的那个对象，长这样 `{'connect': 7.5, 'read': 45.0, 'write': 45.0, 'pool': 45.0}`。7.5 是故意挑的——
  它既不是 45 的因数，也不是任何一个"忘了改"能碰巧写出来的数。
* `test_transport.py:220` 的 `test_the_default_numbers_let_the_endpoints_verdict_win_the_race` 钉**够不够开**：
  出厂值下端点判决的最坏时刻 `3×5.0 + (1.5 + 3) = 19.5s` 必须小于席位 deadline 45s。两个数分别从 `Config`
  和 `LLM.__init__` 的签名上读（`inspect.signature`），因为漂移会来自两侧，而退避常数根本不在 `Config` 里。
* `test_transport.py:245` 的 `test_the_read_budget_is_unchanged_by_the_connect_bound` 是反方向：把 read 也
  一起收紧就重新把"模型在思考"归类成了"这一回合超时"，那是上面 `test_a_stalled_read_stays_one_turns_problem`
  守着的另一半。

RED 分两阶段，因为只量一次会糊掉真正那格：02:03:49Z 三具全红，但红的理由是
`TypeError: unexpected keyword argument`（字段还不存在），那不是缺陷本体；只加 `config.py:117` 那一行、
不动 `transport.py`，02:04:08Z 再量才拿到 `{'connect': 45.0, ...}`——这一格才是这台端点上真正发生的事。
GREEN 02:04:27Z（传输、批次两页 83 passed）。修完在**同一台死端点**上重跑：02:10:43Z→02:11:04Z，
`aborted_endpoint`、19.5s、`fallback=0`、rc=1、14 行 13 个事件，其中 seq 12 那一格 `phase` 记着
"端点不可用：ConnectTimeout"、`result.ok: false`。02:04:45Z 那一次同形状的读数是 19.6s，但它走的是
`| tail -8`，所以那一跑只作证**时长**，`rc` 只有 02:10:43Z 这一跑量过（管道会把 `rc` 换成 `tail` 的码，
第一次就把这个坑踩了一遍）。

| 刀 | 砍在哪 | 结果（实际红，02:09:40Z→02:10:31Z 与预注册第一遍 02:08:16Z→02:09:07Z 逐字相同） |
|---|---|---|
| M1 | 退回裸 `timeout=timeout_s`（今天上线的那一版） | CAUGHT：接线、read 两条 + `test_batch_paired.py` 那条字段读者守卫 |
| M2 | 出厂值从 5.0 抬到 60.0 | CAUGHT：只有"够不够开"那条 |
| M3 | 四个阶段一起收成 `httpx.Timeout(self.cfg.connect_timeout_s)` | CAUGHT：接线、read 两条 |
| M4 | 字段留着，读取点改写成 `getattr(cfg, "connect_timeout_s", …)` | CAUGHT：只有读者守卫那条 |

两具的预注册期望在第一遍就被量歪了，两处都能从读过的代码里先验推出来（`_reads_attribute`，
`test_batch_paired.py:67` 只认 AST 上的属性访问），所以改的是期望而不是结果：M1 多一个证人——把 `transport.py`
那一行退回去，同时也就删掉了 src 里唯一那次 `cfg.connect_timeout_s` 属性访问，字段当场变成"有 hash 没读者"
的标签；M4 少两个证人——`getattr` 在字段存在时返回**同一个数**，接线与 read 两条是等价变异，它们不可能红，
而这一具本来就是照着 `#76`/`#85` 那一族设计的。

代价要说清楚，这一刀动的是 `config_hash`：`fb3e99579b7e` → `c63e21dfcc5a`（02:12:47Z 实测，把新键从
`to_dict()` 里去掉再按同一配方算回来，得到的正好是 2026-09-24 那三局真日志身上那个号——也就是说前面这
一整批离线加固**一次都没**动过 hash，动它的是这一格）。不重钉金样本：全仓库 `tests/` 里没有任何一处钉过
12 位十六进制的 hash 字面量（只有伪造用的 `0123456789ab`），比号的两处也都不问"和今天的出厂值相同吗"——
`batch.py:583` 拿日志对的是同一批的 manifest，`cli.py:592` 只是把松散日志按 hash 分组。所以这一格的后果
是"旧日志从此与新配置不同号"，那是它的本意，不是一笔要平的账。

限界三条：

1. `request.extensions["timeout"]` 证的是**发出去的那个对象长什么样**，不证"httpx 真的会在第 5 秒放弃
   connect"——后者只有真 socket 量得到。测试里不复制黑洞：拿"listen 但不 accept、把 accept 队列填满"
   造 SYN 丢失是跨内核不稳的写法（Linux 可能回 RST，那就成了 `ConnectError` 那一支），而这一支眼下有一台
   真的在（02:04:45Z、02:10:43Z 两次）。
2. 闸门钉的是出厂值，`--set A.connect_timeout_s=60` 这一臂能把 race 原样装回去，眼下没人拦：新字段进了
   `axis_fields`（它是被真代码读的轴，按 `#76` 的判据就该进），而"够不够开"那条读的是 `Config()`。
   真要拦得在开局处校验两模块的乘积，那是另一刀的账。
3. 修好之后黑洞那一支在产物里只留**一条** seq 12（首回合就 abort，没有第二回合），而 `timeout_after_45s`
   这个字符串在新形状下仍然会出现在"连得上但慢"那一支上——那一支的计数没人读（`m7_cost.n_calls=0`、
   `prefix_cache.calls=0` 对着一局有 13 次调用的日志，见 `#117`）。

次序：撞上去 01:44:20Z → 四格因果链读数 01:59:11Z~02:00:18Z → RED 两阶段 02:03:49Z/02:04:08Z →
GREEN 02:04:27Z → 死端点重跑 02:04:45Z → 全套 02:05:22Z→02:06:18Z（55.02s，891 passed、1 failed，唯一
那格红是行号闸门逮住 `config.py` 插 5 行把下游两处号顶后 5 行——取价那一格 `max_tokens_speech` 现在在
`config.py:196`、全局预算 `max_game_completion_tokens` 在 `config.py:119`，文档里已跟着改成这两个号）
→ 文档两页 02:06:58Z（35 passed）→ 电池两遍（第一遍 2/4 且两具的偏离都进了
上文，第二遍 4/4）→ 带 rc 的重跑 02:10:43Z → hash 半径 02:12:47Z → 本节这一笔落笔后再跑：文档两页
02:16:19Z（35 passed）、全套 02:17:58Z→02:18:54Z（54.85s、892 passed、0 红；892 = 上一节的 889 加本节
三条新用例，而那一跑在〈端点说不的时候〉的交叉补充与末尾清单那一笔 ✅ 之后，所以它是这两笔的证人）。

### 闸门给一张模型从没答过的桌发了"不消极"合格证

端点断到底的那一批（`/tmp/down116/20260925T014420Z_g00000001.jsonl`，26 次调用 26 次超时）读出来是
`passivity_rate = 0.3333（需 < 0.05）达标`。主判据在这里量的不是模型，是引擎：兜底发言落盘的 `text`
是空串、`act` 是法官指派的那个动作，而 `passivity_rate` 只把 `act in (listen, align)` 记成消极——于是
九张空嘴里凡是摊到攻击性动作的那几张，被记成了**主动发言**。一张模型一次也没答过的桌子，拿到了
"这批模型不消极"的合格证。

机制不在阈值上，所以修的地方也不是阈值。加了一只谓词 `answered_by_engine`（读 `payload.meta.rung`
== -1：那是 `agent.take_turn` 在"连提案都没有"时自己写的那一格），主判据的分母里一张模型嘴都没有时，
`passivity_rate` 走**现成的** `ok=None → NOT_EVALUABLE` 那条路：数照印（藏起来就等于让人以为闸门没算
过），但它不再是一次读数。不新增阈值、不动五条判据里任何一条的算术、不发新事件、不改 `config_hash`。
另加一行始终可见的组成：`代打 N/M 轮发言、X/Y 次调用`，与 `#92` 的截断那一格同形——一只谓词、一处
算术、一行渲染。干净批次也要印这一行，"量过了、是 0"和"没量"是两个意思（`#99`）。三局真数据
（`data/real-20260924/`）重跑闸门后与改动前逐字节相同，只多了这一行：`代打 0/60`。

`fallback` 不是这个问题，这一点我搞错了两次，都记在这里。第一次是把 `rung == -1` 和 `fallback == 1`
当成同一件事：按 `payload.meta` 数整盘（8 份日志、485 条带 meta 的记录，2026-09-25 02:45Z），
`fallback=1` 只有 3 条且全在 `night_action` 上，`speech` 上一条也没有，而 `rung == -1` 有 314 条。
第二次更糟：我在谓词的 docstring 里写了"两种形状在真桌上同时存在（`data/real-20260924/` 02:28Z
普查）：话留着、只是 act 标签被改的轮次是 `rung=1, fallback=1`"，而 02:28Z 那次普查是坏的——它的
守卫跳过了没有 `kind` 的记录，配平它的后半脚本从未成功跑过。上面那三个数才是重跑得到的，唯一的
`rung=1, fallback=1` 是一条 `night_action`，`text` 是空的，撑不起"话留着"那半句。谓词的结论没变，
变的是它不许再挂一个没跑过的出处。还有一次是路径：同一份普查我先按记录根的 `meta` 取，八份全报 0，
与闸门自己印的 `不计：9/9` 对不上才发现键住在 `payload.meta`——如果那一行没人印出来，这条错就进文档了。

**这一刀没有切到的地方**：只有当主判据的分母里**一张模型嘴都没有**时才拒绝。半死的批次（18 轮里 9 轮
代打）仍然把代打轮混在同一个 `n` 里算，仍然可能印 PASS——新印的那一行让它看得见（`代打 9/18`），但
拒绝需要一个新阈值，而五条判据是预注册的，加阈值是处理变更，不在这一刀里。

变异电池 7 具（`/tmp/mut117_defs.py`，每具跑整套、逐具按字节还原，02:42–02:56Z）：

| 刀 | 结果 | 红用例 |
| --- | --- | --- |
| K1 谓词恒 False | 击杀（5 红） | `test_a_batch_the_engine_answered_for_cannot_be_certified_not_passive`、`test_the_gate_prints_who_wrote_the_answers_it_is_scoring`、`test_no_engine_function_declares_a_parameter_nobody_reads`、`test_batch_paired` 那两条 |
| K2 谓词改读 `fallback == 1` | 击杀（2 红） | `test_a_real_clock_gives_latency_a_reading_without_certifying_the_arm`、`test_each_arm_gets_its_own_gate_verdict_rather_than_one_shared_answer` |
| K3 只要有轮次就拒（去掉 `== n_turns` 那半条件） | 击杀（8 红） | 含 `test_the_gate_returns_a_verdict_for_every_pre_registered_criterion` 与 `test_a_measured_failure_is_not_hidden_behind_a_missing_reading` |
| K4 把拒绝写成 `ok=False`（"没测到"混成"不合格"） | 击杀（4 红） | `test_a_batch_the_engine_answered_for_cannot_be_certified_not_passive` 与 `test_relabelling_a_mock_batch_cannot_turn_the_m3_gate_green` 等 |
| K5 删掉 refusal 文案、只留 `ok=None` | 击杀（2 红） | `test_a_batch_the_engine_answered_for_cannot_be_certified_not_passive`（`"引擎" in note`） |
| K6 渲染器不认 refusal，把"不计"印成"无读数" | **第一遍存活** → 补用例后击杀 | `test_a_refused_criterion_is_not_printed_as_a_missing_reading` |
| K7 次分母抄主分母（`n_engine_calls = n_engine_turns`） | **第一遍存活** → 补用例后击杀 | `test_the_engine_share_counts_calls_separately_from_turns` |

两处预期落空，各记一句。K2 我押它活，理由是"没有任何闸门用例造过 `rung>=0 且 fallback=1` 的发言"——
这句是对的，但方向反了：真桌上 `rung=-1` 与 `fallback=1` 几乎不相交（314 对 3），所以读错键会翻掉
**替身桌那两条**，而它们一直在。押错的方向是安全的那一边。K6/K7 是真缺口：判层拒了、渲染层没人问，
次分母印了、没人核它是"次"的——两个分母在旧夹具里恰好相等（18 与 18），所以第一遍的夹具量不出差别，
补的那两条各塞进一张引擎代投的票才把 20/10 与 18/9 分开。
### 一个键落了两份盘，今天起有人对账

`fallback` 在日志里有两个位置：`payload.meta.fallback`（法官那一侧的记账，M3 的 fallback 率读它）和
`result.fallback`（这次调用的结果快照，直播与复盘 HTML 的〔引擎代打〕标记读它）。两支笔、四个读点
（03:18:52Z 用测试里那把 AST 尺数 `fallback` 的 Load 点：直播 1、`metrics` 2、复盘 HTML 1；那个数不含
本次新加的一格，它自己又添两处），而 2026-09-25 之前没有任何一处问过它们说的是不是同一件事。形状和
`vote_result.summary` 那一族一样——
**双写本身不是 bug，没人对账才是**：那三例双写（`summary`、`abstainers`、`cause_zh`）是删掉了一份才收的，
这一格删不掉，因为两份各有读者、而且删任何一份都会挪动已经落盘的字节格式。

03:04:01Z 扫了语料（`data/**/*.jsonl` 11 份、464 条带 `payload.meta` 的决策记录，读的是
`rec["payload"]["meta"]["fallback"]` 与 `rec["result"]["fallback"]` 两处）：两键每条都在，且逐条相等。
"目前没坏"这句现在有一个地方能读——`audit` 多一格 `fallback_copy_check`，`n_compared` 是比过的条数、
`divergent` 是不一致的条数。算术在 `metrics.fallback_copy_check` 一处，CLI 只负责印。

> 复现：`.venv/bin/wolf audit data/real-20260924/20260924T153245Z_g00000300.jsonl | jq -c '.fallback_copy_check'`
> 实跑 03:12:35Z 印 `{"n_compared":57,"divergent":0}`；同一次 03:11:58Z 三局真数据是 57 / 58 / 58 条、
> `divergent` 全 0。端点断掉那一局是 26 条比过、0 条不一致，而**这 26 条里有 24 条两份都是 1**——
> 所以那个 0 不是"两边都写 0、当然相等"蒙出来的。

两处限界写在结论旁边，不留到下一轮才发现：

1. **缺任何一份拷贝的记录不进分母**。所以 `n_compared` 小于决策记录数时，这一格说的是"没看过"，不是
   "没坏"——钉它的用例是少一份拷贝要看见分母跟着掉，而不是看见 `divergent` 变红。
2. **它只保证两份一致，不保证那一份是对的**。写侧若哪天把同一个错误写两遍，这一格仍然绿。唯一的解法是把
   两份合并成一份，那是处理变更（要改落盘格式、要重钉金样本），不在这一格里做。

这一格自己也被具检查过（`/tmp/mut119.py`，03:15:14–03:16:31Z；两个 stage 的基准都是 0 红才开的电池，
三具都在快的那组红，两具刀所在文件每一轮都按字节还原）：

| 刀 | 结果 | 证人 |
| --- | --- | --- |
| N1 比对还在做、判决永远加 0 | CAUGHT，1 红 | `tests/test_payload_shape.py` 那条用例的第 2 段（"坏一处要点得出来"） |
| N2 只有两份都缺才跳过 | CAUGHT，1 红 | 同一条用例的第 3 段——红在那次调用上（读到缺的那一份就 `KeyError`），不是第 3 段那条断言。这仍然是红，但形状要说清：它证明的是"这一刀会被看见"，不是"分母算错了会被比较" |
| N3 CLI 不再印这一格 | CAUGHT，2 红 | `test_audit_counts_match_the_log_it_was_handed`、`test_audit_prints_metrics_and_nothing_else` |

一处工具缺陷也记在这儿：脚本拿裸用例名去对 `文件::用例` 形式的名册，所以"超预期/缺席"那一栏两边都非空。
判定没受影响（红名单是我逐条读出来的，且与预注册一致），但下一份 harness 要先归一化名字再比。

### 臂级的那一笔账：批次报告也会说"比过 N 条、几条对不上"

`#119` 给了一份日志里的对账，`#120` 给一臂的：`batch.fallback_copies_by_arm` 逐局调用
`metrics.fallback_copy_check` 再归约，`compare` 把它落成 `comparison.md` 的一节，和「末行截断」同一族
（只点名、不剔除）。算术仍然只有一只手，批次侧不许再数一遍；键名原样搬，批次侧只多两格 `n`（按
**文件**计）与 `files`，`divergent` 按**条**计——"一份文件漂了两条"和"两局各漂一条"在报告里要点名成
不同的东西。

为什么单局对账不够：`audit` 一次只看一个文件，而"这一臂漂了几条"是关于臂的问题，40 局里漂一条要翻
40 次才知道。为什么干净那一臂也要印：`0 条对不上` 只有和"比过 N 条"同一行才是读数——单看那个 0，它
与"没人比过"在纸面上完全一样，而后者正是 `#119` 之前所有批次的状态。

离线取证（`--mock` 批，永不碰端点；2026-09-25T03:34:38Z）：`wolf batch --configs A,B --mock --games 2
--seed0 4242 --out /tmp/f120 --set B.temperature=0.6`，把 `actor_kinds` 手工改成 `["llm"]`
（`tests/test_batch_paired.py::_as_real_table` 那同一种伪造，为的是走通渲染而不是替模型说话），再
`wolf compare /tmp/f120 --axis temperature`：

- 干净那一次印 `- A：比过 115 条，0 条两份拷贝对不上`（B 同）。
- 把 A 臂两份文件里 `seq` 最小那条的 `result.fallback` 翻掉之后，报告印
  `- A：比过 115 条，2 条两份拷贝对不上（2 局）：20260925T033403Z_g00004242.jsonl、
  20260925T033403Z_g00004243.jsonl`。

两次的分母都是 115：找到不一致**不缩小分母**（`#56` 同一条规矩），那条记录既被比过也被点名。点名用
文件名不用 `game_id`，理由与 `#54`/`#55` 一致——文件被人改过的时候，局号是它自己内部的标签。

真端点上这一格还没有读数：臂级要批次，而 `data/real-20260924/` 那三局是 `wolf run` 出的**单局**，
`compare` 只认带 `run_manifest.json` 的批次目录。它要等一次真批次，而端点仍不通（03:21:09Z TCP
TimeoutError 5.01s、`WOLF_LLM_API_KEY` 未导出）。

测试两只：`test_the_batch_side_names_files_whose_two_fallback_copies_disagree`（归约：文件数/条数分开、
分母不许动、键名不许自己另起一套）、`test_the_batch_report_prints_the_reconciled_denominator_for_both_arms`
（渲染：两臂各一行、干净那行也得有分母）。五具变异 `/tmp/mut120.py`（刀前整套 899 条全绿、刀后整套
899 条全绿，每具还原后校验 sha256 与 `import wolfengine.batch`；每次子进程一个全新的
`PYTHONPYCACHEPREFIX`，见 `#79`）：

| 刀 | 结果 | 证人 |
|---|---|---|
| K1 不点名（`hit = []`） | CAUGHT，2 红 | 归约那一只 + 报告那一只，与预注册逐字一致 |
| K2 干净臂闭嘴（只印有 `divergent` 的臂） | CAUGHT，1 红 | 报告那一只（`row_b` 那一行没了） |
| K3 只印分子、不印分母（去掉"比过 N 条"） | CAUGHT，1 红 | 报告那一只（两行都少了分母） |
| K4 把条数当局数归约 | CAUGHT，1 红 | 归约那一只，红在 `pick=1` 那一腿 |
| K5 键名自己另起一套（`n_compared`→`n_checked`） | CAUGHT，**30 红** | 预期 2 红，多出的 28 条是连带 |

K4 那一具是这一片里唯一需要**先改测试**才杀得掉的：只翻一条记录时"按文件归约"与"按条归约"给出同一个
数，两份实现完全等价。补的那一腿是同一份文件里再翻一条（`_flip_fallback_copy(..., pick=1)`），于是
`n == 1` 而 `divergent == 2`，两个数分开了才有证人——这一腿也是 `#106` 那条老话的又一次命中：等价变异
不在预期里，就只能靠夹具的数量说话。

K5 要按样子读：它确实被杀，但**不是一把能定位证人的刀**。键名一改，渲染器里 `d['n_compared']` 抛
`KeyError`，于是每一个走 `compare` 渲染的用例都红——30 条里只有 2 条是我预注册的那两只。这一具证明的
是"这个键名有读者"，不是"哪个断言在钉它"；要定位证人得改用只动归约、不动渲染的那一族刀（K1、K4）。

### 读数也要有人能自动拿：`compare --json`

`audit` 从第一天起就是"一局一个 JSON 对象"，`compare` 却只有一份 `comparison.md`：臂级那九格读数
（M1 胜率、M3★ 判定、degraded、编号破损、末行截断、两份 `fallback` 拷贝对账、区域预算、前缀缓存、
配对分母）全住在散文里，脚本要拿就得 grep 中文。`#120` 刚把"哪几个文件的 `fallback` 对不上"印进
报告，而它的下一句话就是"那谁来自动查"。

现在 `wolf compare <批次目录> --axis temperature --json` 另写一份**同基名**的 JSON：内容就是
`compare()` 返回的那个字典，只挖掉 `markdown` 一格。两条理由各自钉在用例里——

* 键集断言写的是 `set(js) == set(compare()) - {"markdown"}`，不是抄一份名单：以后往报告里加一格，
  它自动进 JSON，不需要有人记得改第二处。把 `markdown` 也塞进去等于同一批读数落两个地方，改口的
  时候只有一个是真的。
* JSON 跟着 `-o` 走，不钉死在批次目录：一次比较要留档的人，两个产物分在两个目录里就一定会有一个
  被忘掉；不敲 `--json` 时什么都不许多产。

**为什么是文件而不是 stdout**（`audit` 走的是后者）：`audit` 一次一局，stdout 就是它的产物；
`compare` 的 stdout 已经被 markdown 占了，再接一段 JSON 等于让 `wolf compare | jq` 拿到两条流。

**拒绝的批次也落盘**，而里面**没有**那九格——`m1`/`torn`/`fallback_copies` 这些键根本不存在，不是
被填成 `0` 或 `{}`。合成桌那一批留在 `stats` 键下的逐条率是另一件事：它有自己的用例钉着"不许抹掉"
（mock 批的价值就是证明管线通），而 OK 那一批永远不带 `stats`，所以消费者不会在两个地方找同一份率。

实跑（2026-09-25T03:55:02Z，`/tmp/f120` 那份伪造批次）：

```bash
.venv/bin/wolf compare /tmp/f120 --axis temperature --json | tail -2
# JSON -> /tmp/f120/comparison.json
# OK -> /tmp/f120/comparison.md
```

13 个键：`canary_note`、`degraded`、`fallback_copies`、`m1`、`m3_verdict`、`n_pairs`、`numbering`、
`prefix_cache`、`region_budget`、`torn`、`utterance`、`verdict`、`win`。其中 `fallback_copies`
原样搬出那两份对不上的文件名，而 `n_compared` 仍是 115——分母一个没动。

四把刀的预检（`/tmp/mut121.py`，每把点名它期望弄红的那一条用例，跑完按字节还原）：

| 刀 | 判定 |
| --- | --- |
| `markdown` 也进 JSON | 咬住（第一条） |
| JSON 钉死在批次目录 | 咬住（第三条） |
| 只在出结论时才落 JSON | 咬住（第二条**和**第三条：第三条走的也是合成桌拒绝，是我预注册时漏了它） |
| 少带一格 `torn` | 咬住（第一条） |

**这一片没有动的**：仓库里没有任何脚本消费 `comparison.json`——它是出口，不是消费方；而这一格里
的臂级读数仍然全部来自伪造批次（同 `#120`），真端点上两臂的 JSON 至今没有产出过一次。

### 一行字变成一张票：那一席从契约变成坐得下人的座位，`#122`

§十五那三条（超时按 actor 取、并发度表剔掉 `blocking`、墙钟只对全模型桌生效）在 `#34` 就有测试了，
但那一席的 `act()` 是一句 `NotImplementedError`。于是能测的只有"**编排层**为'有人在想'prepared 到
什么程度"，测不出"**一个人**怎么把那三样用起来"——因为没有实现。这一片补后半边：
`src/wolfengine/human.py`（读一行字、算一屏卡片、那块真终端）和 `HumanActor.act()`（把读懂的那一行
交出去，把没读懂的那一行问第二遍）。

五个决定各自有理由，都不是风格：

* **不进 `schema.py`**。`test_purity.py` 那份纯核名单里 `schema.py` 不许碰 I/O，而这一模块存在的理由
  就是碰终端；把"读一个人"的语法放进去，等于让"零 LLM 单测整局"依赖一台连着控制台的机器。
* **借词表，不借容错**。玩家能打的词整张来自 `schema.ACT_SYNONYMS`（模型那条解析梯子用的同一张表），
  但 `normalize_act` 那支"整句里含动作词就算"不借——它是对模型输出的容错，用在打字的人身上会把一句
  题外的话变成一张他没打算下的票。
* **问第二遍住在 `HumanActor.act()` 里，不住在 `human.py` 里**：重问需要那块屏幕，而 `human.py`
  一只手都没有。分开的另一个好处是它整个模块字符串进、字符串出，测一屏卡片不必构造 `Proposal`。
  打错的行既不占 `cfg.max_repair_retries`（那是模型的账），也不进 `attempts[]`（没人被问过，就没有
  一条偏好对的"被拒"侧）。
* **读输入走 `asyncio.to_thread`**。阻塞的 `input()` 写在协程里占的是**事件循环**，其余八座都在里面。
  §十五第 2 条以前只在替身桌上测过（`_Seat` 的 `blocking` 旋钮），这一片是它第一次有现场。
* **EOF 是一个名字，不是一次代答**：`Proposal(failure="human_input_closed")` 让"这个人走了"落进
  `attempts[]`，而引擎替他做的是 `pass`（`default_action` 的口径），不是替谁编一票。

十用例在 `tests/test_human_seat.py`（现 30 条，一秒钟内，不发请求；这一片落地的是其中前十条，`#124`
又在这份文件里加了两条屏上局况的）。八具变异加两具负控制照
`/tmp/mut122.py`（2026-09-25T10:33Z，每具点名它期望弄红的那条，跑完按字节 `cmp` 还原，前后基准都是
0 红，pyc 前缀每轮换新）：

| 刀 | 红用例 |
| --- | --- |
| K1 「在行首」放宽成「句中含」 | `test_a_sentence_that_merely_contains_an_act_word_is_not_an_instruction` |
| K2 删掉「紧跟非座位号 ⇒ 整行不算指令」那一支 | 同上 |
| K3 玩家那句话被丢掉，只剩动作和座位号 | `test_a_typed_line_names_the_act_then_the_seat_then_the_words` + `test_the_players_sentence_lands_in_the_same_cell_the_models_do` |
| K4 读不懂就交一份失败的、不问第二遍 | `test_an_unintelligible_line_is_asked_again_without_burning_a_repair_retry` |
| K5 输入关了不说是谁答的（改成交 `Proposal()`） | `test_a_closed_input_ends_the_turn_and_says_which_hand_answered` |
| K6 卡片把所有动作都列出来 | `test_the_card_offers_only_the_acts_this_turn_can_answer` |
| K7 卡片不说法官指派了什么 | `test_the_card_says_what_the_judge_assigned_and_what_refusing_costs` |
| K8 读输入不再走线程 | `test_reading_a_human_does_not_hold_the_event_loop` |
| K9 负控制：换提示符的文字 | 活着（对：那串字不是这套用例认领的东西） |
| K10 负控制：`Console.show` 不再 flush | 活着（同上一条） |

**K1 在第一轮下刀时是活着的**，而它活着说明的不是刀钝，是那一支收紧没有证人。第一版 K1 只把
`text.startswith(t)` 换成 `t in text`，十条全绿：`rest = text[len(token):]` 仍从行首切，剩下的是
`_lead_seat` 在挡。把刀修诚实（按找到的位置切）之后差别才露出来——「先票 3」这种"动作词前面有字、
后面紧跟空格和座位号"的行，放宽以后是一张票，按原来的代码是"没读懂"。少了这条断言，"动作词必须在
行首"这句话就只活在 docstring 里。补上之后 K1 才有了上面那一格。顺带把 `human.py` 里那句"这是唯一
区分两种行的东西"改成实测的样子：两处收紧各挡一种松法，而「我票了3号」同时落在两处——单独放宽任何
一处都到不了"变成一张票"那一步，所以两把刀分开下、两条断言分开钉。

**改文档**：`tests/test_actor_contract.py` 的文档计数从九顶到八（删掉的那条钉的是 `act()` 里那句
`NotImplementedError`，前提没了；条数闸门抓的正是这一格），`tests/test_wiring.py` 里拿 `HumanActor.act`
当"整个 body 只有一个 `raise`"例子的两处改成今天仍是桩的名字，`docs/views.md` 那条"真人入座的界面"
从"只定契约不实现"改成了"座位有了、命令行还没接"。

**这一片没有动的**：`wolf run` 还认不出"这一席坐着人"——全树没有一处生产代码构造 `HumanActor`
（`#123`）；那一屏给这个人看的**内容**还没有金丝雀，`decision_card()` 读的是 `LegalSet` 而不是事件流，
`test_info_isolation.py` 那套正反证的是 `percept_for` 过滤得对，够不到这条新通路（`#124`）；端点这一轮
仍然密钥未导出，所以这十条一次请求都没发（这一席本来就不经过端点），但它们也不是从桩上取的数——
`HumanActor`、`agent.take_turn`、`legality.check_action` 和落盘那几列都是真的。上面两格后来各自落了地：
名册与落盘是 `#123`，那一屏的金丝雀是 `#124`，`test_info_isolation.py` 如今也读屏上的字节。

### 一个人坐进命令行那一桌：`run --human 3`，`#123`

`#122` 之后那一席会读一行字、会问第二遍、会把"这个人走了"落进日志——但**全树没有一处生产代码构造
`HumanActor`**，所以"AI 与人类博弈"是一件存在于测试对象里、不存在于一条命令里的事。这一片把它变成
一条命令：`wolf run --mock --human 3`（其余八席仍是替身；接真端点时同一根旋钮把一席从模型换成打字的人）。

四个决定：

* **名册只有一个构造点**。`_roster(cfg, seat_actor, *, human)` 按 `cfg.seat_count` 摆椅子，`human`
  那一席换成 `HumanActor`，mock 桌和真桌都从这一只手过。判据不是审美而是可数的：
  `test_only_one_place_in_the_cli_puts_a_person_in_a_chair` 在磁盘上数 `src/` 里 `HumanActor(` 的调用点，
  要求恰好一处且在 `cli.py`。座位表一旦有两处拼法，"这一席坐着人"就有了两个写者，而日志里
  `actor_kinds` 只有一份。
* **三句拒绝都站在 `mkdir` 之前**（`_human_error`，次序是 `#58`/`#59` 立的那条）。两个 `--human`：一桌
  只有一个键盘，两席同时读 stdin 会把两个人的半句话拼成一行，而日志会显示两席都"答了"；
  `--dry-run --human`：转储的每一行都是"模型这一席会读到什么"，混进一席不由模型答的座位就是假账；
  `--human 10`：不在名册里的那一席不会被 anyone 坐，而它会被静默读成"这局没有真人"。范围用
  `apply_overrides` 之后的 `cfg.seat_count`，所以 `--set seat_count=5 --human 9` 也拦得住。
* **`no_network` 搬进 `tests/conftest.py`**，不是顺手整理：`#123` 是第二个需要"这条命令够不到端点"
  这把尺的文件，而夹具按名字注入、AST 里没有点名者——`#84` 那条用例的 docstring 早就写了这一族的
  修法（"搬进 conftest，不是给闸门开豁免"）。留在一处，两份判据就是同一份。
* **真人那一席答没答，日志里分得开**：`actor_kinds` 落成 `['human','mock']`（`game.open_log` 从名册取，
  不是 CLI 另写一句），逐回合则看 `result.fallback` 与 `attempts[].failure`。

`#123` 那一片落的是六用例，如今这些在 `tests/test_run_with_human.py`（13 条、跑起来 15 个用例，
一秒内，不发请求；多出的七条是读侧那一族）。真跑一遍的现场
（2026-09-25T10:57Z，`--mock --human 3 --seed 7`，rc 0、83 事件、`actor_kinds=['human','mock']`，
stdin 用管道喂三行后就是 EOF）里，3 号那六回合各自说的是三种不同的事：

| seq | 回合 | 落下来的 | 依据 |
| --- | --- | --- | --- |
| 19 | 发言 | 他**第二句**的动作（`vote 2`），`fallback=1` | `attempts[]` 两条：`act_not_as_assigned:listen` 然后 `:vote`——被判官指派换掉会被再问一次 |
| 36 | 投票 | 引擎代答 `pass` | 他打的「指控 8」在投票轮不是 `act_not_as_assigned` 而是 `act_not_allowed`，越出"只驳标签"那一支，句子按规矩不保留 |
| 47/60/73/79 | 发言与投票 | `pass`/代答，`attempts[]` 记 `human_input_closed` | 键盘已经关了：这一席空着，但每一回合都说清了是谁答的 |

**顺带量出来一件这一片没解决的事**（记账给 `#127`，下面那一节已经把它接掉）：`agent.py` 的修复重试对人也生效——第一句被驳回后
再问一次，`retry=` 那一格跟着加，而第二次印的是**同一张卡片**：`ctx.attempt` 已经带着"这是第几次问"，
`retry_note`（`agent.py:232` 算出来的拒绝理由）只进模型那一侧的 prompt，真人这一侧零读者。于是他会看到
自己刚读过的那屏原样重来，而卡片上那句"换成别的会被引擎代答一次"与实际发生的"会被再问一次"不一致。

七把正刀、两把负控制、一具缺口刀（共十具）照 `/tmp/mut123.py`（2026-09-25T11:13Z，每具跑
`tests/test_run_with_human.py` 与 `tests/test_cli.py` 两个文件，合计 75 个用例）——带上后者是因为
这一片动的是 `cmd_run` 的入口和名册拼法，一把只该弄红真人用例的刀若顺手红了替身桌的旧用例，那张
"红用例 = 这条主张"的表就是
假话，所以判据是**恰好相等**、多一个也算不符；跑完按字节 `cmp` 还原，前后基准都是 0 红，pyc 前缀每轮
换新）：

| 刀 | 红用例 |
| --- | --- |
| K1 mock 桌根本没把椅子交出去（`human=human` 改成 `human=None`） | `test_a_person_at_the_table_is_asked_and_their_words_are_in_the_log` + `test_the_person_who_leaves_leaves_a_record_and_the_game_still_ends` |
| K2 椅子给了隔壁那一席（`s == human` 改成 `s == human - 1`） | 同上两条 |
| K3 构造点变成两处（`make_actors` 的 lambda 里也换一次） | `test_only_one_place_in_the_cli_puts_a_person_in_a_chair` |
| K4 越界只拦下界、不拦上界 | `test_a_seat_outside_the_table_is_refused_before_the_disk_is_touched` |
| K5 三句拒绝挪到 `out.mkdir` 之后 | 上面那一条 + `test_two_people_on_one_keyboard_are_refused` + `test_the_prompt_dump_refuses_to_promise_a_seat_a_person_is_in` |
| K6 两把键盘放行（`len(seats) > 1` 改成 `> 2`） | `test_two_people_on_one_keyboard_are_refused` |
| K7 dry-run 那一支不拦 | `test_the_prompt_dump_refuses_to_promise_a_seat_a_person_is_in` |
| K8 负控制：换 `--human` 的帮助文字 | 活着（对：那串字不是这批用例认领的东西） |
| K9 负控制：换 `_roster` 的 docstring 措辞 | 活着（同上一条） |
| K10 缺口刀：真桌不交椅子（`_llm_actors` 里 `human=None`） | 活着，而**这一具的活着不是刀钝**——它量的正是上面那句"`['human','llm']` 那张桌这轮没有用例" |

**K2 第一轮是漏刀，而它漏出来的东西比咬住更贵。** 第一版那条断言扫的是**全部**事件里有没有那个人的
原话，于是把椅子挪到 2 号之后它仍然全绿：`actor_kinds` 还是 `['human','mock']`（桌边确实有个人）、
`calls` 还是非空（他确实被打字）、`meta.rung` 还是 -1（`#122` 那格定了替身也不走解析梯子）——换句话说
"名册说的那一席"六个字当时在整批用例里**只有一个证人**（EOF 那位的 `human_input_closed`）。补的断言
把同一句话收进 3 号自己的格子，收完之后先在**未变异**的代码上确认它是绿的（把用例修剪到产物上不算
证人），K2 才有了上面那一格。

**改文档**：`cli.py` 里这次有四处插入（`make_actors` 体内、`_seat_error` 之后、`cmd_run` 体内、
`build_parser` 的 `run` 子命令），下游偏移是 +7/+35/+40/+45 几种混合，所以文档里 23 处 `cli.py:NN`
全部按**语句**重钉而不是按算式——`/tmp/relock123.py` 在写盘前逐条回读新号那一行，要求它仍然含着点名的
东西（`_games_error` 的定义、`_run_and_close` 的两个调用点、`res.out_dir`……），有一条对不上就整体不写。
`tests/test_loopback_endpoint.py` 里那句"收尾用的是同一只手"后面跟的号写的是 `cli.py` 的 457 行，而这是
**这一片之前就已经漂了**的号（当时真号 524 行、现在 569 行）：`test_doc_citations.py` 的行号闸门只扫
`docs/*.md` 与 `README.md`，测试文件的 docstring 不在范围内，所以它没红过——记为 `#126`。

**这一片没有动的**：那个人的**屏幕上还没有局况**——`decision_card()` 读的是 `LegalSet` 而不是事件流，
所以他只知道自己这一轮能答什么、不知道前面发生过什么（`#124`，金丝雀与这一屏同片做）；`['human','llm']`
那张桌（`--human` 不配 `--mock`）这轮没有用例，端点可及性是唯一原因；含真人座位的局在读侧还没被点名
剔除并附一句依据，`metrics.is_synthetic` 会把它们请出配对语料分母，但那是替身桌那句话在替他说
（`#125`）。第一格紧接着就落地了，见下一节；后两格仍然没动。

### 那一屏不再只回答"这一轮能答什么"：局况上桌，金丝雀同片，`#124`

`#123` 之后那个人的卡片上只有三样东西：轮到谁、这一轮答得了哪些动作、能点名哪几席。他**不知道
前面发生过什么**。而这不是缺一个功能，是缺一条证据：`agent.py` 早就把过滤好的 `Percept` 递给每一席
（包括人那一席），`info.py` 的模块 docstring 也早就写着"人类拿的是同一个 `Percept` 对象，所以这一侧
也漏不出去"——这句话这一片之前**只活在散文里**：那个对象在 `decision_card()` 里一个字段都没读。

卡片因此多了两样东西：

```
轮到你了：3 号（villager）
局况（你看得见的最近 2 条）：
  [e1] 法官：开局座位 1、2、3、4、5、6、7、8、9号。
  [e4] 法官（私发）：你的身份是 villager。
这一轮可以答：
  票/投票 加座位号 后面可以跟你要说的话
  弃票/过/跳过 后面可以跟你要说的话
可点名的座位：1、2、4、5、6、7、8、9
```

四个决定：

* **不新开一只手**。局况逐行走 `compress.render_line`——仓库里唯一的那只渲染器。屏上的行和
  `replay`、直播、prompt 里的行同格式，座位号、〔沉默〕、私有标记都不是这一片新写的词。
* **屏幕不去够日志**。`decision_card()` 拿到的还是那个 `Percept`，窗口是 `percept.tail(SCREEN_TAIL)`，
  没有 `log.all()` 这条路。"这一席看不到的不许印出来"于是是一个**形状**上的结论：数据不在这个对象里。
  但形状不等于证明，所以另一半是金丝雀。
* **`SCREEN_TAIL` 是窗口，不是整份**。一个人面前不该堆一百行；`replay` 才是复盘的出口。
* **"可答动作"那一段要有一条边界**。局况是中文散文，而 `ACT_SYNONYMS` 里有单字词（票、听、刀、
  出局、发言……），散文一旦混进可答段，"卡片列的=闸门答得上的"这条断言就会由着法官的一句话蒙绿。
  所以 `OFFER_HEADER` 把可答段立成一段，边界是那两格缩进。

### 这一片的两条主张各自住在哪个文件

"该在的在"是 `tests/test_human_seat.py`（现 30 条）：他听过的两句原话上了屏、屏上说了是谁说的、窗口
的**方向**（砍的是最旧的）、以及标题上那个"最近 N 条"和屏上真的条数是同一个数。
"不该在的不在"是 `tests/test_info_isolation.py`（12 条函数、跑起来 36 个用例，其中三条是这一片新加的，
展开成 15 个断言）：原来那张板的五条私有
金丝雀逐席双向断言（`在不在 == 有没有权看`）、九席各自的身份、还有一条**屏幕版反向对照**——把狼队
私聊改成全体可见，它必须真的能上屏。最后这条不是礼貌：局况是个窗口，掉出窗口的金丝雀会让"没泄漏"
那半边**因为错的原因**成立；这条一红，就是在提醒板子已经长过窗口了。

九具变异照 `/tmp/mut124.py`（2026-09-25T11:45Z，产物 `/tmp/bat124.out`，EXIT=0，每具跑上面两个文件、
合计 46 个用例，判据是红名单**恰好相等**，跑完按字节 `cmp` 还原，前后基准 0 红）：

| 刀 | 红用例 |
| --- | --- |
| N1 局况那一格整格死掉（`... if False`） | 5 条屏幕金丝雀 + 屏幕反向对照 + "该在的在" + 窗口那两条 |
| N1b 局况被缩成"只印发言" | 5 条屏幕金丝雀 + 屏幕反向对照（发言与窗口那两条**不**红，它们读的就是发言） |
| N2 取消窗口，整份记录上屏 | `test_the_screen_block_is_a_window_not_the_whole_transcript` |
| N3 窗口砍错一头（留最旧的 12 条） | 同上那一条，红的是"窗口砍掉的是最近的发言"那句 |
| N4 把局况挪进"可答动作"段内 | `test_the_card_offers_only_the_acts_this_turn_can_answer` |
| N5 不走 `render_line`、自己印 payload | `test_the_screen_says_what_this_seat_was_allowed_to_hear` |
| N6 共闸刀：`Event.visible_to` 永远放行 | 23 条（两套 canary、9 条屏幕身份、两条反向对照、`__post_init__` 那条、狼队频道那条） |
| N7 负控制：局况标题换个说法 | 活着（对：那串字不是这批用例认领的东西） |
| N8 负控制：`OFFER_HEADER` 的字面值换掉 | 活着（同上，`_offer_block` 按常量找段，不认字面） |

`#79`（`cmp` 只证明文件干净、不证明执行的是它）这一跑是双保险：每个子进程带一次性
`PYTHONPYCACHEPREFIX`，同时 `PYTHONDONTWRITEBYTECODE=1` 让磁盘上一个 `.pyc` 都不落——所以真正拦住
"字节相同而跑的是变异体"的是后者，前者留给哪天关掉后者的人。

**两件是量出来的、不是想出来的。** 第一具 N1 我下的刀是 `tail(SCREEN_TAIL)` → `tail(0)`，本意"这一格
又没有局况了"，结果只有窗口那一条红：Python 里 `events[-0:]` 就是 `events[0:]`，`tail(0)` 等于**全给**，
是一具等价变异。换成 `if False` 才问到我想问的那件事。顺带量到 `Percept.tail` 现在全树只有这一片新的
那一个调用点，所以那个 `-0` 陷阱够不到生产代码，就没为它加校验。

N6 那一格我把预期说错了**两次**：先判断"prompt 那 9 条身份断言会绿，因为 region B 只挑
`visibility=all`"（方向对、理由错），又改口"会红，因为私有块印的是 `priv[-8:]`"——去读那一行才知道
`assemble.py` 写的是 `e.kind != Kind.DEAL`，身份卡根本不走那条路。跑出来 23 红，正是第一版那份名单。
留下来的是这句：**prompt 那一侧的"看不到别人的身份"有两只手（`percept_for` 的过滤 + 装配器一条显式
排除），屏幕这一侧只有一只**。所以那 9 条屏幕身份断言不是 prompt 那 9 条的复本——它们是这一屏唯一的
一道证人。

**改文档**：`test_human_seat.py` 的条数从 10 顶到 12（两处：README 的 `#122` 一节与 `docs/views.md`），
计数闸门抓的正是这种"加了用例没人改账"。

**这一片没有动的**：`#127` 那格当时原样还在（下面那一节接掉了它）——他被驳回后看到的是**同一张卡片**原样重来，如今那一屏还
多了一窗局况（最多 `SCREEN_TAIL` 条），重复的成本比这一片之前更高；`['human','llm']` 那张混合桌仍然没有用例（端点唯一原因）。
读侧那一格由 `#125` 接手，见下一节。

### 这一局是谁答的：三个出口认领"桌边坐着一个人"，`#125`

缺口是读出来的，不是推出来的。2026-09-25T12:04Z 拿 `--mock --human 3 --seed 7` 打出来的那份日志逐屏
读过：`replay`、`watch --once`、`export` 的 HTML 里**连 "human" 这串都没出现**，三屏给人看的唯一一句
免责是"本局不可复现：端点没有确定性"——而桌边坐着一个从不经过端点的人时，那句话的主语根本不在这张
桌上。机器那一侧也不算没人管（`SYNTHETIC_CLAUSE["human"]` 早就在，§十五 也早就挡），管的是这一片的
真缺口：**引它的那些用例页眉全是手填的**（`test_synthetic_attribution.py` 的 `_table` 造 dict、
`_rehumanise` 就地改 jsonl 第一行）。手填的页眉证不到引擎写的那一格长成什么样，而写侧
（`game.open_log` 的 `sorted(set(...))`）、谓词（`is_synthetic` 的 `sorted(...) != ["llm"]`）、摊平
（`seat_kinds` 的 set 推导）是三处各写各的读法。

补上的是这一句，三个出口共用一只手（`events.roster_notice`，与 `meta_notice`/`empty_notice`/
`seq_notice`/`torn_notice` 同一族）：

```
桌边坐着一个真人（actor_kinds=['human', 'mock']）：他答的那几席不经过端点，这一屏不是九个模型自玩的那一局
```

四个决定，每一个都有一把刀对着：

* **不在这句里引 §十五**。屏幕要回答的是"这些是谁的话"，条款那句讲的是"这批能不能进语料"，后者在
  批次侧已经有一个主人（`compare` 的拒绝语与两处 note），复用到屏幕上就是把两种读者绑在一句话上。
* **页眉那一串 `⚠` 不给它**。那四个 `⚠` 说的是"这个文件坏了"，而 3 号席坐着一个人什么也没坏。
* **挂在这串的**前面**，不是后面**。转录的最后两格早被认领了（倒数第二是编号那句、最后是"日志在这里
  截断"），一句关于谁在桌边的话不该去占"文件停在这里"那一格——这一条是被 E1 逼出来的，见下面。
* **一个主人**。判据在 `events.py`，`cli.py`/`render_live.py`/`render_html.py` 各自只够那只手。

七条新用例（`tests/test_run_with_human.py` 从 6 条长到 13 条、跑起来 15 个，一次请求不发）：真日志的
页眉与 `is_synthetic`/`seat_kinds` 对两次账（有人的桌引两条、没人的桌只引 §十一）、三个出口各印一句
（正反两向：纯替身的那一局三屏都不许印）、`actor_kinds` 那串要在句子里、两局共用的种类不许数两遍、
以及那只手只有一个主人。

十四具刀（十一正、三负）照 `/tmp/mut125.py`（2026-09-25T13:13Z，产物 `/tmp/bat125b.out`，EXIT=0，每具跑
五个文件、134 个用例：真人的那一族 + §十五 那张表的旧证人 + `meta_notice` 那一族的旧证人 + 两个渲染器；
判据是红名单**恰好相等**，跑完按字节 `cmp` 还原，前后基准都 0 红，pyc 前缀每轮换新）：

| 刀 | 红用例 |
| --- | --- |
| E1 判据反了（`"human" not in` 改成 `in`） | 三屏那三条 + 反向对照那一条（共 4） |
| E2 不读页眉、永远不说 | 三屏那三条 |
| E3 不读页眉、永远都说 | 只有反向对照那一条——**只有它能问到"替身桌不许被说成有人在场"** |
| K1/K2/K3 三个出口各自那只手断了 | 一具只红自己那一屏（`replay`/`watch`/`export`） |
| K4 页面把它标成文件损坏（加一个 `⚠`） | `...says_a_person_sat_at_the_table[export]` |
| K5 页面自己抄一遍字面（不走那只手） | "一个主人"那一条 + 导出那一屏（抄的那份没有 `actor_kinds=`，于是"说了有人却说不出的种类"也红） |
| W1 写侧不去重不排序（`game.open_log`） | 真人的那两条 + 读侧三条（共 5）；全量半径 13，见本节末 |
| W2 依据表里 `human` 借用 mock 那一条 | §十五 那三处出口 + 读侧那条（共 4） |
| W3 `seat_kinds` 跨局不去重 | 两局那一条 + 手填页眉的两条（共 3） |
| P1 位置刀：只把导出屏那句挪到 `⚠` 链之后 | 活着（块内第几格不是这一族的主张） |
| N1/N2 负控制：后半句换个说法 / 直播里挪到两个主人之间 | 活着（同上：那串字与那一格都不是主张） |

**三件是量出来的、不是想出来的。** 第一具 E1 跑出来红 **12** 条、E3 红 **9** 条，而多出来的 8 条全在
`test_log_recovery.py`：那 8 条说的是"转录最后一行是截断那句""倒数第二行是编号那句""页眉里第一个 `⚠`
就是它"。这不是那 8 条太脆，是我把新句子挂在了它们**已经认领的格子里**——挪到块首之后 8 条全部转绿，
E1 只剩我自己那 4 条、E3 只剩那 1 条，而 P1 那把位置刀在**最终代码**上确认"块内第几格"确实没有主张
（它红 0）。这套电池里所有 14 具的位置读数都是 0。

第二件：W3 第一轮对真日志那一组是**等价变异**——我预期它弄红单局那条读侧用例，实际一条不红，红的是
另外两条手填页眉的用例。`game.open_log` 已经在每一局的页眉里去了一次重，单局摊平永远没有重复可去；
只有把**两局**真日志放到同一张桌上才问得到那件事。补的那条同时把 W1 的预期顶宽了一条（写侧不去重会让
那张桌的种类表多出六个 `mock`）。这就是 `#123` 的 K2 那一课第二次兑现：预注册的红望必须点名那几条用例。

第三件是电池脚本自己的坑，`#106` 的续集：`-rf` 会把非 ASCII 的参数 id **转义**打印（`缺号` →
`\u7f3a\u53f7`），按空格切回来的名字带着字面反斜杠，于是既对不上"恰好相等"、也对不上位置读数——那
8 条里就有三条是参数化用例。改成按正则整段取再解回真名，并且加了一道"名单条数 ≠ pytest 报的条数就
 abort"。同轮还抓到这台脚本自己的一具假证据：全量爆炸半径那一跑写在 `finally` **之后**，量的其实是
已经还原干净的树——那一行报的是 `2 failed, 941 passed`，而那两条红是当时还没改的文档计数闸门，和这具
刀无关。于是"写侧去重只伤真人那一族"既看着像已被全量否证、又看着像已被全量确认，两种读法都免费。挪进
变异窗口里重跑才是真数：**13**，比上表那一格多出 8——多出来的全在批次与指标那一族（四格在
`test_batch_live.py`：臂级分母、端点没答的那一臂、比较表、逐阶段分解；`test_m3_gate.py`、
`test_golden_game.py`、`test_soak.py` 各一格）。页眉那串不去重伤到的首先是**机器的分母**，不是给人看
的三屏。

**这一片没有动的**：`#127` 那格原样（已由下面 `#127` 那一节接掉；当时卡片上"代答一次"与实测"再问一遍"仍对不上，`retry_note` 在真人
这条腿上零读者）；`['human','llm']` 那张混合桌仍然没有用例（端点唯一原因）；`--human` 那一席的
`parse_human_line` 有一处量出来的毛刺——「票 5 号 他昨晚那一刀」会把「号」留在 `speech` 里（座位号
与"号"之间有空格时才犯，`票5号`、`投票 5号` 都干净），账记成 `#128`，下一节就是它。

### 座位号和正文之间那道边界：`票 5 号 …` 不再把「号」留在发言里，`#128`

动手之前先把毛刺的面量全。2026-09-25T13:18Z 起在 HEAD 的代码上一行一行打出来，三种写法犯的是同
一个错：

```
票 5 号 他昨晚那一刀没有道理   →  target=5, speech='号 他昨晚那一刀没有道理'
票 5，号外的事回头说            →  target=5, speech='，号外的事回头说'
票 5 号位 你先说 / 票 5 位 …    →  同一个位置上多一个字
```

那串字要落进日志、上复盘页——人看见的是自己话开头多了一个字。所以缺口不是「号」这一个字，是
**座位号到正文之间那段边界没人负责切**：`_CN_SUFFIXES` 里的单位只在紧贴数字时才生效（隔一个空格就
不认），切完剩下的句读也没人管（`parse_human_line` 收尾只 `strip()` 空白）。只修其一会得到两种新的
怪相，所以两片各有一条证人、各有一把刀。

四个决定：

* **「5 号」和「5号」是同一次引用**，`cut = tail.lstrip(" \t")` 之后照旧要过"单位后面是不是边界"
  那道检查。差别只出现在「票 5 号码是我的」这种行上：「号」后面还接得上字，它就不是单位而是下句话
  的开头。「行尾正好收在单位上」（`票 5 号`，没有正文）单独算一种边界——那是唯一没有下文可判的写法。
* **切走的是边界上的填充，不是"句读"这张整表。** 第一版修法直接 `tail.lstrip(_SEPARATORS)`，于是
  「指控 3 「他是狼」」从 HEAD 的 `'「他是狼'` 变成 `'他是狼'`——两个引号都没了，比修之前更坏。
  13:39Z 实测到这一格才分成两张表：`_SEPARATORS` 回答"这个字符能不能待在边界上"，
  `_BOUNDARY_CHARS` 回答"那个人打的字从哪里开始"，开括号只答前一句。
* **单位表里 `号位`、`位` 先前一个字都没被测过**（`_CN_SUFFIXES` 换顺序谁都发现不了），补在同一格
  里，配 A5 那具负控制。
* **紧贴写法那一支被删了**：`_lead_seat` 原来对"数字后面紧跟单位"另开一支，而它和隔空格那一支在
  同一个检查上失败（实测 `票5号他说` 改前改后都是 `None`）。这一片没为这次删除下刀——没有行为差异
  可红，就是等价改动的定义。

四条新用例（`tests/test_human_seat.py` 那一片从 12 涨到 16，全在本地、一次请求不发）：隔空格的单位
三种写法各一条断言、行尾收在单位上、「号码」那把刀的反例、开括号那一格。

九具刀（五正、三负、一探针）照 `/tmp/mut128.py`（2026-09-25T13:52Z，产物 `/tmp/bat128d.out`，EXIT=0，
每具跑五个文件、79 个用例：这一族 + 真人接进引擎那一族 + §十五 那张表 + 两个 import 过 `human`
的旧证人；红名单**恰好相等**，按字节 `cmp` 还原，前后基准都 0 红，pyc 前缀每轮换新）：

| 刀 | 红用例 |
| --- | --- |
| A1 空格那一格不认（`cut` 不越过空格） | 「5 号」那一条 |
| A2 单位后面的边界不看（跳过一个空格就吞单位） | 「号码」那一条 |
| A3 完全不切（回到只 `strip()` 空白） | 逗号那一条 + 开括号那一条（全量半径 2，见下） |
| A4 行尾那一格不当单位（`len` 特例挪错一格） | 「5 号」（无正文）那一格所在的条 |
| A8 两张表并回一张（切的时候连开括号一起啃） | 开括号那一条 |
| A5/A6/A9 负控制：单位表换顺序 / 只认空格不认制表符 / 排除表换顺序 | 活着（那三样都不是主张） |
| A7 探针：删掉「数字后面紧跟非分隔符 ⇒ 整行不算指令」那一支 | **红 0 条＝这一支没有证人** |

**半径那一跑要先有同口径的基准。** 上一轮 A3 报的是"半径 2 条"，其中一条红在文档计数闸门上——那是
我自己把条数写成了 14（实际 15），和变异体无关；当时的五文件基准看不见它，于是既有红被记成了影响面。
这一轮先在整个 `tests/` 上取基准（947 全绿、0 红），A3 在变异窗口里量的那条半径才是真数：**2**，两条
都在 `test_human_seat.py` 里，别的模块没有一个读这段落盘文本。同轮的另一具 A8 半径不单独量：A3 的
切法是 A8 的子集，"还有谁读这段文本"这一问，宽的那具问得完。

A7 是这一片留下的**登记项而不是缺陷**：那一句严格性判据（数字后面紧跟的不是分隔符 ⇒ 整行不算指令）
今天一个用例都弄不红。它挡的行是「票5号他说」，而这一格两头没对上：决策卡片上写着「加座位号
后面可以跟你要说的话」（`human.py` 的 `decision_card`），解析器却把这种紧挨着的写法整行拒掉。合起来
连同下面那一格记成 `#129`。

**量出来的另一格，不是这一片弄出来的**：`parse_human_line` 在 `_lead_seat` 之前对整行做
`rest.strip(_SEPARATORS)`，那是从**行尾**啃字，于是每个人打的句子末尾那个标点从来没进过日志
（13:39Z 实测，改前改后一样）：

```
票 3 他昨晚没动手。   → speech='他昨晚没动手'
投票 5 先听听吧！      → speech='先听听吧'
弃票 就这样。          → speech='就这样'
指控 3 「他是狼」      → speech='「他是狼'
```

前三条读起来无害，第四条说明它不是无害而是**在编辑那个人说的话**：丢的是引号，成对符号的另一半还
留在正文里。这一片没动它——动它要重写"整行的首尾"和"边界"两处共用一张表的那个决定，而那条决定
正是 A8 对着的东西。`#129` 一并接：行尾那个标点、卡片承诺与解析器的差、A7 那支没有证人的严格性（三格已由下一节接掉）。

**这一片没有动的**：`#127` 已由下面那一节接掉；`#129` 三格（上表，已由下一节接掉）；混合桌仍然端点阻塞。`coerce_seat` 认 `P3`
（实测返回 3），而 `_lead_seat` 不把它当座位引用——真人打「票 P3」读出的是 `target=None` 加一句
`P3`。卡片没有教人这么打，所以它是文档里的一处口径差，不是缺陷。

### 那一行字的三格账：行尾的句号、卡片印的例子、词表漏掉的那个动作，`#129`

`#128` 收尾时量出来的那三格，这一片一次接掉。三条都不是"引擎算错了"，是**一个人打字的那一行
进了系统之后，被人替着改了、被卡片许空了、或者根本打不出来**。

**第一格：行尾那个标点从来没进过日志。** `parse_human_line` 在把剩下的部分交给 `_lead_seat`
之前做的是 `rest.strip(_SEPARATORS)`——同一张表既从行首切边界、也从行尾啃字。2026-09-25T14:26Z
实测（HEAD 与现在各跑一次同一份探针 `/tmp/probe129.py`）：

| 打进去的一行 | 改前落盘的 speech | 现在 |
|---|---|---|
| `票 3 他昨晚没动手。` | `他昨晚没动手` | `他昨晚没动手。` |
| `投票 5 先听听吧！` | `先听听吧` | `先听听吧！` |
| `弃票 就这样。` | `就这样` | `就这样。` |
| `指控 3 「他是狼」` | `「他是狼` | `「他是狼」` |
| `票3他说得对` | `None` | `None`（没动） |
| `票5号他说得对` | `None` | `None`（没动） |

前三条读起来只是少一个句号，第四条才是这一格的真名字：**丢的是引号，成对符号的另一半还留在正文
里**，那是把一个人说的话改成了另一个人会说的话。修法一个字符：`strip` → `lstrip`，边界只在开头，
结尾归打字的人自己负责。

`#128` 那一节里有两处旧读数归这一格管：末尾"这一片没动的"那一段引的四行（`speech='他昨晚没动手'`
那族），和中段那句「两个引号都没了，比修之前更坏」。两句都是**改之前**的实照，按原样留着不改——
上面那张表的右列就是它们的后继，"更坏"那一格今天两头引号都在。

**第二格：卡片上那句「后面可以跟你要说的话」没有例子。** 那是一句关于**怎么打**的承诺，而这一片
之前卡片和解析器之间零读者。现在卡片末尾多一行不缩进的 `示例：票 1 我先记着，回头再说`，证人
不是去匹配那串字，而是把它原样喂回 `parse_human_line`：断言它读得通、动作在这一轮允许集合里、
带一句话、点的名在可点名清单里。例子刻意不缩进——那一屏"这一轮可以答"的边界是两格缩进，例子
进那段就等于给卡片加了一条法官没批的答法。

**第三格是最贵的：`discuss` 在词表里根本没有词。** 接线上的例子时，`test_run_with_human.py` 里
一条读侧用例当场崩了——`RuntimeError: coroutine raised StopIteration`，崩在 `agent.py`，
而根因是 `decision_card` 里那句 `next(zh for … if en == demo)`：`ACT_SYNONYMS` 有 29 个词、覆盖
14 个 act，`ActName` 有 15 个，缺的就是 `discuss`（狼队夜里那一轮唯一的动作）。也就是说真人坐在
狼席上时，卡片给他印的是一个**空格子**，而他除了被引擎代答没有第二条路。两个动作：词表补
`"讨论": "discuss"`（30 词 → 15 act，`get_args(ActName) - set(values)` 现在为空），以及例子的词
不再自己二次查表，改用上面那个循环算好的 `words`——缺词从"崩掉整桌"降级成"那一行没印出来"，
而没印出来是有断言可红的。

新增五条用例（`tests/test_human_seat.py` 由 16 涨到 21）：`test_the_last_character_of_what_a_person_typed_stays_in_his_sentence`、
`test_a_seat_number_that_runs_straight_into_the_sentence_is_refused`（今天绿，红望在电池那一侧，见下）、
`test_the_card_prints_one_example_and_that_example_parses`、
`test_a_wolf_chat_turn_offers_a_word_and_an_example_that_answer_it`、
`test_every_act_the_engine_can_ask_for_has_a_word_the_player_can_type`。

七具刀（四正、两负、一探针）照 `/tmp/mut129.py`，跑了两轮：pass 1 `/tmp/bat129a.out`（写完于
14:37Z，`EXIT=143`——半径那一步之前被中断）、pass 2 `/tmp/bat129b.out`（15:01Z，`EXIT=0`，
七具全判定 + 收尾基线 `194 passed`）。判定行两轮逐字一致：

| 刀 | 结果 |
|---|---|
| B1 行尾又被人啃掉一格（`lstrip`→`strip`） | 红 1 条 = `test_the_last_character_of_what_a_person_typed_stays_in_his_sentence`，两轮同 |
| B2 座位号紧挨正文也算指令（`#128` 的 A7 那一支） | 红 1 条 = `test_a_seat_number_that_runs_straight_into_the_sentence_is_refused` |
| B3 卡片不再印示例 | 红 2 条 = `test_the_card_prints_one_example_and_that_example_parses` 与 `test_a_wolf_chat_turn_offers_a_word_and_an_example_that_answer_it` |
| B4 负控制：例子改取最后一个词 | 活着（`rc=0`）——主张是"打得通"，不是拼写 |
| B5 探针：例子不再优先挑能点名的 act | 活着（`rc=0`）：**登记成新缺口**，见本节末 |
| B6 词表漏回 `discuss` | 红 2 条 = `test_a_wolf_chat_turn_offers_a_word_and_an_example_that_answer_it` 与 `test_every_act_the_engine_can_ask_for_has_a_word_the_player_can_type` |
| B7 负控制：`discuss` 多一个词 | 活着（`rc=0`），pass 2 才跑到的那一具 |

### 半径这一格：名字集作数，时间不作数

全量半径只对 B1、B6 两具量（变异体还在盘上时跑整套 `tests/`）：**B1 = 1 条、B6 = 2 条**。名字集
取自 pass 2 打印的红名单，计数与 pass 1 的 `[k0-all] 1 failed` / `[k5-all] 2 failed` 相互对上。
也就是说这一片改的那段落盘文本只有 `test_human_seat.py` 里那一条读；词表那一格有两条，其中一条
是这一片新加的"每个 act 都有能打的词"。

同一轮里那个 63 → 292 秒（4.6 倍）**不发布**，理由不是它算错而是它不可归因：同样这 194 条七文件
范围，pass 1 开跑时 1.38 秒跑完，pass 2 开跑时 20.33 秒——机器自己的基准在两轮之间挪了 15 倍
（当时 `vm.loadavg` 59.7，占 CPU 前列的是浏览器与容器，没有一个是这套测试）。在这种斜率上，一具刀
的 4.6 倍和外面进程的 15 倍是同一个形状。要坐实"缺一个词会拖慢测试"，得在负载回落后把三档各复跑
取中位数——这一格记在提交说明里，不在结论里。

**B1 那一格两跑对不上，多出来的名字已定性，不算读者。** pass 2 的 B1 红名单比 pass 1 多一条
`test_a_finished_batch_prints_its_line_and_exits_0`（批次收尾那一行，住在
`tests/test_loopback_endpoint.py`）。单独复跑把它咬住后拿到的失败文本是
`rc=1 out='批次 -> …（2 局日志，seed0=11，canary INVALID_DRIFT，终态 INVALID_DRIFT）…'`，
而那份批次目录留下的 `drift.md` 写着 `异常探针：latency×0.14`、中位延迟比 0.137、**五个探针的
批首答案与批尾答案逐字相同**。`report.py:315` 那条 `latency×` 追加与 `report.py:310` 的 `:answer`
是两回事：这条红走的是延迟闸门，不是内容。而 `parse_human_line` 在生产链上只有一个调用者
（`actors.py:361` 那一行），批次侧没有 HumanActor——一具改人话解析的刀没有路径去动端点延迟。
所以半径发布为 1，这一条记成"canary 的延迟通道对机器负载敏感"的又一例：同一只手、同一个理由，
也是上面那个 4.6 倍不发布的理由。**一具刀弄红一条、而我说不清它为什么红的，不算证据。**

**B2 是把上一片的登记项结掉的。** `#128` 那表里 A7 红 0 条＝"数字后面紧跟的不是分隔符 ⇒ 整行
不算指令"这一支没有证人；这一片给它补了 `test_a_seat_number_that_runs_straight_into_the_sentence_is_refused`，
同一具刀现在必须弄红它——红了才说明那条严格性判据真的有人守，也才说明它是**选择**而不是疏忽。
选它的理由和 #128 同一句：读错了要落进日志、永远留在那条发言里；读不出来只是再问一遍。

**B5 开出来的新缺口**：例子挑哪一个 act 印（优先挑能点名的那个）现在没有证人——把那个 `not`
加回去，194 条一条不红。这一片的例子只在"恰好是 `vote`"这一种挑法下被测过。

**这一片没有动的**：`#127` 那格原样（已由下面 `#127` 那一节接掉；当时卡片上"会被引擎代答一次"与实测仍对不上：出厂
`max_repair_retries=1` 下第一次打回是再问一遍，第二次才代答），以及 `discuss` 带着 target 落盘
却没人读（`legality.py` 对 targetless act 压根不看 target，`compress.py` 的狼聊那一支只印
`_said(p)`）——记成 `#130`。

### 被驳回之后的那一屏印什么：拒绝的码走到人这一侧，`#127`

`#122` 到 `#129` 一路把"坐得下一个真人"接上了桌，但那一席在被法官打回之后看到的是**同一张
卡片**。机器侧从来不缺账：`agent.py` 的循环对每种座位都记一条拒绝，人打的那一行也一样进
`attempts[]`。缺的是人这一侧——那份拒绝理由渲染出来只有一个读者：`schema.errors_to_prompt_lines`
把它写成 C5 区、进模型的 prompt，末尾那句是「只输出一个 JSON 对象，不要任何解释文字。」。
拿它给人看等于要求一个人吐 JSON。所以这一片走的是"**码走一路，各受众自己印**"：
`TurnContext.refusal`（`actors.py:70`）带上闸门那一轮过不了的码，`human.decision_card` 印成人话，
模型的梯子一个字不动——第二条规则来源才是这一片最该避免的东西。

第二屏顶上那一行长这样（3 号位坐着一个真人，那一轮法官只 grant 了投票，他先打了「毒 5 …」；
整屏由 `tests/test_human_seat.py` 那个 `_turn` 夹具真跑一遍印出来，不是手抄的样式）：

```
法官打回：「毒」这一轮不能答。
```

用的必须是他自己打的那个词，不是闸门内部的英文码：`_act_word` 从 `ACT_SYNONYMS` 反查，取的是
卡片"这一轮可以答"那一块同一个派生的第一个词；查不到的动作原样印英文，认不出的码整条原样印出去
——一条看不见的拒绝等于一次没有理由的再问。

#### 卡片那句承诺：三行真值表

`#129` 登记过卡片上「换成别的会被引擎代答一次，你那句话仍然算你说的」是一句没有证人的承诺。这一
片把实测的三行钉住，并按闸门自己的 `HARD_PHASES` 分岔——分岔依据来自 `legality.py`，不是第二份
口径：

| 行 | 场景 | 闸门走的分支 | 落盘 | 证人 |
| --- | --- | --- | --- | --- |
| R1 | 软相位、换掉法官指派的动作 | 打回再问一遍，`_only_the_label_was_refused` 为真 → 留他的动作、写 `fallback=1` | 他打的那个动作 | `test_the_assigned_line_tells_the_truth_about_a_soft_phase` |
| R2 | 硬相位、答本轮没 grant 的动作 | 打回再问一遍，两把账都在 → `default_action` | 引擎的默认动作，他的话只活在 `attempts[]` | `test_the_assigned_line_tells_the_truth_about_a_hard_phase` |
| R3 | 软相位、答本轮没 grant 的动作 | `act_not_allowed` 进 `flags` 不进 `violations`，`ok` 保持 True | 照他打的收，一次都不多问 | `test_an_out_of_set_act_in_a_soft_phase_is_taken_as_typed` |

R3 是**选择**不是疏忽：软相位放行是 M4 那把测量留下的证据通道，钉住它是因为电池里有一把专门把它
拧硬的刀。R1、R2 两行共用"再问一遍"这一步，代价却不同，所以卡片上的那句话必须按相位分岔；
第一屏就得说清，不能等他坚持两遍之后才发现句子被换掉了。

被再问那一屏的另一半是"不许有无中生有"：`assert "打回" not in cards[0]`。为此 `agent.py` 里那个
`Verdict(ok=False, violations=["no_attempt"])` 的哨兵被换成空表——同一个列表如今既当内部记账又当
屏幕的输入，一个"还没有任何事发生"的记号放在里面，早晚会被印成一句「法官打回：no_attempt」。
`ok=False` 本身仍然标着那条从没问出口的分支，`_only_the_label_was_refused` 对空表和对哨兵的判定
一样（`bool([])` 为假），全仓库再没有第二个 `no_attempt` 的读者（改前按精确字符串扫过：一处，就是
那行初始化；改后 `grep -rn no_attempt` 在 `src/`、`tests/`、`scripts/` 下 0 命中）。

#### 电池：七具刀 + 一具负控制

照 `/tmp/mut127.py`，跑七个文件：`tests/test_human_seat.py`（这一片的五条新证人）、
`test_run_with_human.py`（真人接进引擎那一族，读的是落盘文本）、`test_info_isolation.py`、
`test_actor_contract.py`、`test_wiring.py`（都读过 `decision_card` 或 `HumanActor`）、
`test_agent_turns.py`、`test_legality.py`（那条循环和闸门本身的旧读者）。带旧读者不是贪覆盖：判据
是红名单**恰好相等**，一把只该弄红卡片那三条的刀若顺手红了替身桌的旧用例，那张表就是假话。
窄基准 216 个用例 0 红，全量基准 957 个用例 0 红；跑了两轮（`/tmp/bat127a.out`、
`/tmp/bat127b.out`），逐具判定行两轮**逐字一致**：

| 刀 | 结果 |
|---|---|
| M1 拒绝的码不再走（`refusal=()`） | 红 3 条 = 卡片那三条证人，一轮不差 |
| M2 那一屏报的是闸门的英文码，不是他打的词 | 红 3 条 = 同一批（`_act_word` 退化成原样返回） |
| M3 不认识的码被静默丢掉（删掉 `else` 那一支） | 红 1 条 = `test_a_code_the_card_has_no_words_for_is_still_shown` |
| M4 软相位一律拧硬（`strict` 恒真） | 红 8 条，该在的两条在里面（这一具是 `kill-in`：伤的是闸门不是卡片） |
| M5 卡片退回那一句旧承诺 | 红 2 条 = 软硬两条真值表证人 |
| M6 没有账也印「法官打回」（无中生有） | 红 1 条 = `test_the_second_screen_names_the_word_the_gate_sent_back` |
| M7 把 `no_attempt` 哨兵放回拒绝名单里 | 红 1 条 = 同一条（第一屏凭空账印出一句假话） |
| C1 负控制：「法官打回」那一行挪到卡片末尾 | 活着（`rc=0`、红 0 条）——三条证人都按整屏取子串，位置没有主张 |

全量半径只对 M1、M4 两具量（变异体还在盘上时跑整套 `tests/`，减掉同一窗口里先取的全量基准）：
**M1 = 3 条**（就是卡片那三条，"拒绝的码只有卡片这一个读者"这句主张由它撑）、**M4 = 8 条**，
名单是 `test_a_soft_phase_flags_the_same_violation_instead_of_refusing_it`、
`test_an_out_of_set_act_in_a_soft_phase_is_taken_as_typed`、
`test_no_engine_function_declares_a_parameter_nobody_reads`、
`test_the_assigned_line_tells_the_truth_about_a_soft_phase`、
`test_the_default_action_is_itself_legal[empty]`、
`test_the_impossible_percept_flag_fires_on_the_measured_failure`、
`test_the_over_long_flag_the_gate_writes_is_the_one_the_renderer_reads`、
`test_without_known_ids_an_unknown_id_is_only_flagged`。这一片没有给那八条逐条归因（要归因得给每
一条配一具只动那一格的刀），M4 也因此只按 `kill-in` 判；能说的是它们住在哪儿：除去这一片自己的两
条证人，其余那六条分别住在闸门的两个旧读者文件里（`test_legality.py` 与 `test_wiring.py`），没有
一条是卡片的读者——`strict` 恒真伤的是分流本身。第一轮那份打印把名单切成前六条、计数仍报八，两处
对不上，所以这一版把 `radius[:6]` 改回整表：半径的口径是"点名到每一条"，不是一个数。

收尾基线 216 个用例 0 红、`extra-red` 为空、每具刀改完都 `cmp` 回原字节、残留 `.pyc` 无、
`EXIT=0`。

#### 这一片没有动的

`#130`（真人打「讨论 5 …」时那个 5 落盘却没人读）与 `#131`（表格形状只靠写盘脚本自查）都已由后面的
小节接掉、`#126`（行号闸门不扫 `tests/`）一张票原样。真人和模型混坐那一桌（`actor_kinds=['human', 'llm']`）
仍然要等端点：这一片的五条证人全在本地，一次请求都不发。

### 文档里每张表的格子数有人一张一张数：`#131`

`#127` 那一轮的写盘脚本里带了一份"表格逐张数格子"的自查，跑完就消失了——那是这一族唯一的读者，
所以有人直接在 README 里手改一行表格时没有任何东西会红。这一片把那份自查搬进闸门：
`tests/test_doc_tables.py`（8 条、跑起来 8 个用例）。判据只有渲染器真正用的那两条：表头行与分隔行
成对出现才算一张表，两者的列数必须相等；每一条数据行的列数等于表头。

为什么形状值得钉：一张列数不对齐的表在 GitHub 上**不报错**——多出来的格子被丢掉、少掉的格子留空，
读的人拿到的是"一张写着对的话的错表"。这是这一族最便宜也最安静的腐烂。

真语料今天一次就过：README 加 docs 里 90 张表、679 条数据行，列数全部和表头一致。三处诚实要说清，
因为它们的牙目前只长在合成夹具上：

* **围栏里的东西一张都不算**（文档会把坏形状包进围栏给人看长什么样）。但今天的语料里没有这一族：
  把围栏算进来仍然扫到 90 张 / 679 行、0 处报告。
* **格子里的转义竖线不分列**（一格里要写竖线只能写成反斜杠加竖线）。同样是合成夹具的牙——README
  和 docs 里一个转义竖线都没有。
* **分隔行的列数**：90 张表的分隔行今天都等于表头，这一格换不来任何新读数（`#105`、`#107` 写过
  同一句话）。它买的是"以后有人手改表格时，坏的那一行会被点名到行号"，报告里带上实际几格对表头
  几格——一张二十行的表不该要人自己数是哪一行歪的。

重跑这一片：`PYTHONPATH=src .venv/bin/pytest tests/test_doc_tables.py`（8 条，一眨眼的工夫，不发请求）。

#### 电池：四具刀 + 一具负控制

这一片没有新的生产代码——闸门整个住在测试文件里，所以刀全下在闸门的四个判据上。窄名册是两个文件：
新证人加同一族文档闸门的邻居 `tests/test_doc_citations.py`（它不 import 新模块，五具刀下都应当活着；
它红了就是假归因）。基线：窄名册 33 passed 2.83 秒、全量 965 passed 58.20 秒，全量基准上没有红着的
用例。

| 刀 | 结果 |
| --- | --- |
| K1 只报"多出来"的那一列（把不等于改成大于） | 咬住，红名单恰好 `test_a_missing_cell_in_a_data_row_is_reported_too` |
| K2 围栏里的行也算表 | 咬住，红名单恰好 `test_pipe_lines_inside_a_fence_are_not_tables` |
| K3 分隔行的列数不查 | 咬住，红名单恰好 `test_the_separator_row_has_to_have_as_many_columns_as_the_header` |
| K4 格子里的转义竖线当分列 | 咬住，红名单恰好 `test_an_escaped_pipe_inside_a_cell_does_not_split_it` |
| C1 负控制：报告不再排序 | 活着（33 passed）——报告的次序没有任何一条用例主张过 |

四具刀每一具都是 1 failed、32 passed，extra-red 为空：一条刀只弄红一条用例，而那条用例的 docstring
里就写着它钉的是哪个判据。K2 另外量了一次全量半径（变异体还在盘上时数的）：**1 条**，正是它自己那条
用例——上面那句"这一族没有第二个读者"是量出来的，不是推断的。

K2 的刀口要特别说明，不然这一具是**钝刀**。第一版只把判据那一行的 `fence` 换成 `False`，量出来的
结果是零处报告、和闸门原样一模一样：`fence` 变量还在、围栏那一行照样 `continue`，围栏里的行永远走
不到那扇门。改成同时把夹具里那两行围栏符号抹掉，这一具才咬在"围栏不成表"这件事上——和 `#127` 那
一轮印错半径是同一族教训：**写下来的数要来自真的那一次**。逐刀还原按 `cmp` 验过字节相同，残留字节码
为零，整个电池两分四十五秒。

#### 这一片没有动的

`#130`（真人打「讨论 5 …」时那个 5 落盘却没有任何一个读者）原样。`#126`（行号闸门不扫
`tests/`）也原样，但这一轮顺手把它的代价量出来了：tests 与 scripts 里 19 处行号引用，按最朴素的
对法只有 5 处点得到东西、2 处指向的不是 `src/` 里的文件、12 处那一行找不到名字——其中 `metrics.py`
的 75 行指的是一行空白，那是真的漂了。收这一族要改的句子比想象多，先记下规模再谈怎么收。

### 那一格落的席位终于有人读了：`#130`

真人在狼队私聊那一栏打「讨论 5 今晚刀他」：`#128` 把那个 5 解析成 `target`，`agent.py:352` 给每一条
决策无条件写它，`events.py:72` 的形状表也确实给 `wolf_chat` 声明了这一格。三段都成立，两头却没人接。

* **落盘之前没人管**：席位校验原本长成 `if action.target is not None and action.act not in
  TARGETLESS_ACTS:` 这个样子——`discuss`、`defend`、`listen` 这些"本不带席位"的动作带着一个号码过来，
  闸门看都不看就放行。打「讨论 9」而 9 号不在合法名单上，日志里落的就是 9。
* **落盘之后没人读**：`compress.py:109` 的 `WOLF_CHAT` 分支整行是
  `return f"[{tag}] 狼队私聊 {e.actor}号：{_said(p)}"`。那一格从不出现在任何一行给人看的话里。

为什么这一格躲过了普查：`test_payload_shape.py` 数读者是按**键名**在 `src/` 全局数的，`target` 在隔壁
`SEER_RESULT` 分支被人读着，于是 `wolf_chat` 的这一格算绿。普查要改成逐格（kind 与 key 一对）才有牙，
这一族另开 `#132`（票里写清了量出来的规模和打算用的判据）；这一片只把自己那一格补上读者。

改了两处，各一处判据：`legality.py:95` 的条件收成 `if action.target is not None:`——数字是谁打的都是
一个关于桌面的主张。不合法的走这套已有的两级严重性：硬阶段（夜间、投票）拒掉重问，软阶段（白天发言）
只打旗不噤声。`compress.py` 的 `WOLF_CHAT` 分支在说话人后面印 `（指 N号）`。被驳回之后的那一屏不用新
代码：`#127` 那条链早就把 `target_not_legal` 翻成了人话（`human.py:141` 那一支，印出来是「N 号这一轮
点不到」），缺的只是让这个码真能为 targetless 的动作发出来。

证人五条新的、一条改判：`tests/test_legality.py` 里两条（硬阶段拒、软阶段只打旗），`tests/test_wiring.py`
里一条（渲染出来的那六个字，正反两格都钉：没有席位号时那一行不许凭空长出一对括号），
`tests/test_human_seat.py` 里两条真驱动 `HumanActor`——一条把「讨论 5」一路走到落盘再走到渲染，一条先打
「讨论 9」再打「讨论 5」，要求第二次屏幕上印得出「9 号这一轮点不到」、最后落盘的是 5。
`tests/test_render_live.py::test_revealing_a_seat_shows_that_seat_and_nobody_else` 是**改**的：它把
`[e12]` 那一行整行钉死，期望串跟着长了那六个字。它红不是回归，是这条旧证人本来就在替这一格作保。

金样本那两条狼话里，被指的席位正好也在正文中说过一遍（「刀3号」），所以那一行看着像重复；这一格的
价值在正文不提席位号的那一桌——`tests/test_wiring.py` 的新证人用的正是「今晚动手。」加一个 5。

#### 电池：三具刀 + 一具等价变形

窄名册四个文件（新证人所在的三处，加把整行钉死的 `test_render_live.py`），基线 179 passed 3.21 秒；
全量基线 970 passed 57.86 秒，没有一条是预先红着的。三具动共用代码的刀各量一次全量半径。

| 刀 | 结果 |
| --- | --- |
| L1 把 targetless 的 target 校验退回去（HEAD 的形状） | 咬住 3 条：`test_legality` 那两条加"第二次屏幕"那条 |
| L2 把软阶段的那一格也判成硬的（`strict` 换成恒真） | 咬住 4 条，其中三条是旧证人 |
| C1 渲染不回显被指的席位 | 咬住 3 条：渲染证人、真人走到读者那条、观众模式钉整行那条 |
| C2 负控制：`if tgt is not None` 换成等价的 `if tgt` | 活着（179 passed）——拼写没有主张，座位号里没有 0 |

三具刀的全量半径（变异体还在盘上时数的）各自恰好等于窄名册里的红名单：3、4、3 条。extra-red 为空、
逐刀 `cmp` 还原字节相同、残留 `.pyc` 无、收尾基线 179 passed，整个电池四分二十秒。

L2 那一具要单记一笔：**第一跑的预期写的是 1 条，实测 4 条**，脚本按口径非零退出（16:40–16:44Z 那一
跑）。重读那三条旧证人的断言才看懂——它们各自 `assert v.ok`，而递进去的 `accuse` 都点了一个当时不合法
的席位，所以"发言永远是软闸门"这句话一直就压在这一格的 `strict` 参数上。这一格有旧读者，还是三个不同
文件里的。按断言改完名册重跑（16:45–16:49Z），四具全部符合；改的是预期，没有改任何一条用例的断言。

#### 这一片没有动的

`#132`（普查按键名数读者，看不见"这一 kind 的这一格没人读"）是新开的票，本片的第二半——把
`src_load_sites` 改成逐格——不在这里做：先量代价再动刀，那一族今天有多少格没读者还不知道。
`#126`（行号闸门不扫 `tests/`）原样。真人和模型混坐那一桌（`actor_kinds=['human', 'llm']`）仍然等
端点：这一片的证人全在本地，一次请求都不发。

### 发牌那一行现在念得出队友：`deal.teammates` 有了读者，`#133`

`#130` 那格的目标是"落了盘没人读"，这一片是同一族里的另一格：`deal.teammates`。

写侧一直是通的。`game.py:134` 给每只狼落一份不含自己的队友名册（`teammates=[x for x in wolves
if x != seat] if role == "wolf" else []`），`events.py:68` 的形状表也把 `teammates` 这格声明了，
`info.py:81` 甚至备着一个 `Percept.teammates()` 专门读它。断的是最后一段：`compress.py` 的 DEAL
分支只印到
`你的身份是 wolf。`，而 `Percept.teammates()` 全工程零调用者——那把备好的钥匙从来没插进过任何一
扇门。代价不是"日志里多一格死字段"，是牌桌上一个事实：狼不知道队友是谁。

量出来的那一格（`/tmp/probe133.py`，17:02Z，12 局 mock）：74 次问狼里 **12 次** 的 percept 里
认不出任何队友，而分布不是随机的——**每局恰好一次**，就是 `phases.py:137` 那个先开口的 proposer。
他在任何一条狼队私聊落盘之前就得决定今晚刀谁，而他唯一的队友线索本该是私聊行。这一格不是"偶尔
少一条信息"，是每一局的第一次提案都在盲打。

#### 这一片动的两处

两处落点，各自有证人，因为它们是两条不同的主张：

* `compress.py:100` 的 DEAL 分支：`mates` 非空时行尾接 `，队友是 2号、4号。`，空和缺键都退回旧
  那一行。写在这里而不是另起一条事件，是因为 `render_line` 是唯一一个渲染器，而念得到发牌事件的
  那几台机器——`--god` 时间线、直播、复盘 HTML、真人屏上的局况——都从它取句子。一次改动四台都
  念得到，也不需要第二条取数据的路。
* `human.py:180` 的那一句 `mates = sorted(ctx.percept.teammates())` 起了页眉：狼那一席的卡片在
  `轮到你了：1 号（wolf）` 下面多一行 `你的队友是 2号、4号。`。这一条不是上一条的重复：真人那一屏
  的局况只有 `SCREEN_TAIL` 等于 12 条，打到第 13 条事件之后发牌那行就从他眼前滚走了，而他的队友
  不会因此变少。这一格也是 `Percept.teammates()` 的第一个真读者。

证人在 `tests/test_wiring.py`（渲染那一行：狼一条、平民缺键一条、平民空列表一条）和
`tests/test_human_seat.py`（卡片页眉那条，加一条"平民那一屏不得多出『队友』二字"的反证）。反证
那条落笔时就绿——它的杀伤力由 V3 那具刀补上：把 `if mates:` 拆掉，红的第一条就是它。

#### 这一段梯子上有一扇门不在这一片里

写完上一条才去核"模型那一屏念不念得到"，答案是**不念**，而且原因不在 `render_line`：发牌事件进
prompt 的路有两条，两条都是关的。B 段那条走 `compress.py:214` 的 `chronicle()`，它只留
`visibility == "all"` 的事件——发牌是私发，进不来。C 段那条走 `assemble.py:209` 的私有信息块，
它的条件里明写着 `e.kind != Kind.DEAL`（`assemble.py:209`），因为模型的身份由 `assemble.py:204` 那一句
`你是{percept.seat}号。你的身份是：{seat_role}。{roster}` 负责，不需要再念一遍事件。

所以本片的四处读者是**离线视图 + 真人屏**，AI 狼在第一夜仍然不知道队友——那一句"你的身份是"背
后没有名册。`#134` 那一节接着做的正是这一格：接法和卡片页眉同构（在座位行里接 `percept.teammates()`），
要另量的是前缀稳定与金丝雀的方向性：`tests/test_info_isolation.py` 里那条
`test_a_seat_sees_its_own_role_and_nobody_elses` 现在就能绿，是因为身份串走的是座位行而不是事件
渲染——名册接进同一句不违背它，但 teammates 是席位号、没有 canary 形状，得另配一具"把名册印给
全体席位"的反向控制刀，否则"过滤器没跑"和"断言恒真"长得一样（`#129` 同一族）。这一格写在这里而
不是默默做掉，是因为"共用一个渲染器"这句话的半径必须点名：四台机器念得到，第五台（模型）不在这
条梯子上。

#### 电池：五具刀 + 一具负控制

窄名册三个文件（`test_wiring.py`、`test_human_seat.py`，加 `#124` 那族金丝雀
`test_info_isolation.py`），基线 148 passed（2.93 秒）；全量基线 973 passed（55.07 秒），没有一
条是预先红着的。V2、V3、V4 各量一次全量半径。

| 刀 | 结果 |
| --- | --- |
| V1 DEAL 分支不印名册（退回旧行为） | 咬住，名册恰好那一条 |
| V2 DEAL 分支无条件印（平民被凭空配一支队伍） | 咬住，两条（见下） |
| V3 卡片那一行无条件印（平民那一屏多出「队友」） | 咬住，名册恰好反证那一条 |
| V4 卡片那一行挪到卡片末尾（不再在页眉） | 咬住，名册恰好页眉那一条 |
| V5 名册不排序（写侧给 [4,2] 就印成 4号、2号） | 咬住，名册恰好那一条 |
| C1 负控制：卡片那一行换个措辞（「队友名单：」） | 活着（对：措辞没有主张） |

三具动共用代码的刀，全量半径（变异体还在盘上时数的）分别是 2、1、1 条；V4 那个数就是这具刀第三
跑才修好的判据——第一跑它 rc 0、半径 0 条（越界 `insert` 在那一刻等于 `append`，落点没动，钝
刀），第二跑半径 25 条（括号位置写错把平民那一屏整个变空串，坏刀），第三跑半径 1 条且红的正是页
眉那一条。extra-red 为空、逐刀 `cmp` 还原字节相同、残留 `.pyc` 无、收尾基线 148 passed，整个电
池 3 分 26 秒。

V1 与 V2 打中的是同一条用例的两条腿（`test_the_deal_line_says_who_is_on_your_team` 里正证一条、
反证两条），所以名册相同是预期而不是漏刀：渲染那一格只有一个出口，正反两腿只能住在一处。V2 的证
人比 V1 多一条则是这一跑才量出来的：卡片上的局况走的正是 `render_line`，所以 DEAL 分支无条件印
会顺着梯子漏进平民那一屏，`test_a_villager_seat_is_not_handed_a_team` 于是也是它的证人——这一条
反倒是好消息，它说明"卡片不经过渲染梯子"是我先前的错判。

#### 这一片没有动的

`state.py:130` 的 `teammates_of` 还在原地，而且它和 `Percept.teammates()` **不是同一个判据**：
前者按 `alive_seats` 过滤（死狼不算队友），后者念的是发牌那一刻写下的名册（死了也还在名单上）。
卡片印的是后者——那句"你的队友是"说的是发牌，不是当前存活。两份实现谁该活下来是 `#81` 那族
"没有读者的具名函数"的账（`teammates_of` 目前只有 `tests/test_rules.py` 三个读者），不在这里顺
手统一。`#132` 的普查也仍然看不见这一格：它按键名数读者，`info.py:84` 那个 Load 点在开了
`human.py` 这一扇门之前就已经把 `teammates` 记成"有读者"了。`#126` 原样。真人和模型混坐那一桌
仍然等端点。

### 名册这一回走到了模型那一屏：`#134`

`#133` 那一节末尾点名的那格：发牌行印出了队友，可模型看不见它。这一片接上这一段。

落点只有一个——`assemble.py:204` 的座位行，`{roster}` 接在"你的身份是"之后：

```
== 你的座位 ==
你是1号。你的身份是：wolf。你的队友是 2号、3号。这条信息只有法官和你看得到。
```

**为什么不新开一块，也不去开那两扇关着的门。** `chronicle()` 只收公开事件、私有块明写着
`e.kind != Kind.DEAL`（`assemble.py:209`），这两条过滤本身是对的：身份由座位行负责，不该再从
事件里念一遍；把 DEAL 放进私有块会让"你的身份是"这件事有两个写者。所以这一片改的是**名册**这个
字段走的路，不是事件走的路。而选座位行还有一笔账要算：`block_tokens` 换东家的判据是"这一串以
`== ` 开头"，`你的座位` 这一块本来就不在 §5 的五个预算格里（B0/C1–C4 各有数，它没有），把十几个
字加在它身上不挤占任何一格——加进 C2 就要从主张卡那里扣。

正证的两条腿跑在真装配器上：`tests/test_info_isolation.py` 里那个 `_ctx_for` 走的就是产品入口
`assemble.assemble`，不是测试自己拼字符串，所以"三席狼各自读到升序的、不含自己的名册"这条主张量的
是模型真的会收到的字节。反证那条钉的是另外六席：整个 prompt 里不出现「队友」二字——只查"狼看得
到"会放过一具无条件印名册的刀，而那一具刀的形状恰好就是狼队名单公开。

#### 电池：六具刀 + 一具位置负控制

窄名册五个文件（`test_info_isolation.py`、`test_wiring.py`、`test_human_seat.py` 加两条前缀
梯子），基线 171 passed（3.22 秒）；全量基线 975 passed（58.24 秒），没有一条是预先红着的。
九段替换、锚点全唯一、行数中性。

| 刀 | 结果 |
| --- | --- |
| W1 座位行不印名册（退回旧行为） | 咬住，红的正是正证那一条 |
| W2 名册印成「除本席外全体」（狼队名单公开） | 咬住，正证 + 反证两条 |
| W3 名册倒序（升序没有主张） | 咬住 |
| W4 自己也算自己的队友 | 咬住，正证 + 反证两条 |
| W5 名册从座位行挪进 C2 事实块 | 咬住 |
| C1 换措辞（「队友名单：」）——预期是红的 | 咬住 |
| C2 负控制：只在座位行内部挪位置 | 活着（对：这一处没有主张） |

全量半径（变异体还在盘上时量、减同一窗口的全量基线）：W2 两条、W4 两条、W5 一条。extra-red
空、收尾基线 171 passed 无红、残留 `.pyc` 无，整个电池 267 秒。

W3 这具刀换过两次写法。原本想下"不排序"（`sorted(` 摘掉），但 `Percept.teammates()` 返回的是
frozenset，`{2, 3}` 这种小整数集恰好按升序迭代——那会是一具**等价刀**，rc 0 只能证明我的刀钝，
证不了升序有主张。改成 `reverse=True` 之后它咬得住：正证的期望串是升序写死的。这一格和 `#133`
的 V4 是同一族教训（钝刀与坏刀都要在名册上点名，见上一节的第三跑）。

W2 与 W4 是两条不同的主张、同一对证人：前者把别人的队伍也算进来（隔离破了），后者把自己算进自己
的名单（名册错了但没泄）。它们红的都是正证 + 反证两条——正证那条本来就该红，因为期望串里既没有
多出来的席位也没有本席。

C1 是这一片里唯一一具"刀与负控制换了身份"的。第一跑把它当负控制下（措辞不是主张，预期应当活着），
它红了正证——回去读断言，`want = "你的队友是 " + "、".join(…)` 把措辞按字节钉住了。钉住措辞是
我写的还是碰巧的？是写的：这一行是念给模型看的，措辞就是它看到的唯一形态，所以第二跑把它改判成
第六具刀，期望名单照断言列。真负控制换成了 C2：名册还在同一行里，只是从"你的身份是"后面挪到
"这条信息…"后面，块内字节偏移没有主张，这一具必须活着——它红了就说明断言严过了主张。

（第一跑的 W5 也漏了一次刀，但那一次红的是电池自己：同一文件的两次替换原先各自从**原始文本**重写，
后一段把前一段覆盖掉，落盘的变异体只加了事实块那份、座位行里那份还在，等于半具刀。改成按文件累积
替换，并加了一条"这具刀不能是 no-op"的自查。）

#### 这一片没有动的

那两条 DEAL 过滤原样保留（上面说了为什么）。真人那一屏不在这一片里：`#133` 已经给它接了页眉。
`run --mock` 单独跑不落 prompt 转储（18:06Z 实测：一局跑完，目录里只有一枚日志），所以用例是钉住
prompt 字节的地方；但 `--mock --dry-run` 落的 `g<seed>.prompts.jsonl` 里就是整段字节（18:05Z、
seed 134：56 行装配，含名册的那 16 行恰好全是狼席 1/4/8 的轮次，座位行印作
`你是1号。你的身份是：wolf。你的队友是 4号、8号。这条信息只有法官和你看得到。`）。这一格答得了
字节、答不了行为：`--dry-run` 物理上发不出请求，所以"模型拿到名册之后第一夜提案有没有变"是
`#4`/`#5` 那族等端点的账。`#126`、`#132` 原样。


### 文档精简的那一刀：逐片取证从 README 搬去 `docs/iterations.md`（`#135`）

搬的动因不是"README 太长了"这种手感，是量出来的一件事：`tests/test_no_secrets.py` 的
`SCAN` 名单是 `src/`、`tests/fixtures/`、`docs/` 三个目录，而 **`README.md` 作为一个裸文件路径
不在其中**。这份仓库里最容易长出一段终端粘贴的文件恰恰是 README——真跑一次 `batch --real` 之后，
粘进文档的就是那一段 stderr，里面带着 `Authorization: Bearer …`。上一跑数过：README 5678 行，
而密钥扫描一行都不读它。

所以这一片同时动两处，且顺序是有讲究的：先把不属于说明书的那 4999 行搬进 `docs/iterations.md`
（它本来就一直在扫描范围里），再把 README 本身接进扫描。反过来做的话，等于一边宣布"README 里的
粘贴物有人看管"，一边把 99% 的粘贴历史留在管外。

搬的那一刀用脚本落（`/tmp/split_docs.py`），三条断言在写盘之前：接缝那一行必须是
`### 端点说不的时候` 开头、README 尾部那一行必须是 `## 这个仓库现在能做什么、不能做什么` 开头、
以及 `A + M + B` 逐字符等于原文。第一次跑它 `TypeError`——`SPLIT_A, SPLIT_M, SPLIT_B = 472, 5471, None`
这种解包写法把 `SPLIT_B` 绑成了 `None`，崩在写盘之前所以什么都没弄坏。

守恒量出来的账：README 5678 行 → 683 行（`#136` 之后 691 行），`docs/iterations.md` 5017 行；
**A 471 + M 4999 + B 208 = 5678**，搬完之后"在目标文件里找不到的非空行"是 **0**。

搬完还要回答一句"这里头的规则还有读者吗"。答案是拿三件假东西种的：一处早就漂走的用例名、
一行畸形形状的表格、一串 `sk-` 形状的字面量，写进 `docs/iterations.md` 之后
`test_doc_citations.py`、`test_doc_tables.py`、`test_no_secrets.py` 各报一次红，还原之后字节相同。
这份文件顶部那 18 行因此写着三条规矩：这里的数字是**带时刻的历史读数**、它归这三道闸门管、
以及它为什么不该住在说明书里。

`test_no_secrets.py` 里新增的那条守卫只钉一件事——"文档点名的那份粘贴目标在不在扫描集里"，
不去钉"扫到了多少个文件"：后者会随仓库长缩，那是一句每天都要重数的数，而这一片的主张是
范围，不是容量。

### README 的演示块第一次被整块执行：`LOG=` 挑中的是一份 prompt 转储（`#136`）

`#135` 把 README 收到 683 行之后，说明书里剩下的每一句都更该是真的。这一片去读〈三分钟离线
演示〉那一节——它教人连着敲六条命令，而**从来没有一条用例执行过它**。执行的这一次，是把它
原样交给 bash（`test_the_readme_demo_block_runs_verbatim`）。

现场不是编的，是 README 自己吩咐出来的：上面〈命令一览〉里就写着一条往 `--out data` 落的
`--dry-run`（`#138` 之后它多了一个 `--seed 21`，为的是不跟同节第一条抢局号；共目录这件事没变），
而 `run` 与 `--dry-run` 共用 `--out`，所以 `data/`
里合法地住着两种 `*.jsonl`。引擎的两个读取器各有一份"这个名字不是轨迹"的判据
（`metrics.read_dir`、`batch.read_arm`，两处的断言都在 `#49` 那轮补齐）。演示块里那句

```bash
LOG=$(ls data/*.jsonl | tail -1)
```

是**同一句判据的第三份抄写**，而这一份抄错了：局日志叫 `2026…Z_g00000007.jsonl`，转储叫
`g00000022.prompts.jsonl`，`ls` 按字节序排，`g` 永远排在时间戳的数字后面，于是 `tail -1`
每次挑中的都是转储。第一次实跑的读数：**退出码 1，六条
「日志读不下去：data/g00000022.prompts.jsonl: … 这不是一局日志」**——六条，因为块里六条命令
全都拿着这个 `$LOG`。那六条报错本身是 `#47`/`#50` 换来的（当时这里是一屏 traceback）。

同一块里还有第二件事：末尾那条 `wolf audit "$LOG" --calibration data/calibration.json` 依赖的
那份 sidecar 是 `scripts/calibrate.py` 体检真端点的产物，而 `data/` 在 `.gitignore` 里，
`git ls-files data/` 是空的——新克隆的仓库上**没有**这个文件，所以那一行在"三分钟离线演示"里
不可跑。它因此从可粘贴区挪进了注释，理由写在旁边。

`LOG` 的取值改成问 `run` 自己：摘要行的末尾就是它刚写下的那份
（`… 0.0s -> data/2026…_g00000007.jsonl`），用 `sed -n 's/.* -> //p'` 取出来，不再有任何 glob。
这一改顺带消掉了另一处隐患——旧写法即便加了 `*_g00000007.jsonl` 那样的 glob，也只是把 seed
抄第二遍。

#### 电池：九具刀 + 两具负控制（11 个 stage）

第一跑（v1）有两具刀没咬住，那两条是本片真正的收获：

| 刀 | v1 | v2 |
| --- | --- | --- |
| K2 `--god` 打成 `--god-typo` | 漏（GREEN） | 咬（RED） |
| K4 那一节的标题改名 | 漏（GREEN） | 咬（RED） |

漏因两条，各自都指向证人的写法而不是文档：

- **K2 漏**：块里的命令后面挂着 `| head -3`，管道的退出码是 `head` 的，所以"某条命令 argparse
  失败"这件事在 `$?` 里根本看不见；而 v1 的标记 `[e1] 法官：开局座位` 三条命令都会印，另一条
  replay 替它作了证。改成"一块一条、每条一个别人替不了的读数"（按下表实测的窗口逐命令数过
  谁印了什么），K2 才咬得住。
- **K4 漏**：抽取用的是 `str.find()`，`## 三分钟离线演示x` 是 `## 三分钟离线演示` 的**超串**，
  照样命中。改成按整行相等找标题。

v2 的 11 个 stage：每具刀先量同窗基线（11 次全部 GREEN），再量刀下结果，最后逐字节还原核对。

| 刀 | 结果 |
| --- | --- |
| K1 退回 `ls data/*.jsonl \| tail -1`（今天真的错过） | RED，符合 |
| K2 flag 打错 `--god` → `--god-typo` | RED，符合 |
| K3a–f 块里每条命令各换成一具 no-op（六具：`run` 的 `echo`、两条 `replay`、`export`、`watch`、`audit`） | 六具全 RED，符合 |
| K4 标题改名（抽取锚点断了） | RED，符合 |
| C1 负控制：只改块里一句注释的措辞 | GREEN，符合 |
| C2 负控制：把两处 `\| head -3` 换成 `\| head -4` | GREEN，符合 |

K3a–f 那六具是这一片给"文档里的命令"补的归因：证人声称一块一条，那每条都得能被单独问出来。
C2 顺带量出一处**限界**，得说明白：`--seat 3` 那条的归因是窗口相对的——席位视图本来就是上帝
视图的子集，`[e4] 法官（私发）` 只在文档写着 `| head -3` 时才是那一行独有的。窗口放到四行，
`--god` 也印得出 e4，C2 因此 GREEN 是**如实**而不是漏刀；这条限界写进了用例的注释里。

#### 这一片改到的旧数

三处写着 `tests/test_cli.py` **现行**条数的句子（defs 一格、收集数两格）被这 +1 顶红，是
`test_a_case_count_written_next_to_a_module_name_matches_that_module` 和
`test_a_collected_count_written_in_the_docs_matches_what_pytest_collects` 当场报出来的，
一起 bump 成六十一/七十（两处 `docs/iterations.md`、一处 README，行数不变）。README 的〈测试〉
一节里那句套件总数与那条收集数链跟着记了本片的一次新账。

#### 这一片没有动的

`data/calibration.json` 本身没动，也没往仓库里塞一份假的：它是 `#1`/`#10` 那族等端点的账。
`docs/metrics.md` 里那几条 `ls /tmp/.../*.jsonl | head -1` 是同族形状，但那些目录只装批次
产物、不装 `--dry-run` 的转储，本片没有实测到它们会挑错文件，所以只记在这里，不动。


### 〈人怎么上桌〉那一块也被整块执行了：第一版证人放过了删掉 replay 的那具刀（`#137`）

`#136` 立了规矩之后，README 里剩下两块给人粘贴的命令块，这一块是"引入人类玩家"那半条主功能。
先按 `#137` 的票面量三件事（`--mock --human 3 --seed 7`，2026-09-26T07:31Z，`/tmp/d137m`）：

| 问的 | 实测 |
| --- | --- |
| 只粘贴这一块、`$LOG` 从哪来？ | 那块里没有 `LOG=` 的取值行，靠的是上一节留下的 shell 状态。单独粘贴时 `wolf replay "" --seat 3` 退出码 2（电池里 K2 那一具量到的就是这个形状）。而且它重放的是上一节那局**没有真人**的棋，旁边那句「从你那一席的视图重看一遍」在说一件没发生过的事。 |
| 第一条在 stdin 不是 tty 时怎么走？ | 走得完：递三行进 stdin，rc 0。第一行「票 5 先听听」被「法官打回：法官指派的是「改口」，「票」不算。」退回（`#127` 那条路），第二行的后半句进了日志成为 `[e19] 3号：我投他`，第三行之后的卡片由引擎代打（`〔引擎代打〕`）。 |
| 第二条（真模型那一桌）能不能留在块里？ | 不能。它没有 `--mock`，粘下去就去拨局域网判官——和 `#136` 那格 `--calibration` 同族，所以同样的处置：挪进注释，主张留在原地。 |

#### 证人的第一版是错的，是电池当场报出来的

`桌边坐着一个真人` 那句一直被当成读取侧的招牌，实测它 **`run` 收尾时也印**（`run.out` 的第 205 行）。
于是"一块一条"的第一版把这句记在 `replay` 名下，K4（把 replay 那行换成 `true`）就此 GREEN——
和 `#136` 的 K2 同一族：一条命令的读数被邻居替它作了证。修法不是换一句更花哨的话，是把两件事
拆开：

- `桌边坐着一个真人` 改成**数到 2**（打完的那一屏 + 重看的那一屏）。要让它分得出"重看的到底是不是
  刚打的那一局"，夹具得先在 `data/human` 里放一局**没有真人**的旧日志当对立面——目录里只有一个
  文件时，`tail -1` 与 `head -1` 是同一句话（K7 那一具量的是这个）。旧日志的文件名钉成 2000 年：
  同一目录、同一秒的第二局会被引擎拒掉（"a game id is not reusable"），不同秒无事。
- 视图这一格改由 `[e4] 法官（私发）：你的身份是 villager。〔私有〕` 钉，`〔私有〕` 这个后缀只有
  读取侧印（`run.out` 里 grep 它是 0 命中），席位卡片上没有；再用一条**缺席**断言收掉 `--god`：
  上帝视角的第一屏就是别人的发牌行（seed 7 的 `[e2]/[e3]/[e5]`），3 号看不到，所以
  `[e2] 法官（私发）` 不许出现在整块输出里。

`#136` 那条"窗口相对"的限界在这里没有复发：这一行的 replay 不接 `| head -3`，重看的人本来就该
看到整屏。K8（`--seat 9`）与 K9（`--god`）因此是可以咬住的实刀，而不是像上一片那样只能当限界记账。

#### 11 个 stage：9 具 RED、2 具负控制 GREEN

每具刀先量同窗基线（11 次全部 GREEN），再量刀下结果，最后逐字节还原核对（`/tmp/bat137.py`）。

| 刀 | 结果与归因 |
| --- | --- |
| K1 把 `--human 3` 从可粘贴的那条上摘掉 | RED：「这一块里已经没有一条真把人放上桌的命令了」 |
| K2 `HLOG` 的形状指到没有的种子上 | RED：退出码 2（等于单独粘贴那一块时原来的样子） |
| K3 把需要端点的那条从注释里放回可粘贴区 | RED：前置断言在**执行之前**拦下，一条请求都没发出 |
| K4 把 replay 那行换成 no-op | RED：`桌边坐着一个真人` 只数到 1 次 |
| K5 那一节标题改名（抽取锚点断了） | RED：「README 里没有整行等于 … 的标题」 |
| K6 产品刀：`parse_human_line` 丢掉人敲的后半句 | RED：标记 `3号：我投他` 没印出来 |
| K7 `tail -1` → `head -1`（挑到夹具里那局旧的） | RED：那句只数到 1 次 |
| K8 replay 的视图换成 `--seat 9` | RED：3 号那张牌没印出来 |
| K9 replay 的视图换成 `--god` | RED：`[e2] 法官（私发）` 出现了 |
| C1 负控制：改块里一句注释的措辞 | GREEN，符合 |
| C2 负控制：改 replay 行尾那句注释（注释不是主张） | GREEN，符合 |

K6 那具刀下在 `src/wolfengine/human.py:79` 的那一句 `speech=`（人敲的后半句就是从这一格进
`Action` 的），它证明第三条标记不是"文档里的字恰好也被别人印了一遍"，而是一条走得通整个来回的链。

#### 这一片改到的旧数

`tests/test_cli.py` 的现行条数三处（defs 一格、收集数两格）被这条 +1 顶红，bump 成六十二/七十一
（两处 `docs/iterations.md`、一处 README，行数不变）；README〈测试〉那节的套件总数与收集数链各记
一次新账（07:45Z 全量重跑 978 passed）。抽取块的函数从 `_readme_demo_block()` 变成
`_readme_block(heading=…)`，两条用例读同一个抽取器——抄一份"等价"的块就是第二份主张。

#### 这一片没有动的

`wolf run … --human` 那条真模型桌仍然没有可读的产物：它属于 `#1`/`#4`/`#5`/`#10`/`#40`/`#88`/`#96`
/`#112` 那一族等端点的账，本片没有向局域网发过任何请求。README 里下面那张卡片样例（「实测：
`--mock --human 3 --seed 7`，2026-09-25T12:18Z」）是**带时间戳的历史读数**，本片没重钉它：
它印的是票型轮的卡片，而上面那三行 stdin 走进的是白天的讨论轮。

### 〈命令一览〉那一块的举例：逐条敲进同一个空目录（`#138`）

`#136`/`#137` 各收了块，README 里剩最后一块给人粘贴的命令——〈命令一览〉那张表下面的围栏。
它和前两块的形状不同：不是一段演示，而是**八条各自独立的举例**，其中两条故意返回 1
（`compare`/`gate` 对合成桌出拒绝语），所以整块交给 bash 加 `set -e` 必然在倒数第二条上断。
先按票面量三件事（`/tmp/m138a` 逐条的退出码与读数、`/tmp/repro138.py` 撞局号那一格，
2026-09-26T07:58Z 与 08:00Z）：

| 问的 | 实测 |
| --- | --- |
| 逐条敲的时候，每条的退出码和读数是什么？ | 八条各有自己的格子：`run` 三种加 `batch` 四条 rc 0（`--dry-run` 那条末尾印"成本合计"），`compare` 的 `SYNTHETIC_TABLE` 与 `gate` 的 `NOT_EVALUABLE` 各 rc 1。这一块此前**一条**都没有执行证人。 |
| 块里有没有会拨判官的命令行？ | **有一条活的**：`git show HEAD:README.md` 里那一行是 `wolf run --seed 7 --games 1 --god   # 真端点；…`，没有 `#` 打头。按 `#136`/`#137` 立的规矩（跑它们的是子进程，`no_network` 那层夹具挡不住），它只能挪进注释，主张留在原地。 |
| 八条举例共用 `--out` 会怎样？ | `--seed` 的出厂值是 **7**、`--out` 的出厂值是 **data**（`src/wolfengine/cli.py:657`、`:659`），所以原块里有**三条**往 `(data, 7)` 上写：第一条明写、第二条 `--dry-run` 用默认 seed、第五条 `--human` 连 `--out` 都没给。局号是「秒 + seed」，挨着敲十次里八次退回 rc 2（`路径用不了：… already holds a game log`）；把默认 seed 显式改成 21 之后 0/10。 |

#### 逐条跑的那一圈量不到"同一秒"这一格，所以它变成了文件系统的不变量

前三次实跑都过了——**过**不等于这块没有那一格：每条命令各花 ~1 秒，跨没跨过一秒是运气。
所以证人不只数退出码与标记，收尾还要把 `tmp_path` 里落下的 `*_g<seed>.jsonl` 按 `(目录, seed)`
分组，任何一组出现两份就是这一块在抢局号（`landed` / `doubled` 那一格）。这一格是确定的，
不看秒针；逐条那一圈只在**跨了秒**的时候才撞上 rc 2，两格各有各的用处。

#### 12 个 stage：9 具 RED、1 具声明为等价的刀 GREEN、2 具负控制 GREEN

每具刀先量同窗基线（12 次全部 `3 passed`，没有一次需要"基线本来就红"的归因），再落刀、执行、
逐字节还原（`/tmp/bat138.py`，还原后 `README.md` 与 `tests/test_cli.py` 的 sha256 前缀
`7e292e92febf` / `977d2241ee4d` 与开局一致）。每一具 RED 都只红在 `menu` 那一块上，另两块证人
（演示块、〈人怎么上桌〉块）全程绿——这一片动的是共享的切分器与那一块的顺序，没伤到邻居。

| 刀 | 结果与归因 |
| --- | --- |
| K1 把需要端点的那条从注释里放回可粘贴区 | RED：拨号前置断言在**执行之前**拦下，一条请求都没发出 |
| K2 把 `--god` 那条举例删掉（而不是注释掉） | RED：`god_examples` 数到 0，「默认需要端点」的例子只剩上面表格里那一行 |
| K3 `--dry-run` 那条摘掉 `--seed 21` | RED：`landed` 里 `(data, 00000007)` 有两份 |
| K4 真人那条从 `--out data/human` 挪回 `--out data` | RED：同一格，第三份 `(data, 00000007)` |
| K5 真人那条的 `--human 3` 换成 `--quiet` | RED：第 4 格的标记 `轮到你了：3 号` 没印出来 |
| K6 把 `compare` 挪到 `batch` 那两条之前 | RED：`compare` 先跑，目录还不存在（实测缺目录 rc 2，期望 rc 1） |
| K7 第一条的 `--seed 7` 改成 8 | RED：第 1 格的标记 `_g00000007.jsonl` 不再由那一条印出 |
| K8 `gate` 的臂目录改成 `A-vs-X/A` | RED：读一个不存在的臂目录，rc 2 对不上期望的 1 |
| K9 摘掉 `_dialer_statements` 的**行内注释剥离**（换成永不匹配的模式） | GREEN，**这具是预先声明的等价刀**：这一块里没有把 `--mock` 藏在行内注释里的命令行，所以剥不剥离读到的同一串。它证的是"这条判据在本片这块上没被用到"，不是"它没用"——`#137` 那块也没有这种命令行，剥离那一格是判据的通用形状，留给下一片真撞上时再用 |
| K10 切分器不再认反斜杠续行 | RED：`batch` 那条被切成两半，`--mock` 落在后半，前半成了拨号命令行 |
| C1 负控制：改第一条行尾注释的用词 | GREEN，符合 |
| C2 负控制：真人那条的注释前少一个空格 | GREEN，符合 |

K9 报的和 K10 报的都在 `_dialer_statements` / `_readme_statements` 这两个共享判据上，方向相反：
K10 证明"续行必须合成一条"这一格有牙齿（少了它就误判成拨号），K9 证明"行内注释要切掉"这一格
在本片这块上**没有**第二个读者——这一格要留成结论就得给它一个自己的例子，本片没造，所以如实记成
等价而不是收益。

#### 这一片改到的旧数

`tests/test_cli.py` 的条数三处（defs 一格、收集数两格）被这条 +1 顶红，bump 成六十三/七十二
（两处本节、一处 README，行数不变）；README〈测试〉那节的套件总数与收集数链各记一次新账
（08:21Z 全量重跑 979 passed、`--collect-only` 数到 979）。另外把 README 里一句**半真话**收回来：
`--dry-run` 不只落 prompt 转储，同一个 `--out` 里还多出那几局的合成局日志——`#136` 那句"两种
`*.jsonl` 共目录"的成因原本写成"演示块自己造出来的现场"，其实〈命令一览〉里就有一条往
`--out data` 落的 `--dry-run`（`#136` 那节的这段已被改成指向这一片）。

#### 这一片没有动的

`--god` 那条真端点举例仍然只是注释：它属于 `#1`/`#4`/`#5`/`#10`/`#40`/`#88`/`#96`/`#112` 那一族
等端点的账，本片没有向局域网发过任何请求（也没有导出过 key）。〈命令一览〉与〈三分钟离线演示〉
两块都往 `data` 里写 `--seed 7`，`data/human` 里也有两条举例各写一次 seed 7——这一格 `landed`
管不到，也不算主张：四块各有自己的证人、各在自己的空目录里跑，而人照着 README 敲时块与块之间
隔着正文与一张表，局号按秒分，晚一秒就写下第二份文件而不是被拒。这条限界是**块与块之间**的，
上面的不变量只管一块之内。

### 行号闸门第一次有人看着 .py 里的号：38 处引用 30 处报红，没有一处靠豁免消掉（`#126`）

这一族的语料从 `#77` 起只有 markdown（`docs/*.md` 加 `README.md`），而测试文件的 docstring 与
注释里写的是同一个形状 `文件.py:号`——读者照样照着号去看**现在**那一行。08:43:59Z 把扫描面扩到
`tests/` 与 `scripts/` 那 44 个 .py 上，拿闸门自己的判据跑一遍：**38 处引用、30 处报红**
（按出现数，去重是 27 个不同的指针；09:12:10Z 对着同一批字节重数，两个数一字不差）。票面上这条写
的代价是"豁免 10 处、修 1 处"，实测是 30 处报红：差距全在"历史复述"那一类里，闸门把那 16 处转述
旧号的句子当成了 live 主张，而它是对的。

第一处确证的漂移在 `tests/test_m3_gate.py`：那句话写的是 `metrics.py` 的 75 行，而 75 行今天坐着
`collapse_round` 的 docstring，它点的那句除法实在 88 行。处置没有只改号——把名字 `collapse_round`
补进句子、号收到 `metrics.py:73` 的 `def` 行，这样下一次插入把号顶走时报的是"这句话点的名字不在
那一行"，而不是"号对着一条 docstring"。

#### 处置只在句子上：17 处摘形状、14 处改号或补名字

判据一字未改，豁免名单一条没给——这个文件开头那条约定（`` `文件名.py:NNN` `` 从此是一句**今天还对**
的主张）本来就说清了复述该怎么写：

* **17 处**摘掉 live 形状，号原样留着写成"`X.py` 的 N 行"这种正则接不住的样子（38 → 21）。其中
  **16 处**住在本片的闸门文件自己身上——它那 16 处引用在旧形状下**全部**报红，因为那些"13:48:25Z
  实测那两处…"的取证句子复述的正是**当时**的号；剩下 1 处是 `tests/test_anti_repeat.py` 里指着一条
  golden 用例的号，那句话点的东西从来就不在那一行，改成点名用例名之后号不再出现。
* **14 处**改号或补名字。其中 3 处**号一个字没动**（`legality.py` 的 85、`game.py` 的 134、
  `test_golden_game.py` 的 337）：补的是名字——判据在代码行上取句中 ASCII 标识符去核被点那一行，
  而那几句中文散文里没有一个词落在那行上。另有 1 处改的是"这句话点的**东西**变了"：
  `tests/test_loopback_endpoint.py` 那句括号原本描述的是行号闸门的范围，而那个范围正是本片改掉的，
  所以整段换成点名 `cmd_batch`（`cli.py:533`）的那一句。

票面原本要求的"给 `test_doc_citations.py` 一份显式豁免（它的字符串是夹具）"实测不需要：夹具里的号
指的就是仓库里那些真行，漂了就是漂了，豁免只会把这一族重新变成没人看的表。

这一节的**初稿**自己撞上过一次（09:21:46Z 那一跑 2 条证人红、6＋2 处报红）：叙述变异刀时把被顶走的
号照点形状写了（`X.py` 后跟数字），"这一片改到的旧数"里两处历史条数用了"`X.py` … N 条"的形状。
处置照本节立的规矩走，不动闸门：刀的账改成"`X.py` 的 N 行顶成 N±1"，历史条数写成汉字——改完同一跑
26 passed 全绿。这条记账留着，因为它是本片那条约定的第一次**自我适用**：规矩不是只对写完的别人生效。

#### 两条证人各管一件事：判据跑得对，语料扫得全

| 证人 | 钉的 | 少了它会怎样 |
| --- | --- | --- |
| `test_a_line_number_written_in_the_docs_still_points_at_the_thing_named_beside_it` | 判据跑在**加宽之后**的语料上（08:52:44Z 数全语料 190 处引用、其中 .py 侧 21 处；地板 150） | 号漂了没人报，读者照抄一个点不到的入口 |
| `test_the_line_citation_gate_reads_the_python_files_that_cite_line_numbers` | 语料的**组成**：`tests/` 与 `scripts/` 下每个 .py 都必须出现在 `_line_cite_corpus` 里 | 把扫描面收窄回去时上一条**不红**（markdown 那 169 处仍然过地板），只有这一条报 |

地板取 150 而不是 190：190 是今天的读数，钉死它等于以后每加一处行号都要来改这道地板；150 落在
markdown 那一半之下，所以它只管"正则或范围坏了"，不管"文档变薄了"。

#### 9 具变异：7 具 RED、1 具负控制 GREEN、1 具声明过的等价刀 GREEN

`/tmp/mut126.py`，09:04:31Z 起跑、256 秒。同窗基线两趟都干净（闸门 26 passed、全量 980 passed
0 红），七份被动的文件逐字节还原，收尾再数 `find . -name '*.pyc' -newer /tmp/mut126.py` 为空
（`#79` 那一格：还原字节相同不证明跑的是盘上的代码）。

| 刀 | 结果与归因 |
| --- | --- |
| K1 .py 里的号被插一行顶走（`llm.py` 的 178 行顶成 177） | RED 只红 T_LINE；全量半径 1 条（变异体在盘上时数，减同窗基线） |
| K2 同一个号漂进被指文件的注释行（`legality.py` 的 113 行顶成 112） | RED：报的是"号落在注释行上，而这句话没把被指的东西写成名字"，与 K1 两种措辞各有分支 |
| K3 测试文件自己的注释块里指针对错一格（`batch.py` 的 500 行改成 501） | RED：这一具下刀的位置是本片新收进来的那类文本（`#` 起的注释） |
| K4 .py 里点一个不存在的文件（`events.py`→`eventss.py`） | RED："src/tests/scripts 里没有这个文件" |
| K5 把一处历史复述改回 live 形状（`cli.py` 的 620 行写成 `` 文件名.py:NNN `` 那个形状） | RED：证明那 17 处**在旧形状下就是红的**，消掉它们靠的是句子不是闸门 |
| K6 语料收窄回只读 markdown | RED 只红 T_SCOPE，T_LINE 全程绿——两条证人的分工由这一具分开 |
| K7 README 的条数/收集数回退一格（26→25） | RED 两条：条数与收集数各一条。这一具是给本片改的那两个数发证的 |
| C1 负控制：只换 .py 侧那句指针周围的中文，号不动 | GREEN，符合：加宽之后闸门仍然只判"号对不对"，不判措辞 |
| E1 等价刀（预先声明）：`metrics.py` 的 1331 行改成 1332 | GREEN：1332 那行仍被同一句话里的名字背书。它证不了判据，只说明这个锚点钝，所以记成等价而不是收益 |

#### 这一片改到的旧数

`tests/test_doc_citations.py` 二十五条 → 二十六条，README 里那一格（条数与收集数各一次）跟着改；套件
总数 08:58Z 全量重跑数到 **980**（链上记一格）。另外把 `docs/iterations.md` 里 `#111` 那一段
"文档闸门（`test_doc_citations.py`，二十五条）"的条数摘掉：那是那一跑的**历史读数**，而计数闸门
只认形状不认这句话指的是哪棵树——留着就是把旧顶成新的那类错（改回原值不成，那就摘形状）。

#### 这一片没有动的

产品代码一行没改：这一族的输入是散文与注释，判据、窗口、取词都在闸门文件里，本片动的是它读的
**范围**。`src/` 侧的行号引用仍然只由 markdown 那半边管——`src/**/*.py` 没进语料，票面也没要求；
下一片要收它，先得量有多少处注释里的号在昨天就对不上（本片对 .py 侧做的正是这一件事）。

### 行号闸门的语料第一次收进产品代码的注释：src 侧只有一处，那一处是假话（`#139`）

上一片结尾那句"下一片要收它，先得量有多少处注释里的号在昨天就对不上"就是本片的票面。代价先量，
09:29:11Z 那一跑：src 侧 28 个 .py 里写着 `文件名.py:数字` 这个形状的注释**只有一处**，两个号，
**两个都报红**。收进来的成本是一个句子，收益是那一处假话当场藏不住。

#### 那两处的红不是同一种红

号没错。`events.py:332` 坐着的就是 `idempotency_key` 那个早退判断，`events.py:410` 坐着的就是
`except LogDamage` 那一行——闸门报的红两条都是"这句话没写出可核对的名字"，而它附带的"它在第 330 行
（差 -2 行）"是它自己找强标识符时的落点，不是修复指令（这一格由 K6a 单独发证）。

假的是**依据**。那句 docstring 说这两个号"cited by line number in README"，而 09:29:39Z 实测 README
里 `events.py` 带号的引用是 **0 处**：`#135` 把那段逐片取证搬去本文件时，README 里原本有 8 处，搬完
一处不剩。也就是说这句话从 `#135` 起就是假的，一直假到本片把它读出来。处置是改句子而不是改闸门：
依据改指本文件（此刻本文件里确实还点着这两个号，各一处与三处），并把"当初 README 有 8 处、今天 0 处"
写进那句话自己，让它下次被搬家时能自证。

#### 闸门动了三格，判据一格没动

- **语料加宽**：`_line_cite_files` 从"markdown + `tests/` + `scripts/`"再加 `src/` 的递归 .py。
- **键换成相对路径**：原先按 `f.name` 存键，而 `src/wolfengine/__init__.py` 与
  `src/wolfengine/prompts/__init__.py` 同名——第二棵会在字典里**静默消失**，不是报错是少读一棵。
  今天这两棵都没有行号引用，所以换回文件名建键不改变任何一处判定（K2 的实测：主闸门绿着，
  只有新加的两条证人红）。
- **两条新证人**：`test_the_line_citation_gate_reads_the_product_files_that_cite_line_numbers`
  钉"src 里每个 .py 都在语料里"；`test_the_line_citation_corpus_has_one_entry_per_file_it_scans`
  钉"语料条数==扫描文件数"，并且它第一句 `assert dupes` 反向要求扫描面里**还有**同名文件——
  同名那一格一旦被搬家消掉，这条守卫就自称空跑并红掉，免得自己变成一条永远绿的装饰。
  `tests/`+`scripts/` 那条旧证人的比较集合也跟着换成相对路径。

扩宽后的读数（09:35:02Z）：78 个文件、78 条语料、194 处引用（markdown 171 + tests/scripts 21 +
src 2），src 那 2 处报红 0。T_LINE 的地板仍是 150——它只管"正则或范围坏了"，理由与上一片同。

#### 变异电池（09:43Z 起，450 秒，10 具刀、11 段替换）

全量基准：982 passed，无预存红。半径都是变异体在盘上时数的全量读数。

| 刀 | 结果 |
| --- | --- |
| K1 src 那一格换成不存在的目录（语料收窄回上一片） | RED 两条：src 证人 + 同名守卫（收窄后扫描面里再无同名文件，守卫自报空跑）。全量半径 2 条 |
| K2 语料的键换回文件名 | RED 三条：上一条的 2 条再加 `tests/`+`scripts/` 那条旧证人。全量半径 3 条 |
| K3 号差一行（332→333，那里是 `for e in reversed`） | RED 只红 T_LINE。全量半径 1 条 |
| K4 摘掉句子里的两个名字、号原样留着 | RED 只红 T_LINE：证的是"号与名字都是主张"，缺一个就红 |
| K6a 照闸门报的"它在第 330 行"改号而不写名字 | 仍然 RED——那一格的红消息不是修复指令，这一具是它的反证 |
| E1 等价刀（预先声明）：410→411 | GREEN：411 是同一个 wrapper 的 `raise`，句子里的名字在 410 也读得通。限界，不是收益 |
| E2 等价刀（预先声明）：410→409 | GREEN：409 那行是 `records_from_lines`，句子里 "by line number" 的 `line` 作为**子串**给它背书。窗口的判据对巧合子串会误放 |
| K7 缺席（预先声明）：依据改回已被证伪的 `in README`，号与名字都不动 | GREEN：闸门判号与名字，没有任何断言在读"那个文件还点不点我"。这一具产出的不是收益，是 `#140` |
| C1/C2 负控制：只换那句依据的措辞；只换 `_line_cite_files` 里两行的先后 | GREEN，符合：加宽之后闸门仍不判措辞、不判那三棵的先后 |

还原：两处被点文件字节相同；收尾基线 28 passed 无红；`find . -name '*.pyc' -newer` 在 `wolfengine/`
下 0 命中（无字节码投毒）。

#### 这一片改到的旧数

`tests/test_doc_citations.py` 二十六条 → 二十八条，README 里那一格（条数与收集数各一次）跟着改；
套件总数 09:43Z 电池基准那一跑数到 **982**、09:58Z 与 10:00Z 收尾两跑仍是 **982**（链上记一格；README
那一格发的秒数是 09:58Z 那一跑的，四跑的秒数见它旁边那句"墙钟不是账"）。README〈行号闸门〉那一格补了 src 这一层，
`src/wolfengine/events.py` 末尾那句 docstring 是本片唯一改动的产品代码，且只改散文、没动一行语句。

#### 这一片没有动的

判据、窗口、取词、豁免名单全部原样（本片一处豁免没给）。markdown 那半边 171 处引用一处没动。
`#140` 立着没做：src 里以"文档按行号点了我"为自己摆放理由的一共三处（`cli.py` 两处 docstring、
`events.py` 一处），本片只把其中一处的**内容**修对了，那三处共用的**读法**仍然没有断言在读。

### 摆放理由这句话第一次有人核对：src 里三处，措辞与自指要同时出现才算（`#140`）

上一片结尾给它立的票面就是本片。`#139` 把那一处假话的**内容**改对了，但它靠的是我用眼睛读；
这一片要的是"它下次再假的时候有人报"。

#### 判据是三格，主张不是"号对不对"

一句代码里的散文可以说"文档按行号点着我，所以我住在文件尾"。它主张的是**这份依据此刻还成不成立**，
而不是行号没漂。三格分别是：

- `_placement_claims`：只扫 .py；被扫的那一行要**同时**出现措辞（`按行号引`／`按行号点`／
  `by line number in` 这几个字面量）与自指（`本文件`，或者把自己的文件名写成指针的前缀）。
- `_claim_sources`：句子里点名了哪一份文档就只查那一份；只泛指"文档"就查语料里除自己以外的全部。
- `_bad_placements`：被点名的那一份里已经没有任何一处指向本文件的号 → 报，并把出处点名在报告里。

真语料的读数（10:11:09Z）：78 条语料里认出摆放理由**正好三处**——`cli.py` 的 742 行与 781 行
（两句都只泛指"文档"，句中没有文件名）、`events.py` 的 481 行（点名本文件）。报红 0 处，
也就是那三句此刻都是真话。产品侧证人把地板钉在 3：认出的比这少，说明是正则或扫法坏了，
而不是"终于没人这么写了"。

#### 四半判据各自的代价，落笔前逐一量过（10:12:05Z 一次性脚本）

| 摘掉哪半 | 量到的后果 |
| --- | --- |
| 只扫 .py | 认出 3 处、报 0 处，与摘掉之前**完全同**——markdown 里那两行措辞句本来就没有自指。所以这一半是等价声明，别当成收益 |
| 自指那一半 | 认出 3 处变 7 处，多出的 4 条全在闸门自己文件里（正则定义两行 + 夹具两行），产品证人当场红 |
| 出处按名字算，放宽成"语料里任意一处指向就行" | 当时的夹具两侧都不红，**等价**。补一份 decoy（`docs/other.md` 里故意留着指向那个假文件的号）之后同一具刀立刻转成能抓 |
| 泛指那一支（没点名就查全部） | 旧夹具同样不红，补一格"泛指、且哪份里都没有号"之后才抓得住 |

**How to apply** 那句写在记忆里，这里只留结论：夹具要按"哪半判据被摘掉之后仍然全绿"来配，
那半就是没证到的；本片的夹具最后长到五格（点名而依据已失效 / 点名且依据仍在 / 泛指而依据仍在 /
泛指而依据全无 / decoy 那份不误伤），断言是"认出四条、只报两条"。

#### 这一族的老毛病在本片现世两次，都是闸门吃自己

10:07:11Z：夹具里照形状写了假文件名与号，行号闸门把"`src/tests/scripts` 里没有这个文件"判成一条
漂了的引用，红三格。10:15:17Z：换了新谓词之后，夹具某格把措辞与自指写进了**同一行源码**，于是
本文件被自己算成第四条摆放理由，产品证人红。两次都按这一族的规矩处置——**改句子，不改闸门**：
假号改成运行时拼装（`_fixture_cite()`），措辞与自指拆成相邻字面量。安全的判据是"这一格的几个形状
是否同现行"，而那是每一格新自己决定的，所以**每加一格都要重跑这道闸门**。

已知限制，一句话，不打算修：判据是**按行**的。措辞与自指被硬换行拆到两行时它看不见。这一格
钉不住（要钉就得把整段读成一句，那会把不相干的两句接成一条假依据），所以写进证人自己的
docstring 里承认它。

#### 变异电池：两轮，第一轮红的是脚本自己

**第一跑**（10:18:37Z 起，10 具：8 kill、1 声明等价、2 负控制）。基线全量 `984 passed`，
每一具 kill 落地后的全量收尾都是 `1 failed, 983 passed`，K3 与两个负控制是 `984 passed`——
**半径这一半是真的**。但汇总判了 7 具 NONCONFORM：每具都印"红 无"。

归因不在产品侧。抓取用的是要求分隔符的正则，而 pytest 的 `-rf` 摘要行对**过长**的 node id
直接印 `FAILED <nodeid>`、**不带** ` - 失败信息` 尾巴，于是名册整条读成空。这一格 10:41:55Z 现验过：
把 K4 落上去、只跑夹具那一条，原始摘要行是

```
FAILED tests/test_doc_citations.py::test_a_fixture_that_claims_a_doc_backs_its_place_is_tested_against_that_doc
```

111 个字符、` - ` 出现 0 次，所以 `FAILED (\S+) - .*` 这一支整条不命中，而"去掉前缀再按空格切第一段"
接得到名字。判据没坏、刀没坏、半径没坏，坏的是我从日志里抽名单的那一层。这也是 `#106` 那一格的
第二次现世，只是这回丢的不是一个名字而是全部。

**修抓取之后窄跑**（10:36:31Z→10:36:57Z，26 秒，环境变量 `NARROW` 置一，只跳过全量那一层）：**10 具全部
CONFORM**，不 Conform 的 stage 为 0。九具按预期落点（K1/K4/K5/K6/K8 红夹具那条、K2/K7 红产品那条、
K3/C1/C2 全绿），而**第十具是真没抓住的**：K4 把报告里点名的出处丢掉，断言
`"README" in 报告` 照样绿——因为那条报告的形状是"…：+ 被指的原文"，而原文里本来就写着 README。
出处被引号洗白了。修法是把断言挪到引文之外：按中文冒号切，只看前一半起不起手于那个名字。

窄跑跳掉的那一层单独重量过，免得两半的账混记（10:37:28Z→10:39:51Z，只在 K4 这一具上）：
落地前基线 `984 passed in 69.22s`，K4 生效时 `1 failed, 983 passed in 71.43s`、名册恰好是夹具那一条。
也就是说收紧断言没有改变半径——红的本来就是同一条用例。

K5 那一具（判据整个不跑）是这一片设计上的一格：它红夹具、**必须**让产品证人绿，因为后者断言的是
缺席。所以它的预期同时带"该红的红"和"该绿的绿"，脚本对后者单独判一项。

还原：三处被点文件（`tests/test_doc_citations.py`、`src/wolfengine/cli.py`、`src/wolfengine/events.py`）
全部字节一致、收尾窄跑 `2 passed`、`wolfengine/` 下 0 个比脚本新的 `.pyc`。锚点自查在正式跑之前
抓到两处不唯一的 `continue`（那一行在两处缩进层级上都有），处置是把它们从锚点表里摘掉——刀只换
`if` 那一行。

#### 顺手量掉的一个候选

10:21:00Z 把 src 整个扫了一遍"英文孪生"（谁读我式散文主张）：只有一处，`schema.py` 第 11 行那句
"structured errors consumed by the retry prompt"。它主张的是"谁读我"而不是"文档按号点我"，属
`#118`/`#82` 那一族，本片不动；记在这里是为了那句"如果还要加宽，代价是一处而不是几十处"。

#### 这一片改到的旧数

`tests/test_doc_citations.py` 二十八条 → 三十条（两条新证人：夹具那条、产品那条），README 里
〈行号闸门〉那一格的条数跟着改；套件总数 982 → **984**，README 那一格发的秒数来自 10:44:13Z 那一跑
（`984 passed in 68.86s`），那之后落盘的只有 README 自己的三格计数与墙钟句子，10:45:57Z 在最终树上
复跑仍数到 **984**（`72.38s`，又一格"墙钟不是账"）。产品代码一行没动——`cli.py` 与
`events.py` 里那三句只是被读，不是被改。

### 载荷普查学会问"你读的是哪本字典"：一张双写表被收了回去（`#132`）

票面上写的是"按键名数读者，看不见这一 kind 的这一格没人读"。动手前先量了两跑（约定：先量样本再
写方案），两跑合起来把这张票改成了另一件事。

**量的第一跑（10:56:55Z，`data/measure132.py`）**：形状表 14 个 kind、26 个唯一键名，按 kind 展开
是 55 格，跨 kind 共用的键名 7 个（`text` 跨 7 个 kind、`target` 跨 6 个，五个决策 kind 共用
`act`/`belief`/`evidence`/`meta`）。把判据换成逐格认领之后，零读者的格子是 **1 格 → 1 格**：
kind 归因在今天的语料上一条都没多抓到。原因是保守的归因规则（读者所在的函数里一个 `Kind.X` 都没提，
就算它服务所有 kind），而真正的读者恰好都是泛用的渲染器与度量器。所以这一片**不做** kind 归因，
把测量留在文档里，而不是把 55 格换成一个今天不开火的口径。

**量的第二跑（10:59:19Z，`data/measure132b.py`）**：换一条口径——不问"有没有人读这个键名"，问
"被下标的那个对象像不像一份 payload"。零读者立刻从 1 个键名变成 2 个：多出来的那个是 `evidence`。
它此前不是没人读，是被一个假读者养着：`schema.py:352` 的 `out.get("evidence")` 读的是**模型答出来
的那个 dict**，跟落盘的五份 payload 没有半点关系，而只数键名的尺子把它算成了读者。同一跑把别名
的账也量了：src 里 base 恰为 `p` 的 Load 点 47 个，其中键名落在形状表里的 24 个**全部**在
`compress.py`（`p, k = e.payload, e.kind` 那行散出去的别名），所以"像 payload"必须认这个名字，
否则 24 处真读者一起被算丢。顺带量的第三件事：判据原来用 `glob("*.py")` 扫 `src/wolfengine`，
子包整层不在扫面里（26 个文件 vs 递归的 28 个）——今天多出的两个文件贡献 0 个 Load 点，改成
`rglob` 并**把标签从 basename 换成相对路径**（`#139` 那一课的孪生：两棵 `__init__.py` 会同名）。

**顺着 `evidence` 查到的东西（11:00:47Z）**：它不是"写了没人读"，是**写了两遍**。同一格旁边的
`meta.citation_stats` 里躺着 `cited` / `valid` / `invented` / `not_visible` / `malformed` 五串编号，
是 `legality._check_citations` 算出来的，而 `payload.evidence` 是同一个 `action` 上再抄一遍。扫
`data/**/*.jsonl` 12 份：553 条带 `evidence` 的记录，三份清单各自去重排序后
`evidence == valid` 553/553、`evidence == cited` 553/553，一条不差——因为它们本来就是同一次
`check_legality` 的两个出口。非空的那 209 条（11:00:13Z 另跑）说明这格是真在记东西，不是空表。
读者那一侧却只有一支笔有：`metrics` 两处 + `batch` 一处读 `citation_stats`，产品链上读
`payload.evidence` 的是 0 处。这一格和 `#110`/`#111`/`#113` 是同一族，那一族的处置一直是**删掉抄的
那一份**，被抄的那一份留着。于是 `agent._write` 不再抄 `evidence`，形状表里五个 kind 的那一格一起
收回。指针留在表上：`events.py:70` 那一行 `SPEECH` 的字典里现在少了那一格，五行各自原地改过、
总行数一格没动。

**两条证人挪了家，不是删了**：`test_the_salvage_never_launders_a_second_kind_of_violation` 和
`test_an_invented_citation_is_refused_and_the_refusal_is_kept` 原来读 `payload["evidence"]` 来证明
"编造的编号没被洗进最终动作"。两格都改读 `meta.citation_stats`，且比原来更严：前一处现在同时要求
`valid == []` **和** `invented == []`（只查抄本只能发现"抄了什么"，查原本能发现"洗进了什么"）。
K10 那具就是钉这一条的：把 rung4 的复用条件从 `all` 改成 `any`，编造编号会被真的复用进最终动作，
那条证人必须红。

**已知限制，写在判据旁边而不是这里**："像 payload"认的是 `payload` / `p` / `pl` 三个名字加上任何
含 `payload` 的链式表达式——认名字，不认数据流。今天它没有放过任何东西，但 `report.py`/`batch.py`
/`metrics.py` 里那些 `p` 是别的字典（prompt 记录、逐臂统计），下一个撞上新键名的人会被误算。要做
成数据流得追到调用点，代价是把一条 AST 判据变成半个类型推导器；这一格宁可挂着名字。

#### 变异电池（11:14:18Z 起，61 秒，10 具刀 + 2 具负控制，12 个 stage）

基准：三具证人的子集（`test_payload_shape.py` + `test_agent_turns.py` + `test_golden_game.py`）**66
passed，0 预存红**才开的电池。刻意跑子集而不是全量：文档闸门那几位的红与本片无关，混在名册里只会
把归因变成猜（`#106` 那一课）。下面的"红几条"都是子集读数，不是套件半径。

| 刀 | 结果 |
| --- | --- |
| K1 把 `evidence` 那一格抄回去（`agent._write` 恢复第二支笔） | RED 2：`test_the_citation_list_is_recorded_once`（抓到双写）+ `test_the_table_names_every_key_that_lands_on_disk`（表里没有这一格了，落盘却多一条）。要求不含 STALE 那一具——它是 K2 的活 |
| K2 只把形状表里那一格恢复、产品代码不动 | RED 2：`test_the_table_names_no_key_the_engine_stopped_writing`（表点了引擎已停笔的键）+ 无人读取闸门（`evidence` 重新变成零读者）。与 K1 红的是不同的两条，两具各自定位一半 |
| K3 `is_payload` 恒真（退回"只按键名数"） | RED 2：`test_a_read_only_counts_when_what_got_subscripted_is_a_payload` + 控制用例（`evidence` 的假读者又绿了）。这正是这一片的缺陷本体 |
| K4 `PAYLOAD_BASES` 只剩 `"payload"`（不认别名 `p`） | RED 3：上一条的 2 条再加 `test_every_declared_key_is_read_by_somebody_or_named_here`——24 处真读者一起被算丢，`teammates` 之类的键重新变成"无人读" |
| K5 砍掉 `.get`/`.pop` 那一支（只认下标） | RED 3：同 K4。`compress.py` 的 `p.get('summary', '')` 在单引号 + f-string 里，这一支是它唯一的读法 |
| K6 等价刀（预先声明）：Load 判定放宽成 Load/Store/Del | GREEN：今天表里没有"只被写、从不被读"的 payload 键可放宽，尺子量不到。限界，不是收益 |
| K7 等价刀（预先声明）：`rglob` 收回 `glob` | GREEN：预先量过——子包多出的两个文件贡献 0 个 Load 点，所以这一格今天不承重。这一具的意义是记下"等谁出现" |
| K8 等价刀（预先声明）：标签从相对路径退回 basename | GREEN：`src/wolfengine` 今天只有一棵 `__init__.py`，同名还没成灾。`#139` 那一课的预付款 |
| K9 `"valid": sorted(valid)` 改成 `[]`（原本被抹平，抄本还在） | RED 2：`test_a_private_citation_passes_and_a_public_one_does_not_become_illegal` + `test_m4_citation_rates_have_the_designed_numerators`。证的是证人搬家以后仍然咬得住原本 |
| K10 rung4 的复用条件 `all(` → `any(`（编造编号被真复用进最终动作） | RED 1：`test_the_salvage_never_launders_a_second_kind_of_violation`。挪家后的那条断言同时要求 `valid == []` 与 `invented == []`，只看抄本的旧写法在这里会漏 |
| C1/C2 负控制：白名单 `PAYLOAD_BASES` 多一个语料里没人用的名字 `pp`；把 `_write` 里新写的那句注释换个说法 | GREEN，符合：认名字的尺子不该被"多一个名字"改动，判据更不该读注释散文 |

还原：12 段替换后逐文件 `cmp` **全部字节一致**；`find . -name '*.pyc'` 在 `PYTHONDONTWRITEBYTECODE=1`
下 0 命中（无字节码投毒）。

#### 这一片改到的旧数

`tests/test_payload_shape.py` 七条 → **九条**（新增：抄本那条、"读的是哪本字典"那条），README 里没有
它的条数格，条数主张住在 `docs/iterations.md` 的〈载荷普查〉那一节；套件总数 984 → **986**，11:22:26Z
那一跑 `986 passed in 72.69s`，最终树上又数了两跑（11:27:29Z `79.37s`、11:31Z `109.37s`）仍是 986——
同一份树，散文改动之间秒数动了 36 秒，「墙钟不是账」那一格又添一例。落盘的产物代码只有两处，且都是**减**：`agent._write` 少抄一格、
`events.py` 形状表少五格。`data/**/*.jsonl` 那 12 份是历史语料，里面的 `payload.evidence` 还在盘上，
读侧现在没人读它——所以这一片之后，旧日志里那一格是"当年写过"，不是"现在还在写"。

#### 这一片顺手撞上的另一件事（记成 `#141`，没动它）

三跑全量里有一跑（11:25:23Z，`1 failed, 985 passed in 100.24s`）红在
`test_a_finished_batch_prints_its_line_and_exits_0`：rc=1，stdout 印着 `canary INVALID_DRIFT`。
它单独重跑 4 passed，前后两跑全量都是 986，所以红的是**宿主机负载**不是这一片的改动——但可以复现：
桩对每个请求 `time.sleep(0.02)`（`tests/test_loopback_endpoint.py:59` 那句注释主张"定长：让 canary 的
头尾比测的是端点，不是建连"），而 `report.canary_verdict` 拿头尾探针延迟**中位数之比**越不过
`DRIFT_RATIO=1.5` 当判据，20ms 的底子上只有 10ms 的余量，GIL 争用与调度抖动跨得过去。
"换权重"（`answer` 变了）和"这台机器此刻很忙"共用同一个终态码，是 plan §8 第 5 条 / R7 预注册过的
语义，**不是这一片该单方面收的口径**——所以只登记不修：修哪一侧（判据分码，还是夹具让延迟量得出来）
不在这一格里做。**而且这不是第一次现世**：`#129` 那轮 B1 那具刀在 pass 2 多弄红一条批次收尾用例，
当时读那批目录里的 `drift.md` 拿到 `异常探针：latency×0.14`、五个探针答案逐字相同，已经定性成
"canary 延迟通道对负载敏感"并留在文档里。那一轮选择的是不改判据；这一票要回答的是同一件事第二次
出现以后还走不走同一条路。


### 手册里 32 条"复现照某个脚本跑"指到的东西全部不存在（`#142`）

缺口是数出来的，不是感觉出来的。11:43–11:46Z 拿一条正则扫手册语料（`README.md` + `docs/*.md`），
凡形如 `/tmp/xxx.py`、`/tmp/xxx.out`、`/tmp/xxx.log`、`/tmp/某目录/*` 的串都算一次命中，再把每个
唯一名字拿 `test -f` 点一遍：手册里 **32 处命中**（README 20、`docs/comparison.md` 5、
`docs/views.md` 5、`docs/metrics.md` 2），README 那 17 个脚本名逐个验过全都不在。句式是「N 具变异
照某个脚本跑（…，x/x CAUGHT，某文件按字节还原）」——那一轮真正的证据是紧跟其后的那张表，
**账留下了，来路指不到**。同族前例是 `#118`（注释里一条没实测过的出处）与 `#91`（"已入库"而那个
目录是 gitignored）。

不批量改写归档。`docs/iterations.md` 里同形状的指针有 **184 处 /tmp 提及、141 处工件命中**（13:15Z
量的；12:00:49Z 补完声明那一趟量到 177 / 137，两次之差正是下面这一节自己写下的那几个形状例子）。
但这些指针是历史记录，把它们抹掉等于宣称那几轮没跑过。改法是把"这些脚本已经不在了"这句话写在开头：
它现在是**第一条规矩**里的一句，而不是第四条（顶部的"三条规矩"数目因此一根没动）。
它同时也是这一节要立的那道闸门的唯一豁免理由——豁免要有形状，见下。

#### 新闸门四条，住在 `tests/test_doc_citations.py`

以前三十条，现在三十四条。四条各自的落点：

* `test_the_artifact_scanner_tells_a_dead_pointer_from_a_scratch_dir` —— 判据先要站在它自己划的
  那条线上。四类工件形状认得出，两类命令行形状放过：`--out /tmp/clipin` 和
  `wolf audit /tmp/clipin/A/*.jsonl` 是**今天还能敲**的命令行，把它们算进来会让文档不敢再写
  "怎么复核"，而这一片要保住的正是"怎么复核"。这道界由正则尾部那一项负向预查划，代价量过：
  手册里现存的 15 处 `/tmp/` 命令行（`comparison.md` 4、`metrics.md` 10、`views.md` 1，同一趟扫的）
  全在那一边。
* `test_the_archive_is_excluded_only_while_it_says_its_scripts_are_gone` —— 豁免没有形状就是洞。
  归档被挡在判据之外的理由不是"历史可以撒谎"，是"历史已经声明过它指的是当时跑过的东西"，所以
  这句话要有唯一读者：先要求归档里确实有工件指针（不然豁免是空转），再要求开头那节里同时留着
  那个路径前缀、"一次性"、以及"重启/消失"里的任一个词。
* `test_the_manual_never_points_at_an_artifact_that_evaporates` —— 手册 32 处清零。
* `test_the_artifact_scanner_scans_every_manual_page` —— 组成证人：上一条件只断言"没有"，把语料
  **收窄**会让它更像绿，所以另钉一条"每一本都在被读"（手册 == 全部 markdown 减那一本归档，且
  README 在其中）。`#126` 用的是同一招。

正则拆成两段字符串拼出来写，是这一片自己撞上的那一格：判据扫的是 markdown，而证人文件既要在夹具里
拼出这个形状、又要在 docstring 里谈它，整串留在源码里会让"讲这一族的句子"变成这一族的第 33 处
命中——`#140` 的自噬那一格同一个形状。

#### 变异电池：一轮，5 kill + 1 负控制

脚本是一次性工件、不入库。12:00Z→12:13Z 一跑到底；每个 stage 各自先量同窗基线（闸门
`34 passed in 3.33s`、全量 `990 passed in 77.33s`），半径在刀下那一跑里量。预注册的预期全部落地，
**没有一具需要事后改判**：

| 具 | 刀 | 判决 | 基线外红的那几条 | 全量半径 |
| --- | --- | --- | --- | --- |
| K1 | 摘掉负向预查那一项 | CAUGHT | 形状那条 + `test_the_manual_never_points_at_an_artifact_that_evaporates` | 2 |
| K2 | 摘掉通配符那一支 | CAUGHT | 只红形状那条（备份 `dir/*` 那一格漏掉） | 1 |
| K3 | 扩展名只留 `py` | CAUGHT | 只红形状那条（`.out`/`.log` 不再算工件） | 1 |
| K4 | 删掉归档开头那句"一次性工件"声明 | CAUGHT | 只红豁免那条 | 1 |
| K5 | 把 `metrics.md` 也从手册语料里排除 | CAUGHT | 只红组成那条（"没有"那条必须不红，它断言的是缺席） | 1 |
| K6 | `_artifact_paths` 改成保序去重 | SURVIVED | 无——负控制，半径 0，今天的语料里没有重名命中 | 0 |

K1 是全表唯一一具双红的：假缺陷同时把手册那一格顶红，也就是命令行这一边的代价不是纸面推的。
K6 那一具证明判据不是靠巧合绿（去重与否在现存语料上取到同一串）。

墙钟从 77.33s 漂到 89–131s（宿主负载，无 timeout、无一跑需要重跑）。还原：两处被改文件
（`tests/test_doc_citations.py`、`docs/iterations.md`）按字节写回、收尾核对 sha 一致
（`2639f37d851b` / `4774416da8a5`），干净树再跑一遍闸门 `34 passed in 4.69s`。

#### 这一片改到的旧数与句子

处置：手册那 32 处摘掉路径，句子改成"这一节那张表就是记录 / 电池脚本是一次性工件、不入库"，
**具数、日期、按字节还原的事实一个不动**。README 里〈测试〉那一节发的用例数从三十拧到三十四，
`docs/comparison.md`、`docs/metrics.md`、`docs/views.md` 各 5/2/5 处同改；归档一处没摘，只是开头多了
一句声明、末尾多了本节自己那 7 处例子（177 → 184 提及、137 → 141 命中）。产品代码一行没动——这一片
改的全是文档与闸门。

### 手册里那四张具名变异账表搬进了这一份（`#143`）

`#135` 立了规矩——每一片的取证不在手册里——可它当时只搬走散文：README〈测试〉一节末尾还坐着四张
"第 N 具变异红了哪条用例"的账表，64 行、56 条具名变异，全是逐片取证的样子，没有一条是"怎么跑、
跑出来该看到什么"。这一片把它们搬进来，并且给那条规矩补了第一个读者：判据是表头形状
（`| 变异` 打头、下一行是分隔行），不是"这一段属于哪一轮"。

#### D 表：文档引用闸门的三十四具（`#32` / `#44` / `#45` / `#61` 那四批）

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
| D36 `_src_names` 只收 `ast.Name`（丢掉参数名）——**预期活下来** | 见 README 里「活的原因不同」那两句：它防的是误报，不是漏报 |

#### K 表：kind 字面量守卫扩到 `tests/` 的五具（`#33`）

| 变异 | 红用例（红在哪条断言上） |
| --- | --- |
| K1 一处已改好的语料退回字面量 | `test_no_module_compares_an_event_kind_to_a_bare_string`（违规清单；那条行为测试照常绿） |
| K2 守卫不再扫 `tests/` | 同上，但红在**规模断言**：范围缩回 src/ 一个违规都不留，只留下一个空扫描器 |
| K3 原始形式不再被收集 | 两条：主断言红在规模，`test_the_kind_guard_sees_both_tiers_on_a_synthetic_file` 红在原始档没收到东西 |
| K4 第二档"未声明"判定改瞎 | 只有对照那条（真实语料是干净的） |
| K5 第一档收集改瞎 | 同上 |

#### A·B·C 表：§十五 上桌契约的八具（`#34`）

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

#### W 表：`wilson_ci` 三个锚点的九具（`#35`）

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

#### 这一片改到的旧数与句子

README 那四个落点各留一行指针，四张表周围解释规则的散文**一行没搬**——「D15–D21 数的是第三类可
核对的主张」「`_src_names` 收的是能被赋值的名字」那几段讲的是规矩本身，手册正是它们该待的地方。
被改的句子只有把表当账本用的那几处：`#135` 之前写下的「所以下面这张表本身就是记录」改成指向归档，
「红用例具名如下」改成「见……一节的 D 表」，D36 那一格的「见下面那段」改成点名 README 里那一段
（它指的段落留在原地没搬），能力清单里「〈测试〉末尾那张 D 表」去掉限定、补一句"三张表都在
`docs/iterations.md`"，末尾那句「取舍与边界写在那两张表下面」改成点名那几段讲的是哪两件事——
表搬走之后，「那两张表下面」已经答不出"是哪两张"了。

`tests/test_doc_citations.py` 三十→三十四→三十七：`#142` 加过四条，这一片再加三条
（一条钉手册里账表为零、一条钉归档的地板、一条钉判据认的是形状而不是那三个字）。

#### 变异电池（13:50Z 起，7 个 stage：5 具刀 + 2 具负控制）

同窗基线：三道文档闸门 **56 passed**、全量 **993 passed in 60.65s**（红集空）。电池跑的是全量，
而 `README.md` 与 `docs/metrics.md` 都在被扫的语料里，所以这 7 个 stage 期间没有碰过任何文档或测试。
每一具都是"窄跑三闸门定判决 + 全量定半径"，`finally` 里按字节还原并复核 sha256：七具全部「还原：字节
相同」，收尾复查 **993 passed in 59.15s**、红集空。

| 具 | 刀 | 判决 | 基线外红的那几条 | 全量半径 |
| --- | --- | --- | --- | --- |
| K1 | 往 `README.md` 末尾追加一张「变异」打头、下一行是分隔行的账表 | CAUGHT | `test_the_manual_carries_no_per_slice_mutation_tally` | 1 failed, 992 passed（58.06s） |
| K2 | 同一张表追加到另一本页（`docs/metrics.md`）——判据不是只盯 README | CAUGHT | 同 K1 | 1 failed, 992 passed（59.57s） |
| K3 | 判据丢掉「下一行必须是分隔行」那一半 | CAUGHT | `test_a_line_naming_mutations_without_a_separator_is_not_a_tally` | 1 failed, 992 passed（57.95s） |
| K4 | 归档地板 20 抬到 999（钉住那条守卫不是空转） | CAUGHT | `test_the_archive_is_where_a_moved_mutation_tally_lands` | 1 failed, 992 passed（57.19s） |
| K5 | 把 `README.md` 从 `_manual_pages()` 的语料里摘出去 | CAUGHT | `test_the_artifact_scanner_scans_every_manual_page`（`#142` 那条） | 1 failed, 992 passed（54.80s） |
| C1 | 手册页追加一张合法的「判据 / 位置」表 | CLEAN | 无 | 993 passed（63.04s） |
| C2 | 手册页追加一句散文，正文里出现「变异」那两个字但没有分隔行 | CLEAN | 无 | 993 passed（60.29s） |

K5 是这一片**留下的假绿，记在这里而不是抹掉**：README 一旦被摘出手册语料，红的只有 `#142` 的组成证人，
`test_the_manual_carries_no_per_slice_mutation_tally` 当场对 README 沉默——它没有"README 必须在被扫之列"
这条断言，只有"扫到的每一页都不许有账表"。所以这条判据的覆盖面是 `_manual_pages()` 收到什么就在什么上
开火，不是"README 一定被扫"。

### 样例广告的两个变量在代码里零读者，而一条测试正反过来钉着这句假话（`#146`）

**动因是把账重算了一遍，不是看样例长得不顺眼。** 新判据落绿之后，拿它自己的三个正则对着
`git show HEAD:.env.example` 的字节重跑（工作树一个字不动）：旧样例广告 **3** 个名字，代码语料只
读到 **1** 个，缺的是 `WOLF_LLM_BASE_URL` 与 `WOLF_LLM_MODEL`；这两个名字在 `src/` 和 `scripts/`
里各 **0** 处出现。照手册去 `export WOLF_LLM_MODEL=…` 的人拿到的是**静默无效**——端点与模型走的是
`config.py` 的字段默认值那条字面量路，从来不经 `os.environ`；而 `--set` 和 `compare` 又把它们列在
`FORBIDDEN_AXIS` 里，所以也不存在第二条读法能让那两行生效。语料是量出来的三本文件：`src` 与
`scripts` 下正文里出现 `os.environ` 的 .py，即 `config.py`、`transport.py`、`scripts/calibrate.py`。

**这句假话之所以长期看不见，是因为有一条断言在主动要求它成立。** `tests/test_no_secrets.py` 里原本
有一句"`WOLF_LLM_BASE_URL=http` 必须出现在样例里"——样例少了那行就红。所以这一片的第一步不是删
样例，而是把那条反向断言挪回它仍然为真的地方：现在钉的是那个端点字面量必须躺在
`src/wolfengine/config.py` 里（原处注释同时点名"样例只许出现真有人读的名字"）。删广告和摘反向
断言若分成两片，中间那半片就会变成"测试逼着广告留在文件里"的死锁。

新增两条用例，一正一反：
`test_every_variable_the_example_advertises_has_a_reader` 管广告这一侧（样例里每个 `NAME=` 都要在
代码语料里有个读者，且样例不许塌成空广告）；`test_the_env_reader_corpus_actually_finds_the_key_name`
管语料这一侧（`WOLF_LLM_API_KEY` 必须真的被扫到——它只经字段默认值那一支进来，调用点上从来不是
字面量，所以语料或正则一坏这条就红，而不是让上一条对着空集合发合格证）。

#### 变异电池（14:0xZ 起，5 个 stage：3 具咬住 + 1 具声明等价 + 1 具负控制）

同窗基线：三道文档闸门 **58 passed in 1.83s**、全量 **995 passed in 60.70s**（两把红集都空）。电池
跑的是全量，而 `README.md`、`docs/`、`.env.example` 都在被扫或被读的一侧，所以这 5 个 stage 期间
没有碰过任何文档、测试或样例文件。每具同样是"窄跑三闸门定判决 + 全量定半径"，`finally` 里按字节
还原并复核 sha256：五具全部「还原：字节相同」，收尾复查 **995 passed in 60.70s**、红集空——开局与
收尾两次秒数一字不差，这是 `#139` 那一格"同一棵树连跑的秒数连抖了多少都答不出来"的又一个回声。

| 具 | 刀 | 判决 | 基线外红的那几条 | 全量半径 |
| --- | --- | --- | --- | --- |
| N1 | 往 `.env.example` 追加一行没有任何读者的 `WOLF_LLM_NOT_READ=` | CAUGHT | `test_every_variable_the_example_advertises_has_a_reader` | 1 failed, 994 passed（58.27s） |
| N2 | 判据丢掉"字段默认值"那一支（把 `ENV_NAME_FIELD` 的收集顶成一句 `pass`） | CAUGHT | 上面那条 + `test_the_env_reader_corpus_actually_finds_the_key_name`（广告与语料同时失守） | 2 failed, 993 passed（57.65s） |
| N3 | 语料丢掉"这个文件碰过环境吗"的过滤（收下目录下每一个 .py） | 等价（跑之前就声明） | 无 | 995 passed（57.28s） |
| C1 | 样例里追加一行**注释掉的**旧样例名（`# WOLF_LLM_MODEL=…`） | CLEAN（负控制） | 无 | 995 passed（60.26s） |
| C2 | 把键那一行写成带值的形状（`sk` 开头的一段假值） | CAUGHT | `test_gitignore_covers_traces_and_env`（旧的那条形状守卫，不是新判据） | 1 failed, 994 passed（57.54s） |

N3 的等价是有理由的等价：那半过滤只决定"读不读这个文件"，而今天没有被它挡在外面的 .py 里藏着
环境变量名，所以摘掉它语料一字不变——它省的是 IO，不是判据。C1 证的则是这条判据**当前唯一的
绕过口**：`ENV_ADVERTISED` 锚在行首的大写名字，注释行不数。这是声明过的限制（注释掉的一行不是
"你可以去 export 这个"的广告），但要知道它在。

**这一片有一格要记在电池自己头上，不是产品。** 判决表达式写的是"新增红集合 == 预期红集合"，于是
预期为空的两类 stage（等价刀 N3、负控制 C1）在日志里都印成 `CAUGHT 新增红=[] 预期=[]`。上表的
"等价/CLEAN"不是脚本给的标签，是我读过它各自是哪把刀之后认领的。集合相等分不开这两类——要分开
得给每个 stage 带上类型（刀 / 等价 / 对照），这是下一片该补的一行。

这一片改到的句子：手册开头那一节原本只说"非密配置可以入库"，现在点名它们住在哪、为什么不走环境
变量、以及这本广告由谁数着；同一段里对扫描集的列举漏了本页自己（`#135` 起 `SCAN` 就含
`README.md`），一并补上。套件 **993 → 995**：`tests/test_no_secrets.py` 收集数 11 → 13，新增的两条
都在这个文件里。

**顺手量到、但没在这片里修的一件事**：`transport.py` 的清洗表把要擦的变量名硬编码成两个，其中
`MVP_VLM_API_KEY` 在全仓只有这一处出现（没有任何读者），而真正的键名来自 `Config.api_key_env`——
两者没有联结。也就是说哪天 `api_key_env` 换了名字，擦值的那一步会静默失效。这条记成新票，不在
本片里改：它动的是脱敏链，得先有失败测试。


### 手册那句"本仓库还没有任何提交"死于它所在的那一次提交，而查历史这件事从此有了读者（`#145`）

**先拿 `git show` 量那句话的出生。** `git log -S'还没有任何提交' -- README.md` 只返回一格：
`aa0d646`（初始提交，2026-09-23 14:22:23Z）。原文是「查提交历史里有没有混进过 key（本仓库目前
**还没有任何提交**，这两条要等第一次 `git commit` 之后才有对象可查；工作树的扫描已经在测试里了）」，
底下给两条命令：`git log -S'WOLF_LLM_API_KEY' --oneline` 与 `git log -p | rg -n 'sk-[A-Za-z0-9]{20,}'`。
所以这句话**写下来的时候是真的**（README 先于 `git init` 存在），而它骗过的是之后每一个读到它的人：
它死于把自己提交进去的那一刀，此后 24 个提交、以及已经公开到 `origin/main` 的那一份，都把它当现行
说明在发。这一族和 `#146` 那条"扫描集漏了 README 自己"是同一族——不是有人写错，是**散文没人核对**；
差别在于这句还额外让人以为"这件事暂时没法做"，于是把它留成了作业。

**作业这一格现在由套件每次做。** 新增三条用例（都在 `tests/test_no_secrets.py`，判据写成纯函数）：

- `test_the_committed_history_added_no_key_value`：把 `git log -p --all` 的**新增行**扫一遍，只豁免
  脱敏用例自己埋的那枚哨兵；git 不答话、或答出来是空的，`_committed_history_patch()` 直接报错而不是
  让"零命中"冒充干净。
- `test_the_history_scan_is_not_reading_an_empty_corpus`：正控制。不豁免时同一个判据必须在真历史里
  抓到东西，且每一条命中都得点名 `tests/test_no_secrets.py`；外加一层地板——读到的新增行必须多于
  20 000 行。三种失效（`_added_lines` 少一层判断、`git log -p` 参数写错、豁免表放宽成"任何 `sk-` 都算
  自己人"）都长得和"历史干净"一样，这一条就是来分它们的。
- `test_the_history_scanner_names_a_planted_leak`：合成补丁喂判据。真历史是干净的，干净状态下唯一的
  红证据来自"埋一根假的进去"，而那不能往真历史里埋（提交删不掉，而且 origin 是公开的）。

**判据为什么是两半，不是 `HIGH_ENTROPY`。** 在 HEAD `0451f43` 上量的：25 个提交、`git log -p --all`
里新增行 **43 559** 行。`sk-` 形状在新增行上命中 **1** 处——就是那枚哨兵自己。同一批新增行上用
`HIGH_ENTROPY`（28 位以上）扫，命中 **2166** 处（分布在 1900 行里），全文扫是 2908 处：diff 头里
`index abc..def` 那类 40 位哈希、文档里记录的 sha256、`uv.lock` 的完整性哈希。所以历史这一侧用的是
"`sk-` 形状"加"`…API_KEY…= 后面跟着一个不短于 16、且不以 `<` 开头的值"`，前者管值本身、后者管"键名
配长值"这种没按 `sk-` 起的写法。这两个口径都是量出来的历史读数，历史每加一个提交就会动，别当规矩用。

**预注册的两个预期把夹具的半成品抓了出来。** 判据写成两半，夹具就得给每一半各自的证人。第一版夹具里
删除行是纯中文的一句"这一行被删掉了"、占位符写成 `<key>`——拿着这个去预注册 K3（把只数 `+` 改成
`+` 与 `-` 都数）和 K6（摘掉 `<` 占位符守卫）时，两条都会**活下来**：前者没有可误数的对象，后者没有
够长的占位符可放过。于是把夹具改成删除行真的带着一次 `WOLF_LLM_API_KEY=` 加哨兵值的泄漏、占位符写成
22 个字符的 `<REPLACE_WITH_YOUR_KEY>`（短占位符连长度门槛都过不了，"豁免占位符"那一半就没有证人）。
改动在跑电池之前，没有花钱重跑才发现。

#### 变异电池（14:41Z–14:52Z，9 个 stage：6 具咬住 + 1 具声明等价 + 2 具对照）

这一版电池把 `#146` 记在脚本头上那格补上了：**每个 stage 自带类型**（刀 / 等价 / 对照），判决词由
`(类型, 新增红)` 算出来。上一轮"预期红集为空"时等价刀和负控制都印成 `CAUGHT`，这一轮它们印的是
`EQUIVALENT` 和 `CLEAN`——这两个词只有事先声明了类型才可能出现在日志里。同窗基线：窄跑三闸门
**61 passed in 2.25s**、全量 **998 passed in 61.28s**，两把红集都空。九具全部「还原：字节相同」，
收尾复查 **998 passed in 64.19s**。

| 具 | 类型 | 刀 | 判决 | 基线外红的那几条 | 全量半径 |
| --- | --- | --- | --- | --- | --- |
| K1 | 刀 | 默认豁免表从"只豁免哨兵"顶成空表 | CAUGHT | `test_the_committed_history_added_no_key_value` | 1 failed, 997 passed（57.94s） |
| K2 | 刀 | `_added_lines` 的 `elif` 顶成 `elif False:`（一条新增行都不收） | CAUGHT | 地板那条 + 合成补丁那条 | 2 failed, 996 passed（59.38s） |
| K3 | 刀 | 把"只数 `+`"放宽成 `+` 与 `-` 都数 | CAUGHT | `test_the_history_scanner_names_a_planted_leak` | 1 failed, 997 passed（59.29s） |
| K4 | 刀 | 长度门槛 16 抬到 40 | CAUGHT | 同上 | 1 failed, 997 passed（58.40s） |
| K5 | 刀 | `_committed_history_patch()` 直接 `return ""` | CAUGHT | 地板那条（主判决当场对空文本发了合格证，没人看见） | 1 failed, 997 passed（57.96s） |
| K6 | 刀 | 摘掉"值以 `<` 开头算占位符"那一半守卫 | CAUGHT | 合成补丁那条 | 1 failed, 997 passed（57.86s） |
| E1 | 等价 | `KEY_ASSIGN` 的名字部分从"含 `API_KEY` 的大写名"收窄成恰好 `WOLF_LLM_API_KEY` | EQUIVALENT（如预声明） | 无 | 998 passed（58.16s） |
| C1 | 对照 | 地板从 20 000 降到 1 000 | CLEAN（如对照预期） | 无 | 998 passed（60.05s） |
| C2 | 对照 | 豁免表里多一条历史中不存在的项 | CLEAN（如对照预期） | 无 | 998 passed（61.30s） |

K5 是这一片最值的一具：`git log -p --all` 里 git 永远返回 0，所以"读不到东西"这件事在真实运行里
长得和"读到了且干净"一模一样。把 `_committed_history_patch()` 顶成 `return ""` 之后，主判决
`assert not _history_offenders("")` 是**绿的**——它对着空文本发了合格证。红的只有地板那条。这一具
就是地板存在的理由，也是它为什么必须断言"不豁免时抓得到东西"而不只是"读到的行数够多"。

E1 的等价是**有意的收窄没有代价**：真历史和夹具里出现的键名只有 `WOLF_LLM_API_KEY` 这一个，所以
把通配改成字面量今天不改变判决。但它是会烂的那种等价——哪天来了 `SECOND_API_KEY`，判据就静默瞎了。
记在这里，下次加键名时先动这条。

**一条顺带被钉住的规矩**：那枚哨兵的字面量只能待在 `tests/test_no_secrets.py` 里。文档、手册、归档
只要把它抄进正文，被提交之后地板那条就会红（它的 `stray` 断言要求每一条命中都点名这个文件）。这不
是坏事——"密钥的形状只该有一个产地"本来就是这条判据想说的话；手册因此只描述它的形状（`sk-` 加 8 位
以上），不印它。

这一片改到的句子：手册里那两条命令从"等第一次 commit 之后才有对象可查"改成"这件事现在每次跑测试
都会做"，并写明豁免了谁、扫不到历史时报错而不是沉默；`tests/test_no_secrets.py` 的模块 docstring 补
了第四条主张（提交历史的新增行），并把"想查历史请用 README 里那条"这段留给人的作业换成了
`_committed_history_patch()` 的描述——顺手把那一条里写错的"24 位以上"改成正则真正的 `{28,}`。
套件 **995 → 998**：`tests/test_no_secrets.py` 收集数 13 → 16，三条新增都在这个文件里。

### 手册里"细节在那一节"的指法第一次有人核对指得着：10 处点空，5 处点的不是节而是术语（`#144`）

`#135`/`#143` 把逐片取证搬进这一份之后，手册里"细节在别处"的入口几乎全写成尖括号指针，而**没有任何闸门
核对过读者照着这些标题搜不搜得到**。这一片先给指针立判据，因为它是"把 README 能力清单那 225 行压成指针
形"那一片的前置：搬家之前，落点得先是点得着的东西——顺序和 `#142`/`#143` 一样，先有尺，再搬家。

扫法住在 `tests/test_doc_citations.py`（`POINT` / `_flat` / `_headings` / `_pointer_corpus` /
`_dead_pointers`），判据只有一条形状：**指针去掉标点后必须是某一节标题（同样去掉标点）的子串**。三条取舍
都是量出来的，不是拍的：

* 标点不进比对（反引号、直引号与弯引号、冒号、逗号、顿号、括号、空格、省略号尾巴、破折号、波浪线）。
  15:41:15Z 那一趟按"尖括号内至少四个字"扫，53 处分档：连标点抄全的 13 处、去掉标点才相等的 1 处、
  靠子串指到的 16 处、靠省略号尾巴的 3 处、多解 12 处、零命中 8 处。一条"逐字相等"的判据会把 53 处里的
  **40 处**报成缺陷，其中 32 处本来就指得到（1 处只差标点、16 处靠子串、3 处靠省略号、12 处同时指到两处）
  ——手册里没有一个作者会把整节标题连冒号带反引号抄一遍，这种判据红三次就会被整体关掉。
* **多解放过**：12 处同时指到 README 那一节和归档里复述它的标题。这一族管的是"点空"，消歧是另一件事；
  把歧义立成缺陷会逼作者把指针写得更长，而写长了的指针恰恰最容易随一次改名漂走。
* 省略号不配特例：`〈行号闸门第一次有人看着 .py 里的号〉` 那种"抄半句再加尾巴"的写法，去掉尾巴之后本来
  就是标题的子串。为它单开一支判据等于把同一条形状抄两遍——而"同一句判据抄两遍、只有一遍有断言"正是
  `#97` 那一族报的形状。

第一跑（15:45:25Z）报出 **10 处**，处置全落在句子上、判据一字未改、豁免一条没给：

| 处 | 它点的是 | 为什么点空 | 落到哪 |
|---|---|---|---|
| README 第 59 行 | 那一节的内容（四个字） | 节名被缩写成了它的内容 | 改指真标题（README 第 19 行那一节） |
| README 第 615/619/624 行 | `#76`/`#77`/`#78` 那三节 | 任务号写进了尖括号里，而真标题在冒号前还有一整句 | 省略号形，任务号移到括号外面 |
| 归档第 1237 行 ×3 | 记账的三个类别 | 那**不是节** | 换成「」 |
| 归档第 2154 行 | 本归档第 1451 行那一节 | 作者凭记忆写的是那一节表尾那一行的字 | 改指真标题 |
| 归档第 4086 行 | `wolf compare` 产物里的小节名 | 手册里没有这一节 | 换成「」 |
| 归档第 5574 行 | README 里一句散文的短语 | 同上；同一短语在第 5396/5497 行一直是引号写法 | 换成「」 |

这一族由此定了手册的一条书写规矩：**尖括号只用来指节，术语、类别、产物里的小节名写「」**。扫面随之从
64 处降到 15:47:04Z 的 **59** 处（README 28、归档 28、`comparison.md` 3），差掉的 5 处正是上面换成「」的
那五个术语。规模那条用例的地板取 50，并且点名 README 与归档两个大户都必须有份：低于地板说明扫法坏了，
而不是文档变干净了。

顺手被自己抓到三处：

* 加了三条用例之后，README 给这只文件写的活账还停在改之前的数，条数闸门与收集数闸门各红一次、报的是
  同一句话——"作者写完没跟着数"这一族的第六次。README 那句现已跟着改成 40 与 40。
* **而这一片自己刚被它想收的闸门红了一次**：上一条落盘时写的是"停在 37 与 37"那两个旧数，并且点着了一只
  文件的名字——两道闸门立刻把这句**历史复述**当成 live 主张报回来（模块名与数字挨在同一句里，判据分不开
  "当时是多少"和"现在是多少"）。这是 `#72` 那一族（行号闸门把历史复述当 live 引用）在计数侧的孪生，处置
  也一样：改句子，不改闸门——归档里讲旧数的时候不点文件名。
* 数每页指针数那句推导第一次落盘就带着一个错（在推导式里引用另一个推导式的循环变量，`NameError`）。
  它会在**收集阶段**就把整个文件报红，反而把上一条缺陷藏住——两处都是先跑本地脚本才看见的，套件里没有
  留下证据。

限界三格，点名在这里，不等下一次有人记得：

* 判据只看文本，**分不清"指得到"和"指对了"**。归档第 1196 行那处点的是"条数那道闸门"而不是某一节，
  蒙中的恰好是它自己那一节的标题（「条数和收集数是两句真话」开头）——落点对、理由是错的，这一格没有为
  它改判据。**收紧成前缀判据的代价今天量过：0 处**（15:53:10Z，59 处里每一处去标点后都是某一节标题的
  开头）——所以前缀形此刻不比现判据贵，也不比它多抓任何东西；留着子串是因为读者的搜索本来就是"标题里的
  一段话"，前缀会把"从中间指过去"这种合法写法变成红。等真出现一处该报而没报的，再按当时的代价换。
* 同一趟量的多解是 **20** 处（判据落地前按"至少四个字"扫那一次是 12 处）：涨的 8 处几乎全是把扫面降到
  两个字之后进来的「测试」——它同时是 README 那一节的标题，也是归档里若干节标题里的一段话。放过它们和
  上面那条是同一个理由。
* **举例不能贴死指针的原形**：任何一份 .md 里两处尖括号之间两个字以上的东西都在扫面里，包括讲这一族
  缺陷的那些句子。上面那张表因此只能描述 README 第 59 行那处"点了什么"，不能把它原样贴出来——和 `#61`
  那条"错用例名只能描述不能贴出来"同族。标题行本身也在扫面里，所以这一节的标题通篇没举"某某标题"的样子。

第一轮电池（15:49–16:04Z，12 个 stage：9 刀 + 1 具声明为等价 + 2 具负控制）里有三格不符预注册，全部在
第二轮之前修掉，根因各自记在下面——它们不是刀的缺陷，是我这边两处预期写反加一处夹具说谎：

* **P1（把 `_dead_pointers` 改成永远返回 `[]`）预注册成"真实语料那条与合成用例那条都红"，实际只红合成
  那条。** 方向我搞反了：真实语料那条断言的是"**没有**死指针"，扫描器瞎掉正好让它发合格证。这一族里
  `assert not bad` 那一条**永远不能当检测能力的证人**，能证的只有合成用例。这条理由本来早就写在文件
  docstring 里，我第一次预注册时没读自己写的那句。
* **P2（摘掉"去标点就剩空串"那半道护栏）整套 0 红——一具真·等价刀，根因在夹具**：我拿一处"两个全角
  标点"当例子，可其中那个句号**不在 `PUNCT` 表里**，它扁平化之后还剩一个字符，报不报由后半条判据说了算，
  前半条从没被点着过。夹具改成两个都在表里的标点；那条 assert 的说明文字（"去掉标点就剩空串的指针必须
  报"）当时是一句**没被执行过的主张**，一并改对。
* **P7（把归档从语料里排除）预注册成"只红规模那条"，实际两条都红。** 这次反在**低估**：
  `_pointer_corpus()` 是"被扫的页"和"可指到的标题"两个用途共用的一份清单，摘掉归档会连它名下那 28 个
  标题一起摘掉，README 里指归档节的那些指针当场全成死指针。这一具因此比预注册更值得留——它证明归档不只
  贡献条数，它还是**落点的提供者**。

第二轮（16:12:32–16:28Z，13 个 stage：10 刀 + 1 具声明为等价 + 2 具负控制）在上面三处修完之后重跑整趟，
并补一具反方向的刀 **P11**：把子串判据里的否定去掉，于是"指得到的"全被报成死的。前九具全在"漏报"那一侧，
真实语料那条缺一个"会虚警"的证人——没有 P11，把判据写成"只要匹配得上就报死"它一样绿。

开局基线：文档闸门 40 passed、全量 1001 passed，两半红集都是空；收尾复查同样 40 / 1001 全绿，最终树与
基线 sha256 前 12 位相同（5d66dafd9a78），13 具每具的"还原：字节相同"都逐条打在册。**13/13 全部按预期，
0 格不符**。

| 变异 | 落在哪一处 | 新增红 | 全量半径 |
| --- | --- | --- | --- |
| P1 | `_dead_pointers` 改成永远返回空表 | 合成用例 | 1 failed, 1000 passed |
| P2 | 摘掉"去掉标点就剩空串"那半道护栏 | 合成用例 | 1 failed, 1000 passed |
| P3 | 扫面从"两字以上"放宽到"一字以上" | 合成用例 | 1 failed, 1000 passed |
| P4 | `_flat` 换成恒等（标点不再洗掉） | 真实语料 + 合成用例 | 2 failed, 999 passed |
| P6 | 标题行只认二三级（不认四级） | 真实语料 | 1 failed, 1000 passed |
| P7 | 归档被排除出语料 | 真实语料 + 规模 | 2 failed, 999 passed |
| P8 | 规模地板从 50 顶到 5000 | 规模 | 1 failed, 1000 passed |
| P9 | 子串判据收成逐字相等 | 真实语料 + 合成用例 | 2 failed, 999 passed |
| P10 | 报告里丢掉页名，只留行号与原文 | 合成用例 | 1 failed, 1000 passed |
| P11 | 子串判据的否定去掉：指到的全报死 | 真实语料 + 合成用例 | 2 failed, 999 passed |
| E1 | 报告里对指针多做一次 `strip()` | 0 红（声明为等价） | 1001 passed |
| C1 | 只改规模那条用例的 docstring 措辞 | 0 红（负控制） | 1001 passed |
| C2 | `_flat` 的形参与局部变量改名 | 0 红（负控制） | 1001 passed |

"真实语料""合成用例""规模"分别指 `test_every_section_pointer_in_the_manuals_points_at_a_real_heading`、
`test_the_pointer_scanner_fires_on_a_dead_title_only`、`test_the_pointer_scanner_is_not_reading_an_empty_corpus`
三条用例；窄跑（只跑这一只文件）与全量的红集在 13 具里逐具一致，没有一具靠"另一只文件里恰好也红"蒙过判决。

**把散文换成指针之前回查落点，顺带抓到 README 的两处时间戳与归档不是同一趟。** 这一片的落点本来就在
`#59`/`#61` 那两节里，挨个读过去时对出来两句假话：README 能力清单写「修前量过一次（07:53:44Z…）」，而归档
`#59` 那一节写的是 07:36:16Z，两处引的却是同一份读数（1725 字节的观众视图、41 行 12 帧的 stderr），且归档
那一句紧挨着点名了跑这读数的探针工件（`probe59_prefix.py` 与它的 `cmp` 还原）——README 那个时间在 docs/ 全树
只出现这一次，没有任何一趟的账对它。同一节里「08:28:40Z 那一轮把 C10 报成等价体」也一样：那份"三份语料
逐字节相同"的复刻在归档里两处点名是 08:25:47Z 的（1218 与 1239 行），同一趟里换办法的 08:48:51Z 两侧倒是
相符，而 08:28:40Z 同样全树零出现。两处都按归档改（归档是取证的那一份，README 是摘要），`docs/` 与
`tests/` 一字未动。**时间戳不在任何一道闸门的扫描范围里**——行号、条数、用例名、出厂值都有闸门，
`07:53:44Z` 这种串没有，它只能靠"回查落点说的是不是同一件事"被抓，而那正是压缩那一片必须先做的原因。

读数：三道文档闸门 61 → 64 条（`tests/test_doc_citations.py` 37 → 40）；套件 998 → 1001；
扫面在散文落盘之后是 62 处指针（16:11:32Z：README 30、归档 29、`comparison.md` 3），死指针 0 处，
地板仍是 50。

### 被硬换行截断的指针与没人核对过的具数（`#144b`）

上一片立了指针判据之后，第一件事不是搬家，是回读判据自己漏了什么。16:54:24Z 回读时看见的不是"漏了一处"，
是**整族的口径都少算**：`POINT` 的字符类把闭括号和换行一起排除在外，于是散文里的硬换行被当成了"这里没有指针"。这一只
文件是给人读的行宽排版的，一句话的尖括号经常跨在两行上——那正是被这一条排除掉的形状。

盲区是量出来的，不是猜的：同一棵树两趟扫法，逐行 62 处（16:11:32Z）、折叠 67 处（16:55:03Z）。少的 5 处
全列在这里，按 17:18:41Z 的现号——README 第 531、543、558 行，归档第 1009、5051 行（16:55:03Z 那一趟里
README 的三处在 530/542/557，下面那三处聚合数改成各批自己的具数之后各下行一格）。这 5 处**既不进判据也不进
计数**，所以判据不红、地板也不动：盲区的全部表现就是"没有红"，这就是它能在两轮自审里活下来的原因。

改法是两处一起：`POINT` 允许跨行但不许再套一个开括号（有人写了半截括号时，这一条宁可少吞一段，也不会把整节
吸进一次匹配）；`PUNCT` 里加进 `\n` 与全角空格——折掉换行和续行的缩进，那串字符才是那一节的标题。报红时给的
行号是**开括号那一行**，不是闭合那一行。判据仍只有一条形状：去标点后必须是某一节标题（同样去标点）的子串。

光改扫法不够，因为真实语料那条断言的是"没有死指针"，把 `POINT` 改成接不住任何形状它一样绿（上一片的 P1 就是
这个方向）。所以补一条扫面完整性的正控制：每一页 `扫到的处数 == 开括号数 == 闭括号数`。三数不等接住的是两种
不同的坏——某种形状看不见，以及有人写了半截括号（那既不是指针也不是标题，逐行扫法会静默跳过）。这条控制第一次
开火是对着这一节自己的：它把讲正则形状时贴出来的那只**单只闭括号**也算成了不平衡。判据没有为反引号开口子——
一旦"括号在代码式里就不算"，作者就能用反引号把半截指针藏起来，于是这一格和 `#144a` 那条"错用例名只能描述不能
贴出来"是同一条规矩：**形状只能描述，不能贴原形**。

第二半是这一片真正的缺口。`#144` 那一片把能力清单里 225 行逐片取证压成「判据与……见尖括号那一节」那一形，
压完之后留下的句子长这样：九批电池各批的具数见〈日志读不下去的时候：砍断的末行与拿错的文件〉（14 具撕裂末行、
9 具拿错文件、……）。`#144a` 核的是**点得到
点不到**，没有核**这个数在那一节里真写着没有**。具数也是一种入口：读者照着 14 去数那张表，数出 13 具的时候
没有任何东西会替他响。

判据的形状是"两条背书 × 两种定位"，都从真语料里量出来：

* 背书认两种——那一节里写着「N 具+名词」（变异／手术刀／刀三个名词，汉字与阿拉伯两种数、带不带空格都算），
  或那一节里有一张 **N 行的具名表**（表头认「具」「刀」「变异」三种写法）。
* 定位认两种——同一 bullet 里那一处尖括号指针指到的节，或同一 bullet 里点名的 `#票号` 对上节标题里的票号。
* 三条**声明的限界**，各有一条合成用例钉着：跨 bullet 的落点不算（读者照着有数的那一条查仍然查不到）、
  裸的「N 具」不算（同一节里的裸数可能说的是另一批）、README 不给自己背书（拿手册查手册是自我背书，
  背书只从 `docs/*.md` 取）。

第一跑（17:02:33Z）报出 3 处，全是同一个形状：**聚合数**。三处的算术都成立，而归档里没有一个地方写着那个和——

* `#83` 引擎侧七具 + `#84` 测试侧四具 = 11，归档按两片各记各的表；
* 九批 14+9+6+5+4+5+5+5+15 = 68，归档按批记九张具名表；
* 三族 5+8+4 = 17，归档按族记三张。

处置是改句子而不是改判据：把聚合那一形换成各批/各族自己的具数，分量一个不丢。理由和 `#144a` 放过多解是同一条
——**读者能复核的是逐批的那张表，不是别人替他加好的和**；一个加好的和除了"信我"没有第二种用法。

三处假红在改判据之前先撞出来，全部是扫描器的形状假设，句子一字未改：

* 归档写的是「六具变异」而 README 写「6 具变异」，中间那个空格——字符类收紧成 `\s?` 之后两边都认。
* 汉字数：七具、八具、十四具，还有「两具」。加了一支 `_cn_forms`（含「两」）和一位「十」形的解析，
  而不是把归档改成阿拉伯数字。
* 具名表的表头写「| 具 |」「| 刀 |」的比「| 变异 |」多——只认 `变异` 会把三节里现成的表读成没有表。

另一处是 `#108` 那一族在这个文件里的第三次：`8 具` 曾在 `18 具` 里蒙绿。主张侧和背书侧都加了 `(?<!\d)`，
两侧只加一侧等于没加。

曾经有过第三条背书规则（裸的「N 具」也算，只要那一节里出现过这个数）。量完就删了：17:02:11Z 那 20 处有落点的
主张里**0 处需要它**，而它的存在只会给"邻居蒙绿"留空间。删掉一条规则要留证人，所以合成用例里有一条专门断言
裸数不算。

这一片自己也被自己的闸门抓过两次，都记着：(a) 我在一只文件的 docstring 里写了一句带嵌套尖括号的指针，指的是
一节还不存在的东西——指针闸门和那条括号配数的正控制同时报回来，那半截括号删掉了；(b) 计数闸门各红一次
（40 → 42、42 → 45），两道报的是同一句话，"作者写完没跟着数"这一族的第七、第八次。

限界点名在这里：扫面只有 README 的能力清单那 40 条 bullet。全树的具数主张比这多（17:20:29Z 逐本数：归档 78 处、
README 26 处、`metrics.md` 7 处、`comparison.md` 5 处），把这一条拖成"全树具数普查"要同时改掉定位口径
（归档里那些数不是靠指针定位的）和背书口径，那是另一片；这一片管的是**给人看的那份清单里每一个数都有人能查**。

电池 14 具（17:34:17Z 开局：`tests/test_doc_citations.py` 窄跑全绿 45 条、全量 1006 passed in 72.33s，
红集两边都是空；树 sha256 前 12 位 b34f186185e8）。11 刀 + 1 具声明为等价 + 2 具负控制，
**14/14 全部按 17:29:08Z 那一趟只读预注册命中，0 格不符**；每具的"还原：字节相同"逐条打在册，
收尾复查（18:02:18Z）全量仍是 1006 passed、树 sha 与基线相同。

| 变异 | 落在哪一处 | 新增红 | 全量半径 |
| --- | --- | --- | --- |
| B1 | `POINT` 的字符类收回到不跨行 | 括号配数 + 跨行指针 + 真语料具数 | 3 failed, 1003 passed |
| B2 | 扁平化时不再折掉换行 | 真语料指针 + 跨行指针 + 真语料具数 | 3 failed, 1003 passed |
| B3 | 报告里改报闭括号那一行的行号 | 跨行指针 | 1 failed, 1005 passed |
| B4 | README 也收进背书来源 | 具数规模 | 1 failed, 1005 passed |
| B5 | 缩进续行各算一条独立 bullet | 真语料具数 | 1 failed, 1005 passed |
| B6 | 背书不认汉字数（去掉 `_cn_forms`） | 合成具数 + 真语料具数 | 2 failed, 1004 passed |
| B7 | 背书要求数字与「具」之间那个空格 | 合成具数 + 真语料具数 | 2 failed, 1004 passed |
| B8 | 具名表的表头只认一种写法 | 合成具数 + 真语料具数 | 2 failed, 1004 passed |
| B9 | 背书串去掉数字的左边界守卫 | 合成具数 | 1 failed, 1005 passed |
| B10 | 定位只认指针不认任务号 | 合成具数 + 真语料具数 | 2 failed, 1004 passed |
| B11 | 具数地板从 15 顶到 5000 | 具数规模 | 1 failed, 1005 passed |
| E2 | 主张侧去掉左边界守卫 | 0 红（声明为等价） | 1006 passed |
| C1 | 背书两种写法的先后顺序调换 | 0 红（负控制） | 1006 passed |
| C2 | 只改 `_states` 的 docstring 措辞 | 0 红（负控制） | 1006 passed |

七个简称分别指 `test_every_section_pointer_in_the_manuals_points_at_a_real_heading`（真语料指针）、
`test_the_pointer_scanner_reads_a_pointer_broken_by_a_hard_wrap`（跨行指针）、
`test_the_pointer_scanner_sees_every_open_bracket_in_the_real_corpus`（括号配数）、
`test_every_knife_count_in_the_capability_list_has_a_ledger_that_states_it`（真语料具数）、
`test_the_knife_ledger_check_bites_on_each_of_its_two_rules`（合成具数）、
`test_the_knife_ledger_scanner_is_not_reading_an_empty_corpus`（具数规模）；指针那族的地板用例
`test_the_pointer_scanner_is_not_reading_an_empty_corpus`（指针规模）这一趟没有一具碰到——
B1/B2 被更早的证人接走，而它只数条数、不判指得着指不着。窄跑与全量的红集在 14 具里逐具一致
（每具"全量名册多出的"都是无），没有一具靠另一只文件里恰好也红蒙过判决。

**E2 这一具要留在那儿，但它没有证人。** 主张侧那个 `(?<!\d)` 摘掉之后 0 红：数字侧是贪婪的
`[0-9]+`，本来就不可能从一位数的中间开始匹配，所以这一半守卫是装饰性的；有证人的只有背书侧那一半
（B9 → 合成具数）。这与 `#97` 那一族同形——一句判据抄了两遍、只有一遍有断言——区别是这里我知道是哪一遍
没被点着，并把这句话写进册子而不是删掉守卫：留着它，哪天主张侧换成非贪婪或换成分支形，B9 那一族的对偶
就会红。

**开跑前那一趟锚点预检抓到一处假归因。** B10 的锚点（`if any(w and w in flat ...) or any(f"#..."`
那一整段）在这只文件里出现 **两次**：判据 `_unbacked_knife_counts` 里一次，规模用例里那条同形状的推导式
再一次。`str.replace` 不带计数，会把两处一起改掉，而 17:29:08Z 的预注册只重实现了判据那一处——也就是说
预期名册是按"只改一处"量的，跑出来的却是"改两处"。锚点收窄成连同前一行 `located = [` 一起匹配之后才
唯一（规模用例那一处是 `located += [`，不跟着中刀）。这是"预注册重实现的那处 ≠ 替换改到的全部那几处"
第一次被抓到，从此每具电池开跑前先过一遍全表锚点唯一性检查，17:34:07Z 那一趟报的就是这 1 处、改完
14 具 0 处问题。

墙钟在这一趟里抖得比平时大：同一份全量在窗口内从 72.33s（基线）走到 134.45s（B3 那一具），窄跑也从
4.34s 走到 14.50s，而 17:57 之后三具的窄跑又回到 5s 附近。这些数只回答"跑完了没有"，不参与任何判决
（手册里那条"墙钟不是账"在这一趟又多了一格证据）。

读数：三道文档闸门 64 → 69 条（`tests/test_doc_citations.py` 40 → 45）；套件 1001 → 1006
（17:17:46Z `--collect-only` 数到 1006）；扫面在判据落盘时 67 处（17:09:56Z：README 33、归档 31、
`comparison.md` 3），这一节与手册那一段散文落盘之后 69 处（17:25:35Z：README 34、归档 32，两处各 +1 ——
归档那 +1 是本节引用的那句真指针，README 那 +1 是讲这一片时新写的一句，而它**自己就是断在两行上的**，
新扫法接住了它）。死指针 0 处，地板仍是 50；能力清单里 22 处具数主张全部有落点（17:09:30Z；改形之前
17:01:25Z 是 23 处、其中 3 处没有落点），两条背书规则 17:10:15Z 各 20 / 2 处有读者，
两种定位 17:18:53Z 各 7 / 7 处、两者都占 8 处。

收尾复跑（这一节的电池名册与手册那两处读数全部落盘之后）：三道文档闸门 69 passed（18:09:54Z；把下面这几行
写进去之后又复跑一次，18:12:21Z 仍是 69 passed）、全量
1006 passed in 92.99s（18:10:03Z 起跑、18:11:37Z 收尾）、扫面 69 处死指针 0 处、能力清单 22 处具数主张
全部有落点（18:07:52Z），手册仍是 717 行——这一片对那一份 .md 的每一处改动都走行内替换，因为本节引用的
那 5 处行号（手册三处、归档两处）只要有一侧变了行数就全漂；改完之后按"散文落盘 → 重跑闸门 → 才写读数"
的顺序收尾，最后一次的 69 与 1006 才是这一片的账。


### 端点回显里的那个名字，终于和发请求用的是同一个（`#147`）

**动因是上一片留下的那句话。** `#146` 收尾时"顺手量到、但不在这片里修"的那一条写的就是这里：
`transport.py` 的清洗表把要擦的变量名硬编码成一支两份的名单，而真正的键名来自
`Config.api_key_env`，两者没有联结。这一片来收，理由和当时写的一样——它动的是脱敏链，得先有失败
测试，不能顺手改。

**先量那个第二名字还剩几个读者。** 18:19:35Z 全仓扫（`src/`、`tests/`、`docs/`、`README.md`、
`scripts/`、`.env.example`）：产品代码里那一行是它唯一的**出现**处，也就是零读者、零调用者；剩下
唯一的一处是本页 `#146` 那一节里记载"它有零读者"的那句散文。名单里躺着一个没人配置也没人擦的名字，
比名单短更糟的是它让人以为名单覆盖了两处来源。这一行随这一片一起删掉。

**RED 分三条，失败的理由各不相同。** 18:14:37Z 第一条红：把键挪到一个第三名字下（`Config` 支持，
`require_key()` 照着取），发请求照样带上它，而擦值那一步只认名单里那两个名字，于是端点回显的明文
直接进了 `res.error`。第二条是参数化展开的两格，让改过名字的键在**两条** `except` 分支上各撞一次——
这一格不是为了多一个读数，是因为那两处的 `error=` 各写了一遍，把联结只补在其中一处，另一处照样绿着
（电池里 S3、S4 两具分别只红一格，量的就是这件事）。

第三条红在最不像会红的地方。`transport.py` 的非 200 分支先做 `r.text[:300]` 再擦值，于是一枚跨切口的
值——前面 10 个字符留在切口内、后面 11 个被切掉——不再等于 `replace` 要找的那个整串，谁都没擦掉它，
而日志里存着明文的前缀。18:18:30Z 这一格的红点名的是**前缀**断言，整串断言当时是绿的：如果把测试写成
`PLACEHOLDER not in res.error`，旧代码会通过，因为它留下的那 10 个字符不构成整串。这一族记忆里已经
记过（"报告里带引号的原文会洗白断言"），差别在这里是**截断**洗白，不是引号。所以那条用例里同时有一条
正控制（`"x" in res.error`：正文还是要记的，不许靠清空 `error` 变绿），它由电池里的 C1 那一具单独证明
自己会红。

**一处证不出来的联结，留账不删。** 四个 `error=` 调用点里有一处是 `unparseable body: {e}`。18:20:30Z
实测：`e` 是 `json.JSONDecodeError`，它的 `str()` 只有"line 1 column 2 (char 1)"这一类行列号，body
进不去——同一份 body 里放着那枚哨兵时，`str(e)` 仍然不含它。也就是说这一处的 `key_env` 参数今天没有
证人，也不是没写测试，是**造不出证人**（除非伪造一个把 body 抄进消息的异常，而那个异常 httpx 不会抛）。
按上一片 E2 那一形记：**这一具要留在那儿，但它没有证人**。留着而不是退回默认名，是因为默认名那一侧的
依据（`redact()` 手里没有 config）恰恰是这一片要收的限界，参数写下去之后，"这一处也认配置"这件事在
代码里是显式的，而上面那句量过的结论说清了它今天为什么量不到。

**限界跟着挪了一格。** `redact()` 的字符串分支仍取出厂名，它手里确实没有 `Config`；这条以前写在
`tests/test_no_secrets.py` 那条"样例广告要有读者"的 `KNOWN_LIMIT` 里，引用的正是那份两份名单的字面量。
字面量没了，那段话就得跟着重写：现在 `WOLF_LLM_API_KEY` 之所以被记成"有读者"，靠的是 `config.py` 里
那个带 `env` 的字段默认值，清洗表这一侧不再有任何独立名单——广告权只可能来自配置。

**这一片改到的历史读数。** `tests/test_transport.py` 顶部多了一行 `import pytest`，那一支文件的四处
行号引用（归档三处、测试 docstring 一处）整体后移，行号闸门先红后补；另外归档里那句"现 16 条"被
计数闸门顶成了新数——那是 `#93` 那天的读数，处置口径同 `#73`：改回汉字（"那时十六"），别再拿今天
的条数去追一句历史。测试 docstring 里那句点着 `transport.py` 一百二十七行的更麻烦：它说的是**改前**那一行用裸 float
传下去，而 134 行现在是 `httpx.Timeout(...)`，把号挪过去等于让一句话指着反驳自己的代码。这一处不挪号、
改句子（"改前那一行"），这是 `#126` 那句"没有一处靠豁免消掉"里"改句子"的那一支。

**这一节自己也被闸门抓了一次，抓在新落的那一段里。** 上面这段第一次落盘时，"点着一百二十七行"那句
写成了文件名紧跟冒号紧跟号的形——正是这一片判定为死号的那一形，18:27:19Z 报红。它说明闸门读的不是
翻好的旧账，是刚写下的这一句；处置和别处一样：讲历史里的号就用汉字写，别留点号形。

**电池 `mut147.py`（18:30:08Z 起，七阶段全量口径）。** 基线 1010 passed in 99.90s、新增红 0 具，
还原后再量一次 `transport.py` 的 sha256 前 12 位与落刀前相同（`f7db1618fbba`）。名册写在落刀之前。

| 变异 | 落在哪一处 | 预期新增红 | 实测 | 全量半径 |
|---|---|---|---|---|
| S1 名单退回硬编码两枚 | `_scrub_string` 里那句按配置取值，换回两份名单的 `for` | 4 格 | 5 格（多一具假证人） | 5 failed, 1005 passed（103.15s） |
| S2 只漏超时那一支 | 第一条 `except` 的 `error=` 退回单参 | 1 格 | 1 格，红 `[slow-read]` | 1 failed, 1009 passed（102.85s） |
| S3 只漏没建连那一支 | 第二条 `except` 的 `error=` 退回单参 | 1 格 | 1 格，红 `[never-connected]` | 1 failed, 1009 passed（98.62s） |
| S4 截断在擦值之前 | 非 200 分支回到切过的 `text` 上擦 | 1 格 | 1 格，跨切口那条 | 1 failed, 1009 passed（101.38s） |
| S5 退掉 unparseable 那处的联结 | 第四处 `error=` 退回单参 | 0 格 | 0 格 | 1010 passed（100.56s） |
| S6 出厂名改成没人设的名字 | `_scrub_string` 签名上的默认值 | 0 格 | 1 格（预注册少写） | 1 failed, 1009 passed（103.55s） |
| C1 非 200 的 `error` 清空 | 同一处改成空串（负控制） | 1 格 | 2 格（两条正控制） | 2 failed, 1008 passed（97.76s） |

S2 与 S3 各只红一格，就是"两条 `except` 分支各自要有证人"这件事的量法：合上一条就看不出另一条漏了。
S5 是这一片唯一"预期与实测都是 0"的阶段，它把上面那句"造不出证人"从推论变成了读数——把第四处的联结
退掉，整套 1010 条一条不红。

**S1 多出来的那一格是 `#77` 那一族第二次落在电池里。** 红的是
`test_a_line_number_written_in_the_docs_still_points_at_the_thing_named_beside_it`，理由不是行为变了，
而是这一具往 `transport.py` 里**多写了一行**，把文档点着的号整体顶偏。它是行数的证人不是行为的证人，
所以记在这里而不处置：S1 真正要问的那四格全部红了，包括 `#86` 那条"声明了却没人读的入参"守卫——
名单退回硬编码后，`key_env` 参数正好变成一个没人读的入参。

**两处 MISMATCH 都是预注册少写，不是假证人，方向也各不相同。** S6 红的是既有的
`test_redact_elides_values_by_key_name_and_by_literal_content`：它一直在替 `redact()` 那条出厂名默认值
站岗，所以上面"限界跟着挪了一格"那一格写的不是无人看守的代码，只是没有**新增**证人——这一具是 battery
替我把这句话纠正过来的。C1 多红的那一条是
`test_the_scrubber_follows_whatever_env_var_the_config_names`，它自己带了一句 `"<elided>" in res.error`，
把 `error` 清空时它和跨切口那条一起倒：这一片三条用例其实各有正控制，名册里我只给一条记了功。

**顺手收掉 `#149` 里那两处跨文件的指示代词。** 手册能力清单里有两句写的是"见上一节"（`#80` 那具数
一句、体检工具排练一句），而手册从 484 行到文件尾只有一个标题——它们要退的那一节住在这一页，读者在
手册里退无可退。两处都改成带尖括号的指针形，于是它们从"没人核的一句话"变成闸门扫得到的一处落点。

**这一族还剩多少，按点名字样量。** 18:48:44Z 拿这一组字样扫 README 与 `docs/*.md`（`见上一节`、
`见下一节`、`上一节的`、`下一节的`、`上面那节`、`下面那节`、`上面那条`、`下面那条`、`见前文`、
`见上文`、`见下文`）：18 行，其中 README 4、归档 11、`metrics.md` 2、`comparison.md` 1。这 18 处落在
同一个命令块、同一张表或同一条 bullet 里，读者就地能解，所以这一片不动它们；闸门仍然没有——`#149`
开着，等的是"指示代词算不算落点"这一条判据，而那要先有失败测试。

**收尾读数（这一节的电池名册与那两处指针改写都落盘之后）。** 全量 1010 passed in 105.54s（18:50:53Z
起跑、18:52:41Z 收尾，这一串里最后一次；它前面还有一跑 1010 passed in 88.73s，18:46:47Z→18:48:17Z）；三道文档闸门 69 passed（18:50:03Z、18:50:12Z 两跑——这一节所有散文与手册那三处改动都在它们之前落盘，
只有这一句里的时间戳在后）；扫面 71 处指针、死指针 0 处（18:46:38Z：README 36、归档 32、`comparison.md` 3——README 比上一片记的 34 涨 2，
涨的就是这一片补的那两个落点）；能力清单 40 条 bullet 里 22 处具数主张全部有落点（18:46:38Z，与上一片
同口径）。手册 718 行——这一片对它的三处改动（配置那一段 +1 行、指针两处、末行读数一处）全是行内或
段内替换，只有 `#147` 那段散文多出一行。


