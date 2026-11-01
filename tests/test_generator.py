import csv
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yottaflex.anomalies import inject  # noqa: E402
from yottaflex.config import SCALE_FULL, SCALE_STARTER  # noqa: E402
from yottaflex.master import MasterData  # noqa: E402
from yottaflex.slotting import build_slot_assignments  # noqa: E402
from yottaflex.assignment_history import apply_assignment_moves, build_assignment_history  # noqa: E402
from yottaflex.transactions import build_pick_events, build_replenishments  # noqa: E402
from yottaflex.pick_path import WarehouseRouter  # noqa: E402
from yottaflex.validation import run as validate_run  # noqa: E402


def _build(scale=SCALE_STARTER):
    m = MasterData(scale)
    m.build()
    build_slot_assignments(m, use_optimized=True)
    apply_assignment_moves(m)
    build_pick_events(m)
    build_replenishments(m)
    inject(m)
    build_assignment_history(m)
    return m


def test_pk_uniqueness_and_fk_integrity(tmp_path):
    m = _build()
    sku_ids = [s["sku_id"] for s in m.skus]
    assert len(sku_ids) == len(set(sku_ids)), "SKU PK duplicated"
    zone_ids = {z["zone_id"] for z in m.zones}
    assert all(l["zone_id"] in zone_ids for l in m.locations), "bad zone FK"
    order_ids = {o["order_id"] for o in m.orders}
    assert all(r["order_id"] in order_ids for r in m.order_lines)


def test_id_nomenclature():
    m = _build()
    import re
    assert re.fullmatch(r"WH-\d{3}", m.warehouses[0]["warehouse_id"])
    assert m.locations[0]["location_id"].startswith("LOC-")
    assert m.skus[0]["sku_id"] == "SKU-000001"
    assert m.order_lines[0]["order_id"].startswith("ORD-2026-")
    assert m.slot_assignments[0]["slot_id"].startswith("SLOT-2026-")
    assert all(re.fullmatch(r"SLOT-2026-\d{6}", row["slot_id"])
               for row in m.slot_assignments)


def test_validation_reconciliation(tmp_path):
    out = Path(__file__).resolve().parents[1] / "data_starter"
    if out.exists():
        result = validate_run(out)
        assert result["passed"], result["issues"]


def test_anomalies_are_labelled_not_corrupting():
    m = _build()
    types = {g["issue_type"] for g in m.ground_truth}
    assert "CAPACITY_EXCEEDED" in types
    assert "DUPLICATE_ACTIVE_SLOT" in types
    assert "INCOMPATIBLE_COLOCATION" in types
    assert "REPLEN_LABOR_EXCEEDED" in types
    assert "BLOCKED_AISLE_PICK" in types
    # ground truth kept separately: not merged into sku/order features
    assert all("issue_type" not in s for s in m.skus)


def test_pick_events_cover_order_lines_with_active_slots():
    m = _build()
    active_skus = {s["sku_id"] for s in m.slot_assignments
                   if s["status"] == "active" and s["decision"] == "ASSIGNED"}
    expected = sum(line["sku_id"] in active_skus for line in m.order_lines)
    assert len(m.pick_events) == expected


def test_reproducibility_same_seed(tmp_path):
    root = Path(__file__).resolve().parents[1]
    outputs = [tmp_path / "run_a", tmp_path / "run_b"]
    for output in outputs:
        subprocess.run([sys.executable, "-m", "yottaflex.generate", "--scale",
                        "starter", "--out", str(output)], cwd=root,
                       check=True, capture_output=True, text=True)
    hashes = []
    for output in outputs:
        hashes.append({path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in output.rglob("*")
                       if path.is_file() and path.name != "manifest.json"})
    assert hashes[0] == hashes[1]
    for output in outputs:
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["run_timestamp_utc"]
        assert manifest["data_provenance"].startswith("Synthetic data only")
        assert "source_manifest" in manifest
        assert len(manifest["files"]) == len(hashes[0])


def test_sql_seed_loads_with_foreign_keys(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "sql_run"
    subprocess.run([sys.executable, "-m", "yottaflex.generate", "--scale",
                    "starter", "--out", str(output)], cwd=root,
                   check=True, capture_output=True, text=True)
    connection = sqlite3.connect(":memory:")
    connection.executescript((output / "seed.sql").read_text(encoding="utf-8"))
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("SELECT COUNT(*) FROM order_line").fetchone()[0] == 2_000
    assert connection.execute("SELECT COUNT(*) FROM assignment_event").fetchone()[0] > 0
    assert connection.execute("SELECT COUNT(*) FROM inbound_event").fetchone()[0] > 0


def test_validate_only_cli(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "validate_run"
    subprocess.run([sys.executable, "-m", "yottaflex.generate", "--scale",
                    "starter", "--out", str(output)], cwd=root,
                   check=True, capture_output=True, text=True)
    result = subprocess.run([sys.executable, "-m", "yottaflex.generate",
                             "--validate-only", "--out", str(output)], cwd=root,
                            check=True, capture_output=True, text=True)
    assert '"passed": true' in result.stdout


def test_blocked_aisle_graph_excludes_closed_nodes_from_transit():
    locations = [
        {"location_id": "L1", "warehouse_id": "W1", "zone_id": "Z1", "aisle": "A01", "x_coord": 1, "y_coord": 1, "rack_level": 1},
        {"location_id": "L2", "warehouse_id": "W1", "zone_id": "Z1", "aisle": "A02", "x_coord": 2, "y_coord": 2, "rack_level": 1},
        {"location_id": "L3", "warehouse_id": "W1", "zone_id": "Z1", "aisle": "A03", "x_coord": 3, "y_coord": 3, "rack_level": 1},
    ]
    zones = [{"zone_id": "Z1", "warehouse_id": "W1", "blocked_aisle": "A02"}]
    router = WarehouseRouter(locations, zones)
    distance, path = router.route(None, "L3")
    assert distance < float("inf")
    assert "L2" not in path
    target_distance, target_path = router.route("L1", "L2")
    assert target_distance < float("inf")
    assert target_path[-1] == "L2"  # a closed aisle remains a valid pick endpoint


def test_reassignment_and_rescheduling_histories_are_complete():
    m = _build()
    assert m.assignment_moves
    event_types = {event["event_type"] for event in m.assignment_events}
    assert {"ASSIGNMENT_DEACTIVATED", "ASSIGNMENT_RETIRED", "REASSIGNMENT_ACTIVATED"} <= event_types
    assert any(task["schedule_decision"] == "RESCHEDULED" for task in m.replenishments)
    assert {event["event_type"] for event in m.replenishment_events} >= {"REQUESTED", "DEFERRED", "RESCHEDULED"}
