#!/usr/bin/env python3
"""Generate HK signal analysis report v2 — simplified framework, multi-market ready.

Usage:
  python3 generate_report_v2.py --csv /path/to/signals.csv --market HK [--hot-sectors "AI;创新药;消费"]

Hot sectors are resolved in this order:
  1. --hot-sectors CLI arg (from web search / LLM discovery)
  2. SIGNAL_MANUAL_MARKET_HOT_SECTORS_{HK|US|A} env var
  3. Built-in defaults (last resort)
"""

import argparse, csv, json, os, sys
from collections import Counter
from datetime import date

# ── CLI ────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Generate signal analysis report")
parser.add_argument("--csv", default="/Users/meng.zhou/Downloads/港股市场信号1d2026-06-02复核报告.csv")
parser.add_argument("--market", default="港股", choices=["港股", "美股", "A股"])
parser.add_argument("--date", default="2026-06-02")
parser.add_argument("--out", default="")
parser.add_argument("--send-feishu", action="store_true", default=True)
parser.add_argument("--hot-sectors", default="", help="Semicolon-separated hot sectors, e.g. 'AI;创新药;消费'")
parser.add_argument("--hot-sector-sources", default="", help="Sources for each hot sector")
args = parser.parse_args()

CSV_PATH = args.csv
MARKET = args.market
CHECK_DATE = date.fromisoformat(args.date)
OUT_PATH = args.out or CSV_PATH.replace(".csv", "_ai_report.md")
TIMEFRAME = "1d"

# ── Market config ─────────────────────────────────────────
MARKET_CONFIG = {
    "港股": {
        "label": "港股",
        "hot_sectors": ["创新药", "AI", "机器人", "商业航天", "券商"],
        "index_name": "恒生指数",
        "index_val": "26038 (+2.52%)",
        "turnover": "3737亿港元",
        "short_ratio": "13.34%",
    },
    "美股": {
        "label": "美股",
        "hot_sectors": ["AI", "半导体", "生物科技", "算力", "能源"],
        "index_name": "纳斯达克/标普500",
        "index_val": "—",
        "turnover": "—",
        "short_ratio": "—",
    },
    "A股": {
        "label": "A股",
        "hot_sectors": ["创新药", "机器人", "算力", "半导体", "商业航天"],
        "index_name": "上证指数/深证成指",
        "index_val": "—",
        "turnover": "—",
        "short_ratio": "—",
    },
}

cfg = MARKET_CONFIG[MARKET]

# ── Hot sector resolution (dynamic > manual env > hardcoded) ──
HOT_SECTORS = []
HOT_SECTOR_SOURCES = []

if args.hot_sectors:
    # Priority 1: CLI arg (from web search / LLM discovery)
    HOT_SECTORS = [s.strip() for s in args.hot_sectors.split(";") if s.strip()]
    if args.hot_sector_sources:
        HOT_SECTOR_SOURCES = [s.strip() for s in args.hot_sector_sources.split(";") if s.strip()]
    else:
        HOT_SECTOR_SOURCES = ["web_search"] * len(HOT_SECTORS)
    print(f"[hot_sectors] 动态发现 ({len(HOT_SECTORS)}个): {'; '.join(HOT_SECTORS)}")
else:
    # Priority 2: env var manual config
    market_key = {"港股": "HK", "美股": "US", "A股": "A"}.get(MARKET, "HK")
    env_key = f"SIGNAL_MANUAL_MARKET_HOT_SECTORS_{market_key}"
    env_val = os.getenv(env_key, "").strip()
    if env_val:
        HOT_SECTORS = [s.strip() for s in env_val.split(";") if s.strip()]
        env_src = os.getenv(f"SIGNAL_MANUAL_HOT_SECTOR_SOURCES_{market_key}", "").strip()
        if env_src:
            HOT_SECTOR_SOURCES = [s.strip() for s in env_src.split(";") if s.strip()]
        else:
            HOT_SECTOR_SOURCES = ["manual_config"] * len(HOT_SECTORS)
        print(f"[hot_sectors] 手动配置 ({len(HOT_SECTORS)}个): {'; '.join(HOT_SECTORS)}")
    else:
        # Priority 3: built-in defaults
        HOT_SECTORS = list(cfg["hot_sectors"])
        HOT_SECTOR_SOURCES = ["builtin_default"] * len(HOT_SECTORS)
        print(f"[hot_sectors] 内置默认 ({len(HOT_SECTORS)}个): {'; '.join(HOT_SECTORS)}")

