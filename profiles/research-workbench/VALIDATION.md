# 六项研究工作台验收

验收日期：2026-09-26。入口、安装与完整命令见[README.md](README.md)，运行依赖的精确提交见[stack.json](stack.json)。这是研究工作台交付，不是实盘连接或策略盈利认证。

## 功能与验收证据

|开发方向|已交付行为|验证方法|
|---|---|---|
|1．策略配方与实验运行器|闭合YAML配方、预登记、SQLite实验历史、失败保留、完整性校验及重复运行复用|配方边界、并发锁、篡改拒绝与缓存测试；四类模板端到端运行|
|2．因子接入与筛选|17个注册因子、数据要求、覆盖率、1/5/20日IC和RankIC、分段与相关性|稀疏/恒定截面、未知因子、重复键、截止日之后数据扰动测试|
|3．可信数据预检|历史导入及原始文件保留、双时间PIT、覆盖和预热检查、缺口清单|时区、修订、篡改、未来披露、基本面缓存覆盖冲突测试|
|4．稳健性与失败诊断|买入持有、单因子、消融、成本压力、信号延迟、调仓频率及参数邻域|完整候选与账本哈希校验；静态报告、筛选交互和失败记录检查|
|5．AI研究助手|离线草案、结构化探索导入、显式在线模型入口、原文引用校验、相似研究检索|离线真实调用、结构化输入与受控模型响应测试；未进行在线模型调用|
|6．四类策略模板|A股截面选择、ETF趋势轮动、冻结期货价差、冻结crypto基差|真实A股8个、合成ETF7个、期货2个、crypto3个候选全部完成|

## 测试和运行环境

本机使用两个没有system-site-packages的Python3.12独立环境：工作台环境与期货/crypto冻结环境。两套正式安装脚本均执行成功，均通过pip check。工作台应用按stack.json中的干净Git提交安装；冻结环境使用自己的依赖锁，并由隔离子进程核验实际VCS提交。内部依赖锁使用Python3.10重建，条件依赖tomli保留。

|仓库|本地完整测试结果|
|---|---|
|quant-data-kit|499通过，1跳过|
|quant-lab|42通过|
|quant-factors|118通过|
|quant-execution|204通过|
|quant-risk-monitor|62通过|
|a-share-multifactor|152通过|
|quant-report-hub|108通过，4跳过|
|quant-agent|39通过|
|quant-pipeline|86通过|
|quant-workspace|89通过|
|quant-futures-spread，原仓未改|117通过|
|quant-crypto-basis，原仓未改|69通过|

涉及覆盖率门禁的仓库按各自配置执行；精确GitHub状态以对应PR的Checks为准。CI覆盖Python3.10/3.11/3.12，A股仓库另有Windows/Linux矩阵。跳过项不计入通过项。

合并前审查额外复现并修复三处问题，新增6项回归；详见[代码审查记录](REVIEW.md)。

## 端到端结果与边界

四份验收报告保存在集成目录下的studies/release/{ashare,etf,futures,crypto}/research.html，并附study.json、experiments.db和attempts中的账本及因子证据。20个候选全部完成，没有从登记结果中删除失败候选。开发期间失败尝试在早期输出目录中保留，最终验收使用独立目录。

|模板|完成/失败|数据与解释边界|
|---|---|---|
|A股|8/0|2026-07-27至2026-09-18，40日四股票真实固定观察池；回顾性探索|
|ETF|7/0|固定随机种子的合成行情，用于安装和执行链验证|
|期货|2/0|原仓冻结fixture-only；手数对照，不代表真实市场验证|
|crypto|3/0|原仓冻结fixture-only；基差阈值及被动成交对照，不代表真实市场验证|

A股主实验净收益为0.4439691%，买入持有为1.189%，相差约-0.745个百分点。主实验成交12笔、费用81.0309元；2倍费用实验净收益约0.2539432%。这些结果没有支持本样本中主实验优于买入持有的假设。比较使用相同投入上限，不意味着逐日实际持仓相同。报告保留该结论，不自动选择或晋级最高收益候选。

当前仍有以下明确边界：

- 没有新增数据供应商授权；真实ETF行情、全市场多年历史和完整历史财务仍需合法数据源。导入与预检能力已经交付。
- A股/ETF采用固定观察池、初始资金目标份额及日频撮合；停牌或动态成分历史会明确拒绝，未声称支持这些执行情形。
- ICIR与分段收益是描述证据；重叠标签、短样本及探索性研究不构成独立样本外验证。
- 离线助手是可追溯模板草案，相似研究采用词项检索；在线模型仍需自行配置服务，其质量未作实测承诺。
- 未连接经纪商、未发送真实订单。

## 关联PR与集成

这些PR构成同一次跨仓交付。集成入口固定所有依赖提交，因此合并前也能按README运行；如采用squash/rebase合并，应确认已固定的提交仍可获取，或统一更新相关依赖锁和stack.json后重新验收。

建议按依赖顺序评审和合并：

1. [quant-data-kit#19](https://github.com/PureSaber/quant-data-kit/pull/19)、[quant-lab#9](https://github.com/PureSaber/quant-lab/pull/9)、[quant-factors#9](https://github.com/PureSaber/quant-factors/pull/9)。
2. [quant-execution#14](https://github.com/PureSaber/quant-execution/pull/14)、[quant-risk-monitor#10](https://github.com/PureSaber/quant-risk-monitor/pull/10)、[a-share-multifactor#14](https://github.com/PureSaber/a-share-multifactor/pull/14)。
3. [quant-report-hub#16](https://github.com/PureSaber/quant-report-hub/pull/16)、[quant-agent#9](https://github.com/PureSaber/quant-agent/pull/9)。
4. [quant-pipeline#12](https://github.com/PureSaber/quant-pipeline/pull/12)、[quant-workspace#11](https://github.com/PureSaber/quant-workspace/pull/11)。

研究工作台不更新期货/crypto原仓及其认证依赖。不要将新工作台环境混入冻结fixture环境。
