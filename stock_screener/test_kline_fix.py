#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 K 线数据修复 - 验证时间范围是否正确
"""

from kline_fetcher import KlineFetcherFactory
from datetime import datetime

def test_kline_timerange():
    """测试 K 线时间范围是否正确"""

    # 连接 Futu OpenD
    try:
        from futu import OpenQuoteContext
        quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        print('✓ 已连接 Futu OpenD\n')
    except Exception as e:
        print(f'✗ 无法连接 Futu OpenD: {e}')
        return False

    # 创建获取器
    fetchers = KlineFetcherFactory.create_fetcher_chain(quote_ctx=quote_ctx)

    # 测试用例
    test_cases = [
        ('HK.01044', 'HK', '15m', '15分钟线'),
        # ('HK.00700', 'HK', '1d', '日线'),
        # ('HK.00700', 'HK', '1h', '1小时线'),
    ]

    all_passed = True
    current_year = datetime.now().year

    for code, market, timeframe, desc in test_cases:
        print(f'{"="*60}')
        print(f'测试: {code} - {desc} ({timeframe})')
        print(f'{"="*60}')

        for fetcher in fetchers:
            try:
                df = fetcher.fetch(code, market=market, timeframe=timeframe)
                if df is not None and not df.empty:
                    print(f'✓ 数据源: {fetcher.get_name()}')
                    print(f'  K线数量: {len(df)}')

                    first_date = df['date'].iloc[0]
                    last_date = df['date'].iloc[-1]

                    print(f'  时间范围: {first_date} 至 {last_date}')

                    # 检查年份是否正确
                    first_year = first_date.year
                    last_year = last_date.year

                    if last_year == current_year:
                        print(f'  ✅ 最新数据年份正确: {last_year}')
                    else:
                        print(f'  ❌ 最新数据年份错误: {last_year}（应该是 {current_year}）')
                        all_passed = False

                    # 显示最新的 3 根 K 线
                    print(f'\n  最新的 3 根 K 线:')
                    for idx, row in df.tail(3).iterrows():
                        print(f'    {row["date"]} - O:{row["open"]:.2f} H:{row["high"]:.2f} L:{row["low"]:.2f} C:{row["close"]:.2f} V:{int(row.get("volume", 0))}')

                    print()
                    break
            except Exception as e:
                continue
        else:
            print(f'✗ 无法获取数据\n')
            all_passed = False

    quote_ctx.close()

    print(f'{"="*60}')
    if all_passed:
        print('✅ 所有测试通过！K 线时间范围修复成功。')
    else:
        print('❌ 部分测试失败，请检查。')
    print(f'{"="*60}')

    return all_passed


if __name__ == '__main__':
    test_kline_timerange()
