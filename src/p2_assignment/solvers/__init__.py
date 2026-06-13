"""P2 求解器注册表。"""

from . import m0_greedy, m1_two_stage, m2_milp, m3_insertion_ls

SOLVERS = {
    "m0": m0_greedy.solve,
    "m1": m1_two_stage.solve,
    "m2": m2_milp.solve,
    "m3": m3_insertion_ls.solve,
}

SOLVER_LABELS = {
    "m0": "修复版贪心（对照基线）",
    "m1": "两阶段法：0-1 指派(分支定界) + 车内动态规划",
    "m2": "联合 MILP + 目标规划序贯解法",
    "m3": "后悔值插入 + 局部搜索",
}
