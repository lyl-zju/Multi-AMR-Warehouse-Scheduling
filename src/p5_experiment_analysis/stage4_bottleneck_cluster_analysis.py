from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
from collections import defaultdict, deque
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[2] / ".mplconfig"))

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from p5_utils import configure_chinese_style, read_csv_rows, to_float


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
P1_DIR = PROJECT_ROOT / "data" / "processed" / "p1" / "mixed"
P3_DIR = PROJECT_ROOT / "data" / "processed" / "p3"
P4_DIR = PROJECT_ROOT / "data" / "processed" / "p4"
P5_ROOT = PROJECT_ROOT / "data" / "processed" / "p5"
P5_DIR = P5_ROOT / "stage4_bottleneck_cluster"
STAGE3_DIR = P5_ROOT / "stage3_task_capacity"
OUT_DIR = PROJECT_ROOT / "outputs" / "p5"
REPORT_FIGURE_DIR = WORKSPACE_ROOT / "report" / "figures"

P0_SRC_DIR = PROJECT_ROOT / "src" / "p0_data_scene"
P3_SRC_DIR = PROJECT_ROOT / "src" / "p3_schedule_conflicts"
sys.path.insert(0, str(P0_SRC_DIR))
sys.path.insert(0, str(P3_SRC_DIR))
import plot_floor_plan as p0  # noqa: E402
import schedule_and_detect_conflicts as p3  # noqa: E402


CELL_SIZE = 1.0
TIME_BUCKET = 1.0
DEFAULT_TOP_K = 0
PRIMARY_P3_MODE = "wait_reroute_resequence"
PRIMARY_WEIGHTS = {
    "occ": 0.25,
    "conflict": 0.30,
    "wait": 0.30,
    "delay": 0.15,
}
EPS = 1e-9


def parse_args():
    parser = argparse.ArgumentParser(description="P5 stage4 spatial bottleneck finite-difference analysis.")
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="Number of candidate clusters to re-optimize (0 = all bottleneck candidates).",
    )
    parser.add_argument("--capacity-plus", type=int, default=1, help="Unit relaxation applied to each candidate cluster.")
    parser.add_argument(
        "--skip-global-sweep",
        action="store_true",
        help="Skip the all path-cell capacity sweep and only test screened bottleneck candidates.",
    )
    parser.add_argument(
        "--global-limit",
        type=int,
        default=0,
        help="Debug limit for global path-cell tests (0 = all path-library cells).",
    )
    return parser.parse_args()


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def as_float(value, default=0.0) -> float:
    if value is None or str(value).strip() == "":
        return default
    return float(value)


def as_int(value, default=0) -> int:
    if value is None or str(value).strip() == "":
        return default
    return int(float(value))


def cell_of_xy(x: float, y: float) -> tuple[int, int]:
    return (math.floor(float(x) / CELL_SIZE), math.floor(float(y) / CELL_SIZE))


def cell_id(cell: tuple[int, int]) -> str:
    return f"{cell[0]},{cell[1]}"


def parse_cell_id(value: str) -> tuple[int, int]:
    left, right = str(value).split(",", 1)
    return int(left), int(right)


