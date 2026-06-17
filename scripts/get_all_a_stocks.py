"""
获取所有A股股票数据
使用 qstock 库获取所有A股列表和历史数据
"""

from qstock_strategy_optimizer import StrategyOptimizer
import pandas as pd
from datetime import datetime
import os
import time


def get_all_stocks_list():
    """
    获取所有A股股票列表
    
    Returns:
        包含所有股票信息的DataFrame
    """
    print("=" * 60)
    print("获取A股股票列表")
    print("=" * 60)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 方法1: 使用AKShare（推荐）
    print("正在尝试使用 AKShare 获取股票列表...")
    try:
        import akshare as ak
        print("✓ AKShare 已安装")
        
        print("正在获取数据，请稍候...")
        stock_list = ak.stock_zh_a_spot_em()
        
        if not stock_list.empty:
            print(f"✓ 成功获取 {len(stock_list)} 只股票")
            print(f"  列名: {stock_list.columns.tolist()}")
            
            # 显示前10只股票
            print(f"\n前10只股票预览:")
            print(stock_list.head(10).to_string())
            
            # 保存
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"all_a_stocks_list_{timestamp}.csv"
            stock_list.to_csv(filename, index=False, encoding='utf-8-sig')
            print(f"\n股票列表已保存到: {filename}")
            
            return stock_list
        else:
            print("✗ AKShare 返回空数据")
            return pd.DataFrame()
            
    except ImportError:
        print("✗ AKShare 未安装")
        print("  正在安装 AKShare...")
        import subprocess
        import sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "akshare"])
        print("✓ AKShare 安装完成，请重新运行程序")
        return pd.DataFrame()
    except Exception as e:
        print(f"✗ AKShare 获取失败: {e}")
        print(f"  将尝试备用方法...")
    
    # 方法2: 备用方法 - 使用qstock的优化器
    print("\n正在尝试使用 qstock 获取股票列表...")
    try:
        from qstock_strategy_optimizer import StrategyOptimizer
        optimizer = StrategyOptimizer()
        stock_list = optimizer.get_all_stocks()
        
        if not stock_list.empty:
            print(f"✓ 成功获取 {len(stock_list)} 只股票")
            
            # 保存
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"all_a_stocks_list_{timestamp}.csv"
            stock_list.to_csv(filename, index=False, encoding='utf-8-sig')
            print(f"股票列表已保存到: {filename}")
            
            return stock_list
    except Exception as e:
        print(f"✗ qstock 获取失败: {e}")
    
    print("\n所有方法均失败，无法获取股票列表")
    return pd.DataFrame()


def get_stocks_hist_data(stock_list, days=90, limit=None):
    """
    获取指定股票的历史数据
    
    Args:
        stock_list: 股票列表DataFrame
        days: 获取多少天的历史数据
        limit: 最多获取多少只股票（None表示全部）
    
    Returns:
        包含历史数据的字典 {code: DataFrame}
    """
    print("=" * 60)
    print("获取股票历史数据")
    print("=" * 60)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"历史天数: {days}")
    
    if limit:
        print(f"获取数量限制: {limit} 只股票")
        stock_list = stock_list.head(limit)
    
    print(f"计划获取: {len(stock_list)} 只股票")
    print()
    
    # 创建优化器
    optimizer = StrategyOptimizer()
    
    # 创建保存目录
    save_dir = "stock_hist_data"
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)
        print(f"创建保存目录: {save_dir}")
    
    # 存储结果
    hist_data_dict = {}
    success_count = 0
    failed_count = 0
    failed_codes = []
    
    print("开始获取历史数据...")
    print("-" * 60)
    
    total_stocks = len(stock_list)
    
    for idx, stock in stock_list.iterrows():
        code = stock.get('代码', '')
        name = stock.get('名称', '')
        
        if not code:
            continue
        
        progress = (idx + 1) / total_stocks * 100
        
        try:
            # 获取历史数据
            hist_data = optimizer.get_stock_data(code, days=days)
            
            if not hist_data.empty:
                # 保存到字典
                hist_data_dict[code] = hist_data
                
                # 保存到CSV文件
                filename = os.path.join(save_dir, f"{code}_{name.replace('*', '')}_{datetime.now().strftime('%Y%m%d')}.csv")
                hist_data.to_csv(filename, index=False, encoding='utf-8-sig')
                
                success_count += 1
                
                # 每100只股票显示一次进度
                if (idx + 1) % 100 == 0:
                    print(f"进度: {progress:.1f}% ({idx + 1}/{total_stocks}) | 成功: {success_count} | 失败: {failed_count}")
            else:
                failed_count += 1
                failed_codes.append(code)
        
        except Exception as e:
            failed_count += 1
            failed_codes.append(code)
            if len(failed_codes) <= 10:  # 只显示前10个失败的
                print(f"  获取 {code} 失败: {e}")
        
        # 避免请求过快
        time.sleep(0.3)
    
    print()
    print("=" * 60)
    print("数据获取完成！")
    print(f"总计: {total_stocks} 只股票")
    print(f"成功: {success_count} 只")
    print(f"失败: {failed_count} 只")
    
    if failed_codes:
        print(f"失败股票代码前20个: {failed_codes[:20]}")
    
    # 保存失败列表
    if failed_codes:
        failed_file = f"failed_codes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(failed_file, 'w', encoding='utf-8') as f:
            for code in failed_codes:
                f.write(f"{code}\n")
        print(f"失败代码列表已保存到: {failed_file}")
    
    return hist_data_dict


