import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
P1_ALGORITHMS = ("basic_astar", "vg", "avg", "davg")
DEFAULT_P1_ALGORITHM = "basic_astar"


@dataclass
class P1Outputs:
    algorithm: str
    key_nodes: pd.DataFrame
    path_cost: pd.DataFrame
    path_grid_cells: pd.DataFrame
    path_trajectory_samples: pd.DataFrame
    matrix_time: pd.DataFrame
    matrix_total: pd.DataFrame


def p1_processed_dir(algorithm):
    return PROJECT_ROOT / "data" / "processed" / "p1" / algorithm


def parse_args():
    parser = argparse.ArgumentParser(description="Inspect P1 outputs for a selected planner.")
    parser.add_argument(
        "--algorithm",
        choices=P1_ALGORITHMS,
        default=DEFAULT_P1_ALGORITHM,
        help="P1 path planning method to read.",
    )
    parser.add_argument("--from-node", default="IN1", help="Smoke-check source node.")
    parser.add_argument("--to-node", default="P5", help="Smoke-check target node.")
    return parser.parse_args()


def load_p1_outputs(algorithm=DEFAULT_P1_ALGORITHM, processed_dir=None):
    """Load all P1 CSV outputs needed by downstream modules."""
    if processed_dir is None:
        processed_dir = p1_processed_dir(algorithm)
    processed_dir = Path(processed_dir)
    required_files = {
        "key_nodes": processed_dir / "key_nodes.csv",
        "path_cost": processed_dir / "path_cost.csv",
        "path_grid_cells": processed_dir / "path_grid_cells.csv",
        "path_trajectory_samples": processed_dir / "path_trajectory_samples.csv",
        "matrix_time": processed_dir / "path_cost_matrix_time.csv",
        "matrix_total": processed_dir / "path_cost_matrix_total.csv",
    }
    missing = [str(path) for path in required_files.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            f"P1 outputs for algorithm '{algorithm}' are missing. "
            f"Run src/p1_candidate_paths/generate_candidate_paths.py --algorithm {algorithm} first. "
            f"Missing: {missing}"
        )

    return P1Outputs(
        algorithm=algorithm,
        key_nodes=pd.read_csv(required_files["key_nodes"]),
        path_cost=pd.read_csv(required_files["path_cost"]),
        path_grid_cells=pd.read_csv(required_files["path_grid_cells"]),
        path_trajectory_samples=pd.read_csv(required_files["path_trajectory_samples"]),
        matrix_time=pd.read_csv(required_files["matrix_time"], index_col=0),
        matrix_total=pd.read_csv(required_files["matrix_total"], index_col=0),
    )


def zero_path(from_node, to_node):
    return {
        "path_uid": "same_node",
        "from_node": from_node,
        "to_node": to_node,
        "path_id": "same_node",
        "rank_by_total": 0,
        "algorithm": "same_node",
        "path_geometry": "",
        "grid_cell_sequence": "",
        "distance": 0.0,
        "travel_time": 0.0,
        "turn_count": 0,
        "turn_cost": 0.0,
        "risk_cost": 0.0,
        "dynamic_cost": 0.0,
        "total_cost": 0.0,
        "planning_status": "same_node",
    }


def build_best_path_lookup(path_cost):
    """Return the best path row for each ordered endpoint pair."""
    best_paths = (
        path_cost.sort_values(["from_node", "to_node", "total_cost", "travel_time", "rank_by_total"])
        .drop_duplicates(["from_node", "to_node"], keep="first")
    )
    return {
        (row.from_node, row.to_node): row._asdict()
        for row in best_paths.itertuples(index=False)
    }


def get_best_path(outputs, from_node, to_node):
    """Get the lowest-total-cost P1 path from from_node to to_node."""
    if from_node == to_node:
        return zero_path(from_node, to_node)

    lookup = build_best_path_lookup(outputs.path_cost)
    path = lookup.get((from_node, to_node))
    if path is None:
        raise KeyError(f"No P1 path found from {from_node} to {to_node}")
    return path


def parse_path_geometry(path_geometry):
    """Convert 'x,y;x,y;...' into [(x, y), ...]."""
    if pd.isna(path_geometry) or not str(path_geometry).strip():
        return []

    points = []
    for token in str(path_geometry).split(";"):
        x_text, y_text = token.split(",")
        points.append((float(x_text), float(y_text)))
    return points


def parse_grid_cell_sequence(grid_cell_sequence):
    """Convert 'row:col;row:col;...' into [(row, col), ...]."""
    if pd.isna(grid_cell_sequence) or not str(grid_cell_sequence).strip():
        return []

    cells = []
    for token in str(grid_cell_sequence).split(";"):
        row_text, col_text = token.split(":")
        cells.append((int(row_text), int(col_text)))
    return cells


def get_path_by_uid(outputs, path_uid):
    matches = outputs.path_cost[outputs.path_cost["path_uid"] == path_uid]
    if matches.empty:
        raise KeyError(f"No P1 path found with path_uid={path_uid}")
    return matches.iloc[0].to_dict()


def get_path_geometry(outputs, path_uid):
    path = get_path_by_uid(outputs, path_uid)
    return parse_path_geometry(path.get("path_geometry", ""))


def get_grid_cells(outputs, path_uid):
    rows = outputs.path_grid_cells[outputs.path_grid_cells["path_uid"] == path_uid]
    if rows.empty and path_uid != "same_node":
        raise KeyError(f"No grid cells found with path_uid={path_uid}")
    return rows.sort_values("cell_index").copy()


def get_trajectory_samples(outputs, path_uid, start_time=0.0):
    """Return trajectory samples and add absolute_time = start_time + offset_time."""
    rows = outputs.path_trajectory_samples[
        outputs.path_trajectory_samples["path_uid"] == path_uid
    ].copy()
    if rows.empty and path_uid != "same_node":
        raise KeyError(f"No trajectory samples found with path_uid={path_uid}")

    if rows.empty:
        return rows

    rows = rows.sort_values("sample_index")
    rows["absolute_time"] = rows["offset_time"].astype(float) + float(start_time)
    return rows


def get_pair_geometry(outputs, from_node, to_node):
    path = get_best_path(outputs, from_node, to_node)
    return parse_path_geometry(path.get("path_geometry", ""))


def get_pair_trajectory(outputs, from_node, to_node, start_time=0.0):
    path = get_best_path(outputs, from_node, to_node)
    return get_trajectory_samples(outputs, path["path_uid"], start_time=start_time)


def smoke_check(from_node="IN1", to_node="P5", algorithm=DEFAULT_P1_ALGORITHM):
    outputs = load_p1_outputs(algorithm=algorithm)
    path = get_best_path(outputs, from_node, to_node)
    geometry = parse_path_geometry(path["path_geometry"])
    trajectory = get_trajectory_samples(outputs, path["path_uid"])

    return {
        "pair": f"{from_node}->{to_node}",
        "algorithm": algorithm,
        "path_uid": path["path_uid"],
        "distance": path["distance"],
        "travel_time": path["travel_time"],
        "total_cost": path["total_cost"],
        "geometry_points": len(geometry),
        "trajectory_samples": len(trajectory),
    }


def main():
    args = parse_args()
    result = smoke_check(args.from_node, args.to_node, algorithm=args.algorithm)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
