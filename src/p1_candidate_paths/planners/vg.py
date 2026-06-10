"""Ordinary Visibility Graph planner.

VG turns the continuous 2D floor into a sparse graph:
- Vertices are the start point, goal point, and inflated obstacle corners.
- Two vertices are connected if the straight segment between them does not
  cross the interior of any hard obstacle.
- A* then searches this graph with pure Euclidean distance as the path cost.

This planner is still distance-only. It reports turn_count for comparison, but
turn_cost/risk_cost/dynamic_cost are zero in the baseline VG output.
"""

from heapq import heappop, heappush
from math import atan2, hypot

import pandas as pd

from .common import (
    DEFAULT_AMR_SPEED,
    FOOTPRINT_RADIUS,
    GRID_CELL_COLUMNS,
    NODE_OCCUPANCY_COLUMNS,
    PATH_COLUMNS,
    TRAJECTORY_COLUMNS,
    empty_edge_occupancy,
    format_geometry,
    inflate_obstacles,
    path_distance,
    sample_trajectory,
    selected_pairs,
)


def rect_corners(rect):
    """Return the four corners of one inflated rectangular obstacle."""
    return [
        (rect["x_min"], rect["y_min"]),
        (rect["x_min"], rect["y_max"]),
        (rect["x_max"], rect["y_min"]),
        (rect["x_max"], rect["y_max"]),
    ]


def point_strictly_inside_rect(point, rect, eps=1e-9):
    """Treat boundary points as visible, but reject points inside obstacles."""
    x, y = point
    return (
        rect["x_min"] + eps < x < rect["x_max"] - eps
        and rect["y_min"] + eps < y < rect["y_max"] - eps
    )


def segment_rect_overlap_interval(p1, p2, rect, eps=1e-12):
    """Find where a line segment overlaps a rectangle using Liang-Barsky clipping."""
    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1
    t0 = 0.0
    t1 = 1.0

    # Each pair represents one rectangle half-plane. Updating t0/t1 gives the
    # part of the segment that lies inside all four half-planes.
    for p, q in [
        (-dx, x1 - rect["x_min"]),
        (dx, rect["x_max"] - x1),
        (-dy, y1 - rect["y_min"]),
        (dy, rect["y_max"] - y1),
    ]:
        if abs(p) <= eps:
            if q < 0:
                return None
            continue
        ratio = q / p
        if p < 0:
            t0 = max(t0, ratio)
        else:
            t1 = min(t1, ratio)
        if t0 - t1 > eps:
            return None

    return t0, t1


def segment_crosses_rect_interior(p1, p2, rect, eps=1e-9):
    """Return True only when the segment passes through obstacle interior."""
    interval = segment_rect_overlap_interval(p1, p2, rect)
    if interval is None:
        return False

    t0, t1 = interval
    if t1 - t0 <= eps:
        return False

    # Touching an obstacle boundary is allowed. If the overlap has positive
    # length, test its midpoint to decide whether the segment enters interior.
    mid_t = (t0 + t1) / 2.0
    mid = (p1[0] + mid_t * (p2[0] - p1[0]), p1[1] + mid_t * (p2[1] - p1[1]))
    return point_strictly_inside_rect(mid, rect)


def is_visible_segment(p1, p2, hard_obstacles):
    """Check whether two graph vertices can be connected by a straight edge."""
    for rect in hard_obstacles:
        if point_strictly_inside_rect(p1, rect) or point_strictly_inside_rect(p2, rect):
            return False
        if segment_crosses_rect_interior(p1, p2, rect):
            return False
    return True


