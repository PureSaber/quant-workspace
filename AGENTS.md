# quant-workspace

Central path resolver for the PureSaber quant multi-repo stack.

## Commands

```bash
pip install -e ".[dev]"
quant-workspace --config configs/default.workspace.yaml show
quant-workspace --config configs/default.workspace.yaml path a-share-multifactor outputs
pytest -q
ruff check src tests
```

## Related

- [quant-pipeline](../quant-pipeline)
- [quant-lab](../quant-lab)
- [quant-research-notes](../quant-research-notes)
