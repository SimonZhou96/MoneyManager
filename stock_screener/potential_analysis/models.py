#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业潜力分析 — 领域模型

五模块快照对象：
  MacroSnapshot    — 宏观环境
  IndustrySnapshot — 行业景气
  CompanySnapshot  — 企业质量
  ValuationSnapshot— 估值
  TradingSnapshot  — 市场行为

统一证据包：
  EnterprisePotentialEvidencePackage — 五模块聚合
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════════
# 基础因子值
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MacroFactorValue:
    """单个宏观因子的取值"""
    factor_key: str
    factor_name: str
    value: Optional[float] = None
    value_text: Optional[str] = None
    unit: str = ""
    trend: str = ""
    source: str = ""
    status: str = "ok"


# ═══════════════════════════════════════════════════════════════════
# 五模块快照
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MacroSnapshot:
    """宏观环境快照"""

    market: str
    as_of: str = ""
    provider_status: Dict[str, str] = field(default_factory=dict)

    # 利率
    policy_rate: Optional[float] = None
    policy_rate_trend: str = ""
    ten_year_yield: Optional[float] = None
    two_year_yield: Optional[float] = None
    yield_spread: Optional[float] = None
    lpr_1y: Optional[float] = None
    lpr_5y: Optional[float] = None

    # 通胀
    cpi_yoy: Optional[float] = None
    core_cpi_yoy: Optional[float] = None
    cpi_trend: str = ""

    # 经济增长
    pmi_manufacturing: Optional[float] = None
    pmi_services: Optional[float] = None
    gdp_yoy: Optional[float] = None
    industrial_production_yoy: Optional[float] = None

    # 就业
    unemployment_rate: Optional[float] = None

    # 货币
    m2_yoy: Optional[float] = None
    social_financing_yoy: Optional[float] = None
    credit_growth_yoy: Optional[float] = None

    # 汇率/风险
    dxy: Optional[float] = None
    vix: Optional[float] = None

    # 市场指数
    hsi: Optional[float] = None; hsi_change_pct: Optional[float] = None
    sp500: Optional[float] = None; sp500_change_pct: Optional[float] = None
    shanghai_composite: Optional[float] = None; shanghai_composite_change_pct: Optional[float] = None

    # 明细
    factors: List[MacroFactorValue] = field(default_factory=list)
    data_gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        r = {"market": self.market, "as_of": self.as_of,
             "provider_status": dict(self.provider_status),
             "factors": [f.__dict__ for f in self.factors],
             "data_gaps": list(self.data_gaps)}
        for k in ("policy_rate","policy_rate_trend","ten_year_yield","two_year_yield",
                  "yield_spread","lpr_1y","lpr_5y","cpi_yoy","core_cpi_yoy","cpi_trend",
                  "pmi_manufacturing","pmi_services","gdp_yoy","industrial_production_yoy",
                  "unemployment_rate","m2_yoy","social_financing_yoy","credit_growth_yoy",
                  "dxy","vix","hsi","hsi_change_pct","sp500","sp500_change_pct",
                  "shanghai_composite","shanghai_composite_change_pct"):
            r[k] = getattr(self, k)
        return r

    def factor_count(self) -> int:
        return sum(1 for f in self.factors if f.status == "ok" and f.value is not None)

    def coverage_pct(self) -> float:
        t = len(self.factors); return round(self.factor_count()/t*100,1) if t else 0.0


@dataclass
class IndustrySnapshot:
    """行业景气快照"""

    market: str
    code: str = ""
    as_of: str = ""
    provider_status: Dict[str, str] = field(default_factory=dict)

    # 行业归属
    sector: str = ""
    industry: str = ""

    # 行业景气代理指标
    sector_pct_change: Optional[float] = None       # 行业板块近期涨跌幅
    sector_relative_strength: Optional[float] = None # 相对大盘强弱
    hot_sector_tags: List[str] = field(default_factory=list)  # 热点板块标签

    # 定性判断（LLM 或搜索抽取）
    industry_growth_stage: str = ""    # "expansion"/"peak"/"contraction"/"trough"
    policy_support: str = ""           # "strong"/"moderate"/"weak"/"neutral"
    competition_intensity: str = ""    # "high"/"moderate"/"low"

    data_gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market, "code": self.code, "as_of": self.as_of,
            "provider_status": dict(self.provider_status),
            "sector": self.sector, "industry": self.industry,
            "sector_pct_change": self.sector_pct_change,
            "sector_relative_strength": self.sector_relative_strength,
            "hot_sector_tags": list(self.hot_sector_tags),
            "industry_growth_stage": self.industry_growth_stage,
            "policy_support": self.policy_support,
            "competition_intensity": self.competition_intensity,
            "data_gaps": list(self.data_gaps),
        }


