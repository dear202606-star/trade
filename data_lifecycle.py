"""data_lifecycle.py — 数据生命周期策略：全流程唯一事实来源。

2026-11-07 全链路审查后建立。目的：把「什么数据、从哪来、经哪些阶段、
多久更新一次、页面多久读一次、为什么要更新」从散落在 19 个工作流、
20+ 脚本、若干文档里的隐性约定，收敛为一份机器可读、可被校验的配置。

设计原则
  1. 单一写入源：任何展示数据只有一条合法写入链（在线数据库）；
     本地文件不得作为并行主源，避免双套体系的一致性风险。
  2. 按「数据实际变更频率」而非「统一轮询间隔」决定抓取与刷新节奏。
  3. 历史锁定 ≠ 永远不碰：能永久锁的是不随时间漂移的绝对量
     （raw 收盘价、已发生的公司行动、由它们固化并验收的回测产物）；
     前复权序列 adj_close 会随未来分红/拆股整体重算，只能锁
     「复权基准版本 + 可复算证据」，不能锁绝对数值。
  4. 每个策略都必须回答：准确性 / 及时性 / 完整性 / 有效性 / 必要性；
     必要性为 NONE 的环节应从管线中移除，而不是保留"以防万一"。
  5. 页面自动感知数据版本，不依赖人工触发或手工调校。

分层（刷新频率由低到高）
  IMMUTABLE  一次性/事件驱动：源不变就永不重抓，仅在新证券上市、
             行业重分类、交易日历年度发布时由显式事件更新
  FROZEN     历史冻结：2026-01-01 之前的已验收历史，不走采集管线；
             保留只读复核能力（可复核 ≠ 重新采集）
  MONTHLY    月频：月末基准收盘、成分股边界、市场建议
  DAILY      日频：收盘后一次性计算当日日线/净值/排名/预览
  INTRADAY   日内：仅交易时段，行情快照
  DERIVED    前端派生：由已加载数据即时计算，不落库、不抓取
"""
from __future__ import annotations

# 历史冻结边界。与 sql/050_data_lock.sql 现有 962 条 symbol_month 锁
# （2012-01 ~ 2025-12）保持一致，因此 2025 年之前的历史已经处于冻结态。
FROZEN_BEFORE = "2026-01-01"
FROZEN_RANGE = ("2012-01", "2025-12")

LAYERS = {
    "IMMUTABLE": {
        "cadence": "事件驱动 / 一次性",
        "fetch": "仅在源发生变更时（新上市、行业重分类、日历年度发布）",
        "display_ttl_h": 24,
        "accuracy": "必须可追溯至审核证据，错一条即全站错",
        "timeliness": "允许滞后数天，不影响净值计算",
        "completeness": "要求 100% 覆盖当前 universe",
        "validity": "append-only 证据链 + 身份回显校验",
        "necessity": "不轮询；仅在有明确变更事件时重抓",
    },
    "FROZEN": {
        "cadence": "冻结（不再走采集管线）",
        "fetch": "不抓取；只读复核指纹",
        "display_ttl_h": 24,
        "accuracy": "raw 绝对价格与已验收回测结果不可变",
        "timeliness": "无时效要求",
        "completeness": "锁定区间内必须逐年逐月连续",
        "validity": "库内 guard_frozen_prices 触发器硬拒改写 + sha256 指纹",
        "necessity": "重抓为零收益且会引入前复权漂移，明确禁止",
    },
    "MONTHLY": {
        "cadence": "月频",
        "fetch": "每月首个交易日收盘后",
        "display_ttl_h": 6,
        "accuracy": "月末收盘须与已封存日线一致",
        "timeliness": "月度决策前完成即可",
        "completeness": "九个已核验月末 + 当月最新",
        "validity": "交易日历 sha256 绑定",
        "necessity": "市场建议/成分股边界按月变化，日更无意义",
    },
    "DAILY": {
        "cadence": "日频（收盘后）",
        "fetch": "美股收盘后一次；同一场次结果不重复计算",
        "display_ttl_h": 0.5,
        "accuracy": "双源对账 + fail-closed，宁可停更不出错数",
        "timeliness": "收盘后 1 小时内",
        "completeness": "A/B 两口径齐全、持仓非空",
        "validity": "quote_version 与行情快照版本严格一致",
        "necessity": "净值/排名/预览按交易日推进，盘中无新信息",
    },
    "INTRADAY": {
        "cadence": "日内（仅交易时段）",
        "fetch": "UTC 13-21 周一至周五，5 分钟；非交易时段不抓",
        "display_ttl_h": 0.0167,
        "accuracy": "基准 SPY 与持仓报价必须与快照同源",
        "timeliness": "分钟级",
        "completeness": "SPY 必须存在",
        "validity": "evidence_sha256 == version，collected_at 单调递增",
        "necessity": "只在交易时段有价值；盘后继续轮询纯属浪费",
    },
    "DERIVED": {
        "cadence": "浏览器即时计算",
        "fetch": "不抓取、不落库",
        "display_ttl_h": 0,
        "accuracy": "由已加载数据确定性推导",
        "timeliness": "随源数据刷新",
        "completeness": "依赖上游齐全",
        "validity": "前端单元测试覆盖",
        "necessity": "无需服务端重复计算",
    },
}

