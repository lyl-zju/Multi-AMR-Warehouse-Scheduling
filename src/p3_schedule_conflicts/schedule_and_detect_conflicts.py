import argparse
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
P2_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "p2"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "p3"
P1_ALGORITHMS = ("basic_astar", "vg", "avg", "davg")
DEFAULT_P1_ALGORITHM = "basic_astar"

SCHEDULING_METHOD = "baseline_interface_stub"
CONFLICT_METHOD = "interval_capacity_check_stub"
NODE_CAPACITY = 1
EDGE_SCHEDULE_COLUMNS = [
    "amr_id",
    "task_id",
    "sequence_order",
    "segment_type",
    "path_uid",
    "step_index",
    "edge_id",
    "from_node_on_edge",
    "to_node_on_edge",
    "start_time",
    "end_time",
    "duration",
    "capacity",
    "edge_type",
    "lockable",
]
NODE_SCHEDULE_COLUMNS = [
    "amr_id",
    "task_id",
    "sequence_order",
    "segment_type",
    "path_uid",
    "node_index",
    "node_id",
    "time",
]


def p1_data_dir(algorithm):
    return PROJECT_ROOT / "data" / "processed" / "p1" / algorithm


def parse_args():
    parser = argparse.ArgumentParser(description="Build schedules and detect conflicts using selected P1 paths.")
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
    path_cost = pd.read_csv(p1_dir / "path_cost.csv")
    path_edge_occupancy = pd.read_csv(p1_dir / "path_edge_occupancy.csv")
    path_node_occupancy = pd.read_csv(p1_dir / "path_node_occupancy.csv")
    edges = pd.read_csv(RAW_DATA_DIR / "edges.csv")
    nodes = pd.read_csv(RAW_DATA_DIR / "nodes.csv")
    return assignment, path_cost, path_edge_occupancy, path_node_occupancy, edges, nodes


def is_present(value):
    return value is not None and not pd.isna(value) and str(value) != ""


def number_or_none(value):
    if not is_present(value):
        return None
    return float(value)


def expand_edge_occupancy(path_uid, base_time, occupancy_table, context):
    if not is_present(path_uid) or path_uid == "same_node" or base_time is None:
        return []

    rows = occupancy_table[occupancy_table["path_uid"] == path_uid]
    expanded = []
    for row in rows.itertuples(index=False):
        expanded.append(
            {
                **context,
                "path_uid": path_uid,
                "step_index": row.step_index,
                "edge_id": row.edge_id,
                "from_node_on_edge": row.from_node_on_edge,
                "to_node_on_edge": row.to_node_on_edge,
                "start_time": round(base_time + float(row.offset_start), 3),
                "end_time": round(base_time + float(row.offset_end), 3),
                "duration": row.travel_time,
                "capacity": int(row.capacity),
                "edge_type": row.edge_type,
                "lockable": int(row.lockable),
            }
        )
    return expanded


def expand_node_occupancy(path_uid, base_time, occupancy_table, context):
    if not is_present(path_uid) or path_uid == "same_node" or base_time is None:
        return []

    rows = occupancy_table[occupancy_table["path_uid"] == path_uid]
    expanded = []
    for row in rows.itertuples(index=False):
        expanded.append(
            {
                **context,
                "path_uid": path_uid,
                "node_index": row.node_index,
                "node_id": row.node_id,
                "time": round(base_time + float(row.offset_time), 3),
            }
        )
    return expanded


