# 平台能力、版本与验收状态

`platform-status`把已有能力清单、源码盘点、运行环境doctor、发布候选和验收记录汇成同一份只读结果。JSON和HTML使用同一份观测，不启动研究、服务或安装器。

```powershell
quant-workspace platform-status --source-root D:/Quant --format html --out status-001.html
quant-workspace platform-status --source-root D:/prepared --profile runtime.json --candidate candidate.json --evidence accepted.json --prepared-root D:/prepared --state-dir D:/release-state --out status-001.json
```

输出文件必须不存在。退出码0表示本次请求的观测没有发现冲突，不表示所有能力已经验收或服务正在运行；退出码2表示缺失、阻断、身份不匹配或输入错误。没有传入的证据保持`not_checked`、`not_run`或`not_observed`。

## 每一层独立说明

| 字段 | 能证明的范围 |
| --- | --- |
| declaration_status | 目录声明此功能；不等于代码已运行 |
| source | 独立Git检出、当前提交、脏状态和证据文件存在性 |
| installation | 现有doctor重新读取当前源码、锁和安装环境元数据 |
| acceptance | 重新核对指定候选、实际验收输出字节和已准备环境；列出该次验收case种类 |
| active_release | 经过哈希核验的发布指针；不是正在运行的进程 |
| running | 没有进程证据时固定为not_observed |

组合验收只对应候选中的精确提交组合。当前源码后来更新或存在未提交文件时，`source_matches_candidate=false`，不能继承旧验收。未加入候选的仓库标记`not_in_candidate`。只有明确执行过`compatibility`case且证据重新核验通过，才显示`candidate_integration_evidence_verified=true`。通用`integration_verified`还要求当前安装环境就绪且此次检查没有源码、环境、指针或工作流冲突；只有研究case不能替代跨仓接口验收。

验收验证复用`verify_acceptance`，不会重新执行候选命令。结果哈希是本地完整性机制，不是外部可信签名。源码/环境探测也不证明市场数据真实性。此命令不做服务部署、自动切换或业务数据库迁移。

## CI必须看指定作业和提交

可选`--workflow-snapshot github-jobs.json`消费完整GitHub API导出，结构为：

```json
{
  "expected_head_sha": "完整40位提交",
  "required_jobs": ["acceptance (ubuntu-24.04)", "acceptance (windows-latest)"],
  "run": {"id": 123, "run_attempt": 1, "head_sha": "完整40位提交", "status": "completed", "conclusion": "success"},
  "jobs": {"total_count": 2, "jobs": []}
}
```

上面仅展示字段形状，`jobs`必须包含该run attempt所有分页的完整真实job记录；不能使用空示例。所需job名称必须由调用方明确列出，匹配GitHub显示名称。任一缺失、skipped、尚未完成、失败、重复、不同run/attempt/head或不完整分页都不能判为通过。调用方导出的文件标记为`source_authenticated=false`；此工具不把自填JSON认证为GitHub事实。

私有仓库跨仓工作流缺少`QUANT_RESEARCH_READ_TOKEN`时，访问检查直接失败并说明验收未执行。不得用跳过实际验收换取绿色状态。凭据仍由仓库所有者按只读最小权限配置，不进入代码或产物。

## 能力目录和发行范围

开发清单现覆盖26仓，新增互换和策略适配器。历史M8的14仓固定发行成员与提交不变；新仓库不自动继承历史认证。目录列出已有期权市场扩展模块，避免把基础入口的限制当成整个仓库的限制。

每次发布继续使用既有`release-candidate → prepare-release → accept-release → activate-release`流程。平台状态用于核对这些步骤的证据和差异，服务切换仍是独立操作。跨仓升级应保存新候选和新验收，保留旧候选及研究产物。
