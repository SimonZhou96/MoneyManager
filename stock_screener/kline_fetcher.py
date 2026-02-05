#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K线数据获取器 - 支持多数据源fallback和缓存
使用工厂模式抽象数据源，优先AKShare，失败时使用FutuOpenAPI
"""

import pandas as pd
import os
import json
from datetime import date, datetime, timedelta
from abc import ABC, abstractmethod
from typing import Optional, List, Dict
import time
import random


class KlineFetcherBase(ABC):
    """K线数据获取器基类"""
    
    @abstractmethod
    def fetch(self, stock_code: str, market: str = "HK", start_date: Optional[str] = None, 
              end_date: Optional[str] = None, max_count: int = 800) -> Optional[pd.DataFrame]:
        """
        获取K线数据
        
        Args:
            stock_code: 股票代码
            market: 市场（HK/US）
            start_date: 开始日期 (str, format: 'YYYY-MM-DD')
            end_date: 结束日期 (str, format: 'YYYY-MM-DD')
            max_count: 最大数据量
            
        Returns:
            pd.DataFrame or None
        """
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """返回数据源名称"""
        pass


class AKShareKlineFetcher(KlineFetcherBase):
    """AKShare K线数据获取器 - 支持多个接口兜底"""
    
    def __init__(self):
        try:
            import akshare as ak
            self.ak = ak
        except ImportError:
            raise ImportError("请安装AKShare: pip install akshare")
    
    def get_name(self) -> str:
        return "AKShare"
    
    def _convert_hk_code(self, stock_code: str) -> str:
        """转换股票代码格式：HK.00700 -> 00700"""
        if stock_code.startswith('HK.'):
            stock_code = stock_code[3:]
        stock_code = str(stock_code)
        if stock_code.isdigit():
            return stock_code.zfill(5)
        return stock_code

    def _convert_us_code(self, stock_code: str) -> str:
        """转换美股代码格式：US.AAPL -> AAPL"""
        code = str(stock_code).strip()
        if code.upper().startswith('US.'):
            return code[3:]
        return code
    
    def _normalize_date_format(self, date_str: Optional[str]) -> Optional[str]:
        """转换日期格式：YYYY-MM-DD -> YYYYMMDD"""
        if date_str is None:
            return None
        try:
            return pd.to_datetime(date_str).strftime('%Y%m%d')
        except Exception:
            return date_str.replace('-', '')
    
    def _normalize_dataframe(self, df: pd.DataFrame, start_date: Optional[str] = None, 
                             end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """标准化DataFrame格式"""
        if df is None or df.empty:
            return None
        
        # 统一列名映射（更全面的映射）
        column_mapping = {
            '日期': 'date', '时间': 'date', 'time_key': 'date', 'time': 'date',
            '开盘': 'open', '收盘': 'close', '最高': 'high', '最低': 'low',
            '开盘价': 'open', '收盘价': 'close', '最高价': 'high', '最低价': 'low',
            '成交量': 'volume', '成交额': 'turnover', '换手率': 'turnover_rate',
            '涨跌幅': 'change_rate', '振幅': 'amplitude',
            '成交额(元)': 'turnover', '成交额(港元)': 'turnover',
        }
        
        # 重命名列
        for old_col, new_col in column_mapping.items():
            if old_col in df.columns:
                df = df.rename(columns={old_col: new_col})
        
        # 找到日期列（更全面的搜索）
        date_col = None
        for col in df.columns:
            col_lower = str(col).lower()
            if ('date' in col_lower or '日期' in str(col) or '时间' in str(col) or 
                col_lower == 'day' or col_lower == 'time' or col_lower == 'datetime'):
                date_col = col
                break
        
        if date_col and date_col != 'date':
            df = df.rename(columns={date_col: 'date'})
        
        if 'date' not in df.columns:
            return None
        
        # 确保date是datetime类型
        try:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            # 删除无效日期
            df = df[df['date'].notna()]
            if df.empty:
                return None
        except Exception:
            return None
        
        # 过滤日期范围
        if start_date or end_date:
            try:
                start_dt = pd.to_datetime(start_date) if start_date else None
                end_dt = pd.to_datetime(end_date) if end_date else None
                
                if start_dt is not None:
                    df = df[df['date'] >= start_dt]
                if end_dt is not None:
                    df = df[df['date'] <= end_dt]
            except Exception:
                pass
        
        # 确保有必要的OHLC列
        required_cols = ['open', 'high', 'low', 'close']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            return None
        
        # 排序并重置索引
        df = df.sort_values('date').reset_index(drop=True)
        
        return df
    
    def _try_hk_methods(self, hk_code: str, start_date: str, end_date: str, max_retries: int = 2) -> Optional[pd.DataFrame]:
        """尝试多个港股接口，带重试机制"""
        methods = [
            # 方法1: stock_hk_daily (Sina，稳定)
            {
                'name': 'stock_hk_daily',
                'func': lambda: self.ak.stock_hk_daily(symbol=hk_code, adjust="qfq"),
                'needs_filter': True,
                'retry_delay': 1,
            },
            # 方法2: stock_hk_hist (东方财富)
            {
                'name': 'stock_hk_hist',
                'func': lambda: self.ak.stock_hk_hist(
                    symbol=hk_code, period="daily", start_date=start_date, 
                    end_date=end_date, adjust="qfq"
                ),
                'needs_filter': False,
                'retry_delay': 2,
            },
            # 方法3: stock_hk_hist_em (东方财富，另一个接口)
            {
                'name': 'stock_hk_hist_em',
                'func': lambda: self.ak.stock_hk_hist_em(
                    symbol=hk_code, start_date=start_date.replace('-', ''), 
                    end_date=end_date.replace('-', ''), adjust="qfq"
                ) if hasattr(self.ak, 'stock_hk_hist_em') else None,
                'needs_filter': False,
                'retry_delay': 2,
            },
            # 方法4: stock_zh_ah_daily (A+H股) - 修复列名问题
            {
                'name': 'stock_zh_ah_daily',
                'func': lambda: self.ak.stock_zh_ah_daily(
                    symbol=hk_code,
                    start_year=str(pd.to_datetime(start_date).year),
                    end_year=str(pd.to_datetime(end_date).year),
                    adjust="qfq"
                ),
                'needs_filter': False,
                'retry_delay': 2,
            },
            # 方法5: stock_hk_spot_em (东方财富实时行情，可能包含历史)
            {
                'name': 'stock_hk_spot_em',
                'func': lambda: self.ak.stock_hk_spot_em(),
                'needs_filter': True,
                'retry_delay': 1,
            },
            # 方法6: stock_hk_hist_min_em (分钟线，作为最后兜底)
            {
                'name': 'stock_hk_hist_min_em',
                'func': lambda: self.ak.stock_hk_hist_min_em(
                    symbol=hk_code, period="1", adjust="qfq", start_date=start_date, end_date=end_date
                ) if hasattr(self.ak, 'stock_hk_hist_min_em') else None,
                'needs_filter': False,
                'retry_delay': 3,
            },
        ]
        
        for method in methods:
            if not hasattr(self.ak, method['name']):
                continue
            
            # 重试机制
            for retry in range(max_retries):
                try:
                    # 添加随机延迟，避免频繁请求
                    if retry > 0:
                        delay = method.get('retry_delay', 1) + random.uniform(0, 1)
                        time.sleep(delay)
                    
                    data = method['func']()
                    if data is not None and len(data) > 0:
                        # 如果是spot接口，需要过滤特定股票
                        if method['name'] == 'stock_hk_spot_em':
                            code_col = None
                            for col in data.columns:
                                if '代码' in col or 'code' in col.lower() or 'symbol' in col.lower():
                                    code_col = col
                                    break
                            if code_col:
                                data = data[data[code_col].astype(str).str.zfill(5) == hk_code.zfill(5)]
                                if data.empty:
                                    continue
                        
                        normalized = self._normalize_dataframe(data, start_date, end_date)
                        if normalized is not None and len(normalized) > 0:
                            return normalized
                    
                    # 如果成功但没有数据，不重试
                    break
                    
                except Exception as e:
                    error_msg = str(e)
                    # 如果是最后一次重试，打印错误
                    if retry == max_retries - 1:
                        print(f"✗ 获取{hk_code}失败，method:{method['name']}, error:{error_msg[:100]}")
                    # 如果是连接错误，继续重试
                    if 'Connection' in error_msg or 'Remote' in error_msg or 'timeout' in error_msg.lower():
                        continue
                    # 其他错误，不重试
                    break
            
            # 方法之间添加延迟，避免频繁请求
            time.sleep(0.5 + random.uniform(0, 0.5))
        
        return None
    
    def _try_us_methods(self, us_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """尝试多个美股接口"""
        methods = [
            # 方法1: stock_us_daily (Sina)
            {
                'name': 'stock_us_daily',
                'func': lambda: self.ak.stock_us_daily(symbol=us_code, adjust="qfq"),
                'needs_filter': True,
            },
            # 方法2: stock_us_hist (东方财富)
            {
                'name': 'stock_us_hist',
                'func': lambda: self.ak.stock_us_hist(
                    symbol=us_code, period="daily", start_date=start_date,
                    end_date=end_date, adjust="qfq"
                ),
                'needs_filter': False,
            },
            # 方法3: stock_us_spot_em (东方财富实时行情)
            {
                'name': 'stock_us_spot_em',
                'func': lambda: self.ak.stock_us_spot_em(),
                'needs_filter': True,
            },
        ]
        
        for method in methods:
            if not hasattr(self.ak, method['name']):
                continue
            
            try:
                data = method['func']()
                if data is not None and len(data) > 0:
                    # 如果是spot接口，需要过滤特定股票
                    if method['name'] == 'stock_us_spot_em':
                        code_col = None
                        for col in data.columns:
                            if '代码' in col or 'code' in col.lower() or 'symbol' in col.lower():
                                code_col = col
                                break
                        if code_col:
                            data = data[data[code_col].astype(str).str.upper() == us_code.upper()]
                            if data.empty:
                                continue
                    
                    normalized = self._normalize_dataframe(data, start_date, end_date)
                    if normalized is not None and len(normalized) > 0:
                        return normalized
            except Exception as e:
                continue
        
        return None
    
    def fetch(self, stock_code: str, market: str = "HK", start_date: Optional[str] = None,
              end_date: Optional[str] = None, max_count: int = 800) -> Optional[pd.DataFrame]:
        """使用AKShare获取K线数据，支持多个接口兜底"""
        try:
            market = str(market).upper()
            
            # 转换日期格式
            start_date_str = self._normalize_date_format(start_date)
            end_date_str = self._normalize_date_format(end_date)
            
            # 如果没有指定日期，使用默认值
            if end_date_str is None:
                end_date_str = date.today().strftime('%Y%m%d')
            if start_date_str is None:
                start_date_str = (date.today() - timedelta(days=800)).strftime('%Y%m%d')
            
            data = None
            
            if market == "HK":
                hk_code = self._convert_hk_code(stock_code)
                data = self._try_hk_methods(hk_code, start_date_str, end_date_str)
            else:
                us_code = self._convert_us_code(stock_code)
                data = self._try_us_methods(us_code, start_date_str, end_date_str)
            
            if data is None or data.empty:
                return None
            
            # 限制数据量
            if len(data) > max_count:
                data = data.tail(max_count).reset_index(drop=True)
            
            return data
            
        except Exception as e:
            return None


class YFinanceKlineFetcher(KlineFetcherBase):
    """YFinance K线数据获取器 - 支持港股和美股"""
    
    def __init__(self):
        try:
            import yfinance as yf
            self.yf = yf
        except ImportError:
            raise ImportError("请安装yfinance: pip install yfinance")
    
    def get_name(self) -> str:
        return "YFinance"
    
    def _convert_hk_code(self, stock_code: str) -> str:
        """转换港股代码格式：HK.00700 -> 00700.HK"""
        if stock_code.startswith('HK.'):
            stock_code = stock_code[3:]
        stock_code = str(stock_code).zfill(5)
        return f"{stock_code}.HK"
    
    def _convert_us_code(self, stock_code: str) -> str:
        """转换美股代码格式：US.AAPL -> AAPL"""
        if stock_code.upper().startswith('US.'):
            return stock_code[3:]
        return stock_code
    
    def fetch(self, stock_code: str, market: str = "HK", start_date: Optional[str] = None,
              end_date: Optional[str] = None, max_count: int = 800) -> Optional[pd.DataFrame]:
        """使用YFinance获取K线数据"""
        try:
            market = str(market).upper()
            
            # 转换股票代码
            if market == "HK":
                yf_code = self._convert_hk_code(stock_code)
            else:
                yf_code = self._convert_us_code(stock_code)
            
            # 创建ticker对象
            ticker = self.yf.Ticker(yf_code)
            
            # 获取历史数据
            # YFinance的period参数：1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
            # 如果指定了日期范围，使用start和end参数
            try:
                if start_date and end_date:
                    data = ticker.history(start=start_date, end=end_date, auto_adjust=True)
                elif start_date:
                    data = ticker.history(start=start_date, auto_adjust=True)
                else:
                    # 默认获取最近的数据
                    data = ticker.history(period="5y", auto_adjust=True)
            except Exception:
                # 如果失败，尝试不使用auto_adjust
                if start_date and end_date:
                    data = ticker.history(start=start_date, end=end_date)
                elif start_date:
                    data = ticker.history(start=start_date)
                else:
                    data = ticker.history(period="5y")
            
            if data is None or data.empty:
                return None
            
            # YFinance返回的列名：Open, High, Low, Close, Volume
            # 转换为小写
            data = data.reset_index()
            data.columns = [col.lower() if isinstance(col, str) else col for col in data.columns]
            
            # 重命名列
            column_mapping = {
                'date': 'date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'volume',
            }
            
            for old_col, new_col in column_mapping.items():
                if old_col in data.columns:
                    data = data.rename(columns={old_col: new_col})
            
            # 确保有date列
            if 'date' not in data.columns and len(data) > 0:
                # YFinance通常使用index作为日期
                if data.index.name == 'Date' or isinstance(data.index, pd.DatetimeIndex):
                    data = data.reset_index()
                    if 'Date' in data.columns:
                        data = data.rename(columns={'Date': 'date'})
            
            # 标准化
            normalized = self._normalize_dataframe(data, start_date, end_date)
            if normalized is None or normalized.empty:
                return None
            
            # 限制数据量
            if len(normalized) > max_count:
                normalized = normalized.tail(max_count).reset_index(drop=True)
            
            return normalized
            
        except Exception as e:
            return None
    
    def _normalize_dataframe(self, df: pd.DataFrame, start_date: Optional[str] = None,
                             end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """标准化DataFrame格式（复用AKShare的逻辑）"""
        if df is None or df.empty:
            return None
        
        # 确保date是datetime类型
        if 'date' in df.columns:
            try:
                df['date'] = pd.to_datetime(df['date'], errors='coerce')
                df = df[df['date'].notna()]
                if df.empty:
                    return None
            except Exception:
                return None
        else:
            # 如果没有date列，尝试从index获取
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index()
                if 'Date' in df.columns:
                    df = df.rename(columns={'Date': 'date'})
                elif df.index.name == 'Date':
                    df['date'] = df.index
                    df = df.reset_index(drop=True)
        
        # 确保有必要的OHLC列
        required_cols = ['open', 'high', 'low', 'close']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            return None
        
        # 过滤日期范围
        if start_date or end_date:
            try:
                start_dt = pd.to_datetime(start_date) if start_date else None
                end_dt = pd.to_datetime(end_date) if end_date else None
                
                if start_dt is not None:
                    df = df[df['date'] >= start_dt]
                if end_dt is not None:
                    df = df[df['date'] <= end_dt]
            except Exception:
                pass
        
        # 排序并重置索引
        df = df.sort_values('date').reset_index(drop=True)
        
        return df


class FutuKlineFetcher(KlineFetcherBase):
    """富途API K线数据获取器"""
    
    def __init__(self, quote_ctx, rate_limiter=None):
        self.quote_ctx = quote_ctx
        self.rate_limiter = rate_limiter
    
    def get_name(self) -> str:
        return "FutuOpenAPI"
    
    def _normalize_futu_code(self, stock_code: str, market: str) -> str:
        """标准化富途股票代码格式"""
        market = str(market).upper()
        code = str(stock_code).strip()
        if market == "HK":
            if code.startswith("HK."):
                return code
            if code.isdigit():
                return f"HK.{code.zfill(5)}"
        elif market == "US":
            if code.startswith("US."):
                return code
            return f"US.{code}"
        return code

    def fetch(self, stock_code: str, market: str = "HK", start_date: Optional[str] = None,
              end_date: Optional[str] = None, max_count: int = 800) -> Optional[pd.DataFrame]:
        """使用富途API获取K线数据"""
        import futu as ft
        
        if self.rate_limiter:
            self.rate_limiter.wait_if_needed()
        
        try:
            code = self._normalize_futu_code(stock_code, market)
            
            # 尝试获取历史K线
            ret, data, page_req_key = self.quote_ctx.request_history_kline(
                code=code,
                ktype=ft.KLType.K_DAY,
                max_count=max_count,
                autype=ft.AuType.QFQ  # 前复权
            )
            
            if ret == ft.RET_OK and data is not None and len(data) > 0:
                # 统一列名
                if 'time_key' in data.columns:
                    data = data.rename(columns={'time_key': 'date'})
                
                # 确保date是datetime类型
                if 'date' in data.columns:
                    data['date'] = pd.to_datetime(data['date'])
                    data = data.sort_values('date').reset_index(drop=True)
                    
                    # 过滤日期范围
                    if start_date:
                        start_dt = pd.to_datetime(start_date)
                        data = data[data['date'] >= start_dt]
                    if end_date:
                        end_dt = pd.to_datetime(end_date)
                        data = data[data['date'] <= end_dt]
                    
                    return data
            
            return None
            
        except Exception as e:
            error_msg = str(e)
            # 检查是否是额度用尽或其他错误
            if 'quota' in error_msg.lower() or '额度' in error_msg or 'too frequent' in error_msg.lower():
                raise Exception(f"Futu API quota exhausted or rate limited: {error_msg}")
            return None


class KlineFetcherFactory:
    """K线数据获取器工厂类"""
    
    @staticmethod
    def create_akshare_fetcher() -> Optional[AKShareKlineFetcher]:
        """创建AKShare获取器"""
        try:
            return AKShareKlineFetcher()
        except ImportError:
            return None
    
    @staticmethod
    def create_yfinance_fetcher() -> Optional[YFinanceKlineFetcher]:
        """创建YFinance获取器"""
        try:
            return YFinanceKlineFetcher()
        except ImportError:
            return None
    
    @staticmethod
    def create_futu_fetcher(quote_ctx, rate_limiter=None) -> Optional[FutuKlineFetcher]:
        """创建富途API获取器"""
        if quote_ctx is None:
            return None
        try:
            return FutuKlineFetcher(quote_ctx, rate_limiter)
        except Exception:
            return None
    
    @staticmethod
    def create_fetcher_chain(quote_ctx=None, rate_limiter=None) -> List[KlineFetcherBase]:
        """
        创建获取器链，按优先级排序
        优先级：AKShare > YFinance > FutuOpenAPI
        """
        fetchers = []
        
        # 1. 优先使用AKShare
        ak_fetcher = KlineFetcherFactory.create_akshare_fetcher()
        if ak_fetcher:
            fetchers.append(ak_fetcher)
        
        # 2. Fallback到YFinance
        yf_fetcher = KlineFetcherFactory.create_yfinance_fetcher()
        if yf_fetcher:
            fetchers.append(yf_fetcher)
        
        # 3. Fallback到富途API
        futu_fetcher = KlineFetcherFactory.create_futu_fetcher(quote_ctx, rate_limiter)
        if futu_fetcher:
            fetchers.append(futu_fetcher)
        
        return fetchers


class KlineDataManager:
    """K线数据管理器 - 支持多数据源fallback和文件缓存"""
    
    def __init__(self, cache_dir='cache/kline_data', quote_ctx=None, rate_limiter=None):
        self.cache_dir = cache_dir
        self.fetchers = KlineFetcherFactory.create_fetcher_chain(quote_ctx, rate_limiter)
        
        # 创建缓存目录
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
    
    def set_futu_context(self, quote_ctx, rate_limiter=None):
        """设置富途API上下文（用于动态更新）"""
        self.fetchers = KlineFetcherFactory.create_fetcher_chain(quote_ctx, rate_limiter)
    
    def _get_cache_file_path(self, stock_code: str, market: str) -> str:
        """获取缓存文件路径"""
        safe_code = stock_code.replace('.', '_').replace('/', '_')
        market_tag = str(market).upper()
        return os.path.join(self.cache_dir, f"{market_tag}_{safe_code}.parquet")
    
    def _load_from_cache(self, stock_code: str, market: str) -> Optional[pd.DataFrame]:
        """从缓存加载K线数据"""
        cache_file = self._get_cache_file_path(stock_code, market)
        csv_file = cache_file.replace('.parquet', '.csv')
        
        file_to_load = None
        if os.path.exists(cache_file):
            file_to_load = cache_file
        elif os.path.exists(csv_file):
            file_to_load = csv_file
        
        if file_to_load is None:
            return None
        
        try:
            if file_to_load.endswith('.parquet'):
                try:
                    data = pd.read_parquet(file_to_load)
                except Exception:
                    if os.path.exists(csv_file):
                        data = pd.read_csv(csv_file, encoding='utf-8')
                    else:
                        return None
            else:
                data = pd.read_csv(file_to_load, encoding='utf-8')
            
            # 检查缓存日期
            if 'date' in data.columns:
                data['date'] = pd.to_datetime(data['date'])
                latest_date = data['date'].max()
                today = date.today()
                
                # 如果最新数据是今天或昨天，使用缓存
                if latest_date.date() >= today - timedelta(days=1):
                    return data
            
            return data
        except Exception as e:
            return None
    
    def _save_to_cache(self, stock_code: str, market: str, data: pd.DataFrame):
        """保存K线数据到缓存"""
        if data is None or len(data) == 0:
            return
        
        cache_file = self._get_cache_file_path(stock_code, market)
        
        try:
            if 'date' in data.columns:
                data['date'] = pd.to_datetime(data['date'])
            
            # 尝试保存为parquet格式
            try:
                data.to_parquet(cache_file, index=False)
            except Exception:
                # 如果parquet失败，使用CSV
                csv_file = cache_file.replace('.parquet', '.csv')
                data.to_csv(csv_file, index=False, encoding='utf-8')
        except Exception:
            pass
    
    def get_kline_data(self, stock_code: str, market: str = "HK", start_date: Optional[str] = None,
                       end_date: Optional[str] = None, max_count: int = 800,
                       use_cache: bool = True, verbose: bool = False) -> Optional[pd.DataFrame]:
        """
        获取K线数据（带缓存和多数据源fallback）
        
        Args:
            stock_code: 股票代码
            market: 市场（HK/US）
            start_date: 开始日期
            end_date: 结束日期
            max_count: 最大数据量
            use_cache: 是否使用缓存
            verbose: 是否输出详细信息
            
        Returns:
            pd.DataFrame or None
        """
        # 1. 尝试从缓存加载
        if use_cache:
            cached_data = self._load_from_cache(stock_code, market)
            if cached_data is not None:
                if verbose:
                    latest_date = cached_data['date'].max().strftime('%Y-%m-%d')
                    print(f"  ✓ {stock_code} 从缓存加载: {len(cached_data)} 条 (最新: {latest_date})")
                return cached_data
        
        # 2. 按优先级尝试各个数据源
        last_error = None
        for fetcher in self.fetchers:
            try:
                if verbose:
                    print(f"  📡 {stock_code} 使用{fetcher.get_name()}获取...")
                
                data = fetcher.fetch(
                    stock_code, market=market, start_date=start_date,
                    end_date=end_date, max_count=max_count
                )
                
                if data is not None and len(data) > 0:
                    if verbose:
                        latest_date = data['date'].max().strftime('%Y-%m-%d')
                        oldest_date = data['date'].min().strftime('%Y-%m-%d')
                        print(f"  ✓ {stock_code} {fetcher.get_name()}获取成功: {len(data)} 条 ({oldest_date} ~ {latest_date})")
                    
                    # 保存到缓存
                    self._save_to_cache(stock_code, market, data)
                    return data
                else:
                    if verbose:
                        print(f"  ✗ {stock_code} {fetcher.get_name()}返回空数据")
            except Exception as e:
                last_error = e
                if verbose:
                    print(f"  ✗ {stock_code} {fetcher.get_name()}获取失败: {e}")
                continue
        
        # 3. 如果都失败了
        if verbose:
            print(f"  ✗ {stock_code} 所有数据源均失败")
            if last_error:
                print(f"    最后错误: {last_error}")
        
        return None
