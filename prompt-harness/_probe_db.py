# -*- coding: utf-8 -*-
import sqlite3, os
db = r"C:\Users\24357\Desktop\AInovel Harness\小说系统\ainovel-write\data\ainovel.db"
c = sqlite3.connect(db)
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table'").fetchall()]
print("tables:", tables)
for t in tables:
    if 'preset' in t.lower() or 'api' in t.lower():
        cols = [r[1] for r in c.execute(f"pragma table_info({t})").fetchall()]
        print(f"\n{t} cols:", cols)
        n = c.execute(f"select count(*) from {t}").fetchone()[0]
        print(f"{t} rows: {n}")
        for row in c.execute(f"select * from {t} limit 20"):
            print("  ", row)