def visibility_graph_nodes(start_point, goal_point, inflated_obstacles):
    """Collect start/goal and obstacle corners used as visibility vertices."""
    hard_obstacles = [rect for rect in inflated_obstacles if rect["is_hard"]]
    graph_node_points = {
        "start": start_point,
        "goal": goal_point,
    }
    index = 0

    for rect in hard_obstacles:
        # Walls are scene boundaries, so their corners are not useful candidate
        # waypoints for paths inside the warehouse.
        if rect["obstacle_type"] == "wall":
            continue
        for corner in rect_corners(rect):
            # If a corner is hidden inside another inflated obstacle, routing
            # through it would be invalid, so skip it.
            if any(point_strictly_inside_rect(corner, other) for other in hard_obstacles if other is not rect):
                continue
            graph_node_points[f"v{index}"] = corner
            index += 1

    return graph_node_points, hard_obstacles


def astar_visibility_path(start_point, goal_point, inflated_obstacles):
    """Build the visibility graph and run distance-only A* on it."""
    graph_node_points, hard_obstacles = visibility_graph_nodes(start_point, goal_point, inflated_obstacles)
    adjacency = {node_id: [] for node_id in graph_node_points}
    node_items = list(graph_node_points.items())

    # Complete graph candidate pairs are filtered by line-of-sight. The kept
    # undirected edges are weighted by straight-line Euclidean distance.
    for i, (left_id, left_point) in enumerate(node_items):
        for right_id, right_point in node_items[i + 1:]:
            if not is_visible_segment(left_point, right_point, hard_obstacles):
                continue
            distance = hypot(right_point[0] - left_point[0], right_point[1] - left_point[1])
            adjacency[left_id].append((right_id, distance))
            adjacency[right_id].append((left_id, distance))

    open_heap = []
    counter = 0

    # Heap entries are (f_score, g_score, tie_breaker, vertex_id). The
    # heuristic is straight-line distance to the goal, which is admissible.
    heappush(open_heap, (hypot(goal_point[0] - start_point[0], goal_point[1] - start_point[1]), 0.0, counter, "start"))
    came_from = {}
    g_score = {"start": 0.0}
    closed = set()

    while open_heap:
        _, current_cost, _, current_id = heappop(open_heap)
        if current_id in closed:
            continue
        closed.add(current_id)

        if current_id == "goal":
            node_path = reconstruct_node_path(came_from, current_id)
            return [graph_node_points[node_id] for node_id in node_path], current_cost

        for next_id, edge_cost in adjacency[current_id]:
            # Ordinary VG optimizes only distance, so each transition adds just
            # the visible-edge length.
            tentative_cost = current_cost + edge_cost
            if tentative_cost >= g_score.get(next_id, float("inf")):
                continue
            came_from[next_id] = current_id
            g_score[next_id] = tentative_cost
            next_point = graph_node_points[next_id]
            priority = tentative_cost + hypot(goal_point[0] - next_point[0], goal_point[1] - next_point[1])
            counter += 1
            heappush(open_heap, (priority, tentative_cost, counter, next_id))

    return None, None


def reconstruct_node_path(came_from, final_id):
    """Recover a vertex path by following predecessor links from the goal."""
    path = [final_id]
    current = final_id
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path


def polyline_turn_count(points, eps=1e-9):
    """Count heading changes for reporting; VG does not optimize this value."""
    if len(points) < 3:
        return 0

    turns = 0
    for left, middle, right in zip(points[:-2], points[1:-1], points[2:]):
        angle1 = atan2(middle[1] - left[1], middle[0] - left[0])
        angle2 = atan2(right[1] - middle[1], right[0] - middle[0])
        if abs(angle2 - angle1) > eps:
            turns += 1
    return turns


def path_metrics(points):
    """Compute output metrics for one VG polyline."""
    distance = path_distance(points)
    travel_time = distance / DEFAULT_AMR_SPEED
    return {
        "distance": round(distance, 3),
        "travel_time": round(travel_time, 3),
        "turn_count": polyline_turn_count(points),
        "turn_cost": 0.0,
        "risk_cost": 0.0,
        "dynamic_cost": 0.0,
        "total_cost": round(distance, 3),
    }


