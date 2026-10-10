# 量化研究工作台

本目录是固定提交的研究集成配置及其历史运行方式。当前日常网页入口与操作说明见 [Quant Studio 日常指南](../../../quant-studio/DAILY_WORKFLOW.md)。网页部署使用各应用独立环境；不要用本目录的 bootstrap 覆盖已有冻结账户环境。历史 VALIDATION 文件保留原日期与结论。

一份YAML配方生成全部候选，预登记后回放，再产出账本、因子证据、成本压力、失败诊断和可筛选报告。支持A股固定观察池与ETF趋势轮动；期货价差和crypto基差使用隔离的冻结软件样例。

本轮新增自动滚动样本外验证、本机操作台、受限因子表达式、当前NAV组合、动态状态、前向模拟和有据研究助手。指南见[第二阶段使用说明](GUIDE_V2.md)，测试和真实数据边界见[第二阶段验收记录](VALIDATION_V2.md)。上一轮六项记录保留在[历史验收记录](VALIDATION.md)。

约束与风险接线更新见[第三阶段使用说明](GUIDE_V3.md)和[第三阶段验收记录](VALIDATION_V3.md)：联合约束优化、因果风险收益、绝对/主动因子暴露、整手目标复验，以及实际持仓风险锁存。真实数据证据与前向观察的验收状态须以各研究登记和报告为准。

## 安装

使用独立目录，Python3.10–3.12。先检出本PR的quant-workspace，再运行：

```powershell
python quant-workspace/profiles/research-workbench/bootstrap.py
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py verify
```

bootstrap读取stack.json，对缺失仓库克隆固定提交，对已有仓库只验证、不切换分支或覆盖修改。公共依赖使用requirements.lock；应用仓库按精确Git提交安装。安装器不使用系统site-packages。输出放在各源码仓库外，避免研究产物让代码身份变脏。Linux将Scripts/python.exe替换为bin/python。

Windows新环境可显式选择较新的Python3.12维护版本，例如3.12.13：

```powershell
python quant-workspace/profiles/research-workbench/bootstrap.py --python C:/path/to/python3.12/python.exe --env .venv-research-new
```

