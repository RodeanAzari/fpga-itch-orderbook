# Off-repo data files

Never committed. Place them in `D:\itch` (`/mnt/d/itch` in WSL) or pass the path explicitly.

| File | Trading date | Source | Retrieved | Bytes | SHA-256 |
|---|---|---|---|---|---|
| `S101819-v50.txt.gz` | 2019-10-18 | `https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/S101819-v50.txt.gz` | 2026-11-02 | 3,951,201,663 | `390acb4df5a01d3d8e6c34557858604ac7c655c8edc0a9e312da3c032c5d8da4` |
| `S121225-v50.txt.gz` | 2025-12-12 | `https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/S121225-v50.txt.gz` | 2026-11-10 | 12,551,644,054 | `46f42fb12f0e659814416713c4e22985dba1b062b0c13097ea9b5b003d612798` |

NASDAQ publishes no checksum for these files. Integrity rests on `gzip -t` passing and the byte
count matching the directory listing. The SHA-256 is ours, recorded so later runs can prove
they used the same bytes.
