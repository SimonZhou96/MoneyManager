#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
英伟达产业链 A 股批量筛选脚本

使用 unified_bullish_top20 规则链对 28 只英伟达上下游 A 股供应链公司
进行完整筛选（技术面 + 宏观面 + AI 分析），输出 CSV 和 Markdown 报告。

用法:
    cd stock_screener
    python run_nvidia_supply_chain.py
"""

import os
import sys
import uuid
from datetime import date

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

from custom_list import CustomListScreeningRunner
from fetch_stock_pools import get_db_config
from market import normalize_market
from web.single_stock import normalize_stock_code

# ──────────────────────────────────────────────────────────
# 英伟达产业链 A 股清单（28 只）
# 台股（2330.TW 等）因系统仅支持 HK/US/A 市场，无法筛选
# ──────────────────────────────────────────────────────────
NVIDIA_A_STOCKS: list[tuple[str, str]] = [
    # ── 上游：芯片封测 ──
    ("002156", "通富微电"),   # Chiplet封装，配套N1X
    ("600584", "长电科技"),   # AI芯片封测导入，XDFOI Chiplet

    # ── 中游：PCB ──
    ("300476", "胜宏科技"),   # 显卡及AI服务器PCB，全球市占约50%
    ("002463", "沪电股份"),   # AI服务器主板龙头，北美份额超80%
    ("002916", "深南电路"),   # 高多层贯通板，适配GB300
    ("603228", "景旺电子"),   # 英伟达概念股

    # ── 中游：光模块与光器件 ──
    ("300308", "中际旭创"),   # 800G/1.6T光模块主力，三认证唯一
    ("300394", "天孚通信"),   # 光引擎封装，Quantum交换机
    ("300502", "新易盛"),     # 1.6T产品送样阶段
    ("300570", "太辰光"),     # 光通信器件

    # ── 中游：散热与电源 ──
    ("002837", "英维克"),     # 液冷CDU冷却单元独家供应
    ("002851", "麦格米特"),   # 数据中心高功率电源模块
    ("300837", "铂科新材"),   # 芯片电感软磁粉芯核心供应商

    # ── 中游：材料与基材 ──
    ("601208", "东材科技"),   # 全球唯一M9碳氢树脂认证，GB300封装树脂独家
    ("605589", "圣泉集团"),   # 国内唯一量产电子级PP
    ("600183", "生益科技"),   # 高频高速覆铜板绝对龙头
    ("603256", "宏和科技"),   # 极薄电子布量产国内第一

    # ── 中游：铜缆连接 ──
    ("002130", "沃尔核材"),   # 通过安费诺间接供货，GB300铜缆份额超60%

    # ── 下游：服务器制造与系统集成 ──
    ("601138", "工业富联"),   # AI服务器主力ODM，独家承接GB300整机组装
    ("000977", "浪潮信息"),   # 国内AI服务器龙头，H20核心客户
    ("603296", "华勤技术"),   # 白牌AI PC主力代工，已完成N1X平台开发
    ("000938", "紫光股份"),   # 新华三AI服务器厂商
    ("603019", "中科曙光"),   # 高性能计算与智算中心

    # ── 生态伙伴 ──
    ("002920", "德赛西威"),   # 英伟达DRIVE平台，智能驾驶域控
    ("300496", "中科创达"),   # Jetson生态合作伙伴
    ("688322", "奥比中光"),   # 3D视觉感知合作
    ("600845", "宝信软件"),   # IDC基础设施
    ("002229", "鸿博股份"),   # 子公司合作共建AI算力中心
]


def build_watchlist() -> list[dict]:
    """将原始代码规范化为 watchlist 格式。"""
    watchlist = []
    for raw_code, name in NVIDIA_A_STOCKS:
        try:
            code = normalize_stock_code("A", raw_code)
            watchlist.append({"code": code, "name": name})
        except ValueError as e:
            print(f"⚠️  代码规范化失败: {raw_code} {name} — {e}")
    return watchlist


def main():
    print("=" * 60)
    print("🔍 英伟达产业链 A 股批量筛选")
    print("=" * 60)

    # ── 1. 构造 watchlist ──
    watchlist = build_watchlist()
    print(f"\n📋 待筛选股票: {len(watchlist)} 只")
    for item in watchlist:
        print(f"   {item['code']}  {item['name']}")

    # ── 2. 构造 job dict ──
    job_id = f"nvidia-supply-chain-{uuid.uuid4().hex[:8]}"
    job = {
        "job_id": job_id,
        "markets": ["A"],
        "timeframe": "1d",
        "chain_key": "unified_bullish_top20",
        "options": {
            "chain_key": "unified_bullish_top20",
            "chain_name": "unified_bullish_top20",
            "watchlist_by_market": {
                "A": watchlist,
            },
        },
    }

    print(f"\n⚙️  规则链: unified_bullish_top20")
    print(f"📐 K线周期: 1d")
    print(f"🆔 任务 ID: {job_id}")

    # ── 3. 运行筛选 ──
    mysql_config = get_db_config()
    today_str = date.today().isoformat()
    csv_base = "logs/nvidia_supply_chain"

    print(f"\n⏳ 开始筛选... (K线获取 → 技术规则 → 宏观评估 → AI分析)")
    print("-" * 60)

    runner = CustomListScreeningRunner(mysql_config)
    result = runner.run(
        job=job,
        csv_base=csv_base,
        today_str=today_str,
        enable_ai_analysis=True,
    )

    # ── 4. 输出结果 ──
    print("\n" + "=" * 60)
    print("✅ 筛选完成")
    print("=" * 60)
    print(f"  市场:     {result.market}")
    print(f"  任务 ID:  {result.task_id}")
    print(f"  总数:     {result.total_count}")
    print(f"  通过:     {len(result.passed)}")

    if result.passed:
        print(f"\n📊 通过筛选的股票:")
        for p in result.passed:
            code = p.get("code", "?")
            name = p.get("name", "")
            entry = p.get("entry_score", "?")
            holding = p.get("holding_score", "?")
            print(f"   {code} {name} | 入场分={entry} | 持有分={holding}")

    if result.csv_paths:
        print(f"\n📁 报告文件:")
        for path in result.csv_paths:
            print(f"   {path}")

    print("\n💡 提示: 台股（2330.TW台积电等约17只）因系统仅支持HK/US/A市场，已排除。")
    print("=" * 60)


if __name__ == "__main__":
    main()
