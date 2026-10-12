# 研究结果的比较、回放与证据验收

本页汇总 2026-10-12 八项升级的实际入口。候选源码、测试结果及跨仓验收见
`quant-research-notes/validation/eight-upgrades-20261012/`。
按该交付清单检出完整提交，再使用各仓库的专用依赖文件安装到独立 Python 3.12
环境。不能把旧发行锁、不同开发分支或已安装的同名旧包视为本次组合。

| 项目 | 入口 | 主要产物与适用范围 |
|---|---|---|
| 1 经济可比性 | `quant_measurement`；Lab compare；ReportHub standard adapter | `quant.measurement/v1`，明确不兼容原因；币种、收益公式、资本、费用、时间网格和输入身份必须相同 |
| 2 独立金融基准 | `python -m quant_benchmarks` | 预先写入 Decimal 期望，调用真实 API；公司行动、期货换月、FOF 费用、FX 内部划转的残差和反例 |
| 3 预测到执行 | `strategy-adapters forecast-replay` | 冻结留出预测 → 固定仓位政策 → QExec 次期开盘成交、费用、结算、基准 |
| 4 连续资金 | `quant-portfolio continuous-study` | 共享主账及互斥子账、交收、划转、FX、初始/维持保证金与可恢复检查点 |
| 5 修订影响 | `RevisionRegistry`；`quant-portfolio revision-study` | 不可变依赖闭包；同一代码、控制与计划下旧新市场数据配对重算 |
| 6 模型诊断 | `quant-risk diagnose-model` | 七类分别报告的诊断、预定窗口和区间；缺失观测、低样本与结构变化状态 |
| 7 执行隔离 | `python -m quant_pipeline.isolation`；Studio Notebook 显式政策 | Linux Docker/cgroup v2/seccomp、只读输入、无外网、资源限制、输出哈希与清理证据 |
| 8 助手评测 | `quant-assistant-eval` | 答案分离的证据包、离线无标签基线、结构化语义评分与 JSON/HTML 报告 |

## 可比性与独立基准

先使用生产者实际生成的 measurement 和观测网格验证，再比较声明：

```python
from quant_measurement import compare_measurements, validate_observations

validate_observations(left["measurement"], left["observation_schedule"])
result = compare_measurements(
    [left["measurement"], right["measurement"]], statuses=["completed", "completed"]
)
```

forecast-replay 的网格字段是 `observation_schedule`，continuous-study 是
`observation_timestamps`。连续账户保留同一时间的事件顺序，同时将其净收益连乘为
该时间的一次观察。缺失声明或失败结果会阻止整个比较。普通比较不允许不同市场输入，
数据修订必须进入专用的配对分析。

```sh
quant-lab --db state/experiments.db compare --project PROJECT RUN_A RUN_B --json
python -m quant_benchmarks --sources sources.json --out NEW_BENCHMARK_DIRECTORY
```

基准的 `sources.json` 为 `{"quant-execution":{"path":"...","commit":"完整提交"},...}`，
需要 quant-data-kit、quant-execution、quant-fund、quant-report-hub 的干净检出。
执行前后都会核验源码；缺少仓库、身份不符或适配器不能执行不能算作通过。

## 预测回放、连续资金与修订

```sh
strategy-adapters freeze-forecast-policy --config policy.json --out NEW_POLICY_DIRECTORY
strategy-adapters forecast-replay --archive ML_ARCHIVE --policy NEW_POLICY_DIRECTORY --bundle QDK_BUNDLE --out NEW_FORECAST_DIRECTORY
quant-portfolio freeze-capital-plan --plan plan-definition.json --out NEW_FROZEN_PLAN.json
quant-portfolio continuous-study --plan NEW_FROZEN_PLAN.json --market market.json --out NEW_STUDY.json
quant-portfolio revision-study --request paired-request.json --out NEW_REVISION.json
```

样例分别位于 quant-strategy-adapters 的 `examples/forecast_demo.py` 和
quant-portfolio 的 `examples/continuous/`、`scripts/run_continuous_demo.py`。
修订请求指向旧新 CAS 输入引用，旧记录不会改写。当前连续研究可对比 signals、orders、
history 和收益；没有执行过的 features、forecasts 阶段明确显示不可用。

预测入口仅支持单币种单期货多头/空仓、固定初始资本。它输出的 continuous-events
只映射实际模拟成交和已过账结算。连续账户采用显式比例保证金；与预测入口的固定每张
保证金不能直接声称等价。事件消费成功和期末现金一致并不证明资金约束一致。

## 诊断与助手评测

```sh
quant-risk diagnose-model --input diagnostics.json --out NEW_DIAGNOSTIC_DIRECTORY
quant-assistant-eval export --cases benchmarks/assistant-v1/public/cases.json --output NEW_PUBLIC.json
quant-assistant-eval baseline --cases NEW_PUBLIC.json --output NEW_SUBMISSION.json
quant-assistant-eval score --cases NEW_PUBLIC.json --labels benchmarks/assistant-v1/evaluator/labels.json --submission NEW_SUBMISSION.json --output NEW_ASSESSMENT_DIRECTORY
quant-assistant-eval self-test --suite benchmarks/assistant-v1 --output NEW_SELF_TEST_DIRECTORY
```

评测命令在 quant-agent 目录运行。评分覆盖固定结构的主张、证据引用、财务计算、失败遗漏、
冲突与拒答，不评定任意自然语言回答。公开回归标签不等于未见留出；离线基线通过不说明
任何在线模型的质量。诊断区间依赖独立有界块的假设，不接受假设或缺失观测时不作完整
窗口的置信结论，也不会自动交易、训练或恢复账户。

## Linux 隔离

```sh
python -m quant_pipeline.isolation --policy policy.json --check
python -m quant_pipeline.isolation --policy policy.json --inputs INPUT_DIRECTORY --output NEW_ISOLATION_DIRECTORY -- /usr/local/bin/python -I /inputs/research.py
```

政策须指向操作者预先安装并信任的不可变镜像 ID。实际 Linux CI 执行攻击边界测试，
本地 Windows 明确拒绝，不回退成普通进程。运行前检查、容器运行和输出收集失败分别留证；
只有已确认清理整个容器，才允许报告正常完成或取消。

以上验收使用合成输入检验软件行为。真实数据质量、前向收益、交易资格、部署状态和
私有跨仓 CI 凭据分别列在交付清单中，不从测试数量或源码存在推导。
