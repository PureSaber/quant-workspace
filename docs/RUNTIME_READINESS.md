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

`doctor`只读检查：独立检出、提交、干净状态、锁摘要与固定依赖语法；然后使用所选解释器的隔离模式读取包元数据，检查Python版本、虚拟环境身份、每个适用锁项的版本或Git源与提交、主项目editable来源，以及已安装包的默认依赖兼容性。前后重复核对源码，失败不执行策略或尝试修复。没有安装环境会明确返回 `environment_missing`。输出 `quant.runtime-readiness/v1` JSON；全部就绪退出0，其余退出2。

锁仅支持精确 `==`版本（可有环境标记、pip-compile哈希行）和HTTPS Git源的40位提交。不递归读取其他requirements文件，不接受浮动tag、范围依赖、任意URL wheel或editable路径锁项；必须先用项目规定的流程生成固定锁。跨平台复用同一profile时应保持锁文件原始字节相同，建议源码检出显式设置`core.autocrlf=false`；换行不同也会报告摘要不符，不会悄悄改文件。

`bootstrap-env`默认只返回参数数组计划；`--execute`才创建新目录、用当前匹配Python建立venv、按锁安装、无依赖/无构建隔离安装项目并运行pip check，最后重新执行doctor。新环境路径必须被Git忽略；任何已有目录均拒绝复用，防止修改冻结环境。每步日志留在新环境中；失败目录保留供诊断，不自动删除或重试。再次尝试需选择新的受忽略环境目录并生成新profile。源码必须先匹配profile，安装各步骤和最终验收也会重查源及profile；命令不使用shell字符串。只针对可信的本地检出和锁使用此安装工具。

profile和doctor不自动获取源码、不运行数据采集、测试或研究。包版本、Git来源及兼容性匹配不认证site-packages字节、供应链真实性、跨应用业务兼容或市场数据；这些声明始终为false。`ready`仅表示列明的环境检查通过，正式交付仍须逐应用测试与固定组合集成验收。报告可能含本地路径或诊断信息，应本地保存，不能上传密钥或私有运行内容。
