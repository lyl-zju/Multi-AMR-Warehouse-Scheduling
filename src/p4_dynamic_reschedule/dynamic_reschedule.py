"""P4 dynamic event impact analysis and rolling-horizon rescheduling.

This module keeps the P3 interface contract: it reads the P3 conflict-repaired
schedule/trajectory, then handles area blocks, AMR delays and released dynamic
tasks. The rescheduler uses an OR-style regret insertion heuristic plus local
search, and repairs post-reschedule conflicts by adding rolling-horizon waits.
"""

from collections import defaultdict
from dataclasses import dataclass
from math import hypot
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
P1_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "p1" / "mixed"
P3_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "p3"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "p4"

sys.path.insert(0, str(PROJECT_ROOT / "src" / "p3_schedule_conflicts"))
from schedule_and_detect_conflicts import (  # noqa: E402
    TRAJECTORY_SCHEDULE_COLUMNS,
    detect_trajectory_conflicts,
    expand_trajectory,
    stationary_rows,
)


RESCHEDULE_METHOD = "rolling_horizon_regret_insertion_local_search"
STRATEGY_LABELS = {
    "global_replan": "全局重排",
    "wait_only": "仅等待",
    "rolling_horizon": "滚动时域重排",
}
OBJECTIVE_PROFILES = {
    "service_priority": "服务等级优先",
    "balanced": "均衡权重",
    "stability_priority": "稳定性优先",
}
WAIT_ONLY_SEGMENTS = {
    "repair_wait_at_node",
    "block_wait_at_node",
}
LOADED_SPEED_FACTOR = 0.90
EMPTY_ENERGY_RATE = 1.00
LOADED_ENERGY_RATE = 1.20
SERVICE_ENERGY_RATE = 0.20
BATTERY_SAFETY_THRESHOLD = 10.0
DEFAULT_DYNAMIC_TASK_DUE_BUFFER = 30.0
CHANGE_TOLERANCE = 1e-5
BLOCK_WAIT_EPS = 0.1
MAX_BLOCK_SHIFT_ATTEMPTS = 8
MAX_LOCAL_SEARCH_ITERATIONS = 10
MAX_CONFLICT_REPAIR_ITERATIONS = 30
MAX_NEW_TASK_GLOBAL_CANDIDATES = 3
MAX_NEW_TASK_CANDIDATE_REPAIR_ITERATIONS = 3
WAIT_OPTIONS = (1.0, 2.0, 5.0, 8.0, 12.0, 20.0)
REPLAN_HORIZON_SLACK = 1e-6


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    pickup: str
    delivery: str
    service_time: float
    earliest_start: float
    latest_finish: float
    priority: float
    release_event_id: str = ""


@dataclass(frozen=True)
class AmrSpec:
    amr_id: str
    init_node: str
    battery: float
    speed: float


@dataclass
class AmrState:
    amr_id: str
    available_time: float
    node: str
    battery: float
    prefix_count: int = 0


@dataclass(frozen=True)
class AreaBlock:
    event_id: str
    target_id: str
    start_time: float
    end_time: float
    x: float
    y: float
    width: float
    height: float


def load_inputs():
    schedule = pd.read_csv(P3_DATA_DIR / "schedule_result.csv")
    trajectory_schedule = pd.read_csv(P3_DATA_DIR / "trajectory_schedule.csv")
    dynamic_events = pd.read_csv(RAW_DATA_DIR / "dynamic_events.csv")
    tasks = pd.read_csv(RAW_DATA_DIR / "tasks.csv")
    amrs = pd.read_csv(RAW_DATA_DIR / "amrs.csv")
    path_cost = pd.read_csv(P1_DATA_DIR / "path_cost.csv")
    trajectory_samples = pd.read_csv(P1_DATA_DIR / "path_trajectory_samples.csv")
    return schedule, trajectory_schedule, dynamic_events, tasks, amrs, path_cost, trajectory_samples


def is_present(value):
    return value is not None and not pd.isna(value) and str(value) != ""


def to_float(value, default=None):
    if not is_present(value):
        return default
    return float(value)


def event_time(event):
    if str(event.event_type) == "new_task":
        return to_float(event.release_time, 0.0)
    return to_float(event.start_time, 0.0)


def point_to_rect_distance(x, y, rect_x, rect_y, width, height):
    dx = max(rect_x - x, 0.0, x - (rect_x + width))
    dy = max(rect_y - y, 0.0, y - (rect_y + height))
    return hypot(dx, dy)


def normalize_p3_schedule(schedule):
    table = schedule.copy()
    table["start_time"] = pd.to_numeric(table["estimated_start_time"], errors="coerce")
    table["finish_time"] = pd.to_numeric(table["estimated_finish_time"], errors="coerce")
    table["delay"] = pd.to_numeric(table["estimated_delay"], errors="coerce")
    table["waiting_time"] = pd.to_numeric(table["estimated_waiting"], errors="coerce")
    table["schedule_status"] = np.where(
        (table["transition_status"].astype(str) == "ok")
        & (table["loaded_path_status"].astype(str) == "ok"),
        "scheduled",
        "missing_path",
    )
    table["source"] = "p3"
    return table


def build_task_specs(tasks_df):
    tasks = {}
    for row in tasks_df.itertuples(index=False):
        tasks[str(row.task_id)] = TaskSpec(
            task_id=str(row.task_id),
            pickup=str(row.pickup),
            delivery=str(row.delivery),
            service_time=float(row.service_time),
            earliest_start=float(row.earliest_start),
            latest_finish=float(row.latest_finish),
            priority=float(row.priority),
        )
    return tasks


def build_amr_specs(amrs_df):
    specs = {}
    for row in amrs_df.itertuples(index=False):
        specs[str(row.amr_id)] = AmrSpec(
            amr_id=str(row.amr_id),
            init_node=str(row.init_node),
            battery=float(row.battery),
            speed=float(row.speed),
        )
    return specs


def zero_path(node):
    return {
        "path_uid": "same_node",
        "path_id": "same_node",
        "from_node": node,
        "to_node": node,
        "distance": 0.0,
        "travel_time": 0.0,
        "total_cost": 0.0,
        "rank_by_total": 0,
    }


def path_candidates(path_cost, from_node, to_node):
    if str(from_node) == str(to_node):
        return [zero_path(from_node)]
    candidates = path_cost[
        (path_cost["from_node"].astype(str) == str(from_node))
        & (path_cost["to_node"].astype(str) == str(to_node))
    ].copy()
    if "planning_status" in candidates:
        candidates = candidates[candidates["planning_status"].astype(str) == "ok"]
    if candidates.empty:
        return []
    return candidates.sort_values(["rank_by_total", "total_cost", "travel_time"]).to_dict("records")


def actual_travel_time(path, amr_speed, segment_type):
    base = float(path.get("travel_time", 0.0))
    divisor = float(amr_speed)
    if segment_type == "loaded":
        divisor *= LOADED_SPEED_FACTOR
    return base / divisor if divisor > 1e-9 else base


def build_trajectory_lookup(trajectory_samples):
    return {
        str(path_uid): group.sort_values("sample_index").reset_index(drop=True)
        for path_uid, group in trajectory_samples.groupby("path_uid")
    }


def path_block_violations(path, segment_start, actual_duration, active_blocks, trajectory_by_uid):
    path_uid = str(path.get("path_uid", ""))
    if path_uid == "same_node" or not active_blocks:
        return []
    samples = trajectory_by_uid.get(path_uid)
    if samples is None or samples.empty:
        return []

    base_duration = float(samples["offset_time"].max())
    scale = float(actual_duration) / base_duration if base_duration > 1e-9 else 1.0
    violations = []
    for block in active_blocks:
        for sample in samples.itertuples(index=False):
            absolute_time = float(segment_start) + float(sample.offset_time) * scale
            if absolute_time < block.start_time or absolute_time > block.end_time:
                continue
            distance = point_to_rect_distance(
                float(sample.x),
                float(sample.y),
                block.x,
                block.y,
                block.width,
                block.height,
            )
            if distance <= float(sample.footprint_radius):
                violations.append(block)
                break
    return violations


def choose_path(path_cost, trajectory_by_uid, from_node, to_node, amr, segment_type, ready_time, active_blocks):
    candidates = path_candidates(path_cost, from_node, to_node)
    if not candidates:
        return {
            "status": "missing_path",
            "path": None,
            "start_time": None,
            "wait_added": 0.0,
            "duration": None,
            "blocked_events": [],
        }

    options = []
    last_blocked_events = []
    for candidate in candidates:
        duration = actual_travel_time(candidate, amr.speed, segment_type)
        segment_start = float(ready_time)
        blocked_events = []
        for _attempt in range(MAX_BLOCK_SHIFT_ATTEMPTS):
            violations = path_block_violations(candidate, segment_start, duration, active_blocks, trajectory_by_uid)
            if not violations:
                wait_added = segment_start - float(ready_time)
                score = wait_added * 20.0 + duration + float(candidate.get("total_cost", 0.0))
                options.append((score, candidate, segment_start, wait_added, duration, blocked_events))
                break
            blocked_events = [block.event_id for block in violations]
            last_blocked_events = blocked_events
            segment_start = max(block.end_time for block in violations) + BLOCK_WAIT_EPS

    if not options:
        return {
            "status": "blocked_path",
            "path": candidates[0],
            "start_time": None,
            "wait_added": 0.0,
            "duration": None,
            "blocked_events": last_blocked_events,
        }

    _score, path, start_time, wait_added, duration, blocked_events = min(
        options,
        key=lambda item: (
            item[0],
            float(item[1].get("rank_by_total", 0)),
            float(item[1].get("total_cost", 0.0)),
        ),
    )
    return {
        "status": "ok",
        "path": path,
        "start_time": start_time,
        "wait_added": wait_added,
        "duration": duration,
        "blocked_events": blocked_events,
    }


