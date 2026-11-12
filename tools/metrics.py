"""ブリッジのログ(標準出力を保存したもの)から稼働状況を集計する。使い方: python3 tools/metrics.py <log>"""
import ast, re, statistics as st, sys

def main(path):
    conn = disc = 0
    rows = []
    first = []
    for line in open(path, encoding="utf-8", errors="replace"):
        if "接続: (" in line: conn += 1
        if "切断" in line: disc += 1
        if "訳し始めから最初の音まで" in line:
            m = re.search(r"まで ([\d.]+)s", line)
            if m: first.append(float(m.group(1)))
        m = re.search(r"端末: (\{.*\})", line)
        if m:
            try: d = ast.literal_eval(m.group(1))
            except Exception: continue
            if "mic_sent" in d: rows.append(d)
    out = {"connections": conn, "disconnects": disc, "samples": len(rows)}
    if rows:
        def col(k): return [r[k] for r in rows if k in r]
        out.update(
            mic_sent_per_10s_mean=round(st.mean(col("mic_sent")), 1),
            rec_fail_total=sum(col("rec_fail")),
            send_max_ms_p95=sorted(col("send_max_ms"))[int(len(rows) * .95) - 1],
            loop_max_ms_p95=sorted(col("loop_max_ms"))[int(len(rows) * .95) - 1],
            rssi_mean=round(st.mean(col("rssi")), 1),
            heap_min=min(col("heap")), heap_max=max(col("heap")),
            cam_per_10s_mean=round(st.mean(col("cam")), 1) if col("cam") else None,
        )
    if first:
        out.update(first_audio_mean_s=round(st.mean(first), 2), first_audio_n=len(first))
    for k, v in out.items(): print(f"{k}: {v}")

if __name__ == "__main__":
    main(sys.argv[1])
