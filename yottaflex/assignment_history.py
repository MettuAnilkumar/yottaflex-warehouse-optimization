"""Append-only assignment lifecycle and explicit reassignment events."""
import csv


def apply_assignment_moves(master, count_per_warehouse=1):
    """Move one eligible SKU per warehouse into a better deterministic free slot."""
    from .slotting import slot_fit

    zones = {row["zone_id"]: row for row in master.zones}
    skus = {row["sku_id"]: row for row in master.skus}
    profiles = {row["sku_id"]: row for row in master.sku_profiles}
    locations = {row["location_id"]: row for row in master.locations}
    number = max((int(row["slot_id"].rsplit("-", 1)[1])
                  for row in master.slot_assignments if row["slot_id"].startswith("SLOT-2026-")),
                 default=0)
    moves = []
    for warehouse in master.warehouses:
        candidates = sorted((row for row in master.slot_assignments
                             if row["warehouse_id"] == warehouse["warehouse_id"]
                             and row["status"] == "active" and row["decision"] == "ASSIGNED"),
                            key=lambda row: row["sku_id"])
        moved = 0
        for old in candidates:
            old_location = locations[old["location_id"]]
            options = []
            for location in master.locations:
                if (location["warehouse_id"] != warehouse["warehouse_id"] or
                        location["occupancy_status"] != "EMPTY" or location["blocked"]):
                    continue
                fit = slot_fit(master, skus[old["sku_id"]], profiles[old["sku_id"]],
                               location, zones)
                if fit is not None and location["location_id"] != old["location_id"]:
                    options.append((fit, location["location_id"], location))
            if not options:
                continue
            score, _, new_location = max(options, key=lambda item: (item[0], item[1]))
            if new_location["zone_id"] == old_location["zone_id"]:
                other_zone = [item for item in options if item[2]["zone_id"] != old_location["zone_id"]]
                if other_zone:
                    score, _, new_location = max(other_zone, key=lambda item: (item[0], item[1]))
            number += 1
            new_slot_id = f"SLOT-2026-{number:06d}"
            move_id = f"MOVE-2026-{len(moves) + 1:06d}"
            old["status"] = "retired"
            old["effective_to"] = "2026-06-30"
            new_row = {"slot_id": new_slot_id, "sku_id": old["sku_id"],
                       "location_id": new_location["location_id"],
                       "warehouse_id": old["warehouse_id"], "score": score,
                       "decision": "ASSIGNED", "status": "active",
                       "effective_from": "2026-07-01", "effective_to": "2026-12-31"}
            master.slot_assignments.append(new_row)
            old_location["occupancy_status"] = "EMPTY"
            new_location["occupancy_status"] = "OCCUPIED"
            moves.append({"move_id": move_id, "sku_id": old["sku_id"],
                          "old_slot_id": old["slot_id"], "new_slot_id": new_slot_id,
                          "old_location_id": old_location["location_id"],
                          "new_location_id": new_location["location_id"]})
            moved += 1
            if moved >= count_per_warehouse:
                break
    master.assignment_moves = moves
    return moves


def build_assignment_history(master):
    events = []
    seq = 0

    def add(row, kind, before, after, stamp, move_id="", related=""):
        nonlocal seq
        seq += 1
        events.append({
            "event_id": f"AEVT-{seq:06d}", "slot_id": row["slot_id"],
            "sku_id": row["sku_id"], "location_id": row["location_id"],
            "warehouse_id": row["warehouse_id"], "event_type": kind,
            "from_status": before, "to_status": after,
            "decision": row["decision"], "event_timestamp": stamp,
            "effective_from": row["effective_from"], "effective_to": row["effective_to"],
            "move_id": move_id, "related_slot_id": related,
        })

    move_by_slot = {}
    for move in getattr(master, "assignment_moves", []):
        move_by_slot[move["old_slot_id"]] = (move, "old")
        move_by_slot[move["new_slot_id"]] = (move, "new")

    for row in master.slot_assignments:
        move_entry = move_by_slot.get(row["slot_id"])
        if move_entry and move_entry[1] == "old":
            move = move_entry[0]
            add(row, "ASSIGNMENT_ACTIVATED", "", "active", "2026-01-01T00:00:00")
            add(row, "ASSIGNMENT_DEACTIVATED", "active", "inactive",
                "2026-06-30T22:00:00", move["move_id"], move["new_slot_id"])
            add(row, "ASSIGNMENT_RETIRED", "inactive", "retired",
                "2026-06-30T23:00:00", move["move_id"], move["new_slot_id"])
        elif move_entry and move_entry[1] == "new":
            move = move_entry[0]
            add(row, "REASSIGNMENT_ACTIVATED", "", "active", "2026-07-01T00:00:00",
                move["move_id"], move["old_slot_id"])
        elif row["status"] == "rejected":
            add(row, "REJECTED", "", "rejected", "2026-01-01T00:00:00")
        elif row["status"] == "retired":
            add(row, "ASSIGNMENT_ACTIVATED", "", "active", "2026-01-01T00:00:00")
            add(row, "ASSIGNMENT_DEACTIVATED", "active", "inactive", "2026-06-30T22:00:00")
            add(row, "ASSIGNMENT_RETIRED", "inactive", "retired", "2026-06-30T23:00:00")
        elif row["status"] == "active":
            add(row, "ASSIGNMENT_ACTIVATED", "", "active", "2026-01-01T00:00:00")

    assignment_by_id = {row["slot_id"]: row for row in master.slot_assignments}
    assignments_by_location = {}
    for row in master.slot_assignments:
        if row["location_id"]:
            assignments_by_location.setdefault(row["location_id"], []).append(row)
    for truth in master.ground_truth:
        if truth["issue_type"] not in {"DUPLICATE_ACTIVE_SLOT", "INCOMPATIBLE_COLOCATION"}:
            continue
        affected = ([assignment_by_id[truth["record_id"]]]
                    if truth["record_id"] in assignment_by_id else
                    assignments_by_location.get(truth["record_id"], []))
        for row in affected:
            add(row, "EXCEPTION_DETECTED", row["status"], row["status"],
                "2026-12-31T23:00:00")

    master.assignment_events = events
    return events


def export(master, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    fields = ["event_id", "slot_id", "sku_id", "location_id", "warehouse_id",
              "event_type", "from_status", "to_status", "decision",
              "event_timestamp", "effective_from", "effective_to", "move_id",
              "related_slot_id"]
    with open(outdir / "assignment_event.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(master.assignment_events)