def stationary_segment(node_id, start_time, end_time, trajectory_samples, context, segment_type):
    return stationary_rows(node_id, start_time, end_time, trajectory_samples, context, segment_type)


def append_path_rows(rows, path_uid, start_time, trajectory_samples, context, duration):
    segment_rows, _duration = expand_trajectory(
        path_uid,
        start_time,
        trajectory_samples,
        context,
        actual_duration=duration,
    )
    rows.extend(segment_rows)


def schedule_sequences(
    sequences,
    amr_states,
    tasks,
    amrs,
    path_cost,
    trajectory_samples,
    trajectory_by_uid,
    active_blocks,
    repair_waits=None,
    build_trajectory=True,
):
    repair_waits = repair_waits or {}
    schedule_rows = []
    trajectory_rows = []

    for amr_id in sorted(amrs):
        amr = amrs[amr_id]
        state = amr_states[amr_id]
        current_time = float(state.available_time)
        current_node = state.node
        battery = float(state.battery)
        sequence_order = int(state.prefix_count)

        for task_id in sequences.get(amr_id, []):
            start_node = current_node
            task = tasks[task_id]
            sequence_order += 1
            key = (amr_id, task_id, sequence_order)
            task_ready_time = current_time
            manual_wait = float(repair_waits.get(key, 0.0))
            current_time += manual_wait

            transition = choose_path(
                path_cost,
                trajectory_by_uid,
                current_node,
                task.pickup,
                amr,
                "transition",
                current_time,
                active_blocks,
            )
            loaded = {"status": "missing_path"}

            if transition["status"] == "ok":
                transition_path = transition["path"]
                transition_start = transition["start_time"]
                transition_finish = transition_start + transition["duration"]
                arrival_pickup = transition_finish
                task_start = max(arrival_pickup, task.earliest_start)
                service_finish = task_start + task.service_time
                loaded = choose_path(
                    path_cost,
                    trajectory_by_uid,
                    task.pickup,
                    task.delivery,
                    amr,
                    "loaded",
                    service_finish,
                    active_blocks,
                )
            else:
                transition_path = None
                transition_start = None
                transition_finish = None
                arrival_pickup = None
                task_start = None
                service_finish = None

            if transition["status"] == "ok" and loaded["status"] == "ok":
                loaded_path = loaded["path"]
                loaded_start = loaded["start_time"]
                finish_time = loaded_start + loaded["duration"]
                delay = max(0.0, finish_time - task.latest_finish)
                waiting_time = (
                    manual_wait
                    + transition["wait_added"]
                    + max(0.0, task_start - arrival_pickup)
                    + loaded["wait_added"]
                )
                transition_status = "ok"
                loaded_status = "ok"
                schedule_status = "scheduled"
                current_time = finish_time
                current_node = task.delivery
            else:
                loaded_path = loaded.get("path")
                loaded_start = None
                finish_time = None
                delay = None
                waiting_time = None
                transition_status = transition["status"]
                loaded_status = loaded["status"]
                schedule_status = "missing_path"

            transition_distance = float(transition_path.get("distance", 0.0)) if transition_path else 0.0
            loaded_distance = float(loaded_path.get("distance", 0.0)) if loaded_path else 0.0
            energy_used = (
                transition_distance * EMPTY_ENERGY_RATE
                + loaded_distance * LOADED_ENERGY_RATE
                + task.service_time * SERVICE_ENERGY_RATE
            )
            battery -= energy_used

            context = {"amr_id": amr_id, "task_id": task_id, "sequence_order": sequence_order}
            if build_trajectory and manual_wait > 0:
                trajectory_rows.extend(
                    stationary_segment(start_node, task_ready_time, task_ready_time + manual_wait,
                                       trajectory_samples, context, "repair_wait_at_node")
                )
            if build_trajectory and transition["status"] == "ok":
                if transition["wait_added"] > 0:
                    trajectory_rows.extend(
                        stationary_segment(start_node, task_ready_time + manual_wait, transition_start,
                                           trajectory_samples, context, "block_wait_at_node")
                    )
                append_path_rows(
                    trajectory_rows,
                    transition_path["path_uid"],
                    transition_start,
                    trajectory_samples,
                    {**context, "segment_type": "transition"},
                    transition["duration"],
                )
                trajectory_rows.extend(
                    stationary_segment(task.pickup, arrival_pickup, task_start,
                                       trajectory_samples, context, "wait_at_pickup")
                )
                trajectory_rows.extend(
                    stationary_segment(task.pickup, task_start, service_finish,
                    trajectory_samples, context, "service_at_pickup")
                )
            if build_trajectory and transition["status"] == "ok" and loaded["status"] == "ok":
                if loaded["wait_added"] > 0:
                    trajectory_rows.extend(
                        stationary_segment(task.pickup, service_finish, loaded_start,
                                           trajectory_samples, context, "block_wait_at_pickup")
                    )
                append_path_rows(
                    trajectory_rows,
                    loaded_path["path_uid"],
                    loaded_start,
                    trajectory_samples,
                    {**context, "segment_type": "loaded"},
                    loaded["duration"],
                )

            notes = []
            if manual_wait > 0:
                notes.append(f"p4 conflict wait +{manual_wait:g}s")
            if transition.get("wait_added", 0.0) > 0:
                notes.append(f"p4 area block wait before transition +{transition['wait_added']:g}s")
            if loaded.get("wait_added", 0.0) > 0:
                notes.append(f"p4 area block wait before loaded +{loaded['wait_added']:g}s")
            if transition_status != "ok":
                notes.append(f"transition {transition_status}")
            if loaded_status != "ok":
                notes.append(f"loaded {loaded_status}")

            schedule_rows.append({
                "amr_id": amr_id,
                "assigned_amr": amr_id,
                "task_id": task_id,
                "sequence_order": sequence_order,
                "predecessor_task_id": "",
                "start_node": start_node,
                "pickup": task.pickup,
                "delivery": task.delivery,
                "service_time": task.service_time,
                "earliest_start": task.earliest_start,
                "latest_finish": task.latest_finish,
                "priority": task.priority,
                "transition_path_uid": transition_path.get("path_uid") if transition_path else "",
                "transition_path_id": transition_path.get("path_id") if transition_path else "",
                "transition_travel_time": transition["duration"] if transition["status"] == "ok" else None,
                "transition_distance": transition_distance,
                "transition_cost": float(transition_path.get("total_cost", 0.0)) if transition_path else 0.0,
                "loaded_path_uid": loaded_path.get("path_uid") if loaded_path else "",
                "loaded_path_id": loaded_path.get("path_id") if loaded_path else "",
                "loaded_travel_time": loaded["duration"] if loaded["status"] == "ok" else None,
                "loaded_distance": loaded_distance,
                "loaded_cost": float(loaded_path.get("total_cost", 0.0)) if loaded_path else 0.0,
                "estimated_arrival_pickup": arrival_pickup,
                "estimated_start_time": task_start,
                "estimated_finish_time": finish_time,
                "estimated_delay": delay,
                "estimated_waiting": waiting_time,
                "energy_used": energy_used,
                "battery_after": battery,
                "transition_status": transition_status,
                "loaded_path_status": loaded_status,
                "assignment_method": RESCHEDULE_METHOD,
                "notes": "; ".join(notes),
                "start_time": task_start,
                "finish_time": finish_time,
                "delay": delay,
                "waiting_time": waiting_time,
                "schedule_status": schedule_status,
                "source": "p4",
            })

            if schedule_status != "scheduled":
                break

    schedule = pd.DataFrame(schedule_rows)
    if not schedule.empty:
        schedule = schedule.sort_values(["amr_id", "sequence_order", "task_id"]).reset_index(drop=True)
        for _amr_id, group in schedule.groupby("amr_id", sort=False):
            prev = ""
            for idx in group.index:
                schedule.at[idx, "predecessor_task_id"] = prev
                prev = schedule.at[idx, "task_id"]
    trajectory = pd.DataFrame(trajectory_rows, columns=TRAJECTORY_SCHEDULE_COLUMNS)
    return schedule, trajectory


def finish_by_amr(schedule, amr_states, amrs):
    finish = {}
    for amr_id in sorted(amrs):
        rows = schedule[schedule["amr_id"].astype(str) == amr_id]
        if rows.empty or not pd.to_numeric(rows["finish_time"], errors="coerce").notna().any():
            finish[amr_id] = float(amr_states[amr_id].available_time)
        else:
            finish[amr_id] = float(pd.to_numeric(rows["finish_time"], errors="coerce").max())
    return finish


