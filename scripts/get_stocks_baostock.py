"""
使用 Baostock 获取A股股票列表
Baostock 提供免费的证券数据
"""

import pandas as pd
from datetime import datetime, timedelta
import sys


def get_all_stocks_from_baostock():
    """使用Baostock获取A股股票列表"""
    print("=" * 60)
    print("使用 Baostock 获取A股股票列表")
    print("=" * 60)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    try:
        import baostock as bs
        print("✓ Baostock 已安装")
    except ImportError:
        print("✗ Baostock 未安装")
        print("  请运行: pip install baostock")
        return pd.DataFrame()

    # 登录系统
    print("\n正在登录 Baostock...")
    lg = bs.login()

    if lg.error_code != '0':
        print(f"✗ 登录失败: {lg.error_msg}")
        return pd.DataFrame()

    print(f"✓ 登录成功")

    # 获取日期（使用最近一个交易日）
    date_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    # 如果是周末，往前推到工作日
    while datetime.strptime(date_str, '%Y-%m-%d').weekday() >= 5:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)
        date_str = date_obj.strftime('%Y-%m-%d')

    print(f"查询日期: {date_str}")

    # 查询所有股票
    print("\n正在获取股票列表...")
    rs = bs.query_all_stock(day=date_str)

    if rs.error_code != '0':
        print(f"✗ 查询失败: {rs.error_msg}")
        bs.logout()
        return pd.DataFrame()

    # 解析数据
    data_list = []
    count = 0

    while (rs.error_code == '0') & rs.next():
        data_list.append(rs.get_row_data())
        count += 1

        # 显示进度
        if count % 500 == 0:
            print(f"  已获取 {count} 只股票...")

    # 登出
    bs.logout()
    print(f"\n✓ 成功获取 {count} 只股票")

    if not data_list:
        print("✗ 数据为空")
        return pd.DataFrame()

    # 创建DataFrame
    df = pd.DataFrame(data_list, columns=rs.fields)
    print(f"  字段: {df.columns.tolist()}")

    # 数据清洗
    print("\n正在清洗数据...")

    # 只保留A股
    # sh.600xxx - 沪市主板
    # sh.688xxx - 沪市科创板
    # sz.000xxx - 深市主板
    # sz.001xxx - 深市主板
    # sz.300xxx - 深市创业板

    def get_market_type(code):
        """获取市场类型"""
        if code.startswith('sh.600'):
            return '沪市主板'
        elif code.startswith('sh.688'):
            return '沪市科创板'
        elif code.startswith('sz.000'):
            return '深市主板'
        elif code.startswith('sz.001'):
            return '深市主板'
        elif code.startswith('sz.300'):
            return '深市创业板'
        else:
            return '其他'

    df['market_type'] = df['code'].apply(get_market_type)

    # 只保留A股
    a_stock_mask = df['market_type'] != '其他'
    df = df[a_stock_mask]

    print(f"  去除非A股后: {len(df)} 只")

    # 提取股票代码（去除市场前缀）
    df['stock_code'] = df['code'].apply(lambda x: x.split('.')[1])
    df['market'] = df['code'].apply(lambda x: x.split('.')[0].upper())

    # 重新排列列
    df = df[['stock_code', 'market', 'code', 'code_name', 'ipoDate', 'outDate', 'type', 'status', 'market_type']]

    # 重命名列
    df = df.rename(columns={
        'code': 'full_code',
        'code_name': '名称',
        'ipoDate': '上市日期',
        'outDate': '退市日期',
        'type': '类型',
        'status': '状态'
    })

    print(f"\n✓ 数据清洗完成")

    # 市场分布
    print("\n市场分布:")
    market_dist = df['market_type'].value_counts()
    for market, count in market_dist.items():
        print(f"  {market}: {count} 只")

    # 显示前几行
    print(f"\n前10只股票:")
    print(df.head(10).to_string())

    # 保存数据
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"all_a_stocks_baostock_{timestamp}.csv"
    df.to_csv(filename, index=False, encoding='utf-8-sig')
    print(f"\n✓ 数据已保存到: {filename}")

    return df


def analyze_stocks(stock_list):
    """分析股票数据"""
    print("\n" + "=" * 60)
    print("数据分析")
    print("=" * 60)

    if stock_list.empty:
        print("无数据可分析")
        return

    print(f"股票总数: {len(stock_list)}")

    # 市场分布
    print("\n市场分布:")
    market_dist = stock_list['market_type'].value_counts()
    for market, count in market_dist.items():
        pct = count / len(stock_list) * 100
        print(f"  {market}: {count} 只 ({pct:.1f}%)")

    # 状态分布
    print("\n状态分布:")
    status_dist = stock_list['状态'].value_counts()
    for status, count in status_dist.items():
        pct = count / len(stock_list) * 100
        print(f"  {status}: {count} 只 ({pct:.1f}%)")

    # 上市日期分析
    if '上市日期' in stock_list.columns:
        print("\n上市日期分析:")
        # 过滤掉无效日期
        valid_dates = stock_list[stock_list['上市日期'] != '']
        if not valid_dates.empty:
            print(f"  有上市日期的股票: {len(valid_dates)} 只")

            # 最近上市的股票
            valid_dates = valid_dates.copy()
            valid_dates['上市日期'] = pd.to_datetime(valid_dates['上市日期'])
            recent = valid_dates.nlargest(10, '上市日期')
            print(f"\n  最近10只上市股票:")
            for _, row in recent.iterrows():
                print(f"    {row['名称']}({row['stock_code']}): {row['上市日期'].strftime('%Y-%m-%d')}")


def main():
    """主函数"""
    result = get_all_stocks_from_baostock()

    if not result.empty:
        analyze_stocks(result)

        print("\n" + "=" * 60)
        print("程序执行完成！")
        print("=" * 60)
        print("\n提示:")
        print("- 这是A股股票的基本信息列表")
        print("- 如需获取历史价格数据，可使用Baostock的历史数据接口")
        print("- 更多信息请访问: http://baostock.com/")

        return result
    else:
        print("\n程序退出，未能获取股票数据")
        return pd.DataFrame()


if __name__ == '__main__':
    main()