`--python`选择创建环境的基础解释器，不受启动bootstrap的旧Python影响。
已有环境的基础路径或补丁版本不匹配时会拒绝复用；请新建目录，保留冻结账户的原环境。
Python包锁不包含原生C++运行库。排查DuckDB的Windows访问冲突时，还需核对进程实际加载的DLL：
Python目录自带的旧`VCRUNTIME140.dll`可能优先于已更新的系统DLL加载。
参见[DuckDB官方Windows故障说明](https://duckdb.org/docs/current/clients/python/known_issues)。
更新独立Python运行时后应重跑QDK进程完整性和全量测试，不能以一次成功证明偶发崩溃已永久消失。

本研究配置统一选择 `stack.json` 的源码提交，显式覆盖各包独立发行锁中的内部 Git 引用；
`--no-deps` 安装和 `PYTHONPATH` 由配置入口控制，不应与各仓自己的锁混装。
它是研究集成配置，不代表这些主线提交已经通过 M8 tag 发布认证。
历史发布标签、冻结 fixture 环境及旧前向账户继续使用原版本。

2026-10-01维护快照同步了工作台各应用的已审查源码及外部依赖，并纳入QDK行动观察契约修复。
本轮进一步固定因子历史PIT、报告归因、组合现金预算及期货/crypto双腿失败关闭修复；
新目录安装和两种账户策略的验收见[第四阶段验收记录](VALIDATION_V4.md)。
公司行动的首次采集时间仅在完整记录版本相同时保留，每次刷新仍有独立哈希回执；真实来源修订仍会改变前向输入前缀。
该版本必须安装到新的集成目录，不能覆盖旧前向账户或替换其冻结代码身份。

2026-10-03进一步修复主表有效期按日期截断导致的同日误拒，并纳入严格行情和期货输入检查。
13项源码、锁定依赖、两种账户模式和原四ETF候选在新环境的实际验证见[第五阶段验收记录](VALIDATION_V5.md)。
旧账户保持冻结；修复后的历史重放不增加前向观察或独立样本外天数。

公共依赖声明保存在`requirements.in`，涵盖当前应用运行、研究与LLM扩展、构建和验证工具。
使用Python3.10解析并覆盖3.10–3.12条件依赖；重建后同时更新`stack.json`中的锁文件SHA-256：

```bash
uv pip compile profiles/research-workbench/requirements.in --universal --python-version 3.10 \
  --no-header --no-annotate --index-url https://pypi.org/simple \
  --output-file profiles/research-workbench/requirements.lock
```

编译器可使用SHA-256完全一致的随仓AKShare工件进行离线解析，提交锁仍保留原公开URL与哈希。
跨仓CI会实际下载正式工件、运行`pip check`，并将QDK重复采集及真实修订回归接入Pipeline的完整历史前缀校验。

安装后执行跨仓验收（输出目录必须尚不存在）：

```powershell
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/smoke.py --output integration-smoke
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/smoke.py --output integration-continuous --account-policy continuous
```

验收生成明确标记为合成的 ETF 数据，执行带动态状态的滚动训练与测试、训练方向学习、
2 倍成本和延迟信号候选，检查逐折风险证据、报告与续跑产物哈希。
GitHub `Research integration` 工作流在 Windows/Linux 分别从固定提交安装，覆盖独立折账户和连续账户。
连续账户额外检查所选策略路径的完整证据、恢复时指标复算，以及缓存收益被改写后的拒绝行为。
固定源码包含风控 CSV 输入的严格校验、重复证券持仓合并，以及连续路径的跨进程恢复锁。
组合源码同时固定了因子调整后的仓位预算与证券标识保留、多期行业和换手联合约束、
以真实初始持仓计算首期成本，以及市场模拟的严格递增时间检查。
最新固定版本还限制因子只能使用组合截止时已可得的记录；账户建议按实际调仓增量
核算费用和 100 股整手数量，零股清仓明确转为人工复核。高级流动性检查拒绝缺失、
无穷数值及无效阈值，并返回无法评估状态，防止无效输入被误判为通过。
数据无效时风控 CLI 返回非零状态；并发恢复遇到锁占用时可在当前任务结束后重试，已完成结果仍保留在原位置。
`smoke.json` 记录软件集成结果，不能作为策略收益或实盘认证。

## 1．从配方产出研究

以下命令从集成目录执行：

```powershell
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py plan quant-workspace/profiles/research-workbench/templates/ashare.yaml
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py run quant-workspace/profiles/research-workbench/templates/ashare.yaml --output studies/ashare
```

打开`studies/ashare/research.html`。同一命令重跑会校验并复用已完成结果，失败会保留旧尝试并重试；配方、输入或代码改变后需另建study_id及输出目录。不要从多次运行里只保留赢家。

独立新研究：

```powershell
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py init --template quant-workspace/profiles/research-workbench/templates/ashare.yaml --output recipes/my-study.yaml --study-id my-study
```

初始化自动修正相对输入路径。默认exploratory；提供`--holdout-start`及`--holdout-end`可登记未来留出，过去的留出日期会拒绝。未来登记与已完成样本外验证是两件事。

## 2．因子筛选

17个已注册共享因子均可引用，方向显式写`1`或`-1`。报告展示覆盖率、1/5/20日IC与RankIC、ICIR、正IC比例、年度及可用的行业/regime分段、两两相关。至少3个有效证券且截面非恒定才计算相关；缺失结果显示不可用。

diagnostics控制single_factors、ablations、cost_multipliers、signal_delays和frequencies；variants定义有限参数邻域。全部候选在回放前登记。新公式可使用factor_expressions限定表达式，不接受任意Python。

## 3．补充历史与数据预检

使用pe_inv或pb_inv时，必须分别在required_history中声明pe_ratio或pb_ratio属于fundamentals。无关历史文件或行情中缓存的财务列不能代替披露时间记录。

```powershell
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py history history.csv --output data/history-v1 --provider YOUR_PROVIDER --source-uri YOUR_SOURCE --license-note "YOUR_DATA_RIGHTS"
```

格式和时间语义见quant-data-kit的docs/research-history.md。配方inputs.history指向冻结目录，required_history可声明`{pe_ratio: fundamentals, industry: classification, tradable: status, member: universe}`。未知披露时间、断档、缺预热、非法价格和不完整历史会阻断并生成缺口清单。动态回放需显式execution配置、五类状态及股票池历史；缺少当时可得证据仍会阻断。

## 4．稳健性与诊断

自动比较同投入上限买入持有、单因子、组合消融、2倍成本、信号延迟、调仓频率及手工邻域。保留逐年/连续子段收益及明确失败原因。所有成交和费用来自QExec账本。

描述性ICIR、子段和参数扰动不是独立留出证明。validation可按训练结果逐折选择显式候选并输出样本外证据；allocation按当前净值调仓，未声明该字段的旧配方保留原有行为。目前仅日频撮合；真实A股模板是已有40日四股票观察池，不能宣称全市场、多年稳定。

## 5．研究助手

```powershell
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py propose paper.txt --template quant-workspace/profiles/research-workbench/templates/ashare.yaml --output drafts/idea --study-id idea-v1 --database studies/ashare/experiments.db
```

默认离线模板草案，保存来源哈希、数据需求、相似研究及笔记；不会声称离线理解论文。结构化探索导入和显式LLM选项见quant-agent/docs/research-assistant.md。在线调用需要用户配置模型，并显式启用来源发送；来源文本中的指令和模型代码都不会执行。草案通过相同配方预检后可交给run命令。

## 6．四类策略模板

|模板|用途|数据范围|
|---|---|---|
|ashare.yaml|动量+低波截面选择|已保存40日真实观察池|
|etf.yaml|ETF动量排序+趋势过滤|需用户提供真实ETF行情和主表|
|futures.yaml|价差双腿、保证金、换月与手数对照|冻结fixture-only|
|crypto.yaml|基差阈值、funding及maker/taker对照|冻结fixture-only|

可立即运行合成ETF示例验证安装：

```powershell
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py demo --asset etf --output demos/etf
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py run demos/etf/recipe.yaml --output studies/etf-demo
```

合成数据不是市场表现。期货与crypto需要另一个环境，运行`bootstrap_fixtures.py`安装其独立锁，再使用：

```powershell
python quant-workspace/profiles/research-workbench/bootstrap_fixtures.py
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py run quant-workspace/profiles/research-workbench/templates/futures.yaml --output studies/futures --fixture-python .venv-fixtures/Scripts/python.exe
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py run quant-workspace/profiles/research-workbench/templates/crypto.yaml --output studies/crypto --fixture-python .venv-fixtures/Scripts/python.exe
```

子进程隔离PYTHONPATH，并核对三个认证依赖的精确提交。不得把新的A股工作台依赖覆盖到冻结fixture环境。此工作台没有订单发送或经纪商连接。
