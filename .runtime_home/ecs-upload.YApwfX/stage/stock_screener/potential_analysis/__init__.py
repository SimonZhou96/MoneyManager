#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业潜力分析模块 — 宏观/行业/企业/估值/交易 五模块评分 + 批量编排"""

from .models import (
    CompanySnapshot,
    EnterprisePotentialEvidencePackage,
    IndustrySnapshot,
    MacroFactorValue,
    MacroSnapshot,
    TradingSnapshot,
    ValuationSnapshot,
)
from .providers import (
    ChinaMacroProvider,
    GlobalMacroProvider,
    MacroDataProvider,
    build_default_macro_provider,
)
from .builders import (
    CompanySnapshotBuilder,
    IndustrySnapshotBuilder,
    TradingSnapshotBuilder,
    ValuationSnapshotBuilder,
    build_enterprise_evidence,
)
from .scoring import (
    EnterprisePotentialResult,
    EnterprisePotentialScorer,
)
from .service import (
    BatchCompanyFetcher,
    BatchIndustryFetcher,
    BatchTradingFetcher,
    BatchValuationFetcher,
    EnterprisePotentialService,
    PrefetchReport,
)
from .strategizer import (
    EnterprisePotentialAnalysisStrategizer,
    MacroFactorAnalysisStrategizer,
)

__all__ = [
    # models
    "MacroFactorValue", "MacroSnapshot",
    "CompanySnapshot", "ValuationSnapshot",
    "IndustrySnapshot", "TradingSnapshot",
    "EnterprisePotentialEvidencePackage",
    # providers
    "MacroDataProvider", "ChinaMacroProvider", "GlobalMacroProvider",
    "build_default_macro_provider",
    # builders
    "CompanySnapshotBuilder", "ValuationSnapshotBuilder",
    "IndustrySnapshotBuilder", "TradingSnapshotBuilder",
    "build_enterprise_evidence",
    # scoring
    "EnterprisePotentialScorer", "EnterprisePotentialResult",
    # service (batch)
    "EnterprisePotentialService", "PrefetchReport",
    "BatchCompanyFetcher", "BatchValuationFetcher",
    "BatchTradingFetcher", "BatchIndustryFetcher",
    # strategizers
    "MacroFactorAnalysisStrategizer",
    "EnterprisePotentialAnalysisStrategizer",
]
