import sqlite3, sys
con = sqlite3.connect("honeychain.db"); con.row_factory = sqlite3.Row
tables = ["users","beekeepers","hives","honey_batches","processing_records",
          "quality_reports","iot_readings","ownership_history",
          "production_records","ledger_blocks"]
which = sys.argv[1] if len(sys.argv) > 1 else "honey_batches"
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
if which not in tables:
    print("Tables:", ", ".join(tables)); sys.exit()
rows = con.execute(f"SELECT * FROM {which} LIMIT ?", (limit,)).fetchall()
if rows:
    print(" | ".join(rows[0].keys()))
    for r in rows: print(" | ".join(str(r[k])[:26] for k in rows[0].keys()))
else:
    print(f"{which}: (empty)")