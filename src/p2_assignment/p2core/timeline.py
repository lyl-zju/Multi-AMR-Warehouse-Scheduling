"""单 AMR 任务链时间表与电量链推演。

时间规则（命题第 5 节）：
    empty_time  = base_travel_time / speed
    loaded_time = base_travel_time / (speed * LOADED_SPEED_FACTOR)
电量规则（命题第 6 节）：
    空驶耗电 = 距离 * 1.0，载货耗电 = 距离 * 1.2，服务耗电 = 服务时间 * 0.2
    任意时刻剩余电量不得低于安全阈值（基础层不充电）。
"""

from dataclasses import dataclass, field

from . import constants as C


@dataclass
class Leg:
    """一个任务在链中的完整执行记录。"""
    task_id: str
    start_node: str
    pickup: str
    delivery: str
    trans_path: dict = None      # 空驶衔接候选路径记录（缺失为 None）
    loaded_path: dict = None     # 载货候选路径记录（缺失为 None）
    trans_missing: bool = False
    loaded_missing: bool = False
    trans_time: float = 0.0      # 已按 speed 修正
    loaded_time: float = 0.0     # 已按 speed 与载货系数修正
    arrival_pickup: float = 0.0
    waiting: float = 0.0
    start_time: float = 0.0
    finish_time: float = 0.0
    delay: float = 0.0
    energy_used: float = 0.0
    battery_after: float = 0.0
    battery_violation: bool = False


@dataclass
class AmrTimeline:
    amr_id: str
    legs: list = field(default_factory=list)
    finish_time: float = 0.0          # 最后一个任务完成时间（无任务为 0）
    empty_cost: float = 0.0           # 空驶综合成本（来自 total_cost）
    loaded_cost: float = 0.0
    empty_distance: float = 0.0
    loaded_distance: float = 0.0
    total_energy: float = 0.0
    total_waiting: float = 0.0
    missing_transition_count: int = 0
    missing_loaded_path_count: int = 0
    energy_violation_count: int = 0
    battery_end: float = 0.0


def _resolve_path(paths, from_node, to_node, preferred_uid):
    """优先使用指定候选路径（起终点必须匹配），否则取综合成本最优。"""
    if preferred_uid:
        record = paths.by_uid(preferred_uid)
        if record and record["from_node"] == from_node and record["to_node"] == to_node:
            return record
    return paths.best(from_node, to_node)


def compute_leg(data, amr, current_node, current_time, battery, task,
                trans_uid=None, loaded_uid=None) -> Leg:
    """从 (current_node, current_time, battery) 出发执行一个任务，返回执行记录。"""
    paths = data.paths
    trans_path = _resolve_path(paths, current_node, task.pickup, trans_uid)
    loaded_path = _resolve_path(paths, task.pickup, task.delivery, loaded_uid)

    leg = Leg(task_id=task.task_id, start_node=current_node,
              pickup=task.pickup, delivery=task.delivery,
              trans_path=trans_path, loaded_path=loaded_path,
              trans_missing=trans_path is None, loaded_missing=loaded_path is None)

    if trans_path is not None:
        trans_base, trans_dist = trans_path["travel_time"], trans_path["distance"]
    else:
        trans_base, trans_dist = C.MISSING_SURROGATE_TIME, C.MISSING_SURROGATE_DISTANCE
    if loaded_path is not None:
        loaded_base, loaded_dist = loaded_path["travel_time"], loaded_path["distance"]
    else:
        loaded_base, loaded_dist = C.MISSING_SURROGATE_TIME, C.MISSING_SURROGATE_DISTANCE

    leg.trans_time = trans_base / amr.speed
    leg.loaded_time = loaded_base / (amr.speed * C.LOADED_SPEED_FACTOR)

    leg.arrival_pickup = current_time + leg.trans_time
    leg.start_time = max(task.earliest_start, leg.arrival_pickup)
    leg.waiting = leg.start_time - leg.arrival_pickup
    leg.finish_time = leg.start_time + task.service_time + leg.loaded_time
    leg.delay = max(0.0, leg.finish_time - task.latest_finish)

    leg.energy_used = (
        trans_dist * C.EMPTY_ENERGY_RATE
        + loaded_dist * C.LOADED_ENERGY_RATE
        + task.service_time * C.SERVICE_ENERGY_RATE
    )
    leg.battery_after = battery - leg.energy_used
    leg.battery_violation = leg.battery_after < C.BATTERY_SAFETY_THRESHOLD
    return leg


def simulate_amr(data, amr, task_sequence,
                 trans_choice=None, loaded_choice=None) -> AmrTimeline:
    """推演一台 AMR 的整条任务链。

    trans_choice / loaded_choice: {task_id: path_uid}，指定候选路径；
    未指定或与当前链上起终点不匹配时回退到综合成本最优路径。
    """
    trans_choice = trans_choice or {}
    loaded_choice = loaded_choice or {}
    tl = AmrTimeline(amr_id=amr.amr_id)
    node, t, battery = amr.init_node, 0.0, amr.battery

    for task_id in task_sequence:
        task = data.tasks[task_id]
        leg = compute_leg(data, amr, node, t, battery, task,
                          trans_uid=trans_choice.get(task_id),
                          loaded_uid=loaded_choice.get(task_id))
        tl.legs.append(leg)

        if leg.trans_path is not None:
            tl.empty_cost += leg.trans_path["total_cost"]
            tl.empty_distance += leg.trans_path["distance"]
        else:
            tl.missing_transition_count += 1
        if leg.loaded_path is not None:
            tl.loaded_cost += leg.loaded_path["total_cost"]
            tl.loaded_distance += leg.loaded_path["distance"]
        else:
            tl.missing_loaded_path_count += 1

        tl.total_energy += leg.energy_used
        tl.total_waiting += leg.waiting
        if leg.battery_violation:
            tl.energy_violation_count += 1

        node, t, battery = task.delivery, leg.finish_time, leg.battery_after

    tl.finish_time = t if task_sequence else 0.0
    tl.battery_end = battery
    return tl
