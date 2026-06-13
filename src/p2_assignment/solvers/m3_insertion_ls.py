"""M3 后悔值插入 + 局部搜索（课程第四章局部最优思想的离散化应用，课内拓展的元启发式家族）。

构造阶段（regret-2 插入）：
    每轮对所有未插入任务枚举 (AMR, 插入位置)，计算最优与次优插入的评价差，
    优先安置"后悔值"最大（错过最佳位置代价最高）的任务，避免困难任务被挤到最后。

改进阶段（邻域下降，best-improvement）：
    relocate  把一个任务移到任意 AMR 的任意位置（含同车调序）
    swap      交换任意两个任务的位置（同车或跨车）
    reroute   对某任务的载货段或空驶衔接段改选另一条 P1 候选路径
    直至邻域内无改进（陷入局部最优）或达到迭代上限。

所有比较都使用字典序评价元组；regret 差值运算使用 lex_to_scalar 近似标量。
"""

import time

from p2core import Solution, evaluate, lex_to_scalar


def _task_order(data):
    return sorted(
        data.task_ids,
        key=lambda tid: (
            data.tasks[tid].earliest_start,
            -data.tasks[tid].priority,
            data.tasks[tid].latest_finish,
            tid,
        ),
    )


def _insertion_candidates(data, solution, task_id):
    """枚举所有插入方案，返回按字典序排序的 [(lex_key, amr_id, pos)]。"""
    results = []
    for amr_id in data.amr_ids:
        seq = solution.sequences[amr_id]
        for pos in range(len(seq) + 1):
            trial = solution.copy()
            trial.sequences[amr_id].insert(pos, task_id)
            results.append((evaluate(data, trial).lex_key, amr_id, pos))
    results.sort(key=lambda item: item[0])
    return results


def _regret_insertion(data, solution, use_regret=True):
    unassigned = _task_order(data)
    while unassigned:
        if use_regret and len(unassigned) > 1:
            best_choice, best_regret = None, None
            for tid in unassigned:
                cands = _insertion_candidates(data, solution, tid)
                regret = (lex_to_scalar(cands[1][0]) - lex_to_scalar(cands[0][0])
                          if len(cands) > 1 else float("inf"))
                # 后悔值大者优先；后悔值相同时取插入后评价更优者
                rank = (-regret, cands[0][0])
                if best_regret is None or rank < best_regret:
                    best_regret = rank
                    best_choice = (tid, cands[0])
            tid, (_, amr_id, pos) = best_choice
        else:
            tid = unassigned[0]
            _, amr_id, pos = _insertion_candidates(data, solution, tid)[0]
        solution.sequences[amr_id].insert(pos, tid)
        unassigned.remove(tid)
    return solution


def _neighbors(data, solution):
    """生成 relocate / swap / reroute 三类邻域解。"""
    positions = [(a, i) for a in data.amr_ids
                 for i in range(len(solution.sequences[a]))]

    # relocate：移除任务后插到任意位置
    for a, i in positions:
        tid = solution.sequences[a][i]
        for b in data.amr_ids:
            limit = len(solution.sequences[b]) + (0 if b == a else 1)
            for pos in range(limit):
                if b == a and pos == i:
                    continue
                trial = solution.copy()
                trial.sequences[a].pop(i)
                trial.sequences[b].insert(pos, tid)
                yield trial

    # swap：交换两个任务
    for idx, (a, i) in enumerate(positions):
        for b, jj in positions[idx + 1:]:
            trial = solution.copy()
            trial.sequences[a][i], trial.sequences[b][jj] = (
                trial.sequences[b][jj], trial.sequences[a][i])
            yield trial

    # reroute：改选候选路径
    for a in data.amr_ids:
        amr = data.amrs[a]
        seq = solution.sequences[a]
        node = amr.init_node
        for tid in seq:
            task = data.tasks[tid]
            for path in data.paths.candidates(node, task.pickup):
                if path["path_uid"] != solution.trans_choice.get(tid):
                    trial = solution.copy()
                    trial.trans_choice[tid] = path["path_uid"]
                    yield trial
            for path in data.paths.candidates(task.pickup, task.delivery):
                if path["path_uid"] != solution.loaded_choice.get(tid):
                    trial = solution.copy()
                    trial.loaded_choice[tid] = path["path_uid"]
                    yield trial
            node = task.delivery


def _local_search(data, solution, max_iter=300):
    current_key = evaluate(data, solution).lex_key
    iterations = 0
    improved = True
    while improved and iterations < max_iter:
        improved = False
        iterations += 1
        best_trial, best_key = None, current_key
        for trial in _neighbors(data, solution):
            key = evaluate(data, trial).lex_key
            if key < best_key:
                best_key, best_trial = key, trial
        if best_trial is not None:
            solution, current_key = best_trial, best_key
            improved = True
    return solution, iterations


def solve(data, use_regret=True, use_local_search=True, max_iter=300, **kwargs):
    t0 = time.time()
    solution = Solution(sequences={a: [] for a in data.amr_ids})
    solution = _regret_insertion(data, solution, use_regret=use_regret)
    iterations = 0
    if use_local_search:
        solution, iterations = _local_search(data, solution, max_iter=max_iter)
    suffix = "" if use_local_search else "_no_ls"
    prefix = "regret" if use_regret else "cheapest"
    solution.method = f"m3_{prefix}_insertion_ls{suffix}"
    solution.info = {
        "solve_seconds": round(time.time() - t0, 3),
        "local_search_iterations": iterations,
    }
    return solution
