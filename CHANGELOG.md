# CHANGELOG

## V1.2.0 / 2026-11-12
- Fix: RF measurement key must never overwrite PCB Seq; RF machine ID stays stable.
- Fix: MES status has an independent class and is excluded from functional yield and retest denominator.
- Add: first functional fail rate, final functional fail rate, functional retest rate, failure-retest recovery rate; rate denominators documented.
- Add: priority sorting (open FAIL > recovered FAIL > other retest > MES > first PASS).
- Add: explicit MES audit rows and source traceability.
- Add: 10 automated regression tests; fast OOXML report writer with approved 32-sheet layout and scrubbed empty template.
- Build: GitHub Actions Windows EXE with embedded clean template, Python 3.11.
- Known gap: detail tabs 16A/26/27/29 not fully reconstructed; no full cell-by-cell parity confirmation.
