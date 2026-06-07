from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"

ASSIGNMENT_METHOD = "baseline_interface_stub"


def load_inputs():
    tasks = pd.read_csv(RAW_DATA_DIR / "tasks.csv")
    amrs = pd.read_csv(RAW_DATA_DIR / "amrs.csv")
    path_cost = pd.read_csv(PROCESSED_DATA_DIR / "path_cost.csv")
    return tasks, amrs, path_cost


def build_best_path_lookup(path_cost):
    best_paths = (
        path_cost.sort_values(
            ["from_node", "to_node", "total_cost", "travel_time", "rank_by_total"]
        )
        .drop_duplicates(["from_node", "to_node"], keep="first")
    )
    return {
        (row.from_node, row.to_node): row._asdict()
        for row in best_paths.itertuples(index=False)
    }


def zero_path(from_node, to_node):
    return {
        "path_uid": "same_node",
        "path_id": "same_node",
        "travel_time": 0.0,
        "distance": 0.0,
        "total_cost": 0.0,
        "edge_sequence": "",
        "node_sequence": from_node,
    }


def get_best_path(path_lookup, from_node, to_node):
    if from_node == to_node:
        return zero_path(from_node, to_node)
    return path_lookup.get((from_node, to_node))


def path_value(path, key):
    if path is None:
        return None
    return path.get(key)


def path_number(path, key):
    value = path_value(path, key)
    if value is None:
        return None
    return float(value)


def sorted_tasks_for_assignment(tasks):
    data = tasks.copy()
    data["priority_sort"] = -data["priority"].astype(float)
    data = data.sort_values(
        ["earliest_start", "priority_sort", "latest_finish", "task_id"]
    )
    return data.drop(columns=["priority_sort"])


def choose_assignments(tasks, amrs, path_lookup):
    # TODO: replace this baseline with a MIP, CP-SAT, or metaheuristic solver.
    # Current goal: keep the P2 input/output interface usable for P3/P4.
    amr_load = {row.amr_id: 0.0 for row in amrs.itertuples(index=False)}
    amr_init = {row.amr_id: row.init_node for row in amrs.itertuples(index=False)}
    assignments = []

    for task in sorted_tasks_for_assignment(tasks).itertuples(index=False):
        candidates = []
        loaded_path = get_best_path(path_lookup, task.pickup, task.delivery)

        for amr_id, init_node in amr_init.items():
            empty_path = get_best_path(path_lookup, init_node, task.pickup)
            empty_cost = path_number(empty_path, "total_cost")
            loaded_cost = path_number(loaded_path, "total_cost")

            missing_penalty = 100000.0 if empty_path is None or loaded_path is None else 0.0
            score = (
                amr_load[amr_id]
                + (empty_cost if empty_cost is not None else 0.0)
                + (loaded_cost if loaded_cost is not None else 0.0)
                + missing_penalty
            )
            candidates.append((score, amr_id))

        _, assigned_amr = min(candidates, key=lambda item: (item[0], item[1]))
        assignments.append({"task_id": task.task_id, "amr_id": assigned_amr})

        if loaded_path is not None:
            amr_load[assigned_amr] += float(task.service_time) + float(loaded_path["travel_time"])
        else:
            amr_load[assigned_amr] += float(task.service_time)

    return pd.DataFrame(assignments)