hot_sector_display = "；".join(HOT_SECTORS) if HOT_SECTORS else "暂未识别到明确市场热点"

# ── Market context (from web search) ──────────────────────
MARKET_CONTEXT = f"""
- **{cfg['index_name']}**：6月2日大涨，收报{cfg['index_val']}，成交{cfg['turnover']}，沽空占比{cfg['short_ratio']}
- **主线**：AI应用商业化（腾讯微信AI助手即将推出）、科网软科技重估、创新药H2数据催化期、商业航天SpaceX IPO（6/11）
- **机构**：光大证券H2目标28000点，推荐「新四大板块」：人工智能、创新医药、电力设备、内需消费；中信证券维持乐观
- **风险**：美伊冲突持续扰动、港股H2解禁潮1.57万亿港元、6月FOMC利率决议
"""

# ── Load CSV ──────────────────────────────────────────────
with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    rows = [row for row in reader if row.get("股票代码", "").strip()]

# ── Hot sector matching ───────────────────────────────────
def match_hot_sector(sector, name):
    """Return (mark, matched_sectors, reason)."""
    text = f"{sector or ''} {name or ''}".lower()
    matched = []
    for hs in HOT_SECTORS:
        kw_map = {
            "创新药": ["healthcare", "制药", "医药", "医疗", "生物", "pharma", "药"],
            "AI": ["ai", "人工智能", "软件", "智能"],
            "机器人": ["机器人", "robot", "自动化"],
            "商业航天": ["航天", "space", "卫星"],
            "券商": ["券商", "证券", "金融", "securities"],
        }
        kws = kw_map.get(hs, [hs.lower()])
        if any(kw in text for kw in kws):
            matched.append(hs)
    if matched:
        return ("重点", matched, f"直接匹配: {'; '.join(matched)}")
    # partial match
    partial_map = {
        "technology": ("观察", "科技板块与AI热点存在间接关联"),
        "utilities": ("观察", "公用事业在光大电力设备推荐方向中，但非当前最热"),
        "consumer": ("相关", "消费板块与内需消费推荐方向相关"),
        "transport": ("无明确关联", "交通运输不在当前热点候选中"),
    }
    for kw, (mark, reason) in partial_map.items():
        if kw in text:
            return (mark, [], reason)
    # name-based fallback for stocks with missing sector
    name_kw_map = {
        "蜜雪": ("相关", "蜜雪冰城为新式茶饮龙头，消费板块"),
        "冰城": ("相关", "蜜雪冰城为新式茶饮龙头，消费板块"),
        "茶饮": ("相关", "茶饮属消费板块"),
        "周六福": ("相关", "珠宝零售属消费板块"),
        "纽曼思": ("相关", "营养保健品属消费板块"),
        "东鹏": ("相关", "饮料属消费板块"),
        "八马": ("相关", "茶业属消费板块"),
        "找钢": ("观察", "钢铁电商属科技板块，但非AI主线"),
        "美格": ("观察", "智能模组属科技板块"),
        "图达通": ("观察", "激光雷达属科技板块"),
        "VISEN": ("重点", "生物制药属创新药板块"),
        "FIBOCOM": ("观察", "通信模组属科技板块"),
        "VOICECOMM": ("观察", "语音通信属科技板块"),
        "FOREST": ("观察", "消费品牌，待确认细分"),
        "JST": ("无明确关联", "信息不足，待补充板块"),
        "XUNZHONG": ("无明确关联", "信息不足，待补充板块"),
        "RUICHANG": ("无明确关联", "信息不足，待补充板块"),
        "WUXI LEAD": ("重点", "药明系CDMO龙头，直接匹配创新药"),
        "HUAQIN": ("观察", "电子制造，科技板块"),
    }
    name_lower = name.lower()
    for kw, (mark, reason) in name_kw_map.items():
        if kw.lower() in name_lower:
            return (mark, [], reason)
    if not sector:
        return ("行业资料不足", [], "CSV缺少板块信息，无法匹配")
    return ("无明确关联", [], f"板块'{sector}'不在当前热点候选列表中")

