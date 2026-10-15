# Windows 本机交付与恢复

本工具没有调度循环。它复用 `prepare-release`、`accept-release`、
`activate-release` 和 transfer 包验证，适用于 Windows 本机 IPv4 回环服务。
保留原有唯一每日自动化及独立冻结解释器。Studio 关闭时照常使用 PowerShell 入口。
工具不接管未识别的现存服务，不杀占用端口的进程。首次接入先用原服务的停止方式关闭
原监护/服务，核实端口空闲，再由控制器启动；不要同时保留两个服务监护者。

## 配置与独立控制

将下面的示例保存到管理员控制的文件。路径均需换成本机绝对路径；不要把凭据写进此文件。
控制器自身的 Python 应安装在独立且稳定的工具目录。它把经过摘要核验的固定 stdlib 启动脚本
保存到自身状态目录，再交给目标解释器运行。旧服务版本无需安装新 Workspace 模块；
脚本在真正的服务进程中记录已导入的路径、版本和源码树摘要。

```json
{
  "schema_version": "quant.local-control/v1",
  "state_dir": "C:/Quant/operations/controller",
  "release_state_dir": "C:/Quant/operations/releases",
  "initial_runtime": "previous",
  "service": {
    "host": "127.0.0.1", "port": 8770,
    "runs_root": "C:/Quant/state/runs",
    "settings": "C:/Quant/config/studio-settings.json",
    "stop_file": "C:/Quant/operations/stop-studio.request"
  },
  "activity_roots": ["C:/Quant/daily-operations"],
  "protected_files": ["C:/Quant/config/daily.json", "C:/Quant/accounts/main/account.json"],
  "runtimes": {
    "previous": {
      "python": "C:/Quant/releases/previous/env/Scripts/python.exe",
      "cwd": "C:/Quant/releases/previous", "baseline": true
    },
    "candidate": {
      "python": "C:/Quant/releases/candidate/quant-studio/.venv/Scripts/python.exe",
      "cwd": "C:/Quant/releases/candidate",
      "candidate": "C:/Quant/operations/candidate.json",
      "evidence": "C:/Quant/operations/acceptance.json",
      "prepared_root": "C:/Quant/releases/candidate"
    }
  }
}
```

可选 service 字段为 `templates`、`access`、`cert`、`key`、`ca`。
旧版本不能识别新 settings 字段时，runtime 项可指定独立 `settings` 绝对路径，默认仍使用
service.settings。预览绑定全部已配置 settings 的字节，失败恢复选用旧版本的兼容文件。
两版的业务账户、账本和数据目录仍共享，不恢复任何旧财务状态。
service.daily_profile 可显式指定旧版所需的每日 JSON；控制器把它固定注入
QUANT_STUDIO_ETF_DAILY，并纳入预览哈希。启动时清除 shell 遗留的相关业务环境变量，
不依赖调用者恰好运行过旧启动脚本。新版仍可从自身 settings 读取配置。
TLS 必须同时提供证书、私钥及信任 CA；工具不会跳过证书核验。
`baseline` 仅供现有环境首次启动及失败恢复，主动 activate/rollback 均要求候选验收。
必须在 settings 中保留 `python_by_repo.quant-pipeline-frozen` 的原值，
并把每日配置、冻结账户定义等不可重绑文件加入 protected_files。
可选 `account_checks` 为 `[{"path":"C:/Quant/accounts/main","python":"C:/Quant/frozen/env/Scripts/python.exe","reader":"paper-account"}]`；
控制器使用指定的原生只读读取器检查未结束账户尝试。新账户目录可用 `paper-accounts`。
activity_roots 同时包含每日操作根和项目登记 state_root；`registering` 状态也阻止维护。

```powershell
$toolPython = 'C:/Quant/tools/env/Scripts/python.exe'
$controlConfig = 'C:/Quant/config/local-control.json'
& $toolPython -I -m quant_workspace.local_control --config $controlConfig status
$plan = & $toolPython -I -m quant_workspace.local_control --config $controlConfig activate --target candidate | ConvertFrom-Json
$plan | ConvertTo-Json -Depth 20
# 审阅该具体预览后：
& $toolPython -I -m quant_workspace.local_control --config $controlConfig activate --target candidate --execute --expected-plan-sha256 $plan.plan_sha256
```

`start`、`stop`、`restart` 使用已选环境；`activate`、`rollback` 必须显式选择配置名称。
更换配置/设置/候选/验收文件或运行记录后原预览失效。目标目录的源版本、依赖、验收输出、
状态契约转换均重验。升级安装仍使用已有 `prepare-release --execute --build-environments`，
目标必须不存在；失败安装保留日志但不会切换服务。

