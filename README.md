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