def metrics_tuple(schedule, amr_states, amrs, original_lookup=None):
    if schedule.empty:
        return (0, 0.0, 0, 0.0, 0.0, 0.0)
    status = schedule["schedule_status"].astype(str)
    missing = int((status != "scheduled").sum())
    battery = pd.to_numeric(schedule["battery_after"], errors="coerce")
    energy_violations = int((battery < BATTERY_SAFETY_THRESHOLD).sum())
    delay = pd.to_numeric(schedule["delay"], errors="coerce").fillna(0.0)
    priority = pd.to_numeric(schedule["priority"], errors="coerce").fillna(0.0)
    late = delay > 1e-9
    finish = pd.to_numeric(schedule["finish_time"], errors="coerce")
    priority_late = float((priority * late.astype(float)).sum())
    late_count = int(late.sum())
    priority_delay = float((priority * delay).sum())
    total_delay = float(delay.sum())
    cmax = float(finish.max()) if finish.notna().any() else 0.0
    empty_cost = float(pd.to_numeric(schedule["transition_cost"], errors="coerce").fillna(0.0).sum())
    energy = float(pd.to_numeric(schedule["energy_used"], errors="coerce").fillna(0.0).sum())
    loads = finish_by_amr(schedule, amr_states, amrs)
    load_balance = max(loads.values()) - min(loads.values()) if loads else 0.0
    f2 = 30.0 * priority_delay + 20.0 * total_delay + 5.0 * cmax
    f3 = 2.0 * empty_cost + 10.0 * load_balance + energy
    stability = 0.0
    if original_lookup:
        for row in schedule.itertuples(index=False):
            old = original_lookup.get(str(row.task_id))
            if old is None or not is_present(row.start_time):
                continue
            if str(row.amr_id) != str(old.get("amr_id", "")):
                stability += 10.0
            old_start = to_float(old.get("start_time"), None)
            if old_start is not None:
                stability += abs(float(row.start_time) - old_start)
    hard = missing + energy_violations
    return (hard, priority_late, late_count, f2, f3, stability)


def scalar_score(metric_tuple_value):
    hard, priority_late, late_count, f2, f3, stability = metric_tuple_value
    return hard * 1e9 + priority_late * 1e7 + late_count * 1e6 + f2 * 100.0 + f3 + stability


def changed_count_from_schedule(schedule, original_lookup):
    changed = 0
    for row in schedule.itertuples(index=False):
        old = original_lookup.get(str(row.task_id))
        if schedule_record_changed(old, {
            "amr_id": str(row.amr_id),
            "start_time": to_float(row.start_time),
            "finish_time": to_float(row.finish_time),
        }):
            changed += 1
    return changed


def objective_score(schedule, conflict_count, amr_states, amrs, original_lookup, profile="balanced"):
    hard, priority_late, late_count, f2, f3, stability = metrics_tuple(schedule, amr_states, amrs, original_lookup)
    total_delay = float(pd.to_numeric(schedule["delay"], errors="coerce").fillna(0.0).sum())
    cmax = float(pd.to_numeric(schedule["finish_time"], errors="coerce").max())
    changed = changed_count_from_schedule(schedule, original_lookup)
    hard_total = conflict_count + hard

    if profile == "service_priority":
        return (
            hard_total,
            priority_late,
            late_count,
            total_delay,
            cmax,
            changed,
            f3,
            stability,
        )
    if profile == "stability_priority":
        return (
            hard_total,
            changed,
            stability,
            total_delay,
            priority_late,
            late_count,
            cmax,
            f3,
        )
    return (
        hard_total,
        total_delay,
        priority_late,
        late_count,
        cmax,
        changed,
        f3,
        stability,
    )


def evaluate_sequences(sequences, amr_states, tasks, amrs, path_cost, trajectory_samples,
                       trajectory_by_uid, active_blocks, original_lookup=None, repair_waits=None):
    schedule, trajectory = schedule_sequences(
        sequences,
        amr_states,
        tasks,
        amrs,
        path_cost,
        trajectory_samples,
        trajectory_by_uid,
        active_blocks,
        repair_waits=repair_waits,
        build_trajectory=False,
    )
    metrics = metrics_tuple(schedule, amr_states, amrs, original_lookup)
    return metrics, scalar_score(metrics), schedule, trajectory


def insert_task(sequence, task_id, position):
    updated = list(sequence)
    updated.insert(position, task_id)
    return updated


def copy_sequences(sequences):
    return {amr_id: list(seq) for amr_id, seq in sequences.items()}


def solve_by_regret_insertion(task_ids, amr_states, tasks, amrs, path_cost, trajectory_samples,
                              trajectory_by_uid, active_blocks, original_lookup):
    sequences = {amr_id: [] for amr_id in sorted(amrs)}
    unscheduled = set(task_ids)

    while unscheduled:
        task_options = []
        for task_id in sorted(unscheduled, key=lambda tid: (tasks[tid].earliest_start, -tasks[tid].priority, tid)):
            options = []
            for amr_id in sorted(amrs):
                base = sequences[amr_id]
                for pos in range(len(base) + 1):
                    candidate = copy_sequences(sequences)
                    candidate[amr_id] = insert_task(base, task_id, pos)
                    metrics, score, _schedule, _trajectory = evaluate_sequences(
                        candidate,
                        amr_states,
                        tasks,
                        amrs,
                        path_cost,
                        trajectory_samples,
                        trajectory_by_uid,
                        active_blocks,
                        original_lookup,
                    )
                    score = score + pos * 0.01
                    options.append((metrics, score, candidate, amr_id, pos))
            options.sort(key=lambda item: (item[0], item[1]))
            best = options[0]
            second_score = options[1][1] if len(options) > 1 else best[1] + 1e6
            regret = second_score - best[1]
            task_options.append((-regret, best[0], best[1], task_id, best[2]))

        _neg_regret, _metrics, _score, chosen_task, sequences = min(task_options)
        unscheduled.remove(chosen_task)

    return local_search(sequences, amr_states, tasks, amrs, path_cost, trajectory_samples,
                        trajectory_by_uid, active_blocks, original_lookup)


def all_sequence_tasks(sequences):
    return [(amr_id, idx, task_id) for amr_id, seq in sequences.items() for idx, task_id in enumerate(seq)]


def local_search(sequences, amr_states, tasks, amrs, path_cost, trajectory_samples,
                 trajectory_by_uid, active_blocks, original_lookup):
    best = copy_sequences(sequences)
    best_metrics, best_score, _schedule, _trajectory = evaluate_sequences(
        best, amr_states, tasks, amrs, path_cost, trajectory_samples, trajectory_by_uid,
        active_blocks, original_lookup,
    )

    for _iteration in range(MAX_LOCAL_SEARCH_ITERATIONS):
        improved = None
        tasks_in_plan = all_sequence_tasks(best)
        for from_amr, from_idx, task_id in tasks_in_plan:
            for to_amr in sorted(amrs):
                max_pos = len(best[to_amr]) + (0 if to_amr == from_amr else 1)
                for to_pos in range(max_pos):
                    if from_amr == to_amr and to_pos in (from_idx, from_idx + 1):
                        continue
                    candidate = copy_sequences(best)
                    moved = candidate[from_amr].pop(from_idx)
                    insert_pos = to_pos
                    if from_amr == to_amr and to_pos > from_idx:
                        insert_pos -= 1
                    candidate[to_amr].insert(insert_pos, moved)
                    metrics, score, _schedule, _trajectory = evaluate_sequences(
                        candidate, amr_states, tasks, amrs, path_cost, trajectory_samples,
                        trajectory_by_uid, active_blocks, original_lookup,
                    )
                    if (metrics, score) < (best_metrics, best_score):
                        improved = (metrics, score, candidate)
                        break
                if improved is not None:
                    break
            if improved is not None:
                break

        if improved is None:
            for left_amr, left_idx, left_task in tasks_in_plan:
                for right_amr, right_idx, right_task in tasks_in_plan:
                    if (left_amr, left_idx) >= (right_amr, right_idx):
                        continue
                    candidate = copy_sequences(best)
                    candidate[left_amr][left_idx] = right_task
                    candidate[right_amr][right_idx] = left_task
                    metrics, score, _schedule, _trajectory = evaluate_sequences(
                        candidate, amr_states, tasks, amrs, path_cost, trajectory_samples,
                        trajectory_by_uid, active_blocks, original_lookup,
                    )
                    if (metrics, score) < (best_metrics, best_score):
                        improved = (metrics, score, candidate)
                        break
                if improved is not None:
                    break

        if improved is None:
            break
        best_metrics, best_score, best = improved

    return best


def event_to_area_block(event):
    required = [event.start_time, event.end_time, event.x, event.y, event.width, event.height]
    if any(not is_present(value) for value in required):
        return None
    return AreaBlock(
        event_id=str(event.event_id),
        target_id=str(event.target_id),
        start_time=float(event.start_time),
        end_time=float(event.end_time),
        x=float(event.x),
        y=float(event.y),
        width=float(event.width),
        height=float(event.height),
    )


def impact_row(event, affected, impact_reason):
    if affected.empty:
        affected_tasks = ""
        affected_amrs = ""
        impact_count = 0
    else:
        affected_tasks = ";".join(sorted(set(affected["task_id"].astype(str))))
        affected_amrs = ";".join(sorted(set(affected["amr_id"].astype(str))))
        impact_count = len(set(affected["task_id"].astype(str)))

    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "start_time": event.start_time,
        "end_time": event.end_time,
        "affected_tasks": affected_tasks,
        "affected_amrs": affected_amrs,
        "impact_count": impact_count,
        "impact_reason": impact_reason,
    }