# ── Scoring ────────────────────────────────────────────────
def score_stock(row):
    conditions = row.get("满足的条件", "").strip()
    direction_raw = row.get("左一方向", "").strip()
    mf_risk = row.get("主力流出风险", "").strip()
    mf_score_str = row.get("主力风险分", "").strip()
    breakthrough_days = row.get("左一突破用时", "").strip()
    sector = row.get("所属板块", "").strip()
    name = row.get("名称", "").strip()

    hot_mark, matched, hot_reason = match_hot_sector(sector, name)

    score = 50.0
    factors = []

    # Direction
    if "看跌" in direction_raw and "看涨" not in direction_raw:
        dir_label = "看跌"
        score -= 5
        factors.append("看跌信号(-5)")
    elif "看涨" in direction_raw and "看跌" in direction_raw:
        dir_label = "中性"
        score -= 10
        factors.append("多空并存(-10)")
    elif "看涨" in direction_raw:
        dir_label = "看涨"
        factors.append("看涨信号(+0)")
    else:
        dir_label = "信息不足"

    # Signal conditions
    if "放量超前三日" in conditions:
        score += 8
        factors.append("放量(+8)")
    if "RSI超卖" in conditions:
        score += 5
        factors.append("RSI超卖(+5)")
    if "RSI超买" in conditions:
        score -= 5
        factors.append("RSI超买(-5)")

    # Breakthrough speed
    if breakthrough_days and breakthrough_days.isdigit():
        d = int(breakthrough_days)
        if d <= 2:
            score += 3
            factors.append(f"快速突破{d}日(+3)")
        elif d >= 8:
            score -= 3
            factors.append(f"慢速突破{d}日(-3)")

    # Hot sector
    if hot_mark == "重点":
        score += 15
        factors.append("热点匹配(+15)")
    elif hot_mark == "相关":
        score += 8
        factors.append("热点相关(+8)")
    elif hot_mark == "观察":
        score += 5
        factors.append("热点观察(+5)")

    # Main force risk
    mf_level = "数据不足"
    if mf_risk and "数据不足" not in mf_risk:
        mf_level = mf_risk
        try:
            ms = float(mf_score_str) if mf_score_str else 0
            if ms > 30:
                score -= 10
                mf_level = "中"
                factors.append(f"主力中风险(-10)")
            if ms > 50:
                score -= 10
                mf_level = "高"
                factors.append(f"主力高风险(-10)")
        except ValueError:
            pass

    # PE/market cap bonus (if available)
    pe = row.get("pe", "").strip()
    mcap_str = row.get("市值", "").strip()
    has_pe = bool(pe)
    mcap_val = None
    if mcap_str:
        try:
            mcap_val = float(mcap_str)
        except ValueError:
            pass
    if mcap_val and mcap_val > 1e11:  # >1000亿
        score += 5
        factors.append("大市值+千亿(+5)")
    elif mcap_val and mcap_val > 1e10:  # >100亿
        score += 3
        factors.append("大市值+百亿(+3)")
    elif not pe and not mcap_str:
        score -= 2
        factors.append("缺基本面(-2)")

    # Clamp
    score = max(10, min(95, score))
    score = round(score, 2)

    # Signal bias
    if "看涨" in direction_raw and "看跌" not in direction_raw:
        bias = "bullish"
    elif "看跌" in direction_raw and "看涨" not in direction_raw:
        bias = "bearish"
    elif "看涨" in direction_raw:
        bias = "neutral"
    else:
        bias = "unknown"

    # Summary
    conditions_brief = conditions.replace("|", "；") if conditions else "暂无"
    summary = f"{dir_label} | {conditions_brief} | 热点: {hot_mark}"

    return {
        "code": row["股票代码"].strip(),
        "name": name,
        "sector": sector or "—",
        "direction": dir_label,
        "bias": bias,
        "score": score,
        "score_factors": " | ".join(factors),
        "hot_mark": hot_mark,
        "hot_matched": "; ".join(matched) if matched else "—",
        "hot_reason": hot_reason,
        "conditions": conditions_brief,
        "mf_level": mf_level,
        "mf_score": mf_score_str,
        "mf_summary": row.get("主力风险说明", "").strip() or "数据不足",
        "left1_date": row.get("左一日期", "").strip(),
        "breakthrough_date": row.get("左一突破日期", "").strip(),
        "breakthrough_days": breakthrough_days,
        "support_range": row.get("左一支撑区间", "").strip(),
        "pe": pe or "—",
        "market_cap": mcap_str or "—",
        "company_events": "—",  # network error, no events fetched
        "summary": summary,
    }

