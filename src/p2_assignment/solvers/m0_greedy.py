"""M0 修复版贪心（对照基线）。

相对原 baseline stub 的修复：
1. 跟踪 AMR 链式真实位置（上一任务送货点），而非始终用初始点估成本；
2. 时间按 speed 与载货速度系数修正；
3. 电量链参与可行性（违反在字典序最高层惩罚，贪心会自动避开）；
4. 候选追加位置直接用全局字典序评价比较，路径缺失方案自然被排到最后。

仍保留贪心的局限：任务只能追加到队尾，不回头调整顺序。
"""

from p2core import Solution, evaluate


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


def solve(data, **kwargs):
    solution = Solution(sequences={a: [] for a in data.amr_ids}, method="m0_greedy")

    for task_id in _task_order(data):
        best_key, best_amr = None, None
        for amr_id in data.amr_ids:
            trial = solution.copy()
            trial.sequences[amr_id].append(task_id)
            key = evaluate(data, trial).lex_key
            if best_key is None or key < best_key:
                best_key, best_amr = key, amr_id
        solution.sequences[best_amr].append(task_id)

    return solution
