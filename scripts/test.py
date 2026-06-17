import pandas as pd

# 读取原始Excel文件
file_path = r'D:\公司文档\CRM导入\客户经理变更记录表--0415.xlsx'
df = pd.read_excel(file_path)

# 数据清洗：去除客户id为空的行，转换数据类型
df_clean = df.dropna(subset=['客户id']).copy()
df_clean['客户id'] = df_clean['客户id'].astype(int)  # 客户id转为整数
df_clean['用户ID'] = df_clean['用户ID'].astype(int)  # 用户ID转为整数

# 按要求重命名列：客户id→client，用户ID→user
df_output = df_clean[['客户id', '用户ID']].rename(
    columns={'客户id': 'client', '用户ID': 'user'}
)

# 去重（确保没有重复的客户ID和用户ID组合）
df_output = df_output.drop_duplicates()

# 保存为CSV文件
csv_output_path = 'D:\公司文档\CRM导入\客户用户ID映射表.csv'
df_output.to_csv(csv_output_path, index=False, encoding='utf-8-sig')

# 输出处理结果
print("CSV文件生成完成！")
print(f"文件路径：{csv_output_path}")
print(f"\n数据概览：")
print(f"总记录数：{len(df_output)}")
print(f"前10条数据：")
print(df_output.head(10))
print(f"\n数据范围：")
print(f"client（客户ID）：{df_output['client'].min()} - {df_output['client'].max()}")
print(f"user（用户ID）：{df_output['user'].min()} - {df_output['user'].max()}")