@dataclass
class CompanySnapshot:
    """企业质量快照"""

    market: str
    code: str
    name: str = ""
    as_of: str = ""
    provider_status: Dict[str, str] = field(default_factory=dict)

    # 盈利质量
    roic: Optional[float] = None          # ROIC (%)
    roe: Optional[float] = None           # ROE (%)
    gross_margin: Optional[float] = None   # 毛利率 (%)
    net_margin: Optional[float] = None     # 净利率 (%)
    operating_margin: Optional[float] = None  # 营业利润率 (%)

    # 成长性
    revenue_growth: Optional[float] = None   # 收入增速 (%)
    earnings_growth: Optional[float] = None  # 盈利增速 (%)

    # 财务健康
    debt_to_equity: Optional[float] = None    # 负债权益比
    current_ratio: Optional[float] = None     # 流动比率
    free_cash_flow: Optional[float] = None    # 自由现金流

    # 估值基础
    market_cap: Optional[float] = None
    enterprise_value: Optional[float] = None
    ebitda: Optional[float] = None

    # 定性
    moat_tags: List[str] = field(default_factory=list)
    event_impact: str = ""        # LLM增强: "利好"/"偏利好"/"利空"/"偏利空"/""
    news_validation: bool = False # LLM增强: 公司事件是否被热点新闻验证

    data_gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market, "code": self.code, "name": self.name,
            "as_of": self.as_of, "provider_status": dict(self.provider_status),
            "roic": self.roic, "roe": self.roe,
            "gross_margin": self.gross_margin, "net_margin": self.net_margin,
            "operating_margin": self.operating_margin,
            "revenue_growth": self.revenue_growth,
            "earnings_growth": self.earnings_growth,
            "debt_to_equity": self.debt_to_equity,
            "current_ratio": self.current_ratio,
            "free_cash_flow": self.free_cash_flow,
            "market_cap": self.market_cap,
            "enterprise_value": self.enterprise_value,
            "ebitda": self.ebitda,
            "moat_tags": list(self.moat_tags),
            "event_impact": self.event_impact,
            "news_validation": self.news_validation,
            "data_gaps": list(self.data_gaps),
        }


@dataclass
class ValuationSnapshot:
    """估值快照"""

    market: str
    code: str
    as_of: str = ""
    provider_status: Dict[str, str] = field(default_factory=dict)

    pe_trailing: Optional[float] = None      # PE (TTM)
    pe_forward: Optional[float] = None       # PE (forward)
    pb: Optional[float] = None               # PB
    ps: Optional[float] = None               # PS
    peg: Optional[float] = None              # PEG
    ev_ebitda: Optional[float] = None        # EV/EBITDA

    market_cap: Optional[float] = None
    enterprise_value: Optional[float] = None

    data_gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market, "code": self.code, "as_of": self.as_of,
            "provider_status": dict(self.provider_status),
            "pe_trailing": self.pe_trailing, "pe_forward": self.pe_forward,
            "pb": self.pb, "ps": self.ps, "peg": self.peg,
            "ev_ebitda": self.ev_ebitda,
            "market_cap": self.market_cap,
            "enterprise_value": self.enterprise_value,
            "data_gaps": list(self.data_gaps),
        }


