# CyTrade2

CyTrade2 是一个基于 [xtquant](https://dict.thinktrader.net/nativeApi/start_now.html) / QMT 的 Python 量化交易框架，覆盖以下完整链路：

- 交易连接与自动重连
- 实时行情订阅与分发
- 策略运行与状态恢复
- 订单追踪与成交回调
- 持仓管理与盈亏统计
- 手续费追踪与 T+0/T+1 可用仓位控制
- Watchdog 监控告警
- FastAPI + Vue Web 控制台

当前仓库适合以下用途：

- 作为个人量化交易框架骨架
- 作为 xtquant/QMT 集成示例
- 作为策略开发、回放验证、Web 控台集成的学习项目

> 安全说明：仓库默认不再包含任何真实账号、密码、令牌或本地客户端路径。运行前请自行配置本地环境。

---

## 特性

| 模块 | 能力 |
|---|---|
| 连接管理 | QMT 连接、断线重连、重连回调 |
| 数据订阅 | 个股订阅、全市场订阅、重连后恢复订阅 |
| 历史数据 | 批量下载、独立读取、多周期、复权、字段选择、缓存复用 |
| 交易日历 | 交易日判断、交易日偏移、交易日区间生成 |
| 交易执行 | 限价、市价、按金额下单、平仓、撤单 |
| 订单管理 | UUID 追踪、柜台单号映射、成交/状态更新 |
| 持仓管理 | 移动平均成本、FIFO、实时浮盈/实盈统计 |
| 费率管理 | 费率表匹配、佣金/印花税追踪、T+0/T+1 可用仓位 |
| 策略框架 | `BaseStrategy`、信号与交易分离、风控前置 |
| 策略运行 | 选股、行情分发、调度、快照恢复、停止归档 |
| 数据持久化 | SQLite、本地状态恢复、可选 PostgreSQL 同步 |
| 监控告警 | 心跳、连接状态、数据超时、CPU/内存、钉钉通知 |
| Web 控制台 | FastAPI REST、WebSocket、Vue 3 前端 |

---

## 项目结构

```text
cytrade2/
├── config/                  # 枚举、配置、费率表模板
├── core/                    # QMT 回调、连接、订阅、历史数据
├── data/                    # SQLite / 状态文件 / 可选远程同步
├── monitor/                 # 日志、看门狗
├── position/                # 持仓模型与管理器
├── strategy/                # 策略基类、运行器、示例策略
├── trading/                 # 交易执行、订单管理、交易模型
├── web/                     # FastAPI 后端 + Vue 前端
├── tests/                   # pytest 回归测试
├── main.py                  # 主入口
├── requirements.txt
├── 设计文档.md
├── plan.md
└── 终审.md
```

---

## 运行环境

- Python 3.10 推荐
- Windows
- 已安装并可登录的 QMT 客户端
- Node.js 18+（仅前端开发时需要）

Python 依赖见 `requirements.txt`，前端依赖见 `web/frontend/package.json`。

---

## 快速开始

### 1. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

如后续已发布到 PyPI，也可直接安装：

```bash
pip install cytrade2
```

### 2. 配置本地环境

框架支持两种配置方式：

1. 直接修改 `config/settings.py`
2. 通过环境变量覆盖默认值（推荐开源使用方式）

常用环境变量如下：

| 变量名 | 说明 | 示例 |
|---|---|---|
| `QMT_PATH` | QMT 客户端路径 | `D:\QMT\XtMiniQmt.exe` |
| `ACCOUNT_ID` | 资金账号 | `your_account_id` |
| `ACCOUNT_PASSWORD` | 登录密码 | `your_password` |
| `SUBSCRIPTION_PERIOD` | 默认行情订阅周期 | `tick` / `1m` / `5m` |
| `SQLITE_DB_PATH` | SQLite 路径 | `./data/db/cytrade2.db` |
| `STATE_SAVE_DIR` | 策略状态目录 | `./saved_states` |
| `LOG_DIR` | 日志目录 | `./logs` |
| `WEB_PORT` | Web 端口 | `8080` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `ENABLE_REMOTE_DB` | 是否启用远程同步 | `false` |
| `FEE_TABLE_PATH` | 费率表路径 | `./config/fee_rates.csv` |
| `DEFAULT_BUY_FEE_RATE` | 默认买入手续费率 | `0.0001` |
| `DEFAULT_SELL_FEE_RATE` | 默认卖出手续费率 | `0.0001` |
| `DEFAULT_STAMP_TAX_RATE` | 默认印花税率 | `0.0003` |

参考环境变量模板见 `.env.example`。

说明：

- `SUBSCRIPTION_PERIOD` 当前支持的合法值为：`tick`、`1m`、`5m`
- 配置加载后会按 `SubscriptionPeriod` 枚举校验
- 若环境变量值非法，会自动回退为 `tick`

### 2.1 费率表配置

默认读取 [config/fee_rates.csv](config/fee_rates.csv)。

当前已支持：

- 按证券代码精确匹配费率
- 按代码前缀/通配符匹配费率
- 单独配置买入佣金、卖出佣金、卖出印花税
- 配置证券是否为 `T+0`
- 未匹配时回退到 `settings.py` 默认费率

字段说明：

- `code_pattern`：证券代码匹配规则，支持精确匹配和通配符，例如 `600000`、`159***`、`*`
- `buy_fee_rate`：买入佣金费率
- `sell_fee_rate`：卖出佣金费率
- `stamp_tax_rate`：卖出印花税率
- `is_t0`：是否允许当日回转交易

若费率表中未匹配到证券代码，则回退到 `config/settings.py` 中的默认费率：

- 买入手续费率：万 1
- 卖出手续费率：万 1
- 印花税率：万 3

所有费用均按成交金额计算，并向上取到分。

费用追踪生效后：

- 每笔成交会拆分并记录：买入佣金、卖出佣金、印花税、总费用
- 持仓会累计记录：买佣、卖佣、印花税、总费用
- 持仓成本与已实现盈亏会纳入费用影响
- `T+1` 标的当日买入不会增加可卖数量
- `T+0` 标的当日买入后可卖数量会同步增加

### 2.2 历史数据下载与读取

历史数据模块位于 [core/history_data.py](core/history_data.py)，当前支持：

- 使用 `xtdata.download_history_data2(...)` 批量下载历史数据
- 使用 `xtdata.get_market_data_ex(...)` 独立读取本地缓存
- 支持多周期下载与读取
- 支持不同复权方式
- 支持自定义 `field_list`
- 支持控制 `fill_data`
- 下载时支持 `tqdm` 进度条显示

推荐使用方式：

```python
from core.history_data import HistoryDataManager

mgr = HistoryDataManager()

# 1. 先批量下载到本地缓存
mgr.download_history_data(
    stock_list=["000001", "600000"],
    start_date="20250101",
    end_date="20250301",
    period="1d",
)

# 2. 再独立读取本地缓存
data = mgr.read_history_data(
    stock_list=["000001", "600000"],
    start_date="20250101",
    end_date="20250301",
    period="1d",
    dividend_type="front",
    field_list=["open", "high", "low", "close", "volume"],
    fill_data=True,
)
```

兼容接口 `get_history_data(...)` 仍然保留，但当前更推荐：

- `download_history_data(...)`：只下载
- `read_history_data(...)`：只读取

### 3. 启动主程序

```bash
python main.py
```

默认会加载示例策略 `TestGridStrategy`。

启动后默认可访问：

- REST API: `http://localhost:8080/api`
- WebSocket: `ws://localhost:8080/ws/realtime`

### 4. 启动前端开发服务（可选）

```bash
cd web/frontend
npm install
npm run dev
```

开发模式下默认访问：

- 前端开发页：`http://localhost:5173`
- 后端 API：`http://localhost:8080/api`

Vite 已配置代理，前端开发服务会自动转发 `/api` 和 `/ws` 到后端。

### 5. 本机生产化部署

前端已支持生产构建，后端也支持直接托管前端构建产物。

#### 方式 A：前后端分离部署

1. 启动后端：

```bash
python main.py
```

2. 构建前端：

```bash
cd web/frontend
npm install
npm run build
```

3. 本机预览前端构建结果：

```bash
npm run preview
```

#### 方式 B：单服务部署（推荐本机）

1. 先构建前端：

```bash
cd web/frontend
npm install
npm run build
```

2. 回到项目根目录启动后端：

```bash
python main.py
```

3. 直接打开：

```text
http://localhost:8080/
```

说明：

- 如果检测到 `web/frontend/dist/index.html`，后端会自动托管前端静态文件。
- 刷新前端路由页面时会自动回落到 SPA 入口。
- WebSocket 会根据页面协议自动使用 `ws` 或 `wss`。

---

## 核心设计

### 交易日控制

- 交易日工具已统一收敛到 [core/trading_calendar.py](core/trading_calendar.py)
- 可直接从 `core` 包导入：`is_market_day`、`add_one_market_day`、`minus_one_market_day`、`add_market_day`、`TargetDate`
- 兼容层 `date.py` 仍然保留，但新代码建议直接使用 `core.trading_calendar`
- `StrategyRunner` 启动时会先判断是否为交易日：
    - 非交易日：不激活策略、不订阅行情、定时选股直接跳过
    - 交易日：恢复/创建策略后自动激活，并订阅对应标的行情

### 交易主链路

```text
xtquant/QMT
  -> callback.py
  -> order_manager.py / connection.py
  -> position/manager.py
  -> strategy/runner.py
  -> strategy/base.py
  -> trading/executor.py
```

### 设计原则

- 策略只产出信号，不直接操作底层接口
- 订单、成交、持仓分层处理
- 回调统一做异常保护
- 重连后自动恢复订阅
- 清仓后自动归档策略盈亏
- Web 撤单走真实交易执行链路
- 成交费用在回报链路中自动计算并写入持仓/数据库/Web 展示
- 历史数据能力保持为通用基础模块，不与具体策略耦合

---

## 开发策略

在 `strategy/` 下新增策略文件，并继承 `BaseStrategy`。

最小示例如下：

```python
from strategy.base import BaseStrategy
from strategy.models import StrategyConfig
from core.models import TickData


class MyStrategy(BaseStrategy):
    strategy_name = "MyStrategy"

    def select_stocks(self) -> list[StrategyConfig]:
        return [
            StrategyConfig(
                stock_code="000001",
                entry_price=10.0,
                stop_loss_price=9.5,
                take_profit_price=11.0,
                max_position_amount=50_000,
            )
        ]

    def on_tick(self, tick: TickData) -> dict | None:
        if tick.last_price <= self.config.entry_price:
            return {
                "action": "BUY",
                "price": tick.last_price,
                "amount": 10_000,
                "remark": "entry signal",
            }
        return None
```

然后在 `main.py` 中注册：

```python
from strategy.my_strategy import MyStrategy

run(strategy_classes=[MyStrategy])
```

参考实现：`strategy/test_grid_strategy.py`

如需在策略或任务中判断交易日，可直接使用：

```python
from core.trading_calendar import is_market_day, add_market_day

if is_market_day("20260306"):
    next_day = add_market_day("20260306", 1)
```

---

## Web 接口概览

后端提供常用控制与监控接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/strategies` | 查询策略 |
| POST | `/api/strategies/{id}/pause` | 暂停策略 |
| POST | `/api/strategies/{id}/resume` | 恢复策略 |
| POST | `/api/strategies/{id}/close` | 强制平仓 |
| GET | `/api/positions` | 查询持仓 |
| GET | `/api/orders` | 查询订单 |
| POST | `/api/orders/{uuid}/cancel` | 撤单 |
| GET | `/api/trades` | 查询成交 |
| GET | `/api/system/status` | 系统状态 |
| GET | `/api/system/logs` | 最近日志 |

与本次更新相关的接口能力：

- `/api/positions`
    - 返回 `available_quantity`
    - 返回 `is_t0`
    - 返回累计买佣、卖佣、印花税、总费用
- `/api/positions/summary`
    - 返回全局费用汇总与总盈亏
- `/api/trades`
    - 返回单笔成交的买佣、卖佣、印花税、总费用、`is_t0`

前端控制台当前已支持：

- 持仓页查看 `T+0/T+1`、可用数量、累计费用
- 成交页查看单笔费用拆分
- 总览页查看累计费用统计卡片

前端技术栈：

- Vue 3
- Vite
- Element Plus
- Pinia

---

## 测试

当前基线：`84 passed`

```bash
python -m pytest tests/ -v
```

当前已覆盖：

- 连接管理
- 数据管理
- 数据订阅恢复
- 历史数据批量下载与独立读取
- 费率表加载与默认回退
- 主入口装配
- 订单管理
- 交易执行
- 持仓计算
- T+0 / T+1 可用仓位规则
- 手续费计入成本与盈亏
- 策略运行
- Web 撤单链路

说明：策略状态持久化当前采用 `pickle`，用于项目内部跨交易日恢复；
不保证跨大版本结构变更后的兼容性。

---

## 打包与发布

本项目已支持标准 Python 打包。

### 本地构建

```bash
python -m build
python -m twine check dist/*
```

### 发布到 PyPI

推荐通过环境变量提供凭据：

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=<your-pypi-token>
python -m twine upload dist/*
```

Windows PowerShell 可用：

```powershell
$env:TWINE_USERNAME="__token__"
$env:TWINE_PASSWORD="<your-pypi-token>"
python -m twine upload dist/*
```

本地示例配置见 `.pypirc.example`，但不要提交真实 `.pypirc`。

---

## 开源使用建议

### 1. 不要提交真实配置

请勿把以下信息提交到仓库：

- QMT 客户端真实路径
- 资金账号和密码
- 钉钉 Webhook / Secret
- 数据库账号密码
- 任意 Git / API Token

### 2. 建议本地忽略的内容

项目已附带 `.gitignore`，建议本地运行文件、日志、数据库、状态文件都不要纳入版本控制。

### 3. 当前已知限制

- 强依赖 Windows + QMT 环境
- `xtquant` 不同版本的订阅参数细节可能存在差异
- 状态恢复基于 `pickle`，更适合内部运行态恢复，不适合作为长期兼容存档格式

---

## 相关文档

- `设计文档.md`：总体设计说明
- `plan.md`：详细实施计划
- `整改追踪表.md`：问题与整改跟踪
- `终审.md`：终审结论与修复回写
- `CONTRIBUTING.md`：贡献约定
- `SECURITY.md`：安全说明
- `RELEASE_CHECKLIST.md`：发布前检查清单
- `CHANGELOG.md`：版本变更记录

---

## 免责声明

本项目仅用于学习、研究和自有环境验证，不构成任何投资建议。  
实盘使用前，请自行完成：

- 账户权限核验
- 行情与交易接口版本核验
- 风控规则核验
- 长时间稳定性测试
- 真实环境回归测试

---

## License

本项目采用 [MIT License](LICENSE)。
2. **日期格式**：统一使用 `"YYYYMMDD"` 字符串（如 `"20260227"`）。
3. **Mock 模式**：未连接 QMT 时，`TradeExecutor` 自动进入 Mock 模式，订单在内存中模拟成交，适用于策略逻辑调试。
4. **跨交易日恢复**：每日 15:05 定时保存策略快照到 `saved_states/`；下次启动时自动加载当天状态文件。
5. **最小下单单位**：`buy_by_amount` 按 100 股取整，不足 100 股时不下单。
