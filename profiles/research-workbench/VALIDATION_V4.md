# 第四阶段验收：研究时点、账本语义与失败关闭

修复日期：2026-10-01；安装及集成复验日期：2026-10-02。集成使用本目录`stack.json`中的精确合并提交，并安装到独立review目录；旧冻结环境、历史研究产物和前向账户均未修改。

## 本轮固定范围

|仓库|固定提交|集成行为|
|---|---|---|
|quant-factors|`a26c758f2e6d9bc4e6169d97df8cbc97cdaa4f9d`|实际`available_at`按历史日级决策时点重建信号；完整交易日网格避免缺失会话压缩滚动窗口或信号延迟|
|quant-report-hub|`c2afcd3f831cd90e08abb680adaf801caa39d701`|归因恒等式、成本单位、空归因输入及本地报告读取边界|
|quant-portfolio|`3ade084bebd341c2faee652ee45555a7d6fbe9b3`|仓位缩放保留现金和完整NAV；拒绝非有限或越界输入|
|quant-crypto-basis|`a84d2fbc562fb3098b86e445b8ed02ac7fa7d858`|双腿订单未全部足量成交时不返回认证运行|
|quant-futures-spread|`3f1195acb5421f3c4e5b780a2c63bf567bf58ef4`|缺少触发或任一腿未全部成交时拒绝认证；修正首期回撤与货币成本分母|

`quant-regime`、`quant-timing`、港股、基金和Studio不属于当前research-workbench profile，本轮未为扩大表面覆盖而加入。期货与crypto仍通过独立fixture环境运行；更新源码pin不改变其fixture-only研究边界。

## 新目录验证

使用独立的`WORKSPACE_REVIEW`作为集成根目录。该目录于10月1日新建后安装中断，10月2日按原锁完成安装；安装时将`TEMP`、`TMP`和`PIP_CACHE_DIR`全部放在该目录内，未复用或覆盖既有冻结虚拟环境。

安装与验证命令：

```powershell
python WORKSPACE_REVIEW/quant-workspace/profiles/research-workbench/bootstrap.py --root WORKSPACE_REVIEW --env WORKSPACE_REVIEW/.venv-research
WORKSPACE_REVIEW/.venv-research/Scripts/python.exe -m pip check
WORKSPACE_REVIEW/.venv-research/Scripts/python.exe WORKSPACE_REVIEW/quant-workspace/profiles/research-workbench/run.py --root WORKSPACE_REVIEW verify
WORKSPACE_REVIEW/.venv-research/Scripts/python.exe WORKSPACE_REVIEW/quant-workspace/profiles/research-workbench/smoke.py --root WORKSPACE_REVIEW --output WORKSPACE_REVIEW/integration-independent --account-policy independent
WORKSPACE_REVIEW/.venv-research/Scripts/python.exe WORKSPACE_REVIEW/quant-workspace/profiles/research-workbench/smoke.py --root WORKSPACE_REVIEW --output WORKSPACE_REVIEW/integration-continuous --account-policy continuous
```

## 已核验结果

- 13项源码检出均与清单一致且工作树干净；研究环境安装126个包，`pip check`与`run.py verify`通过。
- 外部依赖锁和fixture锁保持原哈希。AKShare使用与正式锁SHA-256完全一致的随仓工件；其他依赖按原固定版本安装。本机下载采用已有缓存及进程级代理，没有修改系统代理或依赖版本。
- 独立fixture环境安装42个包，依赖检查和期货、Crypto的精确源码身份核验通过。
- 新研究环境中的Workspace测试100项通过；独立折smoke的4个候选全部成功，续跑结果和产物哈希不变。
- 对应修复profile的远端`Research integration`已在Windows/Linux分别通过独立折和连续账户验证。完整本地集成结果、最终发行提交与数据门禁见[应用修复发布验收](https://github.com/PureSaber/quant-research-notes/blob/main/validation/maintenance-20261001/APPLICATION_FIX_RELEASE.md)。

两项smoke只证明固定源码栈的合成软件集成、续跑不可变性及连续账户缓存防篡改，不构成策略收益、真实市场或实盘认证。