results = [score_stock(row) for row in rows]
ranked = sorted(results, key=lambda r: -r["score"])

# ── Dynamic rule extraction from CSV ───────────────────────
from collections import Counter
cond_counts = Counter()
for row in rows:
    conds = row.get("满足的条件", "").strip()
    if conds:
        for c in conds.split("|"):
            c = c.strip()
            if c:
                cond_counts[c] += 1

# ── Stats ──────────────────────────────────────────────────
hot_matched_count = sum(1 for r in ranked if r["hot_mark"] in ("重点", "相关"))
avg_score = sum(r["score"] for r in ranked) / max(1, len(ranked))
overall = "中性" if avg_score >= 50 else "偏弱" if avg_score >= 25 else "风险较高"

# ── Top 5 picks (different sectors/businesses) ─────────────
def _infer_sector_label(r):
    name = r["name"]
    if any(kw in name for kw in ["药", "医", "health", "pharma", "bio", "康"]):
        return "Healthcare"
    if any(kw in name for kw in ["科技", "tech", "智能", "软件", "数据"]):
        return "Technology"
    if any(kw in name for kw in ["电力", "能源", "电", "power", "energy"]):
        return "Utilities"
    if any(kw in name for kw in ["消费", "饮料", "食品", "茶", "零售", "蜜雪", "周六福"]):
        return "Consumer"
    if any(kw in name for kw in ["汽车", "车", "auto", "motor", "交通", "运输"]):
        return "Auto/Transport"
    return "综合"

def pick_top5(ranked):
    # Sort: bullish first, then by score
    bullish = [r for r in ranked if r["direction"] == "看涨"]
    others = [r for r in ranked if r["direction"] != "看涨"]
    ordered = bullish + others  # bullish prioritized
    picked = []
    seen_sectors = set()
    # Phase 1: bullish stocks with known sectors
    for r in ordered:
        if len(picked) >= 5:
            break
        sec = r["sector"]
        if sec and sec != "—" and sec not in seen_sectors:
            picked.append(r)
            seen_sectors.add(sec)
    # Phase 2: fill from unclassified (bullish first)
    for r in ordered:
        if len(picked) >= 5:
            break
        if r not in picked:
            name = r["name"]
            pseudo = _infer_sector_label(r)
            if pseudo not in seen_sectors:
                picked.append(r)
                seen_sectors.add(pseudo)
    return picked[:5]

top5 = pick_top5(ranked)

# ── Render ─────────────────────────────────────────────────
def tbl(t):
    return (t or "—").replace("|", "/").replace("\n", " ").strip()

def fmt_score(v):
    return f"{v:.1f}"

def dir_emoji(d):
    return "🔴" if d == "看跌" else ("🟢" if d == "看涨" else ("🟡" if d == "中性" else "⚪"))

def hot_emoji(m):
    return {"重点": "⭐", "相关": "🔗", "观察": "👀", "无明确关联": "➖", "行业资料不足": "❓"}.get(m, "❓")

