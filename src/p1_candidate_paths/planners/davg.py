"""Dynamic Augmented Visibility Graph planner.

DAVG extends AVG in two ways:
- Active-region graph building: use obstacle corners near the start-goal
  corridor, and always include dynamic obstacle corners. Visibility checks still
  test against all hard obstacles, so skipped far-away corners cannot create an
  invalid edge through an obstacle.
- Dynamic cost: visible segments that pass close to a dynamic_block receive an
  extra penalty. This makes DAVG prefer paths with more clearance from temporary
  blocked/risky areas while preserving the same P1 output format as other
  planners.

The search state is still augmented as (previous_vertex, current_vertex), so
the transition cost can include both turn cost and dynamic-area cost.
"""

from heapq import heappop, heappush
from math import hypot

import pandas as pd

from .avg import TURN_COST_PER_RADIAN, TURN_EPS, reconstruct_augmented_node_path, turn_angle
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
from .vg import (
    is_visible_segment,
    point_strictly_inside_rect,
    rect_corners,
    segment_rect_overlap_interval,
)


ACTIVE_CORRIDOR_MARGIN = 1.5
DYNAMIC_COST_BUFFER = 1.0
DYNAMIC_COST_WEIGHT = 2.0


def corridor_bounds(start_point, goal_point, margin):
    """Build a rectangular active region around the start-goal corridor."""
    return {
        "x_min": min(start_point[0], goal_point[0]) - margin,
        "x_max": max(start_point[0], goal_point[0]) + margin,
        "y_min": min(start_point[1], goal_point[1]) - margin,
        "y_max": max(start_point[1], goal_point[1]) + margin,
    }


def rect_intersects_bounds(rect, bounds):
    """Check whether an obstacle overlaps the active corridor bounds."""
    return not (
        rect["x_max"] < bounds["x_min"]
        or rect["x_min"] > bounds["x_max"]
        or rect["y_max"] < bounds["y_min"]
        or rect["y_min"] > bounds["y_max"]
    )


def is_dynamic_rect(rect):
    """Identify obstacles treated as dynamic/risky regions by DAVG."""
    return rect["obstacle_type"] == "dynamic_block" or rect["is_soft"]


def active_visibility_graph_nodes(start_point, goal_point, inflated_obstacles, use_active_region=True):
    """Collect vertices for DAVG while keeping visibility checks globally safe."""
    hard_obstacles = [rect for rect in inflated_obstacles if rect["is_hard"]]
    dynamic_obstacles = [rect for rect in inflated_obstacles if is_dynamic_rect(rect)]
    bounds = corridor_bounds(start_point, goal_point, ACTIVE_CORRIDOR_MARGIN)
    graph_node_points = {
        "start": start_point,
        "goal": goal_point,
    }
    included_obstacle_count = 0
    index = 0

    for rect in hard_obstacles:
        if rect["obstacle_type"] == "wall":
            continue

        # DAVG keeps the graph smaller by adding only locally relevant corners.
        # Dynamic obstacles are always relevant because they define risk/blocked
        # areas even when they are slightly outside the direct start-goal box.
        if use_active_region and not is_dynamic_rect(rect) and not rect_intersects_bounds(rect, bounds):
            continue

        included_obstacle_count += 1
        for corner in rect_corners(rect):
            if any(point_strictly_inside_rect(corner, other) for other in hard_obstacles if other is not rect):
                continue
            graph_node_points[f"v{index}"] = corner
            index += 1

    return graph_node_points, hard_obstacles, dynamic_obstacles, included_obstacle_count


def point_to_rect_distance(point, rect):
    """Distance from a point to a rectangle; zero if the point is inside it."""
    x, y = point
    dx = max(rect["x_min"] - x, 0.0, x - rect["x_max"])
    dy = max(rect["y_min"] - y, 0.0, y - rect["y_max"])
    return hypot(dx, dy)


def point_to_segment_distance(point, p1, p2):
    """Distance from a point to a segment."""
    x, y = point
    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return hypot(x - x1, y - y1)

    ratio = ((x - x1) * dx + (y - y1) * dy) / length_sq
    ratio = min(max(ratio, 0.0), 1.0)
    nearest = (x1 + ratio * dx, y1 + ratio * dy)
    return hypot(x - nearest[0], y - nearest[1])


