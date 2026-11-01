"""Slotting engine: candidate slots -> scored assignments -> lifecycle states."""
import random
from collections import defaultdict

from .config import SEED, YEAR, SLOT_SCORE_WEIGHTS
from .master import (HAZARD_ZONE_OK, INCOMPAT_HAZARD_PAIRS, TEMP_ZONE_MAP,
                     CATEGORY_ZONE_PREF)


def zone_type_of(master, loc):
    return loc["zone_type"]


def slot_fit(master, sku, profile, loc, zones):
    zone = zones[loc["zone_id"]]
    zt = zone["zone_type"]
    if sku["hazard_class"] != "NONE" and zt not in HAZARD_ZONE_OK[sku["hazard_class"]]:
        return None
    if sku["hazard_class"] != "NONE" and zone.get("hazard_segregation") != sku["hazard_class"]:
        return None
    if sku["temp_class"] != "AMBIENT" and zone.get("storage_temp") != sku["temp_class"]:
        return None
    # A location is a single SKU slot. Stock depth is managed separately by
    # replenishment; never pretend all max stock occupies one bin.
    if sku["unit_volume_cm3"] > loc["capacity_volume_cm3"]:
        return None
    if sku["unit_weight_kg"] > loc["max_weight_kg"]:
        return None
    # Check physical dimensions: product must fit in location
    # Try all 6 orientations (length/width/height permutations)
    sku_dims = [sku["length_cm"], sku["width_cm"], sku["height_cm"]]
    loc_dims = [loc["length_cm"], loc["width_cm"], loc["height_cm"]]
    from itertools import permutations
    fits = False
    for sku_perm in permutations(sku_dims):
        if (sku_perm[0] <= loc_dims[0] and
            sku_perm[1] <= loc_dims[1] and
            sku_perm[2] <= loc_dims[2]):
            fits = True
            break
    if not fits:
        return None
    # All components use 0..100, and only eligible candidates reach this point.
    velocity = {"FAST": 100.0, "MEDIUM": 65.0, "SLOW": 35.0}[profile["velocity_class"]]
    max_distance = getattr(master, "max_dock_distance", None)
    if max_distance is None:
        max_distance = max(1, max(l["x_coord"] + l["y_coord"] for l in master.locations))
    distance = 100.0 * (1 - (loc["x_coord"] + loc["y_coord"]) / max_distance)
    capacity = 100.0 * min(1.0, sku["unit_volume_cm3"] / loc["capacity_volume_cm3"])
    # Fast movers need less replenishment travel/effort when nearer the dock;
    # slow movers are less sensitive to that operational trade-off.
    replenishment = distance if profile["velocity_class"] == "FAST" else (
        0.6 * distance + 40.0)
    preferred = zt in profile["preferred_zone_types"].split(";")
    operational = 100.0 if preferred else 40.0
    components = {
        "velocity": velocity,
        "distance": distance,
        "capacity": capacity,
        "replenishment": replenishment,
        "operational": operational,
    }
    return round(sum(SLOT_SCORE_WEIGHTS[k] * components[k] for k in SLOT_SCORE_WEIGHTS), 2)


def build_slot_assignments(master, use_optimized=True):
    """Build slot assignments. If use_optimized=False, uses random valid placement."""
    rng = random.Random(SEED + 4)
    zones = {z["zone_id"]: z for z in master.zones}
    # Compute once; slot_fit is called for every SKU/location candidate.
    master.max_dock_distance = max(
        1, max(l["x_coord"] + l["y_coord"] for l in master.locations))
    prof = {p["sku_id"]: p for p in master.sku_profiles}
    sku_by_id = {s["sku_id"]: s for s in master.skus}
    locs_by_wh = defaultdict(list)
    for l in master.locations:
        locs_by_wh[l["warehouse_id"]].append(l)

    rows = []
    active_loc = set()

    if use_optimized:
        # Constrained and bulky SKUs claim their feasible locations first; broad
        # ambient candidates remain available for flexible SKUs.
        scarcity = {}
        for sku in master.skus:
            profile = prof[sku["sku_id"]]
            feasible = sum(slot_fit(master, sku, profile, loc, zones) is not None
                           for loc in locs_by_wh[sku["warehouse_id"]])
            scarcity[sku["sku_id"]] = (feasible, -int(sku["unit_volume_cm3"]))
        ordered_skus = sorted(master.skus,
                              key=lambda sku: (*scarcity[sku["sku_id"]], sku["sku_id"]))
    else:
        # Baseline: random order for placement
        ordered_skus = master.skus[:]
        rng.shuffle(ordered_skus)

    i = 0
    slot_number = {sku["sku_id"]: index for index, sku in enumerate(master.skus, 1)}
    for sku in ordered_skus:
        p = prof[sku["sku_id"]]
        cand = [l for l in locs_by_wh[sku["warehouse_id"]]
                if l["occupancy_status"] == "EMPTY" and not l["blocked"]]

        if use_optimized:
            # Optimized: pick best-scoring location
            best, best_score = None, -1
            for l in cand:
                sc = slot_fit(master, sku_by_id[sku["sku_id"]], p, l, zones)
                if sc and sc > best_score:
                    best, best_score = l, sc
        else:
            # Baseline: pick first valid location (random due to shuffle)
            best = None
            for l in cand:
                sc = slot_fit(master, sku_by_id[sku["sku_id"]], p, l, zones)
                if sc:
                    best = l
                    break

        i += 1
        number = slot_number[sku["sku_id"]]
        if best is None:
            compatible_zone_exists = any(
                z["warehouse_id"] == sku["warehouse_id"] and
                (sku["hazard_class"] == "NONE" or
                 (z["zone_type"] == "HAZMAT" and
                  z["hazard_segregation"] == sku["hazard_class"])) and
                (sku["temp_class"] == "AMBIENT" or
                 z["storage_temp"] == sku["temp_class"])
                for z in master.zones)
            rows.append({
                "slot_id": f"SLOT-{YEAR}-{number:06d}", "sku_id": sku["sku_id"],
                "location_id": "", "warehouse_id": sku["warehouse_id"],
                "score": 0,
                "decision": "REJECTED_CAPACITY" if compatible_zone_exists else "REJECTED_COMPAT",
                "status": "rejected",
                "effective_from": f"{YEAR}-01-01", "effective_to": f"{YEAR}-12-31",
            })
            continue
        decision, status = "ASSIGNED", "active"
        if best["location_id"] in active_loc:
            decision = "DUPLICATE"  # legal history row, anomaly flagged separately
        active_loc.add(best["location_id"])
        rows.append({
            "slot_id": f"SLOT-{YEAR}-{number:06d}", "sku_id": sku["sku_id"],
            "location_id": best["location_id"],
            "warehouse_id": sku["warehouse_id"],
            "score": best_score if use_optimized else 50.0,
            "decision": decision, "status": status,
            "effective_from": f"{YEAR}-01-01", "effective_to": f"{YEAR}-12-31",
        })
        best["occupancy_status"] = "OCCUPIED"
    master.slot_assignments = rows
    return rows


