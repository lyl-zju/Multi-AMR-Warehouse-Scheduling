"""M2 联合 MILP：任务指派 + 任务排序 + 时间 + 电量的整体 0-1 混合整数规划，
按目标规划的优先级因子用序贯解法逐层求解（课程第三章 + 第五章）。

决策变量：
    x[k,j]   任务 j 指派给 AMR k
    s[k,j]   j 是 k 的第一个任务
    u[k,i,j] k 在任务 i 后紧接执行 j
    S[j], C[j], D[j], late[j], Cmax, B[j](完成 j 后剩余电量), lastC[k], Lmax, Lmin

序贯层次（每层求解后将其目标值固定为约束再进入下一层）：
    L0 缺失路径数 → L1 priority_late_count → L2 late_count
    → L3 F2 = 30*priority_delay + 20*total_delay + 5*Cmax
    → L4 F3 = 2*empty_cost + 10*(Lmax-Lmin) + 1*total_energy

加速与稳健性措施：
    1. 先运行 M3 启发式获得热启动解（CBC warm start）；
    2. 各层目标下界已知时（F1 层下界 0；缺失层下界由鸽笼原理给出：
       载货缺失数 + max(0, 死端送货任务数 - AMR 数)），若热启动解已达下界，
       该层无需求解，直接固定——分支定界的"界"思想用在层间；
    3. 热启动解对模型可行时，为当前层添加上界割 expr <= warm_value；
    4. 每层严格校验 PuLP sol_status，超时且无整数可行解立即中止；
    5. 最终用 evaluator 实测 MILP 解与热启动解的字典序，返回更优者
       （防止超时层读到无效变量值时输出劣解）。

候选路径选择：模型在每个 OD 上采用综合成本最优的候选路径估计时间与成本；
备选路径的精细换路由 M3 的 reroute 邻域或 P3 冲突消解承担。

求解器 CBC = 分支定界 + 割平面（branch-and-cut），即课内分支定界法的进阶版。
"""

import time

from p2core import Solution, evaluate
from p2core import constants as C

DEFAULT_LEVEL_ORDER = ("missing", "f1_priority_late", "f1_late", "f2", "f3")


def _seg(data, amr, from_node, to_node, loaded=False):
    """返回 (实际时间, 距离, 综合成本, 是否缺失)，缺失用代用值。"""
    path = data.paths.best(from_node, to_node)
    if path is None:
        base_t, dist, cost, missing = (C.MISSING_SURROGATE_TIME,
                                       C.MISSING_SURROGATE_DISTANCE, 0.0, True)
    else:
        base_t, dist, cost, missing = (path["travel_time"], path["distance"],
                                       path["total_cost"], False)
    speed = amr.speed * (C.LOADED_SPEED_FACTOR if loaded else 1.0)
    return base_t / speed, dist, cost, missing


def _missing_lower_bound(data):
    """缺失路径数的解析下界：载货缺失为常数；死端送货任务只能做尾单，
    超出 AMR 数的部分按鸽笼原理必然产生衔接缺失。"""
    loaded_missing = sum(
        1 for j in data.task_ids
        if data.paths.best(data.tasks[j].pickup, data.tasks[j].delivery) is None)
    dead_end = sum(
        1 for j in data.task_ids
        if not data.paths.has_outgoing(data.tasks[j].delivery))
    return loaded_missing + max(0, dead_end - len(data.amr_ids))