def analyze_stock_list(stock_list):
    """
    分析股票列表的基本统计信息
    
    Args:
        stock_list: 股票列表DataFrame
    """
    print()
    print("=" * 60)
    print("股票列表分析")
    print("=" * 60)
    
    # 基本统计
    print(f"股票总数: {len(stock_list)}")
    
    # 价格分析
    if '最新价' in stock_list.columns:
        print(f"\n最新价统计:")
        print(f"  平均: {stock_list['最新价'].mean():.2f} 元")
        print(f"  中位数: {stock_list['最新价'].median():.2f} 元")
        print(f"  最低: {stock_list['最新价'].min():.2f} 元")
        print(f"  最高: {stock_list['最新价'].max():.2f} 元")
        
        # 价格区间分布
        price_ranges = [
            (0, 10, "10元以下"),
            (10, 30, "10-30元"),
            (30, 50, "30-50元"),
            (50, 100, "50-100元"),
            (100, float('inf'), "100元以上")
        ]
        
        print(f"\n价格区间分布:")
        for min_p, max_p, label in price_ranges:
            count = len(stock_list[(stock_list['最新价'] >= min_p) & (stock_list['最新价'] < max_p)])
            pct = count / len(stock_list) * 100
            print(f"  {label}: {count} 只 ({pct:.1f}%)")
    
    # 涨跌幅分析
    if '涨跌幅' in stock_list.columns:
        print(f"\n涨跌幅统计:")
        print(f"  平均: {stock_list['涨跌幅'].mean():.2f}%")
        print(f"  上涨: {len(stock_list[stock_list['涨跌幅'] > 0])} 只")
        print(f"  下跌: {len(stock_list[stock_list['涨跌幅'] < 0])} 只")
        print(f"  平盘: {len(stock_list[stock_list['涨跌幅'] == 0])} 只")
        
        # 涨停股票
        limit_up = stock_list[stock_list['涨跌幅'] >= 9.8]
        print(f"\n涨停股票: {len(limit_up)} 只")
        if not limit_up.empty:
            print("涨停股票列表:")
            for _, stock in limit_up.head(10).iterrows():
                print(f"  {stock.get('名称', '')}({stock.get('代码', '')}): {stock.get('涨跌幅', 0):.2f}%")
    
    # 市值分析
    if '总市值' in stock_list.columns:
        print(f"\n总市值统计:")
        total_market_cap = stock_list['总市值'].sum()
        print(f"  总市值: {total_market_cap/100000000000:.2f} 万亿")
        print(f"  平均市值: {stock_list['总市值'].mean()/100000000:.2f} 亿")
        print(f"  最大市值: {stock_list['总市值'].max()/100000000:.2f} 亿")
    
    # 成交额分析
    if '成交额' in stock_list.columns:
        print(f"\n成交额统计:")
        total_turnover = stock_list['成交额'].sum()
        print(f"  总成交额: {total_turnover/100000000:.2f} 亿")
        print(f"  平均成交额: {stock_list['成交额'].mean()/10000:.2f} 万")


if __name__ == '__main__':
    print("\n")
    print("#" * 60)
    print("# A股股票数据获取系统")
    print("#" * 60)
    print()
    
    # 步骤1: 获取股票列表
    stock_list = get_all_stocks_list()
    
    if stock_list.empty:
        print("无法获取股票列表，程序退出！")
        exit(1)
    
    # 步骤2: 分析股票列表
    analyze_stock_list(stock_list)
    
    # 步骤3: 询问是否获取历史数据
    print()
    print("=" * 60)
    print("是否获取股票历史数据？")
    print("警告: 获取所有股票的历史数据需要较长时间！")
    print("=" * 60)
    print()
    print("选项:")
    print("  1. 获取前100只股票的历史数据（测试用）")
    print("  2. 获取前500只股票的历史数据")
    print("  3. 获取所有股票的历史数据（耗时较长）")
    print("  0. 跳过，不获取历史数据")
    print()
    
    choice = input("请输入选项 (0-3): ").strip()
    
    if choice == '1':
        print("\n获取前100只股票的历史数据...")
        get_stocks_hist_data(stock_list, days=90, limit=100)
    elif choice == '2':
        print("\n获取前500只股票的历史数据...")
        get_stocks_hist_data(stock_list, days=90, limit=500)
    elif choice == '3':
        print("\n获取所有股票的历史数据...")
        print("这可能需要数小时时间，请耐心等待...")
        confirm = input("确认继续？(y/n): ").strip().lower()
        if confirm == 'y':
            get_stocks_hist_data(stock_list, days=90, limit=None)
        else:
            print("已取消获取历史数据")
    elif choice == '0':
        print("已跳过获取历史数据")
    else:
        print("无效选项，已跳过获取历史数据")
    
    print()
    print("=" * 60)
    print("程序执行完成！")
    print("=" * 60)
