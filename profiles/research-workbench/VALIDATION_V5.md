# 第五阶段：主表时钟与严格输入检查

日期：2026-10-03。本次profile已在新建独立环境完成安装和集成验证，不更新r4或其他冻结账户。

本次固定3项已合并修复：

|仓库|提交|目的|
|---|---|---|
|a-share-multifactor|`5685259d367876d32350380e5ec3d4afce0de571`|用实际bar时钟核验主表有效期，修复同日收盘后到期的规则被整日误拒|
|quant-data-kit|`1e582d2044ea9c2d517fe7b081ad839da427d983`|拒绝缺失、非数值及非有限行情输入|
|quant-futures-spread|`4439a59370ed44169db6545b5df5a215ec436587`|严格验证旧研究入口的日期、数值及交易布尔值|

其他源码pin、Python依赖锁、fixture依赖锁和AKShare工件哈希不变。全栈维护目录已用相同主线源码完成r4冻结输入的开发回放：原125份历史产物哈希通过，60份业务数据表精确一致，189个测试日的收益和全部绩效未变。完整区间的9月30日由旧代码失败变为成功，详见[冻结r4审计](https://github.com/PureSaber/quant-research-notes/blob/main/validation/p0-p2-20261003/R4_APPLICABILITY.md)。

## 独立环境验收

使用新建的`WORKSPACE_V5`，独立克隆Workspace及13项源码；各应用按`stack.json`精确提交检出。Python3.12.13、126个包完成安装，正式依赖锁及fixture锁未改变。GitHub工件直连失败后，AKShare使用随仓同SHA-256工件安装，其他包使用既有pip缓存；临时安装清单只替换工件传输路径，正式锁未修改。`run.py verify`核对锁文件、实际版本、AKShare哈希、全部源码及干净工作树，连同`pip check`通过。

```powershell
WORKSPACE_V5/.venv-research/Scripts/python.exe WORKSPACE_V5/quant-workspace/profiles/research-workbench/run.py --root WORKSPACE_V5 verify
WORKSPACE_V5/.venv-research/Scripts/python.exe WORKSPACE_V5/quant-workspace/profiles/research-workbench/smoke.py --root WORKSPACE_V5 --output WORKSPACE_V5/integration-independent --account-policy independent
WORKSPACE_V5/.venv-research/Scripts/python.exe WORKSPACE_V5/quant-workspace/profiles/research-workbench/smoke.py --root WORKSPACE_V5 --output WORKSPACE_V5/integration-continuous --account-policy continuous
```

两种模式均完成4个候选，失败0；续跑保持结果及产物哈希不变。连续账户还通过所选路径缓存篡改拒绝检查。远端Windows/Linux两种账户模式、Python3.10—3.12单元测试和CodeQL全部通过，见[PR#23](https://github.com/PureSaber/quant-workspace/pull/23)。smoke使用合成行情，只证明软件集成。

## 冻结真实输入复验

- 原数据根的298份文件逐项复制并核对SHA-256，约61.6MB；保留原采集时间，复制不代表重新采集。
- 原base、buy_hold、cost_2x、delay_1四个候选和全部策略参数保持相同，只更改研究ID与输入所在目录。
- 新环境中4个候选全部成功；500份新产物哈希核验通过，240份业务Parquet与r4逐字段精确相同。
- 四候选的完整验证分折、逐日收益和绩效全部一致，初始cutoff处的输入前缀与r4登记相同。base仍为189个测试日、净收益−3.19088056%，没有因维护选择赢家。

本地结果为`etf-risk-walkforward-20261003-r5`开发研究及其`development-verification.json`，不含自然前向观察。本次pin更新不代表真实数据GA、策略有效性或新账户登记已完成；账户版本化须另行记录实际登记时刻、定义哈希和自动化交接。
