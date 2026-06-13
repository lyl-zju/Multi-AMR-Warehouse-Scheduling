"""P2 主入口：任务分配 + 任务排序 + 候选路径选择 + 时间/电量估算。

用法（项目根目录下）：
    python .\\src\\p2_assignment\\assign_and_sequence_tasks.py                       # 默认 m2 + basic_astar
    python .\\src\\p2_assignment\\assign_and_sequence_tasks.py --method m3 --p1-algorithm vg

方法：
    m0  修复版贪心（对照基线）
    m1  两阶段法：0-1 指派(分支定界) + 车内动态规划       [需要 pulp]
    m2  联合 MILP + 目标规划序贯解法（默认，精确）        [需要 pulp]
    m3  后悔值插入 + 局部搜索（无求解器依赖）

P1 路径算法（--p1-algorithm）：basic_astar / vg / avg / davg，
读取 data/processed/p1/<algorithm>/path_cost.csv。

输出（接口与原框架兼容，P3 直接消费）：
    data/processed/p2/assignment_result.csv
    data/processed/p2/amr_sequence_summary.csv
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from p2core import load_problem, evaluate
from p2core.output_writer import build_assignment_result, build_sequence_summary
from solvers import SOLVERS, SOLVER_LABELS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "p2"
P1_ALGORITHMS = ("basic_astar", "vg", "avg", "davg", "mixed")
DEFAULT_P1_ALGORITHM = "mixed"


def p1_path_cost_csv(algorithm):
    return PROJECT_ROOT / "data" / "processed" / "p1" / algorithm / "path_cost.csv"


def run(method="m2", p1_algorithm=DEFAULT_P1_ALGORITHM, time_limit=60,
        raw_dir=RAW_DATA_DIR, out_dir=PROCESSED_DATA_DIR,
        tasks_csv=None, amrs_csv=None, write_outputs=True):
    data = load_problem(raw_dir, p1_path_cost_csv(p1_algorithm),
                        tasks_csv=tasks_csv, amrs_csv=amrs_csv)

    t0 = time.time()
    solution = SOLVERS[method](data, time_limit=time_limit)
    solve_seconds = round(time.time() - t0, 3)
    evaluation = evaluate(data, solution)

    if write_outputs:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        method_name = f"{solution.method}+{p1_algorithm}"
        assignment = build_assignment_result(data, solution, evaluation, method_name)
        summary = build_sequence_summary(data, evaluation, method_name)
        assignment.to_csv(out_dir / "assignment_result.csv", index=False)
        summary.to_csv(out_dir / "amr_sequence_summary.csv", index=False)

    return data, solution, evaluation, solve_seconds


def main():
    parser = argparse.ArgumentParser(description="P2 任务分配与任务排序")
    parser.add_argument("--method", choices=sorted(SOLVERS), default="m2",
                        help="求解方法（默认 m2 联合 MILP）")
    parser.add_argument("--p1-algorithm", choices=P1_ALGORITHMS,
                        default=DEFAULT_P1_ALGORITHM,
                        help="读取哪个 P1 路径算法的 path_cost.csv")
    parser.add_argument("--time-limit", type=int, default=60,
                        help="MILP 每层求解时间上限（秒）")
    args = parser.parse_args()

    print(f"P2 方法: {args.method} - {SOLVER_LABELS[args.method]}")
    print(f"P1 路径算法: {args.p1_algorithm}")
    data, solution, evaluation, solve_seconds = run(
        method=args.method, p1_algorithm=args.p1_algorithm,
        time_limit=args.time_limit)

    m = evaluation.metrics
    print(f"求解耗时: {solve_seconds}s")
    print("各 AMR 任务序列:")
    for amr_id in data.amr_ids:
        seq = solution.sequences.get(amr_id, [])
        print(f"  {amr_id}: {' -> '.join(seq) if seq else '(空)'}")

    print(f"缺失衔接/载货路径: {m['missing_transition_count']} / "
          f"{m['missing_loaded_path_count']}")
    print(f"电量阈值违反: {m['energy_violation_count']}")
    print(f"F1: priority_late_count={m['priority_late_count']}, "
          f"late_count={m['late_count']}")
    print(f"F2={m['F2']} (priority_delay={m['priority_delay']}, "
          f"total_delay={m['total_delay']}, Cmax={m['Cmax']})")
    print(f"F3={m['F3']} (empty_cost={m['empty_cost']}, "
          f"load_balance={m['load_balance_penalty']}, "
          f"energy={m['total_energy_used']})")
    if getattr(solution, "info", None):
        print(f"求解器信息: {solution.info}")
    print(f"已保存: {PROCESSED_DATA_DIR / 'assignment_result.csv'}")
    print(f"已保存: {PROCESSED_DATA_DIR / 'amr_sequence_summary.csv'}")


if __name__ == "__main__":
    main()
