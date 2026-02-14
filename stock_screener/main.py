#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票筛选器 - EMA10向上突破EMA150筛选（支持港股/美股）

TODO: 本文件（GUI 模式）仍依赖已删除的 KlineDataManager。
      需要将 K 线获取改为使用 KlineFetcherFactory + timeframe 参数。
      当前仅保留代码不做大改，运行 GUI 时可能会报错。
      推荐使用 daily_job.py 或 screen_with_filters.py 替代。
"""

import argparse
import json
import os
import threading
import time
from datetime import date
import pandas as pd
import pandas_ta as ta

# TODO: KlineDataManager 已删除，改为使用 KlineFetcherFactory
try:
    from kline_fetcher import KlineFetcherFactory
    KlineDataManager = None  # 标记已不可用
except ImportError:
    KlineFetcherFactory = None
    KlineDataManager = None

from market import MARKET_CONFIG, market_label, normalize_market
from universe import fetch_stock_list

try:
    import futu as ft
    FUTU_AVAILABLE = True
except Exception:
    ft = None
    FUTU_AVAILABLE = False

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
    GUI_AVAILABLE = True
except Exception:
    tk = None
    ttk = None
    messagebox = None
    GUI_AVAILABLE = False

try:
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MPL_AVAILABLE = True
except Exception:
    plt = None
    FigureCanvasTkAgg = None
    MPL_AVAILABLE = False

# 配置
FUTU_HOST = '127.0.0.1'
FUTU_PORT = 11111
CACHE_DIR = 'cache'  # 缓存目录

# 限流配置：最多60次请求/30秒
MAX_REQUESTS_PER_WINDOW = 60
TIME_WINDOW_SECONDS = 30


class RateLimiter:
    """限流器：控制API请求频率"""
    def __init__(self, max_requests=MAX_REQUESTS_PER_WINDOW, time_window=TIME_WINDOW_SECONDS):
        self.max_requests = max_requests
        self.time_window = time_window
        self.request_times = []
        self.lock = threading.Lock()
        self.last_wait_time = 0
    
    def wait_if_needed(self):
        """如果需要，等待直到可以发送请求"""
        with self.lock:
            now = time.time()
            # 移除time_window秒之前的请求记录
            self.request_times = [t for t in self.request_times if now - t < self.time_window]
            
            # 如果已达到限制，等待
            if len(self.request_times) >= self.max_requests:
                # 计算需要等待的时间（等待最老的请求过期）
                oldest_request = min(self.request_times)
                wait_time = self.time_window - (now - oldest_request) + 0.5  # 加0.5秒缓冲
                if wait_time > 0:
                    if wait_time > self.last_wait_time + 0.1:  # 避免频繁打印
                        print(f"⏳ 限流：等待 {wait_time:.1f} 秒（已请求 {len(self.request_times)}/{self.max_requests}）...")
                        self.last_wait_time = wait_time
                    time.sleep(wait_time)
                    # 重新计算
                    now = time.time()
                    self.request_times = [t for t in self.request_times if now - t < self.time_window]
            
            # 记录本次请求时间
            self.request_times.append(time.time())


class StockScreener:
    def __init__(self, market="HK"):
        self.quote_ctx = None
        self.stocks_data = {}  # {stock_code: DataFrame}
        self.filtered_stocks = []  # 筛选后的股票列表
        self.last_log_records = []  # 结构化日志记录
        self.root = None
        self.market = normalize_market(market)
        self.rate_limiter = RateLimiter()  # 限流器
        # TODO: KlineDataManager 已删除，需改为 KlineFetcherFactory
        self.kline_manager = None
        
        # 创建缓存目录
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)
        
    def set_market(self, market: str):
        self.market = normalize_market(market)

    def _stocks_cache_file(self):
        return os.path.join(CACHE_DIR, f"{self.market.lower()}_stocks.json")

    def _filtered_results_cache_file(self):
        return os.path.join(CACHE_DIR, f"{self.market.lower()}_filtered_results.json")

    def connect(self):
        """连接Futu OpenD"""
        # 无论是否连接Futu，先启用AKShare获取器
        self.kline_manager.set_akshare_fetcher()

        if not FUTU_AVAILABLE:
            print("⚠ futu-api 未安装，将仅使用 AKShare 获取数据")
            return False

        try:
            self.quote_ctx = ft.OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
            print(f"✓ 成功连接到 Futu OpenD ({FUTU_HOST}:{FUTU_PORT})")
            
            # 设置K线数据管理器
            self.kline_manager.set_futu_context(self.quote_ctx, self.rate_limiter)
            
            return True
        except Exception as e:
            print(f"✗ 连接失败: {e}")
            print("将继续使用 AKShare，不影响程序运行")
            self.quote_ctx = None
            return False
    
    def disconnect(self):
        """断开连接"""
        if self.quote_ctx:
            self.quote_ctx.close()
            print("✓ 已断开连接")
    
    def get_stocks(self):
        """获取股票列表（优先从缓存读取）"""
        # 检查缓存文件是否存在且是今天的
        today_str = date.today().isoformat()
        
        stocks_cache_file = self._stocks_cache_file()
        if os.path.exists(stocks_cache_file):
            try:
                with open(stocks_cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                
                # 检查缓存日期是否为今天
                if cache_data.get('date') == today_str:
                    print(f"✓ 从缓存读取股票列表（{len(cache_data['stocks'])} 只）")
                    return cache_data['stocks']
                else:
                    print(f"缓存已过期（日期：{cache_data.get('date')}），重新获取")
            except Exception as e:
                print(f"读取缓存失败: {e}，重新获取")
        
        # 使用统一入口获取（AKShare → Futu 兜底，港股/美股自动 yfinance 补全市值、PE）
        stocks, source = fetch_stock_list(
            self.market,
            quote_ctx=self.quote_ctx if (self.quote_ctx and FUTU_AVAILABLE) else None,
        )
        if stocks:
            print(f"✓ {source or 'API'} 获取到 {len(stocks)} 只{market_label(self.market)}")
        else:
            print("✗ 获取股票列表失败")

        if not stocks:
            return []

        # 保存到缓存
        try:
            cache_data = {
                'date': today_str,
                'stocks': stocks
            }
            with open(stocks_cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            print("✓ 股票列表已保存到缓存")
        except Exception as e:
            print(f"保存缓存失败: {e}")

        return stocks
    
    def save_filtered_results_cache(self, filtered_stocks):
        """保存筛选结果到缓存文件"""
        today_str = date.today().isoformat()
        try:
            cache_data = {
                'date': today_str,
                'count': len(filtered_stocks),
                'market': self.market,
                'stocks': filtered_stocks
            }
            with open(self._filtered_results_cache_file(), 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            print(f"✓ 筛选结果已保存到缓存（{len(filtered_stocks)} 只股票）")
        except Exception as e:
            print(f"保存筛选结果缓存失败: {e}")
    
    def load_filtered_results_cache(self):
        """从缓存文件加载筛选结果"""
        today_str = date.today().isoformat()
        
        cache_file = self._filtered_results_cache_file()
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                
                # 检查缓存日期是否为今天
                if cache_data.get('date') == today_str:
                    stocks = cache_data.get('stocks', [])
                    print(f"✓ 从缓存加载筛选结果（{len(stocks)} 只股票，日期：{cache_data.get('date')}）")
                    return stocks
                else:
                    print(f"筛选结果缓存已过期（日期：{cache_data.get('date')}）")
            except Exception as e:
                print(f"读取筛选结果缓存失败: {e}")
        
        return None
    
    def get_history_kline(self, stock_code, max_count=800, verbose=False):
        """获取历史K线数据（使用KlineDataManager，支持缓存和fallback）"""
        # 使用K线数据管理器获取数据
        data = self.kline_manager.get_kline_data(
            stock_code=stock_code,
            market=self.market,
            max_count=max_count,
            use_cache=True,
            verbose=verbose
        )
        
        # 统一列名：确保有time_key列（兼容现有代码）
        if data is not None and 'date' in data.columns:
            data = data.rename(columns={'date': 'time_key'})
        
        return data
    
    def calculate_indicators(self, df, stock_code=None, verbose=False):
        """计算技术指标"""
        if df is None:
            if verbose and stock_code:
                print(f"  ✗ {stock_code} 数据为空，无法计算指标")
            return None
        
        # 确保有time_key列（统一处理）
        if 'time_key' not in df.columns and 'date' in df.columns:
            df = df.rename(columns={'date': 'time_key'})
        
        if len(df) < 200:
            if verbose and stock_code:
                print(f"  ✗ {stock_code} 数据量不足: {len(df)} 条（需要至少200条）")
            return None
        
        close = df['close']
        
        # 计算EMA
        ema10 = close.ewm(span=10, adjust=False).mean()
        ema150 = close.ewm(span=150, adjust=False).mean()
        
        # 计算HMA
        hma40 = ta.hma(close, length=40)
        hma200 = ta.hma(close, length=200)
        hma600 = ta.hma(close, length=600)
        
        # 添加到DataFrame
        df = df.copy()
        df['EMA10'] = ema10
        df['EMA150'] = ema150
        df['HMA40'] = hma40
        df['HMA200'] = hma200
        df['HMA600'] = hma600
        
        if verbose and stock_code:
            latest_close = df.iloc[-1]['close']
            latest_ema10 = df.iloc[-1]['EMA10']
            latest_ema150 = df.iloc[-1]['EMA150']
            print(f"  ✓ {stock_code} 指标计算完成: 收盘价={latest_close:.2f}, EMA10={latest_ema10:.2f}, EMA150={latest_ema150:.2f}")
        
        return df
    
    def check_ema_cross(self, df, stock_code=None, stock_name=None, verbose=False):
        """检查EMA10是否刚刚向上突破EMA150"""
        if df is None or len(df) < 2:
            if verbose and stock_code:
                print(f"  ✗ {stock_code} ({stock_name}) 数据不足，无法检查突破")
            return False, "数据不足"
        
        # 获取最近两个交易日的数据
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 检查条件：
        # 1. 前一个交易日：EMA10 < EMA150
        # 2. 当前交易日：EMA10 > EMA150
        # 3. 确保有足够的数据（至少150个交易日）
        
        if len(df) < 150:
            if verbose and stock_code:
                print(f"  ✗ {stock_code} ({stock_name}) 历史数据不足: {len(df)} 条（需要至少150条）")
            return False, f"历史数据不足({len(df)}条)"
        
        prev_ema10 = prev['EMA10']
        prev_ema150 = prev['EMA150']
        curr_ema10 = last['EMA10']
        curr_ema150 = last['EMA150']
        
        prev_cross = prev_ema10 <= prev_ema150  # 前一日EMA10必须小于EMA150
        curr_cross = curr_ema10 >= curr_ema150  # 当前EMA10必须大于EMA150（严格大于，表示向上突破）
        
        if verbose and stock_code:
            # 获取日期
            try:
                prev_date = pd.to_datetime(prev['time_key']).strftime('%Y-%m-%d')
            except:
                prev_date = '前一日'
            try:
                curr_date = pd.to_datetime(last['time_key']).strftime('%Y-%m-%d')
            except:
                curr_date = '当前'
            
            print(f"  📊 {stock_code} ({stock_name}) EMA突破检查:")
            print(f"     {prev_date}: EMA10={prev_ema10:.2f}, EMA150={prev_ema150:.2f}, EMA10<EMA150={prev_cross}")
            print(f"     {curr_date}: EMA10={curr_ema10:.2f}, EMA150={curr_ema150:.2f}, EMA10>EMA150={curr_cross}")
            
            if prev_cross and curr_cross:
                print(f"     ✅ 满足突破条件！")
            else:
                if not prev_cross:
                    print(f"     ❌ 前一日EMA10({prev_ema10:.2f}) >= EMA150({prev_ema150:.2f})，不满足条件")
                if not curr_cross:
                    print(f"     ❌ 当前EMA10({curr_ema10:.2f}) <= EMA150({curr_ema150:.2f})，不满足条件")
        
        if prev_cross and curr_cross:
            return True, "满足突破条件"
        else:
            reason = []
            if not prev_cross:
                reason.append(f"前一日EMA10({prev_ema10:.2f})>=EMA150({prev_ema150:.2f})")
            if not curr_cross:
                reason.append(f"当前EMA10({curr_ema10:.2f})<=EMA150({curr_ema150:.2f})")
            return False, "; ".join(reason)
    
    def _build_log_record(self, stock_code, stock_name, status, reason, df=None):
        """生成结构化日志记录"""
        last_close = None
        last_ema10 = None
        last_ema150 = None
        last_date = None
        if df is not None and len(df) > 0:
            last_row = df.iloc[-1]
            last_close = float(last_row['close']) if 'close' in df.columns else None
            last_ema10 = float(last_row['EMA10']) if 'EMA10' in df.columns else None
            last_ema150 = float(last_row['EMA150']) if 'EMA150' in df.columns else None
            if 'time_key' in df.columns:
                try:
                    last_date = pd.to_datetime(last_row['time_key']).strftime('%Y-%m-%d')
                except Exception:
                    last_date = None
            elif 'date' in df.columns:
                try:
                    last_date = pd.to_datetime(last_row['date']).strftime('%Y-%m-%d')
                except Exception:
                    last_date = None

        return {
            'market': self.market,
            'code': stock_code,
            'name': stock_name,
            'status': status,
            'reason': reason,
            'close': last_close,
            'ema10': last_ema10,
            'ema150': last_ema150,
            'date': last_date,
        }

    def screen_stocks(self, stocks, progress_callback=None, verbose=True):
        """筛选股票"""
        filtered = []
        log_records = []
        total = len(stocks)
        stats = {
            'total': total,
            'kline_failed': 0,
            'indicators_failed': 0,
            'no_cross': 0,
            'passed': 0
        }
        
        if verbose:
            print(f"\n{'='*80}")
            print(f"开始筛选 {total} 只{market_label(self.market)}股票...")
            print(f"{'='*80}\n")
        
        for idx, stock in enumerate(stocks):
            stock_code = stock['code']
            stock_name = stock['name']
            
            if progress_callback:
                progress_callback(idx + 1, total, stock_name)
            
            if verbose:
                print(f"\n[{idx+1}/{total}] 处理股票: {stock_code} ({stock_name})")
            
            # 获取K线数据
            kline_data = self.get_history_kline(stock_code, verbose=verbose)
            if kline_data is None:
                stats['kline_failed'] += 1
                if verbose:
                    print(f"  ❌ {stock_code} 跳过：无法获取K线数据")
                log_records.append(
                    self._build_log_record(
                        stock_code, stock_name, 'failed', '无法获取K线数据', None
                    )
                )
                continue
            
            # 计算指标
            df_with_indicators = self.calculate_indicators(
                kline_data, stock_code=stock_code, verbose=verbose
            )
            if df_with_indicators is None:
                stats['indicators_failed'] += 1
                if verbose:
                    print(f"  ❌ {stock_code} 跳过：无法计算技术指标")
                log_records.append(
                    self._build_log_record(
                        stock_code, stock_name, 'failed', '无法计算技术指标', kline_data
                    )
                )
                continue
            
            # 检查突破
            is_cross, reason = self.check_ema_cross(
                df_with_indicators, stock_code=stock_code, stock_name=stock_name, verbose=verbose
            )
            
            if is_cross:
                # 保存数据
                self.stocks_data[stock_code] = df_with_indicators
                
                latest_close = df_with_indicators.iloc[-1]['close']
                latest_ema10 = df_with_indicators.iloc[-1]['EMA10']
                latest_ema150 = df_with_indicators.iloc[-1]['EMA150']
                
                filtered.append({
                    'code': stock_code,
                    'name': stock_name,
                    'close': latest_close,
                    'ema10': latest_ema10,
                    'ema150': latest_ema150
                })
                log_records.append(
                    self._build_log_record(
                        stock_code, stock_name, 'passed', '满足突破条件', df_with_indicators
                    )
                )
                
                stats['passed'] += 1
                if verbose:
                    print(f"  ✅ {stock_code} ({stock_name}) 符合条件！收盘价={latest_close:.2f}")
            else:
                stats['no_cross'] += 1
                if verbose:
                    print(f"  ❌ {stock_code} ({stock_name}) 不符合条件: {reason}")
                log_records.append(
                    self._build_log_record(
                        stock_code, stock_name, 'failed', reason, df_with_indicators
                    )
                )
        
        # 打印统计信息
        if verbose:
            print(f"\n{'='*80}")
            print("筛选统计:")
            print(f"  总股票数: {stats['total']}")
            print(f"  ✓ 符合条件: {stats['passed']} 只")
            print(f"  ✗ K线获取失败: {stats['kline_failed']} 只")
            print(f"  ✗ 指标计算失败: {stats['indicators_failed']} 只")
            print(f"  ✗ 未满足突破条件: {stats['no_cross']} 只")
            print(f"{'='*80}\n")
        
        self.filtered_stocks = filtered
        self.last_log_records = log_records
        if verbose:
            print(f"✓ 筛选完成，找到 {len(filtered)} 只符合条件的股票")
        
        # 保存筛选结果到缓存
        self.save_filtered_results_cache(filtered)
        
        return filtered
    
    def plot_stock_chart(self, stock_code, stock_name):
        """绘制股票图表"""
        if not GUI_AVAILABLE or not MPL_AVAILABLE:
            print("GUI/绘图依赖不可用，无法显示图表")
            return
        # 如果数据不在内存中（比如从缓存加载的结果），则重新获取
        if stock_code not in self.stocks_data:
            print(f"⚠ {stock_code} 的数据不在内存中，正在获取K线数据...")
            kline_data = self.get_history_kline(stock_code, verbose=True)
            if kline_data is None:
                if messagebox:
                    messagebox.showerror("错误", f"无法获取股票 {stock_code} 的K线数据")
                return
            
            # 计算指标
            df_with_indicators = self.calculate_indicators(kline_data, stock_code=stock_code)
            if df_with_indicators is None:
                if messagebox:
                    messagebox.showerror("错误", f"无法计算股票 {stock_code} 的技术指标")
                return
            
            # 保存到内存
            self.stocks_data[stock_code] = df_with_indicators
            df = df_with_indicators
        else:
            df = self.stocks_data[stock_code]
        
        # 确保有time_key列（兼容现有代码）
        if 'time_key' not in df.columns and 'date' in df.columns:
            df = df.rename(columns={'date': 'time_key'})
        
        # 创建图表窗口
        chart_window = tk.Toplevel(self.root)
        chart_window.title(f"{stock_name} ({stock_code})")
        chart_window.geometry("1200x700")
        
        # 创建matplotlib图表
        fig, ax = plt.subplots(figsize=(12, 7))
        
        # 绘制蜡烛图（使用收盘价线图，简化版）
        dates = df['time_key']
        close_prices = df['close']
        
        # 绘制收盘价线
        ax.plot(dates, close_prices, label='收盘价', color='black', linewidth=1.5, alpha=0.8)
        
        # 绘制OHLC（如果有数据）
        if all(col in df.columns for col in ['open', 'high', 'low', 'close']):
            # 绘制最高最低价范围
            for i in range(len(df)):
                ax.plot([dates.iloc[i], dates.iloc[i]], 
                       [df.iloc[i]['low'], df.iloc[i]['high']], 
                       color='gray', linewidth=0.5, alpha=0.3)
            # 绘制涨跌颜色
            for i in range(len(df)):
                color = 'red' if df.iloc[i]['close'] >= df.iloc[i]['open'] else 'green'
                ax.plot([dates.iloc[i], dates.iloc[i]], 
                       [df.iloc[i]['open'], df.iloc[i]['close']], 
                       color=color, linewidth=2, alpha=0.6)
        
        # 绘制EMA线
        ax.plot(dates, df['EMA10'], label='EMA10', color='blue', linewidth=1.5)
        ax.plot(dates, df['EMA150'], label='EMA150', color='red', linewidth=1.5)
        
        # 绘制HMA线
        ax.plot(dates, df['HMA40'], label='HMA40', color='green', linewidth=1, alpha=0.7)
        ax.plot(dates, df['HMA200'], label='HMA200', color='orange', linewidth=1, alpha=0.7)
        ax.plot(dates, df['HMA600'], label='HMA600', color='purple', linewidth=1, alpha=0.7)
        
        # 标记突破点
        if len(df) >= 2:
            last_idx = len(df) - 1
            ax.scatter(dates.iloc[last_idx], df.iloc[last_idx]['EMA10'], 
                      color='red', s=100, marker='^', zorder=5, label='突破点')
        
        ax.set_title(f"{stock_name} ({stock_code}) - 日线图", fontsize=14, fontweight='bold')
        ax.set_xlabel("日期", fontsize=12)
        ax.set_ylabel("价格", fontsize=12)
        ax.legend(loc='best')
        ax.grid(True, alpha=0.3)
        
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        # 嵌入到tkinter窗口
        canvas = FigureCanvasTkAgg(fig, chart_window)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
    
    def run_gui(self):
        """运行GUI界面"""
        if not GUI_AVAILABLE or not MPL_AVAILABLE:
            print("GUI 依赖不可用，无法启动界面")
            return

        self.root = tk.Tk()
        self.root.title("股票筛选器 - EMA10突破EMA150")
        self.root.geometry("1000x700")
        
        # 顶部按钮区域
        top_frame = ttk.Frame(self.root, padding="10")
        top_frame.pack(fill=tk.X)

        ttk.Label(top_frame, text="市场:").pack(side=tk.LEFT, padx=(0, 5))
        self.market_var = tk.StringVar(value=self.market)
        market_values = list(MARKET_CONFIG.keys())
        market_combo = ttk.Combobox(
            top_frame,
            textvariable=self.market_var,
            values=market_values,
            state="readonly",
            width=6,
        )
        market_combo.pack(side=tk.LEFT, padx=5)
        ttk.Label(
            top_frame,
            text="HK=港股, US=美股",
        ).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(top_frame, text="开始筛选", command=self.start_screening).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="刷新", command=self.refresh_results).pack(side=tk.LEFT, padx=5)
        
        # 进度条
        self.progress_var = tk.StringVar(value="准备就绪")
        self.progress_label = ttk.Label(top_frame, textvariable=self.progress_var)
        self.progress_label.pack(side=tk.LEFT, padx=20)
        
        self.progress_bar = ttk.Progressbar(top_frame, mode='determinate')
        self.progress_bar.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)
        
        # 结果显示区域
        result_frame = ttk.Frame(self.root)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 创建表格显示所有筛选结果
        self.result_tree = ttk.Treeview(result_frame, columns=('code', 'name', 'close', 'ema10', 'ema150'), 
                                      show='headings', height=30)
        self.result_tree.heading('code', text='代码')
        self.result_tree.heading('name', text='名称')
        self.result_tree.heading('close', text='收盘价')
        self.result_tree.heading('ema10', text='EMA10')
        self.result_tree.heading('ema150', text='EMA150')
        
        self.result_tree.column('code', width=120)
        self.result_tree.column('name', width=250)
        self.result_tree.column('close', width=100)
        self.result_tree.column('ema10', width=120)
        self.result_tree.column('ema150', width=120)
        
        # 绑定双击事件
        self.result_tree.bind('<Double-1>', lambda e: self.on_stock_double_click(self.result_tree))
        
        # 添加滚动条
        scrollbar = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=scrollbar.set)
        
        self.result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 启动时尝试加载缓存结果
        self.load_cached_results_on_startup()
        
        self.root.mainloop()
    
    def load_cached_results_on_startup(self):
        """启动时加载缓存的筛选结果"""
        cached_results = self.load_filtered_results_cache()
        if cached_results:
            self.filtered_stocks = cached_results
            self.update_results(cached_results)
            self.progress_var.set(f"已加载缓存结果：{len(cached_results)} 只股票")
    
    def start_screening(self):
        """开始筛选（在新线程中运行）"""
        if hasattr(self, "market_var"):
            self.set_market(self.market_var.get())
            self.root.title(f"{market_label(self.market)}筛选器 - EMA10突破EMA150")

        if not self.quote_ctx and messagebox:
            messagebox.showwarning(
                "提示",
                "Futu OpenD 未连接，将使用 AKShare 获取数据",
            )
        
        # 重置进度
        self.progress_var.set("开始筛选...")
        self.progress_bar.config(value=0)
        
        def screening_thread():
            # 获取股票列表
            stocks = self.get_stocks()
            if not stocks:
                self.root.after(0, lambda: messagebox.showerror("错误", "无法获取股票列表"))
                return
            
            # 筛选股票
            def update_progress(current, total, stock_name):
                progress_pct = int((current / total) * 100)
                self.root.after(0, lambda: self.progress_var.set(
                    f"正在筛选: {current}/{total} ({progress_pct}%) - {stock_name}"
                ))
                self.root.after(0, lambda: self.progress_bar.config(value=progress_pct))
            
            # 重置进度条
            self.root.after(0, lambda: self.progress_bar.config(maximum=len(stocks), value=0))
            
            filtered = self.screen_stocks(stocks, update_progress)
            
            # 更新UI
            self.root.after(0, lambda: self.update_results(filtered))
            self.root.after(0, lambda: self.progress_var.set(f"筛选完成，找到 {len(filtered)} 只股票"))
            self.root.after(0, lambda: self.progress_bar.config(value=100))
        
        threading.Thread(target=screening_thread, daemon=True).start()
    
    def update_results(self, filtered_stocks):
        """更新结果显示"""
        # 清除现有数据
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        
        # 添加所有筛选结果
        for stock in filtered_stocks:
            self.result_tree.insert('', 'end', values=(
                stock['code'],
                stock['name'],
                f"{stock['close']:.2f}",
                f"{stock['ema10']:.2f}",
                f"{stock['ema150']:.2f}"
            ))
    
    def on_stock_double_click(self, tree):
        """双击股票时显示图表"""
        selection = tree.selection()
        if selection:
            item = tree.item(selection[0])
            stock_code = item['values'][0]
            stock_name = item['values'][1]
            self.plot_stock_chart(stock_code, stock_name)
    
    def refresh_results(self):
        """刷新结果"""
        if self.filtered_stocks:
            self.update_results(self.filtered_stocks)

    def save_results_to_file(self, filtered_stocks, output_path):
        """保存筛选结果到文件"""
        if not output_path:
            return
        df = pd.DataFrame(filtered_stocks)
        if df.empty:
            print("无筛选结果可保存")
            return
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        if output_path.lower().endswith('.json'):
            df.to_json(output_path, orient='records', force_ascii=False, indent=2)
        else:
            df.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"✓ 筛选结果已保存到: {output_path}")

    def save_log_to_file(self, log_records, output_path):
        """保存结构化日志到文件（JSON Lines）"""
        if not output_path:
            return
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        if not log_records:
            print("无日志记录可保存")
            return
        with open(output_path, 'w', encoding='utf-8') as f:
            for record in log_records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"✓ 日志已保存到: {output_path}")

    def print_results(self, filtered_stocks):
        """命令行输出筛选结果"""
        df = pd.DataFrame(filtered_stocks)
        if df.empty:
            print("未找到符合条件的股票")
            return
        print("\n筛选结果：")
        print(df.to_string(index=False))

    def run_cli(self, output_path='filtered_results.csv', log_path=None, limit=None):
        """命令行模式运行"""
        stocks = self.get_stocks()
        if not stocks:
            print("无法获取股票列表，退出")
            return
        if limit:
            stocks = stocks[:limit]
        filtered = self.screen_stocks(stocks, progress_callback=None, verbose=False)
        self.print_results(filtered)
        self.save_results_to_file(filtered, output_path)
        if log_path is None:
            today = date.today().strftime('%Y-%m-%d')
            log_path = os.path.join('output', f'screen_log_{self.market}_{today}.jsonl')
        self.save_log_to_file(self.last_log_records, log_path)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="股票 EMA10/EMA150 突破筛选器")
    parser.add_argument("--cli", action="store_true", help="使用命令行模式运行")
    parser.add_argument("--output", default="filtered_results.csv", help="筛选结果输出文件")
    parser.add_argument("--log", default=None, help="结构化日志输出文件(JSONL)")
    parser.add_argument("--limit", type=int, default=None, help="限制筛选的股票数量")
    parser.add_argument("--market", default="HK", help="市场选择: HK 或 US")
    args = parser.parse_args()

    screener = StockScreener(market=args.market)
    
    # 连接Futu（可选）
    screener.connect()
    
    try:
        # 运行GUI或CLI
        if args.cli or not GUI_AVAILABLE or not MPL_AVAILABLE:
            if not (GUI_AVAILABLE and MPL_AVAILABLE) and not args.cli:
                print("GUI 依赖不可用，自动切换到命令行模式")
            screener.run_cli(output_path=args.output, log_path=args.log, limit=args.limit)
        else:
            screener.run_gui()
    except KeyboardInterrupt:
        print("\n程序中断")
    finally:
        screener.disconnect()


if __name__ == '__main__':
    main()
