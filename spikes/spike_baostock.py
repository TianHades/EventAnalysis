#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spike: baostock 可达性 + index_component_sw 三级真实返回结构"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

print("=== baostock 可达性（独立 TCP 协议，可能绕过 HTTPS 代理）===")
try:
    import baostock as bs
    lg = bs.login()
    print(f"  login: code={lg.error_code} msg={lg.error_msg}")
    if lg.error_code == '0':
        rs = bs.query_history_k_data_plus("sh.600519", "date,code,open,high,low,close,volume,adjustflag",
                                           start_date="2024-01-02", end_date="2024-01-10", adjustflag="2")
        print(f"  query: code={rs.error_code} msg={rs.error_msg} fields={rs.fields}")
        rows = []
        while (rs.error_code == '0') and rs.next():
            rows.append(rs.get_row_data())
            if len(rows) >= 3: break
        print(f"  sample rows: {rows}")
        bs.logout()
except Exception as e:
    print(f"  EXC: {type(e).__name__}: {e}")

print("\n=== index_component_sw 三级代码真实返回结构 ===")
import akshare as ak
for code in ["850111", "850111.SI", "801010.SI"]:
    try:
        df = ak.index_component_sw(symbol=code)
        print(f"  {code}: shape={df.shape} cols={list(df.columns)}")
        print(f"    head:\n{df.head(2).to_string()[:300]}")
    except Exception as e:
        print(f"  {code}: EXC {type(e).__name__}: {str(e)[:200]}")
print("=== DONE ===")
