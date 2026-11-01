"""Warehouse aisle graph and blocked-aisle route calculation."""
import heapq
from collections import defaultdict

CROSS_AISLE_COST_M = 8.0
ZONE_TRANSFER_COST_M = 5.0


def _add_edge(graph, left, right, distance):
    distance = max(0.1, float(distance))
    graph[left].append((right, distance))
    graph[right].append((left, distance))


def build_warehouse_graph(locations, zones):
    """Build a sparse location graph with within-aisle and cross-aisle links.

    Locations in a zone/aisle are connected in order along the aisle. Each
    location is also linked to its nearest pick face in every other aisle in
    that zone, representing cross-aisle access. Zone aisle entrances link to a
    warehouse dock node. All distances are derived from generated coordinates
    plus the documented cross-aisle/zone-transfer costs.
    """
    graph = defaultdict(list)
    location_by_id = {loc["location_id"]: loc for loc in locations}
    zone_by_id = {zone["zone_id"]: zone for zone in zones}
    aisles_by_zone = defaultdict(lambda: defaultdict(list))

    for loc in locations:
        graph[loc["location_id"]]
        aisles_by_zone[loc["zone_id"]][loc["aisle"]].append(loc)

    dock_by_warehouse = {}
    blocked_by_warehouse = defaultdict(set)
    for zone_id, aisles in aisles_by_zone.items():
        zone = zone_by_id[zone_id]
        warehouse_id = zone["warehouse_id"]
        dock = dock_by_warehouse.setdefault(warehouse_id, f"DOCK:{warehouse_id}")
        graph[dock]
        for aisle_id, aisle_locations in aisles.items():
            if aisle_id == zone["blocked_aisle"]:
                blocked_by_warehouse[warehouse_id].update(
                    loc["location_id"] for loc in aisle_locations)
            aisle_locations.sort(key=lambda loc: (loc["y_coord"], loc["x_coord"],
                                                   loc["rack_level"]))
            for left, right in zip(aisle_locations, aisle_locations[1:]):
                distance = (abs(left["y_coord"] - right["y_coord"])
                            + 2 * abs(left["rack_level"] - right["rack_level"]))
                _add_edge(graph, left["location_id"], right["location_id"], distance)

            # The dock can enter each open aisle at its nearest location. A
            # closed aisle has no dock edge; its pick faces remain terminal
            # destinations reachable from an open cross-aisle connector.
            if aisle_id != zone["blocked_aisle"] and aisle_locations:
                entrance = min(aisle_locations,
                               key=lambda loc: abs(loc["x_coord"]) + abs(loc["y_coord"]))
                _add_edge(graph, dock, entrance["location_id"],
                          abs(entrance["x_coord"]) + abs(entrance["y_coord"])
                          + ZONE_TRANSFER_COST_M)

        aisle_ids = sorted(aisles, key=lambda value: int(value[1:]) if value[1:].isdigit() else value)
        for i, aisle_id in enumerate(aisle_ids):
            left_group = aisles[aisle_id]
            for other_id in aisle_ids[i + 1:]:
                right_group = aisles[other_id]
                lane_gap = abs(int(aisle_id[1:]) - int(other_id[1:])) if (
                    aisle_id[1:].isdigit() and other_id[1:].isdigit()) else 1
                for loc in left_group:
                    bridge = min(right_group,
                                 key=lambda other: (abs(loc["y_coord"] - other["y_coord"]),
                                                    abs(loc["x_coord"] - other["x_coord"])))
                    distance = (abs(loc["y_coord"] - bridge["y_coord"])
                                + abs(loc["x_coord"] - bridge["x_coord"])
                                + CROSS_AISLE_COST_M * lane_gap)
                    _add_edge(graph, loc["location_id"], bridge["location_id"], distance)
                for loc in right_group:
                    bridge = min(left_group,
                                 key=lambda other: (abs(loc["y_coord"] - other["y_coord"]),
                                                    abs(loc["x_coord"] - other["x_coord"])))
                    distance = (abs(loc["y_coord"] - bridge["y_coord"])
                                + abs(loc["x_coord"] - bridge["x_coord"])
                                + CROSS_AISLE_COST_M * lane_gap)
                    _add_edge(graph, loc["location_id"], bridge["location_id"], distance)

    return graph, location_by_id, dock_by_warehouse, blocked_by_warehouse


