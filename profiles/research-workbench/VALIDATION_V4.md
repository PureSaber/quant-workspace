# 第四阶段验收：研究时点、账本语义与失败关闭

验收日期：2026-10-01。最终集成使用本目录`stack.json`中的精确合并提交，并安装到全新的独立目录；旧冻结环境、历史研究产物和前向账户均未修改。

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

使用尚不存在的`WORKSPACE_REVIEW`作为独立集成根目录。安装时将`TEMP`、`TMP`和`PIP_CACHE_DIR`全部放在该目录内，未复用或覆盖既有虚拟环境。

最终提交完成后执行：

```powershell
python WORKSPACE_REVIEW/quant-workspace/profiles/research-workbench/bootstrap.py --root WORKSPACE_REVIEW --env WORKSPACE_REVIEW/.venv-research
WORKSPACE_REVIEW/.venv-research/Scripts/python.exe -m pip check
WORKSPACE_REVIEW/.venv-research/Scripts/python.exe WORKSPACE_REVIEW/quant-workspace/profiles/research-workbench/run.py --root WORKSPACE_REVIEW verify
WORKSPACE_REVIEW/.venv-research/Scripts/python.exe WORKSPACE_REVIEW/quant-workspace/profiles/research-workbench/smoke.py --root WORKSPACE_REVIEW --output WORKSPACE_REVIEW/integration-independent --account-policy independent
WORKSPACE_REVIEW/.venv-research/Scripts/python.exe WORKSPACE_REVIEW/quant-workspace/profiles/research-workbench/smoke.py --root WORKSPACE_REVIEW --output WORKSPACE_REVIEW/integration-continuous --account-policy continuous
```

验证结果将在上述全新目录完成后记录。两项smoke只证明固定源码栈的合成软件集成、续跑不可变性及连续账户缓存防篡改，不构成策略收益、真实市场或实盘认证。
