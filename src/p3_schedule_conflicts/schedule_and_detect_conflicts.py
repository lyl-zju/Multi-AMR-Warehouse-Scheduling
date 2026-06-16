import argparse
from math import hypot
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
P2_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "p2"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "p3"
P1_ALGORITHMS = ("basic_astar", "vg", "avg", "davg", "mixed")
DEFAULT_P1_ALGORITHM = "mixed"

CONFLICT_METHOD = "spacetime_footprint_check"
TIME_TOLERANCE = 0.25
SAFETY_MARGIN = 0.10
TRAJECTORY_SAMPLE_STEP = 0.10
NODE_CAPACITY_TYPES = ("P", "SORT", "OUT")
WAIT_OPTIONS = (1.0, 2.0, 5.0, 8.0, 12.0, 20.0)
MID_WAIT_LEAD_TIMES = (0.8, 1.0, 1.5, 2.0, 3.0, 4.0)
WAIT_COMPRESSION_STEP = 0.5
WAIT_COMPRESSION_MAX_PASSES = 1
MAX_REPAIR_ITERATIONS = 40
LOADED_SPEED_FACTOR = 0.90
EMPTY_ENERGY_RATE = 1.00
LOADED_ENERGY_RATE = 1.20
SERVICE_ENERGY_RATE = 0.20

OBJ_W_DELAY = 100.0
OBJ_W_CMAX = 5.0
OBJ_W_WAIT = 1.0
OBJ_W_REROUTE = 2.0
OBJ_W_RESEQUENCE = 20.0
OBJ_W_INITIAL_WAIT = 20.0

TRAJECTORY_SCHEDULE_COLUMNS = [
    "amr_id", "task_id", "sequence_order", "segment_type", "path_uid", "sample_index",
    "absolute_time", "offset_time", "x", "y", "theta", "speed", "footprint_radius",
]

CONFLICT_COLUMNS = [
    "conflict_id", "time", "location", "location_type", "involved_amrs", "involved_tasks",
    "involved_path_uids", "distance", "clearance_threshold", "time_delta", "repair_action",
    "status", "conflict_method",
]

PROCESS_LOG_COLUMNS = [
    "mode", "iteration", "conflict_id", "conflict_time", "conflict_type", "before_conflicts",
    "action", "target_amr", "target_task", "target_sequence_order", "segment_type",
    "old_path_uid", "new_path_uid", "wait_added", "after_conflicts", "objective",
]


class RepairState:
    def __init__(self, assignment, repair_waits=None, reroutes=None, mid_path_waits=None, resequence_moves=None):
        self.assignment = assignment.copy()
        self.repair_waits = repair_waits or initialize_repair_waits(assignment)
        self.reroutes = reroutes or {}
        self.mid_path_waits = mid_path_waits or {}
        self.resequence_moves = resequence_moves or []

    def copy(self):
        return RepairState(
            self.assignment.copy(),
            dict(self.repair_waits),
            dict(self.reroutes),
            dict(self.mid_path_waits),
            list(self.resequence_moves),
        )


def p1_data_dir(algorithm):
    return PROJECT_ROOT / "data" / "processed" / "p1" / algorithm


def parse_args():
    parser = argparse.ArgumentParser(description="P3 schedule conflict detection and repair.")
    parser.add_argument("--p1-algorithm", choices=P1_ALGORITHMS, default=DEFAULT_P1_ALGORITHM)
    return parser.parse_args()


def load_inputs(p1_algorithm=DEFAULT_P1_ALGORITHM):
    p1_dir = p1_data_dir(p1_algorithm)
    assignment = pd.read_csv(P2_DATA_DIR / "assignment_result.csv")
    trajectory_samples = pd.read_csv(p1_dir / "path_trajectory_samples.csv")
    path_cost = pd.read_csv(p1_dir / "path_cost.csv")
    return assignment, trajectory_samples, path_cost

def best_path_record(path_cost, from_node, to_node, preferred_uid=None):
    candidates = path_cost[
        (path_cost["from_node"].astype(str) == str(from_node))
        & (path_cost["to_node"].astype(str) == str(to_node))
        & (path_cost["planning_status"].astype(str) == "ok")
    ]
    if candidates.empty:
        return None
    if is_present(preferred_uid):
        preferred = candidates[candidates["path_uid"].astype(str) == str(preferred_uid)]
        if not preferred.empty:
            return preferred.iloc[0].to_dict()
    return candidates.sort_values(["rank_by_total", "total_cost", "travel_time"]).iloc[0].to_dict()


def apply_resequence_to_assignment(assignment, path_cost=None):
    updated_groups = []
    for amr_id, group in assignment.groupby("amr_id", sort=True):
        group = group.sort_values("sequence_order").copy().reset_index(drop=True)
        for idx in group.index:
            order = int(idx) + 1
            group.at[idx, "sequence_order"] = order
            group.at[idx, "predecessor_task_id"] = "" if order == 1 else group.at[idx - 1, "task_id"]
            if order > 1:
                group.at[idx, "start_node"] = group.at[idx - 1, "delivery"]
            if path_cost is not None:
                path = best_path_record(path_cost, group.at[idx, "start_node"], group.at[idx, "pickup"], group.at[idx, "transition_path_uid"])
                if path is None:
                    group.at[idx, "transition_status"] = "missing_path"
                    group.at[idx, "transition_path_uid"] = ""
                    group.at[idx, "transition_path_id"] = ""
                    group.at[idx, "transition_travel_time"] = None
                    group.at[idx, "transition_distance"] = None
                    group.at[idx, "transition_cost"] = None
                else:
                    group.at[idx, "transition_status"] = "ok"
                    group.at[idx, "transition_path_uid"] = path["path_uid"]
                    group.at[idx, "transition_path_id"] = path["path_id"]
                    group.at[idx, "transition_travel_time"] = round(float(path["travel_time"]), 6)
                    group.at[idx, "transition_distance"] = float(path["distance"])
                    group.at[idx, "transition_cost"] = float(path["total_cost"])
        updated_groups.append(group)
    return pd.concat(updated_groups, ignore_index=True)


