#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K线数据获取器 - 支持多数据源fallback和缓存
"""

import pandas as pd
import os
import json
from datetime import date, datetime, timedelta
from abc import ABC, abstractmethod
import time


class KlineFetcherBase(ABC):
    """K线数据获取器基类"""
    
    @abstractmethod
    def fetch(self, stock_code, market="HK", start_date=None, end_date=None, max_count=800):
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


class FutuKlineFetcher(KlineFetcherBase):
    """富途API K线数据获取器"""
    
    def __init__(self, quote_ctx, rate_limiter=None):
        self.quote_ctx = quote_ctx
        self.rate_limiter = rate_limiter
    
    def _normalize_futu_code(self, stock_code, market):
        market = str(market).upper()
        code = str(stock_code).strip()
        if market == "HK":
            if code.startswith("HK."):
                return code
            if code.isdigit():
                return f"HK.{code.zfill(5)}"
        if market == "US":
            if code.startswith("US."):
                return code
            return f"US.{code}"
        return code

    def fetch(self, stock_code, market="HK", start_date=None, end_date=None, max_count=800):
        """使用富途API获取K线数据"""
        import futu as ft
        
        if self.rate_limiter:
            self.rate_limiter.wait_if_needed()
        
        try:
            code = self._normalize_futu_code(stock_code, market)
            ret, data, page_req_key = self.quote_ctx.request_history_kline(
                code=code,
                ktype=ft.KLType.K_DAY,
                max_count=max_count,
                autype=ft.AuType.QFQ  # 前复权
            )
            
            if ret == ft.RET_OK:
                # 确保日期列是datetime类型
                if 'time_key' in data.columns:
                    data['time_key'] = pd.to_datetime(data['time_key'])
                    data = data.sort_values('time_key').reset_index(drop=True)
                    # 统一列名为date
                    data = data.rename(columns={'time_key': 'date'})
                elif 'date' in data.columns:
                    data['date'] = pd.to_datetime(data['date'])
                    data = data.sort_values('date').reset_index(drop=True)
                
                return data
            else:
                error_msg = str(data)
                # 检查是否是额度用尽或其他错误
                if 'quota' in error_msg.lower() or '额度' in error_msg or 'too frequent' in error_msg.lower():
                    raise Exception(f"Futu API quota exhausted or rate limited: {error_msg}")
                return None
        except Exception as e:
            error_msg = str(e)
            if 'quota' in error_msg.lower() or '额度' in error_msg or 'too frequent' in error_msg.lower():
                raise Exception(f"Futu API quota exhausted or rate limited: {error_msg}")
            raise e


class AKShareKlineFetcher(KlineFetcherBase):
    """AKShare K线数据获取器"""
    
    def __init__(self):
        try:
            import akshare as ak
            self.ak = ak
        except ImportError:
            raise ImportError("请安装AKShare: pip install akshare")
    
    def _convert_hk_code(self, stock_code):
        """转换股票代码格式：HK.00700 -> 00700"""
        # 移除HK.前缀
        if stock_code.startswith('HK.'):
            stock_code = stock_code[3:]
        stock_code = str(stock_code)
        if stock_code.isdigit():
            return stock_code.zfill(5)
        return stock_code

    def _convert_us_code(self, stock_code):
        """转换美股代码格式：US.AAPL -> AAPL"""
        code = str(stock_code).strip()
        if code.upper().startswith('US.'):
            return code[3:]
        return code
    
    def fetch(self, stock_code, market="HK", start_date=None, end_date=None, max_count=800):
        """使用AKShare获取K线数据"""
        try:
            market = str(market).upper()
            if market == "HK":
                # 转换股票代码格式
                hk_code = self._convert_hk_code(stock_code)
            else:
                hk_code = None
                us_code = self._convert_us_code(stock_code)
            
            # 计算日期范围
            if end_date is None:
                end_date = date.today().strftime('%Y%m%d')
            else:
                # 转换日期格式：YYYY-MM-DD -> YYYYMMDD
                end_date = pd.to_datetime(end_date).strftime('%Y%m%d')
            
            if start_date is None:
                # 默认获取最近800个交易日的数据（约3年）
                start_date = (date.today() - timedelta(days=800)).strftime('%Y%m%d')
            else:
                # 转换日期格式：YYYY-MM-DD -> YYYYMMDD
                start_date = pd.to_datetime(start_date).strftime('%Y%m%d')
            
            # 调用AKShare接口
            
            data = None
            last_error = None

            if market == "HK":
                # 方法1: 优先使用 stock_hk_daily（Sina，稳定且列名统一）
                if hasattr(self.ak, 'stock_hk_daily'):
                    try:
                        data = self.ak.stock_hk_daily(symbol=hk_code, adjust="qfq")
                        # 过滤日期范围
                        if data is not None and len(data) > 0:
                            # 找到日期列
                            date_col = None
                            for col in data.columns:
                                if 'date' in col.lower() or '日期' in col or '时间' in col:
                                    date_col = col
                                    break
                            
                            if date_col:
                                data[date_col] = pd.to_datetime(data[date_col])
                                start_dt = pd.to_datetime(start_date)
                                end_dt = pd.to_datetime(end_date)
                                data = data[(data[date_col] >= start_dt) & (data[date_col] <= end_dt)]
                    except Exception as e:
                        last_error = e
                        if data is None:
                            data = None
    
                # 方法2: 如果 stock_hk_daily 失败，尝试 stock_hk_hist（东方财富）
                if (data is None or len(data) == 0) and hasattr(self.ak, 'stock_hk_hist'):
                    try:
                        data = self.ak.stock_hk_hist(
                            symbol=hk_code,
                            period="daily",
                            start_date=start_date,
                            end_date=end_date,
                            adjust="qfq"
                        )
                        if data is not None and len(data) > 0:
                            pass  # 成功获取
                    except Exception as e:
                        last_error = e
                        data = None
    
                # 方法3: 如果前两种都失败，尝试通过A+H股接口
                # 注意：这需要股票代码在A+H股列表中，且代码格式可能需要调整
                if (data is None or len(data) == 0) and hasattr(self.ak, 'stock_zh_ah_daily'):
                    try:
                        # A+H股接口需要年份参数
                        start_year = pd.to_datetime(start_date).year
                        end_year = pd.to_datetime(end_date).year
                        data = self.ak.stock_zh_ah_daily(
                            symbol=hk_code,
                            start_year=str(start_year),
                            end_year=str(end_year),
                            adjust="qfq"
                        )
                    except Exception as e:
                        last_error = e
                        if data is None:
                            data = None
            else:
                # 美股：优先使用 stock_us_daily（Sina）
                if hasattr(self.ak, 'stock_us_daily'):
                    try:
                        data = self.ak.stock_us_daily(symbol=us_code, adjust="qfq")
                        # 过滤日期范围
                        if data is not None and len(data) > 0:
                            if 'date' in data.columns:
                                data['date'] = pd.to_datetime(data['date'])
                                start_dt = pd.to_datetime(start_date)
                                end_dt = pd.to_datetime(end_date)
                                data = data[(data['date'] >= start_dt) & (data['date'] <= end_dt)]
                    except Exception as e:
                        last_error = e
                        if data is None:
                            data = None
                # 备用：东方财富 stock_us_hist（需要特殊代码）
                if (data is None or len(data) == 0) and hasattr(self.ak, 'stock_us_hist'):
                    try:
                        data = self.ak.stock_us_hist(
                            symbol=us_code,
                            period="daily",
                            start_date=start_date,
                            end_date=end_date,
                            adjust="qfq"
                        )
                    except Exception as e:
                        last_error = e
                        if data is None:
                            data = None
            
            if data is None or len(data) == 0:
                raise Exception(f"AKShare K线接口不可用或返回空数据。最后错误: {last_error}")
            
            if data is None or len(data) == 0:
                return None
            
            # 统一列名和格式
            # AKShare返回的列名可能是中文，需要转换
            column_mapping = {
                '日期': 'date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'turnover',
                'open': 'open',
                'close': 'close',
                'high': 'high',
                'low': 'low',
            }
            
            # 重命名列
            for old_col, new_col in column_mapping.items():
                if old_col in data.columns:
                    data = data.rename(columns={old_col: new_col})
            
            # 确保有date列
            if 'date' not in data.columns:
                # 尝试找到日期列
                for col in data.columns:
                    col_lower = col.lower()
                    if 'date' in col_lower or '日期' in col or '时间' in col or col_lower == 'day':
                        data = data.rename(columns={col: 'date'})
                        break
            
            # 确保date是datetime类型
            if 'date' in data.columns:
                data['date'] = pd.to_datetime(data['date'])
                data = data.sort_values('date').reset_index(drop=True)
            else:
                raise Exception("无法找到日期列")
            
            # 确保有必要的OHLC列
            required_cols = ['open', 'high', 'low', 'close']
            missing_cols = [col for col in required_cols if col not in data.columns]
            if missing_cols:
                raise Exception(f"缺少必要的列: {missing_cols}")
            
            # 限制数据量
            if len(data) > max_count:
                data = data.tail(max_count).reset_index(drop=True)
            
            return data
            
        except Exception as e:
            print(f"  ✗ AKShare获取 {stock_code} K线失败: {e}")
            return None


class KlineDataManager:
    """K线数据管理器 - 支持多数据源fallback和文件缓存"""
    
    def __init__(self, cache_dir='cache/kline_data'):
        self.cache_dir = cache_dir
        self.futu_fetcher = None
        self.akshare_fetcher = None
        
        # 创建缓存目录
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
    
    def set_futu_fetcher(self, quote_ctx, rate_limiter=None):
        """设置富途API获取器"""
        self.futu_fetcher = FutuKlineFetcher(quote_ctx, rate_limiter)
    
    def set_akshare_fetcher(self):
        """设置AKShare获取器"""
        try:
            self.akshare_fetcher = AKShareKlineFetcher()
        except ImportError:
            print("⚠ AKShare未安装，将无法使用AKShare作为主数据源")
            self.akshare_fetcher = None
    
    def _get_cache_file_path(self, stock_code, market):
        """获取缓存文件路径"""
        # 清理股票代码中的特殊字符
        safe_code = stock_code.replace('.', '_').replace('/', '_')
        market_tag = str(market).upper()
        return os.path.join(self.cache_dir, f"{market_tag}_{safe_code}.parquet")
    
    def _load_from_cache(self, stock_code, market):
        """从缓存加载K线数据"""
        cache_file = self._get_cache_file_path(stock_code, market)
        csv_file = cache_file.replace('.parquet', '.csv')
        
        # 优先尝试parquet，如果不存在则尝试CSV
        file_to_load = None
        if os.path.exists(cache_file):
            file_to_load = cache_file
        elif os.path.exists(csv_file):
            file_to_load = csv_file
        
        if file_to_load is None:
            return None
        
        try:
            # 读取文件
            if file_to_load.endswith('.parquet'):
                try:
                    data = pd.read_parquet(file_to_load)
                except Exception:
                    # 如果parquet读取失败，尝试CSV
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
                
                # 如果最新数据是今天的，使用缓存
                if latest_date.date() == today:
                    return data
                # 如果最新数据是昨天的，也使用缓存（避免频繁更新）
                elif latest_date.date() == today - timedelta(days=1):
                    return data
            
            return data
        except Exception as e:
            print(f"  ⚠ 读取缓存失败 {stock_code}: {e}")
            return None
    
    def _save_to_cache(self, stock_code, market, data):
        """保存K线数据到缓存"""
        if data is None or len(data) == 0:
            return
        
        cache_file = self._get_cache_file_path(stock_code, market)
        
        try:
            # 确保date列是datetime类型
            if 'date' in data.columns:
                data['date'] = pd.to_datetime(data['date'])
            
            # 尝试保存为parquet格式（更高效）
            try:
                data.to_parquet(cache_file, index=False)
            except Exception:
                # 如果parquet失败（可能没有pyarrow），使用CSV
                csv_file = cache_file.replace('.parquet', '.csv')
                data.to_csv(csv_file, index=False, encoding='utf-8')
        except Exception as e:
            print(f"  ⚠ 保存缓存失败 {stock_code}: {e}")
    
    def get_kline_data(self, stock_code, market="HK", start_date=None, end_date=None, max_count=800, 
                       use_cache=True, verbose=False):
        """
        获取K线数据（带缓存和多数据源fallback）
        
        Args:
            stock_code: 股票代码
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
        
        # 2. 优先使用AKShare
        data = None
        
        if self.akshare_fetcher:
            try:
                if verbose:
                    print(f"  📡 {stock_code} 使用AKShare获取...")
                data = self.akshare_fetcher.fetch(
                    stock_code, market=market, start_date=start_date, end_date=end_date, max_count=max_count
                )
                
                if data is not None and len(data) > 0:
                    if verbose:
                        latest_date = data['date'].max().strftime('%Y-%m-%d')
                        oldest_date = data['date'].min().strftime('%Y-%m-%d')
                        print(f"  ✓ {stock_code} AKShare获取成功: {len(data)} 条 ({oldest_date} ~ {latest_date})")
                    # 保存到缓存
                    self._save_to_cache(stock_code, market, data)
                    return data
                else:
                    if verbose:
                        print(f"  ✗ {stock_code} AKShare返回空数据")
            except Exception as e:
                if verbose:
                    print(f"  ✗ {stock_code} AKShare获取失败: {e}")
        
        # 3. Fallback到富途API（如果可用）
        if data is None and self.futu_fetcher:
            try:
                if verbose:
                    print(f"  📡 {stock_code} 尝试使用富途API获取...")
                data = self.futu_fetcher.fetch(
                    stock_code, market=market, start_date=start_date, end_date=end_date, max_count=max_count
                )
                
                if data is not None and len(data) > 0:
                    if verbose:
                        latest_date = data['date'].max().strftime('%Y-%m-%d')
                        oldest_date = data['date'].min().strftime('%Y-%m-%d')
                        print(f"  ✓ {stock_code} 富途API获取成功: {len(data)} 条 ({oldest_date} ~ {latest_date})")
                    # 保存到缓存
                    self._save_to_cache(stock_code, market, data)
                    return data
            except Exception as e:
                error_msg = str(e)
                if verbose:
                    print(f"  ✗ {stock_code} 富途API获取失败: {error_msg}")
        
        # 4. 如果都失败了，返回None
        if verbose and data is None:
            print(f"  ✗ {stock_code} 所有数据源均失败")
        
        return None
