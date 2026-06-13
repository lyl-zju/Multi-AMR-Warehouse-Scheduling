"""生成敏感性实验算例（固定随机种子，可复现）。

实验二（AMR 数量敏感性）：固定 12 任务，AMR 数量 2/3/4/5/6；
实验三（任务密度敏感性）：固定 4 台 AMR，任务数 6/12/18/24，每档 3 个种子。

约束说明：
- 新 AMR 初始点只能取 P1 关键节点中已有的出发点（IN1/IN2/CHG1/CHG2），
  否则 path_cost.csv 中没有对应候选路径；
- 取货点取 P1~P9，送货点取 SORT1/SORT2/OUT1/OUT2；
- OUT 送货任务数量不超过 AMR 数量（OUT 为单向死端，只能做尾单，
  超过 AMR 数量则衔接缺失不可避免，见 README 中的死端问题说明）。

输出：data/experiments/<instance>/tasks.csv + amrs.csv
"""

import random
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
EXP_DIR = PROJECT_ROOT / "data" / "experiments"

AMR_INIT_POOL = ["IN1", "IN2", "CHG1", "CHG2"]
PICKUP_POOL = [f"P{i}" for i in range(1, 10)]
SORT_POOL = ["SORT1", "SORT2"]
OUT_POOL = ["OUT1", "OUT2"]

AMR_COUNTS = [2, 3, 4, 5, 6]
TASK_COUNTS = [6, 12, 18, 24]
TASK_SEEDS = [0, 1, 2]


def make_amrs(n, seed):
    rng = random.Random(seed)
    base = pd.read_csv(RAW_DIR / "amrs.csv")
    rows = base.to_dict("records")[:n]
    for i in range(len(rows), n):
        rows.append({
            "amr_id": f"AMR{i + 1}",
            "init_node": AMR_INIT_POOL[i % len(AMR_INIT_POOL)],
            "battery": rng.choice([80, 85, 90, 95, 100]),
            "speed": rng.choice([0.9, 1.0, 1.1]),
        })
    return pd.DataFrame(rows)


def make_tasks(n, seed, amr_count):
    rng = random.Random(seed)
    max_out = amr_count  # OUT 死端任务只能做尾单
    rows, out_used = [], 0
    release = 0
    for i in range(n):
        if out_used < max_out and rng.random() < 0.3:
            delivery = rng.choice(OUT_POOL)
            out_used += 1
        else:
            delivery = rng.choice(SORT_POOL)
        earliest = release
        release += rng.choice([0, 1, 2, 2, 3])
        rows.append({
            "task_id": f"T{i + 1:02d}",
            "pickup": rng.choice(PICKUP_POOL),
            "delivery": delivery,
            "service_time": rng.choice([2, 2, 3]),
            "earliest_start": earliest,
            "latest_finish": earliest + rng.randint(25, 40),
            "priority": rng.randint(1, 3),
        })
    return pd.DataFrame(rows)


def write_instance(name, tasks_df, amrs_df):
    inst_dir = EXP_DIR / name
    inst_dir.mkdir(parents=True, exist_ok=True)
    tasks_df.to_csv(inst_dir / "tasks.csv", index=False)
    amrs_df.to_csv(inst_dir / "amrs.csv", index=False)
    print(f"已生成 {name}: {len(tasks_df)} 任务 x {len(amrs_df)} AMR")


def main():
    base_tasks = pd.read_csv(RAW_DIR / "tasks.csv")
    base_amrs = pd.read_csv(RAW_DIR / "amrs.csv")

    # 基准算例（原始数据的拷贝，作为实验一的输入）
    write_instance("base", base_tasks, base_amrs)

    # 实验二：AMR 数量敏感性，任务固定为原始 12 个
    for n in AMR_COUNTS:
        write_instance(f"amr_n{n}", base_tasks, make_amrs(n, seed=100 + n))

    # 实验三：任务密度敏感性，AMR 固定为原始 4 台
    for n in TASK_COUNTS:
        for seed in TASK_SEEDS:
            write_instance(f"tasks_n{n}_s{seed}",
                           make_tasks(n, seed=1000 + 10 * n + seed,
                                      amr_count=len(base_amrs)),
                           base_amrs)

    print(f"全部算例位于 {EXP_DIR}")


if __name__ == "__main__":
    sys.exit(main())
