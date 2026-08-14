#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spike: index_component_sw 到底能拉哪些行业成分股？一级行业代码用哪种格式？"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import akshare as ak

# 拿一级清单
first = ak.sw_index_first_info()
print("一级清单 codes:", first["行业代码"].tolist()[:6], "... 共", len(first))

def try_comp(code):
    try:
        df = ak.index_component_sw(symbol=code)
        return f"rows={len(df)} cols={list(df.columns)}"
    except Exception as e:
        return f"EXC {type(e).__name__}: {str(e)[:80]}"

# 试 aggregate + 几个一级行业 + 不同格式
for code in ["801001", "801001.SI", "801010", "801010.SI", "801030", "801030.SI", "801110", "801110.SI"]:
    print(f"  {code}: {try_comp(code)}")

# 看一个成功的返回结构（801001）列长啥样
print("\n--- 801001 成分股样本 ---")
try:
    df = ak.index_component_sw(symbol="801001")
    print(df.head(3).to_string())
    print("证券代码样例:", df["证券代码"].head(5).tolist() if "证券代码" in df.columns else "N/A")
except Exception as e:
    print("EXC", e)
