# 独立环境的固定、检查与重建

`runtime-profile`、`doctor`、`bootstrap-env`用于日常开发组合，不修改历史发行清单、冻结研究账户或已有虚拟环境。每个应用保留自己的解释器与依赖锁，不把所有应用安装进同一环境。

先从已选定提交建立干净的独立 Git 检出，在 workspace YAML 中配置其路径。安装本仓后：

```text
quant-workspace --config workspace.yaml runtime-profile --projects quant-fund a-share-multifactor --environment .venv --out runtime.json
quant-workspace --config workspace.yaml doctor --profile runtime.json
quant-workspace --config workspace.yaml bootstrap-env --profile runtime.json --project quant-fund
quant-workspace --config workspace.yaml bootstrap-env --profile runtime.json --project quant-fund --execute
```

profile要求显式选择项目，记录精确 Git 提交、依赖锁原始字节 SHA-256、项目发行名、Python 范围和项目内的新环境目录。默认 Python 范围为 `>=3.12,<3.13`，可用 `--python-spec`明确修改。文件只创建一次，不覆盖。profile本身应保留在源码目录外或受忽略的操作目录中，避免令被检查检出变脏。

`doctor`只读检查：独立检出、提交、干净状态、锁摘要与固定依赖语法；然后由当前可信的quant-workspace进程直接解析目标环境的`pyvenv.cfg`和`*.dist-info`。它不启动目标解释器、不导入目标包，也不会处理`.pth`。检查包括目标解释器文件存在、环境home/可用时的基础解释器路径、Python版本、禁用system-site-packages、每个适用锁项的版本或Git源完整结构与提交、主项目editable来源、已安装包的默认依赖兼容性，以及不允许的锁外发行包。仅允许venv引导产生且无直接来源声明的`pip`作为锁外bootstrap工具；报告会列出其版本并明确`locked: false`与`content_authenticated: false`。前后重复核对源码，失败不执行策略或尝试修复。没有安装环境会明确返回 `environment_missing`。输出 `quant.runtime-readiness/v1` JSON；全部就绪退出0，其余退出2。

锁仅支持精确 `==`版本（可有extras、环境标记、pip-compile哈希行）和HTTPS Git源的40位提交。唯一允许的pip选项是仓内规范相对目录形式的`--find-links`；目录必须留在仓库内且只能包含当前Git提交跟踪的固定wheel，未跟踪、忽略、非wheel或逃逸路径都会阻断。锁中请求的extras会按发行包依赖递推到稳定点后再检查缺失与版本。不递归读取其他requirements文件，不接受其它pip选项、浮动tag、范围依赖、任意URL wheel或editable路径锁项；必须先用项目规定的流程生成固定锁。跨平台复用同一profile时应保持锁文件原始字节相同，建议源码检出显式设置`core.autocrlf=false`；换行不同也会报告摘要不符，不会悄悄改文件。

`bootstrap-env`默认只返回参数数组计划；`--execute`才创建新目录、用当前匹配Python建立venv、按锁安装、无依赖/无构建隔离安装项目并运行pip check，最后重新执行doctor。venv创建命令成功后，bootstrap把可信创建器当时的完整Python版本、实现名称及实现版本写入新环境`pyvenv.cfg`的`quant-workspace-*`字段，并标记来源`trusted-bootstrap-creator`；doctor只接受完整且与标准版本字段一致的记录，不据当前进程或路径为既有环境补猜实现。新环境路径必须被Git忽略；任何已有目录均拒绝复用，防止修改冻结环境。每个已开始或在启动前被源变化阻断的步骤都写入`quant.runtime-bootstrap-step/v1`结构化日志；正常失败、元数据记录失败、超时和收到的`KeyboardInterrupt`会保留状态及可得输出。操作系统强制终止进程时无法保证补写日志。失败目录保留供诊断，不自动删除或重试。再次尝试需选择新的受忽略环境目录并生成新profile。源码必须先匹配profile，安装各步骤和最终验收也会重查源及profile；命令不使用shell字符串。只针对可信的本地检出和锁使用此安装工具。

profile和doctor不自动获取源码、不运行数据采集、测试或研究。元数据路径和版本核对不认证目标解释器二进制、`pyvenv.cfg`、site-packages字节或供应链真实性，也不证明跨应用业务兼容或市场数据质量；对应claims始终为false。`pyvenv.cfg`的`executable`表示创建环境时所用的解释器，报告字段为`creation_executable`，不把它误作home中的基础解释器；`metadata_source`说明实现元数据来自普通`pyvenv.cfg`还是本工具受控的新环境创建步骤。uv环境的`version_info`可能只提供major.minor，且可能不提供创建解释器，此时报告保留`creation_executable: null`，不会把缺失patch静默当成`.0`：只要profile Python范围、`Requires-Python`或`python_full_version` marker依赖patch才能判定，就以`metadata_insufficient:*`阻断；应通过本工具`bootstrap-env`按profile新建并记录创建器元数据的环境，而不是手工修改旧环境或猜测patch。`ready`仅表示列明的元数据检查就绪，不代表目标二进制实际版本已认证；正式交付仍须逐应用测试与固定组合集成验收。报告可能含本地路径或诊断信息，应本地保存，不能上传密钥或私有运行内容。
