# P3 时间调度、路径占用与冲突检测

负责把 P2 的任务顺序展开为时间表，并检测通道或节点冲突。

## 输入

```text
data/processed/p2/assignment_result.csv
data/processed/p1/<algorithm>/path_cost.csv
data/processed/p1/<algorithm>/path_edge_occupancy.csv
data/processed/p1/<algorithm>/path_node_occupancy.csv
data/raw/edges.csv
data/raw/nodes.csv
```

## 脚本

```powershell
python .\src\p3_schedule_conflicts\schedule_and_detect_conflicts.py --p1-algorithm basic_astar
python .\src\p3_schedule_conflicts\schedule_and_detect_conflicts.py --p1-algorithm vg
```

## 输出

```text
data/processed/p3/schedule_result.csv
data/processed/p3/edge_occupancy_schedule.csv
data/processed/p3/node_occupancy_schedule.csv
data/processed/p3/conflict_log.csv
```

## 报告用途

用于展示 AMR 任务时间表、通道/节点占用明细和冲突检测结果。当前只检测冲突，冲突修复策略仍为占位。