def segment_to_rect_distance(p1, p2, rect):
    """Distance from a segment to a rectangle; zero if they touch/intersect."""
    if segment_rect_overlap_interval(p1, p2, rect) is not None:
        return 0.0

    rect_points = rect_corners(rect)
    endpoint_distance = min(point_to_rect_distance(p1, rect), point_to_rect_distance(p2, rect))
    corner_distance = min(point_to_segment_distance(corner, p1, p2) for corner in rect_points)
    return min(endpoint_distance, corner_distance)


def dynamic_edge_cost(p1, p2, dynamic_obstacles):
    """Penalty for traveling close to dynamic/risky obstacle regions."""
    edge_length = hypot(p2[0] - p1[0], p2[1] - p1[1])
    total = 0.0

    for rect in dynamic_obstacles:
        distance = segment_to_rect_distance(p1, p2, rect)
        if distance >= DYNAMIC_COST_BUFFER:
            continue

        # The penalty increases as the segment approaches the dynamic area and
        # scales with edge length so long near-risk segments are discouraged.
        exposure = (DYNAMIC_COST_BUFFER - distance) / DYNAMIC_COST_BUFFER
        total += DYNAMIC_COST_WEIGHT * exposure * edge_length

    return total


def build_dynamic_visibility_adjacency(graph_node_points, hard_obstacles, dynamic_obstacles):
    """Build visible edges and store distance plus dynamic exposure cost."""
    adjacency = {node_id: [] for node_id in graph_node_points}
    node_items = list(graph_node_points.items())

    for i, (left_id, left_point) in enumerate(node_items):
        for right_id, right_point in node_items[i + 1 :]:
            if not is_visible_segment(left_point, right_point, hard_obstacles):
                continue

            distance = hypot(right_point[0] - left_point[0], right_point[1] - left_point[1])
            dynamic_cost = dynamic_edge_cost(left_point, right_point, dynamic_obstacles)
            adjacency[left_id].append((right_id, distance, dynamic_cost))
            adjacency[right_id].append((left_id, distance, dynamic_cost))

    return adjacency


def transition_turn_cost(graph_node_points, previous_id, current_id, next_id):
    """Compute turn penalty for the augmented DAVG transition."""
    if previous_id is None:
        return 0.0

    angle = turn_angle(
        graph_node_points[previous_id],
        graph_node_points[current_id],
        graph_node_points[next_id],
    )
    return TURN_COST_PER_RADIAN * angle


def astar_dynamic_augmented_visibility_path(
    start_point,
    goal_point,
    inflated_obstacles,
    use_active_region=True,
):
    """Run DAVG A* with state=(previous_vertex, current_vertex)."""
    (
        graph_node_points,
        hard_obstacles,
        dynamic_obstacles,
        included_obstacle_count,
    ) = active_visibility_graph_nodes(
        start_point,
        goal_point,
        inflated_obstacles,
        use_active_region=use_active_region,
    )
    adjacency = build_dynamic_visibility_adjacency(graph_node_points, hard_obstacles, dynamic_obstacles)

    start_state = (None, "start")
    open_heap = []
    counter = 0
    start_heuristic = hypot(goal_point[0] - start_point[0], goal_point[1] - start_point[1])
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
            return (
                [graph_node_points[node_id] for node_id in node_path],
                current_cost,
                included_obstacle_count,
            )

        for next_id, edge_distance, edge_dynamic_cost in adjacency[current_id]:
            if next_id == previous_id:
                continue

            next_state = (current_id, next_id)
            turn_cost = transition_turn_cost(graph_node_points, previous_id, current_id, next_id)

            # DAVG transition objective:
            # visible-edge distance + heading-change cost + dynamic exposure.
            tentative_cost = current_cost + edge_distance + turn_cost + edge_dynamic_cost
            if tentative_cost >= g_score.get(next_state, float("inf")):
                continue

            came_from[next_state] = current_state
            g_score[next_state] = tentative_cost
            next_point = graph_node_points[next_id]
            heuristic = hypot(goal_point[0] - next_point[0], goal_point[1] - next_point[1])
            counter += 1
            heappush(open_heap, (tentative_cost + heuristic, tentative_cost, counter, next_state))

    return None, None, included_obstacle_count


