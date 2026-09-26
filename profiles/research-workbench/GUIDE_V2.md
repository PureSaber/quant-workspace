# 第二阶段使用说明

先按README安装固定环境并运行verify。当前电脑已安装的环境名是`.venv-workbench`，新安装默认为`.venv-research`。以下命令从各仓所在的集成根目录运行。

## 直接使用操作台

```powershell
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py demo --advanced --output demos/advanced
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py web --workspace studies/console --data-root demos --template demos/advanced/recipe.yaml --port 8766
```

打开http://127.0.0.1:8766，选择研究→预检→运行→报告。合成例子包含自定义因子、三种组合方法、动态池/交易状态和滚动验证，只证明软件功能。创建自己的研究时，复制为新的研究编号，改用有来源证明的数据与历史主表。

已经登记的配方冻结；修改参数必须复制。可选两至八项研究比较数据、代码、窗口、费用差异；不可比的研究会标记，失败记录仍保留。笔记和有据助手可直接使用，不需要在线模型配置。

## 自动验证及前向账户

完整字段、CLI和时间语义见[quant-pipeline工作台指南](https://github.com/PureSaber/quant-pipeline/blob/main/docs/research-workbench-v2.md)。

滚动验证只在训练窗口选择候选和可选方向，embargo覆盖标签成熟窗口，测试窗口不重叠；每折独立从现金开始并计入成本。报告明确这是历史滚动验证，不是未来留出证明。配对区块bootstrap与BH调整是有假设的探索统计。

前向模拟在源研究全部完成、输入未变且足够新鲜后注册，冻结策略、代码和数据前缀。真实未来开始日期必须在已知交易日历中；结束日期可以是日历边界。观察时使用同源新数据，旧前缀不可变，到期一次性封存。没有券商接口；未自动启用每天运行的计划任务。

## 真实数据与增量更新

```powershell
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py dataset build --root artifacts/etf-data --symbols 510300 510500 159915 588000 --start 2025-01-02 --end 2026-09-24
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py dataset update --root artifacts/etf-data --end 2026-09-24 --overlap-sessions 5
.venv-research/Scripts/python.exe quant-workspace/profiles/research-workbench/run.py dataset inspect --root artifacts/etf-data
```

旧快照不改写，新增快照保留来源、父版本和重叠修订。真实支付日晚于除权日时，应收计入NAV，到付款日才增加可用现金。数据契约与来源见[QDK数据集说明](https://github.com/PureSaber/quant-data-kit/blob/main/docs/RESEARCH_DATASETS.md)。

第1项的抓取、增量、结构与复权/分红检查可自动完成。当前历史证券主表及五类交易状态的PIT来源仍不足；今天抓取到的规则不能倒填到过去。因此真实历史策略回放会阻断。下一步需要取得研究起点前可公开核验或有授权的历史主表/状态数据，再检查供应商口径；人工点击通过不能代替数据证据。

已有40日A股研究和冻结期货/crypto样例保留原有范围。日线模拟不能证明盘口排队、成交容量或实盘收益。在线LLM入口保留，但本轮未进行真实在线模型调用。
