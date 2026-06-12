"""Augmented Visibility Graph planner.

AVG keeps the same geometric graph as ordinary VG, but augments each search
state with the previous vertex. That lets the transition cost include the turn
angle at the current vertex:

    total_cost = path_distance + TURN_COST_PER_RADIAN * sum(turn_angles)

Travel time is still computed from physical distance only. The turn term is a
planning preference/cost term, not an extra time model yet.
"""

from heapq import heappop, heappush
from math import atan2, hypot, pi

import pandas as pd

from .common import (
    DEFAULT_AMR_SPEED,
    FOOTPRINT_RADIUS,
    GRID_CELL_COLUMNS,
    PATH_COLUMNS,
    TRAJECTORY_COLUMNS,
    format_geometry,
    inflate_obstacles,
    path_distance,
    sample_trajectory,
    selected_pairs,
)
from .vg import is_visible_segment, visibility_graph_nodes


TURN_COST_PER_RADIAN = 0.5
TURN_EPS = 1e-9


def angle_delta(angle_a, angle_b):
    """Return the smallest absolute angle difference in radians."""
    # Normalize into [-pi, pi] so a small wrap-around turn near +/-pi is not
    # mistaken for a nearly full-circle turn.
    return abs((angle_b - angle_a + pi) % (2.0 * pi) - pi)


def turn_angle(previous_point, current_point, next_point):
    """Return the heading change made at current_point."""
    # The turn is measured between the incoming heading and outgoing heading.
    # Straight movement gives 0, while sharper turns produce larger penalties.
    in_angle = atan2(
        current_point[1] - previous_point[1],
        current_point[0] - previous_point[0],
    )
    out_angle = atan2(
        next_point[1] - current_point[1],
        next_point[0] - current_point[0],
    )
    return angle_delta(in_angle, out_angle)


def build_visibility_adjacency(graph_node_points, hard_obstacles):
    """Connect every pair of mutually visible graph vertices."""
    adjacency = {node_id: [] for node_id in graph_node_points}
    node_items = list(graph_node_points.items())

    # AVG uses the same geometric visibility graph as VG. The difference is in
    # the search state and transition cost, not in this line-of-sight graph.
    for i, (left_id, left_point) in enumerate(node_items):
        for right_id, right_point in node_items[i + 1 :]:
            if not is_visible_segment(left_point, right_point, hard_obstacles):
                continue
            distance = hypot(right_point[0] - left_point[0], right_point[1] - left_point[1])
            adjacency[left_id].append((right_id, distance))
            adjacency[right_id].append((left_id, distance))

    return adjacency


def transition_turn_cost(graph_node_points, previous_id, current_id, next_id):
    """Compute the AVG turn penalty for state (previous, current) -> next."""
    # The first move has no previous heading, so no turn can be charged yet.
    if previous_id is None:
        return 0.0

    angle = turn_angle(
        graph_node_points[previous_id],
        graph_node_points[current_id],
        graph_node_points[next_id],
    )
    return TURN_COST_PER_RADIAN * angle


def reconstruct_augmented_node_path(came_from, final_state):
    """Recover a vertex path from augmented states."""
    # Each state stores (previous_vertex, current_vertex). After restoring the
    # state chain, the actual path is the current vertex of each state.
    states = [final_state]
    current = final_state
    while current in came_from:
        current = came_from[current]
        states.append(current)
    states.reverse()

    node_path = [states[0][1]]
    for _, current_id in states[1:]:
        node_path.append(current_id)
    return node_path


