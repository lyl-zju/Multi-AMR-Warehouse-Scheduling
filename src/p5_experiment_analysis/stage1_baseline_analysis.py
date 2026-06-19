from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
P2_DIR = PROJECT_ROOT / "data" / "processed" / "p2"
P3_DIR = PROJECT_ROOT / "data" / "processed" / "p3"
P4_DIR = PROJECT_ROOT / "data" / "processed" / "p4"
P5_ROOT = PROJECT_ROOT / "data" / "processed" / "p5"
P5_DIR = P5_ROOT / "stage1_baseline"
OUT_DIR = PROJECT_ROOT / "outputs" / "p5"
TASK_STAGE_DIR = WORKSPACE_ROOT / "task" / "stage1"

EXPECTED_P2_METHOD = "m2_milp_goal_programming+mixed"
EXPECTED_P3_METHOD = "m2_milp_goal_programming+mixed+p3_wait_reroute_resequence"
EXPECTED_P4_METHOD = "rolling_horizon_regret_insertion_local_search"
EXPECTED_P4_STRATEGY = "rolling_horizon"
EXPECTED_P3_SELECTED_MODE = "wait_reroute_resequence"
CELL_SIZE = 1.0

os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".mplconfig"))

import matplotlib.pyplot as plt

from p5_utils import (
    add_top_hotspot_labels,
    build_occupancy_histogram,
    configure_chinese_style,
    draw_floor_context,
    group_rows,
    is_blank,
    natural_amr_order,
    read_csv_rows,
    sort_rows,
    to_float,
    to_int,
    write_csv_rows,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Stage 1 baseline and interface validation.")
    parser.add_argument("--write-report", action="store_true", help="Write markdown reports into task/stage1.")
    return parser.parse_args()


def read_inputs():
    return {
        "tasks": read_csv_rows(RAW_DIR / "tasks.csv"),
        "amrs": read_csv_rows(RAW_DIR / "amrs.csv"),
        "dynamic_events": read_csv_rows(RAW_DIR / "dynamic_events.csv"),
        "floor_zones": read_csv_rows(RAW_DIR / "floor_zones.csv"),
        "floor_obstacles": read_csv_rows(RAW_DIR / "floor_obstacles.csv"),
        "p2_assignment": read_csv_rows(P2_DIR / "assignment_result.csv"),
        "p3_schedule": read_csv_rows(P3_DIR / "schedule_result.csv"),
        "p3_trajectory": read_csv_rows(P3_DIR / "trajectory_schedule.csv"),
        "p3_conflict_log": read_csv_rows(P3_DIR / "conflict_log.csv"),
        "p3_repair_summary": read_csv_rows(P3_DIR / "repair_summary.csv"),
        "p4_reschedule": read_csv_rows(P4_DIR / "reschedule_result.csv"),
        "p4_summary": read_csv_rows(P4_DIR / "reschedule_summary.csv"),
        "p4_trajectory": read_csv_rows(P4_DIR / "trajectory_schedule.csv"),
        "p4_event_impact": read_csv_rows(P4_DIR / "dynamic_event_impact.csv"),
        "p4_method_comparison": read_csv_rows(P4_DIR / "p4_method_comparison.csv"),
        "p4_weight_sensitivity": read_csv_rows(P4_DIR / "p4_weight_sensitivity.csv"),
    }


def require_nonempty(rows, name, validation_rows, errors):
    if not rows:
        errors.append(f"{name}: table is empty")
        validation_rows.append({"check": name, "status": "FAIL", "detail": "table is empty"})
        return False
    validation_rows.append({"check": name, "status": "PASS", "detail": f"{len(rows)} rows"})
    return True


def require_columns(rows, required_columns, name, validation_rows, errors):
    if not rows:
        errors.append(f"{name}: no rows to inspect for columns")
        validation_rows.append({"check": name, "status": "FAIL", "detail": "no rows"})
        return False
    actual = set(rows[0].keys())
    missing = [col for col in required_columns if col not in actual]
    if missing:
        errors.append(f"{name}: missing columns {missing}")
        validation_rows.append({"check": name, "status": "FAIL", "detail": f"missing columns: {missing}"})
        return False
    validation_rows.append({"check": name, "status": "PASS", "detail": "required columns present"})
    return True


def as_float(row, key, default=None):
    value = row.get(key, "")
    if is_blank(value):
        return default
    return float(value)


def as_int(row, key, default=None):
    value = row.get(key, "")
    if is_blank(value):
        return default
    return int(float(value))


def render_template(path: Path, **kwargs) -> str:
    return path.read_text(encoding="utf-8").format(**kwargs)


def validate_monotonic_time(rows, time_key, group_key, validation_rows, errors, name):
    grouped = group_rows(rows, group_key)
    for group_id, group in grouped.items():
        values = [as_float(row, time_key, 0.0) for row in group]
        if any(values[idx] > values[idx + 1] + 1e-9 for idx in range(len(values) - 1)):
            errors.append(f"{name}: {group_key}={group_id} has decreasing {time_key}")
            validation_rows.append({"check": f"{name} monotonic {group_id}", "status": "FAIL", "detail": "time decreases"})
        else:
            validation_rows.append({"check": f"{name} monotonic {group_id}", "status": "PASS", "detail": f"{len(values)} samples"})


def choose_text_color(background_hex: str) -> str:
    palette = {
        "#f28e2b": "#ffffff",
        "#4e79a7": "#ffffff",
        "#d62728": "#ffffff",
        "#9467bd": "#ffffff",
        "#2ca02c": "#ffffff",
    }
    return palette.get(background_hex.lower(), "#ffffff")


def validate_p2(data, validation_rows, errors):
    rows = data["p2_assignment"]
    tasks = data["tasks"]
    amrs = data["amrs"]

    require_nonempty(rows, "P2 assignment_result.csv", validation_rows, errors)
    require_columns(
        rows,
        [
            "task_id",
            "amr_id",
            "sequence_order",
            "estimated_finish_time",
            "estimated_start_time",
            "transition_status",
            "loaded_path_status",
            "assignment_method",
        ],
        "P2 assignment_result.csv",
        validation_rows,
        errors,
    )

    task_ids = [str(row["task_id"]) for row in rows]
    expected_task_ids = {str(row["task_id"]) for row in tasks}
    if len(task_ids) != len(set(task_ids)):
        errors.append("P2 assignment_result.csv: duplicated task_id detected")
        validation_rows.append({"check": "P2 unique tasks", "status": "FAIL", "detail": "duplicated task_id"})
    elif set(task_ids) != expected_task_ids:
        errors.append("P2 assignment_result.csv: task set does not match raw tasks.csv")
        validation_rows.append(
            {
                "check": "P2 task coverage",
                "status": "FAIL",
                "detail": f"expected {len(expected_task_ids)} tasks, got {len(set(task_ids))}",
            }
        )
    else:
        validation_rows.append({"check": "P2 task coverage", "status": "PASS", "detail": f"{len(task_ids)} static tasks"})

    method_set = {row.get("assignment_method", "") for row in rows}
    if method_set != {EXPECTED_P2_METHOD}:
        errors.append(f"P2 assignment method mismatch: {sorted(method_set)}")
        validation_rows.append({"check": "P2 assignment_method", "status": "FAIL", "detail": str(sorted(method_set))})
    else:
        validation_rows.append({"check": "P2 assignment_method", "status": "PASS", "detail": EXPECTED_P2_METHOD})

    amr_map = {str(row["amr_id"]): row for row in amrs}
    grouped = group_rows(rows, "amr_id")
    sequence_count = 0
    finish_times = []
    for amr_id, group in grouped.items():
        group = sort_rows(group, "sequence_order")
        orders = [as_int(row, "sequence_order") for row in group]
        sequence_count += len(group)
        if orders != list(range(1, len(group) + 1)):
            errors.append(f"P2 amr {amr_id}: sequence_order is not a strict 1..n chain")
            validation_rows.append({"check": f"P2 sequence {amr_id}", "status": "FAIL", "detail": str(orders)})
        else:
            validation_rows.append({"check": f"P2 sequence {amr_id}", "status": "PASS", "detail": f"{len(group)} tasks"})

        predecessor_chain = [row.get("predecessor_task_id", "") for row in group]
        if predecessor_chain and predecessor_chain[0] not in ("", None):
            errors.append(f"P2 amr {amr_id}: first task has unexpected predecessor_task_id")
            validation_rows.append({"check": f"P2 predecessor {amr_id}", "status": "FAIL", "detail": predecessor_chain[0]})
        else:
            ok = True
            for idx in range(1, len(group)):
                prev_task = str(group[idx - 1]["task_id"])
                if str(group[idx].get("predecessor_task_id", "")) != prev_task:
                    ok = False
                    break
            if not ok:
                errors.append(f"P2 amr {amr_id}: predecessor_task_id chain mismatch")
                validation_rows.append({"check": f"P2 predecessor {amr_id}", "status": "FAIL", "detail": "chain mismatch"})
            else:
                validation_rows.append({"check": f"P2 predecessor {amr_id}", "status": "PASS", "detail": "chain ok"})

        first_node = str(group[0].get("start_node", ""))
        expected_node = str(amr_map.get(str(amr_id), {}).get("init_node", ""))
        if expected_node and first_node != expected_node:
            errors.append(f"P2 amr {amr_id}: first start_node {first_node} != init_node {expected_node}")
            validation_rows.append({"check": f"P2 init node {amr_id}", "status": "FAIL", "detail": f"{first_node} != {expected_node}"})
        else:
            validation_rows.append({"check": f"P2 init node {amr_id}", "status": "PASS", "detail": first_node or "n/a"})

        for row in group:
            if row.get("transition_status") != "ok" or row.get("loaded_path_status") != "ok":
                errors.append(f"P2 task {row.get('task_id')}: path status is not ok")
                validation_rows.append(
                    {
                        "check": f"P2 path status {row.get('task_id')}",
                        "status": "FAIL",
                        "detail": f"{row.get('transition_status')} / {row.get('loaded_path_status')}",
                    }
                )
            if is_blank(row.get("estimated_start_time")) or is_blank(row.get("estimated_finish_time")):
                errors.append(f"P2 task {row.get('task_id')}: estimated times missing")
                validation_rows.append({"check": f"P2 times {row.get('task_id')}", "status": "FAIL", "detail": "missing estimated times"})
            finish_times.append(as_float(row, "estimated_finish_time", 0.0))

    p2_cmax = max(finish_times) if finish_times else 0.0
    validation_rows.append({"check": "P2 Cmax", "status": "PASS", "detail": f"{p2_cmax:.6f}"})
    return {
        "task_count": len(rows),
        "amr_count": len(grouped),
        "cmax": p2_cmax,
        "sequence_count": sequence_count,
    }


def validate_p3(data, validation_rows, errors):
    rows = data["p3_schedule"]
    repair = data["p3_repair_summary"]
    conflict_log = data["p3_conflict_log"]

    require_nonempty(rows, "P3 schedule_result.csv", validation_rows, errors)
    require_columns(
        rows,
        [
            "task_id",
            "amr_id",
            "sequence_order",
            "estimated_start_time",
            "estimated_finish_time",
            "estimated_delay",
            "transition_status",
            "loaded_path_status",
            "assignment_method",
        ],
        "P3 schedule_result.csv",
        validation_rows,
        errors,
    )
    require_nonempty(repair, "P3 repair_summary.csv", validation_rows, errors)
    require_nonempty(conflict_log, "P3 conflict_log.csv", validation_rows, errors)

    task_ids = [str(row["task_id"]) for row in rows]
    if len(task_ids) != len(set(task_ids)):
        errors.append("P3 schedule_result.csv: duplicated task_id detected")
        validation_rows.append({"check": "P3 unique tasks", "status": "FAIL", "detail": "duplicated task_id"})
    else:
        validation_rows.append({"check": "P3 unique tasks", "status": "PASS", "detail": f"{len(task_ids)} tasks"})

    method_set = {row.get("assignment_method", "") for row in rows}
    if method_set != {EXPECTED_P3_METHOD}:
        errors.append(f"P3 assignment method mismatch: {sorted(method_set)}")
        validation_rows.append({"check": "P3 assignment_method", "status": "FAIL", "detail": str(sorted(method_set))})
    else:
        validation_rows.append({"check": "P3 assignment_method", "status": "PASS", "detail": EXPECTED_P3_METHOD})

    grouped = group_rows(rows, "amr_id")
    finish_times = []
    total_delay = 0.0
    late_count = 0
    priority_late_count = 0.0
    missing_transition = 0
    missing_loaded = 0
    for amr_id, group in grouped.items():
        group = sort_rows(group, "sequence_order")
        orders = [as_int(row, "sequence_order") for row in group]
        if orders != list(range(1, len(group) + 1)):
            errors.append(f"P3 amr {amr_id}: sequence_order is not a strict 1..n chain")
            validation_rows.append({"check": f"P3 sequence {amr_id}", "status": "FAIL", "detail": str(orders)})
        else:
            validation_rows.append({"check": f"P3 sequence {amr_id}", "status": "PASS", "detail": f"{len(group)} tasks"})

        for idx, row in enumerate(group):
            if idx > 0 and str(row.get("predecessor_task_id", "")) != str(group[idx - 1]["task_id"]):
                errors.append(f"P3 amr {amr_id}: predecessor_task_id chain mismatch")
                validation_rows.append({"check": f"P3 predecessor {amr_id}", "status": "FAIL", "detail": "chain mismatch"})
                break
        else:
            validation_rows.append({"check": f"P3 predecessor {amr_id}", "status": "PASS", "detail": "chain ok"})

        for row in group:
            if row.get("transition_status") != "ok":
                missing_transition += 1
            if row.get("loaded_path_status") != "ok":
                missing_loaded += 1
            finish = as_float(row, "estimated_finish_time", 0.0)
            delay = as_float(row, "estimated_delay", 0.0)
            priority = as_float(row, "priority", 0.0)
            finish_times.append(finish)
            total_delay += delay
            if delay > 1e-9:
                late_count += 1
                priority_late_count += priority

    p3_cmax = max(finish_times) if finish_times else 0.0
    validation_rows.append({"check": "P3 Cmax", "status": "PASS", "detail": f"{p3_cmax:.6f}"})

    selected = [row for row in repair if str(row.get("selected", "")) == "1"]
    if len(selected) != 1:
        errors.append("P3 repair_summary.csv: selected row count is not 1")
        validation_rows.append({"check": "P3 selected row", "status": "FAIL", "detail": f"{len(selected)} selected rows"})
        selected_row = selected[0] if selected else repair[0]
    else:
        selected_row = selected[0]
        validation_rows.append({"check": "P3 selected row", "status": "PASS", "detail": selected_row.get("mode", "")})

    selected_mode = str(selected_row.get("mode", ""))
    if selected_mode != EXPECTED_P3_SELECTED_MODE:
        errors.append(f"P3 selected mode mismatch: {selected_mode}")
        validation_rows.append({"check": "P3 selected mode", "status": "FAIL", "detail": selected_mode})
    else:
        validation_rows.append({"check": "P3 selected mode", "status": "PASS", "detail": selected_mode})

    remaining_conflicts = as_int(selected_row, "remaining_conflicts", 999)
    if remaining_conflicts != 0:
        errors.append(f"P3 remaining conflicts must be 0, got {remaining_conflicts}")
        validation_rows.append({"check": "P3 remaining_conflicts", "status": "FAIL", "detail": str(remaining_conflicts)})
    else:
        validation_rows.append({"check": "P3 remaining_conflicts", "status": "PASS", "detail": "0"})

    if missing_transition or missing_loaded:
        errors.append(f"P3 missing path counts are nonzero: {missing_transition}, {missing_loaded}")
        validation_rows.append(
            {
                "check": "P3 missing path counts",
                "status": "FAIL",
                "detail": f"transition={missing_transition}, loaded={missing_loaded}",
            }
        )
    else:
        validation_rows.append({"check": "P3 missing path counts", "status": "PASS", "detail": "0 / 0"})

    validate_monotonic_time(data["p3_trajectory"], "absolute_time", "amr_id", validation_rows, errors, "P3 trajectory_schedule.csv")

    return {
        "task_count": len(rows),
        "amr_count": len(grouped),
        "cmax": p3_cmax,
        "total_delay": total_delay,
        "late_count": late_count,
        "priority_late_count": priority_late_count,
        "remaining_conflicts": remaining_conflicts,
    }


def validate_p4(data, validation_rows, errors):
    rows = data["p4_reschedule"]
    summary = data["p4_summary"]
    impact = data["p4_event_impact"]
    method_comp = data["p4_method_comparison"]
    weight_sens = data["p4_weight_sensitivity"]
    dynamic_events = data["dynamic_events"]

    require_nonempty(rows, "P4 reschedule_result.csv", validation_rows, errors)
    require_columns(
        rows,
        [
            "task_id",
            "old_amr",
            "new_amr",
            "new_start",
            "new_finish",
            "changed",
            "reschedule_status",
            "reschedule_method",
        ],
        "P4 reschedule_result.csv",
        validation_rows,
        errors,
    )
    require_nonempty(summary, "P4 reschedule_summary.csv", validation_rows, errors)
    require_nonempty(impact, "P4 dynamic_event_impact.csv", validation_rows, errors)
    require_nonempty(method_comp, "P4 p4_method_comparison.csv", validation_rows, errors)
    require_nonempty(weight_sens, "P4 p4_weight_sensitivity.csv", validation_rows, errors)

    task_ids = [str(row["task_id"]) for row in rows]
    if len(task_ids) != len(set(task_ids)):
        errors.append("P4 reschedule_result.csv: duplicated task_id detected")
        validation_rows.append({"check": "P4 unique tasks", "status": "FAIL", "detail": "duplicated task_id"})
    else:
        validation_rows.append({"check": "P4 unique tasks", "status": "PASS", "detail": f"{len(task_ids)} tasks"})

    method_set = {row.get("reschedule_method", "") for row in rows}
    if method_set != {EXPECTED_P4_METHOD}:
        errors.append(f"P4 reschedule method mismatch: {sorted(method_set)}")
        validation_rows.append({"check": "P4 reschedule_method", "status": "FAIL", "detail": str(sorted(method_set))})
    else:
        validation_rows.append({"check": "P4 reschedule_method", "status": "PASS", "detail": EXPECTED_P4_METHOD})

    if any(row.get("reschedule_status") != "scheduled" for row in rows):
        errors.append("P4 reschedule_result.csv: not all tasks are scheduled")
        validation_rows.append({"check": "P4 reschedule_status", "status": "FAIL", "detail": "contains non-scheduled rows"})
    else:
        validation_rows.append({"check": "P4 reschedule_status", "status": "PASS", "detail": "all scheduled"})

    new_finish = [as_float(row, "new_finish", 0.0) for row in rows]
    p4_cmax = max(new_finish) if new_finish else 0.0
    p4_total_delay = sum(max(0.0, as_float(row, "delay_added", 0.0)) for row in rows)
    changed_tasks = sum(1 for row in rows if as_int(row, "changed", 0) > 0)
    new_tasks = sum(1 for row in rows if is_blank(row.get("old_amr", "")))

    summary_map = {str(row["metric"]): row["value"] for row in summary}
    method_row = None
    for row in method_comp:
        if str(row.get("method_id", "")) == EXPECTED_P4_STRATEGY:
            method_row = row
            break
    if method_row is None:
        errors.append("P4 p4_method_comparison.csv: rolling_horizon row not found")
        validation_rows.append({"check": "P4 method comparison row", "status": "FAIL", "detail": "missing rolling_horizon"})
    else:
        validation_rows.append({"check": "P4 method comparison row", "status": "PASS", "detail": method_row.get("method", "")})

    hard_violation_count = int(float(summary_map.get("unresolved_trajectory_conflict_count", 999))) + int(float(summary_map.get("blocked_area_violation_count", 999))) + int(float(summary_map.get("delayed_amr_violation_count", 999)))
    conflict_success = as_float(method_row, "conflict_resolution_success_rate", 0.0) if method_row else 0.0

    if hard_violation_count != 0:
        errors.append(f"P4 hard violation count must be 0, got {hard_violation_count}")
        validation_rows.append({"check": "P4 hard violations", "status": "FAIL", "detail": str(hard_violation_count)})
    else:
        validation_rows.append({"check": "P4 hard violations", "status": "PASS", "detail": "0"})

    if conflict_success != 1.0:
        errors.append(f"P4 conflict resolution success rate must be 1.0, got {conflict_success}")
        validation_rows.append({"check": "P4 conflict success", "status": "FAIL", "detail": str(conflict_success)})
    else:
        validation_rows.append({"check": "P4 conflict success", "status": "PASS", "detail": "1.0"})

    all_dynamic = int(float(summary_map.get("all_dynamic_tasks_scheduled", 0)))
    if all_dynamic != 1:
        errors.append("P4 all_dynamic_tasks_scheduled must be 1")
        validation_rows.append({"check": "P4 all dynamic tasks", "status": "FAIL", "detail": str(all_dynamic)})
    else:
        validation_rows.append({"check": "P4 all dynamic tasks", "status": "PASS", "detail": "1"})

    if len(impact) != len(dynamic_events):
        errors.append("P4 dynamic_event_impact.csv row count does not match raw dynamic_events.csv")
        validation_rows.append({"check": "P4 event impact rows", "status": "FAIL", "detail": f"{len(impact)} vs {len(dynamic_events)}"})
    else:
        validation_rows.append({"check": "P4 event impact rows", "status": "PASS", "detail": str(len(impact))})

    if summary_map.get("Cmax") is not None and abs(float(summary_map["Cmax"]) - p4_cmax) > 1e-6:
        errors.append("P4 Cmax recomputation mismatch")
        validation_rows.append({"check": "P4 Cmax", "status": "FAIL", "detail": f"summary={summary_map['Cmax']} recomputed={p4_cmax:.6f}"})
    else:
        validation_rows.append({"check": "P4 Cmax", "status": "PASS", "detail": f"{p4_cmax:.6f}"})

    summary_total_delay = float(summary_map.get("total_delay", 0.0))
    validation_rows.append({"check": "P4 total_delay", "status": "PASS", "detail": f"{summary_total_delay:.6f}"})

    summary_added_delay = float(summary_map.get("TotalAddedDelay", 0.0))
    if summary_map.get("TotalAddedDelay") is not None and abs(summary_added_delay - p4_total_delay) > 1e-6:
        errors.append("P4 TotalAddedDelay mismatch")
        validation_rows.append({"check": "P4 TotalAddedDelay", "status": "FAIL", "detail": f"summary={summary_map['TotalAddedDelay']} recomputed={p4_total_delay:.6f}"})
    else:
        validation_rows.append({"check": "P4 TotalAddedDelay", "status": "PASS", "detail": f"{p4_total_delay:.6f}"})

    if summary_map.get("ChangedTasks") is not None and int(float(summary_map["ChangedTasks"])) != changed_tasks:
        errors.append("P4 changed task count mismatch")
        validation_rows.append({"check": "P4 changed tasks", "status": "FAIL", "detail": f"summary={summary_map['ChangedTasks']} recomputed={changed_tasks}"})
    else:
        validation_rows.append({"check": "P4 changed tasks", "status": "PASS", "detail": str(changed_tasks)})

    if summary_map.get("NewTasks") is not None and int(float(summary_map["NewTasks"])) != new_tasks:
        errors.append("P4 new task count mismatch")
        validation_rows.append({"check": "P4 new tasks", "status": "FAIL", "detail": f"summary={summary_map['NewTasks']} recomputed={new_tasks}"})
    else:
        validation_rows.append({"check": "P4 new tasks", "status": "PASS", "detail": str(new_tasks)})

    if method_row is not None and str(method_row.get("method_id", "")) != EXPECTED_P4_STRATEGY:
        errors.append(f"P4 method_id mismatch: {method_row.get('method_id')}")
        validation_rows.append({"check": "P4 strategy id", "status": "FAIL", "detail": str(method_row.get("method_id", ""))})
    elif method_row is not None:
        validation_rows.append({"check": "P4 strategy id", "status": "PASS", "detail": EXPECTED_P4_STRATEGY})

    validate_monotonic_time(data["p4_trajectory"], "absolute_time", "amr_id", validation_rows, errors, "P4 trajectory_schedule.csv")

    return {
        "task_count": len(rows),
        "cmax": p4_cmax,
        "total_delay": p4_total_delay,
        "changed_tasks": changed_tasks,
        "new_tasks": new_tasks,
        "hard_violation_count": hard_violation_count,
        "conflict_success": conflict_success,
        "reschedule_time": as_float(method_row, "reschedule_time", 0.0) if method_row else 0.0,
        "summary": summary_map,
        "method_row": method_row or {},
    }


def build_heatmap_and_floor_data(data):
    points = data["p3_trajectory"]
    zones = data["floor_zones"]
    obstacles = data["floor_obstacles"]

    numeric_points = [row for row in points if not is_blank(row.get("x")) and not is_blank(row.get("y"))]
    if not numeric_points:
        raise RuntimeError("P3 trajectory_schedule.csv has no usable x/y points")

    all_x = [to_float(row["x"]) for row in numeric_points]
    all_y = [to_float(row["y"]) for row in numeric_points]
    for zone in zones:
        all_x.extend([to_float(zone["x"]), to_float(zone["x"]) + to_float(zone["width"])])
        all_y.extend([to_float(zone["y"]), to_float(zone["y"]) + to_float(zone["height"])])
    for obstacle in obstacles:
        all_x.extend([to_float(obstacle["x"]), to_float(obstacle["x"]) + to_float(obstacle["width"])])
        all_y.extend([to_float(obstacle["y"]), to_float(obstacle["y"]) + to_float(obstacle["height"])])

    min_x = math.floor(min(all_x) / CELL_SIZE) * CELL_SIZE - CELL_SIZE
    max_x = math.ceil(max(all_x) / CELL_SIZE) * CELL_SIZE + CELL_SIZE
    min_y = math.floor(min(all_y) / CELL_SIZE) * CELL_SIZE - CELL_SIZE
    max_y = math.ceil(max(all_y) / CELL_SIZE) * CELL_SIZE + CELL_SIZE
    hist, x_edges, y_edges = build_occupancy_histogram(numeric_points, CELL_SIZE, (min_x, max_x, min_y, max_y))
    hotspots = []
    return hist, x_edges, y_edges, hotspots, zones, obstacles


def save_heatmap_cells(hist, x_edges, y_edges, path: Path):
    rows = []
    for ix in range(hist.shape[0]):
        for iy in range(hist.shape[1]):
            count = int(hist[ix, iy])
            if count <= 0:
                continue
            rows.append(
                {
                    "cell_x": round(float(x_edges[ix]), 3),
                    "cell_y": round(float(y_edges[iy]), 3),
                    "count": count,
                }
            )
    write_csv_rows(path, rows, ["cell_x", "cell_y", "count"])
    return rows


def save_gantt_rows(rows, path: Path):
    out_rows = []
    for row in rows:
        out_rows.append(
            {
                "task_id": row.get("task_id", ""),
                "old_amr": row.get("old_amr", ""),
                "new_amr": row.get("new_amr", ""),
                "old_start": row.get("old_start", ""),
                "new_start": row.get("new_start", ""),
                "old_finish": row.get("old_finish", ""),
                "new_finish": row.get("new_finish", ""),
                "changed": row.get("changed", ""),
                "sequence_order": row.get("sequence_order", ""),
                "affected_event_ids": row.get("affected_event_ids", ""),
            }
        )
    write_csv_rows(path, out_rows, list(out_rows[0].keys()) if out_rows else [])


def plot_dashboard(data, p3_metrics, p4_metrics, hist, x_edges, y_edges, hotspots, output_png, output_pdf):
    configure_chinese_style()
    reschedule_rows = sort_rows(data["p4_reschedule"], "new_amr", "new_start", "task_id")
    dynamic_events = data["dynamic_events"]
    zones = data["floor_zones"]
    obstacles = data["floor_obstacles"]

    amr_order = sorted({str(row["new_amr"]) for row in reschedule_rows}, key=natural_amr_order)
    amr_to_y = {amr: idx for idx, amr in enumerate(amr_order)}
    amr_display = {amr: f"机器人{idx + 1}（{amr}）" for idx, amr in enumerate(amr_order)}

    fig = plt.figure(figsize=(15.5, 7.6), constrained_layout=True)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.0])
    ax_gantt = fig.add_subplot(gs[0, 0])
    ax_map = fig.add_subplot(gs[0, 1])

    # 左图：P4 最终调度甘特图，强调变更任务和事件窗口。
    for row in reschedule_rows:
        amr = str(row.get("new_amr", ""))
        y = amr_to_y.get(amr, 0)
        start = to_float(row.get("new_start"))
        finish = to_float(row.get("new_finish"))
        if start is None or finish is None:
            continue
        duration = max(0.0, finish - start)
        changed = as_int(row, "changed", 0) > 0
        color = "#d95f02" if changed else "#4c78a8"
        edge = "#8d4d02" if changed else "#345b8c"
        ax_gantt.barh(y, duration, left=start, height=0.46, color=color, edgecolor=edge, linewidth=0.8, alpha=0.95, zorder=3 if changed else 2)
        if duration >= 2.6:
            ax_gantt.text(
                start + duration / 2.0,
                y,
                f"任务{row.get('task_id', '')}",
                ha="center",
                va="center",
                fontsize=7,
                color=choose_text_color(color),
                fontweight="bold",
                zorder=4,
            )

    # 动态事件窗口：只保留能解释调度扰动的时间窗。
    legend_items = {}
    for event in dynamic_events:
        event_type = str(event.get("event_type", "")).strip()
        if event_type == "area_block":
            start = to_float(event.get("start_time"))
            end = to_float(event.get("end_time"))
            if start is None or end is None:
                continue
            ax_gantt.axvspan(start, end, color="#d62728", alpha=0.10, label="区域封锁" if "area_block" not in legend_items else None, zorder=1)
            ax_gantt.text((start + end) / 2.0, len(amr_order) - 0.25, f"封锁 {event.get('event_id', '')}", ha="center", va="bottom", fontsize=7, color="#7a1b1b", fontweight="bold")
            legend_items["area_block"] = True
        elif event_type == "amr_delay":
            start = to_float(event.get("start_time"))
            end = to_float(event.get("end_time"))
            if start is None or end is None:
                continue
            ax_gantt.axvspan(start, end, color="#9467bd", alpha=0.10, label="AMR延误" if "amr_delay" not in legend_items else None, zorder=1)
            ax_gantt.text((start + end) / 2.0, len(amr_order) - 0.55, f"延误 {event.get('event_id', '')}", ha="center", va="bottom", fontsize=7, color="#5f3b8c", fontweight="bold")
            legend_items["amr_delay"] = True
        elif event_type == "new_task":
            release_time = to_float(event.get("release_time"))
            if release_time is None:
                continue
            ax_gantt.axvline(release_time, color="#2ca02c", linestyle="--", linewidth=1.2, label="新任务释放" if "new_task" not in legend_items else None, zorder=1)
            ax_gantt.text(release_time, len(amr_order) - 0.85, f"新任务 {event.get('event_id', '')}", ha="center", va="bottom", fontsize=7, color="#1f6a1f", fontweight="bold")
            legend_items["new_task"] = True

    ax_gantt.set_yticks(range(len(amr_order)))
    ax_gantt.set_yticklabels([amr_display[amr] for amr in amr_order])
    ax_gantt.set_xlabel("时间（秒）")
    ax_gantt.set_ylabel("机器人")
    ax_gantt.set_title("P4 基线调度")
    ax_gantt.grid(axis="x", linestyle=":", alpha=0.18, zorder=0)
    ax_gantt.spines["top"].set_visible(False)
    ax_gantt.spines["right"].set_visible(False)
    if legend_items:
        ax_gantt.legend(loc="lower right", frameon=True, fontsize=7.5, title="事件", title_fontsize=8.5)
    ax_gantt.set_ylim(-0.62, max(len(amr_order) - 0.38, 0.5))

    # 右图：P3 轨迹占用热图，用 1m 网格刻画拥堵热点。
    draw_floor_context(ax_map, zones, obstacles)
    mesh = ax_map.pcolormesh(
        x_edges,
        y_edges,
        hist.T,
        cmap="YlOrRd",
        shading="auto",
        alpha=0.68,
        zorder=3,
    )
    hotspots = add_top_hotspot_labels(ax_map, hist, x_edges, y_edges, top_k=5)
    ax_map.set_xlabel("x 方向（米）")
    ax_map.set_ylabel("y 方向（米）")
    ax_map.set_title("P3 轨迹占用热图（1 米网格）")
    cbar = fig.colorbar(mesh, ax=ax_map, fraction=0.046, pad=0.04)
    cbar.set_label("占用次数 / 网格")

    # 把热点编号和计数写回图外，方便报告直接引用。
    if hotspots:
        hotspot_text = "  ".join([f"热点{h['rank']}={h['count']}" for h in hotspots])
        ax_map.text(0.01, 0.01, hotspot_text, transform=ax_map.transAxes, fontsize=7.5, va="bottom", ha="left", color="#1f1f1f", bbox=dict(boxstyle="round,pad=0.20", facecolor="white", edgecolor="#c0c0c0", alpha=0.88))

    ax_gantt.tick_params(axis="y", pad=4)
    for axis in (ax_gantt, ax_map):
        axis.tick_params(labelsize=8)

    fig.suptitle("P5 阶段1", fontsize=15, fontweight="bold")

    output_png.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=300, bbox_inches="tight")
    fig.savefig(output_pdf, bbox_inches="tight")
    plt.close(fig)
    return hotspots

