"""会話ログ(bridge/logs/日付.jsonl)から、その日の通訳の件数・言語・エンジンを集計する。python3 tools/summary.py [YYYY-MM-DD]"""
import collections, json, os, sys, time
day = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d")
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bridge", "logs", day + ".jsonl")
if not os.path.exists(path):
    sys.exit(f"{path} がありません")
rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
print(f"{day}: 通訳 {len(rows)} 件")
for k, n in collections.Counter(f"{r['from']}→{r['to']}" for r in rows).most_common(): print(f"  {k}: {n}")
for k, n in collections.Counter(r.get("engine", "?") for r in rows).most_common(): print(f"  エンジン {k}: {n}")
print("  最後の3件:")
for r in rows[-3:]: print(f"   {r['ts'][11:]} {r['orig']} ⇝ {r['trans']}")