def dijkstra(graph, start, end, blocked_nodes=None):
    """Return shortest distance and node path, excluding blocked aisle nodes.

    A blocked node may be the route endpoint (a deliberate blocked-pick
    exception), but it cannot be used as a transit node.
    """
    blocked_nodes = blocked_nodes or set()
    if start == end:
        return 0.0, [start]
    queue = [(0.0, start)]
    best = {start: 0.0}
    previous = {}
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != best.get(node):
            continue
        if node == end:
            path = [end]
            while path[-1] != start:
                path.append(previous[path[-1]])
            path.reverse()
            return distance, path
        for neighbor, edge_cost in graph.get(node, ()):
            if neighbor in blocked_nodes and neighbor != end:
                continue
            candidate = distance + edge_cost
            if candidate < best.get(neighbor, float("inf")):
                best[neighbor] = candidate
                previous[neighbor] = node
                heapq.heappush(queue, (candidate, neighbor))
    return float("inf"), []


class WarehouseRouter:
    def __init__(self, locations, zones):
        (self.graph, self.location_by_id, self.dock_by_warehouse,
         self.blocked_by_warehouse) = build_warehouse_graph(locations, zones)
        self.source_cache = {}

    def _from_source(self, start, warehouse_id):
        key = (warehouse_id, start)
        if key in self.source_cache:
            return self.source_cache[key]
        blocked = self.blocked_by_warehouse[warehouse_id]
        queue = [(0.0, start)]
        distances = {start: 0.0}
        previous = {}
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances.get(node):
                continue
            # A blocked pick face can be a destination, but is never a transit node.
            if node in blocked and node != start:
                continue
            for neighbor, cost in self.graph.get(node, ()):
                candidate = distance + cost
                if candidate < distances.get(neighbor, float("inf")):
                    distances[neighbor] = candidate
                    previous[neighbor] = node
                    heapq.heappush(queue, (candidate, neighbor))
        self.source_cache[key] = (distances, previous)
        return distances, previous

    def route(self, start_location_id, target_location_id):
        target = self.location_by_id[target_location_id]
        warehouse_id = target["warehouse_id"]
        start = start_location_id or self.dock_by_warehouse[warehouse_id]
        if start_location_id:
            start_loc = self.location_by_id[start_location_id]
            if start_loc["warehouse_id"] != warehouse_id:
                start = self.dock_by_warehouse[warehouse_id]
        distances, previous = self._from_source(start, warehouse_id)
        if target_location_id not in distances:
            return float("inf"), []
        path = [target_location_id]
        while path[-1] != start:
            path.append(previous[path[-1]])
        path.reverse()
        return distances[target_location_id], path

    def distance(self, start_location_id, target_location_id):
        return self.route(start_location_id, target_location_id)[0]


def calculate_alternative_distance(locations, pick_events, blocked_aisle_info=None,
                                  zones=None):
    """Compare direct travel with Dijkstra routes under the warehouse closures."""
    if zones is None:
        raise ValueError("zones are required to build the aisle graph")
    router = WarehouseRouter(locations, zones)
    location_by_id = router.location_by_id
    per_wave = defaultdict(list)
    for event in pick_events:
        per_wave[(event["order_id"], event["shift"])].append(event)
    baseline_distance = alternative_distance = 0.0
    for events in per_wave.values():
        previous = None
        for event in events:
            target_id = event["location_id"]
            target = location_by_id[target_id]
            start_xy = (0, 0) if previous is None else (
                location_by_id[previous]["x_coord"], location_by_id[previous]["y_coord"])
            baseline_distance += abs(target["x_coord"] - start_xy[0]) + abs(target["y_coord"] - start_xy[1])
            alternative_distance += router.distance(previous, target_id)
            previous = target_id
    return {
        "baseline_distance_m": round(baseline_distance, 2),
        "alternative_distance_m": round(alternative_distance, 2),
        "blocked_aisle_detour_cost_m": round(alternative_distance - baseline_distance, 2),
    }