控制器在跨进程 `.local-admission.lock` 内创建 `.local-maintenance.json` 并检查活动任务；
Studio 的提交端必须在同一个文件锁内检查 marker、写任务并入队。标记整个切换期间保留。
运行/排队/取消中或未终结的每日操作阻止维护。控制器崩溃留下的标记/锁文件不可自动清除：
先核实回执、对应进程和账户任务，再由操作员处理。锁文件本身为空闲也可存在；持锁状态由 OS 决定。

所有服务/探测进程均使用隐藏窗口。服务启动成功需同时满足 HTTP 探测、Windows 监听端口归属、
启动器至监听进程父子链、创建时间身份及实际导入记录；Windows venv 启动器 PID 不必等于监听 PID。
回执中 `verified_at` 是历史核验时间，`status` 命令才是当前观测。
切换后再写发布指针；失败会尝试重新启动先前环境并记录恢复结果。失败恢复失败必须人工处理。
工具永远不还原账户/账本，不修改冻结绝对路径，不强制终止运行中的研究任务。

## 完整备份与隔离恢复

与旧工作台状态 ZIP 并存，完整备份必须显式包含 data/project/config/account/receipt 五类根。
每个根声明原生读取器：`inputs`、`history`、`instrument-master`、`projects`、
`paper-account`、`daily-receipts` 或 JSON 配置校验。账户读取器可指定旧冻结 `python`，
其余使用配置默认解释器。旧冻结环境不需要安装本工具。

每个根可选 `reader_path`，缺省为 `.`。归档仍覆盖整个 `path`，原生读取器仅检查
`path/reader_path`；恢复后读取相同的归档内子路径，不依赖原始来源在线。
`reader_path` 必须是使用 `/` 的规范相对路径；绝对路径、`..`、链接或 junction、
不存在的读取目标均拒绝。源路径和恢复副本在调用读取器前分别检查。
外部冻结 inputs 的覆盖范围仍按完整 data 根计算，不会缩小到读取子路径。
例如把 state 整体声明为 project 根并使用 `reader_path: "runs"`；把 data 整体声明为
data 根并使用 `reader_path: "etf/snapshots/实际冻结摘要"` 和 `reader: "inputs"`。
此时删去同一 state/runs、data 快照的重复根；transfer 继续拒绝重复来源。
根内其他文件仍归档，但不因此获得该原生读取器的语义校验。

```json
{
  "schema_version": "quant.operational-backup/v1",
  "python": "C:/Quant/tools/env/Scripts/python.exe",
  "source_repo": "C:/Quant/releases/accepted/quant-workspace",
  "state_dir": "C:/Quant/operations/backups/20261015",
  "archive": "C:/Quant/backups/20261015.zip",
  "restore_destination": "C:/Quant/restore-drills/20261015",
  "roots": {
    "inputs": {"path": "C:/Quant/data/inputs", "category": "data", "reader": "inputs"},
    "history": {"path": "C:/Quant/data/history", "category": "data", "reader": "history"},
    "projects": {"path": "C:/Quant/state/runs", "category": "project", "reader": "projects"},
    "config": {"path": "C:/Quant/config", "category": "config", "reader": "json"},
    "account": {"path": "C:/Quant/accounts/main", "category": "account", "reader": "paper-account",
                "python": "C:/Quant/frozen/env/Scripts/python.exe"},
    "receipts": {"path": "C:/Quant/daily-operations", "category": "receipt", "reader": "daily-receipts"}
  }
}
```

旧冻结账户单独声明。新项目账户根目录使用 `paper-accounts` 读取器，自动发现每次新增的完整账户，
逐个原生核验；`.pending-*`、`.failed-*` 文件原样保存，并在结果中明确列为未激活的部分证据。
非隐藏的账户子目录缺少注册文件会阻止备份，不能静默当作有效账户。
另外把登记 state_root 作为 receipt/JSON 根加入配置，保留预览和登记回执。
添加相应 external input 根以覆盖全部完整账户冻结引用。还应声明独立的 forward receipts、
采集输出、额外项目文件等已使用目录（JSON reader 会解析其中的 JSON，其余字节由 transfer 清单校验）。
source_repo 必须是干净的已提交版本，其 lock 和 Git SHA 写进归档。
输出/恢复目录必须与源根分离。私钥、敏感路径和凭据内容会被已有 transfer 检查拒绝；
管理员应为凭据保留独立的受保护恢复渠道。

```powershell
$backupConfig = 'C:/Quant/config/backup-20261015.json'
$backupPlan = & $toolPython -I -m quant_workspace.operational_backup --config $backupConfig preview | ConvertFrom-Json
$backupPlan | ConvertTo-Json -Depth 20
& $toolPython -I -m quant_workspace.operational_backup --config $backupConfig backup --expected-plan-sha256 $backupPlan.plan_sha256
& $toolPython -I -m quant_workspace.operational_backup --config $backupConfig verify
& $toolPython -I -m quant_workspace.operational_backup --config $backupConfig restore-drill
```

