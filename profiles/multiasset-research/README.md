# Multi-asset research installation

This profile freezes the combined convertible, global futures, macro, market-making, merger, option and swap research runtime. It includes 26 Studio entries. It creates a separate Python 3.12 environment and never changes an existing service, saved recipe or certified environment.

From this repository, use Python 3.12:

```powershell
python profiles/multiasset-research/bootstrap.py --root D:/quant-research-new
```

The destination must not exist. Access to the private `PureSaber/quant-options` and `PureSaber/quant-swaps` repositories requires the user's existing GitHub authorization. Source commits and every external dependency are pinned. The profile's workspace package is pinned independently from this installer to avoid a self-referential manifest.

For a transferred release containing the original source archives, installation can avoid GitHub:

```powershell
python profiles/multiasset-research/bootstrap.py --root D:/quant-research-new --archives D:/release/source-archives
```

Every archive is verified against `stack.json`; unsafe paths and symbolic links are rejected. PyPI dependencies must already be cached or remain reachable. The installer checks dependency closure and writes the settings and installed source manifest into the new destination. Selecting datasets, copying existing settings and switching a running service are separate deployment operations.

## Evidence boundaries

- Convertible research includes bond floor and conversion premium calculations, contractual event windows, stock hedges, borrow/funding/collateral, coupons, conversion resets and terminal settlement. The option component is an explicitly disclosed research approximation; issuer credit and optionality require reviewed assumptions.
- Global futures include a 34-product planning universe, synchronized basis analysis, shared-capital portfolios and causal roll decisions. Free commodity vendor histories are reference data and are not dated contract histories.
- The actual NSE example covers 29 observed sessions, four contracts and one two-product roll. It does not satisfy the long-history coverage gate.
- Macro, institutional, option and swap examples preserve synthetic or retrospective labels. Public ECB/Fed/SOFR observations do not become executable quotes or point-in-time historical universes.
- Licensed histories, historical contract/security terms, borrow availability, actual fees/margin, brokerage access and reviewed legal/clearing terms remain explicit external inputs. No component sends live orders or payments.

The release manifest, installed-runtime acceptance receipts, HTTPS deployment receipt and backup hashes are the authoritative delivery records. A successful installation alone is not evidence of complete real-market coverage or investment performance.
