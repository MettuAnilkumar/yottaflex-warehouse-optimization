# YottaFlex Project Validation Report

**Prepared:** 1 November 2026  
**Scope:** Requirements review and verification of the seeded warehouse slotting pipeline.

## Coverage

The generator produces the ten required warehouse entities plus synthetic suppliers, carriers, inbound shipments, shipment events, and slot-assignment history. CSV, JSON, SQLite SQL, metrics, and a provenance manifest are produced at both starter and full scale. Every entity uses deterministic IDs and documented keys, relationships, units, lifecycle states, and validation rules. Supplier, carrier, shipment, and assignment event records retain their state history.

Slot eligibility applies hard checks for dimensions under permitted orientation, weight and volume capacity, hazard segregation, temperature compatibility, and location availability before ranking. Eligible locations receive the documented configurable score: velocity 30%, dock distance 25%, capacity utilization 15%, replenishment efficiency 15%, and operational fit 15%.

All five required exception classes are generated with separate ground-truth rows containing source IDs, affected IDs, reason, expected decision, severity, and resolution status. Incompatible co-location and duplicate-assignment cases are repeated across warehouses. Assignment history records activation, deactivation, retirement, reassignment activation, rejection, and exception detection, linked by move IDs. Blocked-aisle routes use Dijkstra on a deterministic synthetic aisle graph, excluding blocked nodes from transit while allowing a blocked pick face as the route endpoint. Replenishment work is prioritized and rescheduled into subsequent shifts within per-warehouse/date/shift capacity, with an append-only rescheduling event history.

## Verification results

- Clean-environment verification used Python **3.13.2** in a new virtual environment. `pip install -r requirements.txt` succeeded; starter generation and validation passed, as did `--validate-only` against the full and starter submission datasets.
- Automated tests: **10 passed in 5.69 seconds** in that virtual environment.
- Full scale: **20 warehouses, 10,000 locations, 5,000 SKUs, and 100,000 order lines**; validation passed.
- Starter scale: **3 warehouses, 400 locations, 150 SKUs, and 2,000 order lines**; validation passed.
- CSV and JSON row counts and entity IDs reconcile. Both SQL seed files load with SQLite foreign-key checks passing. The manifest verifies every deterministic output file and records generator version, seed, parameters, run timestamp, checksums, and synthetic-only provenance.
- Full pick-distance comparison uses the same **97,972 comparable pick events**, order/shift waves, nearest-neighbor tours, and Dijkstra aisle-graph distances: **6,600,529.6 m baseline** versus **5,850,081.2 m optimized**, an **11.37% reduction**.
- Slotting coverage is **4,897 of 5,000 SKUs (97.94%)**. The remaining SKUs have no eligible location after physical fit, capacity, compatibility, and availability checks.
- Estimated throughput is **23.40 picks per labor hour**, using the explicit synthetic assumption of 1.5 handling minutes per pick and 60 m/minute walking speed.
- Replenishment totals **164,215.88 labor minutes** across **4,897 tasks**. All tasks are allocated within 480-minute warehouse/date/shift capacity; **154,673.02 minutes** are delayed from their requested shift across **4,791 tasks**. Priority and rescheduling history are exported, with planned and scheduled loads and remaining shift capacity in metrics.
- The full ground truth contains **20 incompatible co-location** and **20 duplicate active assignment** cases across the 20 warehouses. Assignment history records **40 complete moves**, including deactivation, retirement, and replacement activation.
- Accepted assignments have **zero compatibility violations**; all blocked-aisle pick cases have a calculated reroute. Ground-truth metrics report exceptions by type, severity, and resolution.
- The test suite checks reproducibility by generating the starter dataset twice and comparing SHA-256 hashes for every output except the timestamped manifest. The fixed seed and manifest checksums support deterministic generation. No retained side-by-side evidence of two full-scale generations was available for this review, so a full-scale two-run checksum match is not claimed as independently verified.
- The full SQLite seed reports `integrity_check = ok` and **zero foreign-key violations**.

