# M6 StackManifest本地验证记录

## 基线与环境

- 分支：`codex/cross-asset-v2-m6`
- 起点：`origin/main=0deb1a30afc8b77c4d8d4b92bb86e44b6acbfefb`
- 本机解释器：Python3.12.5
- 隔离环境：`.venv-m6`（验证完成后删除，不纳入提交）
- 锁文件：`requirements.lock`

## 实际命令与结果

```powershell
.\.venv-m6\Scripts\python.exe -m pip install -r requirements.lock
.\.venv-m6\Scripts\python.exe -m pip install -e . --no-deps --no-build-isolation
.\.venv-m6\Scripts\python.exe -m pip check
```

结果：安装成功，`No broken requirements found.`

```powershell
.\.venv-m6\Scripts\ruff.exe check src tests
.\.venv-m6\Scripts\ruff.exe format --check src tests
```

结果：Ruff检查通过，7个文件格式检查通过。

```powershell
.\.venv-m6\Scripts\python.exe -m pytest -q `
  --junitxml=validation\m6-pytest-junit.xml `
  --cov=quant_workspace --cov-branch `
  --cov-report=term-missing `
  --cov-report=json:validation\m6-coverage.json `
  --cov-fail-under=80
```

结果：`40 passed in 32.86s`，0skip、0failure；全仓分支覆盖率`91.46%`。

```powershell
.\.venv-m6\Scripts\coverage.exe report `
  --include='src/quant_workspace/stack_manifest.py' --fail-under=90
```

结果：核心`stack_manifest.py`分支覆盖率`94%`，门禁通过。

```powershell
.\.venv-m6\Scripts\python.exe -m compileall -q src
git diff --check
```

结果：编译通过；diff无空白错误，仅Git提示本机CRLF转换策略。

## 真实workspace audit烟测

使用固定`created_at=2026-08-29T00:00:00Z`和本机多仓根目录运行`stack-manifest --mode audit`，随后执行`verify-stack`：

- 创建退出码：0；验证退出码：0。
- 仓库记录：17；DAG节点：17。
- `manifest_hash=72599226b3efe5519b5c981e6e12e82cdd0a148abc81fc245410bb313f13dcf1`。
- `valid=true`且`release_ready=false`，符合audit语义。
- 报告准确识别当前跨仓状态中的浮动`quant-workspace`依赖、内部tag/HEAD不一致、schema/lock缺口、dirty/untagged仓库及`quant-regime`、`sklearn-stock-trend`缺失。
- 烟测清单写入系统临时目录，验证后已删除；未改写任何其他仓库。

## 原始日志

- `validation/m6-pytest-junit.xml`
- `validation/m6-coverage.json`

Python3.10、3.11本机未安装；对应三版本验证已作为GitHub Actions矩阵门禁配置，需push后由CI确认。
