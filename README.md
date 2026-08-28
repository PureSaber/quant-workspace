# quant-workspace

Central path resolver and immutable `StackManifest 1.0.0` producer for the PureSaber quant multi-repo stack. It eliminates hard-coded sibling paths and freezes the exact Git, package, dependency, schema, and external-lock state consumed by releases.

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

Set `QUANT_WORKSPACE_ROOT=D:/projects` to override the `root` field in YAML.

## Stack declarations

Each runnable repository declares release metadata in `pyproject.toml`. Lock files are repository-relative and must not escape the repository.

```toml
[tool.quant-workspace]
layer = "strategy"
schemas = [
  { id = "standard/v2", version = "2.0.0" },
]
lock-files = ["requirements.lock"]
```

Supported layers are `data`, `contract`, `execution`, `strategy`, `portfolio-risk`, `reporting`, and `orchestration`. Internal dependencies are discovered from PEP 508 project dependencies. Release mode accepts only a local, resolvable tag or a full 40-character commit and requires it to resolve to the dependency repository's recorded `HEAD`.

Workspace YAML may freeze the allowed schema set. If omitted, the union of repository declarations is recorded.

```yaml
allowed_schemas:
  - id: standard/v2
    version: 2.0.0
```

`audit` writes a deterministic manifest with exact warnings and always sets `release_ready=false`. `release` fails closed for dirty or untagged repositories, floating or mismatched internal references, missing targets, dependency cycles, missing schemas/locks, and path escape. The writer fsyncs a same-directory temporary file, publishes atomically, and never replaces an existing path.

## Related

- [quant-pipeline](../quant-pipeline) — post-run orchestration
- [quant-lab](../quant-lab) — experiment index
- [quant-research-notes](../quant-research-notes) — architecture docs
