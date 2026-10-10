# 工作台发布升级与迁移复现

本工具把四件事分开记录：候选版本、全新目录准备、实际验收、当前版本指针。它不会重启Studio或其他服务，也不会因版本回退而回滚业务数据。当前用户没有授权真实部署时，可以安全完成候选清单、准备计划、隔离目录演练和迁移包验证，停在手工激活之前。

## 发布候选

候选清单要求现有`runtime-profile`中的每个仓库均为独立、干净检出，HEAD和锁文件字节与profile完全一致，且`doctor`对实际环境返回`ready`。清单记录完整40位commit、锁SHA-256、Python约束、环境目录、实际读取的readiness报告、状态契约和验收套件。若提供`--state-dir`，还会读取当前指针及receipt，记录实际部署候选和receipt摘要；没有受管指针时明确记为`unmanaged`。

状态契约例子：

```json
{
  "schema_version": "quant.application-state-contract/v1",
  "name": "quant-studio-state",
  "version": "3"
}
```

验收套件不接受`passed: true`一类自报结论。每个case指定某个profile项目的真实解释器、参数、工作目录和预期输出字节摘要；`accept-release`实际运行命令，再读取输出并核对SHA-256。`compatibility`用于接口/集成验收，`research`用于固定输入研究复现；状态契约变化还需要`state_compatibility`case，并精确写明转换方向。

```json
{
  "schema_version": "quant.release-acceptance-suite/v1",
  "source_revisions": {"quant-studio": "0123456789abcdef0123456789abcdef01234567"},
  "cases": [
    {
      "id": "synthetic-research",
      "kind": "research",
      "project": "quant-studio",
      "cwd": "quant-studio",
      "args": ["-I", "tests/fixtures/reproduce.py"],
      "outputs": [
        {"path": "quant-studio/.acceptance/result.json", "sha256": "64位小写摘要"}
      ]
    }
  ]
}
```

先生成候选，再查看全新目录计划：

```text
quant-workspace release-candidate --profile runtime.json --source-root D:/quant --acceptance-suite acceptance-suite.json --state-contract state-contract.json --state-dir D:/ops/release-state --out candidate.json
quant-workspace prepare-release --candidate candidate.json --source-root D:/quant --destination D:/staging/candidate-01
```

`prepare-release`默认不写目录。加`--execute`后，工具使用本地干净仓库执行`git clone --local --no-hardlinks --no-checkout`并检出清单commit，不修改来源仓库，也不需要网络获取源码。加`--build-environments`才会对每个项目显式调用现有`bootstrap-env`逻辑；依赖安装是否访问包源取决于锁中的依赖和本机pip配置，工具不会宣称该步骤必然离线。目标必须不存在，失败不会替换目标目录。

```text
quant-workspace prepare-release --candidate candidate.json --source-root D:/quant --destination D:/staging/candidate-01 --execute --build-environments
quant-workspace accept-release --candidate candidate.json --prepared-root D:/staging/candidate-01 --out acceptance.json
```

验收前后均重新执行源码、锁和实际环境检查。输出哈希正确只证明指定case的固定产物一致；它不会自动证明真实市场数据质量、独立前向表现或所有研究均可复现。

## 激活与回退

激活和回退使用同一套安全约束：候选、验收输出和当前准备目录必须仍可复验；状态目录以独占文件锁串行操作；调用方必须提供预期当前候选SHA-256，CAS不一致时当前文件保持原样；receipt先以新文件写入，`current.json`最后原子替换。

```text
quant-workspace activate-release --candidate candidate.json --evidence acceptance.json --prepared-root D:/staging/candidate-01 --state-dir D:/ops/release-state --expected-current none
quant-workspace rollback-release --candidate older-candidate.json --evidence older-acceptance.json --prepared-root D:/staging/older --state-dir D:/ops/release-state --expected-current 当前候选SHA256
```

指针切换不会修改runs、modules、settings、service或任何外部业务数据，也不会自动重启服务。receipt会返回手工应用目标配置并重启的说明。如果当前与目标状态契约摘要不同，目标候选必须包含且通过从当前摘要到目标摘要的`state_compatibility`case；只有反方向证据或没有实跑证据都会阻断。版本回退因此不等于数据回退。

## 迁移包

Studio已有backup覆盖runs、modules、settings和service，但不代表外部数据、源码提交、依赖锁、Python环境或凭据已经归档。`transfer-package`是补充的显式allowlist迁移包：每个普通文件逐项声明来源、目标和类别`run/data/config/runtime/source`，并强制把固定锁作为`source`条目纳入；源码仓库必须匹配完整commit、干净状态和固定锁语法。

```json
{
  "schema_version": "quant.transfer-spec/v1",
  "source": {
    "repo": "quant-studio",
    "revision": "0123456789abcdef0123456789abcdef01234567",
    "lock": "requirements.lock",
    "lock_sha256": "64位小写摘要"
  },
  "files": [
    {"category": "source", "source": "quant-studio/requirements.lock", "target": "quant-studio/requirements.lock"},
    {"category": "config", "source": "quant-studio/settings.json", "target": "config/settings.json"},
    {"category": "run", "source": "quant-studio/runs/example.json", "target": "runs/example.json"}
  ],
  "external_data": [
    {"id": "licensed-market-data", "description": "需在目标机单独配置的授权行情"}
  ],
  "credentials_required": ["MARKET_DATA_TOKEN"],
  "path_mappings": {"studio_settings": "config/settings.json"}
}
```

```text
quant-workspace transfer-package --spec transfer-spec.json --source-root D:/quant --out migration.zip
quant-workspace verify-transfer --archive migration.zip
quant-workspace restore-transfer --archive migration.zip --destination D:/restore/drill-01
```

打包前后都会复查Git、锁和每个文件的身份/摘要。工具拒绝来源或目标越界、重复目标、任一层符号链接、非普通文件、打包期间变化、明显私钥/凭据文件名和常见明文凭据赋值。ZIP恢复拒绝重复成员、额外成员、路径穿越、链接、摘要或大小不符，并且只允许不存在的目标目录；全部payload在临时同级目录核验后才整体改名。

恢复不会改写历史配置，而是另建`migration-paths.generated.json`。`MIGRATION_STATUS.json`分别记录payload完整性、配置状态、缺失外部数据、缺失凭据、runtime重建状态和研究复现状态。凭据只记录标识符，绝不记录值；外部授权数据只记录缺口。即使`integrity_status=verified`，初始`research_reproduction_status`仍为`not_run`，不能把恢复哈希通过解释为研究已经复现。应在新目录按候选profile重建环境，再用`accept-release`或应用自己的固定输入验收实际运行。

敏感内容扫描用于阻止常见误打包，不是通用秘密检测器。allowlist必须由操作者逐项审查；不要把包含私有行情、个人数据或未知二进制配置的目录整体转成文件列表。
