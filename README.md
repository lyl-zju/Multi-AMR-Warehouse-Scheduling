# 多 AMR 仓储调度简易框架

当前版本统一采用“二维仓库平面图 + 障碍物 + 轨迹采样”的建模口径。路径规划不再读取抽象边表；下游调度、冲突检测和动态扰动分析都围绕 P1 输出的二维路径几何与轨迹样本展开。

## 数据目录

```text
data/raw/
  nodes.csv              # AMR 初始点、货架点、分拣点、出库点、充电点等二维坐标
  amrs.csv               # AMR 初始位置、电量、速度
  tasks.csv              # 任务取货点、送货点、服务时间、时间窗、优先级
  floor_zones.csv        # 二维功能区域
  floor_obstacles.csv    # 墙体、货架块、动态风险/封锁区域
  dynamic_events.csv     # area_block、amr_delay、new_task 动态事件
```

## 运行流水线

```powershell
python .\src\p0_data_scene\plot_floor_plan.py
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm basic_astar
python .\src\p1_candidate_paths\plot_candidate_paths.py --algorithm basic_astar
python .\src\p2_assignment\assign_and_sequence_tasks.py --p1-algorithm basic_astar
python .\src\p3_schedule_conflicts\schedule_and_detect_conflicts.py --p1-algorithm basic_astar
python .\src\p4_dynamic_reschedule\dynamic_reschedule.py
python .\src\p4_dynamic_reschedule\plot_p4_results.py
```

`--algorithm` / `--p1-algorithm` 可选 `basic_astar`、`vg`、`avg`、`davg`。

## 模块输出

P0 输出二维场景图：

```text
outputs/p0/warehouse_floor_plan.png
```

P1 生成点到点候选路径、成本矩阵和轨迹样本：

```text
data/processed/p1/<algorithm>/key_nodes.csv
data/processed/p1/<algorithm>/path_cost.csv
data/processed/p1/<algorithm>/path_grid_cells.csv
data/processed/p1/<algorithm>/path_trajectory_samples.csv
data/processed/p1/<algorithm>/path_cost_matrix_time.csv
data/processed/p1/<algorithm>/path_cost_matrix_total.csv
```

P2 读取 P1 的路径成本，生成任务分配与排序：

```text
data/processed/p2/assignment_result.csv
data/processed/p2/amr_sequence_summary.csv
```

P3 读取 P2 排序和 P1 轨迹样本，展开二维时空轨迹并检测 footprint 冲突：

```text
data/processed/p3/schedule_result.csv
data/processed/p3/trajectory_schedule.csv
data/processed/p3/conflict_log.csv
```

P4 读取 P3 时间表、二维轨迹和动态事件，生成扰动影响与重排结果：

```text
data/processed/p4/reschedule_result.csv
data/processed/p4/dynamic_event_impact.csv
data/processed/p4/reschedule_summary.csv
data/processed/p4/trajectory_schedule.csv
data/processed/p4/p4_method_comparison.csv
outputs/p4/p4_gantt_events.png
outputs/p4/p4_trajectory_map.png
outputs/p4/p4_event_impact_metrics.png
outputs/p4/p4_method_comparison.png
outputs/p4/p4_dynamic_reschedule.gif
```

## P1 路径算法

| 参数 | 含义 |
| --- | --- |
| `basic_astar` | 二维栅格 8 邻域 A*，仅按几何距离搜索 |
| `vg` | 可见图最短路 |
| `avg` | 在可见图上加入转角代价 |
| `davg` | 在 AVG 基础上加入动态区域代价 |

`path_cost.csv` 面向 P2，主要字段包括 `from_node`、`to_node`、`path_uid`、`distance`、`travel_time`、`turn_cost`、`dynamic_cost`、`total_cost`。  
`path_trajectory_samples.csv` 面向 P3/P4，包含 `offset_time`、`x`、`y`、`theta`、`speed`、`footprint_radius`。

## 动态事件

`dynamic_events.csv` 使用二维事件：

| event_type | 用途 |
| --- | --- |
| `area_block` | 在一段时间内封锁或提高风险的矩形区域，字段为 `x,y,width,height,start_time,end_time` |
| `amr_delay` | 某台 AMR 在时间窗内延误 |
| `new_task` | 运行中释放新任务 |

P4 当前执行动态滚动重排：它读取 P3 的无冲突基准计划，在 `area_block`、`amr_delay`、`new_task` 事件到来后冻结已执行任务，对未来任务做局部等待修复、新任务滚动插入、候选路径重评估和轨迹冲突修复，并额外比较全局重排、仅等待、滚动时域重排三种策略。
