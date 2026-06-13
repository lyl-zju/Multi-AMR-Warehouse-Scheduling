"""M1 两阶段分解法（课程第三章 + 第八章）。

阶段一：0-1 整数规划解"谁做哪些任务"（分支定界求解）
    变量 x[k,j]；目标 = 空驶接近成本 + 负载均衡 + 缺失路径惩罚；
    约束：任务唯一指派、死端送货任务（OUT）每台 AMR 至多一个、电量粗校验。
阶段二：每台 AMR 用动态规划解"按什么顺序做"
    状态 (已完成任务集合, 末任务)，状态转移由 compute_leg 推进时间与电量，
    每个状态保留字典序最优标号（Held-Karp 结构）。

分解的代价：阶段一用"初始点->取货点"近似空驶成本，忽略链式位置；
这正是与 M2 联合模型对比的实验点。
"""

import time

from p2core import Solution, compute_leg
from p2core import constants as C

MISSING_PENALTY = 1e5


def _stage1_assign(data, time_limit, msg=False):
    import pulp

    tasks, amrs = data.tasks, data.amrs
    T, K = data.task_ids, data.amr_ids
    dead_end_tasks = [j for j in T if not data.paths.has_outgoing(tasks[j].delivery)]

    cost, dur, energy, missing = {}, {}, {}, {}
    for k in K:
        for j in T:
            tj, ak = tasks[j], amrs[k]
            trans = data.paths.best(ak.init_node, tj.pickup)
            loaded = data.paths.best(tj.pickup, tj.delivery)
            miss = (trans is None) or (loaded is None)
            trans_t = (trans["travel_time"] / ak.speed) if trans else C.MISSING_SURROGATE_TIME
            loaded_t = ((loaded["travel_time"] / (ak.speed * C.LOADED_SPEED_FACTOR))
                        if loaded else C.MISSING_SURROGATE_TIME)
            cost[k, j] = (trans["total_cost"] if trans else 0.0)
            dur[k, j] = trans_t + tj.service_time + loaded_t
            energy[k, j] = ((trans["distance"] if trans else C.MISSING_SURROGATE_DISTANCE)
                            * C.EMPTY_ENERGY_RATE
                            + (loaded["distance"] if loaded else C.MISSING_SURROGATE_DISTANCE)
                            * C.LOADED_ENERGY_RATE
                            + tj.service_time * C.SERVICE_ENERGY_RATE)
            missing[k, j] = miss

    prob = pulp.LpProblem("p2_stage1_assignment", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", [(k, j) for k in K for j in T], cat="Binary")
    Lmax = pulp.LpVariable("Lmax", lowBound=0)
    Lmin = pulp.LpVariable("Lmin", lowBound=0)
    # 软约束松弛变量：死端任务超额数 / 电量超耗量（死端任务多于 AMR 数时无法全做尾单）
    dead_over = pulp.LpVariable.dicts("dead_over", K, lowBound=0)
    batt_over = pulp.LpVariable.dicts("batt_over", K, lowBound=0)

    for j in T:
        prob += pulp.lpSum(x[k, j] for k in K) == 1
    for k in K:
        load_k = pulp.lpSum(dur[k, j] * x[k, j] for j in T)
        prob += Lmax >= load_k
        prob += Lmin <= load_k
        # 死端送货任务只能做尾单，每台 AMR 超过 1 个即按缺失衔接惩罚
        prob += (pulp.lpSum(x[k, j] for j in dead_end_tasks)
                 <= 1 + dead_over[k])
        # 电量粗校验（不含任务间衔接耗电，精确校验由阶段二与评价器完成）
        prob += (pulp.lpSum(energy[k, j] * x[k, j] for j in T)
                 <= amrs[k].battery - C.BATTERY_SAFETY_THRESHOLD + batt_over[k])

    prob += (
        C.F3_W_EMPTY_COST * pulp.lpSum(cost[k, j] * x[k, j] for k in K for j in T)
        + C.F3_W_LOAD_BALANCE * (Lmax - Lmin)
        + MISSING_PENALTY * pulp.lpSum(x[k, j] for k in K for j in T if missing[k, j])
        + MISSING_PENALTY * pulp.lpSum(dead_over[k] for k in K)
        + MISSING_PENALTY * pulp.lpSum(batt_over[k] for k in K)
    )
    status = prob.solve(pulp.PULP_CBC_CMD(msg=msg, timeLimit=time_limit))
    if pulp.LpStatus[status] not in ("Optimal", "Not Solved"):
        raise RuntimeError(f"M1 阶段一求解失败: {pulp.LpStatus[status]}")

    assigned = {k: [] for k in K}
    for k in K:
        for j in T:
            if x[k, j].value() and x[k, j].value() > 0.5:
                assigned[k].append(j)
    return assigned


def _partial_key(missing, viol, plc, lc, pdelay, tdelay, finish, empty_cost, energy):
    """与全局字典序同构的部分评价键（Cmax 用当前完成时间近似）。"""
    return (
        missing, viol, plc, lc,
        round(C.F2_W_PRIORITY_DELAY * pdelay + C.F2_W_TOTAL_DELAY * tdelay
              + C.F2_W_CMAX * finish, 6),
        round(C.F3_W_EMPTY_COST * empty_cost + C.F3_W_ENERGY * energy, 6),
    )


def _stage2_sequence_dp(data, amr, task_ids):
    """Held-Karp 动态规划：状态 (已完成任务集合, 末任务)，保留字典序最优标号。"""
    if not task_ids:
        return []
    ids = sorted(task_ids)
    index = {tid: i for i, tid in enumerate(ids)}
    init_label = {
        "key": _partial_key(0, 0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0),
        "node": amr.init_node, "time": 0.0, "battery": amr.battery,
        "missing": 0, "viol": 0, "plc": 0.0, "lc": 0,
        "pdelay": 0.0, "tdelay": 0.0, "empty_cost": 0.0, "energy": 0.0,
        "order": [],
    }
    states = {(frozenset(), None): init_label}

    for _ in ids:
        next_states = {}
        for (done, _last), lab in states.items():
            for tid in ids:
                if index[tid] in done:
                    continue
                task = data.tasks[tid]
                leg = compute_leg(data, amr, lab["node"], lab["time"],
                                  lab["battery"], task)
                missing = lab["missing"] + int(leg.trans_missing) + int(leg.loaded_missing)
                viol = lab["viol"] + int(leg.battery_violation)
                is_late = leg.delay > 1e-9
                plc = lab["plc"] + (task.priority if is_late else 0.0)
                lc = lab["lc"] + int(is_late)
                pdelay = lab["pdelay"] + task.priority * leg.delay
                tdelay = lab["tdelay"] + leg.delay
                empty_cost = lab["empty_cost"] + (
                    leg.trans_path["total_cost"] if leg.trans_path else 0.0)
                energy = lab["energy"] + leg.energy_used
                new_label = {
                    "key": _partial_key(missing, viol, plc, lc, pdelay, tdelay,
                                        leg.finish_time, empty_cost, energy),
                    "node": task.delivery, "time": leg.finish_time,
                    "battery": leg.battery_after,
                    "missing": missing, "viol": viol, "plc": plc, "lc": lc,
                    "pdelay": pdelay, "tdelay": tdelay,
                    "empty_cost": empty_cost, "energy": energy,
                    "order": lab["order"] + [tid],
                }
                state = (done | {index[tid]}, index[tid])
                if state not in next_states or new_label["key"] < next_states[state]["key"]:
                    next_states[state] = new_label
        states = next_states

    best = min(states.values(), key=lambda lab: lab["key"])
    return best["order"]


def solve(data, time_limit=30, msg=False, **kwargs):
    try:
        import pulp  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "M1 阶段一需要 PuLP：请运行 .venv\\Scripts\\pip install pulp") from exc

    t0 = time.time()
    assigned = _stage1_assign(data, time_limit, msg=msg)
    sequences = {
        k: _stage2_sequence_dp(data, data.amrs[k], tids)
        for k, tids in assigned.items()
    }
    solution = Solution(sequences=sequences, method="m1_two_stage_ip_dp")
    solution.info = {"solve_seconds": round(time.time() - t0, 3)}
    return solution