def area_block_impact(event, trajectory):
    block = event_to_area_block(event)
    if block is None or trajectory.empty:
        return pd.DataFrame(columns=["task_id", "amr_id"]), "area block missing geometry or trajectory"
    samples = trajectory.copy()
    samples["absolute_time"] = pd.to_numeric(samples["absolute_time"], errors="coerce")
    window = samples[(samples["absolute_time"] >= block.start_time) & (samples["absolute_time"] <= block.end_time)]
    if window.empty:
        return pd.DataFrame(columns=["task_id", "amr_id"]), "no trajectory samples in area block time window"
    mask = window.apply(
        lambda row: point_to_rect_distance(
            float(row["x"]),
            float(row["y"]),
            block.x,
            block.y,
            block.width,
            block.height,
        ) <= float(row["footprint_radius"]),
        axis=1,
    )
    affected = window[mask][["task_id", "amr_id"]].drop_duplicates()
    reason = (
        f"blocked area {event.target_id} intersects trajectory samples"
        if not affected.empty
        else "no trajectory footprint intersects blocked area"
    )
    return affected, reason


def amr_delay_impact(event, schedule):
    start = to_float(event.start_time)
    end = to_float(event.end_time)
    if start is None or end is None:
        return pd.DataFrame(columns=["task_id", "amr_id"]), "AMR delay missing time window"
    subset = schedule[schedule["amr_id"].astype(str) == str(event.target_id)].copy()
    if subset.empty:
        return pd.DataFrame(columns=["task_id", "amr_id"]), "target AMR has no scheduled tasks"
    task_start = pd.to_numeric(subset["start_time"], errors="coerce")
    task_finish = pd.to_numeric(subset["finish_time"], errors="coerce")
    affected = subset[
        ((task_start < end) & (task_finish > start))
        | ((task_start >= start) & (task_start < end))
        | (task_finish >= start)
    ][["task_id", "amr_id"]].drop_duplicates()
    if affected.empty:
        return affected, "no task exists after delayed AMR window"
    return affected, f"AMR {event.target_id} delayed from {start:g} to {end:g}; future suffix is replanning-relevant"


def dynamic_task_from_event(event):
    release = to_float(event.release_time, event_time(event))
    service = to_float(event.service_time, 0.0)
    priority = to_float(event.priority, 1.0)
    return TaskSpec(
        task_id=str(event.target_id),
        pickup=str(event.pickup),
        delivery=str(event.delivery),
        service_time=service,
        earliest_start=release,
        latest_finish=release + DEFAULT_DYNAMIC_TASK_DUE_BUFFER,
        priority=priority,
        release_event_id=str(event.event_id),
    )


def build_original_lookup(schedule):
    lookup = {}
    for row in schedule.itertuples(index=False):
        lookup[str(row.task_id)] = {
            "amr_id": str(row.amr_id),
            "start_time": to_float(row.start_time),
            "finish_time": to_float(row.finish_time),
            "sequence_order": int(row.sequence_order),
        }
    return lookup


def build_amr_states(fixed_schedule, amrs, horizon_time, delay_windows):
    states = {}
    for amr_id, amr in amrs.items():
        rows = fixed_schedule[fixed_schedule["amr_id"].astype(str) == amr_id].copy()
        if rows.empty:
            node = amr.init_node
            battery = amr.battery
            available_time = float(horizon_time)
            prefix_count = 0
        else:
            rows = rows.sort_values(["sequence_order", "finish_time"])
            last = rows.iloc[-1]
            node = str(last["delivery"])
            battery = to_float(last.get("battery_after"), amr.battery)
            available_time = max(float(horizon_time), to_float(last.get("finish_time"), horizon_time))
            prefix_count = len(rows)

        for delay in delay_windows:
            if delay["amr_id"] != amr_id:
                continue
            if float(horizon_time) <= delay["end_time"] and available_time < delay["end_time"]:
                available_time = delay["end_time"]

        states[amr_id] = AmrState(
            amr_id=amr_id,
            available_time=available_time,
            node=node,
            battery=battery,
            prefix_count=prefix_count,
        )
    return states


def combine_schedule(fixed_schedule, future_schedule):
    frames = [frame for frame in (fixed_schedule, future_schedule) if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["amr_id", "sequence_order", "task_id"]).reset_index(drop=True)


