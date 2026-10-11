# 做市、并购事件与期权组合研究

本目录提供隔离研究环境、数据输入格式、三个 Studio 模板和可重复执行的验收。
这些功能只产生研究报告与模拟成交。合成验收通过不能证明市场历史覆盖、成交真实性或策略收益。

## 功能状态

| 范围 | 已实现的软件 | 数据边界 |
|---|---|---|
| 股票／ETF 做市 | 被动报价、库存偏移与上限、L2/BBO 撮合、撤单延迟、费用、精确账本、按时间留出选择成交模型参数 | 美元现金股票／ETF；库存非负；真实排队与延迟校准需要执行证据 |
| 并购数据库 | SQLite 追加修订、幂等导入、冲突回滚、可得时间过滤及行级哈希 | 固定现金／比例换股；公告和法律交割条款需有来源 |
| 并购概率 | Logistic 训练、Platt 校准、时间分割及标签净化、留出 Brier/log loss 与基准率对照、模型时点门禁 | 不把价格隐含概率或合成训练结果称为真实完成概率 |
| 并购账户 | 部分成交、借券证明、抵押资金、融资借券费用、终止退出、现金兑付、换股交付、收到股票后的卖出 | 交割使用明确可得时间、有效时间和成本分配；不自动解释 CVR、collar 或法律文件 |
| 期权组合 | 多数据包质量、风险分组、Greeks、FX 与压力情景；期权权利金和动态直接标的对冲共用账户 | 账户回放使用单币种现货标的；跨币种风险分析与账户分别处理 |
| 期权执行 | 后续同步 BBO、容量限制、部分退出披露、借券与保证金门禁、资金费用、NAV 和精确账本 | 保证金为明示的合约要求与保守抵押，不代表券商组合保证金算法 |
| Studio | inventory-market-making、merger-event-research、options-hedged-portfolio | 只读预检、完整报告、成交／生命周期／账本表和产物哈希校验 |

## 环境与一键验收

需要 Python 3.12 和相邻八个仓库：quant-data-kit、quant-execution、quant-lab、quant-factors、
quant-us-equity、quant-options、quant-workspace、quant-studio。
`requirements.lock` 固定外部依赖；仓库源码版本见本次交付的 `integration-ready.json`。
这个专用环境使用整组源码，不把各仓库旧发行锁视为包含新增功能。

```powershell
# 新环境路径必须不存在；自动安装固定依赖和本地源码包
python profiles/institutional-research/bootstrap.py --environment <新环境路径> --repos-root <仓库群根目录>

# 将 python 换成该环境解释器，自动生成样例、导入数据库、训练模型、运行三个 Studio 模板并验证报告
python profiles/institutional-research/run.py acceptance --output <新的验收输出目录>

# 同时覆盖成功、失败和边界行为的跨仓库回归
python -m pytest profiles/institutional-research/test_acceptance.py -q --basetemp=<已存在短父目录下的新目录>
```

Windows 测试临时父路径宜短；测试输出和数据目录不得提交到源码仓库。

## 数据与命令

`run.py examples --output <新目录>` 生成全部输入格式的合成示例，不会读取券商凭据。

| 工作流 | 命令参数 |
|---|---|
| market-making | --input market-making.json --output <新目录> |
| calibrate-fills | --input fills.json --as-of <带时区时点> --output <新 JSON> |
| import-mergers | --database events.sqlite --input merger-events.json |
| mergers | --database events.sqlite --bundle merger-prices --config mergers.json --output <新目录> |
| train-merger-probability | --input merger-training.json --as-of <带时区时点> --output <新模型 JSON> |
| merger-replay | --database events.sqlite --bundle merger-prices --config merger-replay.json --output <新目录> |
| options | --config options.json --output <新目录> |
| options-replay | --config options-replay.json --output <新目录> |

以上工作流置于 `python profiles/institutional-research/run.py` 后使用。
`--repos-root` 可覆盖相邻仓库根目录；相对期权数据路径以配置文件所在目录为准。

### 做市