def generate_outputs(obstacles, key_nodes, from_node=None, to_node=None):
    """Generate all P1 CSV table contents for VG."""
    inflated_obstacles = inflate_obstacles(obstacles)
    node_positions = {
        str(row.node_id): (float(row.x), float(row.y))
        for row in key_nodes.itertuples(index=False)
    }

    path_records = []
    waypoint_records = []
    trajectory_records = []
    node_occupancy_records = []

    for from_node, to_node in selected_pairs(key_nodes, from_node, to_node):
        start_point = node_positions[from_node]
        goal_point = node_positions[to_node]
        points, _ = astar_visibility_path(start_point, goal_point, inflated_obstacles)
        if not points:
            continue

        metrics = path_metrics(points)
        path_uid = f"{from_node}__{to_node}__path_1"
        path_id = "path_1"
        trajectory_rows = sample_trajectory(points, DEFAULT_AMR_SPEED)

        # path_cost.csv stores the compact route summary used by P2.
        path_records.append(
            {
                "path_uid": path_uid,
                "from_node": from_node,
                "to_node": to_node,
                "path_id": path_id,
                "rank_by_total": 1,
                "algorithm": "vg",
                "node_sequence": f"{from_node}->{to_node}",
                "edge_sequence": "",
                "edge_occupancy_offset": "",
                "node_occupancy_offset": f"{from_node}@0;{to_node}@{metrics['travel_time']:g}",
                "path_geometry": format_geometry(points),
                "grid_cell_sequence": "",
                "trajectory_sample_count": len(trajectory_rows),
                "distance": metrics["distance"],
                "travel_time": metrics["travel_time"],
                "turn_count": metrics["turn_count"],
                "turn_cost": metrics["turn_cost"],
                "risk_cost": metrics["risk_cost"],
                "dynamic_cost": metrics["dynamic_cost"],
                "total_cost": metrics["total_cost"],
                "planning_status": "ok",
                "start_cell": "",
                "goal_cell": "",
            }
        )

        # VG is waypoint-based rather than grid-based, so row/col stay empty
        # and x/y records the chosen visibility-graph vertices.
        for waypoint_index, (x, y) in enumerate(points):
            waypoint_records.append(
                {
                    "path_uid": path_uid,
                    "from_node": from_node,
                    "to_node": to_node,
                    "path_id": path_id,
                    "cell_index": waypoint_index,
                    "row": None,
                    "col": None,
                    "x": round(x, 3),
                    "y": round(y, 3),
                    "is_dynamic": 0,
                }
            )

        # path_trajectory_samples.csv provides time-stamped 2D samples for P3/P4.
        for row in trajectory_rows:
            trajectory_records.append(
                {
                    "path_uid": path_uid,
                    "from_node": from_node,
                    "to_node": to_node,
                    "path_id": path_id,
                    **row,
                    "speed": DEFAULT_AMR_SPEED,
                    "footprint_radius": FOOTPRINT_RADIUS,
                }
            )

        node_occupancy_records.extend(
            [
                {
                    "path_uid": path_uid,
                    "from_node": from_node,
                    "to_node": to_node,
                    "path_id": path_id,
                    "node_index": 0,
                    "node_id": from_node,
                    "offset_time": 0.0,
                },
                {
                    "path_uid": path_uid,
                    "from_node": from_node,
                    "to_node": to_node,
                    "path_id": path_id,
                    "node_index": 1,
                    "node_id": to_node,
                    "offset_time": metrics["travel_time"],
                },
            ]
        )

    outputs = (
        pd.DataFrame(path_records, columns=PATH_COLUMNS),
        pd.DataFrame(waypoint_records, columns=GRID_CELL_COLUMNS),
        pd.DataFrame(trajectory_records, columns=TRAJECTORY_COLUMNS),
        empty_edge_occupancy(),
        pd.DataFrame(node_occupancy_records, columns=NODE_OCCUPANCY_COLUMNS),
    )
    return outputs, []
