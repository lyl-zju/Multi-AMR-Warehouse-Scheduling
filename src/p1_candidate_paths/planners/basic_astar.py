"""Basic 8-neighbor Grid A* planner.

This planner is the pure baseline used by P1:
- The 2D warehouse is discretized into square grid cells.
- Inflated hard obstacles are treated as blocked cells.
- A* minimizes geometric travel distance only.
- Turn/risk/dynamic costs are emitted as zero so later algorithms can be compared
  against a clean baseline.
"""

from dataclasses import dataclass
from heapq import heappop, heappush
from math import ceil, hypot

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


GRID_RESOLUTION = 0.1

# 8-neighbor grid motion. Orthogonal moves cost 1 cell width, diagonal moves
# cost sqrt(2) cell widths through the Euclidean step distance below.
DIRECTIONS = [
    (-1, 0),
    (1, 0),
    (0, -1),
    (0, 1),
    (-1, -1),
    (-1, 1),
    (1, -1),
    (1, 1),
]


@dataclass(frozen=True)
class GridSpec:
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    resolution: float
    rows: int
    cols: int


def build_grid_spec(obstacles):
    """Build a grid that covers the current floor obstacle extent."""
    min_x = float(obstacles["x"].min())
    min_y = float(obstacles["y"].min())
    max_x = float((obstacles["x"] + obstacles["width"]).max())
    max_y = float((obstacles["y"] + obstacles["height"]).max())
    cols = int(ceil((max_x - min_x) / GRID_RESOLUTION))
    rows = int(ceil((max_y - min_y) / GRID_RESOLUTION))
    return GridSpec(min_x, min_y, max_x, max_y, GRID_RESOLUTION, rows, cols)


def world_to_grid(grid, x, y):
    """Convert a continuous world coordinate into the containing grid cell."""
    col = int((float(x) - grid.min_x) / grid.resolution)
    row = int((float(y) - grid.min_y) / grid.resolution)
    row = min(max(row, 0), grid.rows - 1)
    col = min(max(col, 0), grid.cols - 1)
    return row, col


def grid_to_world(grid, cell):
    """Convert a grid cell into the world coordinate of its cell center."""
    row, col = cell
    x = grid.min_x + (col + 0.5) * grid.resolution
    y = grid.min_y + (row + 0.5) * grid.resolution
    return x, y


def point_in_rect(x, y, rect):
    """Check whether a point lies inside an inflated rectangular obstacle."""
    return rect["x_min"] <= x <= rect["x_max"] and rect["y_min"] <= y <= rect["y_max"]


def build_occupancy_sets(grid, inflated_obstacles):
    """Mark blocked and soft-cost cells by testing each cell center."""
    blocked_cells = set()
    dynamic_cells = set()

    for row in range(grid.rows):
        for col in range(grid.cols):
            x, y = grid_to_world(grid, (row, col))
            for rect in inflated_obstacles:
                if not point_in_rect(x, y, rect):
                    continue
                if rect["is_hard"]:
                    blocked_cells.add((row, col))
                    break
                if rect["is_soft"]:
                    dynamic_cells.add((row, col))

    return blocked_cells, dynamic_cells


def nearest_free_cell(grid, blocked_cells, start_cell):
    """Snap a blocked endpoint to the nearest free cell within a local radius."""
    if start_cell not in blocked_cells:
        return start_cell

    best_cell = None
    best_distance = float("inf")
    max_radius = int(ceil(2.0 / grid.resolution))
    row0, col0 = start_cell

    for radius in range(1, max_radius + 1):
        for row in range(row0 - radius, row0 + radius + 1):
            for col in range(col0 - radius, col0 + radius + 1):
                if row < 0 or row >= grid.rows or col < 0 or col >= grid.cols:
                    continue
                if (row, col) in blocked_cells:
                    continue
                distance = hypot(row - row0, col - col0)
                if distance < best_distance:
                    best_cell = (row, col)
                    best_distance = distance
        if best_cell is not None:
            return best_cell

    return None