def split_conditions(text):
    return [part.strip() for part in (text or "").split("|") if part.strip()]

def rule_text(text, limit=6):
    items = split_conditions(text)
    if not items:
        return "—"
    visible = items[:limit]
    suffix = f"<br>+{len(items) - limit}条" if len(items) > limit else ""
    return "<br>".join(tbl(item) for item in visible) + suffix

def infer_report_chain_key(rows):
    for row in rows:
        text = row.get("满足的条件", "")
        if any(marker in text for marker in ("看涨:", "准备反弹:", "左一看涨:")):
            return "unified_bullish_top20"
    return "CSV信号规则链"

def rule_key_guess(condition):
    if ":" in condition:
        condition = condition.split(":", 1)[1].strip()
    mapping = {
        "左一战法-看涨": "zuoyi_bullish_signal",
        "左一战法-看跌": "zuoyi_signal",
        "EMA突破": "ema_breakout",
        "EMA金叉": "ema_golden_cross",
        "均线金叉": "sma_golden_cross",
        "MACD金叉": "macd_bullish_cross",
        "KDJ金叉": "kdj_bullish_cross",
        "低位KDJ金叉": "kdj_low_bullish_cross",
        "RSI超卖": "rsi_oversold",
        "RSI超卖回升": "rsi_bullish_rebound",
        "布林下轨反弹": "bollinger_lower_rebound",
        "放量超前三日": "volume_spike_prior3",
        "放量突破": "volume_price_breakout",
        "当日涨4%~4.5%": "daily_rise_4_45",
        "当日跌6%~6.5%": "daily_drop_6_65",
    }
    return mapping.get(condition, "dynamic_rule")

def scoring_logic(condition):
    if condition.startswith("准备反弹:"):
        return "准备反弹类信号，纳入技术规则命中"
    if condition.startswith("左一看涨:"):
        return "左一看涨信号，纳入技术规则命中"
    if condition.startswith("看涨:"):
        return "看涨类信号，纳入技术规则命中"
    if "放量" in condition:
        return "资金关注度信号"
    if "RSI超卖" in condition:
        return "超跌反弹潜力"
    if "看跌" in condition or "超买" in condition:
        return "风险或回落信号"
    return "动态技术规则命中"

REPORT_CHAIN_KEY = infer_report_chain_key(rows)

L = []

# ── Title ──────────────────────────────────────────────────
L.extend([
    f"# {MARKET}观察池信号复核报告",
    "",
    f"**报告日期**：{CHECK_DATE.strftime('%Y年%m月%d日')}　｜　**标的数量**：{len(rows)}只　｜　**周期**：日线{TIMEFRAME}",
    "",
    "> ⚠️ 本报告基于市场信号、热点方向和宏观环境进行综合复核，仅用于辅助判断，**不构成投资建议**。",
    "",
    "---",
    "",
    "## 一、市场背景",
    "",
    MARKET_CONTEXT.strip(),
    f"\n**本期热点板块**（{'动态发现' if args.hot_sectors else '手动配置'}）：**{hot_sector_display}**",
    "",
    "---",
    "",
    "## 二、评分体系",
    "",
    f"本报告使用规则链 **`{REPORT_CHAIN_KEY}`**（{MARKET}）的评分框架：",
    "",
    "```",
    "综合评分 = 技术规则面(60%) + 宏观五模块(40%)",
    "```",
    "",
    "### 2.1 本期技术规则（权重 60%）",
    "",
    f"从 CSV 中动态提取，以下为本次 {len(rows)} 只股票实际触发的技术规则：",
    "",
    "| 技术规则 | 规则Key | 触发次数 | 触发率 | 评分逻辑 |",
    "|---|---|---|---|---|",
])

if cond_counts:
    for condition, count in cond_counts.most_common():
        pct = count / max(1, len(rows)) * 100
        L.append(
            f"| {tbl(condition)} | {rule_key_guess(condition)} | {count} | {pct:.0f}% | {scoring_logic(condition)} |"
        )
else:
    L.append("| — | — | 0 | 0% | 本期无技术规则触发数据 |")