`quant.market-making-input/1` 接受 QDK v2 quote、trade、book_snapshot、book_delta、status。
价格、数量、现金为 `{units, scale}` 定点数。必须声明 synthetic/retrospective 与 source_ref。
策略在当前事件撮合之后决策，撤单延迟向上取整到下一事件。BBO 无队列优先级，L2 队列是研究模型。
初始只有现金，买到库存后才报价卖出；期末库存按行情估值，不自动清仓。

`calibrate-fills` 输入包含 `evidence_kind`、`candidates` 参数字典、`records`。
每条执行证据有 order_id、submitted_at、outcome_available_at、quantity、filled_quantity、
predicted_fills（每个候选模型的预测成交量）、source。候选使用同一订单与窗口。
按订单时间分 70% 训练／30% 评估，并排除跨分割点才可得的训练结果；仅用训练误差选择模型。
输出留出误差和偏差，不会自动把未来完成的校准模型应用到过去。

### 并购

QDK 负责原始事件修订契约。每条修订有 available_at，终态不可重开，来源及行哈希必须一致。
研究价格使用美元 raw 股价；事件后拆股须提供重新换算后的条款。
完成／失败情景、期限、借券和成本假设均须在决策时已知。

训练记录是一笔交易一个样本：event_id、feature_at、available_at、label_available_at、
completed（0/1）、features、source。至少 30 个已解决样本；按时间 60/20/20 分割，
训练标签须早于校准期，校准标签须早于评估期；各组至少 5 个且含两种结果。
训练输出可放入某笔交易的 probability_model，配 probability_features 和
probability_features_available_at；不得同时设置 completion_probability。
模型训练完成时点必须不晚于预测决策时点。合成模型会传播 synthetic 证据标记。

账户使用 study、initial_cash、exit_at、timeline、可选 settlement。下一次成交行情必须严格晚于前次决策。
timeline 的 borrow 记录包含 available_at、expires_at、units、source；settles_at 明示出售所得何时可用。
股数跟随已可见的换股比例更新；终止后退出；完成后先回补借券，再按有来源的现金／股票条款交割。
部分成交和退出剩余头寸写入报告；不以未来报价补齐。现金改为换股而初始无收购方输入时会明确拒绝。

### 期权

风险报告保留各包来源、分析／回放权限、同步性、bid/ask、标的价、FX 与持仓单位。
只合并完全相同的 underlying/currency/underlying_kind；不把不同标的 delta 直接相加。
`portfolio` 做横截面风险；`portfolio-replay` 将真实观察的期权成交和现货对冲纳入一个精确账户。
目标持仓及退出时点预先声明，初始买卖后用上一观测的 Greeks 在后续 BBO 对冲。
期权和对冲报价须同步且标的观察一致；到最后交易时间前退出，期权到期行权沿用既有生命周期模块。
资金不足、借券到期、期权规则不可得、缺失报价和越限均拒绝；深度不足如实留下未平仓量。

## 仍需使用者提供的输入

| 输入 | 自动部分 | 需要人工或已有账户权限 |
|---|---|---|
| 真实盘口与成交 | 格式验证、撮合与候选参数选择 | 获授权的 L2／逐笔／订单执行记录及市场费率 |
| 并购事实与标签 | 修订导入、时点过滤、模型训练及留出评估 | 事件清单、来源核实、历史完成／失败结果与时点 |
| 法律交割与借券 | 条款校验、账户交付、费用与借券限额门禁 | 生效条款、成本分配、借券额度／期限和到账时间 |
| 完整期权历史 | QDK 数据包／CSV 接口、质量门禁、组合回放 | 数据许可／密钥、所需市场与历史深度、实际保证金与费用 |

没有真实输入时仍可运行合成验收，但不能标为真实市场验收或实盘可交易。

## 私有仓库持续集成授权

本机已用当前仓库访问权限完整执行安装包验收。GitHub 的跨仓库验收涉及私有的
quant-us-equity 等仓库；工作区当前没有跨仓库读取 secret。
要启用远端这项验收，由仓库所有者配置 `QUANT_RESEARCH_READ_TOKEN`，只授予锁文件中所需私有仓库的 Contents read。
未配置时 workflow 明确记录 BLOCKED 并跳过跨仓库运行，不报告其通过；各仓库自己的 CI 继续独立执行。
