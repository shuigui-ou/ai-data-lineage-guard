#!/usr/bin/env python3
# 一次性生成演示 xlsx（用完即删）
import openpyxl

# 1) 正常值版本（data.xlsx）
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Sales"
ws.append(["id", "product", "amount", "qty"])
for r in [(1, "A", 100, 2), (2, "B", 250, 1), (3, "C", 300, 3), (4, "D", 150, 2), (5, "E", 200, 4)]:
    ws.append(list(r))
wb.save("samples/data.xlsx")

# 2) 公式无缓存值版本（data_formula_nocache.xlsx）
# 把首行 amount 写成公式 =2*50，但未用 Excel 计算保存 -> data_only 读到 None
wb2 = openpyxl.Workbook()
ws2 = wb2.active
ws2.title = "Sales"
ws2.append(["id", "product", "amount", "qty"])
ws2.append([1, "A", "=2*50", 2])  # 公式，无缓存
for r in [(2, "B", 250, 1), (3, "C", 300, 3), (4, "D", 150, 2), (5, "E", 200, 4)]:
    ws2.append(list(r))
wb2.save("samples/data_formula_nocache.xlsx")
print("demo xlsx generated")
