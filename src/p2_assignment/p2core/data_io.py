"""P2 数据加载：任务、AMR、P1 候选路径库。"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class Task:
    task_id: str
    pickup: str
    delivery: str
    service_time: float
    earliest_start: float
    latest_finish: float
    priority: float


@dataclass(frozen=True)
class Amr:
    amr_id: str
    init_node: str
    battery: float
    speed: float


def _zero_path(node):
    return {
        "path_uid": "same_node",
        "path_id": "same_node",
        "from_node": node,
        "to_node": node,
        "travel_time": 0.0,
        "distance": 0.0,
        "total_cost": 0.0,
    }


class PathLibrary:
    """按 (from_node, to_node) 索引 P1 的候选路径，提供最优路径与候选列表查询。"""

    def __init__(self, path_cost: pd.DataFrame):
        self._by_od = {}
        self._by_uid = {}
        # 二维平面图分支的 P1 输出带 planning_status，规划失败的行不可用
        if "planning_status" in path_cost.columns:
            path_cost = path_cost[path_cost["planning_status"] == "ok"]
        ordered = path_cost.sort_values(
            ["from_node", "to_node", "total_cost", "travel_time", "rank_by_total"]
        )
        for row in ordered.itertuples(index=False):
            record = row._asdict()
            self._by_od.setdefault((row.from_node, row.to_node), []).append(record)
            self._by_uid[row.path_uid] = record

    def candidates(self, from_node, to_node):
        """返回 OD 间全部候选路径（按 total_cost 升序）；同点返回零路径。"""
        if from_node == to_node:
            return [_zero_path(from_node)]
        return self._by_od.get((from_node, to_node), [])

    def best(self, from_node, to_node):
        """综合成本最优的候选路径，不存在返回 None。"""
        cands = self.candidates(from_node, to_node)
        return cands[0] if cands else None

    def by_uid(self, path_uid):
        return self._by_uid.get(path_uid)

    def exists(self, from_node, to_node):
        return bool(self.candidates(from_node, to_node))

    def has_outgoing(self, node):
        """该节点是否存在任何出发路径（用于识别 OUT1/OUT2 这类单向死端）。"""
        return any(od[0] == node for od in self._by_od)


@dataclass
class ProblemData:
    tasks: dict          # task_id -> Task
    task_ids: list       # 保持 tasks.csv 原始顺序
    amrs: dict           # amr_id -> Amr
    amr_ids: list
    paths: PathLibrary


def load_problem(raw_dir, path_cost_csv, tasks_csv=None, amrs_csv=None) -> ProblemData:
    """加载问题数据。

    path_cost_csv: P1 输出的 path_cost.csv 完整路径
                   （二维平面图分支为 data/processed/p1/<algorithm>/path_cost.csv）。
    tasks_csv/amrs_csv 可覆盖默认路径，供实验脚本换算例。
    """
    raw_dir = Path(raw_dir)
    tasks_df = pd.read_csv(tasks_csv or raw_dir / "tasks.csv")
    amrs_df = pd.read_csv(amrs_csv or raw_dir / "amrs.csv")
    path_cost = pd.read_csv(path_cost_csv)

    tasks = {
        row.task_id: Task(
            task_id=row.task_id,
            pickup=row.pickup,
            delivery=row.delivery,
            service_time=float(row.service_time),
            earliest_start=float(row.earliest_start),
            latest_finish=float(row.latest_finish),
            priority=float(row.priority),
        )
        for row in tasks_df.itertuples(index=False)
    }
    amrs = {
        row.amr_id: Amr(
            amr_id=row.amr_id,
            init_node=row.init_node,
            battery=float(row.battery),
            speed=float(row.speed),
        )
        for row in amrs_df.itertuples(index=False)
    }
    return ProblemData(
        tasks=tasks,
        task_ids=list(tasks_df["task_id"]),
        amrs=amrs,
        amr_ids=list(amrs_df["amr_id"]),
        paths=PathLibrary(path_cost),
    )
