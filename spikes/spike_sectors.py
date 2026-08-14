#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spike: 哪些行业/概念成分股源现在可达？（补充候选召回，治中船特气式遗漏）"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import akshare as ak

def t(name, fn):
    try:
        df = fn()
        info = f"shape={df.shape} cols={list(df.columns)[:6]}"
        print(f"[OK] {name}: {info}")
        return df
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {str(e)[:100]}")
        return None

# 1. sina 行业板块（sina 可达，最可能成功）
print("=== sina 行业板块 ===")
sectors = t("stock_sector_spot(新浪行业)", lambda: ak.stock_sector_spot(indicator="新浪行业"))
if sectors is not None and not sectors.empty:
    # 取第一个板块的成分股
    sec_col = [c for c in sectors.columns if "板块" in c or "name" in c.lower() or "symbol" in c.lower()]
    print("  板块表示例:", sectors.head(3).to_dict("records"))
    # stock_sector_detail 需要 sector 参数（板块代码/名）
    try:
        first_sec = sectors.iloc[0]
        # 找可能的板块代码列
        code_col = None
        for c in sectors.columns:
            v = str(first_sec[c])
            if any(x in c.lower() for x in ["code","symbol","板块"]) or v.startswith(("new_ptn","ptn")):
                code_col = c; break
        if code_col:
            sec_val = first_sec[code_col]
            print(f"  尝试 detail(sector={sec_val}) ...")
            det = t("  stock_sector_detail", lambda: ak.stock_sector_detail(sector=sec_val))
    except Exception as e:
        print(f"  detail 失败: {e}")

# 2. eastmoney 概念/行业板块（之前 push2 不可达，重测）
print("\n=== eastmoney 行业/概念板块（重测可达性）===")
t("stock_board_industry_name_em", lambda: ak.stock_board_industry_name_em())
t("stock_board_concept_name_em", lambda: ak.stock_board_concept_name_em())

# 3. 申万成分股重试（不同格式）
print("\n=== 申万 index_component_sw 重试 ===")
for code in ["801050", "801050.SI", "801010", "801001"]:
    try:
        df = ak.index_component_sw(symbol=code)
        print(f"[OK] {code}: shape={df.shape} cols={list(df.columns)}")
        if len(df)>0 and "证券代码" in df.columns:
            print(f"    样例: {df['证券代码'].head(3).tolist()}")
    except Exception as e:
        print(f"[FAIL] {code}: {type(e).__name__}: {str(e)[:80]}")
