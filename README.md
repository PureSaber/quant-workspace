# quant-workspace

Central path resolver for the PureSaber quant multi-repo stack. Eliminates hard-coded sibling paths across `quant-lab`, `quant-pipeline`, and other tools.

## Install

```bash
pip install -e ".[dev]"
```

## Usage

```bash
quant-workspace show --config configs/default.workspace.yaml
quant-workspace path a-share-multifactor outputs
quant-workspace lab-config --out ../quant-lab/configs/from-workspace.yaml
```

Set `QUANT_WORKSPACE_ROOT=D:/projects` to override the `root` field in YAML.

## Related

- [quant-pipeline](../quant-pipeline) — post-run orchestration
- [quant-lab](../quant-lab) — experiment index
- [quant-research-notes](../quant-research-notes) — architecture docs