def build_assignment_result(tasks, amrs, assignment_seed, path_lookup):
    task_map = {row.task_id: row._asdict() for row in tasks.itertuples(index=False)}
    amr_init = {row.amr_id: row.init_node for row in amrs.itertuples(index=False)}
    results = []

    merged = assignment_seed.merge(tasks, on="task_id", how="left")
    merged["priority_sort"] = -merged["priority"].astype(float)
    merged = merged.sort_values(
        ["amr_id", "earliest_start", "priority_sort", "latest_finish", "task_id"]
    )

    for amr_id, group in merged.groupby("amr_id", sort=True):
        current_node = amr_init[amr_id]
        current_time = 0.0
        predecessor_task_id = ""

        for sequence_order, row in enumerate(group.itertuples(index=False), start=1):
            task = task_map[row.task_id]
            transition_path = get_best_path(path_lookup, current_node, task["pickup"])
            loaded_path = get_best_path(path_lookup, task["pickup"], task["delivery"])

            transition_time = path_number(transition_path, "travel_time")
            loaded_time = path_number(loaded_path, "travel_time")
            transition_status = "ok" if transition_path is not None else "missing_path"
            loaded_path_status = "ok" if loaded_path is not None else "missing_path"

            if transition_time is not None and loaded_time is not None:
                arrival_pickup = current_time + transition_time
                estimated_start = max(float(task["earliest_start"]), arrival_pickup)
                estimated_finish = estimated_start + float(task["service_time"]) + loaded_time
                estimated_delay = max(0.0, estimated_finish - float(task["latest_finish"]))
                current_time = estimated_finish
            else:
                arrival_pickup = None
                estimated_start = None
                estimated_finish = None
                estimated_delay = None

            notes = []
            if transition_status != "ok":
                notes.append(f"no path from {current_node} to {task['pickup']}")
            if loaded_path_status != "ok":
                notes.append(f"no path from {task['pickup']} to {task['delivery']}")

            results.append(
                {
                    "amr_id": amr_id,
                    "assigned_amr": amr_id,
                    "task_id": task["task_id"],
                    "sequence_order": sequence_order,
                    "predecessor_task_id": predecessor_task_id,
                    "start_node": current_node,
                    "pickup": task["pickup"],
                    "delivery": task["delivery"],
                    "service_time": task["service_time"],
                    "earliest_start": task["earliest_start"],
                    "latest_finish": task["latest_finish"],
                    "priority": task["priority"],
                    "transition_path_uid": path_value(transition_path, "path_uid"),
                    "transition_path_id": path_value(transition_path, "path_id"),
                    "transition_travel_time": transition_time,
                    "transition_distance": path_number(transition_path, "distance"),
                    "transition_cost": path_number(transition_path, "total_cost"),
                    "transition_edge_sequence": path_value(transition_path, "edge_sequence"),
                    "loaded_path_uid": path_value(loaded_path, "path_uid"),
                    "loaded_path_id": path_value(loaded_path, "path_id"),
                    "loaded_travel_time": loaded_time,
                    "loaded_distance": path_number(loaded_path, "distance"),
                    "loaded_cost": path_number(loaded_path, "total_cost"),
                    "loaded_edge_sequence": path_value(loaded_path, "edge_sequence"),
                    "estimated_arrival_pickup": arrival_pickup,
                    "estimated_start_time": estimated_start,
                    "estimated_finish_time": estimated_finish,
                    "estimated_delay": estimated_delay,
                    "transition_status": transition_status,
                    "loaded_path_status": loaded_path_status,
                    "assignment_method": ASSIGNMENT_METHOD,
                    "notes": "; ".join(notes),
                }
            )

            predecessor_task_id = task["task_id"]
            current_node = task["delivery"]

    return pd.DataFrame(results)


def build_sequence_summary(assignment_result):
    rows = []
    for amr_id, group in assignment_result.groupby("amr_id", sort=True):
        finish_times = pd.to_numeric(group["estimated_finish_time"], errors="coerce")
        delays = pd.to_numeric(group["estimated_delay"], errors="coerce").fillna(0.0)
        rows.append(
            {
                "amr_id": amr_id,
                "task_count": len(group),
                "task_sequence": "->".join(group["task_id"].astype(str)),
                "missing_transition_count": int((group["transition_status"] != "ok").sum()),
                "missing_loaded_path_count": int((group["loaded_path_status"] != "ok").sum()),
                "estimated_finish_time": finish_times.max(),
                "estimated_total_delay": delays.sum(),
                "assignment_method": ASSIGNMENT_METHOD,
            }
        )
    return pd.DataFrame(rows)


def main():
    tasks, amrs, path_cost = load_inputs()
    path_lookup = build_best_path_lookup(path_cost)
    assignment_seed = choose_assignments(tasks, amrs, path_lookup)
    assignment_result = build_assignment_result(tasks, amrs, assignment_seed, path_lookup)
    sequence_summary = build_sequence_summary(assignment_result)

    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    assignment_result.to_csv(PROCESSED_DATA_DIR / "assignment_result.csv", index=False)
    sequence_summary.to_csv(PROCESSED_DATA_DIR / "amr_sequence_summary.csv", index=False)

    print(f"Tasks assigned: {len(assignment_result)}")
    print(f"AMRs used: {assignment_result['amr_id'].nunique()}")
    print(f"Missing transitions: {(assignment_result['transition_status'] != 'ok').sum()}")
    print(f"Missing loaded paths: {(assignment_result['loaded_path_status'] != 'ok').sum()}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'assignment_result.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'amr_sequence_summary.csv'}")


if __name__ == "__main__":
    main()