def is_valid_step(grid, blocked_cells, current_cell, next_cell):
    """Validate one grid move and prevent diagonal corner cutting."""
    row, col = next_cell
    if row < 0 or row >= grid.rows or col < 0 or col >= grid.cols:
        return False
    if next_cell in blocked_cells:
        return False

    cur_row, cur_col = current_cell
    d_row = row - cur_row
    d_col = col - cur_col
    if d_row != 0 and d_col != 0:
        if (cur_row + d_row, cur_col) in blocked_cells:
            return False
        if (cur_row, cur_col + d_col) in blocked_cells:
            return False

    return True


def heuristic(grid, cell, goal_cell):
    """Euclidean distance in meters; admissible for 8-neighbor movement."""
    return hypot(cell[0] - goal_cell[0], cell[1] - goal_cell[1]) * grid.resolution


def astar_grid_path(grid, blocked_cells, start_cell, goal_cell):
    """Run A* on the grid and return the raw cell path plus distance cost."""
    open_heap = []
    counter = 0

    # Heap entries are (f_score, g_score, tie_breaker, cell). The tie breaker
    # keeps heap ordering stable when two cells share the same score.
    heappush(open_heap, (heuristic(grid, start_cell, goal_cell), 0.0, counter, start_cell))

    came_from = {}
    g_score = {start_cell: 0.0}
    closed = set()

    while open_heap:
        _, current_cost, _, current_cell = heappop(open_heap)
        if current_cell in closed:
            continue
        closed.add(current_cell)

        if current_cell == goal_cell:
            return reconstruct_cells(came_from, current_cell), current_cost

        for d_row, d_col in DIRECTIONS:
            next_cell = (current_cell[0] + d_row, current_cell[1] + d_col)
            if not is_valid_step(grid, blocked_cells, current_cell, next_cell):
                continue

            # In this baseline, the transition cost is only the Euclidean
            # distance moved through the grid. No turn penalty is added here.
            step_distance = hypot(d_row, d_col) * grid.resolution
            tentative_cost = current_cost + step_distance

            if tentative_cost >= g_score.get(next_cell, float("inf")):
                continue

            came_from[next_cell] = current_cell
            g_score[next_cell] = tentative_cost
            counter += 1

            # f = g + h, where h is a straight-line lower bound to the goal.
            priority = tentative_cost + heuristic(grid, next_cell, goal_cell)
            heappush(open_heap, (priority, tentative_cost, counter, next_cell))

    return None, None


def reconstruct_cells(came_from, final_cell):
    """Recover the path by walking predecessor links backward from the goal."""
    cells = [final_cell]
    current = final_cell
    while current in came_from:
        current = came_from[current]
        cells.append(current)
    cells.reverse()
    return cells


def cell_direction(previous_cell, next_cell):
    return next_cell[0] - previous_cell[0], next_cell[1] - previous_cell[1]


def count_turns(cells):
    """Count direction changes for reporting, not for optimization."""
    if len(cells) < 3:
        return 0

    turns = 0
    previous_direction = cell_direction(cells[0], cells[1])
    for index in range(1, len(cells) - 1):
        direction = cell_direction(cells[index], cells[index + 1])
        if direction != previous_direction:
            turns += 1
        previous_direction = direction
    return turns


def simplify_collinear_cells(cells):
    """Keep only breakpoints so path_geometry stays readable for plotting."""
    if len(cells) <= 2:
        return cells

    simplified = [cells[0]]
    previous_direction = cell_direction(cells[0], cells[1])
    for index in range(1, len(cells) - 1):
        direction = cell_direction(cells[index], cells[index + 1])
        if direction != previous_direction:
            simplified.append(cells[index])
        previous_direction = direction
    simplified.append(cells[-1])
    return simplified


def cells_to_points(grid, cells):
    return [grid_to_world(grid, cell) for cell in cells]


def format_grid_cells(cells):
    return ";".join(f"{row}:{col}" for row, col in cells)


