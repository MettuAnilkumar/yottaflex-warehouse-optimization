"""Labelled anomaly injection. Ground truth is written separately — never
mutated into input features beyond the explicit derived flag columns the
dictionary documents (path_status, over_labor_capacity)."""
import csv
import json
import random
from collections import defaultdict

from .config import SEED


def schedule_replenishments(master, capacity=480.0):
    """Allocate complete tasks by priority into successive warehouse shifts."""
    day_order = ("DAY", "EVE", "NIGHT")
    sku_by = {row["sku_id"]: row for row in master.skus}
    groups = defaultdict(list)
    events = []
    seq = 0
    for task in master.replenishments:
        task["required_shift"] = task.get("required_shift") or task.get("shift") or "DAY"
        task["required_date"] = task.get("required_date") or "2026-01-01"
        task["shift"] = task["required_shift"]
        groups[(sku_by[task["sku_id"]]["warehouse_id"], task["required_date"],
                task["required_shift"])].append(task)

    def add_event(task, kind, before, after, from_date, from_shift, to_date, to_shift, stamp):
        nonlocal seq
        seq += 1
        events.append({"event_id": f"REPLEVT-{seq:07d}",
                       "replenishment_id": task["replenishment_id"],
                       "event_type": kind, "from_status": before, "to_status": after,
                       "event_timestamp": stamp, "from_date": from_date,
                       "from_shift": from_shift, "to_date": to_date,
                       "to_shift": to_shift, "priority_class": task["priority_class"]})

    ordered = []
    for key, tasks in sorted(groups.items()):
        total = round(sum(float(task["labor_minutes"]) for task in tasks), 2)
        for task in tasks:
            task["shift_total_labor_minutes"] = total
        ordered.extend(tasks)
    ordered.sort(key=lambda task: (
        {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[task["priority_class"]],
        task["required_date"], day_order.index(task["required_shift"]),
        task["replenishment_id"]))
    pending = list(ordered)
    actual_load = defaultdict(float)
    cursor_date = {task["replenishment_id"]: task["required_date"] for task in pending}
    cursor_shift = {task["replenishment_id"]: day_order.index(task["required_shift"])
                    for task in pending}
    for task in pending:
        shift = task["required_shift"]
        stamp = f"{task['required_date']}T{'06:00:00' if shift == 'DAY' else '14:00:00' if shift == 'EVE' else '22:00:00'}"
        add_event(task, "REQUESTED", "", "pending", "", "", task["required_date"], shift, stamp)

    for task in pending:
        wh = sku_by[task["sku_id"]]["warehouse_id"]
        labor = float(task["labor_minutes"])
        day = cursor_date[task["replenishment_id"]]
        shift_index = cursor_shift[task["replenishment_id"]]
        original_day, original_shift = task["required_date"], task["required_shift"]
        steps = 0
        while True:
            shift = day_order[shift_index]
            key = (wh, day, shift)
            if actual_load[key] + labor <= capacity + 1e-9:
                actual_load[key] += labor
                break
            steps += 1
            shift_index += 1
            if shift_index == len(day_order):
                shift_index = 0
                import datetime as dt
                day = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
            if steps > 90:
                raise RuntimeError("replenishment scheduler exceeded 90-day horizon")
        delayed = (day != original_day or day_order[shift_index] != original_shift)
        scheduled_shift = day_order[shift_index]
        task.update({"scheduled_date": day, "scheduled_shift": scheduled_shift,
                     "shift": original_shift, "scheduled_labor_minutes": round(labor, 2),
                     "deferred_labor_minutes": round(labor if delayed else 0.0, 2),
                     "scheduled_shift_labor_minutes": round(actual_load[(wh, day, scheduled_shift)], 2),
                     "shift_capacity_minutes": capacity,
                     "schedule_decision": "RESCHEDULED" if delayed else "SCHEDULED",
                     "reschedule_count": steps, "over_labor_capacity": int(delayed),
                     "status": "pending" if delayed else task["status"]})
        if delayed:
            add_event(task, "DEFERRED", "pending", "deferred", original_day, original_shift,
                      original_day, original_shift,
                      f"{original_day}T{'06:00:00' if original_shift == 'DAY' else '14:00:00' if original_shift == 'EVE' else '22:00:00'}")
            add_event(task, "RESCHEDULED", "deferred", "pending", original_day, original_shift,
                      day, scheduled_shift,
                      f"{day}T{'06:00:00' if scheduled_shift == 'DAY' else '14:00:00' if scheduled_shift == 'EVE' else '22:00:00'}")
    for task in pending:
        wh = sku_by[task["sku_id"]]["warehouse_id"]
        load = actual_load[(wh, task["scheduled_date"], task["scheduled_shift"])]
        task["scheduled_shift_labor_minutes"] = round(load, 2)
        task["remaining_shift_capacity_minutes"] = round(capacity - load, 2)
    master.replenishment_events = events
    return [task for task in pending if task["over_labor_capacity"]]


def inject(master, n=25):
    rng = random.Random(SEED + 9)
    truth = []
    slot_numbers = [int(row["slot_id"].rsplit("-", 1)[1])
                    for row in master.slot_assignments
                    if row["slot_id"].startswith("SLOT-2026-")]
    next_slot_number = max(slot_numbers, default=0)

    def next_slot_id():
        nonlocal next_slot_number
        next_slot_number += 1
        return f"SLOT-2026-{next_slot_number:06d}"

    # 1. CAPACITY_EXCEEDED: record an explicit invalid assignment while leaving
    # source SKU master data unchanged.
    act = [s for s in master.slot_assignments if s["status"] == "active"]
    sku_by = {s["sku_id"]: s for s in master.skus}
    loc_by = {l["location_id"]: l for l in master.locations}
    locations_by_wh = defaultdict(list)
    for location in master.locations:
        locations_by_wh[location["warehouse_id"]].append(location)
    candidates = []
    for sku in master.skus:
        for loc in locations_by_wh[sku["warehouse_id"]]:
            if (sku["unit_volume_cm3"] > loc["capacity_volume_cm3"] or
                    sku["unit_weight_kg"] > loc["max_weight_kg"]):
                candidates.append((sku, loc))
    for i, (sku, loc) in enumerate(candidates[:n], 1):
        slot_id = next_slot_id()
        master.slot_assignments.append({
            "slot_id": slot_id, "sku_id": sku["sku_id"],
            "location_id": loc["location_id"], "warehouse_id": sku["warehouse_id"],
            "score": 0, "decision": "REJECTED_CAPACITY", "status": "rejected",
            "effective_from": "2026-01-01", "effective_to": "2026-12-31",
        })
        truth.append({"record_id": slot_id, "entity": "SlotAssignment",
                      "issue_type": "CAPACITY_EXCEEDED",
                      "expected_decision": f"reject {sku['sku_id']} at {loc['location_id']}; select a larger location",
                      "severity": "HIGH"})

    # 2. INCOMPATIBLE_COLOCATION: pair two incompatible-hazard SKUs in the SAME physical location
    from .master import INCOMPAT_HAZARD_PAIRS
    hazardous = [s for s in master.skus if s["hazard_class"] != "NONE"]
    from .master import INCOMPAT_HAZARD_PAIRS
    hazards_by_wh = defaultdict(list)
    for sku in hazardous:
        hazards_by_wh[sku["warehouse_id"]].append(sku)
    prof_by_sku = {row["sku_id"]: row for row in master.sku_profiles}
    loc_by = {row["location_id"]: row for row in master.locations}
    assignments_by_sku = {row["sku_id"]: row for row in act}
    for wh_id, candidates in sorted(hazards_by_wh.items()):
        pair = next(((a, b) for i, a in enumerate(candidates) for b in candidates[i + 1:]
                     if tuple(sorted((a["hazard_class"], b["hazard_class"])))
                     in INCOMPAT_HAZARD_PAIRS and a["sku_id"] in assignments_by_sku
                     and b["sku_id"] in assignments_by_sku), None)
        if not pair:
            continue
        a, b = pair
        old_b = assignments_by_sku[b["sku_id"]]
        target = assignments_by_sku[a["sku_id"]]
        old_b["status"] = "retired"
        old_b["effective_to"] = "2026-06-30"
        number = next_slot_id()
        moved = dict(old_b)
        moved.update({"slot_id": number, "location_id": target["location_id"],
                      "score": 0, "decision": "ASSIGNED", "status": "active",
                      "effective_from": "2026-07-01", "effective_to": "2026-12-31"})
        master.slot_assignments.append(moved)
        move_id = f"MOVE-2026-{len(getattr(master, 'assignment_moves', [])) + 1:06d}"
        master.assignment_moves.append({"move_id": move_id, "sku_id": b["sku_id"],
                                        "old_slot_id": old_b["slot_id"],
                                        "new_slot_id": number,
                                        "old_location_id": old_b["location_id"],
                                        "new_location_id": target["location_id"]})
        loc_by[old_b["location_id"]]["occupancy_status"] = "EMPTY"
        row = {"record_id": number, "entity": "SlotAssignment",
               "issue_type": "INCOMPATIBLE_COLOCATION",
               "expected_decision": f"remove incompatible SKU {b['sku_id']} from location {target['location_id']}; hazards {a['hazard_class']} and {b['hazard_class']} cannot share",
               "severity": "HIGH"}
        truth.append(row)

    # 3. DUPLICATE_ACTIVE_SLOT: force second active assignment on a location
    dup_used = set()
    for wh_id in sorted({row["warehouse_id"] for row in act}):
        dup_target = next((row for row in act if row["warehouse_id"] == wh_id
                           and row["status"] == "active" and row["location_id"] not in dup_used
                           and row["location_id"]), None)
        if not dup_target:
            continue
        dup_used.add(dup_target["location_id"])
        second = dict(dup_target)
        second["slot_id"] = next_slot_id()
        second["decision"] = "DUPLICATE"
        master.slot_assignments.append(second)
        truth.append({"record_id": second["slot_id"], "entity": "SlotAssignment",
                      "issue_type": "DUPLICATE_ACTIVE_SLOT",
                      "expected_decision": f"retire duplicate assignment {second['slot_id']} at {second['location_id']}; retain one active slot",
                      "severity": "MEDIUM"})

    # 4. BLOCKED_AISLE_PICK
    blocked = [e for e in master.pick_events if e["path_status"] == "BLOCKED_AISLE"]
    for e in blocked:
        detour_distance = float(e["travel_distance_m"])
        truth.append({"record_id": e["pick_id"], "entity": "PickEvent",
                      "issue_type": "BLOCKED_AISLE_PICK",
                      "expected_decision": f"reroute via an open aisle; calculated alternate travel is {detour_distance:.1f}m",
                      "severity": "MEDIUM"})

    # 5. Schedule by priority into subsequent shifts without exceeding capacity.
    capped = schedule_replenishments(master)
    for r in capped:
        r["over_labor_capacity"] = 1
        truth.append({"record_id": r["replenishment_id"], "entity": "Replenishment",
                      "issue_type": "REPLEN_LABOR_EXCEEDED",
                      "expected_decision": f"reschedule {r['labor_minutes']:.2f} labor minutes from {r['required_date']} {r['required_shift']} to {r['scheduled_date']} {r['scheduled_shift']} ({r['priority_class']} priority); requested workload was {r['shift_total_labor_minutes']:.2f} against 480 minutes capacity",
                      "severity": "HIGH"})
    assignment_by_id = {row["slot_id"]: row for row in master.slot_assignments}
    assignments_by_location = defaultdict(list)
    for row in master.slot_assignments:
        if row["location_id"]:
            assignments_by_location[row["location_id"]].append(row)
    pick_by_id = {row["pick_id"]: row for row in master.pick_events}
    replen_by_id = {row["replenishment_id"]: row for row in master.replenishments}
    issue_reasons = {
        "CAPACITY_EXCEEDED": "The SKU exceeds physical dimensions, volume, or weight limits at the candidate location.",
        "INCOMPATIBLE_COLOCATION": "Hazard or temperature compatibility rules are violated by co-location.",
        "DUPLICATE_ACTIVE_SLOT": "More than one active assignment uses a location restricted to a single active SKU.",
        "BLOCKED_AISLE_PICK": "The direct pick path crosses an aisle closed for the relevant route.",
        "REPLEN_LABOR_EXCEEDED": "The planned shift workload exceeds its 480-minute labor budget.",
    }
    resolution_by_issue = {
        "CAPACITY_EXCEEDED": "REJECTED",
        "BLOCKED_AISLE_PICK": "REROUTED",
        "INCOMPATIBLE_COLOCATION": "OPEN",
        "DUPLICATE_ACTIVE_SLOT": "OPEN",
        "REPLEN_LABOR_EXCEEDED": "RESCHEDULED",
    }
    for row in truth:
        record_id = row["record_id"]
        related_slots = []
        source_ids = {record_id}
        affected_ids = set()
        if record_id in assignment_by_id:
            assignment = assignment_by_id[record_id]
            related_slots = [assignment]
            source_ids.update((assignment["sku_id"], assignment["location_id"]))
            affected_ids.update((assignment["sku_id"], assignment["location_id"]))
            related_slots = assignments_by_location.get(assignment["location_id"], related_slots)
        elif record_id in assignments_by_location:
            related_slots = assignments_by_location[record_id]
            source_ids.update(s["slot_id"] for s in related_slots)
            source_ids.update(s["sku_id"] for s in related_slots)
            affected_ids.add(record_id)
        elif record_id in pick_by_id:
            event = pick_by_id[record_id]
            source_ids.update((event["order_id"], event["line_id"], event["location_id"]))
            affected_ids.update((event["location_id"], event["sku_id"]))
        elif record_id in replen_by_id:
            task = replen_by_id[record_id]
            source_ids.update((task["sku_id"], task["location_id"], sku_by[task["sku_id"]]["warehouse_id"]))
            affected_ids.update((task["sku_id"], task["location_id"]))
        for related in related_slots:
            source_ids.update((related["slot_id"], related["sku_id"]))
            affected_ids.add(related["sku_id"])
        row["source_ids"] = json.dumps(sorted(value for value in source_ids if value))
        row["affected_ids"] = json.dumps(sorted(value for value in affected_ids if value))
        row["reason"] = issue_reasons[row["issue_type"]]
        row["resolution_status"] = resolution_by_issue[row["issue_type"]]
    master.ground_truth = truth
    return truth


def export(master, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / "ground_truth.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["record_id", "entity", "issue_type",
                                           "source_ids", "affected_ids", "reason",
                                           "expected_decision", "severity",
                                           "resolution_status"])
        w.writeheader()
        w.writerows(master.ground_truth)
    with open(outdir / "replenishment_event.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["event_id", "replenishment_id", "event_type",
                                           "from_status", "to_status", "event_timestamp",
                                           "from_date", "from_shift", "to_date", "to_shift",
                                           "priority_class"])
        w.writeheader()
        w.writerows(master.replenishment_events)