def main():
    args = parse_args()
    data = read_inputs()
    validation_rows = []
    errors = []

    p2 = validate_p2(data, validation_rows, errors)
    p3 = validate_p3(data, validation_rows, errors)
    p4 = validate_p4(data, validation_rows, errors)

    hist, x_edges, y_edges, _, zones, obstacles = build_heatmap_and_floor_data(data)
    save_heatmap_cells(hist, x_edges, y_edges, P5_DIR / "stage1_heatmap_cells.csv")
    hotspots = []
    output_png = OUT_DIR / "stage1_baseline_dashboard.png"
    output_pdf = OUT_DIR / "stage1_baseline_dashboard.pdf"
    hotspots = plot_dashboard(data, p3, p4, hist, x_edges, y_edges, hotspots, output_png, output_pdf)

    metrics = [
        {"metric": "p2_task_count", "value": p2["task_count"]},
        {"metric": "p2_amr_count", "value": p2["amr_count"]},
        {"metric": "p2_cmax", "value": round(p2["cmax"], 6)},
        {"metric": "p3_task_count", "value": p3["task_count"]},
        {"metric": "p3_amr_count", "value": p3["amr_count"]},
        {"metric": "p3_cmax", "value": round(p3["cmax"], 6)},
        {"metric": "p3_total_delay", "value": round(p3["total_delay"], 6)},
        {"metric": "p3_late_count", "value": p3["late_count"]},
        {"metric": "p3_priority_late_count", "value": round(p3["priority_late_count"], 6)},
        {"metric": "p3_remaining_conflicts", "value": p3["remaining_conflicts"]},
        {"metric": "p4_task_count", "value": p4["task_count"]},
        {"metric": "p4_cmax", "value": round(p4["cmax"], 6)},
        {"metric": "p4_total_delay_added", "value": round(p4["total_delay"], 6)},
        {"metric": "p4_changed_tasks", "value": p4["changed_tasks"]},
        {"metric": "p4_new_tasks", "value": p4["new_tasks"]},
        {"metric": "p4_hard_violation_count", "value": p4["hard_violation_count"]},
        {"metric": "p4_conflict_success_rate", "value": round(p4["conflict_success"], 6)},
        {"metric": "p4_reschedule_time", "value": round(p4["reschedule_time"], 6)},
    ]

    validation_status = "PASS" if not errors else "FAIL"
    report = {
        "status": validation_status,
        "errors": errors,
        "p2": p2,
        "p3": p3,
        "p4": p4,
        "hotspots": hotspots,
        "metrics": metrics,
    }

    P5_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv_rows(P5_DIR / "stage1_metrics.csv", metrics, ["metric", "value"])
    write_csv_rows(P5_DIR / "stage1_validation_log.csv", validation_rows, ["check", "status", "detail"])
    write_csv_rows(
        P5_DIR / "stage1_hotspots.csv",
        hotspots,
        ["rank", "cell_x", "cell_y", "count"],
    )
    with (P5_DIR / "stage1_validation_report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    TASK_STAGE_DIR.mkdir(parents=True, exist_ok=True)
    hotspot_lines = "\n".join([f"- 热点{h['rank']}：网格({h['cell_x']:.1f}, {h['cell_y']:.1f})，占用 {h['count']} 次" for h in hotspots]) or "- 无"
    conclusion_text = render_template(
        TASK_STAGE_DIR / "conclusion_template.md",
        p2_task_count=p2["task_count"],
        p2_amr_count=p2["amr_count"],
        p2_cmax=f"{p2['cmax']:.6f}",
        p3_cmax=f"{p3['cmax']:.6f}",
        p3_total_delay=f"{p3['total_delay']:.6f}",
        p3_remaining_conflicts=p3["remaining_conflicts"],
        p4_cmax=f"{p4['cmax']:.6f}",
        p4_total_delay=f"{float(p4['summary'].get('total_delay', 0.0)):.6f}",
        p4_total_added_delay=f"{float(p4['summary'].get('TotalAddedDelay', 0.0)):.6f}",
        p4_changed_tasks=p4["changed_tasks"],
        p4_new_tasks=p4["new_tasks"],
        p4_hard_violation_count=p4["hard_violation_count"],
        p4_conflict_success=f"{p4['conflict_success']:.1f}",
        hotspot_lines=hotspot_lines,
        validation_pass=sum(1 for row in validation_rows if row["status"] == "PASS"),
        validation_fail=sum(1 for row in validation_rows if row["status"] == "FAIL"),
    )
    code_text = render_template(
        TASK_STAGE_DIR / "code_explanation_template.md",
    )
    (TASK_STAGE_DIR / "conclusion.md").write_text(conclusion_text, encoding="utf-8")
    (TASK_STAGE_DIR / "code_explanation.md").write_text(code_text, encoding="utf-8")

    save_gantt_rows(sort_rows(data["p4_reschedule"], "new_amr", "new_start", "task_id"), P5_DIR / "stage1_gantt_rows.csv")

    if errors:
        print("Stage 1 validation failed:")
        for error in errors:
            print(f"- {error}")
        print(f"Validation log: {P5_DIR / 'stage1_validation_log.csv'}")
        print(f"Report: {P5_DIR / 'stage1_validation_report.json'}")
        return 1

    print("Stage 1 validation passed.")
    print(f"Dashboard: {output_png}")
    print(f"Metrics: {P5_DIR / 'stage1_metrics.csv'}")
    print(f"Conclusion md: {TASK_STAGE_DIR / 'conclusion.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
