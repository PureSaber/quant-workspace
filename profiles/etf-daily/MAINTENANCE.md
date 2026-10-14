# 本机统一维护入口

安装负责人保存 `quant.local-maintenance-installation/v1` JSON，所有路径使用绝对路径：

```json
{
  "schema_version": "quant.local-maintenance-installation/v1",
  "tool_python": "C:/Quant/releases/accepted/env/Scripts/python.exe",
  "control_config": "C:/Quant/config/control.json",
  "backup_template": "C:/Quant/config/backup-template.json",
  "backup_cycles": "C:/Quant/backups",
  "display_backup_config": "C:/Quant/config/backup-current.json"
}
```

`control_config` 和 `backup_template` 的格式见仓库 `docs/windows-local-control.md`。
工具解释器所在目录必须保留，服务关闭或切换至旧版后仍通过该解释器执行维护。
备份路径宜短，避免 Windows 文件路径长度限制。

```powershell
$entry = 'C:/Quant/releases/accepted/profile/maintain.ps1'
$installation = 'C:/Quant/config/installation.json'
& $entry -Installation $installation -Action status
$preview = & $entry -Installation $installation -Action restart | ConvertFrom-Json
$preview | ConvertTo-Json -Depth 30
# 阅读预览并核对目标后执行同一计划；变化后必须重新预览。
& $entry -Installation $installation -Action restart -Execute -ExpectedPlanSha256 $preview.plan_sha256

$backup = & $entry -Installation $installation -Action backup | ConvertFrom-Json
$backup | ConvertTo-Json -Depth 30
& $entry -Installation $installation -Action backup -Cycle $backup.maintenance_cycle -Execute -ExpectedPlanSha256 $backup.plan_sha256
& $entry -Installation $installation -Action verify-backup -Cycle $backup.maintenance_cycle
& $entry -Installation $installation -Action restore-drill -Cycle $backup.maintenance_cycle
& $entry -Installation $installation -Action restore-drill -Cycle $backup.maintenance_cycle -Execute
```

首次备份预览创建独立批次并把该批次连接至网页展示。重复预览可指定原 `Cycle`；执行必须携带原批次和摘要。新批次保留旧归档、回执和恢复目录，不覆盖它们。恢复演练目标必须尚不存在，演练成功不表示已切换生产目录。

`start`、`stop`、`restart` 操作当前运行环境；`activate`、`rollback` 还需 `-Target` 指定配置中已验收的环境。所有变更默认仅预览，执行时必须提供摘要。账户、账本及调度不随环境回退。