def build_schedule(assignment, edge_occupancy, node_occupancy):
    schedule_rows = []
    edge_schedule_rows = []
    node_schedule_rows = []

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
            edge_schedule_rows.extend(
                expand_edge_occupancy(
                    row.transition_path_uid,
                    transition_start_time,
                    edge_occupancy,
                    {**task_context, "segment_type": "transition"},
                )
            )
            edge_schedule_rows.extend(
                expand_edge_occupancy(
                    row.loaded_path_uid,
                    loaded_start_time,
                    edge_occupancy,
                    {**task_context, "segment_type": "loaded"},
                )
            )
            node_schedule_rows.extend(
                expand_node_occupancy(
                    row.transition_path_uid,
                    transition_start_time,
                    node_occupancy,
                    {**task_context, "segment_type": "transition"},
                )
            )
            node_schedule_rows.extend(
                expand_node_occupancy(
                    row.loaded_path_uid,
                    loaded_start_time,
                    node_occupancy,
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
        pd.DataFrame(edge_schedule_rows, columns=EDGE_SCHEDULE_COLUMNS),
        pd.DataFrame(node_schedule_rows, columns=NODE_SCHEDULE_COLUMNS),
    )


def detect_edge_conflicts(edge_schedule):
    conflicts = []
    if edge_schedule.empty:
        return conflicts

    for edge_id, group in edge_schedule.groupby("edge_id", sort=True):
        time_points = sorted(set(group["start_time"].tolist() + group["end_time"].tolist()))
        if len(time_points) < 2:
            continue

        for start, end in zip(time_points[:-1], time_points[1:]):
            if start == end:
                continue
            active = group[(group["start_time"] < end) & (group["end_time"] > start)]
            active_amrs = sorted(set(active["amr_id"]))
            capacity_values = pd.to_numeric(active["capacity"], errors="coerce").dropna()
            capacity = int(capacity_values.min()) if not capacity_values.empty else 1
            if len(active_amrs) > capacity:
                conflicts.append(
                    {
                        "time": f"{start:g}-{end:g}",
                        "location": edge_id,
                        "location_type": "edge",
                        "involved_amrs": ";".join(active_amrs),
                        "involved_tasks": ";".join(sorted(set(active["task_id"]))),
                        "involved_path_uids": ";".join(sorted(set(active["path_uid"]))),
                        "capacity": capacity,
                        "active_count": len(active_amrs),
                        "repair_action": "not_repaired_interface_stub",
                        "status": "detected",
                        "conflict_method": CONFLICT_METHOD,
                    }
                )
    return conflicts


def detect_node_conflicts(node_schedule):
    conflicts = []
    if node_schedule.empty:
        return conflicts

    for (node_id, time), group in node_schedule.groupby(["node_id", "time"], sort=True):
        active_amrs = sorted(set(group["amr_id"]))
        if len(active_amrs) > NODE_CAPACITY:
            conflicts.append(
                {
                    "time": f"{time:g}",
                    "location": node_id,
                    "location_type": "node",
                    "involved_amrs": ";".join(active_amrs),
                    "involved_tasks": ";".join(sorted(set(group["task_id"]))),
                    "involved_path_uids": ";".join(sorted(set(group["path_uid"]))),
                    "capacity": NODE_CAPACITY,
                    "active_count": len(active_amrs),
                    "repair_action": "not_repaired_interface_stub",
                    "status": "detected",
                    "conflict_method": CONFLICT_METHOD,
                }
            )
    return conflicts


def build_conflict_log(edge_schedule, node_schedule):
    conflict_rows = detect_edge_conflicts(edge_schedule) + detect_node_conflicts(node_schedule)
    for index, conflict in enumerate(conflict_rows, start=1):
        conflict["conflict_id"] = f"C{index:03d}"

    columns = [
        "conflict_id",
        "time",
        "location",
        "location_type",
        "involved_amrs",
        "involved_tasks",
        "involved_path_uids",
        "capacity",
        "active_count",
        "repair_action",
        "status",
        "conflict_method",
    ]
    return pd.DataFrame(conflict_rows, columns=columns)


def main():
    args = parse_args()
    assignment, path_cost, path_edge_occupancy, path_node_occupancy, edges, nodes = load_inputs(args.p1_algorithm)
    schedule_result, edge_schedule, node_schedule = build_schedule(
        assignment, path_edge_occupancy, path_node_occupancy
    )
    conflict_log = build_conflict_log(edge_schedule, node_schedule)

    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    schedule_result.to_csv(PROCESSED_DATA_DIR / "schedule_result.csv", index=False)
    edge_schedule.to_csv(PROCESSED_DATA_DIR / "edge_occupancy_schedule.csv", index=False)
    node_schedule.to_csv(PROCESSED_DATA_DIR / "node_occupancy_schedule.csv", index=False)
    conflict_log.to_csv(PROCESSED_DATA_DIR / "conflict_log.csv", index=False)

    print(f"Scheduled task rows: {len(schedule_result)}")
    print(f"P1 algorithm: {args.p1_algorithm}")
    print(f"Edge occupancy rows: {len(edge_schedule)}")
    print(f"Node occupancy rows: {len(node_schedule)}")
    print(f"Detected conflicts: {len(conflict_log)}")
    print(f"Unscheduled rows: {(schedule_result['schedule_status'] != 'scheduled').sum()}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'schedule_result.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'edge_occupancy_schedule.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'node_occupancy_schedule.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'conflict_log.csv'}")


if __name__ == "__main__":
    main()
