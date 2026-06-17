"""直接测试 qstock"""
print("测试开始...")

# 方法1: 使用 qstock.get_data
print("\n【方法1】使用 qstock.get_data('stock_list')...")
try:
    import qstock as qs
    print("qstock导入成功")
    print("qstock版本:", getattr(qs, '__version__', 'unknown'))

    stock_list = qs.get_data('stock_list')
    print("✓ 获取成功！")
    print(f"股票数量: {len(stock_list)}")
    print(f"列名: {stock_list.columns.tolist()}")
    print(f"前3行:\n{stock_list.head(3)}")
except Exception as e:
    print(f"✗ 失败: {e}")
    import traceback
    traceback.print_exc()

# 方法2: 使用 get_code
print("\n\n【方法2】使用 get_code()...")
try:
    from qstock.data.trade import get_code
    print("get_code导入成功")

    code_list = get_code()
    print("✓ 获取成功！")
    print(f"股票数量: {len(code_list)}")
    print(f"列名: {code_list.columns.tolist()}")
    print(f"前3行:\n{code_list.head(3)}")
except Exception as e:
    print(f"✗ 失败: {e}")
    import traceback
    traceback.print_exc()

print("\n测试完成")
