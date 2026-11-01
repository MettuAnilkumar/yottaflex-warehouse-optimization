"""CLI entrypoint: python -m yottaflex.generate --scale full --out data
Deterministic under fixed seed; writes CSV/JSON + manifest + metrics."""
import argparse
import hashlib
import json
import platform
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .config import SCALE_FULL, SCALE_STARTER, SEED
from .master import MasterData
from .transactions import (build_pick_events, build_replenishments, export as tx_export)
from .slotting import build_slot_assignments, simulate_pick_paths, export as slot_export
from .anomalies import inject, export as anomaly_export
from .assignment_history import build_assignment_history, export as history_export
from .assignment_history import apply_assignment_moves
from .validation import run as validate
from .partners import build_partner_records, export as partner_export


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(prog="yottaflex")
    ap.add_argument("--scale", choices=["starter", "full"], default="full")
    ap.add_argument("--out", default="data")
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args()
    outdir = Path(args.out)

    if args.validate_only:
        print(json.dumps(validate(outdir, require_artifacts=True), indent=2))
        return

    scale = SCALE_FULL if args.scale == "full" else SCALE_STARTER
    master = MasterData(scale)
    master.build()
    build_partner_records(master)

    # Generate baseline (random valid) slotting for comparison
    baseline_assignments = build_slot_assignments(master, use_optimized=False)
    # Reset locations for optimized run
    for loc in master.locations:
        loc["occupancy_status"] = "EMPTY"
    # Generate optimized (smart) slotting
    build_slot_assignments(master, use_optimized=True)
    apply_assignment_moves(master)
    build_pick_events(master)
    build_replenishments(master)
    inject(master)
    build_assignment_history(master)

    master.export(outdir)
    tx_export(master, outdir)
    slot_export(master, outdir)
    anomaly_export(master, outdir)
    history_export(master, outdir)
    partner_export(master, outdir)

    # Export JSON files
    json_dir = outdir / "json"
    json_dir.mkdir(exist_ok=True)
    json_files = [
        ("warehouse.json", master.warehouses),
        ("zone.json", master.zones),
        ("location.json", master.locations),
        ("sku.json", master.skus),
        ("sku_profile.json", master.sku_profiles),
        ("order.json", master.orders),
        ("order_line.json", master.order_lines),
        ("pick_event.json", master.pick_events),
        ("replenishment.json", master.replenishments),
        ("replenishment_event.json", master.replenishment_events),
        ("slot_assignment.json", master.slot_assignments),
        ("ground_truth.json", master.ground_truth),
        ("assignment_event.json", master.assignment_events),
        ("supplier.json", master.suppliers),
        ("carrier.json", master.carriers),
        ("inbound_shipment.json", master.inbound_shipments),
        ("inbound_event.json", master.inbound_events),
    ]
    for fname, data in json_files:
        (json_dir / fname).write_text(json.dumps(data, indent=2), encoding="utf-8")

    metrics = simulate_pick_paths(master, baseline_assignments)
    active_skus = {s["sku_id"] for s in master.slot_assignments
                    if s["status"] == "active" and s["decision"] == "ASSIGNED"}
    pick_counts_by_shift = {}
    pick_counts_by_peak = {"NORMAL": 0, "PEAK": 0}
    peak_by_order = {row["order_id"]: row["peak_period"] for row in master.orders}
    for event in master.pick_events:
        pick_counts_by_shift[event["shift"]] = pick_counts_by_shift.get(event["shift"], 0) + 1
        period = peak_by_order[event["order_id"]]
        pick_counts_by_peak[period] += 1
    replen_labor = sum(float(row["labor_minutes"]) for row in master.replenishments)
    total_lines = len(master.order_lines)
    metrics.update({
        "throughput": {
            "order_lines": total_lines,
            "pick_events": len(master.pick_events),
            "order_line_slot_coverage_pct": round(
                100 * len(master.pick_events) / total_lines, 2) if total_lines else 0,
            "pick_events_by_shift": pick_counts_by_shift,
            "pick_events_by_peak_period": pick_counts_by_peak,
            "orders": len(master.orders),
            "orders_with_pick_events": len({e["order_id"] for e in master.pick_events}),
        },
        "replenishment_impact": {
            "tasks": len(master.replenishments),
            "total_labor_minutes": round(replen_labor, 2),
            "scheduled_labor_minutes": round(sum(float(row["scheduled_labor_minutes"])
                                                  for row in master.replenishments), 2),
            "deferred_labor_minutes": round(sum(float(row["deferred_labor_minutes"])
                                                 for row in master.replenishments), 2),
            "average_labor_minutes_per_task": round(
                replen_labor / len(master.replenishments), 2)
                if master.replenishments else 0,
            "over_capacity_tasks": sum(
                int(row["over_labor_capacity"]) for row in master.replenishments),
            "rescheduled_tasks": sum(row["schedule_decision"] == "RESCHEDULED"
                                      for row in master.replenishments),
            "next_shift_allocations": sum(row["reschedule_count"] == 1
                                           for row in master.replenishments),
            "reschedule_events": sum(event["event_type"] == "RESCHEDULED"
                                      for event in master.replenishment_events),
        },
        "slotting_coverage": {
            "skus": len(master.skus),
            "accepted_active_skus": len(active_skus),
            "coverage_pct": round(100 * len(active_skus) / len(master.skus), 2)
                if master.skus else 0,
        },
    })
    # Define throughput as pick lines per estimated direct labor hour. The
    # assumptions are explicit synthetic values rather than hidden constants.
    pick_handling_minutes = 1.5
    walking_speed_m_per_minute = 60.0
    pick_labor_minutes = (len(master.pick_events) * pick_handling_minutes +
                          sum(float(e["travel_distance_m"]) for e in master.pick_events)
                          / walking_speed_m_per_minute)
    metrics["throughput"].update({
        "estimated_pick_labor_minutes": round(pick_labor_minutes, 2),
        "estimated_pick_labor_hours": round(pick_labor_minutes / 60, 2),
        "picks_per_labor_hour": round(len(master.pick_events) / (pick_labor_minutes / 60), 2)
            if pick_labor_minutes else 0,
        "labor_assumptions": {
            "handling_minutes_per_pick": pick_handling_minutes,
            "walking_speed_m_per_minute": walking_speed_m_per_minute,
        },
    })

    # Capacity utilization is measured over valid, non-exception assigned bins.
    assignment_rows = master.slot_assignments
    labelled_incompatible_slots = {
        row["record_id"] for row in master.ground_truth
        if row["issue_type"] == "INCOMPATIBLE_COLOCATION" and row["entity"] == "SlotAssignment"
    }
    labelled_incompatible_locations = {
        row["record_id"] for row in master.ground_truth
        if row["issue_type"] == "INCOMPATIBLE_COLOCATION" and row["entity"] == "Location"
    }
    assignment_by_id = {row["slot_id"]: row for row in assignment_rows}
    labelled_incompatible_locations.update(
        assignment_by_id[row["record_id"]]["location_id"]
        for row in master.ground_truth
        if row["issue_type"] == "INCOMPATIBLE_COLOCATION"
        and row["entity"] == "SlotAssignment" and row["record_id"] in assignment_by_id)
    duplicate_slot_ids = {row["record_id"] for row in master.ground_truth
                          if row["issue_type"] == "DUPLICATE_ACTIVE_SLOT"}
    valid_assignments = [row for row in assignment_rows
                         if row["status"] == "active" and row["decision"] == "ASSIGNED"
                         and row["slot_id"] not in labelled_incompatible_slots | duplicate_slot_ids
                         and row["location_id"] not in labelled_incompatible_locations]
    sku_by_id = {row["sku_id"]: row for row in master.skus}
    location_by_id = {row["location_id"]: row for row in master.locations}
    used_volume = sum(float(sku_by_id[row["sku_id"]]["unit_volume_cm3"])
                      for row in valid_assignments)
    used_weight = sum(float(sku_by_id[row["sku_id"]]["unit_weight_kg"])
                      for row in valid_assignments)
    assigned_volume_capacity = sum(float(location_by_id[row["location_id"]]["capacity_volume_cm3"])
                                   for row in valid_assignments)
    assigned_weight_capacity = sum(float(location_by_id[row["location_id"]]["max_weight_kg"])
                                   for row in valid_assignments)
    metrics["capacity_utilization"] = {
        "assigned_locations": len(valid_assignments),
        "volume_utilization_pct": round(100 * used_volume / assigned_volume_capacity, 2)
            if assigned_volume_capacity else 0,
        "weight_utilization_pct": round(100 * used_weight / assigned_weight_capacity, 2)
            if assigned_weight_capacity else 0,
        "denominator": "capacity of valid assigned locations; one SKU unit per assigned bin",
    }

    sku_warehouse = {row["sku_id"]: row["warehouse_id"] for row in master.skus}
    replen_by_warehouse_shift = defaultdict(lambda: {"planned": 0.0, "delayed": 0.0,
                                                     "capacity": 480.0})
    replen_actual_shift = defaultdict(lambda: {"scheduled": 0.0, "remaining_capacity": 480.0})
    for task in master.replenishments:
        bucket = replen_by_warehouse_shift[(sku_warehouse[task["sku_id"]], task["required_date"],
                                            task["required_shift"])]
        bucket["planned"] += float(task["labor_minutes"])
        bucket["delayed"] += float(task["deferred_labor_minutes"])
        actual_key = (sku_warehouse[task["sku_id"]], task["scheduled_date"], task["scheduled_shift"])
        actual = replen_actual_shift[actual_key]
        actual["scheduled"] += float(task["scheduled_labor_minutes"])
    for actual in replen_actual_shift.values():
        actual["remaining_capacity"] = 480.0 - actual["scheduled"]
    metrics["replenishment_impact"]["labor_minutes_by_requested_warehouse_date_shift"] = {
        f"{warehouse_id}:{date}:{shift}": {key: round(value, 2) for key, value in bucket.items()}
        for (warehouse_id, date, shift), bucket in sorted(replen_by_warehouse_shift.items())
    }
    metrics["replenishment_impact"]["labor_minutes_by_scheduled_warehouse_date_shift"] = {
        f"{warehouse_id}:{date}:{shift}": {key: round(value, 2) for key, value in bucket.items()}
        for (warehouse_id, date, shift), bucket in sorted(replen_actual_shift.items())
    }
    by_issue = Counter(row["issue_type"] for row in master.ground_truth)
    by_severity = Counter(row["severity"] for row in master.ground_truth)
    by_resolution = Counter(row["resolution_status"] for row in master.ground_truth)
    metrics["exceptions"] = {
        "total": len(master.ground_truth),
        "by_issue_type": dict(sorted(by_issue.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "by_resolution_status": dict(sorted(by_resolution.items())),
        "labelled_compatibility_exception_cases": by_issue["INCOMPATIBLE_COLOCATION"],
        "accepted_compatibility_violations": 0,
        "blocked_aisle_reroute_cases": by_issue["BLOCKED_AISLE_PICK"],
        "accepted_blocked_path_violations": 0,
    }
    metrics["inbound"] = {
        "shipments": len(master.inbound_shipments),
        "shipment_events": len(master.inbound_events),
        "shipments_by_status": dict(sorted(Counter(
            row["status"] for row in master.inbound_shipments).items())),
        "received_quantity": sum(int(row["received_qty"])
                                  for row in master.inbound_shipments),
    }
    v = validate(outdir)

    from .sql_export import export_sql
    export_sql(outdir, outdir / "seed.sql")

    manifest = {
        "seed": SEED, "scale": args.scale,
        "python": platform.python_version(),
        "generator_version": "1.0.0",
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "generation_parameters": {
            "warehouses": scale.warehouses,
            "locations": scale.locations,
            "skus": scale.skus,
            "order_lines": scale.order_lines,
            "suppliers": len(master.suppliers),
            "carriers": len(master.carriers),
            "inbound_shipments": len(master.inbound_shipments),
            "inbound_events": len(master.inbound_events),
        },
        "data_provenance": "Synthetic data only; no external data sources used.",
        "source_manifest": [],
        "files": {},
    }
    (outdir / "metrics.json").write_text(json.dumps(
        {"validation": v, "pick_metrics": metrics}, indent=2))
    for path in sorted(outdir.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        file_entry = {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        if path.suffix == ".csv":
            file_entry["rows"] = sum(1 for _ in open(path, encoding="utf-8")) - 1
        elif path.suffix == ".json":
            try:
                file_entry["records"] = len(json.loads(path.read_text(encoding="utf-8")))
            except TypeError:
                file_entry["records"] = None
        manifest["files"][str(path.relative_to(outdir)).replace("\\", "/")] = file_entry
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(json.dumps({"scale": args.scale, "validation_passed": v["passed"],
                      "issues": v["issues"][:10], "pick_metrics": metrics,
                      "anomalies": len(master.ground_truth)}, indent=2))
    if not v["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
