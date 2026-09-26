# 第二阶段验收记录

验收环境：Windows、Python3.12.5；所有应用使用独立集成目录及精确Git提交。公共依赖和应用清单见requirements.lock及stack.json。运行产物放在源码仓外。

## 功能范围

|方向|交付能力|证据与边界|
|---|---|---|
|1．真实数据|四ETF行情、原始/复权/公司行动一致性、不可变快照、重叠增量修订|真实历史证券主表仍缺PIT证据，历史策略回放保持阻断|
|2．自动验证|训练选择、embargo、无重叠样本外、逐折报告、配对区块bootstrap与BH调整|历史探索统计；每折从现金开始，不等同于未来留出|
|3．操作台|保存/复制/编辑、预检、队列/恢复、比较、笔记、日志及报告|仅本机；输入根目录限定，配方冻结及摘要复核|
|4．因子筛选|受限表达式、覆盖/IC/相关性、基准残差增量和中性化|拒绝未来位移和代码执行；静态增量是描述性证据|
|5．组合|equal/inverse_vol/cost_aware、当前NAV、完整组合换手限制|落选资产清仓计换手；硬约束不可行明确失败|
|6．状态回放|动态池、上市/退市、停牌、涨跌停、逐session重试|缺历史不默认可交易；退市持仓缺处置证据阻断|
|7．前向账户|冻结策略/代码/数据前缀、连续账本、幂等恢复、到期封存|软件验收使用合成数据及受控时钟，未声称真实未来观察完成|
|8．研究助手|成功/失败研究检索、引用与哈希复验、最小对照和缺口|不会自动运行建议；本轮未真实调用在线LLM|

## 真实数据证据

数据目录为集成根下`artifacts/qdk-etf-research-v3`。当前快照：

`sha256-fb230c347587fa5d61b715a4ed86a33e9540e2c88d5278817f4d865c03d11130`

父快照：

`sha256-7aace1f720375c6aebbccf3a4dd11c0abda07cba8060636d93ab1be57a1990c8`

- 510300、510500、159915、588000；2025-01-02至2026-09-24，共421个交易日。
- raw/adjusted各1684行，benchmark421行，5笔现金分红，catalog4条，captured-current历史18行。
- 交易日缺口0；五日重叠更新的raw/adjusted/benchmark/actions修订数均0；复权现金等式最大标准化误差小于7.5e-14。
- 来源为显式AKShare/Sina价格与独立累计分红，Eastmoney基金分红表及公告；原始文件、来源版本、端点及父子快照哈希均可检查。
- manifest内05:00/05:10是本次调用传入的**受控注册时点**，不能表述为程序独立采集的实际网络响应墙钟。
- catalog.available_at在2026-09-26，listing_date缺少来源，未倒填。`artifacts/real-etf-preflight-v2.json`实测四个标的均报`INSTRUMENT_MASTER_PIT_COVERAGE`，没有用零成交掩盖数据不足。

第1项的工程和数据结构验收可自动完成；真实历史研究需补充当时可得的主表及交易状态来源。人工点击验收不能替代这一证据缺口。

## 审查修复

独立只读验证实际复现并推动修复：

- 换手约束必须覆盖真实当前组合及落选持仓，零仓不能伪造等权当前仓位。
- 分红付款必须匹配应收声明、每股金额和总额；零持仓生命周期合法，同事件拆股不改变已经确认的应收。
- 前向账户首次观察前的历史也冻结；同源未来追加可用，原前缀或来源变更拒绝。
- 账户注册、观察和封存以数据库记录为事实源，派生JSON/HTML写入中断可恢复同一份结果；已封存结果不能重新选优。
- Web执行使用数据库冻结请求及子进程摘要校验；跨研究比较不静默剔除失败；输出路径拒绝符号链接、junction及越界。

## 本地自动验证

|仓库|结果|
|---|---|
|quant-data-kit|505通过、1跳过；全部核心分支覆盖门槛通过|
|quant-lab|62通过；覆盖率82.94%|
|quant-factors|125通过；覆盖率91.90%|
|quant-agent|47通过|
|quant-report-hub|108通过、4跳过；覆盖率87.52%|
|quant-portfolio|59通过、1跳过；覆盖率88.08%|
|quant-risk-monitor|62通过；覆盖率90.41%|
|quant-workspace|89通过|

核心执行、集成端到端及最终CI结果在最终交付时补记。

## 关联PR

- [quant-data-kit#20](https://github.com/PureSaber/quant-data-kit/pull/20)
- [quant-lab#10](https://github.com/PureSaber/quant-lab/pull/10)
- [quant-factors#10](https://github.com/PureSaber/quant-factors/pull/10)
- [quant-execution#15](https://github.com/PureSaber/quant-execution/pull/15)
- [quant-portfolio#11](https://github.com/PureSaber/quant-portfolio/pull/11)
- [quant-risk-monitor#11](https://github.com/PureSaber/quant-risk-monitor/pull/11)
- [quant-agent#10](https://github.com/PureSaber/quant-agent/pull/10)
- [quant-report-hub#17](https://github.com/PureSaber/quant-report-hub/pull/17)

合并依赖顺序：QDK→QExec/QF/QLab→QP/QRisk/QA/QHub→ASM→Pipeline→Workspace。本轮PR尚未合并。