def swap_adjacent_tasks(assignment, amr_id, first_sequence_order, path_cost):
    updated = assignment.copy()
    mask = updated["amr_id"].astype(str) == str(amr_id)
    group = updated[mask].sort_values("sequence_order")
    positions = list(group.index)
    pos = None
    for i, idx in enumerate(positions[:-1]):
        if int(updated.at[idx, "sequence_order"]) == int(first_sequence_order):
            pos = i
            break
    if pos is None:
        return None
    left_idx, right_idx = positions[pos], positions[pos + 1]
    left_row = updated.loc[left_idx].copy()
    right_row = updated.loc[right_idx].copy()
    updated.loc[left_idx] = right_row
    updated.loc[right_idx] = left_row
    return apply_resequence_to_assignment(updated, path_cost)


def is_present(value):
    return value is not None and not pd.isna(value) and str(value) != ""


def number_or_none(value):
    return None if not is_present(value) else float(value)


def task_key(amr_id, task_id, sequence_order):
    return (str(amr_id), str(task_id), int(sequence_order))


def node_requires_capacity(node_id):
    return str(node_id).startswith(NODE_CAPACITY_TYPES)


def stationary_path_uid(node_id):
    return f"stationary::{node_id}"


def time_samples(start_time, end_time):
    if end_time <= start_time:
        return [float(start_time)]
    samples = list(np.arange(float(start_time), float(end_time), TRAJECTORY_SAMPLE_STEP))
    if not samples or abs(samples[-1] - end_time) > 1e-9:
        samples.append(float(end_time))
    return samples


def stationary_rows(node_id, start_time, end_time, trajectory_samples, context, segment_type):
    if start_time is None or end_time is None or end_time <= start_time + 1e-9:
        return []
    reference = trajectory_samples[
        (trajectory_samples["from_node"].astype(str) == str(node_id))
        | (trajectory_samples["to_node"].astype(str) == str(node_id))
    ]
    if reference.empty:
        return []
    if (reference["from_node"].astype(str) == str(node_id)).any():
        row = reference[reference["from_node"].astype(str) == str(node_id)].iloc[0]
    else:
        row = reference[reference["to_node"].astype(str) == str(node_id)].sort_values("sample_index").iloc[-1]
    return [{
        **context,
        "segment_type": segment_type,
        "path_uid": stationary_path_uid(node_id),
        "sample_index": sample_index,
        "absolute_time": round(float(t), 3),
        "offset_time": round(float(t - start_time), 3),
        "x": float(row.x),
        "y": float(row.y),
        "theta": float(row.theta),
        "speed": 0.0,
        "footprint_radius": float(row.footprint_radius),
    } for sample_index, t in enumerate(time_samples(start_time, end_time))]


def expand_trajectory(path_uid, base_time, trajectory_samples, context, actual_duration=None, mid_wait=None):
    if not is_present(path_uid) or path_uid == "same_node" or base_time is None:
        return [], 0.0
    rows = trajectory_samples[trajectory_samples["path_uid"].astype(str) == str(path_uid)].sort_values("sample_index")
    if rows.empty:
        return [], 0.0
    p1_duration = float(rows["offset_time"].max())
    scale = float(actual_duration) / p1_duration if actual_duration is not None and p1_duration > 1e-9 else 1.0
    source_times = rows["offset_time"].to_numpy(dtype=float) * scale
    x_values = rows["x"].to_numpy(dtype=float)
    y_values = rows["y"].to_numpy(dtype=float)
    theta_values = rows["theta"].to_numpy(dtype=float)
    radius_values = rows["footprint_radius"].to_numpy(dtype=float)
    segment_duration = float(actual_duration) if actual_duration is not None else float(source_times[-1])
    wait_offset = None
    wait_amount = 0.0
    if mid_wait is not None:
        wait_offset = min(max(float(mid_wait.get("offset_time", segment_duration / 2.0)), 0.0), segment_duration)
        wait_amount = max(0.0, float(mid_wait.get("wait_amount", 0.0)))

    expanded = []
    sample_index = 0

    def append_move_sample(move_offset, absolute_offset):
        nonlocal sample_index
        expanded.append({
            **context,
            "path_uid": path_uid,
            "sample_index": sample_index,
            "absolute_time": round(float(base_time + absolute_offset), 3),
            "offset_time": round(float(absolute_offset), 3),
            "x": float(np.interp(move_offset, source_times, x_values)),
            "y": float(np.interp(move_offset, source_times, y_values)),
            "theta": float(np.interp(move_offset, source_times, theta_values)),
            "speed": float(1.0 / scale) if scale > 1e-9 else 0.0,
            "footprint_radius": float(np.interp(move_offset, source_times, radius_values)),
        })
        sample_index += 1

    for move_offset in time_samples(0.0, segment_duration):
        if wait_offset is not None and move_offset > wait_offset:
            break
        append_move_sample(move_offset, move_offset)

    if wait_offset is not None and wait_amount > 0:
        wait_context = {**context, "segment_type": f"mid_path_wait_{context.get('segment_type', '')}"}
        wait_x = float(np.interp(wait_offset, source_times, x_values))
        wait_y = float(np.interp(wait_offset, source_times, y_values))
        wait_theta = float(np.interp(wait_offset, source_times, theta_values))
        wait_radius = float(np.interp(wait_offset, source_times, radius_values))
        for t in time_samples(base_time + wait_offset, base_time + wait_offset + wait_amount):
            expanded.append({
                **wait_context,
                "path_uid": f"midwait::{path_uid}",
                "sample_index": sample_index,
                "absolute_time": round(float(t), 3),
                "offset_time": round(float(t - base_time), 3),
                "x": wait_x,
                "y": wait_y,
                "theta": wait_theta,
                "speed": 0.0,
                "footprint_radius": wait_radius,
            })
            sample_index += 1
        for move_offset in time_samples(wait_offset, segment_duration)[1:]:
            append_move_sample(move_offset, move_offset + wait_amount)
    return expanded, segment_duration + wait_amount

