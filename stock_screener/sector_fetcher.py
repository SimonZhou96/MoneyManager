#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块数据获取器 - 获取股票所属板块/行业信息

支持数据源：
- AKShare: 港股/美股板块信息
- Futu OpenAPI: 港股/美股板块信息

参考:
- AKShare GitHub: https://github.com/akfamily/akshare
- Futu OpenAPI GitHub: https://github.com/FutunnOpen/py-futu-api
"""

import os
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Any

from market import normalize_market


def _find_column(columns, keywords):
    """在列名中查找包含关键词的列"""
    for col in columns:
        col_str = str(col).lower()
        for kw in keywords:
            if kw.lower() in col_str or kw in str(col):
                return col
    return None


@dataclass
class SectorInfo:
    """板块信息数据类"""
    code: str                          # 股票代码
    sector: Optional[str] = None       # 板块名称
    industry: Optional[str] = None     # 行业名称
    sector_code: Optional[str] = None  # 板块代码
    industry_code: Optional[str] = None  # 行业代码
    source: str = ""                   # 数据来源


class SectorFetcherBase(ABC):
    """板块数据获取器基类"""
    
    @abstractmethod
    def fetch_stock_sector(self, code: str, market: str = "HK") -> Optional[SectorInfo]:
        """
        获取单只股票的板块信息
        
        Args:
            code: 股票代码
            market: 市场（HK/US）
            
        Returns:
            SectorInfo 或 None
        """
        pass
    
    @abstractmethod
    def fetch_sector_stocks(self, sector_code: str, market: str = "HK") -> List[str]:
        """
        获取板块下的所有股票
        
        Args:
            sector_code: 板块代码
            market: 市场
            
        Returns:
            股票代码列表
        """
        pass
    
    @abstractmethod
    def fetch_all_sectors(self, market: str = "HK") -> List[Dict[str, Any]]:
        """
        获取所有板块列表
        
        Args:
            market: 市场
            
        Returns:
            板块信息列表
        """
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """返回数据源名称"""
        pass


class AKShareSectorFetcher(SectorFetcherBase):
    """AKShare 板块数据获取器
    
    使用 AKShare 获取港股/美股的板块信息
    """
    
    def __init__(self):
        try:
            import akshare as ak
            self.ak = ak
        except ImportError:
            raise ImportError("请安装AKShare: pip install akshare")
        
        # 缓存板块数据（减少API调用）
        self._sector_cache: Dict[str, Dict[str, SectorInfo]] = {}
        self._cache_date: Optional[date] = None
    
    def get_name(self) -> str:
        return "AKShare"
    
    def _convert_hk_code(self, code: str) -> str:
        """转换港股代码格式"""
        code = str(code).strip()
        if code.startswith("HK."):
            code = code[3:]
        if code.isdigit():
            return code.zfill(5)
        return code
    
    def _convert_us_code(self, code: str) -> str:
        """转换美股代码格式"""
        code = str(code).strip()
        if code.upper().startswith("US."):
            code = code[3:]
        return code.upper()

    def _convert_a_code(self, code: str) -> str:
        """转换 A 股代码格式：000001.SZ / 600000.SS（用于缓存 key）"""
        code = str(code).strip()
        if "." in code:
            return code
        if code.isdigit() and len(code) == 6:
            return f"{code}.SS" if code.startswith("6") else f"{code}.SZ"
        return code

    def _load_a_sector_cache(self) -> Dict[str, SectorInfo]:
        """加载 A 股板块缓存（来自 stock_zh_a_spot_em 的行业列）"""
        if "A" in self._sector_cache and self._cache_date == date.today():
            return self._sector_cache["A"]
        sector_map = {}
        try:
            if hasattr(self.ak, "stock_zh_a_spot_em"):
                df = self.ak.stock_zh_a_spot_em()
                if df is not None and not df.empty:
                    code_col = _find_column(df.columns, ["代码", "code"])
                    industry_col = _find_column(df.columns, ["行业", "industry", "板块"])
                    if code_col:
                        for _, row in df.iterrows():
                            raw = str(row[code_col]).strip()
                            code = self._convert_a_code(raw)
                            if not code:
                                continue
                            industry = str(row[industry_col]).strip() if industry_col and industry_col in row else None
                            sector_map[code] = SectorInfo(
                                code=code,
                                sector=industry,
                                industry=industry,
                                source="AKShare",
                            )
        except Exception as e:
            print(f"AKShare 获取 A 股板块数据失败: {e}")
        self._sector_cache["A"] = sector_map
        self._cache_date = date.today()
        return sector_map

    def _load_hk_sector_cache(self) -> Dict[str, SectorInfo]:
        """加载港股板块缓存"""
        if "HK" in self._sector_cache and self._cache_date == date.today():
            return self._sector_cache["HK"]
        
        sector_map = {}
        
        # 尝试从 AKShare 获取港股板块数据
        try:
            # 方法1: 获取港股行业板块成份股
            if hasattr(self.ak, 'stock_hk_industry_spot_em'):
                try:
                    # 东方财富港股行业板块
                    df = self.ak.stock_hk_industry_spot_em()
                    if df is not None and not df.empty:
                        # 找到代码列和行业列
                        code_col = None
                        industry_col = None
                        for col in df.columns:
                            col_str = str(col)
                            if '代码' in col_str or 'code' in col_str.lower():
                                code_col = col
                            if '行业' in col_str or 'industry' in col_str.lower():
                                industry_col = col
                        
                        if code_col:
                            for _, row in df.iterrows():
                                code = self._convert_hk_code(str(row[code_col]))
                                industry = str(row[industry_col]) if industry_col else None
                                sector_map[code] = SectorInfo(
                                    code=code,
                                    sector=industry,  # 行业作为板块
                                    industry=industry,
                                    source="AKShare"
                                )
                except Exception:
                    pass
            
            # 方法2: 使用板块成分股接口
            if not sector_map and hasattr(self.ak, 'stock_hk_sector_spot_em'):
                try:
                    # 获取港股板块列表
                    sectors_df = self.ak.stock_hk_sector_spot_em()
                    if sectors_df is not None and not sectors_df.empty:
                        # 遍历每个板块获取成分股
                        sector_col = None
                        for col in sectors_df.columns:
                            if '板块' in str(col) or 'sector' in str(col).lower():
                                sector_col = col
                                break
                        
                        if sector_col:
                            for sector_name in sectors_df[sector_col].unique()[:20]:  # 限制数量
                                try:
                                    stocks_df = self.ak.stock_hk_sector_stock_em(sector=sector_name)
                                    if stocks_df is not None and not stocks_df.empty:
                                        code_col = None
                                        for col in stocks_df.columns:
                                            if '代码' in str(col) or 'code' in str(col).lower():
                                                code_col = col
                                                break
                                        
                                        if code_col:
                                            for _, row in stocks_df.iterrows():
                                                code = self._convert_hk_code(str(row[code_col]))
                                                sector_map[code] = SectorInfo(
                                                    code=code,
                                                    sector=sector_name,
                                                    source="AKShare"
                                                )
                                except Exception:
                                    continue
                except Exception:
                    pass
            
            # 方法3: 使用个股详情接口（作为兜底）
            # 这个方法效率较低，只在需要时使用
            
        except Exception as e:
            print(f"AKShare 获取港股板块数据失败: {e}")
        
        self._sector_cache["HK"] = sector_map
        self._cache_date = date.today()
        return sector_map
    
    def _load_us_sector_cache(self) -> Dict[str, SectorInfo]:
        """加载美股板块缓存"""
        if "US" in self._sector_cache and self._cache_date == date.today():
            return self._sector_cache["US"]
        
        sector_map = {}
        
        try:
            # 尝试获取美股行业分类
            if hasattr(self.ak, 'stock_us_industry_spot_em'):
                try:
                    df = self.ak.stock_us_industry_spot_em()
                    if df is not None and not df.empty:
                        code_col = None
                        industry_col = None
                        for col in df.columns:
                            col_str = str(col)
                            if '代码' in col_str or 'code' in col_str.lower() or 'symbol' in col_str.lower():
                                code_col = col
                            if '行业' in col_str or 'industry' in col_str.lower() or '板块' in col_str:
                                industry_col = col
                        
                        if code_col:
                            for _, row in df.iterrows():
                                code = self._convert_us_code(str(row[code_col]))
                                industry = str(row[industry_col]) if industry_col else None
                                sector_map[code] = SectorInfo(
                                    code=code,
                                    sector=industry,
                                    industry=industry,
                                    source="AKShare"
                                )
                except Exception:
                    pass
            
            # 方法2: 使用美股行情接口获取行业信息
            if not sector_map and hasattr(self.ak, 'stock_us_spot_em'):
                try:
                    df = self.ak.stock_us_spot_em()
                    if df is not None and not df.empty:
                        code_col = None
                        industry_col = None
                        for col in df.columns:
                            col_str = str(col)
                            if '代码' in col_str or 'code' in col_str.lower() or 'symbol' in col_str.lower():
                                code_col = col
                            if '行业' in col_str or 'industry' in col_str.lower():
                                industry_col = col
                        
                        if code_col and industry_col:
                            for _, row in df.iterrows():
                                code = self._convert_us_code(str(row[code_col]))
                                industry = str(row[industry_col]) if row[industry_col] else None
                                if industry:
                                    sector_map[code] = SectorInfo(
                                        code=code,
                                        sector=industry,
                                        industry=industry,
                                        source="AKShare"
                                    )
                except Exception:
                    pass
                    
        except Exception as e:
            print(f"AKShare 获取美股板块数据失败: {e}")
        
        self._sector_cache["US"] = sector_map
        self._cache_date = date.today()
        return sector_map
    
    def fetch_stock_sector(self, code: str, market: str = "HK") -> Optional[SectorInfo]:
        """获取单只股票的板块信息"""
        market = normalize_market(market)
        
        if market == "HK":
            cache = self._load_hk_sector_cache()
            normalized_code = self._convert_hk_code(code)
        elif market == "A":
            cache = self._load_a_sector_cache()
            normalized_code = self._convert_a_code(code)
        else:
            cache = self._load_us_sector_cache()
            normalized_code = self._convert_us_code(code)
        
        return cache.get(normalized_code)

    def fetch_sector_stocks(self, sector_code: str, market: str = "HK") -> List[str]:
        """获取板块下的所有股票"""
        market = normalize_market(market)
        
        if market == "HK":
            cache = self._load_hk_sector_cache()
        elif market == "A":
            cache = self._load_a_sector_cache()
        else:
            cache = self._load_us_sector_cache()
        
        return [
            info.code for info in cache.values()
            if info.sector == sector_code or info.sector_code == sector_code
        ]

    def fetch_all_sectors(self, market: str = "HK") -> List[Dict[str, Any]]:
        """获取所有板块列表"""
        market = normalize_market(market)
        
        if market == "HK":
            cache = self._load_hk_sector_cache()
        elif market == "A":
            cache = self._load_a_sector_cache()
        else:
            cache = self._load_us_sector_cache()
        
        # 统计每个板块的股票数量
        sector_counts: Dict[str, int] = {}
        for info in cache.values():
            if info.sector:
                sector_counts[info.sector] = sector_counts.get(info.sector, 0) + 1
        
        return [
            {"sector": sector, "count": count}
            for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1])
        ]


class FutuSectorFetcher(SectorFetcherBase):
    """Futu OpenAPI 板块数据获取器
    
    使用 Futu OpenAPI 获取港股/美股的板块信息
    参考: https://github.com/FutunnOpen/py-futu-api
    """
    
    def __init__(self, quote_ctx):
        """
        初始化 Futu 板块获取器
        
        Args:
            quote_ctx: Futu OpenQuoteContext 对象
        """
        try:
            import futu as ft
            self.ft = ft
        except ImportError:
            raise ImportError("请安装futu-api: pip install futu-api")
        
        self.quote_ctx = quote_ctx
        self._sector_cache: Dict[str, Dict[str, SectorInfo]] = {}
        self._cache_date: Optional[date] = None
    
    def get_name(self) -> str:
        return "FutuOpenAPI"
    
    def _normalize_code(self, code: str, market: str) -> str:
        """标准化股票代码（Futu 格式：HK.00700, US.AAPL, SH.600000, SZ.000001）"""
        code = str(code).strip()
        market = market.upper()
        
        if market == "HK":
            if code.startswith("HK."):
                return code
            if code.isdigit():
                return f"HK.{code.zfill(5)}"
        elif market == "US":
            if code.startswith("US."):
                return code
            return f"US.{code}"
        elif market == "A":
            if code.endswith(".SS"):
                return f"SH.{code[:-3]}"
            if code.endswith(".SZ"):
                return f"SZ.{code[:-3]}"
            if code.isdigit() and len(code) == 6:
                return f"SH.{code}" if code.startswith("6") else f"SZ.{code}"
        
        return code

    def _our_code_from_futu(self, futu_code: str, market: str) -> str:
        """将 Futu 代码转为内部格式（000001.SZ / 600000.SS）"""
        if market != "A":
            return futu_code
        if futu_code.startswith("SH."):
            return f"{futu_code[3:]}.SS"
        if futu_code.startswith("SZ."):
            return f"{futu_code[3:]}.SZ"
        return futu_code

    def _load_plate_cache(self, market: str) -> Dict[str, SectorInfo]:
        """加载板块缓存"""
        if market in self._sector_cache and self._cache_date == date.today():
            return self._sector_cache[market]
        
        sector_map = {}
        
        try:
            market_map = {"US": self.ft.Market.US, "HK": self.ft.Market.HK, "A": self.ft.Market.CN}
            market_enum = market_map.get(market, self.ft.Market.HK)
            
            # 获取行业板块列表
            ret, plate_list = self.quote_ctx.get_plate_list(market_enum, self.ft.Plate.INDUSTRY)
            
            if ret == self.ft.RET_OK and plate_list is not None:
                for _, plate in plate_list.iterrows():
                    plate_code = plate.get('code', '')
                    plate_name = plate.get('plate_name', '')
                    
                    if not plate_code:
                        continue
                    
                    # 获取板块成分股
                    ret2, stock_list = self.quote_ctx.get_plate_stock(plate_code)
                    
                    if ret2 == self.ft.RET_OK and stock_list is not None:
                        for _, stock in stock_list.iterrows():
                            stock_code = stock.get('code', '')
                            if stock_code:
                                our_code = self._our_code_from_futu(stock_code, market)
                                sector_map[stock_code] = SectorInfo(
                                    code=our_code,
                                    sector=plate_name,
                                    sector_code=plate_code,
                                    industry=plate_name,
                                    industry_code=plate_code,
                                    source="FutuOpenAPI"
                                )
                    
                    # 限流：避免过于频繁的请求
                    time.sleep(0.1)
            
        except Exception as e:
            print(f"Futu 获取板块数据失败: {e}")
        
        self._sector_cache[market] = sector_map
        self._cache_date = date.today()
        return sector_map
    
    def fetch_stock_sector(self, code: str, market: str = "HK") -> Optional[SectorInfo]:
        """获取单只股票的板块信息"""
        market = normalize_market(market)
        normalized_code = self._normalize_code(code, market)
        
        cache = self._load_plate_cache(market)
        
        # 尝试直接匹配
        if normalized_code in cache:
            return cache[normalized_code]
        
        # 尝试使用Futu API直接查询
        try:
            market_enum = self.ft.Market.CN if market == "A" else (self.ft.Market.US if market == "US" else self.ft.Market.HK)
            ret, data = self.quote_ctx.get_stock_basicinfo(
                market=market_enum,
                stock_type=self.ft.SecurityType.STOCK,
                code_list=[normalized_code]
            )
            
            if ret == self.ft.RET_OK and data is not None and not data.empty:
                row = data.iloc[0]
                industry = row.get('industry', '')
                if industry:
                    our_code = self._our_code_from_futu(normalized_code, market)
                    return SectorInfo(
                        code=our_code,
                        sector=industry,
                        industry=industry,
                        source="FutuOpenAPI"
                    )
        except Exception:
            pass
        
        return None
    
    def fetch_sector_stocks(self, sector_code: str, market: str = "HK") -> List[str]:
        """获取板块下的所有股票"""
        market = normalize_market(market)
        
        try:
            ret, stock_list = self.quote_ctx.get_plate_stock(sector_code)
            
            if ret == self.ft.RET_OK and stock_list is not None:
                return stock_list['code'].tolist()
        except Exception:
            pass
        
        return []
    
    def fetch_all_sectors(self, market: str = "HK") -> List[Dict[str, Any]]:
        """获取所有板块列表"""
        market = normalize_market(market)
        
        try:
            market_map = {"US": self.ft.Market.US, "HK": self.ft.Market.HK, "A": self.ft.Market.CN}
            market_enum = market_map.get(market, self.ft.Market.HK)
            ret, plate_list = self.quote_ctx.get_plate_list(market_enum, self.ft.Plate.INDUSTRY)
            
            if ret == self.ft.RET_OK and plate_list is not None:
                return plate_list.to_dict('records')
        except Exception:
            pass
        
        return []


class SectorDataManager:
    """板块数据管理器
    
    统一管理板块数据获取，支持多数据源 fallback 和缓存
    """
    
    def __init__(self, cache_dir: str = "cache/sector_data", quote_ctx=None):
        """
        初始化板块数据管理器
        
        Args:
            cache_dir: 缓存目录
            quote_ctx: Futu OpenQuoteContext（可选）
        """
        self.cache_dir = cache_dir
        self.fetchers: List[SectorFetcherBase] = []
        
        # 创建缓存目录
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        
        # 初始化获取器（优先 AKShare）
        try:
            self.fetchers.append(AKShareSectorFetcher())
        except ImportError:
            pass
        
        if quote_ctx:
            try:
                self.fetchers.append(FutuSectorFetcher(quote_ctx))
            except ImportError:
                pass
    
    def set_futu_context(self, quote_ctx):
        """设置 Futu 上下文"""
        # 移除旧的 Futu 获取器
        self.fetchers = [f for f in self.fetchers if not isinstance(f, FutuSectorFetcher)]
        
        if quote_ctx:
            try:
                self.fetchers.append(FutuSectorFetcher(quote_ctx))
            except ImportError:
                pass
    
    def _get_cache_file(self, market: str) -> str:
        """获取缓存文件路径"""
        return os.path.join(self.cache_dir, f"{market.lower()}_sectors.json")
    
    def _load_cache(self, market: str) -> Dict[str, SectorInfo]:
        """从缓存加载板块数据"""
        cache_file = self._get_cache_file(market)
        if not os.path.exists(cache_file):
            return {}
        
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 检查缓存日期
            cache_date = data.get('date', '')
            if cache_date != date.today().isoformat():
                return {}
            
            sectors = {}
            for code, info in data.get('sectors', {}).items():
                sectors[code] = SectorInfo(
                    code=info.get('code', code),
                    sector=info.get('sector'),
                    industry=info.get('industry'),
                    sector_code=info.get('sector_code'),
                    industry_code=info.get('industry_code'),
                    source=info.get('source', 'cache')
                )
            return sectors
        except Exception:
            return {}
    
    def _save_cache(self, market: str, sectors: Dict[str, SectorInfo]):
        """保存板块数据到缓存"""
        cache_file = self._get_cache_file(market)
        
        try:
            data = {
                'date': date.today().isoformat(),
                'sectors': {
                    code: {
                        'code': info.code,
                        'sector': info.sector,
                        'industry': info.industry,
                        'sector_code': info.sector_code,
                        'industry_code': info.industry_code,
                        'source': info.source
                    }
                    for code, info in sectors.items()
                }
            }
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    def get_stock_sector(
        self,
        code: str,
        market: str = "HK",
        use_cache: bool = True
    ) -> Optional[SectorInfo]:
        """
        获取单只股票的板块信息
        
        Args:
            code: 股票代码
            market: 市场
            use_cache: 是否使用缓存
            
        Returns:
            SectorInfo 或 None
        """
        market = normalize_market(market)
        
        # 先尝试缓存
        if use_cache:
            cache = self._load_cache(market)
            if code in cache:
                return cache[code]
        
        # 按优先级尝试各个数据源
        for fetcher in self.fetchers:
            try:
                info = fetcher.fetch_stock_sector(code, market)
                if info:
                    # 更新缓存
                    cache = self._load_cache(market)
                    cache[code] = info
                    self._save_cache(market, cache)
                    return info
            except Exception:
                continue
        
        return None
    
    def get_batch_stock_sectors(
        self,
        codes: List[str],
        market: str = "HK",
        use_cache: bool = True,
        verbose: bool = False
    ) -> Dict[str, SectorInfo]:
        """
        批量获取股票板块信息
        
        Args:
            codes: 股票代码列表
            market: 市场
            use_cache: 是否使用缓存
            verbose: 是否输出详细日志
            
        Returns:
            Dict[code, SectorInfo]
        """
        market = normalize_market(market)
        result = {}
        
        # 先从缓存获取
        if use_cache:
            cache = self._load_cache(market)
            for code in codes:
                if code in cache:
                    result[code] = cache[code]
        
        # 获取缺失的
        missing_codes = [code for code in codes if code not in result]
        
        if missing_codes and verbose:
            print(f"📡 正在获取 {len(missing_codes)} 只股票的板块信息...")
        
        for fetcher in self.fetchers:
            if not missing_codes:
                break
            
            try:
                # 对于 AKShare，加载完整缓存后批量查询
                if isinstance(fetcher, AKShareSectorFetcher):
                    # 预加载缓存
                    if market == "HK":
                        ak_cache = fetcher._load_hk_sector_cache()
                        normalizer = fetcher._convert_hk_code
                    elif market == "A":
                        ak_cache = fetcher._load_a_sector_cache()
                        normalizer = fetcher._convert_a_code
                    else:
                        ak_cache = fetcher._load_us_sector_cache()
                        normalizer = fetcher._convert_us_code
                    
                    for code in list(missing_codes):
                        normalized = normalizer(code)
                        if normalized in ak_cache:
                            result[code] = ak_cache[normalized]
                            missing_codes.remove(code)
                else:
                    # 逐个获取
                    for code in list(missing_codes):
                        info = fetcher.fetch_stock_sector(code, market)
                        if info:
                            result[code] = info
                            missing_codes.remove(code)
                        
                        # 限流
                        time.sleep(0.05)
            except Exception as e:
                if verbose:
                    print(f"  ⚠️ {fetcher.get_name()} 获取失败: {e}")
                continue
        
        # 更新缓存
        if use_cache and result:
            cache = self._load_cache(market)
            cache.update(result)
            self._save_cache(market, cache)
        
        if verbose:
            print(f"✓ 获取到 {len(result)}/{len(codes)} 只股票的板块信息")
        
        return result
    
    def get_all_sectors(self, market: str = "HK") -> List[Dict[str, Any]]:
        """获取所有板块列表"""
        market = normalize_market(market)
        
        for fetcher in self.fetchers:
            try:
                sectors = fetcher.fetch_all_sectors(market)
                if sectors:
                    return sectors
            except Exception:
                continue
        
        return []
