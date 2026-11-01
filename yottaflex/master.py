"""Deterministic, seeded synthetic master-data generation.

Order matters: warehouses -> zones -> locations -> SKUs -> profiles.
All downstream entities reference these master records.
"""
import csv
import datetime as dt
import random
from pathlib import Path

from .config import SEED, YEAR

REGIONS = ["NA-EAST", "NA-WEST", "NA-CENTRAL", "EU", "APAC"]
CITIES = ["Atlanta", "Seattle", "Dallas", "Rotterdam", "Osaka", "Chicago",
          "Denver", "Miami", "Toronto", "Hamburg", "Singapore", "Sydney",
          "Phoenix", "Boston", "Austin", "Lyon", "Mexico City", "Vancouver",
          "Newark", "Incheon"]
ZONE_TYPES = ["AMBIENT", "COLD", "FROZEN", "HAZMAT", "BULK"]
CATEGORIES = ["FOOD", "BEVERAGE", "PHARMA", "CHEMICAL", "ELECTRONICS",
              "APPAREL", "HOME"]
HAZARDS = ["NONE", "FLAMMABLE", "CORROSIVE", "TOXIC"]
TEMPS = ["AMBIENT", "CHILLED", "FROZEN"]

# zone_type -> allowed location types
LOC_TYPES = {
    "AMBIENT": ["SHELF", "PALLET", "FLOOR"],
    "COLD": ["COOLER"],
    "FROZEN": ["FREEZER"],
    "HAZMAT": ["HAZ_CABINET"],
    "BULK": ["FLOOR", "PALLET"],
}
CATEGORY_ZONE_PREF = {
    "FOOD": ["AMBIENT", "COLD"], "BEVERAGE": ["AMBIENT", "BULK"],
    "PHARMA": ["COLD", "AMBIENT"], "CHEMICAL": ["HAZMAT", "AMBIENT"],
    "ELECTRONICS": ["AMBIENT"], "APPAREL": ["AMBIENT"], "HOME": ["BULK", "AMBIENT"],
}
HAZARD_ZONE_OK = {"NONE": set(ZONE_TYPES), "FLAMMABLE": {"HAZMAT"},
                  "CORROSIVE": {"HAZMAT"}, "TOXIC": {"HAZMAT"}}
# hazard/hazard pairs that must never share a zone (colocation anomaly basis)
INCOMPAT_HAZARD_PAIRS = {("FLAMMABLE", "CORROSIVE"), ("FLAMMABLE", "TOXIC"),
                         ("CORROSIVE", "TOXIC")}
TEMP_ZONE_MAP = {"CHILLED": {"COLD"}, "FROZEN": {"FROZEN"}}

PALLET_VOL = 1_200_000  # cm3 reference for oversized detection


def _zero(n, width):
    return str(n).zfill(width)


