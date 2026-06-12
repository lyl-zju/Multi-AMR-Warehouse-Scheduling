# P3 时间调度与二维冲突检测

P3 把 P2 的任务顺序展开成时间表，并基于 P1 的二维轨迹样本检测 AMR footprint 冲突。

## 输入

```text
data/processed/p2/assignment_result.csv
data/processed/p1/<algorithm>/path_trajectory_samples.csv
```

## 运行

```powershell
python .\src\p3_schedule_conflicts\schedule_and_detect_conflicts.py --p1-algorithm basic_astar
python .\src\p3_schedule_conflicts\schedule_and_detect_conflicts.py --p1-algorithm davg
```

## 输出

```text
data/processed/p3/schedule_result.csv
data/processed/p3/trajectory_schedule.csv
data/processed/p3/conflict_log.csv
```

`trajectory_schedule.csv` 将每条路径的 `offset_time` 转换为实际 `absolute_time`。`conflict_log.csv` 记录同一时间附近 footprint 距离小于安全阈值的二维空间冲突。