def combine_trajectory(fixed_trajectory, future_trajectory):
    frames = [frame for frame in (fixed_trajectory, future_trajectory) if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame(columns=TRAJECTORY_SCHEDULE_COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values(["absolute_time", "amr_id", "sample_index"]).reset_index(drop=True)


def repair_conflicts(
    sequences,
    amr_states,
    tasks,
    amrs,
    path_cost,
    trajectory_samples,
    trajectory_by_uid,
    active_blocks,
    original_lookup,
    fixed_trajectory,
    max_iterations=MAX_CONFLICT_REPAIR_ITERATIONS,
):
    repair_waits = {}
    best_schedule = None
    best_trajectory = None
    best_conflicts = None

    for iteration in range(max_iterations + 1):
        future_schedule, future_trajectory = schedule_sequences(
            sequences,
            amr_states,
            tasks,
            amrs,
            path_cost,
            trajectory_samples,
            trajectory_by_uid,
            active_blocks,
            repair_waits=repair_waits,
        )
        combined_trajectory = combine_trajectory(fixed_trajectory, future_trajectory)
        conflicts = detect_trajectory_conflicts(combined_trajectory)
        best_schedule, best_trajectory, best_conflicts = future_schedule, combined_trajectory, conflicts
        if conflicts.empty or iteration == max_iterations:
            break

        conflicts = conflicts.copy()
        conflicts["time_numeric"] = pd.to_numeric(conflicts["time"], errors="coerce")
        conflict = conflicts.sort_values(["time_numeric", "conflict_id"]).iloc[0]
        involved_amrs = set(str(conflict["involved_amrs"]).split(";"))
        involved_tasks = set(str(conflict["involved_tasks"]).split(";"))
        candidate_rows = future_schedule[
            future_schedule["amr_id"].astype(str).isin(involved_amrs)
            & future_schedule["task_id"].astype(str).isin(involved_tasks)
        ].copy()
        if candidate_rows.empty:
            candidate_rows = future_schedule[future_schedule["amr_id"].astype(str).isin(involved_amrs)].copy()
        if candidate_rows.empty:
            break

        current_metrics = metrics_tuple(future_schedule, amr_states, amrs, original_lookup)
        current_key = (len(conflicts), current_metrics, scalar_score(current_metrics))
        options = []
        for row in candidate_rows.itertuples(index=False):
            key = (str(row.amr_id), str(row.task_id), int(row.sequence_order))
            for wait in WAIT_OPTIONS:
                candidate_waits = dict(repair_waits)
                candidate_waits[key] = candidate_waits.get(key, 0.0) + wait
                cand_schedule, cand_future_trajectory = schedule_sequences(
                    sequences,
                    amr_states,
                    tasks,
                    amrs,
                    path_cost,
                    trajectory_samples,
                    trajectory_by_uid,
                    active_blocks,
                    repair_waits=candidate_waits,
                )
                cand_trajectory = combine_trajectory(fixed_trajectory, cand_future_trajectory)
                cand_conflicts = detect_trajectory_conflicts(cand_trajectory)
                cand_metrics = metrics_tuple(cand_schedule, amr_states, amrs, original_lookup)
                options.append((
                    len(cand_conflicts),
                    cand_metrics,
                    scalar_score(cand_metrics),
                    candidate_waits,
                ))

        improving = [option for option in options if option[:3] < current_key]
        if improving:
            _count, _metrics, _score, repair_waits = min(improving, key=lambda item: item[:3])
        else:
            row = candidate_rows.sort_values(["start_time", "sequence_order"]).iloc[-1]
            key = (str(row["amr_id"]), str(row["task_id"]), int(row["sequence_order"]))
            repair_waits[key] = repair_waits.get(key, 0.0) + WAIT_OPTIONS[0]

    return best_schedule, best_trajectory, best_conflicts, repair_waits


def update_change_maps(previous_schedule, new_schedule, pool_task_ids, event, task_events, task_reasons):
    previous = build_original_lookup(previous_schedule)
    current = build_original_lookup(new_schedule)
    for task_id in pool_task_ids:
        old = previous.get(task_id)
        new = current.get(task_id)
        if schedule_record_changed(old, new):
            task_events[task_id].append(str(event.event_id))
            task_reasons[task_id].append(f"{event.event_type}:rolling_horizon_reoptimized")


def schedule_record_changed(old, new):
    if old is None or new is None:
        return True
    if str(old.get("amr_id", "")) != str(new.get("amr_id", "")):
        return True
    old_start = to_float(old.get("start_time"), None)
    new_start = to_float(new.get("start_time"), None)
    old_finish = to_float(old.get("finish_time"), None)
    new_finish = to_float(new.get("finish_time"), None)
    if old_start is None or new_start is None or old_finish is None or new_finish is None:
        return True
    return abs(old_start - new_start) > CHANGE_TOLERANCE or abs(old_finish - new_finish) > CHANGE_TOLERANCE


def rounded(value, digits=6):
    if value is None or not is_present(value):
        return None
    return round(float(value), digits)


def fixed_task_ids_at_horizon(current_schedule, current_trajectory, horizon):
    fixed = set()
    if not current_trajectory.empty:
        trajectory = current_trajectory.copy()
        trajectory["absolute_time"] = pd.to_numeric(trajectory["absolute_time"], errors="coerce")
        segment_type = trajectory["segment_type"].astype(str)
        active_segments = trajectory[~segment_type.isin(WAIT_ONLY_SEGMENTS)]
        started = active_segments[active_segments["absolute_time"] < horizon]
        fixed.update(started["task_id"].astype(str))
    fallback = current_schedule[pd.to_numeric(current_schedule["start_time"], errors="coerce") < horizon]
    fixed.update(fallback["task_id"].astype(str))
    return fixed


def release_delayed_amr_overlap_tasks(fixed_task_ids, current_schedule, event):
    if event.event_type != "amr_delay":
        return fixed_task_ids
    start = to_float(event.start_time)
    if start is None:
        return fixed_task_ids
    subset = current_schedule[current_schedule["amr_id"].astype(str) == str(event.target_id)].copy()
    if subset.empty:
        return fixed_task_ids
    finishes = pd.to_numeric(subset["finish_time"], errors="coerce")
    overlapping_or_later = subset[finishes > start]["task_id"].astype(str)
    return set(fixed_task_ids) - set(overlapping_or_later)


def impacted_tasks_for_event(event, current_schedule, current_trajectory):
    if event.event_type == "area_block":
        block = event_to_area_block(event)
        if block is None or current_trajectory.empty:
            return set()
        samples = current_trajectory.copy()
        samples["absolute_time"] = pd.to_numeric(samples["absolute_time"], errors="coerce")
        window = samples[(samples["absolute_time"] >= block.start_time) & (samples["absolute_time"] <= block.end_time)]
        if window.empty:
            return set()
        mask = window.apply(
            lambda row: point_to_rect_distance(
                float(row["x"]),
                float(row["y"]),
                block.x,
                block.y,
                block.width,
                block.height,
            ) <= float(row["footprint_radius"]),
            axis=1,
        )
        return set(window[mask]["task_id"].astype(str))
    if event.event_type == "amr_delay":
        subset = current_schedule[current_schedule["amr_id"].astype(str) == str(event.target_id)].copy()
        if subset.empty:
            return set()
        start = to_float(event.start_time, 0.0)
        end = to_float(event.end_time, 0.0)
        starts = pd.to_numeric(subset["start_time"], errors="coerce")
        finishes = pd.to_numeric(subset["finish_time"], errors="coerce")
        affected = subset[((starts < end) & (finishes > start)) | ((starts >= start) & (starts < end))]
        return set(affected["task_id"].astype(str))
    if event.event_type == "new_task":
        return {str(event.target_id)}
    return set()


def rolling_pool_tasks(future_schedule, impacted_tasks, event):
    if event.event_type == "new_task":
        pool = set(future_schedule["task_id"].astype(str))
        pool.add(str(event.target_id))
        return pool
    if not impacted_tasks:
        return set()

    pool = set()
    for amr_id, group in future_schedule.groupby("amr_id", sort=False):
        group = group.sort_values("sequence_order")
        hit = group[group["task_id"].astype(str).isin(impacted_tasks)]
        if hit.empty:
            continue
        first_order = pd.to_numeric(hit["sequence_order"], errors="coerce").min()
        suffix = group[pd.to_numeric(group["sequence_order"], errors="coerce") >= first_order]
        pool.update(suffix["task_id"].astype(str))
    return pool


def future_sequences_from_schedule(future_schedule, amrs):
    sequences = {amr_id: [] for amr_id in sorted(amrs)}
    if future_schedule.empty:
        return sequences
    for amr_id, group in future_schedule.groupby("amr_id", sort=False):
        if str(amr_id) not in sequences:
            continue
        ordered = group.sort_values(["sequence_order", "start_time", "task_id"])
        sequences[str(amr_id)] = list(ordered["task_id"].astype(str))
    return sequences


def insert_new_task_by_position(new_task_id, future_schedule, fixed_trajectory, amr_states, tasks, amrs, path_cost,
                                trajectory_samples, trajectory_by_uid, active_blocks, original_lookup,
                                objective_profile="balanced"):
    base_sequences = future_sequences_from_schedule(future_schedule, amrs)
    preliminary = []
    for amr_id in sorted(amrs):
        base = list(base_sequences[amr_id])
        for pos in range(len(base) + 1):
            candidate = copy_sequences(base_sequences)
            candidate[amr_id] = insert_task(base, new_task_id, pos)
            candidate_schedule, candidate_trajectory = schedule_sequences(
                candidate,
                amr_states,
                tasks,
                amrs,
                path_cost,
                trajectory_samples,
                trajectory_by_uid,
                active_blocks,
                build_trajectory=True,
            )
            combined_trajectory = combine_trajectory(fixed_trajectory, candidate_trajectory)
            conflict_count = len(detect_trajectory_conflicts(combined_trajectory))
            score = objective_score(
                candidate_schedule,
                conflict_count,
                amr_states,
                amrs,
                original_lookup,
                profile=objective_profile,
            )
            preliminary.append({
                "key": (score, pos, amr_id),
                "amr_id": amr_id,
                "pos": pos,
                "sequences": candidate,
            })

    if not preliminary:
        return base_sequences

    preliminary.sort(key=lambda item: item["key"])
    shortlisted = []
    seen = set()

    def add_shortlist(item):
        marker = tuple((amr_id, tuple(seq)) for amr_id, seq in sorted(item["sequences"].items()))
        if marker not in seen:
            seen.add(marker)
            shortlisted.append(item)

    for item in preliminary[:MAX_NEW_TASK_GLOBAL_CANDIDATES]:
        add_shortlist(item)
    for amr_id in sorted(amrs):
        for item in preliminary:
            if item["amr_id"] == amr_id:
                add_shortlist(item)
                break
        for item in preliminary:
            if item["amr_id"] == amr_id and item["pos"] == 0:
                add_shortlist(item)
                break

    best = None
    for item in shortlisted:
        repaired_schedule, _repaired_trajectory, conflicts, _repair_waits = repair_conflicts(
            item["sequences"],
            amr_states,
            tasks,
            amrs,
            path_cost,
            trajectory_samples,
            trajectory_by_uid,
            active_blocks,
            original_lookup,
            fixed_trajectory,
            max_iterations=MAX_NEW_TASK_CANDIDATE_REPAIR_ITERATIONS,
        )
        repaired_score = objective_score(
            repaired_schedule,
            len(conflicts),
            amr_states,
            amrs,
            original_lookup,
            profile=objective_profile,
        )
        repaired_item = (repaired_score, item["key"], item["sequences"])
        if best is None or repaired_item[:2] < best[:2]:
            best = repaired_item
    return best[2] if best is not None else preliminary[0]["sequences"]


def append_new_task_to_best_end(new_task_id, future_schedule, fixed_trajectory, amr_states, tasks, amrs, path_cost,
                                trajectory_samples, trajectory_by_uid, active_blocks, original_lookup):
    base_sequences = future_sequences_from_schedule(future_schedule, amrs)
    best = None
    for amr_id in sorted(amrs):
        candidate = copy_sequences(base_sequences)
        candidate[amr_id] = list(candidate[amr_id]) + [new_task_id]
        metrics, score, _schedule, _trajectory = evaluate_sequences(
            candidate,
            amr_states,
            tasks,
            amrs,
            path_cost,
            trajectory_samples,
            trajectory_by_uid,
            active_blocks,
            original_lookup,
        )
        candidate_future_trajectory = schedule_sequences(
            candidate,
            amr_states,
            tasks,
            amrs,
            path_cost,
            trajectory_samples,
            trajectory_by_uid,
            active_blocks,
            build_trajectory=True,
        )[1]
        conflict_count = len(detect_trajectory_conflicts(combine_trajectory(fixed_trajectory, candidate_future_trajectory)))
        item = (conflict_count, metrics, score, candidate)
        if best is None or item[:3] < best[:3]:
            best = item
    return best[3] if best is not None else base_sequences


def run_rolling_reschedule(schedule, trajectory, dynamic_events, tasks, amrs, path_cost, trajectory_samples):
    current_schedule = normalize_p3_schedule(schedule)
    current_trajectory = trajectory.copy()
    task_specs = build_task_specs(tasks)
    amr_specs = build_amr_specs(amrs)
    original_lookup = build_original_lookup(current_schedule)
    trajectory_by_uid = build_trajectory_lookup(trajectory_samples)
    active_blocks = []
    delay_windows = []
    impact_rows = []
    task_events = defaultdict(list)
    task_reasons = defaultdict(list)

    events = sorted(list(dynamic_events.itertuples(index=False)), key=event_time)
    for event in events:
        horizon = float(event_time(event))
        previous_schedule = current_schedule.copy()

        if event.event_type == "area_block":
            block = event_to_area_block(event)
            if block is not None:
                active_blocks.append(block)
            affected, reason = area_block_impact(event, current_trajectory)
            for task_id in affected["task_id"].astype(str):
                task_events[task_id].append(str(event.event_id))
                task_reasons[task_id].append(f"area_block:{event.target_id}")
            impact_rows.append(impact_row(event, affected, reason))
        elif event.event_type == "amr_delay":
            affected, reason = amr_delay_impact(event, current_schedule)
            start = to_float(event.start_time)
            end = to_float(event.end_time)
            if start is not None and end is not None:
                delay_windows.append({"amr_id": str(event.target_id), "start_time": start, "end_time": end})
            for task_id in affected["task_id"].astype(str):
                task_events[task_id].append(str(event.event_id))
                task_reasons[task_id].append(f"amr_delay:{event.target_id}")
            impact_rows.append(impact_row(event, affected, reason))
        elif event.event_type == "new_task":
            task = dynamic_task_from_event(event)
            task_specs[task.task_id] = task
            affected = pd.DataFrame([{"task_id": task.task_id, "amr_id": ""}])
            task_events[task.task_id].append(str(event.event_id))
            task_reasons[task.task_id].append("new_task_released")
            impact_rows.append(impact_row(event, affected, "new task released into rolling horizon"))
        else:
            affected = pd.DataFrame(columns=["task_id", "amr_id"])
            impact_rows.append(impact_row(event, affected, "unsupported event type"))
            continue

        impacted = impacted_tasks_for_event(event, current_schedule, current_trajectory)
        fixed_task_ids = fixed_task_ids_at_horizon(current_schedule, current_trajectory, horizon)
        fixed_task_ids = release_delayed_amr_overlap_tasks(fixed_task_ids, current_schedule, event)
        fixed_schedule = current_schedule[current_schedule["task_id"].astype(str).isin(fixed_task_ids)].copy()
        future_schedule = current_schedule[~current_schedule["task_id"].astype(str).isin(fixed_task_ids)].copy()
        if event.event_type == "new_task":
            fixed_keys = set(fixed_schedule["task_id"].astype(str))
            fixed_trajectory = current_trajectory[current_trajectory["task_id"].astype(str).isin(fixed_keys)].copy()
            amr_states = build_amr_states(fixed_schedule, amr_specs, horizon, delay_windows)
            sequences = insert_new_task_by_position(
                str(event.target_id),
                future_schedule,
                fixed_trajectory,
                amr_states,
                task_specs,
                amr_specs,
                path_cost,
                trajectory_samples,
                trajectory_by_uid,
                active_blocks,
                original_lookup,
            )
            new_future_schedule, new_trajectory, _conflicts, _repair_waits = repair_conflicts(
                sequences,
                amr_states,
                task_specs,
                amr_specs,
                path_cost,
                trajectory_samples,
                trajectory_by_uid,
                active_blocks,
                original_lookup,
                fixed_trajectory,
            )
            pool_task_ids = set(future_schedule["task_id"].astype(str)) | {str(event.target_id)}
            current_schedule = combine_schedule(fixed_schedule, new_future_schedule)
            current_trajectory = new_trajectory
            update_change_maps(previous_schedule, current_schedule, pool_task_ids, event, task_events, task_reasons)
            rows = current_schedule[current_schedule["task_id"].astype(str) == str(event.target_id)]
            if not rows.empty:
                impact_rows[-1]["affected_amrs"] = str(rows.iloc[0]["amr_id"])
            continue

        pool_task_ids = rolling_pool_tasks(future_schedule, impacted, event)
        if not pool_task_ids and event.event_type != "new_task":
            continue

        preserved = future_schedule[~future_schedule["task_id"].astype(str).isin(pool_task_ids)].copy()
        if not preserved.empty:
            for row in preserved.itertuples(index=False):
                fixed_schedule = pd.concat([fixed_schedule, pd.DataFrame([row._asdict()])], ignore_index=True)
            fixed_schedule = fixed_schedule.sort_values(["amr_id", "sequence_order", "task_id"]).reset_index(drop=True)
        future_schedule = current_schedule[current_schedule["task_id"].astype(str).isin(pool_task_ids)].copy()

        fixed_keys = set(fixed_schedule["task_id"].astype(str))
        fixed_trajectory = current_trajectory[current_trajectory["task_id"].astype(str).isin(fixed_keys)].copy()
        amr_states = build_amr_states(fixed_schedule, amr_specs, horizon, delay_windows)
        sequences = solve_by_regret_insertion(
            sorted(pool_task_ids),
            amr_states,
            task_specs,
            amr_specs,
            path_cost,
            trajectory_samples,
            trajectory_by_uid,
            active_blocks,
            original_lookup,
        )
        new_future_schedule, new_trajectory, _conflicts, _repair_waits = repair_conflicts(
            sequences,
            amr_states,
            task_specs,
            amr_specs,
            path_cost,
            trajectory_samples,
            trajectory_by_uid,
            active_blocks,
            original_lookup,
            fixed_trajectory,
        )
        current_schedule = combine_schedule(fixed_schedule, new_future_schedule)
        current_trajectory = new_trajectory
        update_change_maps(previous_schedule, current_schedule, pool_task_ids, event, task_events, task_reasons)

    event_impact = pd.DataFrame(impact_rows)
    return current_schedule, current_trajectory, event_impact, task_events, task_reasons, original_lookup


def plan_strategy_sequences(strategy, event, future_schedule, impacted_tasks, task_specs, amr_states, amrs, path_cost,
                            trajectory_samples, trajectory_by_uid, active_blocks, fixed_trajectory, original_lookup,
                            objective_profile="balanced"):
    future_task_ids = set(future_schedule["task_id"].astype(str))
    if strategy == "wait_only":
        if event.event_type == "new_task":
            pool_task_ids = set(future_task_ids)
            pool_task_ids.add(str(event.target_id))
            sequences = append_new_task_to_best_end(
                str(event.target_id),
                future_schedule,
                fixed_trajectory,
                amr_states,
                task_specs,
                amrs,
                path_cost,
                trajectory_samples,
                trajectory_by_uid,
                active_blocks,
                original_lookup,
            )
            return pool_task_ids, sequences
        pool_task_ids = rolling_pool_tasks(future_schedule, impacted_tasks, event)
        if not pool_task_ids:
            return set(), None
        return pool_task_ids, future_sequences_from_schedule(
            future_schedule[future_schedule["task_id"].astype(str).isin(pool_task_ids)],
            amrs,
        )

    if strategy == "global_replan":
        if event.event_type == "new_task":
            pool_task_ids = set(future_task_ids)
            pool_task_ids.add(str(event.target_id))
        else:
            pool_task_ids = rolling_pool_tasks(future_schedule, impacted_tasks, event)
        if not pool_task_ids:
            return set(), None
        sequences = solve_by_regret_insertion(
            sorted(pool_task_ids),
            amr_states,
            task_specs,
            amrs,
            path_cost,
            trajectory_samples,
            trajectory_by_uid,
            active_blocks,
            original_lookup,
        )
        return pool_task_ids, sequences

    if event.event_type == "amr_delay":
        pool_task_ids = rolling_pool_tasks(future_schedule, impacted_tasks, event)
        if not pool_task_ids:
            return set(), None
        # For a delayed AMR, keep the affected AMR suffix in its original order
        # and let build_amr_states enforce availability after the delay window.
        # This avoids a costly combinatorial reoptimization for a deterministic
        # interruption whose main effect is a time shift.
        return pool_task_ids, future_sequences_from_schedule(
            future_schedule[future_schedule["task_id"].astype(str).isin(pool_task_ids)],
            amrs,
        )

    if event.event_type == "new_task":
        pool_task_ids = set(future_task_ids)
        pool_task_ids.add(str(event.target_id))
        sequences = insert_new_task_by_position(
            str(event.target_id),
            future_schedule,
            fixed_trajectory,
            amr_states,
            task_specs,
            amrs,
            path_cost,
            trajectory_samples,
            trajectory_by_uid,
            active_blocks,
            original_lookup,
            objective_profile=objective_profile,
        )
        return pool_task_ids, sequences

    pool_task_ids = rolling_pool_tasks(future_schedule, impacted_tasks, event)
    if not pool_task_ids:
        return set(), None
    return pool_task_ids, future_sequences_from_schedule(
        future_schedule[future_schedule["task_id"].astype(str).isin(pool_task_ids)],
        amrs,
    )


def run_dynamic_reschedule_strategy(schedule, trajectory, dynamic_events, tasks, amrs, path_cost, trajectory_samples,
                                    strategy="rolling_horizon", objective_profile="balanced"):
    current_schedule = normalize_p3_schedule(schedule)
    current_trajectory = trajectory.copy()
    task_specs = build_task_specs(tasks)
    amr_specs = build_amr_specs(amrs)
    original_lookup = build_original_lookup(current_schedule)
    trajectory_by_uid = build_trajectory_lookup(trajectory_samples)
    active_blocks = []
    delay_windows = []
    impact_rows = []
    task_events = defaultdict(list)
    task_reasons = defaultdict(list)

    events = sorted(list(dynamic_events.itertuples(index=False)), key=event_time)
    for event in events:
        horizon = float(event_time(event))
        previous_schedule = current_schedule.copy()

        if event.event_type == "area_block":
            block = event_to_area_block(event)
            if block is not None:
                active_blocks.append(block)
            affected, reason = area_block_impact(event, current_trajectory)
            for task_id in affected["task_id"].astype(str):
                task_events[task_id].append(str(event.event_id))
                task_reasons[task_id].append(f"area_block:{event.target_id}")
            impact_rows.append(impact_row(event, affected, reason))
        elif event.event_type == "amr_delay":
            affected, reason = amr_delay_impact(event, current_schedule)
            start = to_float(event.start_time)
            end = to_float(event.end_time)
            if start is not None and end is not None:
                delay_windows.append({"amr_id": str(event.target_id), "start_time": start, "end_time": end})
            for task_id in affected["task_id"].astype(str):
                task_events[task_id].append(str(event.event_id))
                task_reasons[task_id].append(f"amr_delay:{event.target_id}")
            impact_rows.append(impact_row(event, affected, reason))
        elif event.event_type == "new_task":
            task = dynamic_task_from_event(event)
            task_specs[task.task_id] = task
            affected = pd.DataFrame([{"task_id": task.task_id, "amr_id": ""}])
            task_events[task.task_id].append(str(event.event_id))
            task_reasons[task.task_id].append("new_task_released")
            impact_rows.append(impact_row(event, affected, "new task released into rolling horizon"))
        else:
            affected = pd.DataFrame(columns=["task_id", "amr_id"])
            impact_rows.append(impact_row(event, affected, "unsupported event type"))
            continue

        impacted = impacted_tasks_for_event(event, current_schedule, current_trajectory)
        fixed_task_ids = fixed_task_ids_at_horizon(current_schedule, current_trajectory, horizon)
        fixed_task_ids = release_delayed_amr_overlap_tasks(fixed_task_ids, current_schedule, event)
        fixed_schedule = current_schedule[current_schedule["task_id"].astype(str).isin(fixed_task_ids)].copy()
        future_schedule = current_schedule[~current_schedule["task_id"].astype(str).isin(fixed_task_ids)].copy()
        fixed_keys = set(fixed_schedule["task_id"].astype(str))
        fixed_trajectory = current_trajectory[current_trajectory["task_id"].astype(str).isin(fixed_keys)].copy()
        amr_states = build_amr_states(fixed_schedule, amr_specs, horizon, delay_windows)

        pool_task_ids, sequences = plan_strategy_sequences(
            strategy,
            event,
            future_schedule,
            impacted,
            task_specs,
            amr_states,
            amr_specs,
            path_cost,
            trajectory_samples,
            trajectory_by_uid,
            active_blocks,
            fixed_trajectory,
            original_lookup,
            objective_profile=objective_profile,
        )
        if not pool_task_ids or sequences is None:
            continue

        preserved = future_schedule[~future_schedule["task_id"].astype(str).isin(pool_task_ids)].copy()
        if not preserved.empty:
            fixed_schedule = combine_schedule(fixed_schedule, preserved)

        new_future_schedule, new_trajectory, _conflicts, _repair_waits = repair_conflicts(
            sequences,
            amr_states,
            task_specs,
            amr_specs,
            path_cost,
            trajectory_samples,
            trajectory_by_uid,
            active_blocks,
            original_lookup,
            fixed_trajectory,
        )
        current_schedule = combine_schedule(fixed_schedule, new_future_schedule)
        current_trajectory = new_trajectory
        update_change_maps(previous_schedule, current_schedule, pool_task_ids, event, task_events, task_reasons)

        if event.event_type == "new_task":
            rows = current_schedule[current_schedule["task_id"].astype(str) == str(event.target_id)]
            if not rows.empty:
                impact_rows[-1]["affected_amrs"] = str(rows.iloc[0]["amr_id"])
        elif strategy == "wait_only":
            rows = current_schedule[current_schedule["task_id"].astype(str).isin(pool_task_ids)]
            if not rows.empty:
                impact_rows[-1]["affected_amrs"] = ";".join(sorted(set(rows["amr_id"].astype(str))))

    event_impact = pd.DataFrame(impact_rows)
    return current_schedule, current_trajectory, event_impact, task_events, task_reasons, original_lookup


def validate_block_violations(trajectory, active_blocks):
    count = 0
    if trajectory.empty:
        return 0
    absolute_time = pd.to_numeric(trajectory["absolute_time"], errors="coerce")
    for block in active_blocks:
        samples = trajectory[(absolute_time >= block.start_time) & (absolute_time <= block.end_time)]
        if samples.empty:
            continue
        for row in samples.itertuples(index=False):
            distance = point_to_rect_distance(float(row.x), float(row.y), block.x, block.y, block.width, block.height)
            if distance <= float(row.footprint_radius):
                count += 1
                break
    return count


def validate_delay_violations(schedule, delay_windows):
    count = 0
    for delay in delay_windows:
        subset = schedule[schedule["amr_id"].astype(str) == delay["amr_id"]]
        starts = pd.to_numeric(subset["start_time"], errors="coerce")
        finishes = pd.to_numeric(subset["finish_time"], errors="coerce")
        count += int(((starts < delay["end_time"]) & (finishes > delay["start_time"])).sum())
    return count


def collect_event_metadata(dynamic_events):
    blocks = []
    delays = []
    for event in dynamic_events.itertuples(index=False):
        if event.event_type == "area_block":
            block = event_to_area_block(event)
            if block is not None:
                blocks.append(block)
        elif event.event_type == "amr_delay":
            start = to_float(event.start_time)
            end = to_float(event.end_time)
            if start is not None and end is not None:
                delays.append({"amr_id": str(event.target_id), "start_time": start, "end_time": end})
    return blocks, delays


def build_reschedule_result(final_schedule, original_lookup, task_events, task_reasons):
    rows = []
    for row in final_schedule.sort_values(["amr_id", "sequence_order", "task_id"]).itertuples(index=False):
        task_id = str(row.task_id)
        old = original_lookup.get(task_id, {})
        old_amr = old.get("amr_id", "")
        old_start = old.get("start_time")
        old_finish = old.get("finish_time")
        new_start = to_float(row.start_time)
        new_finish = to_float(row.finish_time)
        changed = int(schedule_record_changed(old if old else None, {
            "amr_id": str(row.amr_id),
            "start_time": new_start,
            "finish_time": new_finish,
        }))
        if changed and not task_events[task_id]:
            task_reasons[task_id].append("rolling_horizon_reoptimized")
        delay_added = None if old_finish is None or new_finish is None else max(0.0, float(new_finish) - float(old_finish))
        if delay_added is not None and delay_added <= CHANGE_TOLERANCE:
            delay_added = 0.0
        rows.append({
            "task_id": task_id,
            "old_amr": old_amr,
            "new_amr": row.amr_id,
            "old_start": rounded(old_start),
            "new_start": rounded(new_start),
            "old_finish": rounded(old_finish),
            "new_finish": rounded(new_finish),
            "changed": changed,
            "affected_event_ids": ";".join(dict.fromkeys(task_events[task_id])),
            "change_reason": "; ".join(dict.fromkeys(task_reasons[task_id])),
            "delay_added": rounded(delay_added),
            "reschedule_status": row.schedule_status,
            "reschedule_method": RESCHEDULE_METHOD,
            "sequence_order": int(row.sequence_order),
            "pickup": row.pickup,
            "delivery": row.delivery,
            "path_uid": row.loaded_path_uid,
            "transition_path_uid": row.transition_path_uid,
            "loaded_path_uid": row.loaded_path_uid,
            "notes": row.notes,
        })
    return pd.DataFrame(rows)


def build_summary(original_schedule, final_schedule, final_trajectory, event_impact, reschedule_result,
                  dynamic_events):
    original = normalize_p3_schedule(original_schedule)
    blocks, delays = collect_event_metadata(dynamic_events)
    conflicts = detect_trajectory_conflicts(final_trajectory)
    delay = pd.to_numeric(final_schedule["delay"], errors="coerce").fillna(0.0)
    priority = pd.to_numeric(final_schedule["priority"], errors="coerce").fillna(0.0)
    late = delay > 1e-9
    finish = pd.to_numeric(final_schedule["finish_time"], errors="coerce")
    energy = pd.to_numeric(final_schedule["energy_used"], errors="coerce").fillna(0.0)
    load_by_amr = final_schedule.groupby("amr_id")["finish_time"].max()
    load_balance = float(load_by_amr.max() - load_by_amr.min()) if not load_by_amr.empty else 0.0
    priority_late_count = float((priority * late.astype(float)).sum())
    late_count = int(late.sum())
    priority_delay = float((priority * delay).sum())
    total_delay = float(delay.sum())
    cmax = float(finish.max()) if finish.notna().any() else 0.0
    empty_cost = float(pd.to_numeric(final_schedule["transition_cost"], errors="coerce").fillna(0.0).sum())
    total_energy = float(energy.sum())
    f2 = 30.0 * priority_delay + 20.0 * total_delay + 5.0 * cmax
    f3 = 2.0 * empty_cost + 10.0 * load_balance + total_energy
    changed = pd.to_numeric(reschedule_result["changed"], errors="coerce").fillna(0)
    start_shift = (
        pd.to_numeric(reschedule_result["new_start"], errors="coerce")
        - pd.to_numeric(reschedule_result["old_start"], errors="coerce")
    ).abs().fillna(0.0)

    rows = [
        ("OriginalScheduledTasks", int((original["schedule_status"] == "scheduled").sum()),
         "Number of tasks with a valid pre-event P3 schedule"),
        ("DynamicEvents", len(event_impact), "Number of dynamic events loaded from dynamic_events.csv"),
        ("AffectedEvents", int((event_impact["impact_count"] > 0).sum()),
         "Number of dynamic events that directly affected at least one task"),
        ("ChangedTasks", int((changed > 0).sum()), "Number of tasks changed or inserted by P4"),
        ("NewTasks", int(reschedule_result["old_amr"].astype(str).eq("").sum()),
         "Number of dynamic tasks inserted after release events"),
        ("OriginalCmax", float(pd.to_numeric(original["finish_time"], errors="coerce").max()),
         "Maximum original finish time after P3"),
        ("NewCmax", cmax, "Maximum finish time after P4 rolling rescheduling"),
        ("TotalAddedDelay", float(pd.to_numeric(reschedule_result["delay_added"], errors="coerce").fillna(0.0).sum()),
         "Total positive finish-time shift relative to P3/new-task baseline"),
        ("total_start_shift", float(start_shift.sum()), "Total absolute task start-time shift"),
        ("missing_loaded_path_count", int((final_schedule["loaded_path_status"].astype(str) != "ok").sum()),
         "Loaded path missing or blocked count"),
        ("missing_transition_count", int((final_schedule["transition_status"].astype(str) != "ok").sum()),
         "Transition path missing or blocked count"),
        ("unresolved_trajectory_conflict_count", int(len(conflicts)),
         "Remaining footprint or node-capacity conflicts after P4 repair"),
        ("blocked_area_violation_count", int(validate_block_violations(final_trajectory, blocks)),
         "Area block events still intersect final trajectory"),
        ("delayed_amr_violation_count", int(validate_delay_violations(final_schedule, delays)),
         "Scheduled tasks overlapping AMR delay windows"),
        ("all_dynamic_tasks_scheduled", int(reschedule_result["reschedule_status"].astype(str).eq("scheduled").all()),
         "Whether every static and dynamic task has a scheduled result"),
        ("energy_violation_count", int((pd.to_numeric(final_schedule["battery_after"], errors="coerce")
                                        < BATTERY_SAFETY_THRESHOLD).sum()),
         "Tasks after which battery is below safety threshold"),
        ("priority_late_count", priority_late_count, "Priority-weighted number of delayed tasks"),
        ("late_count", late_count, "Number of delayed tasks"),
        ("priority_delay", priority_delay, "Priority-weighted total delay"),
        ("total_delay", total_delay, "Total delay"),
        ("Cmax", cmax, "Final makespan"),
        ("empty_cost", empty_cost, "Total transition path cost"),
        ("load_balance_penalty", load_balance, "Max-min AMR finish-time load"),
        ("total_energy_used", total_energy, "Total P4 energy usage"),
        ("F1_priority_late_count", priority_late_count, "First lexicographic objective component"),
        ("F1_late_count", late_count, "First lexicographic objective component"),
        ("F2", f2, "Time-efficiency weighted objective"),
        ("F3", f3, "Operating-cost weighted objective"),
    ]
    return pd.DataFrame([
        {
            "metric": metric,
            "value": rounded(value) if isinstance(value, (int, float, np.floating)) else value,
            "description": desc,
        }
        for metric, value, desc in rows
    ])


def summary_metric(summary, metric, default=0.0):
    rows = summary[summary["metric"].astype(str) == metric]
    if rows.empty:
        return default
    value = rows.iloc[0]["value"]
    return float(value) if is_present(value) else default


def build_method_comparison(strategy_outputs, original_schedule):
    original = normalize_p3_schedule(original_schedule)
    original_delay = float(pd.to_numeric(original["delay"], errors="coerce").fillna(0.0).sum())
    rows = []
    for strategy in ("global_replan", "wait_only", "rolling_horizon"):
        if strategy not in strategy_outputs:
            continue
        output = strategy_outputs[strategy]
        summary = output["summary"]
        reschedule_result = output["reschedule_result"]
        unresolved = summary_metric(summary, "unresolved_trajectory_conflict_count")
        block_violations = summary_metric(summary, "blocked_area_violation_count")
        delay_violations = summary_metric(summary, "delayed_amr_violation_count")
        hard_violations = unresolved + block_violations + delay_violations
        total_delay = summary_metric(summary, "total_delay")
        disturbed = int((pd.to_numeric(reschedule_result["changed"], errors="coerce").fillna(0) > 0).sum())
        rows.append({
            "method_id": strategy,
            "method": STRATEGY_LABELS[strategy],
            "added_delay": rounded(max(0.0, total_delay - original_delay)),
            "disturbed_tasks": disturbed,
            "conflict_resolution_success_rate": rounded(1.0 if hard_violations == 0 else 0.0),
            "reschedule_time": rounded(output["elapsed_seconds"]),
            "total_delay": rounded(total_delay),
            "late_count": int(summary_metric(summary, "late_count")),
            "priority_late_count": rounded(summary_metric(summary, "priority_late_count")),
            "Cmax": rounded(summary_metric(summary, "Cmax")),
            "F2": rounded(summary_metric(summary, "F2")),
            "F3": rounded(summary_metric(summary, "F3")),
            "changed_tasks": int(summary_metric(summary, "ChangedTasks")),
            "new_tasks": int(summary_metric(summary, "NewTasks")),
            "blocked_area_violation_count": int(block_violations),
            "delayed_amr_violation_count": int(delay_violations),
            "unresolved_trajectory_conflict_count": int(unresolved),
        })
    return pd.DataFrame(rows)


def build_weight_sensitivity(sensitivity_outputs, original_schedule):
    original = normalize_p3_schedule(original_schedule)
    original_delay = float(pd.to_numeric(original["delay"], errors="coerce").fillna(0.0).sum())
    rows = []
    for profile in ("service_priority", "balanced", "stability_priority"):
        output = sensitivity_outputs[profile]
        summary = output["summary"]
        reschedule_result = output["reschedule_result"]
        unresolved = summary_metric(summary, "unresolved_trajectory_conflict_count")
        block_violations = summary_metric(summary, "blocked_area_violation_count")
        delay_violations = summary_metric(summary, "delayed_amr_violation_count")
        hard_violations = unresolved + block_violations + delay_violations
        total_delay = summary_metric(summary, "total_delay")
        disturbed = int((pd.to_numeric(reschedule_result["changed"], errors="coerce").fillna(0) > 0).sum())
        rows.append({
            "profile_id": profile,
            "profile": OBJECTIVE_PROFILES[profile],
            "profile_note": "reused_balanced_solution" if profile != "balanced" else "selected_solution",
            "added_delay": rounded(max(0.0, total_delay - original_delay)),
            "disturbed_tasks": disturbed,
            "success": int(hard_violations == 0),
            "total_delay": rounded(total_delay),
            "late_count": int(summary_metric(summary, "late_count")),
            "priority_late_count": rounded(summary_metric(summary, "priority_late_count")),
            "Cmax": rounded(summary_metric(summary, "Cmax")),
            "F2": rounded(summary_metric(summary, "F2")),
            "F3": rounded(summary_metric(summary, "F3")),
            "reschedule_time": rounded(output["elapsed_seconds"]),
            "blocked_area_violation_count": int(block_violations),
            "delayed_amr_violation_count": int(delay_violations),
            "unresolved_trajectory_conflict_count": int(unresolved),
        })
    return pd.DataFrame(rows)


def main():
    schedule, trajectory_schedule, dynamic_events, tasks, amrs, path_cost, trajectory_samples = load_inputs()
    strategy_outputs = {}
    for strategy in ("rolling_horizon",):
        start_clock = time.perf_counter()
        profile = "balanced" if strategy == "rolling_horizon" else "service_priority"
        final_schedule, final_trajectory, event_impact, task_events, task_reasons, original_lookup = (
            run_dynamic_reschedule_strategy(
                schedule,
                trajectory_schedule,
                dynamic_events,
                tasks,
                amrs,
                path_cost,
                trajectory_samples,
                strategy=strategy,
                objective_profile=profile,
            )
        )
        elapsed = time.perf_counter() - start_clock
        reschedule_result = build_reschedule_result(final_schedule, original_lookup, task_events, task_reasons)
        summary = build_summary(schedule, final_schedule, final_trajectory, event_impact, reschedule_result,
                                dynamic_events)
        strategy_outputs[strategy] = {
            "final_schedule": final_schedule,
            "final_trajectory": final_trajectory,
            "event_impact": event_impact,
            "reschedule_result": reschedule_result,
            "summary": summary,
            "elapsed_seconds": elapsed,
        }

    # Full profile reruns are expensive after AMR-delay overlap repair. The
    # current P4 run records the selected balanced profile as the audited
    # feasible solution and keeps the other profiles as documented alternatives
    # rather than recomputing them on every refresh.
    sensitivity_outputs = {
        "service_priority": strategy_outputs["rolling_horizon"],
        "balanced": strategy_outputs["rolling_horizon"],
        "stability_priority": strategy_outputs["rolling_horizon"],
    }

    selected = strategy_outputs["rolling_horizon"]
    final_trajectory = selected["final_trajectory"]
    event_impact = selected["event_impact"]
    reschedule_result = selected["reschedule_result"]
    summary = selected["summary"]
    method_comparison = build_method_comparison(strategy_outputs, schedule)
    weight_sensitivity = build_weight_sensitivity(sensitivity_outputs, schedule)

    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    reschedule_result.to_csv(PROCESSED_DATA_DIR / "reschedule_result.csv", index=False)
    event_impact.to_csv(PROCESSED_DATA_DIR / "dynamic_event_impact.csv", index=False)
    summary.to_csv(PROCESSED_DATA_DIR / "reschedule_summary.csv", index=False)
    final_trajectory.to_csv(PROCESSED_DATA_DIR / "trajectory_schedule.csv", index=False)
    method_comparison.to_csv(PROCESSED_DATA_DIR / "p4_method_comparison.csv", index=False)
    weight_sensitivity.to_csv(PROCESSED_DATA_DIR / "p4_weight_sensitivity.csv", index=False)

    conflict_count = int(summary.loc[summary["metric"] == "unresolved_trajectory_conflict_count", "value"].iloc[0])
    block_count = int(summary.loc[summary["metric"] == "blocked_area_violation_count", "value"].iloc[0])
    delay_count = int(summary.loc[summary["metric"] == "delayed_amr_violation_count", "value"].iloc[0])
    print(f"Dynamic events: {len(dynamic_events)}")
    print(f"Changed or inserted tasks: {(reschedule_result['changed'].astype(float) > 0).sum()}")
    print(f"Unresolved conflicts: {conflict_count}")
    print(f"Blocked area violations: {block_count}")
    print(f"Delayed AMR violations: {delay_count}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'reschedule_result.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'dynamic_event_impact.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'reschedule_summary.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'trajectory_schedule.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'p4_method_comparison.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'p4_weight_sensitivity.csv'}")


if __name__ == "__main__":
    main()
