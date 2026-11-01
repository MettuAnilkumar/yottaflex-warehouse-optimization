"""Validation: PK uniqueness, FK integrity, enums, ordering, reconciliation."""
import csv
import datetime as dt
import re
import json
import hashlib
from collections import Counter, defaultdict
from pathlib import Path


def _load(outdir, name):
    with open(outdir / name, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def run(outdir: Path, require_artifacts: bool = False) -> dict:
    outdir = Path(outdir)
    issues = []

    def check(cond, msg):
        if not cond:
            issues.append(msg)

    wh = _load(outdir, "warehouse.csv")
    zn = _load(outdir, "zone.csv")
    loc = _load(outdir, "location.csv")
    sku = _load(outdir, "sku.csv")
    prof = _load(outdir, "sku_profile.csv")
    order = _load(outdir, "order.csv")
    ol = _load(outdir, "order_line.csv")
    pe = _load(outdir, "pick_event.csv")
    rep = _load(outdir, "replenishment.csv")
    rep_events = _load(outdir, "replenishment_event.csv")
    sa = _load(outdir, "slot_assignment.csv")
    ae = _load(outdir, "assignment_event.csv")
    gt = _load(outdir, "ground_truth.csv")
    suppliers = _load(outdir, "supplier.csv")
    carriers = _load(outdir, "carrier.csv")
    inbound = _load(outdir, "inbound_shipment.csv")
    inbound_events = _load(outdir, "inbound_event.csv")

    json_sources = {
        "warehouse.json": ("warehouse_id", wh),
        "zone.json": ("zone_id", zn),
        "location.json": ("location_id", loc),
        "sku.json": ("sku_id", sku),
        "sku_profile.json": ("profile_id", prof),
        "order.json": ("order_id", order),
        "order_line.json": ("line_id", ol),
        "pick_event.json": ("pick_id", pe),
        "replenishment.json": ("replenishment_id", rep),
        "replenishment_event.json": ("event_id", rep_events),
        "slot_assignment.json": ("slot_id", sa),
        "assignment_event.json": ("event_id", _load(outdir, "assignment_event.csv")),
        "ground_truth.json": (None, gt),
        "supplier.json": ("supplier_id", suppliers),
        "carrier.json": ("carrier_id", carriers),
        "inbound_shipment.json": ("shipment_id", inbound),
        "inbound_event.json": ("event_id", inbound_events),
    }
    for filename, (key, csv_rows) in json_sources.items():
        path = outdir / "json" / filename
        if not path.exists():
            check(False, f"export: missing {path.relative_to(outdir)}")
            continue
        try:
            json_rows = json.loads(path.read_text(encoding="utf-8"))
            check(isinstance(json_rows, list) and len(json_rows) == len(csv_rows),
                  f"export: row count mismatch for {filename}")
            if key and isinstance(json_rows, list):
                check({row.get(key) for row in json_rows} == {row.get(key) for row in csv_rows},
                      f"export: ID mismatch for {filename}")
        except (json.JSONDecodeError, OSError):
            check(False, f"export: invalid JSON {filename}")

    if require_artifacts:
        manifest_path = outdir / "manifest.json"
        sql_path = outdir / "seed.sql"
        check(manifest_path.exists(), "export: missing manifest.json")
        check(sql_path.exists(), "export: missing seed.sql")
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                check(bool(manifest.get("run_timestamp_utc")), "manifest: missing run timestamp")
                check(bool(manifest.get("generator_version")), "manifest: missing generator version")
                check(bool(manifest.get("generation_parameters")), "manifest: missing generation parameters")
                check("source_manifest" in manifest, "manifest: missing source manifest")
                listed = manifest.get("files", {})
                actual_paths = {str(p.relative_to(outdir)).replace("\\", "/")
                                for p in outdir.rglob("*")
                                if p.is_file() and p.name != "manifest.json"}
                check(set(listed) == actual_paths, "manifest: output file list mismatch")
                for relpath, entry in listed.items():
                    path = outdir / relpath
                    if path.exists():
                        digest = hashlib.sha256(path.read_bytes()).hexdigest()
                        check(entry.get("sha256") == digest and entry.get("bytes") == path.stat().st_size,
                              f"manifest: checksum or size mismatch {relpath}")
            except (json.JSONDecodeError, OSError, TypeError):
                check(False, "manifest: invalid manifest.json")

    wh_ids = {w["warehouse_id"] for w in wh}
    zn_ids = {z["zone_id"] for z in zn}
    loc_ids = {l["location_id"] for l in loc}
    sku_ids = {s["sku_id"] for s in sku}
    ord_ids = {o["order_id"] for o in order}
    line_ids = {r["line_id"] for r in ol}
    known_ids = (wh_ids | zn_ids | loc_ids | sku_ids | ord_ids | line_ids |
                 {r["pick_id"] for r in pe} | {r["replenishment_id"] for r in rep} |
                 {r["event_id"] for r in rep_events} |
                 {r["slot_id"] for r in sa} | {r["profile_id"] for r in prof} |
                 {r["supplier_id"] for r in suppliers} | {r["carrier_id"] for r in carriers} |
                 {r["shipment_id"] for r in inbound} | {r["event_id"] for r in inbound_events})
    sku_by = {r["sku_id"]: r for r in sku}
    loc_by = {r["location_id"]: r for r in loc}
    order_by = {r["order_id"]: r for r in order}
    line_by = {r["line_id"]: r for r in ol}
    zone_by = {r["zone_id"]: r for r in zn}
    pick_by_id = {r["pick_id"]: r for r in pe}
    slot_by_id = {r["slot_id"]: r for r in sa}

    # PK uniqueness
    for name, rows, key in [("warehouse", wh, "warehouse_id"), ("zone", zn, "zone_id"),
                            ("location", loc, "location_id"), ("sku", sku, "sku_id"),
                            ("sku_profile", prof, "profile_id"), ("order", order, "order_id"),
                            ("order_line", ol, "line_id"), ("pick_event", pe, "pick_id"),
                            ("replenishment", rep, "replenishment_id"),
                            ("replenishment_event", rep_events, "event_id"),
                            ("slot_assignment", sa, "slot_id"),
                            ("assignment_event", ae, "event_id"),
                            ("supplier", suppliers, "supplier_id"),
                            ("carrier", carriers, "carrier_id"),
                            ("inbound_shipment", inbound, "shipment_id"),
                            ("inbound_event", inbound_events, "event_id")]:
        dups = [k for k, c in Counter(r[key] for r in rows).items() if c > 1]
        check(not dups, f"{name}: duplicate PK {dups[:5]}")

    # FK integrity
    check(all(z["warehouse_id"] in wh_ids for z in zn), "zone: bad warehouse FK")
    check(all(l["zone_id"] in zn_ids and l["warehouse_id"] in wh_ids for l in loc),
          "location: bad FK")
    check(all(l["warehouse_id"] == zone_by[l["zone_id"]]["warehouse_id"] and
              l["zone_type"] == zone_by[l["zone_id"]]["zone_type"] for l in loc),
          "location: warehouse/zone values disagree")
    check(all(s["warehouse_id"] in wh_ids for s in sku), "sku: bad warehouse FK")
    check(all(p["sku_id"] in sku_ids for p in prof), "sku_profile: bad FK")
    check(len({p["sku_id"] for p in prof}) == len(prof),
          "sku_profile: duplicate SKU profile relationship")
    check(all(r["order_id"] in ord_ids and r["sku_id"] in sku_ids for r in ol),
          "order_line: bad FK")
    check(all(order_by[r["order_id"]]["warehouse_id"] == sku_by[r["sku_id"]]["warehouse_id"]
              for r in ol), "order_line: SKU belongs to a different warehouse")
    check(all(e["order_id"] in ord_ids and e["line_id"] in line_ids
              and e["location_id"] in loc_ids and e["sku_id"] in sku_ids
              for e in pe), "pick_event: bad FK")
    check(all(r["sku_id"] in sku_ids for r in rep), "replenishment: bad FK")
    check(all(s["sku_id"] in sku_ids and (not s["location_id"] or
              s["location_id"] in loc_ids) for s in sa), "slot_assignment: bad FK")
    check(all(s["warehouse_id"] == sku_by[s["sku_id"]]["warehouse_id"] and
              (not s["location_id"] or
               loc_by[s["location_id"]]["warehouse_id"] == s["warehouse_id"])
              for s in sa), "slot_assignment: warehouse relationships disagree")
    check(all(e["slot_id"] in {s["slot_id"] for s in sa} and
              e["sku_id"] in sku_ids and
              (not e["location_id"] or e["location_id"] in loc_ids) and
              e["warehouse_id"] in wh_ids for e in ae),
          "assignment_event: bad FK")
    check(all(e["replenishment_id"] in {r["replenishment_id"] for r in rep}
              for e in rep_events), "replenishment_event: bad FK")
    assignment_by_id = {row["slot_id"]: row for row in sa}
    check(all(e["sku_id"] == assignment_by_id[e["slot_id"]]["sku_id"] and
              e["location_id"] == assignment_by_id[e["slot_id"]]["location_id"] and
              e["warehouse_id"] == assignment_by_id[e["slot_id"]]["warehouse_id"] and
              e["decision"] == assignment_by_id[e["slot_id"]]["decision"]
              for e in ae), "assignment_event: values disagree with assignment")
    initial_events = [e for e in ae if e["event_type"] in {
        "ASSIGNMENT_ACTIVATED", "REASSIGNMENT_ACTIVATED", "REJECTED"}]
    check(all(sum(e["slot_id"] == row["slot_id"] for e in initial_events) == 1 for row in sa),
          "assignment_event: each assignment must have one initial/rejection event")
    check(all((e["event_type"] in {"ASSIGNMENT_ACTIVATED", "REASSIGNMENT_ACTIVATED"} and
               e["to_status"] == "active" and not e["from_status"])
              or (e["event_type"] == "REJECTED" and
                  e["to_status"] == "rejected" and not e["from_status"])
              or (e["event_type"] == "EXCEPTION_DETECTED" and
                  e["from_status"] == e["to_status"])
              or (e["event_type"] == "ASSIGNMENT_DEACTIVATED" and
                  e["from_status"] == "active" and e["to_status"] == "inactive")
              or (e["event_type"] == "ASSIGNMENT_RETIRED" and
                  e["from_status"] == "inactive" and e["to_status"] == "retired")
              for e in ae), "assignment_event: invalid lifecycle transition")
    check(all(e["event_type"] in {"ASSIGNMENT_ACTIVATED", "REASSIGNMENT_ACTIVATED",
                                   "ASSIGNMENT_DEACTIVATED", "ASSIGNMENT_RETIRED",
                                   "REJECTED", "EXCEPTION_DETECTED"}
              for e in ae), "assignment_event: invalid event type")
    lifecycle = defaultdict(list)
    for event in ae:
        lifecycle[event["slot_id"]].append(event)
    for slot_id, history in lifecycle.items():
        history.sort(key=lambda event: event["event_timestamp"])
        check(history[-1]["to_status"] == assignment_by_id[slot_id]["status"],
              f"assignment_event: final status mismatch {slot_id}")
    move_histories = defaultdict(list)
    for event in ae:
        if event.get("move_id"):
            move_histories[event["move_id"]].append(event)
    for move_id, history in move_histories.items():
        check(len({event["slot_id"] for event in history}) >= 2 and
              all(event["related_slot_id"] for event in history),
              f"assignment_event: incomplete linked move {move_id}")
    check(all(e["event_timestamp"] for e in ae), "assignment_event: missing event timestamp")
    supplier_ids = {row["supplier_id"] for row in suppliers}
    carrier_ids = {row["carrier_id"] for row in carriers}
    inbound_ids = {row["shipment_id"] for row in inbound}
    check(all(row["warehouse_id"] in wh_ids and row["supplier_id"] in supplier_ids and
              row["carrier_id"] in carrier_ids and row["sku_id"] in sku_ids
              and row["warehouse_id"] == sku_by[row["sku_id"]]["warehouse_id"]
              for row in inbound), "inbound_shipment: bad or inconsistent FK")
    check(all(row["shipment_id"] in inbound_ids for row in inbound_events),
          "inbound_event: bad shipment FK")
    check(all(row["status"] == "ACTIVE" for row in suppliers + carriers),
          "supplier/carrier: invalid status")
    check(all(row["status"] in {"BOOKED", "IN_TRANSIT", "RECEIVED"} and
              int(row["qty"]) > 0 and 0 <= int(row["received_qty"]) <= int(row["qty"]) and
              (row["status"] == "RECEIVED") == (int(row["received_qty"]) == int(row["qty"])) and
              row["booked_date"] <= row["planned_arrival_date"]
              for row in inbound), "inbound_shipment: invalid status, quantity, or date")
    events_by_shipment = defaultdict(list)
    for event in inbound_events:
        events_by_shipment[event["shipment_id"]].append(event)
    for shipment in inbound:
        events = sorted(events_by_shipment[shipment["shipment_id"]],
                        key=lambda event: event["event_timestamp"])
        transitions = [(event["from_status"], event["to_status"]) for event in events]
        expected = {"BOOKED": [("", "BOOKED")],
                    "IN_TRANSIT": [("", "BOOKED"), ("BOOKED", "IN_TRANSIT")],
                    "RECEIVED": [("", "BOOKED"), ("BOOKED", "IN_TRANSIT"),
                                 ("IN_TRANSIT", "RECEIVED")]}[shipment["status"]]
        check(transitions == expected, f"inbound_event: invalid lifecycle {shipment['shipment_id']}")
        check(bool(events) and events[-1]["to_status"] == shipment["status"],
              f"inbound_event: final state mismatch {shipment['shipment_id']}")

    # enums
    check(all(o["status"] in {"open", "picking", "shipped", "cancelled"} for o in order),
          "order: bad status enum")
    check(all(e["path_status"] in {"OK", "BLOCKED_AISLE"} for e in pe),
          "pick_event: bad path_status")
    check(all(w["region"] in {"NA-EAST", "NA-WEST", "NA-CENTRAL", "EU", "APAC"}
              for w in wh), "warehouse: bad region enum")
    check(all(re.fullmatch(r"WH-\d{3}", w["warehouse_id"]) for w in wh) and
          all(re.fullmatch(r"SKU-\d{6}", s["sku_id"]) for s in sku) and
          all(re.fullmatch(r"ORD-2026-\d{6}", o["order_id"]) for o in order) and
          all(re.fullmatch(r"PICK-ORD-2026-\d{6}-\d{3}", e["pick_id"]) for e in pe) and
          all(re.fullmatch(r"PRF-\d{6}", p["profile_id"]) for p in prof) and
          all(re.fullmatch(r"SLOT-2026-\d{6}", s["slot_id"]) for s in sa) and
          all(re.fullmatch(r"REPL-\d{6}", r["replenishment_id"]) for r in rep) and
          all(re.fullmatch(r"AEVT-\d{6}", e["event_id"]) for e in ae) and
          all(re.fullmatch(r"SUP-\d{6}", r["supplier_id"]) for r in suppliers) and
          all(re.fullmatch(r"CAR-\d{6}", r["carrier_id"]) for r in carriers) and
          all(re.fullmatch(r"INB-2026-\d{6}", r["shipment_id"]) for r in inbound) and
          all(re.fullmatch(r"INBEVT-\d{7}", r["event_id"]) for r in inbound_events),
          "identifier: invalid nomenclature")
    check(all(z["zone_type"] in {"AMBIENT", "COLD", "FROZEN", "HAZMAT", "BULK"}
              and 3 <= int(z["aisle_count"]) <= 5 for z in zn), "zone: invalid type/count")
    # Dimension validation
    check(all(float(s.get("length_cm", 0)) > 0 and
              float(s.get("width_cm", 0)) > 0 and
              float(s.get("height_cm", 0)) > 0 for s in sku),
          "sku: missing or invalid dimensions")
    check(all(float(l.get("length_cm", 0)) > 0 and
              float(l.get("width_cm", 0)) > 0 and
              float(l.get("height_cm", 0)) > 0 for l in loc),
          "location: missing or invalid dimensions")
    check(all(l["loc_type"] in {"SHELF", "PALLET", "FLOOR", "COOLER", "FREEZER", "HAZ_CABINET"}
              and l["occupancy_status"] in {"EMPTY", "OCCUPIED"} and
              float(l["capacity_volume_cm3"]) > 0 and float(l["max_weight_kg"]) > 0
              for l in loc), "location: invalid type/state/capacity")
    by_zone_locations = defaultdict(list)
    for location in loc:
        by_zone_locations[location["zone_id"]].append(location["location_id"])
    for zone_id, ids in by_zone_locations.items():
        suffixes = sorted(int(location_id.rsplit("-", 1)[1]) for location_id in ids)
        check(suffixes == list(range(1, len(ids) + 1)) and
              all(location_id.startswith(f"LOC-{zone_id}-") for location_id in ids),
              f"location: invalid zone-scoped sequence {zone_id}")
    check(all(o["channel"] in {"RETAIL", "ECOM", "WHOLESALE"} for o in order),
          "order: bad channel enum")
    check(all(o["peak_period"] in {"NORMAL", "PEAK"} for o in order),
          "order: bad peak period")
    check(all(((dt.date.fromisoformat(o["order_date"]) -
                dt.date(2026, 1, 1)).days >= 250) ==
              (o["peak_period"] == "PEAK")
              for o in order), "order: peak period does not match order date")
    check(all(e["shift"] in {"DAY", "EVE", "NIGHT"} for e in pe),
          "pick_event: bad shift enum")
    check(all(r["shift"] in {"DAY", "EVE", "NIGHT"} and
              r["required_shift"] in {"DAY", "EVE", "NIGHT"} and
              r["scheduled_shift"] in {"DAY", "EVE", "NIGHT"} and
              r["over_labor_capacity"] in {"0", "1"} for r in rep),
          "replenishment: bad shift/flag enum")
    check(all(re.fullmatch(r"REPLEVT-\d{7}", e["event_id"]) for e in rep_events),
          "replenishment_event: invalid identifier")
    check(all(s["decision"] in {"ASSIGNED", "REJECTED_CAPACITY", "REJECTED_COMPAT", "DUPLICATE"}
              and s["status"] in {"active", "rejected", "retired"} and
              s["effective_from"] <= s["effective_to"] for s in sa),
          "slot_assignment: bad decision/status/date range")

    # date ordering
    check(all(o["order_date"] <= o["ship_date"] for o in order),
          "order: order_date > ship_date")

    # arithmetic reconciliation: slot score 0..100, location counts
    check(all(0 <= float(s["score"]) <= 100 for s in sa if s["score"]),
          "slot: score out of range")
    latest_ship = max((dt.date.fromisoformat(r["ship_date"]) for r in order),
                      default=dt.date(2026, 1, 1))
    window_start = latest_ship - dt.timedelta(days=29)
    expected_freq = Counter()
    for line in ol:
        if dt.date.fromisoformat(order_by[line["order_id"]]["ship_date"]) >= window_start:
            expected_freq[line["sku_id"]] += int(line["qty"])
    check(all(int(profile["pick_frequency_30d"]) == expected_freq[profile["sku_id"]]
              for profile in prof), "profile: 30-day pick frequency does not reconcile")
    check(all(profile["velocity_class"] in {"FAST", "MEDIUM", "SLOW"} and
              abs(float(profile["avg_daily_picks"]) -
                  int(profile["pick_frequency_30d"]) / 30) < 0.001
              for profile in prof), "profile: invalid velocity or daily average")

    # Validate all recorded foreign keys, enums, business constraints and labels.
    check(all(r["location_id"] in loc_ids for r in rep), "replenishment: bad location FK")
    profile_by = {r["sku_id"]: r for r in prof}
    check(set(profile_by) == sku_ids, "sku_profile: every SKU must have exactly one profile")
    check(all(r["category"] in {"FOOD", "BEVERAGE", "PHARMA", "CHEMICAL",
                                  "ELECTRONICS", "APPAREL", "HOME"} and
              r["hazard_class"] in {"NONE", "FLAMMABLE", "CORROSIVE", "TOXIC"} and
              r["temp_class"] in {"AMBIENT", "CHILLED", "FROZEN"} and
              float(r["unit_volume_cm3"]) > 0 and float(r["unit_weight_kg"]) > 0
              for r in sku), "sku: invalid dimensions or enum")
    check(all(r["hazard_segregation"] in {"NONE", "FLAMMABLE", "CORROSIVE", "TOXIC"}
              for r in zn), "zone: invalid hazard segregation")
    check(all(r["status"] in {"open", "picking", "shipped"} and
              r["channel"] in {"RETAIL", "ECOM", "WHOLESALE"} and
              r["order_date"] <= r["ship_date"] for r in order),
          "order: invalid status/channel or date ordering")
    check(all(r["qty"].isdigit() and 1 <= int(r["qty"]) <= 999 for r in ol),
          "order_line: invalid quantity")
    for event in pe:
        line = line_by.get(event["line_id"], {})
        order_row = order_by.get(event["order_id"], {})
        try:
            hour = int(event["pick_ts"][11:13])
        except (ValueError, IndexError):
            hour = -1
        shift_valid = ((event["shift"] == "DAY" and 6 <= hour <= 13) or
                       (event["shift"] == "EVE" and 14 <= hour <= 21) or
                       (event["shift"] == "NIGHT" and hour in {22, 23}))
        check(line.get("order_id") == event["order_id"] and
              line.get("sku_id") == event["sku_id"],
              f"pick_event: line relationship mismatch {event['pick_id']}")
        check(event["pick_ts"][:10] == order_row.get("ship_date") and shift_valid,
              f"pick_event: timestamp outside ship date/shift {event['pick_id']}")
        check(loc_by.get(event["location_id"], {}).get("warehouse_id") ==
              sku_by[event["sku_id"]]["warehouse_id"],
              f"pick_event: location in wrong warehouse {event['pick_id']}")
    check(all(r["status"] in {"pending", "in_progress", "completed"} and
              int(r["qty"]) > 0 and float(r["labor_minutes"]) > 0 and
              abs(float(r["scheduled_labor_minutes"]) - float(r["labor_minutes"])) < 0.011 and
              float(r["shift_capacity_minutes"]) == 480 and
              r["schedule_decision"] in {"SCHEDULED", "RESCHEDULED"} and
              int(r["over_labor_capacity"]) == int(float(r["deferred_labor_minutes"]) > 0)
              for r in rep), "replenishment: invalid status or workload")
    check(all((r["schedule_decision"] == "SCHEDULED" and
               float(r["deferred_labor_minutes"]) == 0 and r["required_date"] == r["scheduled_date"] and
               r["required_shift"] == r["scheduled_shift"]) or
              (r["schedule_decision"] == "RESCHEDULED" and
               float(r["deferred_labor_minutes"]) == float(r["labor_minutes"]) and
               r["reschedule_count"] != "0" and r["status"] == "pending")
              for r in rep), "replenishment: schedule decision/status mismatch")
    replen_by_wh_shift = defaultdict(list)
    for task in rep:
        key = (sku_by[task["sku_id"]]["warehouse_id"], task["required_date"], task["required_shift"])
        replen_by_wh_shift[key].append(task)
    for key, tasks in replen_by_wh_shift.items():
        total = sum(float(task["labor_minutes"]) for task in tasks)
        check(all(abs(float(task["shift_total_labor_minutes"]) - total) < 0.011
                  for task in tasks), f"replenishment: shift total mismatch {key}")
    scheduled_by_shift = defaultdict(float)
    for task in rep:
        scheduled_by_shift[(sku_by[task["sku_id"]]["warehouse_id"], task["scheduled_date"],
                            task["scheduled_shift"])] += float(task["scheduled_labor_minutes"])
    check(all(total <= 480.011 for total in scheduled_by_shift.values()),
          "replenishment: scheduled shift exceeds capacity")
    check(all(abs(float(task["remaining_shift_capacity_minutes"]) -
                   (480 - scheduled_by_shift[(sku_by[task["sku_id"]]["warehouse_id"],
                                             task["scheduled_date"], task["scheduled_shift"])])) < 0.011
              for task in rep), "replenishment: remaining capacity mismatch")
    rep_events_by_id = defaultdict(list)
    for event in rep_events:
        rep_events_by_id[event["replenishment_id"]].append(event)
    for task in rep:
        history = sorted(rep_events_by_id[task["replenishment_id"]],
                         key=lambda event: event["event_timestamp"])
        types = [event["event_type"] for event in history]
        expected_history = ["REQUESTED"] if task["schedule_decision"] == "SCHEDULED" else ["REQUESTED", "DEFERRED", "RESCHEDULED"]
        check(types == expected_history,
              f"replenishment_event: invalid history {task['replenishment_id']}")
        check(bool(history) and history[-1]["to_date"] == task["scheduled_date"] and
              history[-1]["to_shift"] == task["scheduled_shift"],
              f"replenishment_event: final schedule mismatch {task['replenishment_id']}")
    check(all(event["event_type"] in {"REQUESTED", "DEFERRED", "RESCHEDULED"} and event["event_timestamp"]
              for event in rep_events), "replenishment_event: invalid event data")
    assignment_skus = {r["sku_id"] for r in sa
                       if r["decision"] == "ASSIGNED" and r["status"] == "active"}
    check(all(e["sku_id"] in assignment_skus for e in pe),
          "pick_event: SKU lacks valid active assignment")
    active = [r for r in sa if r["status"] == "active"]
    gt_keys = {(r["issue_type"], r["record_id"]) for r in gt}
    exception_event_slots = {e["slot_id"] for e in ae if e["event_type"] == "EXCEPTION_DETECTED"}
    duplicate_ids = {record for issue, record in gt_keys
                     if issue == "DUPLICATE_ACTIVE_SLOT"}
    duplicate_locations = {r["location_id"] for r in sa if r["slot_id"] in duplicate_ids}
    capacity_exception_slots = {record for issue, record in gt_keys
                                if issue == "CAPACITY_EXCEEDED"}
    incompatible_locations = {r["record_id"] for r in gt if r["issue_type"] == "INCOMPATIBLE_COLOCATION" and r["entity"] == "Location"}
    incompatible_slots = {r["record_id"] for r in gt if r["issue_type"] == "INCOMPATIBLE_COLOCATION" and r["entity"] == "SlotAssignment"}
    incompatible_locations.update(slot_by_id[sid]["location_id"] for sid in incompatible_slots
                                   if sid in slot_by_id and slot_by_id[sid]["location_id"])
    for r in active:
        if not r["location_id"]:
            continue
        s, l = sku_by[r["sku_id"]], loc_by[r["location_id"]]
        if r["location_id"] in duplicate_locations:
            continue
        if r["slot_id"] in incompatible_slots or r["location_id"] in incompatible_locations:
            continue
        p, z = profile_by.get(r["sku_id"], {}), zone_by.get(l["zone_id"], {})
        volume = float(s["unit_volume_cm3"])
        weight = float(s["unit_weight_kg"])
        if r["slot_id"] not in capacity_exception_slots:
            check(volume <= float(l["capacity_volume_cm3"]) and weight <= float(l["max_weight_kg"]),
                  f"slot: physical capacity exceeded {r['slot_id']}")
            from itertools import permutations
            sku_dims = [float(s["length_cm"]), float(s["width_cm"]), float(s["height_cm"])]
            loc_dims = [float(l["length_cm"]), float(l["width_cm"]), float(l["height_cm"])]
            fits_dimensions = any(all(dim <= limit for dim, limit in zip(orientation, loc_dims))
                                  for orientation in permutations(sku_dims))
            check(fits_dimensions, f"slot: physical dimensions do not fit {r['slot_id']}")
            check(s["hazard_class"] == "NONE" or z.get("zone_type") == "HAZMAT",
                  f"slot: hazard compatibility failed {r['slot_id']}")
            check(s["hazard_class"] == "NONE" or
                  z.get("hazard_segregation") == s["hazard_class"],
                  f"slot: hazard segregation failed {r['slot_id']}")
            expected_temp = None if s["temp_class"] == "AMBIENT" else s["temp_class"]
            check(expected_temp is None or z.get("storage_temp") == expected_temp,
                  f"slot: temperature compatibility failed {r['slot_id']}")
    check(all(r["issue_type"] in {"CAPACITY_EXCEEDED", "INCOMPATIBLE_COLOCATION",
                                  "DUPLICATE_ACTIVE_SLOT", "BLOCKED_AISLE_PICK",
                                  "REPLEN_LABOR_EXCEEDED"} for r in gt),
          "ground_truth: bad issue_type")
    required_issues = {"CAPACITY_EXCEEDED", "INCOMPATIBLE_COLOCATION",
                       "DUPLICATE_ACTIVE_SLOT", "BLOCKED_AISLE_PICK",
                       "REPLEN_LABOR_EXCEEDED"}
    check(required_issues <= {row["issue_type"] for row in gt},
          "ground_truth: one or more required exception classes are missing")
    for e in pe:
        if e["path_status"] == "BLOCKED_AISLE":
            check(("BLOCKED_AISLE_PICK", e["pick_id"]) in gt_keys,
                  f"pick_event: unlabelled blocked aisle {e['pick_id']}")
    for r in rep:
        if r["over_labor_capacity"] == "1":
            check(("REPLEN_LABOR_EXCEEDED", r["replenishment_id"]) in gt_keys,
                  f"replenishment: unlabelled labor exception {r['replenishment_id']}")
    issue_rows = {
        "CAPACITY_EXCEEDED": {r["slot_id"] for r in sa
                              if r["decision"] == "REJECTED_CAPACITY" and r["status"] == "rejected"},
        "INCOMPATIBLE_COLOCATION": {r["record_id"] for r in gt if r["issue_type"] == "INCOMPATIBLE_COLOCATION"},
        "DUPLICATE_ACTIVE_SLOT": {r["slot_id"] for r in sa if r["decision"] == "DUPLICATE"},
        "BLOCKED_AISLE_PICK": {r["pick_id"] for r in pe if r["path_status"] == "BLOCKED_AISLE"},
        "REPLEN_LABOR_EXCEEDED": {r["replenishment_id"] for r in rep
                                  if r["over_labor_capacity"] == "1"},
    }
    for truth_row in gt:
        try:
            source_ids = json.loads(truth_row["source_ids"])
            affected_ids = json.loads(truth_row["affected_ids"])
            check(isinstance(source_ids, list) and bool(source_ids) and
                  set(source_ids) <= known_ids,
                  f"ground_truth: invalid source IDs {truth_row['record_id']}")
            check(isinstance(affected_ids, list) and bool(affected_ids) and
                  set(affected_ids) <= known_ids,
                  f"ground_truth: invalid affected IDs {truth_row['record_id']}")
        except (json.JSONDecodeError, TypeError):
            check(False, f"ground_truth: malformed traceability IDs {truth_row['record_id']}")
        check(bool(truth_row["reason"]) and bool(truth_row["expected_decision"]),
              f"ground_truth: missing explanation {truth_row['record_id']}")
        check(truth_row["severity"] in {"LOW", "MEDIUM", "HIGH"},
              f"ground_truth: invalid severity {truth_row['record_id']}")
        check(truth_row["resolution_status"] in {"OPEN", "REJECTED", "REROUTED", "DEFERRED", "RESCHEDULED"},
              f"ground_truth: invalid resolution status {truth_row['record_id']}")
        check(truth_row["record_id"] in issue_rows.get(truth_row["issue_type"], set()),
              f"ground_truth: record does not match issue {truth_row['record_id']}")
        if truth_row["issue_type"] == "CAPACITY_EXCEEDED":
            exception = slot_by_id.get(truth_row["record_id"])
            if exception and exception["location_id"] in loc_by:
                item = sku_by[exception["sku_id"]]
                slot = loc_by[exception["location_id"]]
                check(float(item["unit_volume_cm3"]) > float(slot["capacity_volume_cm3"]) or
                      float(item["unit_weight_kg"]) > float(slot["max_weight_kg"]),
                      f"ground_truth: capacity not actually exceeded {truth_row['record_id']}")
        elif truth_row["issue_type"] == "INCOMPATIBLE_COLOCATION":
            if truth_row["entity"] == "SlotAssignment":
                slot = slot_by_id.get(truth_row["record_id"])
                if slot and slot["location_id"] in loc_by:
                    check(slot["slot_id"] in exception_event_slots,
                          f"assignment_event: incompatible exception not recorded {slot['slot_id']}")
                    location_skus = [sku_by[s["sku_id"]] for s in sa
                                     if s["location_id"] == slot["location_id"] and s["status"] == "active"]
                    hazard_classes = [sku["hazard_class"] for sku in location_skus
                                      if sku["hazard_class"] != "NONE"]
                    from .master import INCOMPAT_HAZARD_PAIRS
                    has_incompatible = any(
                        tuple(sorted((h1, h2))) in INCOMPAT_HAZARD_PAIRS
                        for i, h1 in enumerate(hazard_classes)
                        for h2 in hazard_classes[i + 1:])
                    check(has_incompatible,
                          f"ground_truth: slot location has no incompatible pair {truth_row['record_id']}")
            elif truth_row["entity"] == "Location":
                loc = loc_by.get(truth_row["record_id"])
                if loc:
                    # Find all SKUs assigned to this location
                    location_skus = [sku_by[s["sku_id"]] for s in sa if s["location_id"] == truth_row["record_id"] and s["status"] == "active"]
                    hazard_classes = [s["hazard_class"] for s in location_skus if s["hazard_class"] != "NONE"]
                    # Check for incompatible pairs
                    from .master import INCOMPAT_HAZARD_PAIRS
                    has_incompatible = any(tuple(sorted((h1, h2))) in INCOMPAT_HAZARD_PAIRS
                                          for i, h1 in enumerate(hazard_classes)
                                          for h2 in hazard_classes[i+1:])
                    check(has_incompatible,
                          f"ground_truth: location has no incompatible hazards {truth_row['record_id']}")
                    check(any(s["slot_id"] in exception_event_slots
                              for s in sa if s["location_id"] == truth_row["record_id"]),
                          f"assignment_event: incompatible exception not recorded {truth_row['record_id']}")
        elif truth_row["issue_type"] == "DUPLICATE_ACTIVE_SLOT":
            check(truth_row["record_id"] in exception_event_slots,
                  f"assignment_event: duplicate exception not recorded {truth_row['record_id']}")
        if truth_row["issue_type"] == "BLOCKED_AISLE_PICK":
            event = pick_by_id.get(truth_row["record_id"])
            if event:
                slot = loc_by[event["location_id"]]
                parent = zone_by[slot["zone_id"]]
                check(slot["aisle"] == parent["blocked_aisle"],
                      f"ground_truth: blocked aisle label mismatch {event['pick_id']}")

    return {"passed": len(issues) == 0, "issues": issues}
