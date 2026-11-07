"""runs.txt (sweep.sh) -> CSV of per-run counter deltas from the rx_stats UART lines."""
import sys
F = {"udp_ok": 8, "ovf_lost": 7, "seq_err": 13, "seq_lost": 14, "crc_err": 3}  # 1-based after 'RX'
lines = open(sys.argv[1]).read().split("\n")
print("label,pps,sent,udp_ok,ovf_lost,crc_err,seq_err,seq_lost,seq_err_pct")
label = sys.argv[2] if len(sys.argv) > 2 else ""
for i, l in enumerate(lines):
    if not l.startswith("pps="):
        continue
    kv = dict(x.split("=") for x in l.split())
    b = [int(x, 16) for x in lines[i + 1].split()[2:]]
    a = [int(x, 16) for x in lines[i + 2].split()[2:]]
    d = {k: a[j - 1] - b[j - 1] for k, j in F.items()}
    print(f"{label},{kv['pps']},{kv['sent']},{d['udp_ok']},{d['ovf_lost']},{d['crc_err']},"
          f"{d['seq_err']},{d['seq_lost']},{100 * d['seq_err'] / int(kv['sent']):.4f}")
