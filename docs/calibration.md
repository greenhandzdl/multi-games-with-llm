# 端点体检结果（全工程唯一合法的延迟常数来源）
- 生成命令：`export WOLF_LLM_API_KEY=<key> && python scripts/calibrate.py`
- base_url：未记录（当前配置为 `http://100.87.65.60:13000/v1`，不是本次测量的出处）  model：未记录（当前配置为 `gemma-4-26b-a4b-nvfp4`，不是本次测量的出处）
- 密钥仅从环境变量读取，本文件不含其值。
- 机器可读的孪生件：`data/calibration.json` **今天读不出常数**——没有 constants 块（这份 sidecar 早于该字段，或那次体检在写出常数之前就断了）：重跑 scripts/calibrate.py。`wolf audit --calibration <该文件>` 打印的就是 loader 这一句，本页与它一起等下一次体检：补不出常数时，两边都不许被当成来源。
- 数据来源：采集时间未记入 sidecar（早于该字段），唯一可依据的是文件 mtime 2026-09-20T12:56:24Z；本报告离线重渲染自 `data/calibration.json`
> **本节尚不构成常数来源**：D_decode_tok_s、per_call_fixed_overhead_s 没有可用的值（未测得或不为正）。见第 0 节。补齐之前，引用这些位置的报告一律标『未标定』，不得回填计划里的估计值。
## 0. 体检完整性
- 延迟矩阵失败单元格：**8**；温度扫描失败档位：**4**
- 失败单元格保留在下表并注明原因。**未测得即为未知，不得用计划里的估计值冒充实测值。**
- **另有 7 处的值被 redact 抹去**：features.tokenize/tokenize、features.tokenize/v1/tokenize、features.apc_visibility.has_cached_tokens、features.usage_keys_seen、ratio.en_tokens_per_char、ratio.zh_tokens_per_char、ratio.unit_tokens_estimate。这些格子是防护逻辑遮掉了结论，**不是**端点没返回；要么把结论换成不含凭据键名的形式记录，要么在本节写明该结论不可得。
  - 上面那句 remedy 的后半已经做了：`CRED_KEYS` 里不再有裸 `token`（就是它当年把 `has_cached_tokens`、`usage_keys_seen` 一起遮掉的），被遮的格子也由 `elided_paths` 逐格报出来而不是静默消失。**下一次 M0 重跑预期能把这些格读回来**——这是从代码读出的预期，不是本次重跑实测（本报告是旧 sidecar 的离线重渲染）。