def solve(data, time_limit=30, level_order=DEFAULT_LEVEL_ORDER, msg=False, **kwargs):
    try:
        import pulp
    except ImportError as exc:
        raise ImportError(
            "M2 需要 PuLP（CBC 分支定界求解器）：请运行 "
            ".venv\\Scripts\\pip install pulp"
        ) from exc

    from .m3_insertion_ls import solve as m3_solve

    t0 = time.time()
    tasks, amrs = data.tasks, data.amrs
    T, K = data.task_ids, data.amr_ids

    # ---- 热启动解：完整 m3 解（含换路）用于最终对比；
    #      去掉换路的序列版与模型同口径，用于 warm start / 上界割 / 跳层 ----
    warm_full = m3_solve(data)
    warm_full_eval = evaluate(data, warm_full)
    warm = Solution(sequences={k: list(v) for k, v in warm_full.sequences.items()})
    warm_eval = evaluate(data, warm)
    wm = warm_eval.metrics
    warm_model_feasible = wm["energy_violation_count"] == 0
    warm_values = {
        "missing": wm["missing_transition_count"] + wm["missing_loaded_path_count"],
        "f1_priority_late": wm["priority_late_count"],
        "f1_late": wm["late_count"],
        "f2": wm["F2"],
        "f3": wm["F3"],
    }
    lower_bounds = {
        "missing": _missing_lower_bound(data),
        "f1_priority_late": 0.0,
        "f1_late": 0.0,
    }

    # ---- 预计算参数 ----
    lt = {}            # (k,j) 载货时间
    task_energy = {}   # j 载货+服务耗电
    loaded_missing = {}
    for j in T:
        tj = tasks[j]
        for k in K:
            lt[k, j], _, _, _ = _seg(data, amrs[k], tj.pickup, tj.delivery, loaded=True)
        _, dist, _, miss = _seg(data, amrs[K[0]], tj.pickup, tj.delivery, loaded=True)
        loaded_missing[j] = miss
        task_energy[j] = dist * C.LOADED_ENERGY_RATE + tj.service_time * C.SERVICE_ENERGY_RATE

    tt_init, en_init, cost_init, miss_init = {}, {}, {}, {}
    for k in K:
        for j in T:
            t, d, c, m = _seg(data, amrs[k], amrs[k].init_node, tasks[j].pickup)
            tt_init[k, j], en_init[k, j], cost_init[k, j], miss_init[k, j] = (
                t, d * C.EMPTY_ENERGY_RATE, c, m)

    tt, en_tr, cost_tr, miss_tr = {}, {}, {}, {}
    for k in K:
        for i in T:
            for j in T:
                if i == j:
                    continue
                t, d, c, m = _seg(data, amrs[k], tasks[i].delivery, tasks[j].pickup)
                tt[k, i, j], en_tr[k, i, j], cost_tr[k, i, j], miss_tr[k, i, j] = (
                    t, d * C.EMPTY_ENERGY_RATE, c, m)

    horizon = max(t.earliest_start for t in tasks.values()) + sum(
        tasks[j].service_time + max(lt[k, j] for k in K)
        + max([tt_init[k, j] for k in K]
              + [tt[k, i, j] for k in K for i in T if i != j])
        for j in T
    )
    M = horizon + max(t.latest_finish for t in tasks.values())
    MB = C.MAX_BATTERY * 2

    # ---- 建模 ----
    prob = pulp.LpProblem("p2_joint_milp", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", [(k, j) for k in K for j in T], cat="Binary")
    s = pulp.LpVariable.dicts("s", [(k, j) for k in K for j in T], cat="Binary")
    u = pulp.LpVariable.dicts(
        "u", [(k, i, j) for k in K for i in T for j in T if i != j], cat="Binary")
    S = pulp.LpVariable.dicts("S", T, lowBound=0)
    Cv = pulp.LpVariable.dicts("C", T, lowBound=0)
    D = pulp.LpVariable.dicts("D", T, lowBound=0)
    late = pulp.LpVariable.dicts("late", T, cat="Binary")
    B = pulp.LpVariable.dicts("B", T,
                              lowBound=C.BATTERY_SAFETY_THRESHOLD,
                              upBound=C.MAX_BATTERY)
    Cmax = pulp.LpVariable("Cmax", lowBound=0)
    lastC = pulp.LpVariable.dicts("lastC", K, lowBound=0)
    Lmax = pulp.LpVariable("Lmax", lowBound=0)
    Lmin = pulp.LpVariable("Lmin", lowBound=0)

    for j in T:
        # 每个任务必须且仅分配给一台 AMR；恰有一个前驱（首任务或紧前任务）
        prob += pulp.lpSum(x[k, j] for k in K) == 1
        for k in K:
            prob += (pulp.lpSum(u[k, i, j] for i in T if i != j) + s[k, j]
                     == x[k, j])
        prob += S[j] >= tasks[j].earliest_start
        prob += Cv[j] == (S[j] + tasks[j].service_time
                          + pulp.lpSum(lt[k, j] * x[k, j] for k in K))
        prob += D[j] >= Cv[j] - tasks[j].latest_finish
        prob += D[j] <= M * late[j]
        prob += Cmax >= Cv[j]

    for k in K:
        prob += pulp.lpSum(s[k, j] for j in T) <= 1
        for i in T:
            # 每个任务至多一个紧后任务
            prob += pulp.lpSum(u[k, i, j] for j in T if i != j) <= x[k, i]

    for k in K:
        for j in T:
            # 时间衔接与电量链（首任务）
            prob += S[j] >= tt_init[k, j] - M * (1 - s[k, j])
            prob += B[j] <= (amrs[k].battery - en_init[k, j] - task_energy[j]
                             + MB * (1 - s[k, j]))
            for i in T:
                if i == j:
                    continue
                # 时间衔接与电量链（紧前任务为 i）
                prob += S[j] >= Cv[i] + tt[k, i, j] - M * (1 - u[k, i, j])
                prob += B[j] <= (B[i] - en_tr[k, i, j] - task_energy[j]
                                 + MB * (1 - u[k, i, j]))

    for k in K:
        # y[k,j] = x - Σu 表示 j 是 k 的末任务；lastC[k] 即该 AMR 时间负载
        for j in T:
            y_expr = x[k, j] - pulp.lpSum(u[k, j, i] for i in T if i != j)
            prob += lastC[k] >= Cv[j] - M * (1 - y_expr)
            prob += lastC[k] <= Cv[j] + M * (1 - y_expr)
        prob += lastC[k] <= M * pulp.lpSum(x[k, j] for j in T)
        prob += Lmax >= lastC[k]
        prob += Lmin <= lastC[k]

    # ---- 写入热启动初值（与模型同口径的 warm 序列）----
    chain_pairs, firsts, assign_of = set(), {}, {}
    for k in K:
        seq = warm.sequences.get(k, [])
        if seq:
            firsts[k] = seq[0]
        for idx, tid in enumerate(seq):
            assign_of[tid] = k
            if idx > 0:
                chain_pairs.add((k, seq[idx - 1], tid))
    for (k, j), var in x.items():
        var.setInitialValue(1 if assign_of.get(j) == k else 0)
    for (k, j), var in s.items():
        var.setInitialValue(1 if firsts.get(k) == j else 0)
    for key, var in u.items():
        var.setInitialValue(1 if key in chain_pairs else 0)
    finish_times = []
    for k in K:
        tl = warm_eval.timelines[k]
        finish_times.append(tl.finish_time)
        lastC[k].setInitialValue(tl.finish_time)
        for leg in tl.legs:
            S[leg.task_id].setInitialValue(leg.start_time)
            Cv[leg.task_id].setInitialValue(leg.finish_time)
            D[leg.task_id].setInitialValue(leg.delay)
            late[leg.task_id].setInitialValue(1 if leg.delay > 1e-9 else 0)
            B[leg.task_id].setInitialValue(
                min(C.MAX_BATTERY,
                    max(C.BATTERY_SAFETY_THRESHOLD, leg.battery_after)))
    Cmax.setInitialValue(max(finish_times) if finish_times else 0.0)
    Lmax.setInitialValue(max(finish_times) if finish_times else 0.0)
    Lmin.setInitialValue(min(finish_times) if finish_times else 0.0)

    # ---- 各层目标表达式 ----
    missing_expr = (
        pulp.lpSum(s[k, j] for k in K for j in T if miss_init[k, j])
        + pulp.lpSum(u[k, i, j] for k in K for i in T for j in T
                     if i != j and miss_tr[k, i, j])
        + sum(1 for j in T if loaded_missing[j])
    )
    empty_cost_expr = (
        pulp.lpSum(cost_init[k, j] * s[k, j] for k in K for j in T)
        + pulp.lpSum(cost_tr[k, i, j] * u[k, i, j]
                     for k in K for i in T for j in T if i != j)
    )
    energy_expr = (
        sum(task_energy[j] for j in T)
        + pulp.lpSum(en_init[k, j] * s[k, j] for k in K for j in T)
        + pulp.lpSum(en_tr[k, i, j] * u[k, i, j]
                     for k in K for i in T for j in T if i != j)
    )
    levels = {
        "missing": (missing_expr, 0.5),
        "f1_priority_late": (pulp.lpSum(tasks[j].priority * late[j] for j in T), 0.5),
        "f1_late": (pulp.lpSum(late[j] for j in T), 0.5),
        "f2": (C.F2_W_PRIORITY_DELAY * pulp.lpSum(tasks[j].priority * D[j] for j in T)
               + C.F2_W_TOTAL_DELAY * pulp.lpSum(D[j] for j in T)
               + C.F2_W_CMAX * Cmax, 1e-4),
        "f3": (C.F3_W_EMPTY_COST * empty_cost_expr
               + C.F3_W_LOAD_BALANCE * (Lmax - Lmin)
               + C.F3_W_ENERGY * energy_expr, 1e-4),
    }

    # ---- 目标规划序贯解法：逐层最小化并固定 ----
    # Windows 上 CBC 的 warmStart 必须 keepFiles=True 才生效（临时文件不留在项目目录）
    import tempfile
    solver = pulp.PULP_CBC_CMD(msg=msg, timeLimit=time_limit, warmStart=True,
                               keepFiles=True)
    solver.tmpDir = tempfile.mkdtemp(prefix="p2_m2_cbc_")
    ok_statuses = (pulp.LpSolutionOptimal, pulp.LpSolutionIntegerFeasible)
    level_values, level_status = {}, {}
    cutoff_valid = warm_model_feasible  # 热启动解是否仍满足已固定各层 → 上界割有效
    solved_ok = True
    for name in level_order:
        expr, eps = levels[name]
        warm_value = warm_values.get(name) if warm_model_feasible else None
        lb = lower_bounds.get(name)
        if warm_value is not None and lb is not None and warm_value <= lb + 1e-9:
            # 热启动解已达该层下界，最优性得证，免去一次分支定界
            level_values[name] = warm_value
            level_status[name] = "skipped_at_lower_bound"
            prob += expr <= warm_value + eps, f"fix_level_{name}"
            continue
        if cutoff_valid and warm_value is not None:
            prob += expr <= warm_value + eps, f"warm_cutoff_{name}"
        prob.setObjective(expr)
        prob.solve(solver)
        if prob.sol_status not in ok_statuses:
            level_status[name] = f"no_solution({pulp.LpSolution[prob.sol_status]})"
            solved_ok = False
            break
        value = pulp.value(prob.objective)
        level_values[name] = value
        level_status[name] = pulp.LpSolution[prob.sol_status]
        prob += expr <= value + eps, f"fix_level_{name}"
        if warm_value is None or value < warm_value - 1e-9:
            cutoff_valid = False

    info = {
        "level_values": {k: round(v, 6) for k, v in level_values.items()},
        "level_status": level_status,
        "level_order": list(level_order),
    }

    # ---- 还原 MILP 解并用 evaluator 实测，与 m3 完整解（含换路）取字典序更优 ----
    milp_solution = None
    if solved_ok:
        sequences = {k: [] for k in K}
        succ = {(k, i): j for (k, i, j), var in u.items()
                if var.value() and var.value() > 0.5}
        for k in K:
            first = [j for j in T if s[k, j].value() and s[k, j].value() > 0.5]
            if not first:
                continue
            node = first[0]
            while node is not None and node not in sequences[k]:
                sequences[k].append(node)
                node = succ.get((k, node))
        milp_solution = Solution(sequences=sequences, method="m2_milp_goal_programming")

    if (milp_solution is not None
            and evaluate(data, milp_solution).lex_key <= warm_full_eval.lex_key):
        chosen, info["solution_source"] = milp_solution, "milp"
    else:
        # 超时/无效解兜底：返回热启动完整解，保证 m2 不劣于 m3
        chosen = warm_full.copy()
        chosen.method = "m2_milp_goal_programming"
        info["solution_source"] = "warm_start_fallback"

    info["solve_seconds"] = round(time.time() - t0, 3)
    chosen.info = info
    return chosen