# 数据集清单：key -> (层, 用途, 途径/来源, 阶段, 存储, 展示策略)
# stage 取值：acquire(抓取) / clean(清洗对账) / store(转存) / analyze(计算) / present(展现)
DATASETS = {
    # ── IMMUTABLE：一次性 / 事件驱动 ────────────────────────────────
    "security_directory": ("IMMUTABLE", "证券身份与市场代码映射",
        "sys_event.live_security_directory（append-only 审核证据）", "acquire",
        "sys_event", "仅显式事件更新"),
    "name_zh": ("IMMUTABLE", "持仓中文名展示",
        "static 映射合并 → site_dataset", "store",
        "site_dataset.name_zh", "24h 缓存，不轮询在线库"),
    "sectors": ("IMMUTABLE", "GICS 行业标签",
        "sector_frozen + overrides → site_dataset", "store",
        "site_dataset.sectors", "24h 缓存，不轮询在线库"),
    "exchange_calendar": ("IMMUTABLE", "交易日历与月末边界",
        "sys_event 已核验证据（append-only）", "acquire",
        "v_market_calendar", "年度发布，sha256 绑定"),
    "corporate_actions": ("IMMUTABLE", "已发生的分红/拆股事件",
        "发行方与数据源交叉核验", "acquire",
        "corporate_actions", "历史部分冻结，仅新增事件"),

    # ── FROZEN：2026-01-01 之前，不走采集管线 ──────────────────────
    "prices_monthly_raw": ("FROZEN", "历史原始收盘价（回测基准）",
        "2012-01~2025-12 已验收锁", "acquire",
        "prices_monthly", "只读复核，禁止重抓"),
    "curve": ("FROZEN", "全周期回测净值曲线",
        "由冻结历史一次性计算并验收", "analyze",
        "site_dataset.curve / b/curve", "源不变则永不重算"),
    "strategy_history": ("FROZEN", "逐年持仓与收益",
        "由冻结历史一次性计算并验收", "analyze",
        "site_dataset.strategy_history / b/*", "locked_ok 秒级返回"),
    "annual_anchor": ("FROZEN", "年度持仓锚点",
        "显式审核后写入，revision 递增", "store",
        "live_strategy_anchor", "年度换仓才变更"),

    # ── MONTHLY：月频 ──────────────────────────────────────────────
    "members": ("MONTHLY", "指数成分股边界",
        "已审核边界证据 + 时点快照", "acquire",
        "members / v_membership_open", "月度更新"),
    "market_advice": ("MONTHLY", "大盘仓位建议（九个已核验月末）",
        "SPY 月末收盘 + 交易日历", "analyze",
        "equity.market_advice", "月度决策前"),
    "frozen_fixtures": ("MONTHLY", "冻结基线快照与漂移基线",
        "refresh-fixtures 月度工作流", "store",
        "static/*_frozen.* + 基线 JSON", "每月 1 日"),

    # ── DAILY：收盘后一次 ──────────────────────────────────────────
    "daily_bars": ("DAILY", "当日日线（raw + 复权）",
        "东财主源 / Yahoo 独立源交叉核验", "clean",
        "live_ranking_context / prices_monthly(新增月)", "收盘后一次"),
    "equity": ("DAILY", "A/B 两口径当期净值与持仓",
        "锚点 + 当日日线 + 行情快照对账", "analyze",
        "v_live_strategy_results", "30min 展示缓存，版本驱动刷新"),
    "signal": ("DAILY", "当期信号与大盘开关",
        "年度锚点 payload.signal", "store",
        "live_strategy_anchor", "30min 展示缓存"),
    "preview": ("DAILY", "次年策略预览与动态排名",
        "全 universe 排名上下文计算", "analyze",
        "v_live_strategy_results(preview)", "30min 展示缓存"),
    "site_dataset": ("DAILY", "页面历史/静态展示数据集",
        "CI 构建产物 → 在线库单一写入口", "store",
        "site_dataset", "内容 sha256 相同则不更新"),
    "health": ("DAILY", "数据体检与停更标记",
        "质量核对结果", "analyze",
        "site_dataset.health", "日更"),

    # ── INTRADAY：仅交易时段 ───────────────────────────────────────
    "quotes": ("INTRADAY", "实时行情快照（SPY + 持仓）",
        "行情源 → 双源修复 → 版本化写入", "acquire",
        "live_quote_snapshot / v_live_quote_display", "交易时段 60s 展示缓存"),

    # ── DERIVED：前端即时计算 ──────────────────────────────────────
    "today_pnl": ("DERIVED", "当日盈亏/市值/涨跌",
        "equity + quotes 前端计算", "present", "不落库", "随源刷新"),
    "charts": ("DERIVED", "净值曲线图/年度回撤图",
        "curve + strategy_history 前端绘制", "present", "不落库", "随源刷新"),
}

