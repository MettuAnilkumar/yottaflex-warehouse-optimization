"""Export SQLite-compatible SQL seed (DDL + bulk INSERTs)."""
from pathlib import Path

DDL = """
PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
CREATE TABLE warehouse (warehouse_id TEXT PRIMARY KEY, name TEXT, city TEXT, country TEXT, region TEXT, opened_date TEXT);
CREATE TABLE zone (zone_id TEXT PRIMARY KEY, warehouse_id TEXT REFERENCES warehouse(warehouse_id), zone_type TEXT, hazard_segregation TEXT, storage_temp TEXT, blocked_aisle TEXT, aisle_count INT);
CREATE TABLE location (location_id TEXT PRIMARY KEY, zone_id TEXT REFERENCES zone(zone_id), warehouse_id TEXT REFERENCES warehouse(warehouse_id), zone_type TEXT, loc_type TEXT, aisle TEXT, rack_level INT, capacity_volume_cm3 INT, max_weight_kg REAL, length_cm INT, width_cm INT, height_cm INT, x_coord INT, y_coord INT, blocked INT, occupancy_status TEXT);
CREATE TABLE sku (sku_id TEXT PRIMARY KEY, warehouse_id TEXT REFERENCES warehouse(warehouse_id), name TEXT, category TEXT, hazard_class TEXT, temp_class TEXT, unit_weight_kg REAL, unit_volume_cm3 INT, length_cm INT, width_cm INT, height_cm INT, oversized INT);
CREATE TABLE sku_profile (profile_id TEXT PRIMARY KEY, sku_id TEXT UNIQUE REFERENCES sku(sku_id), pick_frequency_30d INT, avg_daily_picks REAL, min_stock_qty INT, max_stock_qty INT, preferred_zone_types TEXT, incompat_with TEXT, velocity_class TEXT, abc_class TEXT);
CREATE TABLE orders (order_id TEXT PRIMARY KEY, warehouse_id TEXT REFERENCES warehouse(warehouse_id), customer_ref TEXT, order_date TEXT, ship_date TEXT, status TEXT, channel TEXT, peak_period TEXT);
CREATE TABLE order_line (line_id TEXT PRIMARY KEY, order_id TEXT REFERENCES orders(order_id), sku_id TEXT REFERENCES sku(sku_id), qty INT, pick_freq_weight INT);
CREATE TABLE pick_event (pick_id TEXT PRIMARY KEY, order_id TEXT REFERENCES orders(order_id), line_id TEXT REFERENCES order_line(line_id), sku_id TEXT REFERENCES sku(sku_id), location_id TEXT REFERENCES location(location_id), pick_ts TEXT, shift TEXT, travel_distance_m REAL, path_status TEXT);
CREATE TABLE replenishment (replenishment_id TEXT PRIMARY KEY, sku_id TEXT REFERENCES sku(sku_id), location_id TEXT REFERENCES location(location_id), qty INT, labor_minutes REAL, required_date TEXT, required_shift TEXT, scheduled_date TEXT, scheduled_shift TEXT, priority_class TEXT, reschedule_count INT, scheduled_shift_labor_minutes REAL, remaining_shift_capacity_minutes REAL, scheduled_labor_minutes REAL, deferred_labor_minutes REAL, shift_total_labor_minutes REAL, shift_capacity_minutes REAL, schedule_decision TEXT, shift TEXT, status TEXT, over_labor_capacity INT);
CREATE TABLE replenishment_event (event_id TEXT PRIMARY KEY, replenishment_id TEXT REFERENCES replenishment(replenishment_id), event_type TEXT, from_status TEXT, to_status TEXT, event_timestamp TEXT, from_date TEXT, from_shift TEXT, to_date TEXT, to_shift TEXT, priority_class TEXT);
CREATE TABLE slot_assignment (slot_id TEXT PRIMARY KEY, sku_id TEXT REFERENCES sku(sku_id), location_id TEXT REFERENCES location(location_id), warehouse_id TEXT REFERENCES warehouse(warehouse_id), score REAL, decision TEXT, status TEXT, effective_from TEXT, effective_to TEXT);
CREATE TABLE assignment_event (event_id TEXT PRIMARY KEY, slot_id TEXT REFERENCES slot_assignment(slot_id), sku_id TEXT REFERENCES sku(sku_id), location_id TEXT REFERENCES location(location_id), warehouse_id TEXT REFERENCES warehouse(warehouse_id), event_type TEXT, from_status TEXT, to_status TEXT, decision TEXT, event_timestamp TEXT, effective_from TEXT, effective_to TEXT, move_id TEXT, related_slot_id TEXT REFERENCES slot_assignment(slot_id));
CREATE TABLE ground_truth (record_id TEXT, entity TEXT, issue_type TEXT, source_ids TEXT, affected_ids TEXT, reason TEXT, expected_decision TEXT, severity TEXT, resolution_status TEXT);
CREATE TABLE supplier (supplier_id TEXT PRIMARY KEY, name TEXT, country TEXT, status TEXT);
CREATE TABLE carrier (carrier_id TEXT PRIMARY KEY, name TEXT, transport_mode TEXT, status TEXT);
CREATE TABLE inbound_shipment (shipment_id TEXT PRIMARY KEY, warehouse_id TEXT REFERENCES warehouse(warehouse_id), supplier_id TEXT REFERENCES supplier(supplier_id), carrier_id TEXT REFERENCES carrier(carrier_id), sku_id TEXT REFERENCES sku(sku_id), qty INT, booked_date TEXT, planned_arrival_date TEXT, status TEXT, received_qty INT);
CREATE TABLE inbound_event (event_id TEXT PRIMARY KEY, shipment_id TEXT REFERENCES inbound_shipment(shipment_id), event_type TEXT, from_status TEXT, to_status TEXT, event_timestamp TEXT);
"""


def _lit(v):
    if v in ("", None):
        return "NULL"
    s = str(v)
    if s.replace(".", "", 1).replace("-", "", 1).isdigit():
        return s
    return "'" + s.replace("'", "''") + "'"


def export_sql(csv_dir: Path, out: Path):
    import csv
    tables = [("warehouse", "warehouse.csv"), ("zone", "zone.csv"),
              ("location", "location.csv"), ("sku", "sku.csv"),
              ("sku_profile", "sku_profile.csv"), ("orders", "order.csv"),
              ("order_line", "order_line.csv"), ("pick_event", "pick_event.csv"),
              ("replenishment", "replenishment.csv"),
              ("replenishment_event", "replenishment_event.csv"),
              ("slot_assignment", "slot_assignment.csv"),
              ("assignment_event", "assignment_event.csv"),
              ("ground_truth", "ground_truth.csv"),
              ("supplier", "supplier.csv"),
              ("carrier", "carrier.csv"),
              ("inbound_shipment", "inbound_shipment.csv"),
              ("inbound_event", "inbound_event.csv")]
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(DDL)
        for table, fname in tables:
            with open(csv_dir / fname, encoding="utf-8") as c:
                for row in csv.DictReader(c):
                    vals = ", ".join(_lit(v) for v in row.values())
                    fh.write(f"INSERT INTO {table} VALUES ({vals});\n")
        fh.write("COMMIT;\n")