def initialize_repair_waits(assignment):
    return {task_key(row.amr_id, row.task_id, row.sequence_order): 0.0 for row in assignment.itertuples(index=False)}


def add_wait_before_task(repair_waits, amr_id, sequence_order, wait_amount):
    updated = dict(repair_waits)
    for key in list(updated):
        if key[0] == str(amr_id) and key[2] == int(sequence_order):
            updated[key] = updated.get(key, 0.0) + float(wait_amount)
            break
    return updated


def build_schedule(assignment, trajectory_samples, repair_waits=None):
    state = RepairState(assignment, repair_waits=repair_waits)
    return build_schedule_from_state(state, trajectory_samples)


def build_schedule_from_state(state, trajectory_samples):
    schedule_rows = []
    trajectory_rows = []
    assignment = apply_reroutes_to_assignment(state.assignment, state.reroutes)
    assignment = assignment.sort_values(["amr_id", "sequence_order", "task_id"])

    for amr_id, group in assignment.groupby("amr_id", sort=True):
        current_time = 0.0
        battery = infer_initial_battery(group)
        for row in group.itertuples(index=False):
            task_ready_time = current_time
            sequence_order = int(row.sequence_order)
            key = task_key(amr_id, row.task_id, sequence_order)
            repair_wait = float(state.repair_waits.get(key, 0.0))
            transition_ok = row.transition_status == "ok"
            loaded_ok = row.loaded_path_status == "ok"
            transition_time = number_or_none(row.transition_travel_time)
            loaded_time = number_or_none(row.loaded_travel_time)
            service_time = float(row.service_time)
            earliest_start = float(row.earliest_start)
            latest_finish = float(row.latest_finish)
            transition_mid_wait = state.mid_path_waits.get((str(amr_id), str(row.task_id), sequence_order, "transition"))
            loaded_mid_wait = state.mid_path_waits.get((str(amr_id), str(row.task_id), sequence_order, "loaded"))
            transition_mid_wait_amount = float(transition_mid_wait.get("wait_amount", 0.0)) if transition_mid_wait else 0.0
            loaded_mid_wait_amount = float(loaded_mid_wait.get("wait_amount", 0.0)) if loaded_mid_wait else 0.0

            transition_start_time = current_time + repair_wait if transition_ok else None
            transition_actual_duration = transition_time + transition_mid_wait_amount if transition_time is not None else None
            transition_finish_time = transition_start_time + transition_actual_duration if transition_start_time is not None and transition_actual_duration is not None else None
            if transition_ok and loaded_ok:
                arrival_pickup = transition_finish_time
                time_window_wait = max(0.0, earliest_start - arrival_pickup)
                waiting_time = time_window_wait + repair_wait + transition_mid_wait_amount + loaded_mid_wait_amount
                start_time = arrival_pickup + time_window_wait
                loaded_start_time = start_time + service_time
                loaded_actual_duration = loaded_time + loaded_mid_wait_amount
                finish_time = loaded_start_time + loaded_actual_duration
                delay = max(0.0, finish_time - latest_finish)
                current_time = finish_time
                schedule_status = "scheduled"
            else:
                arrival_pickup = waiting_time = start_time = loaded_start_time = finish_time = delay = None
                loaded_actual_duration = loaded_time
                schedule_status = "missing_path"

            energy_used = (
                float(row.transition_distance or 0.0) * EMPTY_ENERGY_RATE
                + float(row.loaded_distance or 0.0) * LOADED_ENERGY_RATE
                + service_time * SERVICE_ENERGY_RATE
            )
            battery = battery - energy_used

            context = {"amr_id": amr_id, "task_id": row.task_id, "sequence_order": sequence_order}
            if transition_ok and repair_wait > 0:
                trajectory_rows.extend(stationary_rows(row.start_node, task_ready_time, transition_start_time, trajectory_samples, context, "repair_wait_at_node"))
            transition_rows, _transition_duration = expand_trajectory(
                row.transition_path_uid,
                transition_start_time,
                trajectory_samples,
                {**context, "segment_type": "transition"},
                transition_time,
                transition_mid_wait,
            )
            trajectory_rows.extend(transition_rows)
            if transition_ok and loaded_ok:
                trajectory_rows.extend(stationary_rows(row.pickup, arrival_pickup, start_time, trajectory_samples, context, "wait_at_pickup"))
                trajectory_rows.extend(stationary_rows(row.pickup, start_time, loaded_start_time, trajectory_samples, context, "service_at_pickup"))
            loaded_rows, _loaded_duration = expand_trajectory(
                row.loaded_path_uid,
                loaded_start_time,
                trajectory_samples,
                {**context, "segment_type": "loaded"},
                loaded_time,
                loaded_mid_wait,
            )
            trajectory_rows.extend(loaded_rows)

            schedule_rows.append({
                "amr_id": amr_id,
                "task_id": row.task_id,
                "sequence_order": sequence_order,
                "pickup": row.pickup,
                "delivery": row.delivery,
                "start_time": start_time,
                "finish_time": finish_time,
                "path_id": row.loaded_path_id,
                "path_uid": row.loaded_path_uid,
                "transition_path_uid": row.transition_path_uid,
                "loaded_path_uid": row.loaded_path_uid,
                "transition_start_time": transition_start_time,
                "transition_finish_time": transition_finish_time,
                "arrival_pickup_time": arrival_pickup,
                "waiting_time": waiting_time,
                "service_time": service_time,
                "loaded_start_time": loaded_start_time,
                "loaded_travel_time": loaded_time,
                "latest_finish": latest_finish,
                "delay": delay,
                "is_delayed": int(delay is not None and delay > 0),
                "schedule_status": schedule_status,
                "notes": "",
                "battery_after": battery,
            })
    return pd.DataFrame(schedule_rows), pd.DataFrame(trajectory_rows, columns=TRAJECTORY_SCHEDULE_COLUMNS)