def polyline_turn_angles(points):
    """List all nonzero heading changes along the DAVG waypoint polyline."""
    angles = []
    for left, middle, right in zip(points[:-2], points[1:-1], points[2:]):
        angle = turn_angle(left, middle, right)
        if angle > TURN_EPS:
            angles.append(angle)
    return angles


def path_dynamic_cost(points, dynamic_obstacles):
    """Recompute dynamic exposure cost for the final polyline."""
    return sum(
        dynamic_edge_cost(left, right, dynamic_obstacles)
        for left, right in zip(points[:-1], points[1:])
    )


def path_metrics(points, dynamic_obstacles):
    """Compute distance, time, turn cost, dynamic cost, and total cost."""
    distance = path_distance(points)
    travel_time = distance / DEFAULT_AMR_SPEED
    turn_angles = polyline_turn_angles(points)
    turn_cost = TURN_COST_PER_RADIAN * sum(turn_angles)
    dynamic_cost = path_dynamic_cost(points, dynamic_obstacles)
    total_cost = distance + turn_cost + dynamic_cost

    return {
        "distance": round(distance, 3),
        "travel_time": round(travel_time, 3),
        "turn_count": len(turn_angles),
        "turn_cost": round(turn_cost, 3),
        "risk_cost": 0.0,
        "dynamic_cost": round(dynamic_cost, 3),
        "total_cost": round(total_cost, 3),
    }


def waypoint_is_dynamic(point, dynamic_obstacles):
    """Flag waypoints that lie inside or near a dynamic obstacle buffer."""
    return any(
        point_to_rect_distance(point, rect) <= DYNAMIC_COST_BUFFER
        for rect in dynamic_obstacles
    )


def generate_outputs(obstacles, key_nodes, from_node=None, to_node=None):
    """Generate P1 output tables for DAVG."""
    inflated_obstacles = inflate_obstacles(obstacles)
    dynamic_obstacles = [rect for rect in inflated_obstacles if is_dynamic_rect(rect)]
    node_positions = {
        str(row.node_id): (float(row.x), float(row.y))
        for row in key_nodes.itertuples(index=False)
    }

    path_records = []
    waypoint_records = []
    trajectory_records = []
    fallback_count = 0
    active_obstacle_counts = []

    for from_node, to_node in selected_pairs(key_nodes, from_node, to_node):
        start_point = node_positions[from_node]
        goal_point = node_positions[to_node]
        points, _, included_count = astar_dynamic_augmented_visibility_path(
            start_point,
            goal_point,
            inflated_obstacles,
            use_active_region=True,
        )

        # Active-region DAVG is faster/smaller, but a path may need corners just
        # outside the selected corridor. In that case, fall back to the full
        # obstacle-corner graph while keeping the same DAVG costs.
        if not points:
            fallback_count += 1
            points, _, included_count = astar_dynamic_augmented_visibility_path(
                start_point,
                goal_point,
                inflated_obstacles,
                use_active_region=False,
            )
        if not points:
            continue

        active_obstacle_counts.append(included_count)
        metrics = path_metrics(points, dynamic_obstacles)
        path_uid = f"{from_node}__{to_node}__path_1"
        path_id = "path_1"
        trajectory_rows = sample_trajectory(points, DEFAULT_AMR_SPEED)

        path_records.append(
            {
                "path_uid": path_uid,
                "from_node": from_node,
                "to_node": to_node,
                "path_id": path_id,
                "rank_by_total": 1,
                "algorithm": "davg",
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

        for waypoint_index, (x, y) in enumerate(points):
            point = (x, y)
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
                    "is_dynamic": int(waypoint_is_dynamic(point, dynamic_obstacles)),
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
    avg_active_obstacles = (
        sum(active_obstacle_counts) / len(active_obstacle_counts)
        if active_obstacle_counts
        else 0.0
    )
    messages = [
        f"DAVG turn cost per radian: {TURN_COST_PER_RADIAN:g}",
        f"DAVG dynamic cost buffer: {DYNAMIC_COST_BUFFER:g}",
        f"DAVG dynamic cost weight: {DYNAMIC_COST_WEIGHT:g}",
        f"DAVG dynamic obstacles: {len(dynamic_obstacles)}",
        f"DAVG average active obstacles: {avg_active_obstacles:.2f}",
        f"DAVG full-graph fallbacks: {fallback_count}",
    ]
    return outputs, messages
