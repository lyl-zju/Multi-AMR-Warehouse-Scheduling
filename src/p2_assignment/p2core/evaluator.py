"""方案评价：分层字典序目标 min lex(可行性, F1, F2, F3)。

对应命题第 10 节（目标规划优先级因子）：
    第 0 层（可行性）：衔接/载货路径缺失数、电量阈值违反数 —— 必须先压到最小；
    第 1 层 F1：priority_late_count, late_count；
    第 2 层 F2：30*priority_delay + 20*total_delay + 5*Cmax；
    第 3 层 F3：2*empty_cost + 10*load_balance_penalty + 1*total_energy。
lex_key 为 Python 元组，天然支持字典序比较。
"""

from dataclasses import dataclass, field

from . import constants as C
from .timeline import simulate_amr


@dataclass
class Solution:
    """P2 解：每台 AMR 的任务序列 + 每个任务的候选路径选择（可选）。"""
    sequences: dict                      # amr_id -> [task_id, ...]
    trans_choice: dict = field(default_factory=dict)   # task_id -> path_uid
    loaded_choice: dict = field(default_factory=dict)  # task_id -> path_uid
    method: str = ""

    def copy(self):
        return Solution(
            sequences={k: list(v) for k, v in self.sequences.items()},
            trans_choice=dict(self.trans_choice),
            loaded_choice=dict(self.loaded_choice),
            method=self.method,
        )


@dataclass
class Evaluation:
    timelines: dict          # amr_id -> AmrTimeline
    metrics: dict
    lex_key: tuple


def evaluate(data, solution: Solution) -> Evaluation:
    timelines = {}
    plc = lc = 0.0
    pdelay = tdelay = 0.0
    missing_trans = missing_loaded = energy_viol = 0
    empty_cost = loaded_cost = total_energy = total_waiting = 0.0
    empty_distance = loaded_distance = 0.0
    finish_times = []

    for amr_id in data.amr_ids:
        seq = solution.sequences.get(amr_id, [])
        tl = simulate_amr(data, data.amrs[amr_id], seq,
                          trans_choice=solution.trans_choice,
                          loaded_choice=solution.loaded_choice)
        timelines[amr_id] = tl
        finish_times.append(tl.finish_time)
        missing_trans += tl.missing_transition_count
        missing_loaded += tl.missing_loaded_path_count
        energy_viol += tl.energy_violation_count
        empty_cost += tl.empty_cost
        loaded_cost += tl.loaded_cost
        empty_distance += tl.empty_distance
        loaded_distance += tl.loaded_distance
        total_energy += tl.total_energy
        total_waiting += tl.total_waiting
        for leg in tl.legs:
            task = data.tasks[leg.task_id]
            if leg.delay > 1e-9:
                plc += task.priority
                lc += 1
                pdelay += task.priority * leg.delay
                tdelay += leg.delay

    cmax = max(finish_times) if finish_times else 0.0
    load_balance = (max(finish_times) - min(finish_times)) if finish_times else 0.0
    f2 = (C.F2_W_PRIORITY_DELAY * pdelay
          + C.F2_W_TOTAL_DELAY * tdelay
          + C.F2_W_CMAX * cmax)
    f3 = (C.F3_W_EMPTY_COST * empty_cost
          + C.F3_W_LOAD_BALANCE * load_balance
          + C.F3_W_ENERGY * total_energy)

    metrics = {
        "missing_transition_count": missing_trans,
        "missing_loaded_path_count": missing_loaded,
        "energy_violation_count": energy_viol,
        "priority_late_count": plc,
        "late_count": lc,
        "priority_delay": round(pdelay, 6),
        "total_delay": round(tdelay, 6),
        "Cmax": round(cmax, 6),
        "empty_cost": round(empty_cost, 6),
        "loaded_cost": round(loaded_cost, 6),
        "empty_distance": round(empty_distance, 6),
        "loaded_distance": round(loaded_distance, 6),
        "total_energy_used": round(total_energy, 6),
        "total_waiting": round(total_waiting, 6),
        "load_balance_penalty": round(load_balance, 6),
        "F2": round(f2, 6),
        "F3": round(f3, 6),
    }
    lex_key = (
        missing_trans + missing_loaded,
        energy_viol,
        round(plc, 9),
        round(lc, 9),
        round(f2, 6),
        round(f3, 6),
    )
    return Evaluation(timelines=timelines, metrics=metrics, lex_key=lex_key)


# 字典序 -> 标量的近似映射，仅供 regret 插入等需要做"差值"运算的启发式使用。
_LEX_SCALAR_WEIGHTS = (1e12, 1e10, 1e8, 1e6, 1e2, 1.0)


def lex_to_scalar(lex_key):
    return sum(w * v for w, v in zip(_LEX_SCALAR_WEIGHTS, lex_key))
