# 量化研究工作台

一份YAML配方生成全部候选，预登记后回放，再产出账本、因子证据、成本压力、失败诊断和可筛选报告。支持A股固定观察池与ETF趋势轮动；期货价差和crypto基差使用隔离的冻结软件样例。

六项功能、测试结果、数据边界与关联PR见[验收记录](VALIDATION.md)。

## 安装

使用独立目录，Python3.10–3.12。先检出本PR的quant-workspace，再运行：

```powershell
python quant-workspace/profiles/research-workbench/bootstrap.py
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py verify
```

bootstrap读取stack.json，对缺失仓库克隆固定提交，对已有仓库只验证、不切换分支或覆盖修改。公共依赖使用requirements.lock；应用仓库按精确Git提交安装。安装器不使用系统site-packages。输出放在各源码仓库外，避免研究产物让代码身份变脏。Linux将Scripts/python.exe替换为bin/python。

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

diagnostics控制single_factors、ablations、cost_multipliers、signal_delays和frequencies；variants定义有限参数邻域。全部候选在回放前登记。新公式需在quant-factors实现并测试注册；配方不接受任意Python。

## 3．补充历史与数据预检

使用pe_inv或pb_inv时，必须分别在required_history中声明pe_ratio或pb_ratio属于fundamentals。无关历史文件或行情中缓存的财务列不能代替披露时间记录。

```powershell
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py history history.csv --output data/history-v1 --provider YOUR_PROVIDER --source-uri YOUR_SOURCE --license-note "YOUR_DATA_RIGHTS"
```

格式和时间语义见quant-data-kit的docs/research-history.md。配方inputs.history指向冻结目录，required_history可声明`{pe_ratio: fundamentals, industry: classification, tradable: status, member: universe}`。未知披露时间、断档、缺预热、非法价格和不完整历史会阻断并生成缺口清单。该功能提供导入、版本化、PIT筛选及覆盖检查，**没有凭空增加真实供应商历史权限**。当前回放仅支持固定观察池，遇到停牌/变动成分会拒绝，不能假装撮合。

## 4．稳健性与诊断

自动比较同投入上限买入持有、单因子、组合消融、2倍成本、信号延迟、调仓频率及手工邻域。保留逐年/连续子段收益及明确失败原因。所有成交和费用来自QExec账本。

描述性ICIR、子段和参数扰动不是独立留出证明，也不自动挑选赢家。当前份额根据初始资金计算、仅日频撮合；真实A股模板复用已有40日四股票观察池，不能宣称全市场、多年稳定或替代被数据冲突阻断的126日样本。

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