def infer_initial_battery(group):
    first = group.sort_values("sequence_order").iloc[0]
    return float(first["battery_after"]) + float(first["energy_used"])


def apply_reroutes_to_assignment(assignment, reroutes):
    if not reroutes:
        return assignment.copy()
    updated = assignment.copy()
    for (amr_id, task_id, sequence_order, segment_type), path in reroutes.items():
        mask = (
            (updated["amr_id"].astype(str) == str(amr_id))
            & (updated["task_id"].astype(str) == str(task_id))
            & (updated["sequence_order"].astype(int) == int(sequence_order))
        )
        if not mask.any():
            continue
        prefix = "transition" if segment_type == "transition" else "loaded"
        updated.loc[mask, f"{prefix}_path_uid"] = path["path_uid"]
        updated.loc[mask, f"{prefix}_path_id"] = path["path_id"]
        updated.loc[mask, f"{prefix}_distance"] = float(path["distance"])
        updated.loc[mask, f"{prefix}_cost"] = float(path["total_cost"])
        travel_time = float(path["travel_time"])
        if segment_type == "loaded":
            travel_time = travel_time / LOADED_SPEED_FACTOR
        updated.loc[mask, f"{prefix}_travel_time"] = round(travel_time, 6)
    return updated


def resample_trajectory_schedule(trajectory_schedule):
    if trajectory_schedule.empty:
        return pd.DataFrame(columns=TRAJECTORY_SCHEDULE_COLUMNS)
    df = trajectory_schedule.copy()
    df["absolute_time"] = pd.to_numeric(df["absolute_time"], errors="coerce").round(3)
    return df.sort_values(["absolute_time", "amr_id", "sample_index"]).reset_index(drop=True)


def detect_trajectory_conflicts(trajectory_schedule):
    if trajectory_schedule.empty:
        return pd.DataFrame(columns=CONFLICT_COLUMNS)
    samples = resample_trajectory_schedule(trajectory_schedule)
    conflicts = []
    rows = list(samples.itertuples(index=False))
    for left_index, left in enumerate(rows):
        left_time = float(left.absolute_time)
        for right in rows[left_index + 1:]:
            time_delta = float(right.absolute_time) - left_time
            if time_delta > TIME_TOLERANCE:
                break
            if str(left.amr_id) == str(right.amr_id):
                continue
            distance = hypot(float(left.x) - float(right.x), float(left.y) - float(right.y))
            threshold = float(left.footprint_radius) + float(right.footprint_radius) + SAFETY_MARGIN
            if distance >= threshold:
                continue
            conflict_time = (left_time + float(right.absolute_time)) / 2.0
            conflicts.append({
                "time": f"{conflict_time:g}",
                "location": f"{(float(left.x) + float(right.x)) / 2:.2f},{(float(left.y) + float(right.y)) / 2:.2f}",
                "location_type": "space",
                "involved_amrs": ";".join(sorted([str(left.amr_id), str(right.amr_id)])),
                "involved_tasks": ";".join(sorted([str(left.task_id), str(right.task_id)])),
                "involved_path_uids": ";".join(sorted([str(left.path_uid), str(right.path_uid)])),
                "distance": round(distance, 3),
                "clearance_threshold": round(threshold, 3),
                "time_delta": round(abs(time_delta), 3),
                "repair_action": "pending_p3_repair",
                "status": "detected",
                "conflict_method": CONFLICT_METHOD,
            })
    conflicts.extend(detect_node_capacity_conflicts(samples))
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
    return pd.DataFrame(conflicts, columns=CONFLICT_COLUMNS)


def node_from_stationary_path_uid(path_uid):
    text = str(path_uid)
    return text.split("::", 1)[1] if text.startswith("stationary::") else None


def detect_node_capacity_conflicts(samples):
    conflicts = []
    stationary = samples[samples["path_uid"].astype(str).str.startswith("stationary::")].copy()
    if stationary.empty:
        return conflicts
    stationary["node_id"] = stationary["path_uid"].map(node_from_stationary_path_uid)
    stationary = stationary[stationary["node_id"].map(node_requires_capacity)]
    for (node_id, t), group in stationary.groupby(["node_id", "absolute_time"], sort=True):
        amrs = sorted(group["amr_id"].astype(str).unique())
        if len(amrs) <= 1:
            continue
        conflicts.append({
            "time": f"{float(t):g}",
            "location": f"{float(group['x'].mean()):.2f},{float(group['y'].mean()):.2f}",
            "location_type": f"node:{node_id}",
            "involved_amrs": ";".join(amrs),
            "involved_tasks": ";".join(sorted(group["task_id"].astype(str).unique())),
            "involved_path_uids": ";".join(sorted(group["path_uid"].astype(str).unique())),
            "distance": 0.0,
            "clearance_threshold": 1.0,
            "time_delta": 0.0,
            "repair_action": "pending_node_capacity_wait_repair",
            "status": "detected",
            "conflict_method": "node_capacity_check",
        })
    return conflicts


def first_conflict(conflict_log):
    if conflict_log.empty:
        return None
    ordered = conflict_log.copy()
    ordered["time_numeric"] = pd.to_numeric(ordered["time"], errors="coerce")
    return ordered.sort_values(["time_numeric", "conflict_id"]).iloc[0]


def parse_semicolon_values(value):
    return [] if not is_present(value) else [part for part in str(value).split(";") if part]


def conflict_candidates(conflict, schedule_result):
    amrs = parse_semicolon_values(conflict["involved_amrs"])
    tasks = parse_semicolon_values(conflict["involved_tasks"])
    candidates = []
    for amr_id in amrs:
        for task_id in tasks:
            matches = schedule_result[(schedule_result["amr_id"].astype(str) == amr_id) & (schedule_result["task_id"].astype(str) == task_id)]
            for row in matches.itertuples(index=False):
                candidates.append(task_key(row.amr_id, row.task_id, row.sequence_order))
    return sorted(set(candidates))