def grid_path_metrics(grid, cells):
    """Compute output metrics for one Grid A* path."""
    raw_points = cells_to_points(grid, cells)
    simplified_cells = simplify_collinear_cells(cells)
    simplified_points = cells_to_points(grid, simplified_cells)

    distance = path_distance(raw_points)
    travel_time = distance / DEFAULT_AMR_SPEED

    return {
        "raw_points": raw_points,
        "simplified_cells": simplified_cells,
        "simplified_points": simplified_points,
        "distance": round(distance, 3),
        "travel_time": round(travel_time, 3),
        "turn_count": count_turns(cells),
        "turn_cost": 0.0,
        "risk_cost": 0.0,
        "dynamic_cost": 0.0,
        "total_cost": round(distance, 3),
    }


def generate_outputs(obstacles, key_nodes, from_node=None, to_node=None):
    """Generate all P1 CSV table contents for the selected endpoint pairs."""
    grid = build_grid_spec(obstacles)
    inflated_obstacles = inflate_obstacles(obstacles)
    blocked_cells, dynamic_cells = build_occupancy_sets(grid, inflated_obstacles)
    node_positions = {
        str(row.node_id): (float(row.x), float(row.y))
        for row in key_nodes.itertuples(index=False)
    }

    path_records = []
    grid_cell_records = []
    trajectory_records = []

    for from_node, to_node in selected_pairs(key_nodes, from_node, to_node):
        start_x, start_y = node_positions[from_node]
        goal_x, goal_y = node_positions[to_node]
        original_start_cell = world_to_grid(grid, start_x, start_y)
        original_goal_cell = world_to_grid(grid, goal_x, goal_y)
        start_cell = nearest_free_cell(grid, blocked_cells, original_start_cell)
        goal_cell = nearest_free_cell(grid, blocked_cells, original_goal_cell)

        if start_cell is None or goal_cell is None:
            continue

        cells, _ = astar_grid_path(grid, blocked_cells, start_cell, goal_cell)
        if not cells:
            continue

        metrics = grid_path_metrics(grid, cells)
        path_uid = f"{from_node}__{to_node}__path_1"
        path_id = "path_1"
        trajectory_rows = sample_trajectory(metrics["simplified_points"], DEFAULT_AMR_SPEED)

        # path_cost.csv keeps the compact, planner-level summary used by P2.
        path_records.append(
            {
                "path_uid": path_uid,
                "from_node": from_node,
                "to_node": to_node,
                "path_id": path_id,
                "rank_by_total": 1,
                "algorithm": "basic_astar",
                "path_geometry": format_geometry(metrics["simplified_points"]),
                "grid_cell_sequence": format_grid_cells(metrics["simplified_cells"]),
                "trajectory_sample_count": len(trajectory_rows),
                "distance": metrics["distance"],
                "travel_time": metrics["travel_time"],
                "turn_count": metrics["turn_count"],
                "turn_cost": metrics["turn_cost"],
                "risk_cost": metrics["risk_cost"],
                "dynamic_cost": metrics["dynamic_cost"],
                "total_cost": metrics["total_cost"],
                "planning_status": "ok",
                "start_cell": f"{start_cell[0]}:{start_cell[1]}",
                "goal_cell": f"{goal_cell[0]}:{goal_cell[1]}",
            }
        )

        # path_grid_cells.csv stores the full raw grid path for inspection.
        for cell_index, cell in enumerate(cells):
            x, y = grid_to_world(grid, cell)
            grid_cell_records.append(
                {
                    "path_uid": path_uid,
                    "from_node": from_node,
                    "to_node": to_node,
                    "path_id": path_id,
                    "cell_index": cell_index,
                    "row": cell[0],
                    "col": cell[1],
                    "x": round(x, 3),
                    "y": round(y, 3),
                    "is_dynamic": int(cell in dynamic_cells),
                }
            )

        # path_trajectory_samples.csv is a time-sampled 2D trajectory for P3/P4.
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
        pd.DataFrame(grid_cell_records, columns=GRID_CELL_COLUMNS),
        pd.DataFrame(trajectory_records, columns=TRAJECTORY_COLUMNS),
    )
    messages = [f"Grid size: {grid.rows} x {grid.cols}"]
    return outputs, messages