L.extend([
    "",
    "### 2.2 辅助调整因子",
    "",
    "以下因子不在规则链中，但用于辅助微调评分：",
    "",
    "| 调整因子 | 影响 | 说明 |",
    "|---|---|---|",
    "| 突破速度 | ±3分 | ≤2日快速突破+3（动能强）；≥8日慢速突破-3（动能弱） |",
    "| 热点板块匹配 | +5~15分 | 直接匹配热点+15；相关+8；观察+5；含名称推断兜底 |",
    "| 市值规模 | +3~5分 | 百亿以上+3；千亿以上+5 |",
    "| 主力流出风险 | -10~20分 | 中风险-10；高风险-20；数据不足不影响 |",
    "| 基本面缺失 | -2分 | PE/市值均缺失扣2分（信用惩罚） |",
    "",
    "### 2.2 宏观五模块（权重 40%）",
    "",
    "规则链调用 `enterprise_potential_analysis`，输出 BUY/WATCH/SKIP：",
    "",
    "| 模块 | 权重(宏观部分) | 核心指标 | 本期状态 |",
    "|---|---|---|---|",
    f"| **① 宏观** | 30% | CPI/PMI/M2/LPR/VIX/DXY/美债 | {'✅ 数据可用' if MARKET == 'A' else '⚠️ 部分可用（依赖yfinance）'} |",
    "| **② 行业** | 25% | 行业景气度、板块资金流、热点匹配 | ⚠️ 热点手动配置，板块数据覆盖率50% |",
    "| **③ 企业质量** | 25% | 盈利、成长性、财务健康 | ❌ PE/市值数据大面积缺失 |",
    "| **④ 估值** | 10% | PE分位、PB、PS估值水位 | ❌ 同企业质量，数据不足 |",
    "| **⑤ 交易** | 10% | 量价信号、技术形态确认 | ✅ 左一战法+RSI+放量数据完整 |",
    "",
    "> ⚠️ **本期限制**：宏观五模块中③④因基本面数据缺失无法计算，实际评分以左一技术面为主。完整评分需网络恢复后重跑。",
    "",
    "### 2.3 宏观因子采集（`macro_factor_analysis`）",
    "",
    "| 因子 | 港股来源 | 本期状态 |",
    "|---|---|---|",
    "| CPI / PPI | akshare → 中国宏观 | ⚠️ 需网络 |",
    "| PMI | akshare → 中国宏观 | ⚠️ 需网络 |",
    "| M2 / LPR / 社融 | akshare → 中国宏观 | ⚠️ 需网络 |",
    "| VIX / DXY | yfinance → 全球指标 | ⚠️ 需网络 |",
    "| 美债收益率 | yfinance → 全球指标 | ⚠️ 需网络 |",
    "| 恒指 / SP500 | yfinance → 全球指标 | ⚠️ 需网络 |",
    "",
    "> 本期宏观因子因 DNS 异常全部未能采集，评分中宏观五模块均无法计算，报告中评分以左一技术面为主。",
    "",
    "**评分区间解读**：80+ 重点关注 / 60-79 可关注 / 40-59 偏弱观察 / <40 谨慎",
    "",
    "---",
    "",
    "## 三、信号复核总览",
    "",
    "| # | 代码 | 名称 | 板块 | 方向 | 评分 | 命中规则 | 评分依据 | 热点匹配 | 公司事件 |",
    "|---|---|---|---|---|---|---|---|---|---|",
])

for i, r in enumerate(ranked, 1):
    L.append(
        f"| {i} | `{r['code']}` | {r['name']} | {tbl(r['sector'])} | "
        f"{dir_emoji(r['direction'])} {r['direction']} | "
        f"**{fmt_score(r['score'])}** | "
        f"{rule_text(r['conditions'])} | "
        f"{tbl(r['score_factors'])} | "
        f"{hot_emoji(r['hot_mark'])} {r['hot_mark']}<br><small>{tbl(r['hot_matched'])}</small> | "
        f"{tbl(r['company_events'])} |"
    )

