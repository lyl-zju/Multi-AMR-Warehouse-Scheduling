from collections import defaultdict
from math import atan2, hypot
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"

P1_ALGORITHMS = ("basic_astar", "vg", "avg", "davg")
IMPLEMENTED_ALGORITHMS = {"basic_astar", "vg", "avg", "davg"}
DEFAULT_P1_ALGORITHM = "basic_astar"

OBSTACLE_INFLATION = 0.30
DEFAULT_AMR_SPEED = 1.0
TRAJECTORY_SAMPLE_DT = 0.5
FOOTPRINT_RADIUS = 0.25

HARD_OBSTACLE_TYPES = {"rack", "wall", "dynamic_block"}
SOFT_OBSTACLE_TYPES = set()

PATH_COLUMNS = [
    "path_uid",
    "from_node",
    "to_node",
    "path_id",
    "rank_by_total",
    "algorithm",
    "path_geometry",
    "grid_cell_sequence",
    "trajectory_sample_count",
    "distance",
    "travel_time",
    "turn_count",
    "turn_cost",
    "risk_cost",
    "dynamic_cost",
    "total_cost",
    "planning_status",
    "start_cell",
    "goal_cell",
]
GRID_CELL_COLUMNS = [
    "path_uid",
    "from_node",
    "to_node",
    "path_id",
    "cell_index",
    "row",
    "col",
    "x",
    "y",
    "is_dynamic",
]
TRAJECTORY_COLUMNS = [
    "path_uid",
    "from_node",
    "to_node",
    "path_id",
    "sample_index",
    "offset_time",
    "x",
    "y",
    "theta",
    "speed",
    "footprint_radius",
]


def p1_processed_dir(algorithm):
    return PROJECT_ROOT / "data" / "processed" / "p1" / algorithm


def load_raw_data():
    nodes = pd.read_csv(RAW_DATA_DIR / "nodes.csv")
    amrs = pd.read_csv(RAW_DATA_DIR / "amrs.csv")
    tasks = pd.read_csv(RAW_DATA_DIR / "tasks.csv")
    zones = pd.read_csv(RAW_DATA_DIR / "floor_zones.csv", skipinitialspace=True)
    obstacles = pd.read_csv(RAW_DATA_DIR / "floor_obstacles.csv", skipinitialspace=True)
    return nodes, amrs, tasks, zones, obstacles


def build_key_nodes(nodes, amrs, tasks):
    sources = defaultdict(set)

    for node_id in amrs["init_node"].dropna():
        sources[node_id].add("amr_init")
    for node_id in tasks["pickup"].dropna():
        sources[node_id].add("pickup")
    for node_id in tasks["delivery"].dropna():
        sources[node_id].add("delivery")
    for node_id in nodes.loc[nodes["node_type"] == "charger", "node_id"]:
        sources[node_id].add("charger")

    source_order = ["amr_init", "pickup", "delivery", "charger"]
    records = []
    for node in nodes.itertuples(index=False):
        if node.node_id not in sources:
            continue
        ordered_sources = [name for name in source_order if name in sources[node.node_id]]
        records.append(
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "x": float(node.x),
                "y": float(node.y),
                "source": ";".join(ordered_sources),
            }
        )

    return pd.DataFrame(records)


def add_manual_endpoint_nodes(key_nodes, nodes, from_node=None, to_node=None):
    if not from_node and not to_node:
        return key_nodes

    selected = {node_id for node_id in [from_node, to_node] if node_id}
    existing = set(key_nodes["node_id"].astype(str))
    records = []

    for node_id in selected - existing:
        rows = nodes[nodes["node_id"].astype(str) == str(node_id)]
        if rows.empty:
            available = ", ".join(nodes["node_id"].astype(str).tolist())
            raise ValueError(f"Unknown endpoint node '{node_id}'. Available nodes: {available}")
        node = rows.iloc[0]
        records.append(
            {
                "node_id": node["node_id"],
                "node_type": node["node_type"],
                "x": float(node["x"]),
                "y": float(node["y"]),
                "source": "manual_endpoint",
            }
        )

    if not records:
        return key_nodes
    return pd.concat([key_nodes, pd.DataFrame(records)], ignore_index=True)


def selected_pairs(key_nodes, from_node=None, to_node=None):
    key_node_ids = key_nodes["node_id"].astype(str).tolist()
    if from_node is None and to_node is None:
        return [
            (left, right)
            for left in key_node_ids
            for right in key_node_ids
            if left != right
        ]

    if not from_node or not to_node:
        raise ValueError("Specify both --from-node and --to-node, or neither.")
    if from_node == to_node:
        raise ValueError("--from-node and --to-node must be different.")

    missing = [node_id for node_id in [from_node, to_node] if node_id not in key_node_ids]
    if missing:
        available = ", ".join(key_node_ids)
        raise ValueError(f"Endpoint(s) not in planning node set: {missing}. Available nodes: {available}")

    return [(from_node, to_node)]