# 允许「故障期兜底」的数据集必须能由在线库单向导出，且带同源指纹。
# 镜像不是第二套数据：它由 CI 从主源只读导出，禁止任何手写/反向写入。
MIRROR_POLICY = {
    "role": "故障期只读兜底",
    "write_source": "postgres-direct (只读导出)",
    "direction": "数据库 → 镜像（严格单向）",
    "normal_mode": "不参与常规读取；常态前端只读在线库",
    "failure_mode": "熔断窗口内由镜像 + localStorage 提供最近一次成功值",
    "consistency": "每个数据集带 sha256 与 updated_at，导出后回读校验",
    "atomicity": "临时文件 + os.replace，校验失败绝不覆盖上一份好镜像",
}

# 整库备份策略：排除凭据表，产出可校验、可恢复的自定义格式转储。
BACKUP_POLICY = {
    "cadence": "每日一次（UTC 04:00，美股收盘后、采集高峰前）",
    "retention_days": 90,
    "scope": "全部业务表 + 视图定义 + 锁 + 审计事件",
    "exclude": ["data_source_secret"],   # 凭据永不进入备份产物
    "verify": "sha256 + 表清单 + 行数统计 + 恢复预检",
    "local": "支持 --output 落到本地，满足整库回本地要求",
}


def layer_of(key: str) -> str:
    entry = DATASETS.get(key)
    if not entry:
        raise KeyError("未登记的数据集：%s（请先在策略中定义）" % key)
    return entry[0]


def display_ttl_ms(key: str) -> int:
    """前端展示缓存 TTL；策略变更时前后端共用同一份定义。"""
    return int(LAYERS[layer_of(key)]["display_ttl_h"] * 3600 * 1000)


def summary() -> dict:
    counts = {}
    for layer, _purpose, _src, _stage, _store, _disp in DATASETS.values():
        counts[layer] = counts.get(layer, 0) + 1
    return {
        "frozen_before": FROZEN_BEFORE,
        "frozen_range": FROZEN_RANGE,
        "datasets": len(DATASETS),
        "by_layer": counts,
        "layers": {k: v["cadence"] for k, v in LAYERS.items()},
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