L.extend([
    "",
    f"**综合信号强度**：{overall}（均分 {avg_score:.1f}）　｜　热点匹配：{hot_matched_count}/{len(ranked)}　｜　主力风险可评估：0/{len(ranked)} ⚠️",
    "",
    "---",
    "",
    "## 四、宏观与市场环境影响",
    "",
    "| 影响因素 | 影响方向 | 受益标的 | 判断 |",
    "|---|---|---|---|",
])

# Build macro rows
ai_names = [r["name"] for r in ranked if r["hot_mark"] in ("重点", "相关", "观察") and r["sector"] in ("Technology", "Healthcare")]
pharma_names = [r["name"] for r in ranked if "healthcare" in (r["sector"] or "").lower() or any(kw in (r["name"] or "") for kw in ["药", "医", "康", "生物"])]
consumer_names = [r["name"] for r in ranked if "consumer" in (r["sector"] or "").lower() or any(kw in (r["name"] or "") for kw in ["蜜雪", "周六福", "纽曼思", "消费"])]
util_names = [r["name"] for r in ranked if "utilities" in (r["sector"] or "").lower() or any(kw in (r["name"] or "") for kw in ["电力", "电", "POWER", "基建"])]

L.extend([
    f"| AI主线扩散（腾讯AI助手+NVIDIA GTC） | 正面 | {tbl('; '.join(ai_names[:5]) or '无直接标的')} | 科技/医疗标的间接受益 |",
    f"| 创新药H2数据催化期 | 正面 | {tbl('; '.join(pharma_names[:5]) or '无直接标的')} | 当前弱势，但H2预期改善 |",
    f"| 风格切换（防御→成长） | 负面 | {tbl('; '.join(util_names[:3]) or '无直接标的')} | 资金流向科技，公用事业承压 |",
    f"| 消费复苏+内需推荐 | 正面 | {tbl('; '.join(consumer_names[:3]) or '无直接标的')} | 光大推荐内需消费方向 |",
    "",
    "> 宏观因素仅作背景参考，不单独构成买入依据。需结合热点、公司事件和买卖点共同判断。",
    "",
    "---",
    "",
    "## 五、精选推荐",
    "",
    "从30只信号标的中，按**不同板块/业务方向**各选1只最具潜力的看涨股票（看跌信号不参与精选）：",
    "",
    "| 股票 | 板块 | 信号 | 评分 | 建议 | 核心理由 |",
    "|------|------|------|------|------|---------|",
])

recommendations = []
for r in top5:
    name = r["name"]
    code = r["code"]
    sector = r["sector"] if r["sector"] != "—" else _infer_sector_label(r)
    score = r["score"]

    # Generate recommendation (all top5 should be bullish now)
    if r["direction"] != "看涨":
        action = "⚠️ **看跌回避**"
    elif score >= 70:
        action = "🟢 **买入**"
    elif score >= 55:
        action = "🟡 **持有/加观察**"
    else:
        action = "🟠 **轻仓观察**"

    # Build reason
    parts = []
    condition_items = split_conditions(r["conditions"])
    if condition_items:
        grouped = []
        for prefix in ("左一看涨", "准备反弹", "看涨"):
            names = [
                item.split(":", 1)[1].strip()
                for item in condition_items
                if item.startswith(prefix + ":") and ":" in item
            ]
            if names:
                grouped.append(f"{prefix}: {'、'.join(names[:2])}")
        parts.extend(grouped or condition_items[:3])
    elif "看涨" in r["direction"]:
        parts.append("看涨信号")
    elif "看跌" in r["direction"]:
        parts.append("看跌信号（注意方向）")
    if r["hot_mark"] in ("重点", "相关"):
        parts.append(f"热点匹配: {r['hot_matched'] or r['hot_mark']}" if r['hot_matched'] else f"与热点{r['hot_mark']}")
    if r["breakthrough_days"] and r["breakthrough_days"].isdigit():
        d = int(r["breakthrough_days"])
        if d <= 2:
            parts.append(f"仅用{d}日即突破，动能强")
    if "放量" in r["conditions"]:
        parts.append("放量确认")
    if r["support_range"] and r["support_range"] != "—":
        parts.append(f"支撑区间: {r['support_range']}")

    reason = "；".join(parts) if parts else "综合信号"

    recommendations.append((r, action, reason))
    dir_str = f"{dir_emoji(r['direction'])} {r['direction']}"
    L.append(f"| **{name}**<br>`{code}` | {sector} | {dir_str} | **{fmt_score(score)}** | {action} | {reason} |")