def selected_stage3_case() -> dict:
    report_path = STAGE3_DIR / "stage3_validation_report.json"
    if not report_path.exists():
        raise RuntimeError("stage3_validation_report.json not found; run stage3 first")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "PASS":
        raise RuntimeError(f"stage3 validation is not PASS: {report.get('status')}")

    summary = report.get("summary", {})
    n_capacity = int(summary.get("n_capacity", 0))
    method = str(report.get("method", summary.get("method", "")))
    raw_path = Path(report.get("raw_result_csv", STAGE3_DIR / "stage3_task_sweep_raw.csv"))
    rows = read_csv_dicts(raw_path)
    candidates = [
        row
        for row in rows
        if as_int(row.get("n")) == n_capacity and str(row.get("method", "")) == method
    ]
    if not candidates:
        raise RuntimeError(f"no stage3 raw rows for method={method}, n_capacity={n_capacity}")
    candidates = sorted(candidates, key=lambda row: as_float(row.get("Cmax")))
    selected = candidates[len(candidates) // 2]
    out_dir = PROJECT_ROOT / selected["case_output_dir"]
    assignment_path = out_dir / "assignment_result.csv"
    if not assignment_path.exists():
        raise RuntimeError(f"selected stage3 assignment does not exist: {assignment_path}")
    return {
        "method": method,
        "n_capacity": n_capacity,
        "seed": as_int(selected.get("seed")),
        "run_id": selected.get("run_id", ""),
        "Cmax": as_float(selected.get("Cmax")),
        "case_output_dir": str(out_dir.relative_to(PROJECT_ROOT)),
        "assignment_path": assignment_path,
        "stage3_report": report,
    }


def metrics_from_result(result: dict) -> dict:
    metrics = p3.compute_metrics(result["schedule"], result["conflicts"], result["state"])
    objective = p3.objective_value(result["schedule"], result["conflicts"], result["state"])
    return {
        "remaining_conflicts": int(metrics["remaining_conflicts"]),
        "total_delay": round(float(metrics["total_delay"]), 6),
        "Cmax": round(float(metrics["cmax"]), 6),
        "total_wait_added": round(float(metrics["total_wait_added"]), 6),
        "total_waiting": round(float(metrics["total_waiting"]), 6),
        "reroute_count": int(metrics["reroute_count"]),
        "resequence_count": int(metrics["resequence_count"]),
        "initial_wait_added": round(float(metrics["initial_wait_added"]), 6),
        "objective": round(float(objective), 6),
    }


def run_p3_modes(assignment: pd.DataFrame, trajectory_samples: pd.DataFrame, path_cost: pd.DataFrame):
    original, wait, wait_reroute, wait_reroute_resequence, selected = p3.run_all_modes(
        assignment,
        trajectory_samples,
        path_cost,
    )
    return {
        "original": original,
        "wait": wait,
        "wait_reroute": wait_reroute,
        "wait_reroute_resequence": wait_reroute_resequence,
        "selected": selected,
    }


def run_p3_single_mode(
    assignment: pd.DataFrame,
    trajectory_samples: pd.DataFrame,
    path_cost: pd.DataFrame,
    mode: str,
):
    result = p3.run_repair_mode(assignment, trajectory_samples, path_cost, mode)
    return p3.compress_repair_result(result, trajectory_samples)


def original_state_result(assignment: pd.DataFrame, trajectory_samples: pd.DataFrame) -> dict:
    state = p3.RepairState(assignment)
    schedule, trajectory, conflicts, objective = p3.evaluate_state(state, trajectory_samples)
    return {
        "mode": "original",
        "state": state,
        "schedule": schedule,
        "trajectory": trajectory,
        "conflicts": conflicts,
        "process_log": pd.DataFrame(columns=p3.PROCESS_LOG_COLUMNS),
        "initial_conflicts": len(conflicts),
        "iterations": 0,
        "objective": objective,
    }


def locate_event_cell(trajectory: pd.DataFrame, row: pd.Series) -> tuple[int, int] | None:
    amr_id = str(row.get("target_amr", ""))
    task_id = str(row.get("target_task", ""))
    if not amr_id or not task_id:
        return None
    try:
        conflict_time = float(row.get("conflict_time"))
    except (TypeError, ValueError):
        return None
    matches = trajectory[
        (trajectory["amr_id"].astype(str) == amr_id)
        & (trajectory["task_id"].astype(str) == task_id)
    ].copy()
    if matches.empty:
        return None
    matches["time_gap"] = (pd.to_numeric(matches["absolute_time"], errors="coerce") - conflict_time).abs()
    nearest = matches.sort_values("time_gap").iloc[0]
    return cell_of_xy(float(nearest["x"]), float(nearest["y"]))


def add_occ_scores(trajectory: pd.DataFrame) -> dict[tuple[int, int], float]:
    seen = set()
    counts: dict[tuple[int, int], float] = defaultdict(float)
    for row in trajectory.itertuples(index=False):
        cell = cell_of_xy(float(row.x), float(row.y))
        bucket = math.floor(float(row.absolute_time) / TIME_BUCKET)
        key = (cell, bucket, str(row.amr_id))
        if key in seen:
            continue
        seen.add(key)
        counts[cell] += 1.0
    return counts


def add_wait_scores(trajectory: pd.DataFrame, process_log: pd.DataFrame) -> tuple[dict[tuple[int, int], float], list[dict]]:
    wait_by_cell: dict[tuple[int, int], float] = defaultdict(float)
    workflow_rows = []

    for row in trajectory.itertuples(index=False):
        segment = str(row.segment_type)
        if segment.startswith("mid_path_wait") or segment == "repair_wait_at_node":
            wait_by_cell[cell_of_xy(float(row.x), float(row.y))] += p3.TRAJECTORY_SAMPLE_STEP

    if not process_log.empty:
        for row in process_log.itertuples(index=False):
            action = str(getattr(row, "action", ""))
            if action not in {"mid_path_wait", "wait_before_task", "reroute", "resequence"}:
                continue
            wait_added = as_float(getattr(row, "wait_added", 0.0))
            row_series = pd.Series(row._asdict())
            cell = locate_event_cell(trajectory, row_series)
            if cell is None:
                continue
            wait_by_cell[cell] += wait_added
            workflow_rows.append(
                {
                    "mode": getattr(row, "mode", ""),
                    "iteration": getattr(row, "iteration", ""),
                    "conflict_time": getattr(row, "conflict_time", ""),
                    "action": action,
                    "target_amr": getattr(row, "target_amr", ""),
                    "target_task": getattr(row, "target_task", ""),
                    "segment_type": getattr(row, "segment_type", ""),
                    "cell_id": cell_id(cell),
                    "wait_added": round(wait_added, 6),
                    "before_conflicts": getattr(row, "before_conflicts", ""),
                    "after_conflicts": getattr(row, "after_conflicts", ""),
                }
            )
    return wait_by_cell, workflow_rows


def add_conflict_scores(trajectory: pd.DataFrame, process_log: pd.DataFrame) -> dict[tuple[int, int], float]:
    counts: dict[tuple[int, int], float] = defaultdict(float)
    if process_log.empty:
        return counts
    seen = set()
    for row in process_log.itertuples(index=False):
        action = str(getattr(row, "action", ""))
        if action == "no_improving_action":
            continue
        row_series = pd.Series(row._asdict())
        cell = locate_event_cell(trajectory, row_series)
        if cell is None:
            continue
        dedupe_key = (
            str(getattr(row, "mode", "")),
            str(getattr(row, "iteration", "")),
            str(getattr(row, "conflict_time", "")),
            str(getattr(row, "target_amr", "")),
            str(getattr(row, "target_task", "")),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        counts[cell] += 1.0
    return counts


def add_delay_scores(trajectory: pd.DataFrame, schedule: pd.DataFrame) -> dict[tuple[int, int], float]:
    delay_by_task = {}
    for row in schedule.itertuples(index=False):
        delay = as_float(getattr(row, "delay", 0.0))
        if delay <= EPS:
            continue
        delay_by_task[(str(row.amr_id), str(row.task_id), int(row.sequence_order))] = delay
    if not delay_by_task:
        return {}

    cells_by_task: dict[tuple[str, str, int], set[tuple[int, int]]] = defaultdict(set)
    for row in trajectory.itertuples(index=False):
        key = (str(row.amr_id), str(row.task_id), int(row.sequence_order))
        if key in delay_by_task:
            cells_by_task[key].add(cell_of_xy(float(row.x), float(row.y)))

    delay_by_cell: dict[tuple[int, int], float] = defaultdict(float)
    for key, cells in cells_by_task.items():
        if not cells:
            continue
        share = delay_by_task[key] / len(cells)
        for cell in cells:
            delay_by_cell[cell] += share
    return delay_by_cell


def z_scores(values_by_cell: dict[tuple[int, int], float], cells: set[tuple[int, int]]) -> dict[tuple[int, int], float]:
    values = np.array([float(values_by_cell.get(cell, 0.0)) for cell in cells], dtype=float)
    if values.size == 0:
        return {}
    mean = float(values.mean())
    std = float(values.std())
    if std <= EPS:
        std = 1.0
    return {cell: (float(values_by_cell.get(cell, 0.0)) - mean) / std for cell in cells}


def connected_components(hot_cells: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    components = []
    seen = set()
    for start in sorted(hot_cells):
        if start in seen:
            continue
        comp = []
        queue = deque([start])
        seen.add(start)
        while queue:
            cell = queue.popleft()
            comp.append(cell)
            x, y = cell
            for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nxt in hot_cells and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        components.append(sorted(comp))
    return components


def rect_distance(px: float, py: float, rect: dict) -> float:
    x = to_float(rect["x"])
    y = to_float(rect["y"])
    width = to_float(rect["width"])
    height = to_float(rect["height"])
    dx = max(x - px, 0.0, px - (x + width))
    dy = max(y - py, 0.0, py - (y + height))
    return math.hypot(dx, dy)


def nearest_rect(px: float, py: float, rows: list[dict], id_key: str) -> str:
    if not rows:
        raise RuntimeError(f"cannot map cluster to nearest {id_key}: no rows")
    nearest = min(rows, key=lambda row: rect_distance(px, py, row))
    value = str(nearest.get(id_key, "")).strip()
    if not value:
        raise RuntimeError(f"nearest {id_key} is blank")
    return value


def compute_cell_and_cluster_rows(
    selected: dict,
    p4_trajectory: pd.DataFrame | None,
    zones: list[dict],
    obstacles: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    trajectory = selected["trajectory"].copy()
    schedule = selected["schedule"].copy()
    process_log = selected["process_log"].copy()

    occ = add_occ_scores(trajectory)
    conflict = add_conflict_scores(trajectory, process_log)
    wait, workflow_rows = add_wait_scores(trajectory, process_log)
    delay = add_delay_scores(trajectory, schedule)
    p4_occ = add_occ_scores(p4_trajectory) if p4_trajectory is not None and not p4_trajectory.empty else {}

    all_cells = set(occ) | set(conflict) | set(wait) | set(delay)
    if not all_cells:
        raise RuntimeError("no stage4 cells found")

    z_occ = z_scores(occ, all_cells)
    z_conflict = z_scores(conflict, all_cells)
    z_wait = z_scores(wait, all_cells)
    z_delay = z_scores(delay, all_cells)

    cell_rows = []
    score_by_cell = {}
    for cell in sorted(all_cells):
        score = (
            PRIMARY_WEIGHTS["occ"] * z_occ.get(cell, 0.0)
            + PRIMARY_WEIGHTS["conflict"] * z_conflict.get(cell, 0.0)
            + PRIMARY_WEIGHTS["wait"] * z_wait.get(cell, 0.0)
            + PRIMARY_WEIGHTS["delay"] * z_delay.get(cell, 0.0)
        )
        score_by_cell[cell] = score
        cell_rows.append(
            {
                "cell_id": cell_id(cell),
                "cell_x": cell[0],
                "cell_y": cell[1],
                "center_x": round(cell[0] + 0.5, 6),
                "center_y": round(cell[1] + 0.5, 6),
                "occ_score": round(float(occ.get(cell, 0.0)), 6),
                "conflict_score": round(float(conflict.get(cell, 0.0)), 6),
                "wait_score": round(float(wait.get(cell, 0.0)), 6),
                "delay_score": round(float(delay.get(cell, 0.0)), 6),
                "p4_occ_score": round(float(p4_occ.get(cell, 0.0)), 6),
                "z_occ": round(float(z_occ.get(cell, 0.0)), 6),
                "z_conflict": round(float(z_conflict.get(cell, 0.0)), 6),
                "z_wait": round(float(z_wait.get(cell, 0.0)), 6),
                "z_delay": round(float(z_delay.get(cell, 0.0)), 6),
                "screening_score": round(float(score), 6),
            }
        )

    positive_scores = [score for score in score_by_cell.values() if score > 0.0]
    if not positive_scores:
        raise RuntimeError("no positive scoring cells; cannot form bottleneck clusters")
    threshold = float(np.quantile(np.array(positive_scores), 0.70))
    hot_cells = {
        cell
        for cell, score in score_by_cell.items()
        if score >= threshold and (conflict.get(cell, 0.0) > 0.0 or wait.get(cell, 0.0) > 0.0)
    }
    if not hot_cells:
        hot_cells = {cell for cell, score in score_by_cell.items() if score > 0.0}
    clusters = connected_components(hot_cells)
    if not clusters:
        raise RuntimeError("no connected clusters found")

    cluster_rows = []
    for idx, cells in enumerate(clusters, start=1):
        centroid_x = sum(cell[0] + 0.5 for cell in cells) / len(cells)
        centroid_y = sum(cell[1] + 0.5 for cell in cells) / len(cells)
        total_occ = sum(occ.get(cell, 0.0) for cell in cells)
        total_conflict = sum(conflict.get(cell, 0.0) for cell in cells)
        total_wait = sum(wait.get(cell, 0.0) for cell in cells)
        total_delay = sum(delay.get(cell, 0.0) for cell in cells)
        mean_score = sum(score_by_cell[cell] for cell in cells) / len(cells)
        label = "bottleneck_candidate" if total_conflict > 0.0 or total_wait > 0.0 else "traffic_only"
        cluster_rows.append(
            {
                "cluster_id": f"C{idx:02d}",
                "cell_ids": ";".join(cell_id(cell) for cell in cells),
                "cell_count": len(cells),
                "centroid_x": round(centroid_x, 6),
                "centroid_y": round(centroid_y, 6),
                "occ_score": round(float(total_occ), 6),
                "conflict_score": round(float(total_conflict), 6),
                "wait_score": round(float(total_wait), 6),
                "delay_score": round(float(total_delay), 6),
                "total_score": round(float(mean_score), 6),
                "cluster_type": label,
                "nearest_zone": nearest_rect(centroid_x, centroid_y, zones, "zone_id"),
                "nearest_obstacle_id": nearest_rect(centroid_x, centroid_y, obstacles, "obstacle_id"),
                "p4_occ_score": round(float(sum(p4_occ.get(cell, 0.0) for cell in cells)), 6),
            }
        )

    cluster_rows = sorted(cluster_rows, key=lambda row: float(row["total_score"]), reverse=True)
    for rank, row in enumerate(cluster_rows, start=1):
        row["screening_rank"] = rank
    return cell_rows, cluster_rows, workflow_rows


def relaxed_detector_factory(cluster_cells: set[tuple[int, int]], relaxed_capacity: int):
    def in_cluster(x: float, y: float) -> bool:
        return cell_of_xy(x, y) in cluster_cells

    def cluster_load(samples: pd.DataFrame, time_value: float) -> int:
        low = float(time_value) - p3.TIME_TOLERANCE
        high = float(time_value) + p3.TIME_TOLERANCE
        window = samples[(samples["absolute_time"] >= low) & (samples["absolute_time"] <= high)]
        if window.empty:
            return 0
        mask = [
            cell_of_xy(float(row.x), float(row.y)) in cluster_cells
            for row in window.itertuples(index=False)
        ]
        if not any(mask):
            return 0
        return int(window.loc[mask, "amr_id"].astype(str).nunique())

    def detect(trajectory_schedule: pd.DataFrame):
        if trajectory_schedule.empty:
            return pd.DataFrame(columns=p3.CONFLICT_COLUMNS)
        samples = p3.resample_trajectory_schedule(trajectory_schedule)
        conflicts = []
        rows = list(samples.itertuples(index=False))
        for left_index, left in enumerate(rows):
            left_time = float(left.absolute_time)
            for right in rows[left_index + 1 :]:
                time_delta = float(right.absolute_time) - left_time
                if time_delta > p3.TIME_TOLERANCE:
                    break
                if str(left.amr_id) == str(right.amr_id):
                    continue
                distance = math.hypot(float(left.x) - float(right.x), float(left.y) - float(right.y))
                threshold = float(left.footprint_radius) + float(right.footprint_radius) + p3.SAFETY_MARGIN
                if distance >= threshold:
                    continue
                conflict_time = (left_time + float(right.absolute_time)) / 2.0
                cx = (float(left.x) + float(right.x)) / 2.0
                cy = (float(left.y) + float(right.y)) / 2.0
                if in_cluster(cx, cy) and cluster_load(samples, conflict_time) <= relaxed_capacity:
                    continue
                conflicts.append(
                    {
                        "time": f"{conflict_time:g}",
                        "location": f"{cx:.2f},{cy:.2f}",
                        "location_type": "space",
                        "involved_amrs": ";".join(sorted([str(left.amr_id), str(right.amr_id)])),
                        "involved_tasks": ";".join(sorted([str(left.task_id), str(right.task_id)])),
                        "involved_path_uids": ";".join(sorted([str(left.path_uid), str(right.path_uid)])),
                        "distance": round(distance, 3),
                        "clearance_threshold": round(threshold, 3),
                        "time_delta": round(abs(time_delta), 3),
                        "repair_action": "pending_p3_repair",
                        "status": "detected",
                        "conflict_method": p3.CONFLICT_METHOD,
                    }
                )

        stationary = samples[samples["path_uid"].astype(str).str.startswith("stationary::")].copy()
        if not stationary.empty:
            stationary["node_id"] = stationary["path_uid"].map(p3.node_from_stationary_path_uid)
            stationary = stationary[stationary["node_id"].map(p3.node_requires_capacity)]
            for (node_id, t), group in stationary.groupby(["node_id", "absolute_time"], sort=True):
                amrs = sorted(group["amr_id"].astype(str).unique())
                gx = float(group["x"].mean())
                gy = float(group["y"].mean())
                local_capacity = relaxed_capacity if in_cluster(gx, gy) else 1
                if len(amrs) <= local_capacity:
                    continue
                conflicts.append(
                    {
                        "time": f"{float(t):g}",
                        "location": f"{gx:.2f},{gy:.2f}",
                        "location_type": f"node:{node_id}",
                        "involved_amrs": ";".join(amrs),
                        "involved_tasks": ";".join(sorted(group["task_id"].astype(str).unique())),
                        "involved_path_uids": ";".join(sorted(group["path_uid"].astype(str).unique())),
                        "distance": 0.0,
                        "clearance_threshold": float(local_capacity),
                        "time_delta": 0.0,
                        "repair_action": "pending_node_capacity_wait_repair",
                        "status": "detected",
                        "conflict_method": "node_capacity_check",
                    }
                )

        if conflicts:
            deduped = {}
            for conflict in conflicts:
                time_bucket = round(float(conflict["time"]) * 2.0) / 2.0
                key = (
                    time_bucket,
                    conflict["location_type"],
                    conflict["involved_amrs"],
                    conflict["involved_tasks"],
                    conflict["involved_path_uids"],
                )
                if key not in deduped or float(conflict["distance"]) < float(deduped[key]["distance"]):
                    deduped[key] = conflict
            conflicts = sorted(deduped.values(), key=lambda item: (float(item["time"]), item["location_type"]))
        for index, conflict in enumerate(conflicts, start=1):
            conflict["conflict_id"] = f"C{index:03d}"
        return pd.DataFrame(conflicts, columns=p3.CONFLICT_COLUMNS)

    return detect


def run_relaxed_case(
    assignment: pd.DataFrame,
    trajectory_samples: pd.DataFrame,
    path_cost: pd.DataFrame,
    cluster_cells: set[tuple[int, int]],
    baseline_capacity: int,
    capacity_plus: int,
    mode: str,
) -> dict:
    original_detector = p3.detect_trajectory_conflicts
    p3.detect_trajectory_conflicts = relaxed_detector_factory(cluster_cells, baseline_capacity + capacity_plus)
    try:
        return run_p3_single_mode(assignment, trajectory_samples, path_cost, mode)
    finally:
        p3.detect_trajectory_conflicts = original_detector


def capacity_tests(
    cluster_rows: list[dict],
    assignment: pd.DataFrame,
    trajectory_samples: pd.DataFrame,
    path_cost: pd.DataFrame,
    baseline_metrics: dict,
    baseline_mode: str,
    top_k: int,
    capacity_plus: int,
) -> list[dict]:
    candidates = [
        row
        for row in cluster_rows
        if row.get("cluster_type") == "bottleneck_candidate"
    ]
    if top_k and top_k > 0:
        candidates = candidates[:top_k]
    rows = []
    for row in candidates:
        print(
            f"Testing {row['cluster_id']} with capacity +{capacity_plus} "
            f"using P3 mode {baseline_mode}...",
            flush=True,
        )
        cells = {parse_cell_id(value) for value in str(row["cell_ids"]).split(";") if value}
        relaxed = run_relaxed_case(
            assignment,
            trajectory_samples,
            path_cost,
            cells,
            baseline_capacity=1,
            capacity_plus=capacity_plus,
            mode=baseline_mode,
        )
        after = metrics_from_result(relaxed)
        delta_cmax = float(baseline_metrics["Cmax"]) - float(after["Cmax"])
        delta_wait = float(baseline_metrics["total_wait_added"]) - float(after["total_wait_added"])
        delta_objective = float(baseline_metrics["objective"]) - float(after["objective"])
        rows.append(
            {
                "cluster_id": row["cluster_id"],
                "baseline_capacity": 1,
                "relaxed_capacity": 1 + capacity_plus,
                "screening_rank": row["screening_rank"],
                "screening_score": row["total_score"],
                "cell_ids": row["cell_ids"],
                "nearest_zone": row["nearest_zone"],
                "nearest_obstacle_id": row["nearest_obstacle_id"],
                "Cmax_baseline": baseline_metrics["Cmax"],
                "Cmax_cap_plus_1": after["Cmax"],
                "Delta_Cmax": round(delta_cmax, 6),
                "wait_baseline": baseline_metrics["total_wait_added"],
                "wait_cap_plus_1": after["total_wait_added"],
                "Delta_wait": round(delta_wait, 6),
                "objective_baseline": baseline_metrics["objective"],
                "objective_cap_plus_1": after["objective"],
                "Delta_objective": round(delta_objective, 6),
                "remaining_conflicts_cap_plus_1": after["remaining_conflicts"],
                "selected_mode_cap_plus_1": baseline_mode,
                "screening_method": "connected trajectory-conflict-wait cluster",
                "final_rank_metric": "Delta_Cmax",
            }
        )
    rows = sorted(
        rows,
        key=lambda item: (
            float(item["Delta_Cmax"]),
            float(item["Delta_wait"]),
            float(item["Delta_objective"]),
        ),
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


def path_library_cells(trajectory_samples: pd.DataFrame) -> set[tuple[int, int]]:
    cells = set()
    for row in trajectory_samples.itertuples(index=False):
        cells.add(cell_of_xy(float(row.x), float(row.y)))
    return cells


def cell_lookup(cell_rows: list[dict]) -> dict[str, dict]:
    return {str(row["cell_id"]): row for row in cell_rows}


def global_capacity_tests(
    cells: set[tuple[int, int]],
    cell_rows: list[dict],
    assignment: pd.DataFrame,
    trajectory_samples: pd.DataFrame,
    path_cost: pd.DataFrame,
    zones: list[dict],
    obstacles: list[dict],
    baseline_metrics: dict,
    baseline_mode: str,
    capacity_plus: int,
    limit: int = 0,
) -> list[dict]:
    lookup = cell_lookup(cell_rows)
    rows = []
    ordered_cells = sorted(cells)
    if limit and limit > 0:
        ordered_cells = ordered_cells[:limit]
    total = len(ordered_cells)
    for index, cell in enumerate(ordered_cells, start=1):
        cid = f"G{index:03d}"
        cid_text = cell_id(cell)
        print(
            f"Global cell test {index}/{total}: {cid_text} capacity +{capacity_plus} "
            f"using P3 mode {baseline_mode}...",
            flush=True,
        )
        relaxed = run_relaxed_case(
            assignment,
            trajectory_samples,
            path_cost,
            {cell},
            baseline_capacity=1,
            capacity_plus=capacity_plus,
            mode=baseline_mode,
        )
        after = metrics_from_result(relaxed)
        delta_cmax = float(baseline_metrics["Cmax"]) - float(after["Cmax"])
        delta_wait = float(baseline_metrics["total_wait_added"]) - float(after["total_wait_added"])
        delta_objective = float(baseline_metrics["objective"]) - float(after["objective"])
        source = lookup.get(cid_text, {})
        center_x = cell[0] + 0.5
        center_y = cell[1] + 0.5
        rows.append(
            {
                "global_id": cid,
                "cluster_id": cid,
                "cell_ids": cid_text,
                "cell_x": cell[0],
                "cell_y": cell[1],
                "center_x": round(center_x, 6),
                "center_y": round(center_y, 6),
                "baseline_capacity": 1,
                "relaxed_capacity": 1 + capacity_plus,
                "screening_score": source.get("screening_score", ""),
                "occ_score": source.get("occ_score", 0.0),
                "conflict_score": source.get("conflict_score", 0.0),
                "wait_score": source.get("wait_score", 0.0),
                "delay_score": source.get("delay_score", 0.0),
                "nearest_zone": nearest_rect(center_x, center_y, zones, "zone_id"),
                "nearest_obstacle_id": nearest_rect(center_x, center_y, obstacles, "obstacle_id"),
                "Cmax_baseline": baseline_metrics["Cmax"],
                "Cmax_cap_plus_1": after["Cmax"],
                "Delta_Cmax": round(delta_cmax, 6),
                "wait_baseline": baseline_metrics["total_wait_added"],
                "wait_cap_plus_1": after["total_wait_added"],
                "Delta_wait": round(delta_wait, 6),
                "objective_baseline": baseline_metrics["objective"],
                "objective_cap_plus_1": after["objective"],
                "Delta_objective": round(delta_objective, 6),
                "remaining_conflicts_cap_plus_1": after["remaining_conflicts"],
                "selected_mode_cap_plus_1": baseline_mode,
                "screening_method": "exhaustive P1 path-library grid cell sweep",
                "final_rank_metric": "Delta_Cmax",
            }
        )
    rows = sorted(
        rows,
        key=lambda item: (
            float(item["Delta_Cmax"]),
            float(item["Delta_wait"]),
            float(item["Delta_objective"]),
        ),
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


def update_cluster_ranks(cluster_rows: list[dict], test_rows: list[dict]) -> list[dict]:
    by_cluster = {row["cluster_id"]: row for row in test_rows}
    out = []
    for row in cluster_rows:
        item = dict(row)
        test = by_cluster.get(item["cluster_id"])
        if test:
            item["Delta_Cmax"] = test["Delta_Cmax"]
            item["Delta_wait"] = test["Delta_wait"]
            item["Delta_objective"] = test["Delta_objective"]
            item["final_rank"] = test["rank"]
        else:
            item["Delta_Cmax"] = ""
            item["Delta_wait"] = ""
            item["Delta_objective"] = ""
            item["final_rank"] = ""
        out.append(item)
    return out


def plot_stage4(
    nodes: pd.DataFrame,
    amrs: pd.DataFrame,
    tasks: pd.DataFrame,
    zones: list[dict],
    obstacles: list[dict],
    cluster_rows: list[dict],
    test_rows: list[dict],
    output_png: Path,
    output_pdf: Path,
) -> None:
    configure_chinese_style()
    fig, ax_map = plt.subplots(1, 1, figsize=(8.6, 5.4))
    fig.subplots_adjust(left=0.09, right=0.985, bottom=0.12, top=0.92)

    def draw_p0_floor_map(ax: plt.Axes) -> None:
        floor = Rectangle(
            (-0.8, -0.8),
            13.6,
            7.4,
            facecolor="#fbfbf7",
            edgecolor="#111827",
            linewidth=1.9,
            zorder=0,
        )
        ax.add_patch(floor)
        p0.draw_rectangles(ax, nodes.iloc[0:0], p0.ZONE_STYLE, "zone_type")
        p0.draw_rectangles(ax, pd.DataFrame(zones), p0.ZONE_STYLE, "zone_type")
        p0.draw_rectangles(ax, pd.DataFrame(obstacles), p0.OBSTACLE_STYLE, "obstacle_type", label_mode="obstacle")

        for node_type, style in p0.POINT_STYLE.items():
            group = nodes[nodes["node_type"].astype(str) == node_type]
            if group.empty:
                continue
            ax.scatter(
                group["x"],
                group["y"],
                s=style["size"],
                c=style["color"],
                marker=style["marker"],
                edgecolors="white",
                linewidths=1.0,
                alpha=0.40 if node_type == "junction" else 0.95,
                zorder=5,
            )

        for node in nodes.itertuples(index=False):
            if str(node.node_type) in {"inbound", "outbound", "shelf", "sorting", "charger"}:
                ax.text(
                    float(node.x),
                    float(node.y) + 0.22,
                    str(node.node_id),
                    fontsize=7,
                    ha="center",
                    va="bottom",
                    zorder=6,
                )

        pos = {row.node_id: (float(row.x), float(row.y)) for row in nodes.itertuples(index=False)}
        for amr in amrs.itertuples(index=False):
            x, y = pos.get(str(amr.init_node), (None, None))
            if x is None:
                continue
            ax.scatter(
                [x],
                [y - 0.34],
                s=170,
                marker="*",
                c="#111827",
                edgecolors="white",
                linewidths=1.0,
                zorder=7,
            )
            ax.text(x, y - 0.67, str(amr.amr_id), fontsize=7, ha="center", color="#111827", zorder=8)

        pickup_nodes = set(tasks["pickup"].astype(str))
        delivery_nodes = set(tasks["delivery"].astype(str))
        for node_id in pickup_nodes:
            if node_id in pos:
                x, y = pos[node_id]
                ax.scatter([x + 0.18], [y + 0.18], s=56, marker="^", c="#06b6d4", zorder=8)
        for node_id in delivery_nodes:
            if node_id in pos:
                x, y = pos[node_id]
                ax.scatter([x - 0.18], [y - 0.18], s=56, marker="v", c="#ef4444", zorder=8)

        ax.set_xlim(-1.0, 13.0)
        ax.set_ylim(-1.0, 6.8)
        ax.set_aspect("equal", adjustable="box")
        ax.set_facecolor("#fbfbf7")
        ax.set_xlabel("x 方向（米）")
        ax.set_ylabel("y 方向（米）")
        ax.grid(True, linestyle="--", linewidth=0.45, alpha=0.22)

    draw_p0_floor_map(ax_map)

    screened_rows = [row for row in test_rows if str(row["cluster_id"]).startswith("C")]
    unselected_rows = [row for row in test_rows if str(row["cluster_id"]).startswith("G")]
    positive_rows = [row for row in test_rows if float(row["Delta_Cmax"]) > EPS]
    screened_zero_rows = [
        row
        for row in screened_rows
        if float(row["Delta_Cmax"]) <= EPS
    ]

    def draw_capacity_cells(
        rows: list[dict],
        facecolor: str,
        edgecolor: str,
        alpha: float,
        linewidth: float,
        zorder: int,
    ) -> None:
        for row in rows:
            cells = [parse_cell_id(value) for value in str(row["cell_ids"]).split(";") if value]
            for cell in cells:
                ax_map.add_patch(
                    Rectangle(
                        (cell[0], cell[1]),
                        1.0,
                        1.0,
                        facecolor=facecolor,
                        edgecolor=edgecolor,
                        linewidth=linewidth,
                        alpha=alpha,
                        zorder=zorder,
                    )
                )

    draw_capacity_cells(unselected_rows, "#94a3b8", "#475569", 0.16, 0.8, 4)
    draw_capacity_cells(screened_zero_rows, "#facc15", "#854d0e", 0.24, 1.1, 5)
    draw_capacity_cells(positive_rows, "#22c55e", "#14532d", 0.36, 1.8, 6)
    legend_handles = [
        Rectangle((0, 0), 1, 1, facecolor="#22c55e", edgecolor="#14532d", alpha=0.36, label="容量+1后正收益"),
        Rectangle((0, 0), 1, 1, facecolor="#facc15", edgecolor="#854d0e", alpha=0.24, label="筛选候选但零收益"),
        Rectangle((0, 0), 1, 1, facecolor="#94a3b8", edgecolor="#475569", alpha=0.16, label="未筛入补集验证"),
    ]
    ax_map.legend(handles=legend_handles, loc="lower right", fontsize=7, frameon=True, framealpha=0.88)
    ax_map.text(
        0.02,
        0.98,
        f"验证网格：筛选 {len(screened_rows)}，补集 {len(unselected_rows)}，正收益 {len(positive_rows)}",
        transform=ax_map.transAxes,
        ha="left",
        va="top",
        fontsize=7,
        color="#111827",
        bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": "#cbd5e1", "alpha": 0.86},
        zorder=20,
    )
    ax_map.set_title("容量松弛验证位置", fontsize=13, fontweight="bold")
    output_png.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=300)
    fig.savefig(output_pdf)
    plt.close(fig)


def summary_rows(stage3_case: dict, baseline_metrics: dict, test_rows: list[dict]) -> list[dict]:
    if not test_rows:
        return []
    best = test_rows[0]
    status = "single_cluster_bottleneck" if float(best["Delta_Cmax"]) > 1e-6 else "no_dominant_single_cluster"
    return [
        {
            "stage3_run_id": stage3_case["run_id"],
            "stage3_method": stage3_case["method"],
            "n_capacity": stage3_case["n_capacity"],
            "selected_seed": stage3_case["seed"],
            "baseline_Cmax": baseline_metrics["Cmax"],
            "baseline_total_wait_added": baseline_metrics["total_wait_added"],
            "baseline_objective": baseline_metrics["objective"],
            "best_cluster_id": best["cluster_id"],
            "best_nearest_zone": best["nearest_zone"],
            "best_nearest_obstacle_id": best["nearest_obstacle_id"],
            "best_Delta_Cmax": best["Delta_Cmax"],
            "best_Delta_wait": best["Delta_wait"],
            "best_Delta_objective": best["Delta_objective"],
            "status": status,
            "interpretation": "finite-difference shadow value from P3 spatial capacity relaxation",
        }
    ]


def main():
    args = parse_args()
    stage3_case = selected_stage3_case()
    assignment = pd.read_csv(stage3_case["assignment_path"])
    trajectory_samples = pd.read_csv(P1_DIR / "path_trajectory_samples.csv")
    path_cost = pd.read_csv(P1_DIR / "path_cost.csv")
    nodes, amrs, tasks, zones_df, obstacles_df = p0.load_data()
    zones = zones_df.to_dict("records")
    obstacles = obstacles_df.to_dict("records")
    p4_trajectory = pd.read_csv(P4_DIR / "trajectory_schedule.csv") if (P4_DIR / "trajectory_schedule.csv").exists() else None

    print(f"Running Stage 4 baseline P3 mode {PRIMARY_P3_MODE}...", flush=True)
    baseline_original = original_state_result(assignment, trajectory_samples)
    selected = run_p3_single_mode(assignment, trajectory_samples, path_cost, PRIMARY_P3_MODE)
    baseline_metrics = metrics_from_result(selected)

    cell_rows, cluster_rows, workflow_rows = compute_cell_and_cluster_rows(
        selected,
        p4_trajectory,
        zones,
        obstacles,
    )
    test_rows = capacity_tests(
        cluster_rows,
        assignment,
        trajectory_samples,
        path_cost,
        baseline_metrics,
        PRIMARY_P3_MODE,
        top_k=args.top_k,
        capacity_plus=args.capacity_plus,
    )
    extra_test_rows = []
    if args.skip_global_sweep:
        print("Skipping unselected path-cell capacity sweep.", flush=True)
    else:
        screened_cells = {parse_cell_id(row["cell_id"]) for row in cell_rows}
        extra_cells = path_library_cells(trajectory_samples) - screened_cells
        extra_test_rows = global_capacity_tests(
            extra_cells,
            cell_rows,
            assignment,
            trajectory_samples,
            path_cost,
            zones,
            obstacles,
            baseline_metrics,
            PRIMARY_P3_MODE,
            capacity_plus=args.capacity_plus,
            limit=args.global_limit,
        )
    cluster_rows = update_cluster_ranks(cluster_rows, test_rows)
    final_test_rows = sorted(
        test_rows + extra_test_rows,
        key=lambda item: (
            float(item["Delta_Cmax"]),
            float(item["Delta_wait"]),
            float(item["Delta_objective"]),
        ),
        reverse=True,
    )
    summary = summary_rows(stage3_case, baseline_metrics, final_test_rows)

    P5_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(P5_DIR / "stage4_cells.csv", cell_rows)
    write_csv(P5_DIR / "stage4_clusters.csv", cluster_rows)
    write_csv(P5_DIR / "stage4_wait_workflow.csv", workflow_rows)
    write_csv(P5_DIR / "stage4_capacity_tests.csv", test_rows)
    if extra_test_rows:
        write_csv(P5_DIR / "stage4_unselected_capacity_tests.csv", extra_test_rows)
    write_csv(P5_DIR / "stage4_hotspot_summary.csv", summary)

    validation = {
        "status": "PASS" if test_rows and summary else "FAIL",
        "errors": [] if test_rows and summary else ["no capacity tests generated"],
        "stage3_case": {
            key: value
            for key, value in stage3_case.items()
            if key not in {"assignment_path", "stage3_report"}
        },
        "baseline_metrics": baseline_metrics,
        "screened_test_count": len(test_rows),
        "unselected_test_count": len(extra_test_rows),
        "top_test_count": len(final_test_rows),
        "outputs": {
            "cells": str(P5_DIR / "stage4_cells.csv"),
            "clusters": str(P5_DIR / "stage4_clusters.csv"),
            "capacity_tests": str(P5_DIR / "stage4_capacity_tests.csv"),
            "unselected_capacity_tests": str(P5_DIR / "stage4_unselected_capacity_tests.csv") if extra_test_rows else "",
            "summary": str(P5_DIR / "stage4_hotspot_summary.csv"),
        },
    }
    (P5_DIR / "stage4_validation_report.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    output_png = OUT_DIR / "stage4_bottleneck_cluster.png"
    output_pdf = OUT_DIR / "stage4_bottleneck_cluster.pdf"
    plot_stage4(nodes, amrs, tasks, zones_df, obstacles_df, cluster_rows, final_test_rows, output_png, output_pdf)
    REPORT_FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(output_png, REPORT_FIGURE_DIR / "stage4_bottleneck_cluster.png")
    shutil.copyfile(output_pdf, REPORT_FIGURE_DIR / "stage4_bottleneck_cluster.pdf")

    print(f"Stage3 case: {stage3_case['run_id']} n={stage3_case['n_capacity']} seed={stage3_case['seed']}")
    print(f"Baseline Cmax: {baseline_metrics['Cmax']:.6f}, objective: {baseline_metrics['objective']:.6f}")
    if final_test_rows:
        best = final_test_rows[0]
        print(
            "Best cluster: "
            f"{best['cluster_id']} Delta_objective={float(best['Delta_objective']):.6f} "
            f"Delta_Cmax={float(best['Delta_Cmax']):.6f} "
            f"Delta_wait={float(best['Delta_wait']):.6f}"
        )
    print(f"Saved: {P5_DIR / 'stage4_capacity_tests.csv'}")
    print(f"Saved: {output_png}")


if __name__ == "__main__":
    main()
