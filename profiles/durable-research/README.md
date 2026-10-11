# 共享快照与持久因子研究安装配置

此配置固定六仓Git提交和Python3.12公共依赖，提供QDK共享快照/CAS、Factors持久缓存、Lab滚动研究、Pipeline独立Worker及Studio网页提交入口。按固定源码提交安装普通包，检查`pip check`、模块安装位置及VCS来源身份；不依赖`PYTHONPATH`源码覆盖。

本配置显式选择`stack.json`的六仓组合，覆盖各仓独立发行依赖中较旧的内部Git引用。安装器通过`--no-deps`执行这份经过测试的组合，不能与历史发行锁混装。Workspace包本身使用已合并的稳定提交；当前profile文件来自本次检出的交付提交，避免清单引用自身提交。

## 服务机安装

先检出包含此目录的quant-workspace交付版本，选择一个**尚不存在的永久目录**。需要Python3.12和Git：

```powershell
py -3.12 quant-workspace/profiles/durable-research/bootstrap.py --root D:/quant-durable-v1
```

安装器会创建`D:/quant-durable-v1/workspace`和`D:/quant-durable-v1/env`，下载锁定依赖、检出六仓精确提交并保存`installed-stack.json`。失败目录保留便于检查，重试请选另一个新目录。虚拟环境直接在最终位置创建，不移动已有环境。

若服务机已有完整Git仓库，可添加`--source-root D:/quant-sources`使用本地克隆；各仓仍必须含清单中的精确提交。不能覆盖旧环境、前向账户或已有研究记录。

## 由服务机执行验收

```powershell
D:/quant-durable-v1/env/Scripts/python.exe -I quant-workspace/profiles/durable-research/smoke.py --root D:/quant-durable-v1
```

脚本在新的`acceptance`目录生成固定合成快照，检查：

- 六仓普通安装包的实际导入位置与Git提交身份。
- 网页重复提交返回同一任务；关闭网页服务后，独立Worker完成任务；网页重启后可以查到结果。
- 同一快照的5/10/20窗口研究，共享输入只登记一次，重复计算命中缓存。
- 失败尝试保留并可重试，所有结果引用真实CAS产物且仍受pin保护，原始输入哈希不变。

结果保存在`acceptance/acceptance.json`，测试进程退出后没有常驻网页或Worker。该目录必须不存在；需要复验时使用新的完整安装目录，以保留前次证据。

GitHub工作流在Windows/Linux执行相同的软件集成测试。实际服务机的账号、磁盘、长期进程管理、自启、备份与异地访问仍需在目标机器单独确认。本次提交尚未执行服务机部署验收。

## 接入网页和Worker

服务机验收通过后，管理员将实际已发布快照的完整单因子请求配置到Studio的`QUANT_STUDIO_DURABLE_CONFIG`。详细格式与命令见[Studio后台因子研究](https://github.com/PureSaber/quant-studio/blob/main/DURABLE_RESEARCH.md)。网页和Worker使用相同的任务库、CAS及缓存路径，独立启动并管理生命周期。

`smoke.py`生成的`acceptance/durable-config.json`只配置合成样例，不能当作真实行情。真实数据需要先由QDK发布不可变快照，再填入完整请求；网页不会接收任意路径、模块或命令。

已有工作台可使用新Studio代码及现有设置，在单独Python3.12环境运行；各应用解释器继续沿用原设置。切换前保留原服务配置与记录，按服务机原流程安排重启和回退。本profile不自动迁移历史Notebook或冻结账户。