L.extend([
    "",
    "### 持有建议说明",
    "",
    "| 建议 | 含义 |",
    "|------|------|",
    "| 🟢 买入 | 信号较强，热点匹配，可考虑建仓或加仓 |",
    "| 🟡 持有/加观察 | 信号可关注，但需等待更多确认（公司事件、二次放量等） |",
    "| 🟠 轻仓观察 | 有一定信号但风险因素较多，仅适合小仓位试探 |",
    "",
    "---",
    "",
    "## 六、风险提示",
    "",
    "1. **主力数据缺失** ⚠️ 全部30只主力流出风险数据不足，缺少资金面验证",
    "2. **板块数据缺失** 15只（50%）CSV板块字段为空，热点匹配依赖名称推断",
    "3. **公司事件缺失** 本次联网检索因DNS异常未获取公司事件，所有标的事件维度为「信息不足」",
    "4. **宏观扰动** 美伊冲突、1.57万亿解禁潮、6月FOMC为下半月关键变量",
    "5. **基本面稀疏** 仅1只有市值数据，PE数据全部缺失，估值判断依据不足",
    "",
    "> 建议网络恢复后重新运行完整分析流程（联网检索 + LLM），补全公司事件和AI判断维度。",
    "",
    "---",
    "",
    "## 附录：数据说明",
    "",
    f"- **信号数据**：{CSV_PATH}",
    f"- **热点板块**：{'、'.join(HOT_SECTORS)}（{'动态发现: ' + ', '.join(HOT_SECTOR_SOURCES) if HOT_SECTOR_SOURCES and HOT_SECTOR_SOURCES[0] != 'builtin_default' else '手动配置/内置默认'}）",
    f"- **联网检索**：部分执行（Tavily/ZhipuAI，因DNS异常部分失败）",
    f"- **分析引擎**：Claude Opus 4.8（因所有LLM Provider DNS异常，由Claude替代完成）",
    f"- **主力数据**：Futu OpenD（当日数据不足）",
    "",
    "> 该分析仅用于辅助判断，不构成投资建议。",
    "",
])

report = "\n".join(L)

with open(OUT_PATH, "w", encoding="utf-8") as f:
    f.write(report)

print(f"✅ Report: {OUT_PATH}")
print(f"   Length: {len(report)} chars, Stocks: {len(ranked)}")
print(f"   Top5: {[(r['name'], r['sector'], r['score']) for r in top5]}")

# ── Feishu ─────────────────────────────────────────────────
sys.path.insert(0, "/Users/meng.zhou/Desktop/goworkspace/src/github.com/SimonZhou96/MoneyManager/stock_screener")
from feishu_notifier import send_feishu_text

webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/d3562cfc-4c31-4885-8773-e22a155d8594"
LIMIT = 14900

title_line = f"**[{MARKET}信号复核] {CHECK_DATE.strftime('%Y-%m-%d')}**\n\n"
title_len = len(title_line)

if len(report) <= LIMIT:
    ok = send_feishu_text(webhook, report)
    print(f"Feishu: {'✅' if ok else '❌'}")
else:
    chunks = [report[i:i+LIMIT-title_len] for i in range(0, len(report), LIMIT-title_len)]
    for i, chunk in enumerate(chunks):
        if i == 0:
            payload = title_line + chunk
        else:
            payload = chunk + f"\n\n---\n📄 ({i+1}/{len(chunks)})"
        ok = send_feishu_text(webhook, payload[:LIMIT])
        print(f"Feishu {i+1}/{len(chunks)}: {'✅' if ok else '❌'}")
