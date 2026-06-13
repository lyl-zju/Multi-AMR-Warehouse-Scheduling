"""把 Solution + Evaluation 写成与原框架兼容的 P3 接口文件。

与 baseline 的列保持一致（P3 直接消费），并新增电量与等待信息列。
注意：transition_travel_time / loaded_travel_time 写入的是按 speed 与
载货速度系数修正后的实际时间（命题第 5 节要求），而非 P1 基准时间。
"""

import pandas as pd


def _path_field(path, key):
    return path.get(key) if path is not None else None


def build_assignment_result(data, solution, evaluation, method_name):
    rows = []
    for amr_id in data.amr_ids:
        tl = evaluation.timelines[amr_id]
        predecessor = ""
        for order, leg in enumerate(tl.legs, start=1):
            task = data.tasks[leg.task_id]
            missing = leg.trans_missing or leg.loaded_missing
            rows.append({
                "amr_id": amr_id,
                "assigned_amr": amr_id,
                "task_id": leg.task_id,
                "sequence_order": order,
                "predecessor_task_id": predecessor,
                "start_node": leg.start_node,
                "pickup": task.pickup,
                "delivery": task.delivery,
                "service_time": task.service_time,
                "earliest_start": task.earliest_start,
                "latest_finish": task.latest_finish,
                "priority": task.priority,
                "transition_path_uid": _path_field(leg.trans_path, "path_uid"),
                "transition_path_id": _path_field(leg.trans_path, "path_id"),
                "transition_travel_time": None if leg.trans_missing else round(leg.trans_time, 6),
                "transition_distance": _path_field(leg.trans_path, "distance"),
                "transition_cost": _path_field(leg.trans_path, "total_cost"),
                "loaded_path_uid": _path_field(leg.loaded_path, "path_uid"),
                "loaded_path_id": _path_field(leg.loaded_path, "path_id"),
                "loaded_travel_time": None if leg.loaded_missing else round(leg.loaded_time, 6),
                "loaded_distance": _path_field(leg.loaded_path, "distance"),
                "loaded_cost": _path_field(leg.loaded_path, "total_cost"),
                "estimated_arrival_pickup": None if missing else round(leg.arrival_pickup, 6),
                "estimated_start_time": None if missing else round(leg.start_time, 6),
                "estimated_finish_time": None if missing else round(leg.finish_time, 6),
                "estimated_delay": None if missing else round(leg.delay, 6),
                "estimated_waiting": None if missing else round(leg.waiting, 6),
                "energy_used": round(leg.energy_used, 6),
                "battery_after": round(leg.battery_after, 6),
                "transition_status": "missing_path" if leg.trans_missing else "ok",
                "loaded_path_status": "missing_path" if leg.loaded_missing else "ok",
                "assignment_method": method_name,
                "notes": "; ".join(
                    n for n in (
                        f"no path from {leg.start_node} to {task.pickup}" if leg.trans_missing else "",
                        f"no path from {task.pickup} to {task.delivery}" if leg.loaded_missing else "",
                        "battery below safety threshold" if leg.battery_violation else "",
                    ) if n
                ),
            })
            predecessor = leg.task_id
    return pd.DataFrame(rows)


def build_sequence_summary(data, evaluation, method_name):
    rows = []
    for amr_id in data.amr_ids:
        tl = evaluation.timelines[amr_id]
        rows.append({
            "amr_id": amr_id,
            "task_count": len(tl.legs),
            "task_sequence": "->".join(leg.task_id for leg in tl.legs),
            "missing_transition_count": tl.missing_transition_count,
            "missing_loaded_path_count": tl.missing_loaded_path_count,
            "estimated_finish_time": round(tl.finish_time, 6),
            "estimated_total_delay": round(sum(leg.delay for leg in tl.legs), 6),
            "total_energy_used": round(tl.total_energy, 6),
            "battery_end": round(tl.battery_end, 6),
            "energy_violation_count": tl.energy_violation_count,
            "assignment_method": method_name,
        })
    return pd.DataFrame(rows)