def astar_augmented_visibility_path(start_point, goal_point, inflated_obstacles):
    """Run A* on states of the form (previous_vertex, current_vertex)."""
    graph_node_points, hard_obstacles = visibility_graph_nodes(start_point, goal_point, inflated_obstacles)
    adjacency = build_visibility_adjacency(graph_node_points, hard_obstacles)

    # Ordinary VG state would be just "start". AVG keeps previous=None so the
    # second step can know whether a real heading change occurs.
    start_state = (None, "start")
    open_heap = []
    counter = 0
    start_heuristic = hypot(goal_point[0] - start_point[0], goal_point[1] - start_point[1])

    # Heap entries are (f_score, g_score, tie_breaker, augmented_state). The
    # heuristic remains straight-line distance, so it is a lower bound on the
    # remaining distance part of the cost.
    heappush(open_heap, (start_heuristic, 0.0, counter, start_state))

    came_from = {}
    g_score = {start_state: 0.0}
    closed = set()

    while open_heap:
        _, current_cost, _, current_state = heappop(open_heap)
        if current_state in closed:
            continue
        closed.add(current_state)

        previous_id, current_id = current_state
        if current_id == "goal":
            node_path = reconstruct_augmented_node_path(came_from, current_state)
            return [graph_node_points[node_id] for node_id in node_path], current_cost

        for next_id, edge_distance in adjacency[current_id]:
            # Skip immediate U-turns to avoid expanding trivial back-and-forth
            # loops. Other revisits are still controlled by g_score/closed.
            if next_id == previous_id:
                continue

            next_state = (current_id, next_id)
            turn_cost = transition_turn_cost(graph_node_points, previous_id, current_id, next_id)

            # This is the core AVG objective: distance on the visible segment
            # plus a penalty for the heading change at current_id.
            tentative_cost = current_cost + edge_distance + turn_cost
            if tentative_cost >= g_score.get(next_state, float("inf")):
                continue

            came_from[next_state] = current_state
            g_score[next_state] = tentative_cost
            next_point = graph_node_points[next_id]
            heuristic = hypot(goal_point[0] - next_point[0], goal_point[1] - next_point[1])
            counter += 1
            heappush(open_heap, (tentative_cost + heuristic, tentative_cost, counter, next_state))

    return None, None


def polyline_turn_angles(points):
    """List all nonzero heading changes along the waypoint polyline."""
    angles = []
    for left, middle, right in zip(points[:-2], points[1:-1], points[2:]):
        angle = turn_angle(left, middle, right)
        if angle > TURN_EPS:
            angles.append(angle)
    return angles


def path_metrics(points):
    """Compute distance, time, and AVG turn-aware total cost."""
    distance = path_distance(points)
    travel_time = distance / DEFAULT_AMR_SPEED
    turn_angles = polyline_turn_angles(points)

    # Travel time remains physical motion time. The turn term is used by
    # total_cost for route preference and later operations-research comparison.
    turn_cost = TURN_COST_PER_RADIAN * sum(turn_angles)
    total_cost = distance + turn_cost

    return {
        "distance": round(distance, 3),
        "travel_time": round(travel_time, 3),
        "turn_count": len(turn_angles),
        "turn_cost": round(turn_cost, 3),
        "risk_cost": 0.0,
        "dynamic_cost": 0.0,
        "total_cost": round(total_cost, 3),
    }


def generate_outputs(obstacles, key_nodes, from_node=None, to_node=None):
    """Generate P1 output tables for AVG."""
    inflated_obstacles = inflate_obstacles(obstacles)
    node_positions = {
        str(row.node_id): (float(row.x), float(row.y))
        for row in key_nodes.itertuples(index=False)
    }

    path_records = []
    waypoint_records = []
    trajectory_records = []

    for from_node, to_node in selected_pairs(key_nodes, from_node, to_node):
        start_point = node_positions[from_node]
        goal_point = node_positions[to_node]
        points, _ = astar_augmented_visibility_path(start_point, goal_point, inflated_obstacles)
        if not points:
            continue

        metrics = path_metrics(points)
        path_uid = f"{from_node}__{to_node}__path_1"
        path_id = "path_1"
        trajectory_rows = sample_trajectory(points, DEFAULT_AMR_SPEED)

        # path_cost.csv is the downstream-facing summary. For AVG, distance and
        # travel_time remain geometric, while total_cost includes turn_cost.
        path_records.append(
            {
                "path_uid": path_uid,
                "from_node": from_node,
                "to_node": to_node,
                "path_id": path_id,
                "rank_by_total": 1,
                "algorithm": "avg",
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

        # AVG is waypoint-based rather than grid-based, so row/col stay empty
        # while x/y stores the selected visibility-graph vertices.
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

    outputs = (
        pd.DataFrame(path_records, columns=PATH_COLUMNS),
        pd.DataFrame(waypoint_records, columns=GRID_CELL_COLUMNS),
        pd.DataFrame(trajectory_records, columns=TRAJECTORY_COLUMNS),
    )
    messages = [f"AVG turn cost per radian: {TURN_COST_PER_RADIAN:g}"]
    return outputs, messages