| 项 | 失败原因 |
|---|---|
| 520 / k=4 | <style>             p {               margin: 10px 0;               color: white |
| 520 / k=8 | {"error":{"message":"upstream error: do request failed (request id: 202609201256 |
| 520 / k=9 | {"error":{"message":"upstream error: do request failed (request id: 202609201256 |
| 800 / k=1 | {"error":{"message":"upstream error: do request failed (request id: 202609201256 |
| 800 / k=2 | {"error":{"message":"upstream error: do request failed (request id: 202609201256 |
| 800 / k=4 | {"error":{"message":"upstream error: do request failed (request id: 202609201256 |
| 800 / k=8 | {"error":{"message":"upstream error: do request failed (request id: 202609201256 |
| 800 / k=9 | {"error":{"message":"upstream error: do request failed (request id: 202609201256 |
| 0.3 / k=- | all calls failed |
| 0.7 / k=- | all calls failed |
| 0.9 / k=- | all calls failed |
| 1.1 / k=- | all calls failed |

## 1. API 支持面与确定性
```json
{
  "models_endpoint": {
    "status": 200,
    "body": {
      "data": [
        {
          "id": "gemma-4-26b-a4b-nvfp4",
          "object": "model",
          "created": 1626777600,
          "owned_by": "openai",
          "supported_endpoint_types": [
            "openai"
          ]
        }
      ],
      "object": "list",
      "success": true
    },
    "version_field_available": false,
    "model_fields": [
      "created",
      "id",
      "object",
      "owned_by",
      "supported_endpoint_types"
    ],
    "version_debt": "端点不提供模型版本/修订字段，因此'服务被人重启换了权重'只能靠 canary 探针间接发现，无法直接依据。这是口径债，不要假装有。"
  },
  "tokenize/tokenize": "<elided>",
  "tokenize/v1/tokenize": "<elided>",
  "apc_visibility": {
    "probe_ok": true,
    "has_cached_tokens": "<elided>",
    "cached_value": null
  },
  "usage_keys_seen": "<elided>",
  "n_gt_1": "rejected (400)",
  "response_format_json": "rejected (400)",
  "logprobs": "supported",
  "stop_honored": true,
  "greedy_seed42_distinct_of_3": 3,
  "determinism_verdict": "NON-deterministic (temp=0 + seed do not pin output)"
}
```
## 2. 流式判定
```json
{
  "supported": true,
  "chunks": 83,
  "ttft_s": 1.127,
  "median_interchunk_s": 0.017,
  "verdict": "缓冲式（全部 chunk 在末尾同时到达）"
}
```
## 3. 单发吞吐与固定开销
`per_call_fixed_overhead_s` 是并发拿不掉的那部分，通常决定一局的下限。
```json
{
  "decode_tps": null,
  "note": "未采集：这次运行没有做单发吞吐测量"
}
```
## 4. chars→tokens 回归
```json
{
  "en_tokens_per_char": "<elided>",
  "en_chars_per_sample": [
    3550,
    10650,
    21300,
    35500
  ],
  "zh_pt": 3221,
  "zh_tokens_per_char": "<elided>",
  "unit_tokens_estimate": "<elided>",
  "fallback_rule_under_test": "ASCII/4 + CJK/1.6"
}
```
## 5. 并发 × 前缀 延迟矩阵
`cold`/`warm` = 该前缀首次与该前缀命中缓存的单发耗时；`wall` = k 个并发请求的总墙钟。
| prefix_reps | pt | k | cold_s | warm_s | apc_speedup | wall_s | lat_p50_s | lat_max_s | agg_decode_tps | agg_prefill_tps | n_errors | err_sample |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 35 | 573 | 1 | 1.16 | 1.19 | 0.97 | 1.64 | 1.64 | 1.64 | 14.7 | 350.1 | 0 |  |
| 35 | 573 | 2 | 1.16 | 1.19 | 0.97 | 2.93 | 2.93 | 2.93 | 15.4 | 391.0 | 0 |  |
| 35 | 573 | 4 | 1.16 | 1.19 | 0.97 | 4.09 | 3.69 | 4.09 | 19.1 | 560.3 | 0 |  |
| 35 | 573 | 8 | 1.16 | 1.19 | 0.97 | 8.95 | 6.24 | 8.9 | 16.8 | 448.3 | 1 |  |
| 35 | 573 | 9 | 1.16 | 1.19 | 0.97 | 12.08 | 9.17 | 11.87 | 13.7 | 379.6 | 1 |  |
| 130 | 1999 | 1 | 1.19 | 1.29 | 0.92 | 1.78 | 1.78 | 1.78 | 13.5 | 1121.4 | 0 |  |
| 130 | 1999 | 2 | 1.19 | 1.29 | 0.92 | 2.78 | 2.78 | 2.78 | 14.4 | 1438.4 | 0 |  |
| 130 | 1999 | 4 | 1.19 | 1.29 | 0.92 | 4.32 | 4.29 | 4.32 | 22.5 | 1851.8 | 0 |  |
| 130 | 1999 | 8 | 1.19 | 1.29 | 0.92 | 9.42 | 6.62 | 9.42 | 15.3 | 1485.6 | 1 |  |
| 130 | 1999 | 9 | 1.19 | 1.29 | 0.92 | 11.34 | 7.31 | 11.33 | 14.0 | 1234.1 | 2 |  |
| 270 | 4098 | 1 | 1.61 | 1.2 | 1.35 | 1.71 | 1.71 | 1.71 | 15.2 | 2400.4 | 0 |  |
| 270 | 4098 | 2 | 1.61 | 1.2 | 1.35 | 4.48 | 4.48 | 4.48 | 10.3 | 1828.0 | 0 |  |
| 270 | 4098 | 4 | 1.61 | 1.2 | 1.35 | 4.47 | 3.98 | 4.47 | 24.1 | 3664.2 | 0 |  |
| 270 | 4098 | 8 | 1.61 | 1.2 | 1.35 | 15.39 | 8.51 | 15.38 | 18.8 | 1864.4 | 1 |  |
| 270 | 4098 | 9 | 1.61 | 1.2 | 1.35 | 16.17 | 10.42 | 16.17 | 17.4 | 2027.2 | 1 |  |
| 520 | 7850 | 1 | 2.96 | 1.24 | 2.39 | 5.94 | 5.94 | 5.94 | 13.5 | 1321.2 | 0 |  |
| 520 | 7850 | 2 | 2.96 | 1.24 | 2.39 | 5.87 | 5.87 | 5.87 | 8.5 | 2672.5 | 0 |  |
| 520 |  | 4 |  |  |  |  |  |  |  |  |  |  |
| 520 |  | 8 |  |  |  |  |  |  |  |  |  |  |
| 520 |  | 9 |  |  |  |  |  |  |  |  |  |  |
| 800 |  | 1 |  |  |  |  |  |  |  |  |  |  |
| 800 |  | 2 |  |  |  |  |  |  |  |  |  |  |
| 800 |  | 4 |  |  |  |  |  |  |  |  |  |  |
| 800 |  | 8 |  |  |  |  |  |  |  |  |  |  |
| 800 |  | 9 |  |  |  |  |  |  |  |  |  |  |
## 6. 温度 × 多样性扫描（R1 塌陷基线）
指标定义与 `metrics.py` 完全一致，因此这里的数字可直接与日后对局日志对比。
注意 `opening_distinct_rate` 高但 `seat_mention_rate` 低 = 措辞多样而行为被动，正是 M3 把 `passivity_rate` 定为主判据的原因。
| temperature | n_speech | collapse_round | opening_distinct_rate | seat_mention_rate | top_fragment | top_fragment_freq |
|---|---|---|---|---|---|---|
| 0.3 |  |  |  |  |  |  |
| 0.7 |  |  |  |  |  |  |
| 0.9 |  |  |  |  |  |  |
| 1.1 |  |  |  |  |  |  |
## 7. 拟合出的契约常数
```json
{
  "D_decode_tok_s": null,
  "per_call_fixed_overhead_s": null,
  "P_prefill_tok_s_best_observed": 3664.2,
  "concurrency_wall_gain": {
    "wall_gain_at_k2": 1.28,
    "wall_gain_at_k4": 1.65,
    "wall_gain_at_k8": 1.51,
    "wall_gain_at_k9": 1.41
  },
  "batching_verdict": "并发几乎不给吞吐（见 concurrency_wall_gain），所以墙钟预算按 Σ单发时间 估，优化方向是减少调用次数与 completion 长度，不是提高并发。",
  "model": "T_call ≈ fixed_overhead + pt/P + ct/D ；T_phase ≈ Σ T_call（并发上限≈1）",
  "source_rows_ok": 4,
  "rows_failed": 8
}
```
