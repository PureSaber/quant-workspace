# quant-workspace

量化投研入口：[研究工作台](profiles/research-workbench/README.md)提供策略配方、因子筛选、历史数据预检、稳健性诊断、研究助手及四类策略模板；[验收记录](profiles/research-workbench/VALIDATION.md)列出测试与适用范围。

Central path resolver, immutable `StackManifest 1.0.0` producer, and strict `M7Certification 1.0.0` verifier for the PureSaber quant multi-repo stack. It eliminates hard-coded sibling paths and freezes the exact Git, package, dependency, schema, performance, and market-data evidence consumed by releases.

## Install

```bash
pip install -e ".[dev]"
```

## Usage

```bash
quant-workspace --config configs/default.workspace.yaml show
quant-workspace --config configs/default.workspace.yaml path a-share-multifactor outputs
quant-workspace --config configs/default.workspace.yaml lab-config --out ../quant-lab/configs/from-workspace.yaml
quant-workspace --config configs/default.workspace.yaml stack-manifest --mode audit --out stack-manifest.json
quant-workspace verify-stack stack-manifest.json
```

M7认证必须额外提供规范化、内容寻址且可本地复验的认证清单：

```bash
quant-workspace verify-m7-certification validation-logs/m7/m7-certification.json
```

该门禁要求数据标准化和完整撮合＋账本各有3次独立的1000万事件运行，逐次检查吞吐、16GiB峰值RSS和产物确定性；同时要求Binance/OKX双源Crypto L2连续30天真实市场认证及Python3.10/3.11/3.12 CI。国内L2只有fixture时清单最多为`rc-ready`，取得合法真实数据并通过同类证据后才可为`ga-ready`。

证据文件必须位于认证清单目录内、记录SHA-256、使用canonical JSON和闭合Schema。性能证据使用`puresaber.m7-benchmark-evidence@1.0.0`，必须把项目、源码commit、dirty状态、计时范围、三次指标及全部正确性断言与认证清单逐字段绑定。市场证据使用`puresaber.m7-market-data-evidence@1.0.0`，必须绑定来源commit、状态、供应商、能力、窗口、冻结stream集合、质量计数和归档恢复结果。哈希正确但内容为任意文本、Schema漂移或声明不一致时一律失败。

CI结果不信任认证清单中的自报字符串。验证器从严格的GitHub Actions运行URL提取run ID，并通过GitHub API核对仓库、固定M7工作流路径、run attempt、事件、精确`head_sha`、总体结论以及Python3.10/3.11/3.12三个成功job。每份性能和市场证据还必须由该精确run上传为名称绑定run attempt的独立GitHub Actions artifact；认证清单记录artifact ID及archive SHA-256，验证器在线核对artifact所属run、commit、名称、未过期状态和GitHub摘要，下载ZIP后只接受唯一的`evidence.json`且必须与本地证据逐字节一致。这样即使同时重写证据与清单并重新计算全部哈希，也不能借用无关的成功CI或其他attempt的产物。公开仓库的运行元数据可匿名核验；artifact下载通常需要`GH_TOKEN`或`GITHUB_TOKEN`。无法联网、artifact缺失/过期、工作流或运行不匹配、job不完整及摘要不一致时均fail closed。

Set `QUANT_WORKSPACE_ROOT=D:/projects` to override the `root` field in YAML.

Rebuild the cross-version lock with Python 3.10 so conditional dependencies required by the
oldest supported interpreter remain visible, then verify the lock as a closed dependency set:

```bash
python -m piptools compile --extra dev --build-deps-for editable \
  --allow-unsafe --strip-extras --resolver backtracking \
  --index-url https://pypi.org/simple --output-file requirements.lock pyproject.toml
python -m pip install --no-deps -r requirements.lock
python -m pip check
python -m pip install -e . --no-deps --no-build-isolation
python -m pip check
```

For the Cross-Asset & Multi-Frequency v2 release, use
`configs/v2.release.workspace.yaml`. It contains exactly 14 runtime repositories and resolves its
root relative to the checked-out sibling layout. Legacy or documentation-only projects remain in
the existing desktop/default configs but are excluded from the release manifest. The allowed schema
set is intentionally derived from the tagged repositories' declarations, so the manifest records
the complete canonical union without duplicating it in YAML.

```bash
quant-workspace --config configs/v2.release.workspace.yaml stack-manifest \
  --mode release --out ../validation-logs/m6/stack-manifest-v2.json
quant-workspace verify-stack ../validation-logs/m6/stack-manifest-v2.json
```

## Stack declarations

Each runnable repository declares release metadata in `pyproject.toml`. Lock files are repository-relative and must not escape the repository.
Static PEP 621 versions and setuptools dynamic versions backed by a literal module attribute are
read without importing or executing repository code; computed, missing, or path-escaping dynamic
versions fail closed as missing package metadata.

```toml
[tool.quant-workspace]
layer = "strategy"
schemas = [
  { id = "standard/v2", version = "2.0.0" },
]
lock-files = ["requirements.lock"]
```

Supported layers are `data`, `contract`, `execution`, `strategy`, `portfolio-risk`, `reporting`, and `orchestration`; a missing or unknown layer blocks release so dependency-direction checks cannot be bypassed. Internal dependencies are discovered from PEP 508 project dependencies, and malformed requirement strings are preserved as explicit audit errors instead of being ignored. Release mode accepts only a local, resolvable tag or a full 40-character commit and requires it to resolve to the dependency repository's recorded `HEAD`.

Workspace YAML may freeze the allowed schema set. If omitted, the union of repository declarations is recorded.

```yaml
allowed_schemas:
  - id: standard/v2
    version: 2.0.0
```

`audit` writes a deterministic manifest with exact warnings and always sets `release_ready=false`. `release` fails closed for dirty or untagged repositories, floating or mismatched internal references, missing targets, dependency cycles, missing layers, malformed PEP 508 requirements, missing schemas/locks, and path escape. The writer fsyncs a same-directory temporary file, publishes atomically, and never replaces an existing path. `verify-stack` accepts only the canonical JSON bytes emitted by the writer and rejects unknown top-level fields.

The v2 release config is a scope contract only: it never stores candidate commits or tags. Release
discovery reads those values from clean local repositories after their default-branch CI and
annotated tags have passed. Roll back the config by reverting its commit; never repair a manifest
by moving a historical tag or editing canonical JSON.

## Related

- [quant-pipeline](../quant-pipeline) — post-run orchestration
- [quant-lab](../quant-lab) — experiment index
- [quant-research-notes](../quant-research-notes) — architecture docs