def simulate_pick_paths(master, baseline_assignments=None):
    """Compare baseline vs optimized slotting using nearest-neighbor routing.

    Args:
        master: MasterData object with pick_events
        baseline_assignments: If provided, uses these for baseline distance calculation.
                              If None, uses current master.slot_assignments for both.

    Returns metrics comparing slotting impact (not just routing).
    """
    locs = {l["location_id"]: l for l in master.locations}

    # Build location mapping from assignments
    def get_sku_location(assignments):
        return {a["sku_id"]: a["location_id"] for a in assignments
                if a["status"] == "active" and a["decision"] == "ASSIGNED"}

    optimized_sku_loc = get_sku_location(master.slot_assignments)
    baseline_sku_loc = get_sku_location(baseline_assignments) if baseline_assignments else None

    # Compare the exact same SKU/order-line set on each layout. If a baseline
    # cannot place a SKU, exclude that line from both sides and report it.
    per_wave = defaultdict(list)
    for ev in master.pick_events:
        per_wave[(ev["order_id"], ev["shift"])].append(ev)

    baseline_distance = optimized_distance = 0
    orders_with_multi = comparable_events = excluded_events = 0
    zone_by_id = {zone["zone_id"]: zone for zone in master.zones}
    locations_by_zone = defaultdict(list)
    for location in master.locations:
        locations_by_zone[location["zone_id"]].append(location)
    blocked_by_zone = {zone["zone_id"]: zone["blocked_aisle"] for zone in master.zones}

    from .pick_path import WarehouseRouter
    router = WarehouseRouter(master.locations, master.zones)

    def tour_distance(points):
        remaining = list(points)
        current = None
        distance = 0
        while remaining:
            target = min(remaining, key=lambda point: router.distance(
                current, point["location_id"]))
            distance += router.distance(current, target["location_id"])
            current = target["location_id"]
            remaining.remove(target)
        return distance

    for key, evs in sorted(per_wave.items()):
        common_events = [event for event in evs
                         if event["sku_id"] in optimized_sku_loc and
                         (baseline_sku_loc is None or event["sku_id"] in baseline_sku_loc)]
        excluded_events += len(evs) - len(common_events)
        comparable_events += len(common_events)
        seen_opt = set()
        pts_opt = []
        for event in common_events:
            loc_id = optimized_sku_loc.get(event["sku_id"])
            if loc_id and loc_id not in seen_opt:
                seen_opt.add(loc_id)
                pts_opt.append(locs[loc_id])

        if not pts_opt:
            continue
        if len(pts_opt) >= 2:
            orders_with_multi += 1

        optimized_distance += tour_distance(pts_opt)

        # Baseline: use baseline slotting if provided, else same as optimized
        if baseline_sku_loc is not None:
            seen_base = set()
            pts_base = []
            for event in common_events:
                loc_id = baseline_sku_loc.get(event["sku_id"])
                if loc_id and loc_id not in seen_base:
                    seen_base.add(loc_id)
                    pts_base.append(locs[loc_id])

            if pts_base:
                baseline_distance += tour_distance(pts_base)
        else:
            baseline_distance += tour_distance(pts_opt)

    master.pick_metrics = {
        "waves_with_multi_stop_tours": orders_with_multi,
        "comparable_pick_events": comparable_events,
        "excluded_pick_events_missing_layout_assignment": excluded_events,
        "baseline_distance_m": baseline_distance,
        "optimized_distance_m": optimized_distance,
        "savings_pct": round(100 * (baseline_distance - optimized_distance) / baseline_distance, 2)
        if baseline_distance else 0.0,
    }
    return master.pick_metrics


def export(master, outdir):
    from .master import MasterData
    MasterData.write_csv(master.slot_assignments, outdir / "slot_assignment.csv",
                         ["slot_id", "sku_id", "location_id", "warehouse_id", "score",
                          "decision", "status", "effective_from", "effective_to"])
