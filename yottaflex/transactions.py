"""Order / OrderLine / PickEvent generation with peak shifts and valid FKs."""
import datetime as dt
import random
from collections import defaultdict

from .config import SEED, YEAR

SHIFTS = ["DAY", "EVE", "NIGHT"]
STATUS = ["open", "picking", "shipped"]


def build_order_lines(master):
    rng = random.Random(SEED + 1)
    sku_ids = [s["sku_id"] for s in master.skus]
    sku_wh = {s["sku_id"]: s["warehouse_id"] for s in master.skus}
    skus_by_wh = defaultdict(list)
    for sku in master.skus:
        skus_by_wh[sku["warehouse_id"]].append(sku["sku_id"])
    # Pareto-ish velocity within each warehouse: 20% of its assortment receive
    # about 80% of line demand.
    hot_by_wh = {wh: ids[:max(1, int(len(ids) * 0.2))]
                 for wh, ids in skus_by_wh.items()}
    lines, orders = [], {}
    n = master.scale.order_lines
    order_days = list(range(1, 301))
    order_day_weights = [3 if day >= 250 else 1 for day in order_days]
    line_seq = defaultdict(int)
    for i in range(1, n + 1):
        # group into orders of 1..4 lines, deterministic order id
        order_no = (i - 1) // 4 + 1
        oid = f"ORD-{YEAR}-{str(order_no).zfill(6)}"
        if oid in orders:
            wh_id = orders[oid]["warehouse_id"]
            sku_pool = hot_by_wh[wh_id] if rng.random() < 0.8 and hot_by_wh[wh_id] else skus_by_wh[wh_id]
            sku = rng.choice(sku_pool)
        else:
            wh_id = rng.choice(list(skus_by_wh))
            sku_pool = hot_by_wh[wh_id] if rng.random() < 0.8 else skus_by_wh[wh_id]
            sku = rng.choice(sku_pool)
        if oid not in orders:
            day = rng.choices(order_days, weights=order_day_weights, k=1)[0]
            od = dt.date(YEAR, 1, 1) + dt.timedelta(days=day)
            peak_period = "PEAK" if day >= 250 else "NORMAL"
            orders[oid] = {
                "order_id": oid,
                "warehouse_id": sku_wh[sku],
                "customer_ref": f"CUST-{rng.randint(1, 99999):05d}",
                "order_date": od.isoformat(),
                "ship_date": (od + dt.timedelta(days=rng.randint(1, 5))).isoformat(),
                "status": rng.choices(STATUS, [0.15, 0.25, 0.6])[0],
                "channel": rng.choice(["RETAIL", "ECOM", "WHOLESALE"]),
                "peak_period": peak_period,
            }
        else:
            # keep warehouse consistent inside an order
            pass
        lines.append({
            "line_id": f"{oid}-L{line_seq[oid] + 1:03d}",
            "order_id": oid, "sku_id": sku,
            "qty": rng.randint(1, 999 if rng.random() < 0.05 else 60),
            "pick_freq_weight": 1,
        })
        line_seq[oid] += 1
    master.orders = list(orders.values())
    master.order_lines = lines
    return lines


def build_pick_events(master):
    """PickEvents derive locations from SlotAssignments (dependency, not random)."""
    rng = random.Random(SEED + 2)
    slot_by_sku = {}
    for s in master.slot_assignments:
        if s["status"] == "active" and s["decision"] == "ASSIGNED" and s["sku_id"] not in slot_by_sku:
            slot_by_sku[s["sku_id"]] = s["location_id"]
    loc_by_id = {l["location_id"]: l for l in master.locations}
    events = []
    seq_by_order = defaultdict(int)
    order_by_id = {o["order_id"]: o for o in master.orders}
    zone_by_id = {z["zone_id"]: z for z in master.zones}
    from .pick_path import WarehouseRouter
    router = WarehouseRouter(master.locations, master.zones)
    previous_location_by_order = {}
    for ol in master.order_lines:
        loc = slot_by_sku.get(ol["sku_id"])
        if not loc:
            continue
        order = order_by_id[ol["order_id"]]
        seq_by_order[ol["order_id"]] += 1
        l = loc_by_id[loc]
        zone_of = zone_by_id[l["zone_id"]]
        blocked_pick = l["aisle"] == zone_of["blocked_aisle"]
        peak = order["peak_period"] == "PEAK"
        shift = rng.choices(SHIFTS, [0.35, 0.5 if peak else 0.4, 0.15])[0]
        start_key = (order["order_id"], shift)
        previous_location = previous_location_by_order.get(start_key)
        travel, _ = router.route(previous_location, loc)
        previous_location_by_order[start_key] = loc
        hour = {"DAY": rng.randint(6, 13), "EVE": rng.randint(14, 21),
                "NIGHT": rng.choice([22, 23])}[shift]
        events.append({
            "pick_id": f"PICK-{ol['order_id']}-{seq_by_order[ol['order_id']]:03d}",
            "order_id": ol["order_id"], "line_id": ol["line_id"],
            "sku_id": ol["sku_id"], "location_id": loc,
            "pick_ts": f"{order['ship_date']}T{hour:02d}:00:00",
            "shift": shift,
            "travel_distance_m": travel,
            "path_status": "BLOCKED_AISLE" if blocked_pick else "OK",
        })
        # One pick record per order line with an active slot. Do not silently
        # truncate the event history at an arbitrary fraction of the workload.
    master.pick_events = events
    return events


