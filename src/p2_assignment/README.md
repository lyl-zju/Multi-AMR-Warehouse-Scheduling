# P2 任务分配与任务排序

负责决定每个任务由哪台 AMR 执行，以及同一台 AMR 内部的任务顺序。

## 输入

```text
data/raw/tasks.csv
data/raw/amrs.csv
data/processed/path_cost.csv
```

## 脚本

```powershell
python .\src\p2_assignment\assign_and_sequence_tasks.py
```

## 输出

```text
data/processed/assignment_result.csv
data/processed/amr_sequence_summary.csv
```

## 报告用途

用于展示“哪台 AMR 做哪个任务”和“每台 AMR 的任务执行顺序”。当前算法为接口占位版，后续可替换为整数规划或启发式算法。