class MasterData:
    def __init__(self, scale):
        self.scale = scale
        self.rng = random.Random(SEED)
        self.warehouses, self.zones, self.locations = [], [], []
        self.skus, self.sku_profiles = [], []
        self.orders, self.order_lines = [], []
        self.replenishments, self.replenishment_events = [], []
        self.assignment_moves, self.assignment_events, self.ground_truth = [], [], []
        self.skus_by_wh = {}

    # ---------- warehouses ----------
    def gen_warehouses(self):
        rng = self.rng
        for i in range(1, self.scale.warehouses + 1):
            wid = f"WH-{_zero(i, 3)}"
            self.warehouses.append({
                "warehouse_id": wid,
                "name": f"Warehouse {CITIES[i - 1]}",
                "city": CITIES[i - 1],
                "country": ("USA" if i <= 3 or i in (6, 7, 8, 13, 14, 15, 17)
                            else "other"),
                "region": REGIONS[(i - 1) % len(REGIONS)],
                "opened_date": f"{rng.randint(2000, 2025)}-"
                               f"{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            })

    # ---------- zones ----------
    def gen_zones(self):
        rng = self.rng
        for wh in self.warehouses:
            wid = wh["warehouse_id"]
            # Every warehouse has each storage environment so SKU assignment
            # decisions reflect physical fit instead of missing zone coverage.
            types = list(ZONE_TYPES)
            for z, zt in enumerate(types):
                zone_id = f"ZN-{wid[3:]}-{chr(ord('A') + z)}"
                blocked_aisle = rng.choice(["A01", "A02", "A03"])
                self.zones.append({
                    "zone_id": zone_id, "warehouse_id": wid, "zone_type": zt,
                    "hazard_segregation": "NONE",
                    "storage_temp": {"COLD": "CHILLED", "FROZEN": "FROZEN"}.get(zt, "AMBIENT"),
                    "blocked_aisle": blocked_aisle, "aisle_count": rng.randint(3, 5),
                })
            # Dedicated hazmat compartments prevent incompatible classes from
            # sharing a zone while retaining HAZMAT as the storage zone type.
            for code, hazard in (("HF", "FLAMMABLE"), ("HC", "CORROSIVE"), ("HT", "TOXIC")):
                for temp_code, temp in (("A", "AMBIENT"), ("C", "CHILLED"), ("F", "FROZEN")):
                    self.zones.append({
                        "zone_id": f"ZN-{wid[3:]}-{code}-{temp_code}", "warehouse_id": wid,
                        "zone_type": "HAZMAT", "hazard_segregation": hazard,
                        "storage_temp": temp,
                        "blocked_aisle": rng.choice(["A01", "A02", "A03"]),
                        "aisle_count": rng.randint(3, 5),
                    })

    # ---------- locations ----------
    def gen_locations(self):
        rng = self.rng
        zones = list(self.zones)
        seq = 0
        zone_seq = {}
        target = self.scale.locations
        while seq < target:
            zone = zones[seq % len(zones)]
            seq += 1
            zid = zone["zone_id"]
            zone_seq[zid] = zone_seq.get(zid, 0) + 1
            lt = rng.choice(LOC_TYPES[zone["zone_type"]])
            aisle = f"A{rng.randint(1, zone['aisle_count']):02d}"
            level = rng.randint(1, 5)
            cap_v, cap_w, dims = {
                "SHELF": (120_000, 80, (60, 40, 50)),
                "PALLET": (1_200_000, 900, (120, 100, 100)),
                "FLOOR": (3_000_000, 2000, (200, 150, 100)),
                "COOLER": (200_000, 150, (80, 60, 50)),
                "FREEZER": (200_000, 150, (80, 60, 50)),
                "HAZ_CABINET": (500_000, 500, (100, 80, 60)),
            }[lt]
            length_cm, width_cm, height_cm = dims
            blocked = 0
            self.locations.append({
                "location_id": f"LOC-{zid}-{_zero(zone_seq[zid], 6)}",
                "zone_id": zid,
                "warehouse_id": zone["warehouse_id"],
                "zone_type": zone["zone_type"],
                "loc_type": lt,
                "aisle": aisle,
                "rack_level": level,
                "capacity_volume_cm3": cap_v,
                "max_weight_kg": cap_w,
                "length_cm": length_cm,
                "width_cm": width_cm,
                "height_cm": height_cm,
                "x_coord": rng.randint(1, 60),
                "y_coord": rng.randint(1, 40),
                "blocked": blocked,
                "occupancy_status": "EMPTY",
            })

    # ---------- SKUs + profiles ----------
    def gen_skus(self):
        rng = self.rng
        for i in range(1, self.scale.skus + 1):
            sid = f"SKU-{_zero(i, 6)}"
            wh = self.warehouses[i % len(self.warehouses)]["warehouse_id"]
            seeded_hazard_case = i <= 3 * len(self.warehouses)
            cat = "CHEMICAL" if seeded_hazard_case else rng.choice(CATEGORIES)
            if seeded_hazard_case:
                hazard = ("FLAMMABLE", "CORROSIVE", "TOXIC")[(i - 1) // len(self.warehouses)]
            elif cat == "CHEMICAL":
                hazard = rng.choice(["FLAMMABLE", "CORROSIVE", "TOXIC"])
            elif cat == "PHARMA":
                hazard = rng.choice(["NONE", "TOXIC"])
            else:
                hazard = "NONE"
            temp = rng.choice(TEMPS) if cat in ("FOOD", "BEVERAGE", "PHARMA") else "AMBIENT"
            vol = int(rng.lognormvariate(11.0, 1.0))  # ~ 60k..3M cm3 skew
            vol = max(5_000, min(vol, 3_500_000))
            w = round(max(0.05, vol * rng.uniform(0.0002, 0.0012)), 2)
            # Generate dimensions that approximately match volume
            # Volume = length * width * height, so we derive reasonable dimensions
            base_dim = int(vol ** (1/3))  # cube root approximation
            length_cm = rng.randint(int(base_dim * 0.8), int(base_dim * 1.2))
            width_cm = rng.randint(int(base_dim * 0.7), int(base_dim * 1.1))
            height_cm = max(1, vol // (length_cm * width_cm))
            # Recalculate volume from dimensions for consistency
            actual_vol = length_cm * width_cm * height_cm
            self.skus.append({
                "sku_id": sid, "warehouse_id": wh, "name": f"{cat.title()} Item {i}",
                "category": cat, "hazard_class": hazard, "temp_class": temp,
                "unit_weight_kg": w, "unit_volume_cm3": actual_vol,
                "length_cm": length_cm,
                "width_cm": width_cm,
                "height_cm": height_cm,
                "oversized": int(actual_vol > PALLET_VOL),
            })
            self.skus_by_wh.setdefault(wh, []).append(sid)

    def gen_profiles(self, order_lines):
        """Derive ABC / velocity from actual order-line pick counts (no leakage)."""
        picks = {}
        order_by_id = {order["order_id"]: order for order in self.orders}
        latest_ship = max((dt.date.fromisoformat(order["ship_date"])
                           for order in self.orders), default=dt.date(YEAR, 1, 1))
        window_start = latest_ship - dt.timedelta(days=29)
        for ol in order_lines:
            order = order_by_id[ol["order_id"]]
            if dt.date.fromisoformat(order["ship_date"]) >= window_start:
                picks[ol["sku_id"]] = picks.get(ol["sku_id"], 0) + ol["qty"]
        ranked = sorted(picks, key=lambda s: -picks.get(s, 0))
        n = max(1, len(ranked))
        cutoff_a, cutoff_b = int(n * 0.2), int(n * 0.5)
        abc = {}
        for rank, sid in enumerate(ranked):
            abc[sid] = "A" if rank < cutoff_a else ("B" if rank < cutoff_b else "C")
        profiles_by_sku = {profile["sku_id"]: profile for profile in self.sku_profiles}
        for sku in self.skus:
            sid = sku["sku_id"]
            if sid not in abc:
                abc[sid] = "C"  # SKU with zero picks
            f = picks.get(sid, 0)
            pref = CATEGORY_ZONE_PREF[sku["category"]]
            if sku["hazard_class"] != "NONE":
                pref = ["HAZMAT"]
            incompat = []
            if sku["hazard_class"] in ("FLAMMABLE", "CORROSIVE", "TOXIC"):
                incompat = [h for h in HAZARDS
                            if h != sku["hazard_class"] and h != "NONE"]
            profile_row = {
                "profile_id": f"PRF-{sid[4:]}", "sku_id": sid,
                "pick_frequency_30d": f,
                "avg_daily_picks": round(f / 30, 3),
                "min_stock_qty": max(1, f // 10),
                "max_stock_qty": max(10, f * 3),
                "preferred_zone_types": ";".join(pref),
                "incompat_with": ";".join(incompat),
                "velocity_class": ("FAST" if abc[sid] == "A" else
                                   "MEDIUM" if abc[sid] == "B" else "SLOW"),
                "abc_class": abc[sid],
            }
            existing = profiles_by_sku.get(sid)
            if existing is None:
                self.sku_profiles.append(profile_row)
                profiles_by_sku[sid] = profile_row
            else:
                existing.update(profile_row)

    def build_order_lines_stub(self):
        """Temporary in-memory order lines so profile derivation works before
        transactions module finalizes them; transactions module owns the rows."""
        from .transactions import build_order_lines
        return build_order_lines(self)

    def build(self):
        from .transactions import build_order_lines
        self.gen_warehouses()
        self.gen_zones()
        self.gen_locations()
        self.gen_skus()
        # Materialize profile identities and default attributes before child
        # order data; demand-derived fields are filled after order lines exist.
        self.gen_profiles([])
        lines = build_order_lines(self)
        self.gen_profiles(lines)
        return lines

    # ---------- export ----------
    @staticmethod
    def write_csv(rows, path: Path, fields):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    def export(self, outdir: Path):
        self.write_csv(self.warehouses, outdir / "warehouse.csv",
                       ["warehouse_id", "name", "city", "country", "region", "opened_date"])
        self.write_csv(self.zones, outdir / "zone.csv",
                       ["zone_id", "warehouse_id", "zone_type", "hazard_segregation", "storage_temp",
                        "blocked_aisle", "aisle_count"])
        self.write_csv(self.locations, outdir / "location.csv",
                       ["location_id", "zone_id", "warehouse_id", "zone_type", "loc_type",
                        "aisle", "rack_level", "capacity_volume_cm3", "max_weight_kg",
                        "length_cm", "width_cm", "height_cm",
                        "x_coord", "y_coord", "blocked", "occupancy_status"])
        self.write_csv(self.skus, outdir / "sku.csv",
                       ["sku_id", "warehouse_id", "name", "category", "hazard_class",
                        "temp_class", "unit_weight_kg", "unit_volume_cm3",
                        "length_cm", "width_cm", "height_cm", "oversized"])
        self.write_csv(self.sku_profiles, outdir / "sku_profile.csv",
                       ["profile_id", "sku_id", "pick_frequency_30d", "avg_daily_picks",
                        "min_stock_qty", "max_stock_qty", "preferred_zone_types",
                        "incompat_with", "velocity_class", "abc_class"])
