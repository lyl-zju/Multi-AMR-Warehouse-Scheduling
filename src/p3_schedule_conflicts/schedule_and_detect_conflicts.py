import argparse
from math import hypot
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
P2_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "p2"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "p3"
P1_ALGORITHMS = ("basic_astar", "vg", "avg", "davg")
DEFAULT_P1_ALGORITHM = "basic_astar"

SCHEDULING_METHOD = "baseline_interface_stub"
CONFLICT_METHOD = "spacetime_footprint_check"
TIME_TOLERANCE = 0.25
SAFETY_MARGIN = 0.10

TRAJECTORY_SCHEDULE_COLUMNS = [
    "amr_id",
    "task_id",
    "sequence_order",
    "segment_type",
    "path_uid",
    "sample_index",
    "absolute_time",
    "offset_time",
    "x",
    "y",
    "theta",
    "speed",
    "footprint_radius",
]

CONFLICT_COLUMNS = [
    "conflict_id",
    "time",
    "location",
    "location_type",
    "involved_amrs",
    "involved_tasks",
    "involved_path_uids",
    "distance",
    "clearance_threshold",
    "time_delta",
    "repair_action",
    "status",
    "conflict_method",
]


def p1_data_dir(algorithm):
    return PROJECT_ROOT / "data" / "processed" / "p1" / algorithm


def parse_args():
    parser = argparse.ArgumentParser(description="Build schedules and detect 2D trajectory conflicts.")
    parser.add_argument(
        "--p1-algorithm",
        choices=P1_ALGORITHMS,
        default=DEFAULT_P1_ALGORITHM,
        help="P1 path planning method to read.",
    )
    return parser.parse_args()


def load_inputs(p1_algorithm=DEFAULT_P1_ALGORITHM):
    p1_dir = p1_data_dir(p1_algorithm)
    assignment = pd.read_csv(P2_DATA_DIR / "assignment_result.csv")
    trajectory_samples = pd.read_csv(p1_dir / "path_trajectory_samples.csv")
    return assignment, trajectory_samples


def is_present(value):
    return value is not None and not pd.isna(value) and str(value) != ""


def number_or_none(value):
    if not is_present(value):
        return None
    return float(value)


def expand_trajectory(path_uid, base_time, trajectory_samples, context):
    if not is_present(path_uid) or path_uid == "same_node" or base_time is None:
        return []

    rows = trajectory_samples[trajectory_samples["path_uid"] == path_uid]
    expanded = []
    for row in rows.itertuples(index=False):
        offset_time = float(row.offset_time)
        expanded.append(
            {
                **context,
                "path_uid": path_uid,
                "sample_index": int(row.sample_index),
                "absolute_time": round(base_time + offset_time, 3),
                "offset_time": offset_time,
                "x": float(row.x),
                "y": float(row.y),
                "theta": float(row.theta),
                "speed": float(row.speed),
                "footprint_radius": float(row.footprint_radius),
            }
        )
    return expanded


def build_schedule(assignment, trajectory_samples):
    schedule_rows = []
    trajectory_rows = []

    assignment = assignment.sort_values(["amr_id", "sequence_order", "task_id"])

    for amr_id, group in assignment.groupby("amr_id", sort=True):
        current_time = 0.0

        for row in group.itertuples(index=False):
            transition_ok = row.transition_status == "ok"
            loaded_ok = row.loaded_path_status == "ok"
            transition_time = number_or_none(row.transition_travel_time)
            loaded_time = number_or_none(row.loaded_travel_time)
            service_time = float(row.service_time)
            earliest_start = float(row.earliest_start)
            latest_finish = float(row.latest_finish)

            transition_start_time = current_time if transition_ok else None
            transition_finish_time = (
                transition_start_time + transition_time
                if transition_start_time is not None and transition_time is not None
                else None
            )

            if transition_ok and loaded_ok:
                arrival_pickup_time = transition_finish_time
                waiting_time = max(0.0, earliest_start - arrival_pickup_time)
                start_time = arrival_pickup_time + waiting_time
                loaded_start_time = start_time + service_time
                finish_time = loaded_start_time + loaded_time
                delay = max(0.0, finish_time - latest_finish)
                current_time = finish_time
                schedule_status = "scheduled"
            else:
                arrival_pickup_time = None
                waiting_time = None
                start_time = None
                loaded_start_time = None
                finish_time = None
                delay = None
                schedule_status = "missing_path"

            task_context = {
                "amr_id": amr_id,
                "task_id": row.task_id,
                "sequence_order": int(row.sequence_order),
            }
            trajectory_rows.extend(
                expand_trajectory(
                    row.transition_path_uid,
                    transition_start_time,
                    trajectory_samples,
                    {**task_context, "segment_type": "transition"},
                )
            )
            trajectory_rows.extend(
                expand_trajectory(
                    row.loaded_path_uid,
                    loaded_start_time,
                    trajectory_samples,
                    {**task_context, "segment_type": "loaded"},
                )
            )

            notes = []
            if not transition_ok:
                notes.append("transition path missing")
            if not loaded_ok:
                notes.append("loaded path missing")

            schedule_rows.append(
                {
                    "amr_id": amr_id,
                    "task_id": row.task_id,
                    "sequence_order": int(row.sequence_order),
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
                    "arrival_pickup_time": arrival_pickup_time,
                    "waiting_time": waiting_time,
                    "service_time": service_time,
                    "loaded_start_time": loaded_start_time,
                    "loaded_travel_time": loaded_time,
                    "latest_finish": latest_finish,
                    "delay": delay,
                    "is_delayed": int(delay is not None and delay > 0),
                    "schedule_status": schedule_status,
                    "scheduling_method": SCHEDULING_METHOD,
                    "notes": "; ".join(notes),
                }
            )

    return (
        pd.DataFrame(schedule_rows),
        pd.DataFrame(trajectory_rows, columns=TRAJECTORY_SCHEDULE_COLUMNS),
    )