def reroute_candidates(conflict, state, path_cost):
    path_uids = set(uid for uid in parse_semicolon_values(conflict["involved_path_uids"]) if not uid.startswith("stationary::"))
    if not path_uids:
        return []
    current = apply_reroutes_to_assignment(state.assignment, state.reroutes)
    options = []
    for row in current.itertuples(index=False):
        for segment_type in ("transition", "loaded"):
            current_uid = getattr(row, f"{segment_type}_path_uid")
            if str(current_uid) not in path_uids:
                continue
            from_node = row.start_node if segment_type == "transition" else row.pickup
            to_node = row.pickup if segment_type == "transition" else row.delivery
            alternatives = path_cost[
                (path_cost["from_node"].astype(str) == str(from_node))
                & (path_cost["to_node"].astype(str) == str(to_node))
                & (path_cost["planning_status"].astype(str) == "ok")
                & (path_cost["path_uid"].astype(str) != str(current_uid))
            ].sort_values(["rank_by_total", "total_cost", "travel_time"]).head(4)
            for alt in alternatives.to_dict("records"):
                options.append((task_key(row.amr_id, row.task_id, row.sequence_order), segment_type, alt, current_uid))
    return options



def mid_wait_candidates(conflict, state, schedule_result):
    path_uids = set(
        uid for uid in parse_semicolon_values(conflict["involved_path_uids"])
        if not uid.startswith("stationary::") and not uid.startswith("midwait::")
    )
    if not path_uids:
        return []
    conflict_time = float(conflict["time"])
    current = apply_reroutes_to_assignment(state.assignment, state.reroutes)
    options = []
    seen = set()
    schedule_lookup = schedule_result.set_index(["amr_id", "task_id", "sequence_order"])
    for row in current.itertuples(index=False):
        row_key = (str(row.amr_id), str(row.task_id), int(row.sequence_order))
        if row_key not in schedule_lookup.index:
            continue
        sched = schedule_lookup.loc[row_key]
        for segment_type in ("transition", "loaded"):
            path_uid = getattr(row, f"{segment_type}_path_uid")
            if str(path_uid) not in path_uids:
                continue
            duration = number_or_none(getattr(row, f"{segment_type}_travel_time"))
            if duration is None or duration <= 0.2:
                continue
            segment_start = sched["transition_start_time"] if segment_type == "transition" else sched["loaded_start_time"]
            if not is_present(segment_start):
                continue
            for lead_time in MID_WAIT_LEAD_TIMES:
                offset = float(conflict_time) - float(segment_start) - float(lead_time)
                offset = min(max(offset, 0.1), max(0.1, float(duration) - 0.1))
                option_key = (row_key, segment_type, str(path_uid), round(offset, 3))
                if option_key in seen:
                    continue
                seen.add(option_key)
                options.append((task_key(row.amr_id, row.task_id, row.sequence_order), segment_type, str(path_uid), offset))
    return options

def resequence_candidates(conflict, state, path_cost):
    amrs = parse_semicolon_values(conflict["involved_amrs"])
    tasks = parse_semicolon_values(conflict["involved_tasks"])
    options = []
    current = state.assignment.copy()
    for amr_id in amrs:
        group = current[current["amr_id"].astype(str) == str(amr_id)].sort_values("sequence_order")
        if len(group) < 2:
            continue
        for row in group.itertuples(index=False):
            if str(row.task_id) not in tasks:
                continue
            order = int(row.sequence_order)
            for first_order in (order - 1, order):
                if first_order < 1 or first_order >= len(group):
                    continue
                candidate_assignment = swap_adjacent_tasks(current, amr_id, first_order, path_cost)
                if candidate_assignment is None:
                    continue
                options.append((amr_id, row.task_id, order, first_order, candidate_assignment))
    return options
