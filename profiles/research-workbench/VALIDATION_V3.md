# 第三阶段验收：约束、PIT与风险执行

最终集成使用本目录stack.json中的精确提交。2026-09-26本地共享Python3.12环境的`pip check`和`run.py verify`通过；各执行仓GitHub最新提交CI通过。源码、数据和研究产物分别冻结，未来观察不可随新代码更新。

本轮解决联合约束不可行时继续输出权重、风险输入缺失被当零、L1换手单位不一致、信号证据与延迟执行不一致、风险模型未接入整手目标和真实账本等问题。真实运行又发现全现金目标被基准相对TE拦截，现保留warning并允许退出；非空组合、绝对暴露、数据和模型有效性仍严格检查。

最终ASM套件181passed，覆盖率85.70%，run_contract纯分支211/216=97.69%；Pipeline133passed，覆盖率80.90%；Workspace90passed。所有关联仓的具体套件、来源范围、12个PR及未完成事项见[Notes验收报告](https://github.com/PureSaber/quant-research-notes/blob/codex/risk-pit-workflow/validation/risk-pit-20260926/VALIDATION.md)。

真实研究`etf-risk-walkforward-20260926-r3`在固定四ETF池完成4个候选、3折、189个历史测试日。base净收益-3.1909%、最大回撤14.6810%、Sharpe-0.1873；买入持有对照+7.3811%。成本2倍与延迟1日候选均完整保留。未发现base优于对照的支持证据，不把修复后收益改善解释为策略有效。

500项派生产物哈希、注册数据库和代码身份已核验，公开压缩包解包后再次通过验证。原始首轮失败、r2结果及旧零观察账户完整保留；修复是在看到历史结果之后做的，r3仍属于开发期历史验证。结果、哈希与压缩包见[Notes真实结果](https://github.com/PureSaber/quant-research-notes/blob/codex/risk-pit-workflow/validation/risk-pit-20260926/RESULTS.md)。

活跃前向账户为`etf-risk-forward-20260928-r3`，已在2026-09-26 17:50:49北京时间提前注册，观察2026-09-28至2026-12-31、固定base。截至登记观察数0。真实时钟下提前观察9月28日已被拒绝。未来要先重新核验证据、延长真实已观察规则区间，再bind-master和update，不得改写历史前缀或伪造future数据。详见[前向操作手册](https://github.com/PureSaber/quant-research-notes/blob/codex/risk-pit-workflow/validation/risk-pit-20260926/FORWARD_RUNBOOK.md)。

当前不是完整动态PIT或完整Barra：实际数据缺逐日停复牌、盘中状态和历史动态全集，信号仍为当前复权vintage；实测风险模型仅market统计代理。真实风格描述子、行业约束加权回归、风险校准与ETF穿透仍需数据和实现。未来分红若修订历史复权价，旧前向账户必须停止，不能拼接旧值绕过验证。

代码已推送`codex/risk-pit-workflow`并创建PR，没有合并默认分支。期货价差和加密基差继续使用独立冻结样例。主数据与完整官方附件保留在本地，不包含在公开派生证据包中。
