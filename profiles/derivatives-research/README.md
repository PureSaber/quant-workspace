# 独立衍生品研究环境

当前固定组合新增 Cboe VX、NSE 指数 F&O 与 Deribit 公开数据接入。
网页流程和限制见 [免费源指南](https://github.com/PureSaber/quant-studio/blob/main/FREE_SOURCES.md)。
安装仍不自动下载真实行情；用户在数据集页面选择合约、日期后显式采集。
Deribit 币本位期权仅作美元折算分析。既有方案模板保持不变，免费源使用新增模板。

该配置安装期权、海外期货和 Studio，固定应用源码提交与完整依赖锁。使用 Python 3.12、Git 和已获授权的 quant-options 私有仓库访问。不要向脚本或命令行粘贴密钥，使用已有 Git 凭证管理器。

```powershell
python profiles/derivatives-research/bootstrap.py --root D:/quant-derivatives-new
D:/quant-derivatives-new/env/Scripts/python.exe -m quant_studio serve --host 127.0.0.1 --port 8780 --runs-root D:/quant-derivatives-new/runs --settings D:/quant-derivatives-new/studio-settings.json
```

`--root` 必须是新目录；失败后保留诊断，重新尝试用新目录。Linux 解释器路径为 `env/bin/python`。安装器检出精确提交、验证锁文件 SHA-256、安装非 editable 应用、执行 `pip check`，生成明确标记为合成的两类样例数据和 settings。

已有工作台只合并两个数据目录与 `quant-options`、`quant-futures-global` 运行环境项，并在原 workspace 下同步相应源码。`quant-futures-global` 指向 quant-futures-spread 的 `international/` 子项目；原认证期货环境的 `quant-futures-spread` 映射保留。不要覆盖既有研究数据、方案、前向账户、M8 清单或历史 research-workbench 配置。

网页操作、数据采集上限及模型边界见 [Studio 衍生品指南](https://github.com/PureSaber/quant-studio/blob/main/DERIVATIVES.md)。本配置用于工程研究，不认证交易获利。完整真实历史需要具体交割合约、条款来源、结算/标的价格及有效数据权限。