在空闲窗口或 Studio 停止后执行备份；完整备份不能把自身正在执行的 Studio 任务算成空闲。
文件集合及哈希前后重验，变化则没有成功备份回执。原生账户读取器检查注册定义、历史观察、
账本与回执，不运行策略。归档、配置须匹配成功备份回执，恢复目标必须不存在；损坏包和占用目标拒绝恢复。
恢复后使用原生读取器核实副本并再次核对字节，失败保留隔离诊断目录和失败回执。
冻结原始绝对绑定字节保持不变，`restart_ready` 永远为 false：隔离验证不代表新路径可直接投入生产。
较新的生产账本不会被旧包覆盖，恢复到生产原绑定是另一次明确的人工灾难恢复操作。

## Studio 连接

已有 delivery JSON 可选增加：

```json
{"local_control":"C:/Quant/config/local-control.json",
 "backups":{"daily":{"config":"C:/Quant/config/backup-20261015.json"}}}
```

在现有环境与交付页预览并排队执行 `control-status`、`verify-backup`、`restore-drill`。
停止/切换在独立控制器执行，避免交付任务关闭自身队列。两处运维页面展示当前 Studio PID/导入位置、
冻结解释器探测、历史发布指针、控制回执、备份及隔离恢复状态。历史回执明确标记为历史证据。

## 现有 ETF 单环境安装的升级与回退

ETF profile 已有 `bootstrap.py` 使用 `<runtime>/env` 普通安装十一仓库，并写入
`installed-stack.json`、`profile/stack.json` 和锁文件。继续使用该工具安装全新版本目录：

```powershell
& $toolPython -I 'C:/Quant/reviewed-profile/bootstrap.py' --root 'C:/Quant/releases/next' --source-root 'C:/Quant/pinned-sources'
& 'C:/Quant/releases/next/env/Scripts/python.exe' -I 'C:/Quant/releases/next/profile/bootstrap.py' --root 'C:/Quant/releases/next' --verify-installed
```

最终 profile 的仓库 SHA 与锁文件由集成发布负责人固定。本适配器不修改它们。
控制器的 ETF runtime 项使用以下字段，替代通用 candidate/evidence/prepared_root：

```json
{"python":"C:/Quant/releases/next/env/Scripts/python.exe",
 "cwd":"C:/Quant/releases/next", "etf_root":"C:/Quant/releases/next",
 "acceptance":"C:/Quant/operations/next-acceptance.json"}
```

集成负责人完成真实本地兼容性、研究、状态和冻结环境验收后，生成以下回执。
`files` 哈希必须对应新目录中的实际字节，每项 checks 指向实际成功测试输出且 returncode 为 0。
工具会校验输出哈希，不会替负责人伪造成功结果。

```json
{
  "schema_version":"quant.etf-runtime-acceptance/v1", "status":"accepted",
  "runtime_root":"C:/Quant/releases/next",
  "state_contract_sha256":"<64位状态契约SHA256>",
  "state_transitions":[],
  "files":{
    "installed-stack.json":"<SHA256>",
    "profile/stack.json":"<SHA256>",
    "profile/requirements.lock":"<SHA256>",
    "profile/bootstrap.py":"<SHA256>"
  },
  "checks":{
    "compatibility":{"returncode":0,"evidence":"C:/Quant/evidence/compatibility.txt","sha256":"<SHA256>"},
    "research":{"returncode":0,"evidence":"C:/Quant/evidence/research.txt","sha256":"<SHA256>"},
    "state_compatibility":{"returncode":0,"evidence":"C:/Quant/evidence/state.txt","sha256":"<SHA256>"},
    "frozen_runtime":{"returncode":0,"evidence":"C:/Quant/evidence/frozen.txt","sha256":"<SHA256>"}
  }
}
```

ETF 适配器每次切换前再次调用目标自身的 `profile/bootstrap.py --verify-installed`，
核实全部十一包的实际路径、VCS commit、源码树和安装回执。启动后仍验证真实监听与导入身份。
如果应用状态契约发生变化，state_transitions 必须含 `[旧SHA256,新SHA256]` 且有已通过的状态验收。
回退环境也应有同样完整验收回执，不能把新旧版本全部设置成 baseline 绕过检查。
ETF 指针为 release_state_dir 下的 `etf-current.json`，与通用交付 `current.json` 分别展示。
在服务健康后才发布指针；若随后的运行选择文件写入失败，控制器以 CAS 恢复原指针。
若无法确认或恢复指针，失败回执明确记录 pointer_recovery 和 observed_pointer_after，
不会把“旧进程已启动”表述为整个发布事务恢复成功。
