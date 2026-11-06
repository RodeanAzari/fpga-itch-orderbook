# Committed test fixtures (< 5 MB total)

| File | Bytes | SHA-256 | Made from |
|---|---|---|---|
| `2019-10-18_AAPL_head50k.itch` | 1,731,117 | `a6cbe1da…7a354d3534` | First 50,000 messages of the AAPL extract of `S101819-v50.txt.gz` (`data/MANIFEST.md`; extract made by `analysis/itch_extract.py`). Length-prefixed ITCH 5.0. Used by `tb/cocotb/test_order_table.py`. |

Recreate: `python -c` over `model.itch.iter_raw` of `/mnt/d/itch/derived/2019-10-18_AAPL.itch`,
keeping the first 50,000 messages with their 2-byte length prefixes.
