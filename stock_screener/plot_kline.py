#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K线图绘制工具 - 从数据库查询并绘制股票K线图

TODO: 当前仍从旧的 db.get_klines 获取日线数据。
      需改为使用 KlineFetcherFactory 直接从 API 获取 K 线，并支持 --timeframe 参数。
"""

import argparse
import os
from datetime import date, timedelta

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

from db import MarketDatabase, MySqlConfig
from daily_job import _parse_stock_code
from market import market_label


def plot_candlestick(ax, df: pd.DataFrame, width=0.6):
    """
    绘制K线蜡烛图
    
    Args:
        ax: matplotlib axes对象
        df: 包含 date, open, high, low, close 列的DataFrame
        width: 蜡烛宽度（相对于日期间隔的比例）
    """
    if df.empty or "date" not in df.columns:
        return
    
    # 确保日期是datetime类型
    dates = pd.to_datetime(df["date"])
    
    # 计算日期间隔（用于确定蜡烛宽度）
    if len(dates) > 1:
        date_diff = (dates.iloc[-1] - dates.iloc[0]).days / len(dates)
        candle_width = date_diff * width
    else:
        candle_width = 1.0
    
    for i, row in df.iterrows():
        date_val = dates.iloc[i]
        open_price = row.get("open")
        high_price = row.get("high")
        low_price = row.get("low")
        close_price = row.get("close")
        
        if pd.isna(open_price) or pd.isna(close_price):
            continue
        
        # 确定颜色：涨为红色，跌为绿色（中国股市习惯）
        is_up = close_price >= open_price
        color = "#ff4444" if is_up else "#00aa00"  # 红色上涨，绿色下跌
        edge_color = "#cc0000" if is_up else "#008800"
        
        # 绘制影线（最高到最低）
        if not pd.isna(high_price) and not pd.isna(low_price):
            ax.plot(
                [date_val, date_val],
                [low_price, high_price],
                color=edge_color,
                linewidth=0.8,
                alpha=0.8,
            )
        
        # 绘制实体（开盘到收盘）
        body_low = min(open_price, close_price)
        body_high = max(open_price, close_price)
        body_height = body_high - body_low
        
        # 如果开盘价和收盘价相同，绘制一条线
        if body_height < 0.001:
            ax.plot(
                [date_val - timedelta(days=candle_width/2), date_val + timedelta(days=candle_width/2)],
                [open_price, open_price],
                color=edge_color,
                linewidth=1.5,
            )
        else:
            rect = Rectangle(
                (mdates.date2num(date_val) - candle_width/2, body_low),
                candle_width,
                body_height,
                facecolor=color,
                edgecolor=edge_color,
                linewidth=0.8,
                alpha=0.8,
            )
            ax.add_patch(rect)


def plot_volume(ax, df: pd.DataFrame, color_map=None):
    """
    绘制成交量柱状图
    
    Args:
        ax: matplotlib axes对象
        df: 包含 date, volume, close, open 列的DataFrame
        color_map: 颜色映射（可选）
    """
    if df.empty or "date" not in df.columns or "volume" not in df.columns:
        return
    
    dates = pd.to_datetime(df["date"])
    volumes = df["volume"]
    
    # 如果没有颜色映射，根据涨跌设置颜色
    if color_map is None:
        colors = []
        for _, row in df.iterrows():
            open_price = row.get("open", 0)
            close_price = row.get("close", 0)
            is_up = close_price >= open_price
            colors.append("#ff4444" if is_up else "#00aa00")
    else:
        colors = color_map
    
    # 计算日期间隔
    if len(dates) > 1:
        date_diff = (dates.iloc[-1] - dates.iloc[0]).days / len(dates)
        bar_width = date_diff * 0.8
    else:
        bar_width = 1.0
    
    ax.bar(dates, volumes, width=bar_width, color=colors, alpha=0.6, edgecolor="none")


def plot_kline_chart(
    stock_code: str,
    mysql: MySqlConfig,
    start_date: str = None,
    end_date: str = None,
    show_volume: bool = True,
    save_path: str = None,
):
    """
    绘制股票K线图
    
    Args:
        stock_code: 股票代码，例如 HK.00700 或 US.AAPL
        mysql: MySQL配置
        start_date: 开始日期（YYYY-MM-DD），可选
        end_date: 结束日期（YYYY-MM-DD），可选
        show_volume: 是否显示成交量
        save_path: 保存路径，如果为None则显示图表
    """
    # 解析股票代码
    parsed = _parse_stock_code(stock_code)
    if not parsed:
        print(f"❌ 无法解析股票代码: {stock_code}")
        return
    market, code = parsed
    
    # 连接数据库
    db = MarketDatabase(mysql)
    
    # 获取股票名称
    stocks = db.get_stocks(market)
    stock_info = next((s for s in stocks if s["code"] == code), None)
    stock_name = stock_info.get("name") if stock_info else code
    
    # 查询K线数据
    print(f"📊 正在查询 {code} ({stock_name}) 的K线数据...")
    df = db.get_klines(market, code, start_date=start_date, end_date=end_date)
    db.close()
    
    if df.empty:
        print(f"❌ 未找到股票 {code} 的K线数据")
        print(f"💡 提示: 请先使用 daily_job.py --stock-code {stock_code} 同步数据")
        return
    
    print(f"✓ 查询到 {len(df)} 条K线数据")
    print(f"   日期范围: {df['date'].min().strftime('%Y-%m-%d')} 至 {df['date'].max().strftime('%Y-%m-%d')}")
    
    # 准备数据
    df = df.sort_values("date").reset_index(drop=True)
    
    # 创建图表
    if show_volume:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), height_ratios=[3, 1])
        fig.suptitle(f"{stock_name} ({code}) - K线图", fontsize=16, fontweight="bold")
    else:
        fig, ax1 = plt.subplots(1, 1, figsize=(14, 8))
        fig.suptitle(f"{stock_name} ({code}) - K线图", fontsize=16, fontweight="bold")
        ax2 = None
    
    # 绘制K线
    plot_candlestick(ax1, df)
    
    # 设置K线图样式
    ax1.set_xlabel("日期", fontsize=12)
    ax1.set_ylabel("价格", fontsize=12)
    ax1.grid(True, alpha=0.3, linestyle="--")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    ax1.xaxis.set_major_locator(mdates.AutoDateLocator())
    
    # 如果数据点太多，旋转日期标签
    if len(df) > 30:
        plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha="right")
    
    # 添加价格统计信息
    if not df.empty:
        max_price = df["high"].max()
        min_price = df["low"].min()
        latest_close = df["close"].iloc[-1]
        latest_date = df["date"].iloc[-1].strftime("%Y-%m-%d")
        
        info_text = f"最高: {max_price:.2f} | 最低: {min_price:.2f} | 最新收盘: {latest_close:.2f} ({latest_date})"
        ax1.text(
            0.02, 0.98, info_text,
            transform=ax1.transAxes,
            fontsize=10,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
        )
    
    # 绘制成交量
    if show_volume and ax2 is not None:
        plot_volume(ax2, df)
        ax2.set_xlabel("日期", fontsize=12)
        ax2.set_ylabel("成交量", fontsize=12)
        ax2.grid(True, alpha=0.3, linestyle="--")
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
        
        if len(df) > 30:
            plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")
    
    plt.tight_layout()
    
    # 保存或显示
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches="tight")
        print(f"✓ 图表已保存到: {save_path}")
    else:
        plt.show()


def main():
    parser = argparse.ArgumentParser(description="绘制股票K线图")
    parser.add_argument("--stock_code", default=os.getenv("STOCK_CODE", "HK.00700"), help="股票代码，例如: HK.00700 或 US.AAPL")
    parser.add_argument("--mysql-host", default=os.getenv("MYSQL_HOST", "127.0.0.1"), help="MySQL Host")
    parser.add_argument("--mysql-port", type=int, default=int(os.getenv("MYSQL_PORT", "3306")), help="MySQL Port")
    parser.add_argument("--mysql-user", default=os.getenv("MYSQL_USER", "root"), help="MySQL User")
    parser.add_argument("--mysql-password", default=os.getenv("MYSQL_PASSWORD", "123456"), help="MySQL Password")
    parser.add_argument("--mysql-database", default=os.getenv("MYSQL_DATABASE", "market_data"), help="MySQL Database")
    parser.add_argument("--mysql-charset", default=os.getenv("MYSQL_CHARSET", "utf8mb4"), help="MySQL Charset")
    parser.add_argument("--start-date", default=None, help="开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="结束日期 (YYYY-MM-DD)")
    parser.add_argument("--no-volume", action="store_true", help="不显示成交量")
    parser.add_argument("--save", default=None, help="保存图表到文件路径（例如: output/kline.png）")
    args = parser.parse_args()
    
    mysql = MySqlConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_database,
        charset=args.mysql_charset,
    )
    
    plot_kline_chart(
        stock_code=args.stock_code,
        mysql=mysql,
        start_date=args.start_date,
        end_date=args.end_date,
        show_volume=not args.no_volume,
        save_path=args.save,
    )


if __name__ == "__main__":
    main()