def detect_trajectory_conflicts(trajectory_schedule):
    conflicts = []
    if trajectory_schedule.empty:
        return pd.DataFrame(conflicts, columns=CONFLICT_COLUMNS)

    samples = trajectory_schedule.sort_values("absolute_time").reset_index(drop=True)
    for left_index, left in samples.iterrows():
        for right_index in range(left_index + 1, len(samples)):
            right = samples.iloc[right_index]
            time_delta = float(right["absolute_time"]) - float(left["absolute_time"])
            if time_delta > TIME_TOLERANCE:
                break
            if left["amr_id"] == right["amr_id"]:
                continue

            distance = hypot(float(left["x"]) - float(right["x"]), float(left["y"]) - float(right["y"]))
            threshold = (
                float(left["footprint_radius"])
                + float(right["footprint_radius"])
                + SAFETY_MARGIN
            )
            if distance >= threshold:
                continue

            conflict_time = (float(left["absolute_time"]) + float(right["absolute_time"])) / 2.0
            location_x = (float(left["x"]) + float(right["x"])) / 2.0
            location_y = (float(left["y"]) + float(right["y"])) / 2.0
            conflicts.append(
                {
                    "time": f"{conflict_time:g}",
                    "location": f"{location_x:.2f},{location_y:.2f}",
                    "location_type": "space",
                    "involved_amrs": ";".join(sorted([str(left["amr_id"]), str(right["amr_id"])])),
                    "involved_tasks": ";".join(sorted([str(left["task_id"]), str(right["task_id"])])),
                    "involved_path_uids": ";".join(sorted([str(left["path_uid"]), str(right["path_uid"])])),
                    "distance": round(distance, 3),
                    "clearance_threshold": round(threshold, 3),
                    "time_delta": round(abs(time_delta), 3),
                    "repair_action": "not_repaired_interface_stub",
                    "status": "detected",
                    "conflict_method": CONFLICT_METHOD,
                }
            )

    for index, conflict in enumerate(conflicts, start=1):
        conflict["conflict_id"] = f"C{index:03d}"
    return pd.DataFrame(conflicts, columns=CONFLICT_COLUMNS)


def main():
    args = parse_args()
    assignment, trajectory_samples = load_inputs(args.p1_algorithm)
    schedule_result, trajectory_schedule = build_schedule(assignment, trajectory_samples)
    conflict_log = detect_trajectory_conflicts(trajectory_schedule)

    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    schedule_result.to_csv(PROCESSED_DATA_DIR / "schedule_result.csv", index=False)
    trajectory_schedule.to_csv(PROCESSED_DATA_DIR / "trajectory_schedule.csv", index=False)
    conflict_log.to_csv(PROCESSED_DATA_DIR / "conflict_log.csv", index=False)

    print(f"Scheduled task rows: {len(schedule_result)}")
    print(f"P1 algorithm: {args.p1_algorithm}")
    print(f"Trajectory sample rows: {len(trajectory_schedule)}")
    print(f"Detected conflicts: {len(conflict_log)}")
    print(f"Unscheduled rows: {(schedule_result['schedule_status'] != 'scheduled').sum()}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'schedule_result.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'trajectory_schedule.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'conflict_log.csv'}")


if __name__ == "__main__":
    main()
