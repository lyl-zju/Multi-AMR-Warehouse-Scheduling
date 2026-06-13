"""P2 对比实验批量运行器。

实验一  方法对比：base 算例 x {m0, m1, m2, m3}
实验二  AMR 数量敏感性：amr_n{2..6} x {m1, m2, m3}
实验三  任务密度敏感性：tasks_n{6,12,18,24}_s{0..2} x {m1, m2, m3}
实验四  消融与目标规划优先级敏感性（base 算例）：
        m3 去局部搜索 / m3 cheapest 插入 / m2 调换 F2、F3 优先级层

用法（项目根目录，先运行 generate_instances.py）：
    python .\\src\\p2_assignment\\experiments\\run_experiments.py --exp 1
    python .\\src\\p2_assignment\\experiments\\run_experiments.py --exp all

输出：outputs/p2_experiments/p2_method_comparison.csv（追加去重）
"""

import argparse
import sys
import time
import traceback
from pathlib import Path

import pandas as pd

P2_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P2_DIR))

from p2core import load_problem, evaluate          # noqa: E402
from solvers import SOLVERS, m2_milp, m3_insertion_ls  # noqa: E402

PROJECT_ROOT = P2_DIR.parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
EXP_DATA_DIR = PROJECT_ROOT / "data" / "experiments"
OUT_DIR = PROJECT_ROOT / "outputs" / "p2_experiments"
RESULT_CSV = OUT_DIR / "p2_method_comparison.csv"

MILP_TIME_LIMIT = 30  # 每层秒数；18/24 任务的 MILP 仅取时限内最好解
P1_ALGORITHM = "mixed"  # 实验 1-4 统一使用的 P1 路径源（mixed = 四算法融合候选库）
PATH_SOURCES = ("basic_astar", "vg", "avg", "davg", "mixed")  # 实验 5 对比的路径源


def p1_path_cost_csv(algorithm):
    return (PROJECT_ROOT / "data" / "processed" / "p1"
            / algorithm / "path_cost.csv")


def load_instance(name, p1_algorithm=P1_ALGORITHM):
    inst_dir = EXP_DATA_DIR / name
    return load_problem(RAW_DIR, p1_path_cost_csv(p1_algorithm),
                        tasks_csv=inst_dir / "tasks.csv",
                        amrs_csv=inst_dir / "amrs.csv")


def run_case(instance, label, solver_fn, p1_algorithm=P1_ALGORITHM, **solver_kwargs):
    data = load_instance(instance, p1_algorithm)
    t0 = time.time()
    try:
        solution = solver_fn(data, **solver_kwargs)
    except Exception:
        print(f"[失败] {instance} x {label}")
        traceback.print_exc()
        return None
    solve_seconds = round(time.time() - t0, 3)
    metrics = evaluate(data, solution).metrics
    row = {
        "instance": instance,
        "method": label,
        "p1_algorithm": p1_algorithm,
        "task_count": len(data.task_ids),
        "amr_count": len(data.amr_ids),
        "solve_time": solve_seconds,
        **metrics,
    }
    if getattr(solution, "info", None):
        row["solver_info"] = str(solution.info)
    print(f"[完成] {instance} x {label}: "
          f"missing={metrics['missing_transition_count']}, "
          f"F1=({metrics['priority_late_count']},{metrics['late_count']}), "
          f"F2={metrics['F2']}, F3={metrics['F3']}, {solve_seconds}s")
    return row


def exp1():
    return [run_case("base", m, SOLVERS[m], time_limit=60)
            for m in ("m0", "m1", "m2", "m3")]


def exp2():
    rows = []
    for n in (2, 3, 4, 5, 6):
        inst = f"amr_n{n}"
        for m in ("m1", "m2", "m3"):
            rows.append(run_case(inst, m, SOLVERS[m], time_limit=MILP_TIME_LIMIT))
    return rows


def exp3():
    rows = []
    for n in (6, 12, 18, 24):
        for seed in (0, 1, 2):
            inst = f"tasks_n{n}_s{seed}"
            for m in ("m1", "m2", "m3"):
                rows.append(run_case(inst, m, SOLVERS[m],
                                     time_limit=MILP_TIME_LIMIT))
    return rows


def exp4():
    swapped = ("missing", "f1_priority_late", "f1_late", "f3", "f2")
    return [
        run_case("base", "m3_no_local_search", m3_insertion_ls.solve,
                 use_local_search=False),
        run_case("base", "m3_cheapest_insertion", m3_insertion_ls.solve,
                 use_regret=False),
        run_case("base", "m2_swap_f2_f3", m2_milp.solve,
                 time_limit=60, level_order=swapped),
    ]


def exp5():
    """路径源对比：固定 m3，喂五种 P1 路径源（P1 x P2 模块联动实验）。"""
    return [
        run_case("base", f"m3@{src}", m3_insertion_ls.solve, p1_algorithm=src)
        for src in PATH_SOURCES
    ]


EXPERIMENTS = {"1": exp1, "2": exp2, "3": exp3, "4": exp4, "5": exp5}


def main():
    parser = argparse.ArgumentParser(description="P2 对比实验")
    parser.add_argument("--exp", choices=[*EXPERIMENTS, "all"], default="1")
    args = parser.parse_args()

    names = list(EXPERIMENTS) if args.exp == "all" else [args.exp]
    rows = []
    for name in names:
        print(f"===== 实验{name} =====")
        rows.extend(r for r in EXPERIMENTS[name]() if r is not None)

    if not rows:
        print("没有成功的实验结果")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    new_df = pd.DataFrame(rows)
    if RESULT_CSV.exists():
        old_df = pd.read_csv(RESULT_CSV)
        merged = pd.concat([old_df, new_df], ignore_index=True)
        merged = merged.drop_duplicates(["instance", "method"], keep="last")
    else:
        merged = new_df
    merged.to_csv(RESULT_CSV, index=False)
    print(f"结果已写入 {RESULT_CSV}（共 {len(merged)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
