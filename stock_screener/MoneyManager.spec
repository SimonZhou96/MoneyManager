# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 将 MoneyManager 打包为 macOS .app / Windows .exe"""

import sys
from pathlib import Path

_block_cipher = None

# 项目根
PROJ = Path(__file__).resolve().parent

# 需打入的数据文件
_datas = []

# .env
_env = PROJ / ".env"
if _env.exists():
    _datas.append((str(_env), "."))

# 前端 dist
_dist = PROJ / "web_frontend" / "dist"
if _dist.exists():
    # 递归收集 dist 下所有文件
    _datas.append((str(_dist), "web_frontend/dist"))

_icon = str(PROJ / "web_frontend" / "MoneyManager_icon.icns") if sys.platform == "darwin" else None
if _icon and not Path(_icon).exists():
    _icon = None

a = Analysis(
    [str(PROJ / "desktop.py")],
    pathex=[str(PROJ)],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        "web",
        "web.main",
        "web.auth",
        "web.config",
        "web.business",
        "web.errors",
        "web.market_intel",
        "web.options",
        "web.quant",
        "web.sectors",
        "web.stock_terminal",
        "web.single_stock",
        "web.rule_chains",
        "web.rate_limit",
        "web.validation",
        "web.jobs",
        "web.worker",
        "web.queue",
        "api.screen_service",
        "api",
        "signal_analysis",
        "signal_analysis.chain",
        "signal_analysis.factories",
        "signal_analysis.hot_sectors",
        "signal_analysis.llm_providers",
        "signal_analysis.models",
        "signal_analysis.search_providers",
        "signal_analysis.service",
        "signal_analysis.sentiment",
        "signal_analysis.browser_search_providers",
        "market_intel",
        "market_intel.macro_scoring",
        "market_intel.reporting",
        "market_intel.evidence",
        "market_intel.providers",
        "market_intel.providers.factory",
        "market_intel.providers.eastmoney",
        "market_intel.providers.source_registry",
        "market_intel.repository",
        "market_intel.service",
        "potential_analysis",
        "potential_analysis.service",
        "potential_analysis.providers",
        "potential_analysis.builders",
        "quant_lab",
        "quant_lab.service",
        "quant_lab.paper",
        "kline_fetcher",
        "sector_resolver",
        "main_force_risk",
        "rule_engine",
        "db",
        "filters",
        "stock_pool",
        "market",
        "timeframe",
        "stock_name_resolver",
        "custom_list",
        "feishu_notifier",
        "feishu_app_client",
        "strategy",
        "strategizers",
        "macro_strategies",
        "screening_defaults",
        "screening_config_store",
        "screening_prompt_flow",
        "universe",
        "universe_filter",
        "report_naming",
        "network_preflight",
        "yf_ratelimit",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numba", "scipy", "PIL"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=_block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=_block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MoneyManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False if sys.platform != "darwin" else False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="MoneyManager.app",
        icon=_icon,
        bundle_identifier="com.moneymanager.stockscreener",
        info_plist={
            "CFBundleName": "MoneyManager",
            "CFBundleDisplayName": "MoneyManager 选股器",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
        },
    )