def inflate_obstacles(obstacles):
    inflated = []
    for obstacle in obstacles.itertuples(index=False):
        obstacle_type = str(obstacle.obstacle_type)
        inflate = OBSTACLE_INFLATION
        inflated.append(
            {
                "obstacle_id": obstacle.obstacle_id,
                "obstacle_type": obstacle_type,
                "x_min": float(obstacle.x) - inflate,
                "x_max": float(obstacle.x) + float(obstacle.width) + inflate,
                "y_min": float(obstacle.y) - inflate,
                "y_max": float(obstacle.y) + float(obstacle.height) + inflate,
                "is_hard": obstacle_type in HARD_OBSTACLE_TYPES,
                "is_soft": obstacle_type in SOFT_OBSTACLE_TYPES,
            }
        )
    return inflated


def path_distance(points):
    return sum(
        hypot(x2 - x1, y2 - y1)
        for (x1, y1), (x2, y2) in zip(points[:-1], points[1:])
    )


def format_geometry(points):
    return ";".join(f"{x:.2f},{y:.2f}" for x, y in points)


def sample_trajectory(points, speed=DEFAULT_AMR_SPEED):
    if not points:
        return []
    if len(points) == 1:
        return [{"sample_index": 0, "offset_time": 0.0, "x": points[0][0], "y": points[0][1], "theta": 0.0}]

    total_distance = path_distance(points)
    total_time = total_distance / speed if speed > 0 else 0.0
    samples = []
    sample_index = 0
    next_time = 0.0

    for segment_index, ((x1, y1), (x2, y2)) in enumerate(zip(points[:-1], points[1:])):
        segment_length = hypot(x2 - x1, y2 - y1)
        if segment_length <= 1e-9:
            continue

        segment_theta = atan2(y2 - y1, x2 - x1)
        segment_start_distance = path_distance(points[: segment_index + 1])
        segment_end_distance = segment_start_distance + segment_length

        while next_time * speed <= segment_end_distance + 1e-9:
            current_distance = next_time * speed
            if current_distance + 1e-9 < segment_start_distance:
                break
            ratio = min(max((current_distance - segment_start_distance) / segment_length, 0.0), 1.0)
            samples.append(
                {
                    "sample_index": sample_index,
                    "offset_time": round(next_time, 3),
                    "x": round(x1 + ratio * (x2 - x1), 3),
                    "y": round(y1 + ratio * (y2 - y1), 3),
                    "theta": round(segment_theta, 6),
                }
            )
            sample_index += 1
            next_time += TRAJECTORY_SAMPLE_DT
            if next_time > total_time + TRAJECTORY_SAMPLE_DT:
                break

    end_x, end_y = points[-1]
    if not samples or hypot(samples[-1]["x"] - end_x, samples[-1]["y"] - end_y) > 1e-6:
        last_theta = samples[-1]["theta"] if samples else 0.0
        samples.append(
            {
                "sample_index": sample_index,
                "offset_time": round(total_time, 3),
                "x": round(end_x, 3),
                "y": round(end_y, 3),
                "theta": last_theta,
            }
        )

    return samples


def build_cost_matrix(path_cost, key_nodes, value_column):
    node_ids = key_nodes["node_id"].tolist()
    best_paths = (
        path_cost.sort_values(["from_node", "to_node", value_column, "total_cost", "travel_time"])
        .drop_duplicates(["from_node", "to_node"], keep="first")
    )
    matrix = best_paths.pivot(index="from_node", columns="to_node", values=value_column)
    matrix = matrix.reindex(index=node_ids, columns=node_ids)

    for node_id in node_ids:
        matrix.loc[node_id, node_id] = 0.0

    return matrix


def save_outputs(processed_data_dir, key_nodes, outputs):
    (
        path_cost,
        path_grid_cells,
        path_trajectory_samples,
    ) = outputs

    processed_data_dir.mkdir(parents=True, exist_ok=True)
    key_nodes.to_csv(processed_data_dir / "key_nodes.csv", index=False)
    path_cost.to_csv(processed_data_dir / "path_cost.csv", index=False)
    path_grid_cells.to_csv(processed_data_dir / "path_grid_cells.csv", index=False)
    path_trajectory_samples.to_csv(processed_data_dir / "path_trajectory_samples.csv", index=False)

    matrix_time = build_cost_matrix(path_cost, key_nodes, "travel_time")
    matrix_total = build_cost_matrix(path_cost, key_nodes, "total_cost")
    matrix_time.to_csv(processed_data_dir / "path_cost_matrix_time.csv")
    matrix_total.to_csv(processed_data_dir / "path_cost_matrix_total.csv")

    return path_cost
