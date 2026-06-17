"""快速测试 AKShare"""
print("测试 AKShare...")
print()

try:
    import akshare as ak
    print("✓ AKShare 导入成功")
    print()

    print("正在获取A股数据...")
    df = ak.stock_zh_a_spot_em()

    print(f"✓ 成功！获取 {len(df)} 只股票")
    print(f"  列数: {len(df.columns)}")
    print(f"  列名: {df.columns.tolist()}")
    print()
    print("前5只股票:")
    print(df.head().to_string())

    # 保存
    from datetime import datetime
    filename = f"stocks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(filename, index=False, encoding='utf-8-sig')
    print(f"\n已保存到: {filename}")

except ImportError:
    print("✗ AKShare 未安装")
except Exception as e:
    print(f"✗ 错误: {e}")
    import traceback
    traceback.print_exc()