@dataclass
class TradingSnapshot:
    """市场行为快照"""

    market: str
    code: str
    as_of: str = ""
    provider_status: Dict[str, str] = field(default_factory=dict)

    current_price: Optional[float] = None
    pct_change: Optional[float] = None       # 最近交易日涨跌幅
    beta: Optional[float] = None              # Beta

    # 趋势
    trend_strength: Optional[float] = None    # 0-100 趋势强度评分
    price_vs_50ma: Optional[float] = None     # 价格 vs 50日均线 (%)
    price_vs_200ma: Optional[float] = None    # 价格 vs 200日均线 (%)

    # 波动率
    volatility_30d: Optional[float] = None    # 30日波动率 (%)

    data_gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market, "code": self.code, "as_of": self.as_of,
            "provider_status": dict(self.provider_status),
            "current_price": self.current_price, "pct_change": self.pct_change,
            "beta": self.beta,
            "trend_strength": self.trend_strength,
            "price_vs_50ma": self.price_vs_50ma,
            "price_vs_200ma": self.price_vs_200ma,
            "volatility_30d": self.volatility_30d,
            "data_gaps": list(self.data_gaps),
        }


# ═══════════════════════════════════════════════════════════════════
# 统一证据包
# ═══════════════════════════════════════════════════════════════════

@dataclass
class EnterprisePotentialEvidencePackage:
    """五模块统一证据包 — 对接 LLM 评分的核心数据结构"""

    market: str
    code: str
    name: str = ""
    as_of: str = ""

    macro: Optional[MacroSnapshot] = None
    industry: Optional[IndustrySnapshot] = None
    company: Optional[CompanySnapshot] = None
    valuation: Optional[ValuationSnapshot] = None
    trading: Optional[TradingSnapshot] = None

    # 五模块可用性
    modules_available: List[str] = field(default_factory=list)
    modules_missing: List[str] = field(default_factory=list)
    data_gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market, "code": self.code, "name": self.name,
            "as_of": self.as_of,
            "macro": self.macro.to_dict() if self.macro else None,
            "industry": self.industry.to_dict() if self.industry else None,
            "company": self.company.to_dict() if self.company else None,
            "valuation": self.valuation.to_dict() if self.valuation else None,
            "trading": self.trading.to_dict() if self.trading else None,
            "modules_available": list(self.modules_available),
            "modules_missing": list(self.modules_missing),
            "data_gaps": list(self.data_gaps),
        }

    def evidence_digest(self) -> str:
        """生成供 LLM 评分使用的文本摘要"""
        parts = [f"## 股票: {self.code} {self.name} ({self.market})\n"]

        if self.macro:
            parts.append("### 宏观环境")
            parts.append(f"- LPR 1Y: {self.macro.lpr_1y}%, 5Y: {self.macro.lpr_5y}%")
            parts.append(f"- CPI: {self.macro.cpi_yoy}%, PMI: {self.macro.pmi_manufacturing}")
            parts.append(f"- GDP: {self.macro.gdp_yoy}%, M2: {self.macro.m2_yoy}%")
            parts.append(f"- VIX: {self.macro.vix}, DXY: {self.macro.dxy}")
            parts.append(f"- 恒指: {self.macro.hsi}, SP500: {self.macro.sp500}\n")

        if self.company:
            parts.append("### 企业质量")
            parts.append(f"- ROIC: {self.company.roic}%, ROE: {self.company.roe}%")
            parts.append(f"- 毛利率: {self.company.gross_margin}%, 净利率: {self.company.net_margin}%")
            parts.append(f"- 收入增速: {self.company.revenue_growth}%, 盈利增速: {self.company.earnings_growth}%")
            parts.append(f"- 负债权益比: {self.company.debt_to_equity}")
            parts.append(f"- FCF: {self.company.free_cash_flow}, 市值: {self.company.market_cap}\n")

        if self.valuation:
            parts.append("### 估值")
            parts.append(f"- PE(TTM): {self.valuation.pe_trailing}, PE(Fwd): {self.valuation.pe_forward}")
            parts.append(f"- PB: {self.valuation.pb}, PEG: {self.valuation.peg}")
            parts.append(f"- EV/EBITDA: {self.valuation.ev_ebitda}\n")

        if self.trading:
            parts.append("### 市场行为")
            parts.append(f"- 价格: {self.trading.current_price}, Beta: {self.trading.beta}")
            parts.append(f"- 涨跌: {self.trading.pct_change}%, 30日波动率: {self.trading.volatility_30d}%")
            parts.append(f"- vs 50MA: {self.trading.price_vs_50ma}%, vs 200MA: {self.trading.price_vs_200ma}%\n")

        if self.data_gaps:
            parts.append(f"### 数据缺口\n- " + "\n- ".join(self.data_gaps))

        return "\n".join(parts)
