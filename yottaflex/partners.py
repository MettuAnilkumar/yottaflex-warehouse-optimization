"""Synthetic supplier, carrier, inbound shipment, and event records."""
import csv
import random
import datetime as dt
from pathlib import Path

from .config import SEED, YEAR


def build_partner_records(master):
    rng = random.Random(SEED + 11)
    supplier_count = max(3, master.scale.warehouses * 2)
    carrier_count = max(2, master.scale.warehouses)
    shipment_count = max(1, master.scale.order_lines // 100)
    master.suppliers = [
        {"supplier_id": f"SUP-{i:06d}", "name": f"Synthetic Supplier {i:06d}",
         "country": ("USA", "Netherlands", "Japan", "Singapore")[i % 4],
         "status": "ACTIVE"}
        for i in range(1, supplier_count + 1)
    ]
    master.carriers = [
        {"carrier_id": f"CAR-{i:06d}", "name": f"Synthetic Carrier {i:06d}",
         "transport_mode": ("ROAD", "AIR", "RAIL", "OCEAN")[i % 4],
         "status": "ACTIVE"}
        for i in range(1, carrier_count + 1)
    ]

    master.inbound_shipments, master.inbound_events = [], []
    event_number = 0
    for number in range(1, shipment_count + 1):
        shipment_id = f"INB-{YEAR}-{number:06d}"
        sku = master.skus[(number - 1) % len(master.skus)]
        sku_id = sku["sku_id"]
        warehouse_id = sku["warehouse_id"]
        supplier_id = master.suppliers[(number - 1) % len(master.suppliers)]["supplier_id"]
        carrier_id = master.carriers[(number - 1) % len(master.carriers)]["carrier_id"]
        qty = rng.randint(10, 500)
        booked_date_obj = dt.date(YEAR, 1, 1) + dt.timedelta(days=(number - 1) % 300)
        dispatch_date = booked_date_obj + dt.timedelta(days=1)
        arrival_date = dispatch_date + dt.timedelta(days=rng.randint(1, 5))
        status = rng.choices(["BOOKED", "IN_TRANSIT", "RECEIVED"], [0.1, 0.2, 0.7])[0]
        master.inbound_shipments.append({
            "shipment_id": shipment_id, "warehouse_id": warehouse_id,
            "supplier_id": supplier_id, "carrier_id": carrier_id,
            "sku_id": sku_id, "qty": qty,
            "booked_date": booked_date_obj.isoformat(),
            "planned_arrival_date": arrival_date.isoformat(),
            "status": status,
            "received_qty": qty if status == "RECEIVED" else 0,
        })
        transitions = [("", "BOOKED", f"{booked_date_obj.isoformat()}T00:00:00")]
        if status in {"IN_TRANSIT", "RECEIVED"}:
            transitions.append(("BOOKED", "IN_TRANSIT", f"{dispatch_date.isoformat()}T08:00:00"))
        if status == "RECEIVED":
            transitions.append(("IN_TRANSIT", "RECEIVED", f"{arrival_date.isoformat()}T08:00:00"))
        for prior, current, timestamp in transitions:
            event_number += 1
            master.inbound_events.append({
                "event_id": f"INBEVT-{event_number:07d}",
                "shipment_id": shipment_id, "event_type": current,
                "from_status": prior, "to_status": current,
                "event_timestamp": timestamp,
            })


def export(master, outdir: Path):
    records = [
        ("supplier.csv", master.suppliers,
         ["supplier_id", "name", "country", "status"]),
        ("carrier.csv", master.carriers,
         ["carrier_id", "name", "transport_mode", "status"]),
        ("inbound_shipment.csv", master.inbound_shipments,
         ["shipment_id", "warehouse_id", "supplier_id", "carrier_id", "sku_id",
          "qty", "booked_date", "planned_arrival_date", "status", "received_qty"]),
        ("inbound_event.csv", master.inbound_events,
         ["event_id", "shipment_id", "event_type", "from_status", "to_status",
          "event_timestamp"]),
    ]
    for filename, rows, fields in records:
        with open(outdir / filename, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
