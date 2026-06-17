"""
获取股票列表 - 简化版，带详细错误信息
"""

import sys
import traceback

print("=" * 60)
print("开始测试")
print("=" * 60)

# 测试1: 导入qstock
print("\n【测试1】导入 qstock")
try:
    import qstock as qs
    print("✓ qstock 导入成功")
    print(f"  qstock 路径: {qs.__file__}")
except Exception as e:
    print(f"✗ qstock 导入失败: {e}")
    traceback.print_exc()
    sys.exit(1)

# 测试2: 使用 qs.get_data('stock_list')
print("\n【测试2】使用 qs.get_data('stock_list')")
try:
    print("  调用 qs.get_data('stock_list')...")
    stock_list = qs.get_data('stock_list')

    print(f"✓ 调用成功")
    print(f"  返回类型: {type(stock_list)}")
    print(f"  是否为DataFrame: {isinstance(stock_list, type('pd'))}")

    # 尝试导入pandas来检查类型
    import pandas as pd
    print(f"  是否为DataFrame: {isinstance(stock_list, pd.DataFrame)}")
    print(f"  长度: {len(stock_list) if hasattr(stock_list, '__len__') else 'N/A'}")

    if isinstance(stock_list, pd.DataFrame):
        if stock_list.empty:
            print("  ✗ DataFrame 为空")
            print(f"  列名: {stock_list.columns.tolist()}")
        else:
            print(f"  ✓ DataFrame 不为空")
            print(f"  形状: {stock_list.shape}")
            print(f"  列名: {stock_list.columns.tolist()}")
            print(f"\n前3行:")
            print(stock_list.head(3).to_string())

            # 保存
            from datetime import datetime
            filename = f"stock_list_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            stock_list.to_csv(filename, index=False, encoding='utf-8-sig')
            print(f"\n✓ 已保存到: {filename}")
    else:
        print(f"  ✗ 返回的不是DataFrame: {type(stock_list)}")
        print(f"  内容: {str(stock_list)[:500]}")

except AttributeError as e:
    print(f"✗ AttributeError: {e}")
    print("  可能 qstock 没有 get_data 方法")
    traceback.print_exc()
except Exception as e:
    print(f"✗ 失败: {e}")
    traceback.print_exc()

# 测试3: 直接导入模块
print("\n【测试3】直接从 qstock.data.trade 导入")
try:
    from qstock.data.trade import get_code, market_realtime
    print("✓ 导入成功")

    print("\n  尝试 get_code()...")
    code_list = get_code()
    print(f"  ✓ get_code() 返回 {len(code_list)} 条数据")

    if not code_list.empty:
        print(f"  列名: {code_list.columns.tolist()}")
        print(f"  前3行:")
        print(code_list.head(3).to_string())

except Exception as e:
    print(f"✗ 失败: {e}")
    traceback.print_exc()

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