def build_replenishments(master):
    """Replenishment workload derived + min/max stock of profile."""
    rng = random.Random(SEED + 3)
    prof = {p["sku_id"]: p for p in master.sku_profiles}
    slot_by_sku = defaultdict(list)
    for s in master.slot_assignments:
        if s["status"] == "active":
            slot_by_sku[s["sku_id"]].append(s["location_id"])
    rows, i = [], 0
    for sku, locs in slot_by_sku.items():
        p = prof[sku]
        need = max(0, p["max_stock_qty"] - p["min_stock_qty"])
        if need <= 0:
            continue
        i += 1
        qty = max(1, round(need * 0.75))
        base_min = {"FAST": 5, "MEDIUM": 8, "SLOW": 12}[p["velocity_class"]]
        rate_per_unit = {"FAST": 0.04, "MEDIUM": 0.06, "SLOW": 0.08}[p["velocity_class"]]
        labor = round(qty * rate_per_unit + base_min, 2)
        rows.append({
            "replenishment_id": f"REPL-{i:06d}", "sku_id": sku,
            "location_id": locs[0], "qty": qty, "labor_minutes": labor,
            "required_date": "2026-01-01",
            "required_shift": rng.choices(SHIFTS, [0.5, 0.35, 0.15])[0],
            "scheduled_date": "2026-01-01",
            "scheduled_shift": "",
            "priority_class": {"FAST": "HIGH", "MEDIUM": "MEDIUM", "SLOW": "LOW"}[p["velocity_class"]],
            "reschedule_count": 0,
            "scheduled_shift_labor_minutes": 0,
            "remaining_shift_capacity_minutes": 480,
            "scheduled_labor_minutes": 0,
            "deferred_labor_minutes": 0,
            "shift_total_labor_minutes": 0,
            "shift_capacity_minutes": 480,
            "schedule_decision": "UNSCHEDULED",
            "shift": "",
            "status": rng.choices(["pending", "in_progress", "completed"],
                                  [0.3, 0.2, 0.5])[0],
            "over_labor_capacity": 0,  # set by anomalies step
        })
        if i >= master.scale.skus:
            break
    master.replenishments = rows
    return rows


def export(master, outdir):
    from .master import MasterData
    MasterData.write_csv(master.orders, outdir / "order.csv",
                         ["order_id", "warehouse_id", "customer_ref", "order_date",
                         "ship_date", "status", "channel", "peak_period"])
    MasterData.write_csv(master.order_lines, outdir / "order_line.csv",
                         ["line_id", "order_id", "sku_id", "qty", "pick_freq_weight"])
    MasterData.write_csv(master.pick_events, outdir / "pick_event.csv",
                         ["pick_id", "order_id", "line_id", "sku_id", "location_id",
                          "pick_ts", "shift", "travel_distance_m", "path_status"])
    MasterData.write_csv(master.replenishments, outdir / "replenishment.csv",
                         ["replenishment_id", "sku_id", "location_id", "qty",
                          "labor_minutes", "required_date", "required_shift", "scheduled_date",
                          "scheduled_shift", "priority_class", "reschedule_count",
                          "scheduled_shift_labor_minutes", "remaining_shift_capacity_minutes",
                          "scheduled_labor_minutes",
                          "deferred_labor_minutes", "shift_total_labor_minutes",
                          "shift_capacity_minutes", "schedule_decision", "shift",
                          "status", "over_labor_capacity"])
    MasterData.write_csv(master.replenishment_events, outdir / "replenishment_event.csv",
                         ["event_id", "replenishment_id", "event_type", "from_status",
                          "to_status", "event_timestamp", "from_date", "from_shift",
                          "to_date", "to_shift", "priority_class"])