def compute_metrics(schedule_result, conflict_log, state=None):
    scheduled = schedule_result[schedule_result["schedule_status"] == "scheduled"] if "schedule_status" in schedule_result else schedule_result
    finish = pd.to_numeric(scheduled.get("finish_time", pd.Series(dtype=float)), errors="coerce")
    delay = pd.to_numeric(scheduled.get("delay", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    waiting = pd.to_numeric(scheduled.get("waiting_time", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    total_wait_added = 0.0
    if state is not None:
        total_wait_added = sum(state.repair_waits.values()) + sum(float(v.get("wait_amount", 0.0)) for v in state.mid_path_waits.values())
    reroute_count = len(state.reroutes) if state is not None else 0
    resequence_count = len(state.resequence_moves) if state is not None else 0
    initial_wait_added = 0.0
    if state is not None:
        initial_wait_added = sum(wait for (_amr, _task, seq), wait in state.repair_waits.items() if int(seq) == 1)
    return {
        "remaining_conflicts": int(len(conflict_log)),
        "total_delay": float(delay.sum()),
        "cmax": float(finish.max()) if finish.notna().any() else 0.0,
        "total_wait_added": float(total_wait_added),
        "total_waiting": float(waiting.sum()),
        "reroute_count": int(reroute_count),
        "resequence_count": int(resequence_count),
        "initial_wait_added": float(initial_wait_added),
    }


def objective_value(schedule_result, conflict_log, state):
    m = compute_metrics(schedule_result, conflict_log, state)
    return (
        OBJ_W_DELAY * m["total_delay"]
        + OBJ_W_CMAX * m["cmax"]
        + OBJ_W_WAIT * m["total_wait_added"]
        + OBJ_W_REROUTE * m["reroute_count"]
        + OBJ_W_RESEQUENCE * m["resequence_count"]
        + OBJ_W_INITIAL_WAIT * m["initial_wait_added"]
    )



def action_priority(action):
    return {
        "mid_path_wait": 0,
        "reroute": 1,
        "resequence": 2,
        "wait_before_task": 3,
    }.get(action, 9)
def evaluate_state(state, trajectory_samples):
    schedule, trajectory = build_schedule_from_state(state, trajectory_samples)
    conflicts = detect_trajectory_conflicts(trajectory)
    return schedule, trajectory, conflicts, objective_value(schedule, conflicts, state)


def quantize_wait(value):
    return round(max(0.0, float(value)) / WAIT_COMPRESSION_STEP) * WAIT_COMPRESSION_STEP


def zero_conflict_state(state, trajectory_samples):
    schedule, trajectory, conflicts, _objective = evaluate_state(state, trajectory_samples)
    return conflicts.empty, schedule, trajectory, conflicts


def set_repair_wait(state, wait_key, wait_value):
    candidate = state.copy()
    candidate.repair_waits[wait_key] = round(quantize_wait(wait_value), 6)
    return candidate


def set_mid_path_wait(state, wait_key, wait_value):
    candidate = state.copy()
    wait = dict(candidate.mid_path_waits[wait_key])
    wait["wait_amount"] = round(quantize_wait(wait_value), 6)
    candidate.mid_path_waits[wait_key] = wait
    return candidate


def minimize_wait_amount(state, trajectory_samples, wait_key, current_wait, setter):
    current_wait = round(quantize_wait(current_wait), 6)
    if current_wait <= WAIT_COMPRESSION_STEP + 1e-9:
        return state, None

    zero_state = setter(state, wait_key, 0.0)
    feasible, schedule, trajectory, conflicts = zero_conflict_state(zero_state, trajectory_samples)
    if feasible:
        return zero_state, (schedule, trajectory, conflicts)

    low = 0.0
    high = current_wait
    while high - low > WAIT_COMPRESSION_STEP + 1e-9:
        mid = round(quantize_wait((low + high) / 2.0), 6)
        if mid <= low + 1e-9:
            mid = round(low + WAIT_COMPRESSION_STEP, 6)
        if mid >= high - 1e-9:
            break
        candidate = setter(state, wait_key, mid)
        feasible, schedule, trajectory, conflicts = zero_conflict_state(candidate, trajectory_samples)
        if feasible:
            high = mid
            best_payload = (schedule, trajectory, conflicts)
        else:
            low = mid

    best_wait = round(quantize_wait(high), 6)
    best_state = setter(state, wait_key, best_wait)
    feasible, schedule, trajectory, conflicts = zero_conflict_state(best_state, trajectory_samples)
    if not feasible:
        return state, None
    best_payload = (schedule, trajectory, conflicts)

    if best_wait < current_wait - 1e-9:
        return best_state, best_payload
    return state, None


def compress_zero_conflict_waits(state, trajectory_samples):
    feasible, schedule, trajectory, conflicts = zero_conflict_state(state, trajectory_samples)
    if not feasible:
        return state, schedule, trajectory, conflicts

    current_state = state.copy()
    current_payload = (schedule, trajectory, conflicts)
    for _pass in range(WAIT_COMPRESSION_MAX_PASSES):
        changed = False
        repair_items = sorted(
            [(key, wait) for key, wait in current_state.repair_waits.items() if wait > WAIT_COMPRESSION_STEP + 1e-9],
            key=lambda item: item[1],
            reverse=True,
        )
        for wait_key, wait_value in repair_items:
            current_state, payload = minimize_wait_amount(
                current_state,
                trajectory_samples,
                wait_key,
                wait_value,
                set_repair_wait,
            )
            if payload is not None:
                current_payload = payload
                changed = True

        mid_wait_items = sorted(
            [
                (key, float(wait.get("wait_amount", 0.0)))
                for key, wait in current_state.mid_path_waits.items()
                if float(wait.get("wait_amount", 0.0)) > WAIT_COMPRESSION_STEP + 1e-9
            ],
            key=lambda item: item[1],
            reverse=True,
        )
        for wait_key, wait_value in mid_wait_items:
            current_state, payload = minimize_wait_amount(
                current_state,
                trajectory_samples,
                wait_key,
                wait_value,
                set_mid_path_wait,
            )
            if payload is not None:
                current_payload = payload
                changed = True

        if not changed:
            break

    return current_state, *current_payload


def run_repair_mode(assignment, trajectory_samples, path_cost, mode):
    state = RepairState(assignment)
    schedule, trajectory, conflicts, objective = evaluate_state(state, trajectory_samples)
    initial_conflicts = len(conflicts)
    process_rows = []
    iterations = 0
    best_seen = (len(conflicts), objective, state.copy(), schedule.copy(), trajectory.copy(), conflicts.copy())

    while not conflicts.empty and iterations < MAX_REPAIR_ITERATIONS:
        iterations += 1
        conflict = first_conflict(conflicts)
        options = []
        current_key = (len(conflicts), objective)

        for candidate in conflict_candidates(conflict, schedule):
            amr_id, task_id, sequence_order = candidate
            for wait_amount in WAIT_OPTIONS:
                candidate_state = state.copy()
                candidate_state.repair_waits = add_wait_before_task(candidate_state.repair_waits, amr_id, sequence_order, wait_amount)
                cand_schedule, cand_trajectory, cand_conflicts, cand_objective = evaluate_state(candidate_state, trajectory_samples)
                options.append((len(cand_conflicts), cand_objective, action_priority("wait_before_task"), "wait_before_task", candidate_state, cand_schedule, cand_trajectory, cand_conflicts, {
                    "target_amr": amr_id, "target_task": task_id, "target_sequence_order": sequence_order,
                    "segment_type": "task_start", "old_path_uid": "", "new_path_uid": "", "wait_added": wait_amount,
                }))

        for key, segment_type, path_uid, offset in mid_wait_candidates(conflict, state, schedule):
            for wait_amount in WAIT_OPTIONS:
                candidate_state = state.copy()
                candidate_state.mid_path_waits[(key[0], key[1], key[2], segment_type)] = {
                    "path_uid": path_uid,
                    "offset_time": offset,
                    "wait_amount": wait_amount,
                }
                cand_schedule, cand_trajectory, cand_conflicts, cand_objective = evaluate_state(candidate_state, trajectory_samples)
                options.append((len(cand_conflicts), cand_objective, action_priority("mid_path_wait"), "mid_path_wait", candidate_state, cand_schedule, cand_trajectory, cand_conflicts, {
                    "target_amr": key[0], "target_task": key[1], "target_sequence_order": key[2],
                    "segment_type": segment_type, "old_path_uid": path_uid, "new_path_uid": path_uid, "wait_added": wait_amount,
                }))

        if mode in ("wait_reroute", "wait_reroute_resequence"):
            for key, segment_type, alt_path, old_uid in reroute_candidates(conflict, state, path_cost):
                candidate_state = state.copy()
                candidate_state.reroutes[(key[0], key[1], key[2], segment_type)] = alt_path
                cand_schedule, cand_trajectory, cand_conflicts, cand_objective = evaluate_state(candidate_state, trajectory_samples)
                options.append((len(cand_conflicts), cand_objective, action_priority("reroute"), "reroute", candidate_state, cand_schedule, cand_trajectory, cand_conflicts, {
                    "target_amr": key[0], "target_task": key[1], "target_sequence_order": key[2],
                    "segment_type": segment_type, "old_path_uid": old_uid, "new_path_uid": alt_path["path_uid"], "wait_added": 0.0,
                }))

        if mode == "wait_reroute_resequence":
            for amr_id, task_id, sequence_order, first_order, candidate_assignment in resequence_candidates(conflict, state, path_cost):
                candidate_state = state.copy()
                candidate_state.assignment = candidate_assignment
                candidate_state.repair_waits = initialize_repair_waits(candidate_assignment)
                candidate_state.mid_path_waits = {}
                candidate_state.reroutes = {}
                candidate_state.resequence_moves.append({"amr_id": amr_id, "first_order": first_order})
                cand_schedule, cand_trajectory, cand_conflicts, cand_objective = evaluate_state(candidate_state, trajectory_samples)
                options.append((len(cand_conflicts), cand_objective, action_priority("resequence"), "resequence", candidate_state, cand_schedule, cand_trajectory, cand_conflicts, {
                    "target_amr": amr_id, "target_task": task_id, "target_sequence_order": sequence_order,
                    "segment_type": "adjacent_swap", "old_path_uid": f"swap_at_{first_order}", "new_path_uid": f"swap_at_{first_order}", "wait_added": 0.0,
                }))

        improving = [option for option in options if (option[0], option[1]) < current_key]
        if not improving:
            process_rows.append({
                "mode": mode,
                "iteration": iterations,
                "conflict_id": conflict["conflict_id"],
                "conflict_time": conflict["time"],
                "conflict_type": conflict["location_type"],
                "before_conflicts": len(conflicts),
                "action": "no_improving_action",
                "target_amr": "", "target_task": "", "target_sequence_order": "", "segment_type": "",
                "old_path_uid": "", "new_path_uid": "", "wait_added": 0.0,
                "after_conflicts": len(conflicts),
                "objective": round(objective, 6),
            })
            break
        best = min(improving, key=lambda item: (item[0], item[1], item[2]))
        before_count = len(conflicts)
        _count, objective, _priority, action, state, schedule, trajectory, conflicts, detail = best
        if (len(conflicts), objective) < (best_seen[0], best_seen[1]):
            best_seen = (len(conflicts), objective, state.copy(), schedule.copy(), trajectory.copy(), conflicts.copy())
        process_rows.append({
            "mode": mode,
            "iteration": iterations,
            "conflict_id": conflict["conflict_id"],
            "conflict_time": conflict["time"],
            "conflict_type": conflict["location_type"],
            "before_conflicts": before_count,
            "action": action,
            **detail,
            "after_conflicts": len(conflicts),
            "objective": round(objective, 6),
        })

    _best_count, _best_objective, state, schedule, trajectory, conflicts = best_seen
    return {
        "mode": mode,
        "state": state,
        "schedule": schedule,
        "trajectory": trajectory,
        "conflicts": conflicts,
        "process_log": pd.DataFrame(process_rows, columns=PROCESS_LOG_COLUMNS),
        "initial_conflicts": initial_conflicts,
        "iterations": iterations,
        "objective": objective_value(schedule, conflicts, state),
    }


def compress_repair_result(result, trajectory_samples):
    if not result["conflicts"].empty:
        return result
    state, schedule, trajectory, conflicts = compress_zero_conflict_waits(result["state"], trajectory_samples)
    updated = dict(result)
    updated.update({
        "state": state,
        "schedule": schedule,
        "trajectory": trajectory,
        "conflicts": conflicts,
        "objective": objective_value(schedule, conflicts, state),
    })
    return updated

def repair_conflicts(assignment, trajectory_samples, path_cost=None):
    if path_cost is None:
        path_cost = pd.read_csv(p1_data_dir(DEFAULT_P1_ALGORITHM) / "path_cost.csv")
    result = run_repair_mode(assignment, trajectory_samples, path_cost, "wait")
    return result["schedule"], result["trajectory"], result["conflicts"], result["initial_conflicts"], result["iterations"]


def p2_compatible_schedule(original_assignment, state, mode):
    schedule, _trajectory = build_schedule_from_state(state, pd.read_csv(p1_data_dir(DEFAULT_P1_ALGORITHM) / "path_trajectory_samples.csv"))
    updated = apply_reroutes_to_assignment(state.assignment, state.reroutes)
    out = updated.copy()
    lookup = schedule.set_index(["amr_id", "task_id", "sequence_order"])
    for idx, row in out.iterrows():
        key = (row["amr_id"], row["task_id"], int(row["sequence_order"]))
        if key not in lookup.index:
            continue
        s = lookup.loc[key]
        out.at[idx, "estimated_arrival_pickup"] = round(float(s["arrival_pickup_time"]), 6) if pd.notna(s["arrival_pickup_time"]) else None
        out.at[idx, "estimated_start_time"] = round(float(s["start_time"]), 6) if pd.notna(s["start_time"]) else None
        out.at[idx, "estimated_finish_time"] = round(float(s["finish_time"]), 6) if pd.notna(s["finish_time"]) else None
        out.at[idx, "estimated_delay"] = round(float(s["delay"]), 6) if pd.notna(s["delay"]) else None
        out.at[idx, "estimated_waiting"] = round(float(s["waiting_time"]), 6) if pd.notna(s["waiting_time"]) else None
        energy = float(out.at[idx, "transition_distance"]) * EMPTY_ENERGY_RATE + float(out.at[idx, "loaded_distance"]) * LOADED_ENERGY_RATE + float(out.at[idx, "service_time"]) * SERVICE_ENERGY_RATE
        out.at[idx, "energy_used"] = round(energy, 6)
        out.at[idx, "battery_after"] = round(float(s["battery_after"]), 6)
    out["assignment_method"] = out["assignment_method"].astype(str) + f"+p3_{mode}"
    notes = []
    for row in out.itertuples(index=False):
        key = task_key(row.amr_id, row.task_id, row.sequence_order)
        row_notes = []
        wait = state.repair_waits.get(key, 0.0)
        if wait > 0:
            row_notes.append(f"p3 wait +{wait:g}s")
        for (amr_id, task_id, sequence_order, segment_type), path in state.reroutes.items():
            if (amr_id, task_id, sequence_order) == key:
                row_notes.append(f"p3 {segment_type} reroute to {path['path_uid']}")
        notes.append("; ".join(part for part in [getattr(row, "notes", ""), "; ".join(row_notes)] if is_present(part)))
    out["notes"] = notes
    return out[list(original_assignment.columns)]


def build_summary(results, selected_mode):
    rows = []
    for result in results:
        m = compute_metrics(result["schedule"], result["conflicts"], result["state"])
        rows.append({
            "mode": result["mode"],
            "selected": int(result["mode"] == selected_mode),
            "initial_conflicts": result["initial_conflicts"],
            "remaining_conflicts": m["remaining_conflicts"],
            "total_delay": round(m["total_delay"], 6),
            "Cmax": round(m["cmax"], 6),
            "total_wait_added": round(m["total_wait_added"], 6),
            "reroute_count": m["reroute_count"],
            "resequence_count": m["resequence_count"],
            "initial_wait_added": round(m["initial_wait_added"], 6),
            "objective": round(objective_value(result["schedule"], result["conflicts"], result["state"]), 6),
            "selection_rule": "min remaining_conflicts, then 100*delay+5*Cmax+wait_added+2*reroute_count+20*resequence_count+20*initial_wait_added",
        })
    return pd.DataFrame(rows)


def run_all_modes(assignment, trajectory_samples, path_cost):
    original_state = RepairState(assignment)
    original_schedule, original_trajectory, original_conflicts, original_objective = evaluate_state(original_state, trajectory_samples)
    original = {
        "mode": "original", "state": original_state, "schedule": original_schedule,
        "trajectory": original_trajectory, "conflicts": original_conflicts,
        "process_log": pd.DataFrame(columns=PROCESS_LOG_COLUMNS),
        "initial_conflicts": len(original_conflicts), "iterations": 0, "objective": original_objective,
    }
    wait = run_repair_mode(assignment, trajectory_samples, path_cost, "wait")
    wait_reroute = run_repair_mode(assignment, trajectory_samples, path_cost, "wait_reroute")
    wait_reroute_resequence = run_repair_mode(assignment, trajectory_samples, path_cost, "wait_reroute_resequence")
    repair_results = [
        compress_repair_result(wait, trajectory_samples),
        compress_repair_result(wait_reroute, trajectory_samples),
        compress_repair_result(wait_reroute_resequence, trajectory_samples),
    ]
    wait, wait_reroute, wait_reroute_resequence = repair_results
    selected = min(repair_results, key=lambda r: (len(r["conflicts"]), objective_value(r["schedule"], r["conflicts"], r["state"]), {"wait_reroute_resequence": 0, "wait_reroute": 1, "wait": 2}.get(r["mode"], 9)))
    return original, wait, wait_reroute, wait_reroute_resequence, selected


def main():
    args = parse_args()
    assignment, trajectory_samples, path_cost = load_inputs(args.p1_algorithm)
    original, wait, wait_reroute, wait_reroute_resequence, selected = run_all_modes(assignment, trajectory_samples, path_cost)

    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    p2_compatible_schedule(assignment, selected["state"], selected["mode"]).to_csv(PROCESSED_DATA_DIR / "schedule_result.csv", index=False)
    selected["trajectory"].to_csv(PROCESSED_DATA_DIR / "trajectory_schedule.csv", index=False)
    pd.concat([wait["process_log"], wait_reroute["process_log"], wait_reroute_resequence["process_log"]], ignore_index=True).to_csv(PROCESSED_DATA_DIR / "conflict_log.csv", index=False)
    build_summary([wait, wait_reroute, wait_reroute_resequence], selected["mode"]).to_csv(PROCESSED_DATA_DIR / "repair_summary.csv", index=False)

    print(f"P1 algorithm: {args.p1_algorithm}")
    print(f"Original conflicts: {len(original['conflicts'])}")
    print(f"Wait conflicts: {len(wait['conflicts'])}, objective={objective_value(wait['schedule'], wait['conflicts'], wait['state']):.3f}")
    print(f"Wait+reroute conflicts: {len(wait_reroute['conflicts'])}, objective={objective_value(wait_reroute['schedule'], wait_reroute['conflicts'], wait_reroute['state']):.3f}")
    print(f"Wait+reroute+resequence conflicts: {len(wait_reroute_resequence['conflicts'])}, objective={objective_value(wait_reroute_resequence['schedule'], wait_reroute_resequence['conflicts'], wait_reroute_resequence['state']):.3f}")
    print(f"Selected P3 mode: {selected['mode']}")
    print(f"Trajectory sample rows: {len(selected['trajectory'])}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'schedule_result.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'trajectory_schedule.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'conflict_log.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'repair_summary.csv'}")


if __name__ == "__main__":
    main()























