# 动态仓储环境下多 AMR 调度优化框架

本仓库实现了一套面向智能仓储场景的多 AMR 任务分配、路径选择、冲突修复、动态重排与灵敏度分析流程。项目以二维仓库平面、AMR 状态、静态任务、动态事件和候选路径库为输入，逐步生成任务级调度计划、轨迹级可执行计划、动态事件后的滚动重排结果，以及面向运营决策的资源敏感性结论。

完整报告见 `report/main.pdf`，报告源文件位于 `report/main.tex` 及各子目录。本 README 用于说明仓库结构、模块关系和复现实验入口；每个模块的详细说明见 `src` 下对应 README。

## 项目简介

项目研究的问题可以概括为：在给定仓库二维地图、AMR 初始状态、任务时间窗、候选路径和动态事件的条件下，如何决定任务由哪台 AMR 执行、每台 AMR 按什么顺序执行、每段移动选择哪条路径，以及动态扰动到来后如何局部重排，使方案满足路径可达、电量安全、轨迹无冲突和动态事件约束，并尽量降低延期、完工时间、空驶成本、能耗和扰动范围。

当前算例包含 4 台 AMR、12 个静态任务和 1 个动态新增任务。主流程的核心结果为：

| 阶段 | 关键结果 |
| --- | --- |
| P2 静态调度 | 无缺失路径、无电量阈值违反、无延期，静态 `Cmax = 40.840111` |
| P3 冲突修复 | 将 21 个初始时空冲突修复为 0，修复后 `Cmax = 46.596970` |
| P4 动态重排 | 在通道封锁、AMR 延误、新增任务下保持轨迹冲突、封锁违规、延误违规均为 0，`Cmax = 61.918082` |
| P5 灵敏度分析 | 4 台 AMR 是当前算例服务阈值，4 台 AMR 下保守任务容量边界为 14 个任务，C03 是边界工况下主导空间瓶颈 |

## 系统架构

本项目采用 P0-P5 分阶段流水线。各阶段通过 CSV、PNG 和 GIF 文件交接，避免模块之间直接耦合。

```text
data/raw/
  nodes.csv, amrs.csv, tasks.csv, floor_zones.csv,
  floor_obstacles.csv, dynamic_events.csv
        |
        v
P0 场景建模与可视化
        |
        v
P1 二维候选路径生成
        |
        v
P2 静态任务分配、排序与路径选择
        |
        v
P3 时空轨迹展开与冲突修复
        |
        v
P4 动态事件识别与滚动时域重排
        |
        v
P5 资源边际、容量边界、瓶颈通道与全局灵敏度分析
```

各部分关系如下：

| 模块 | 目录 | 作用 | 主要下游 |
| --- | --- | --- | --- |
| P0 | `src/p0_data_scene/` | 读取仓库节点、区域、障碍物、AMR 和任务，绘制二维仓库平面图 | 为报告和后续空间解释提供统一场景 |
| P1 | `src/p1_candidate_paths/` | 用 A*、VG、AVG、DAVG 生成关键节点间路径成本和轨迹采样 | P2 读取成本，P3/P4 读取轨迹 |
| P2 | `src/p2_assignment/` | 构建 mixed 多候选路径库，联合优化任务指派、车内排序和路径选择 | P3 读取任务级静态计划 |
| P3 | `src/p3_schedule_conflicts/` | 将 P2 计划展开为 0.1 秒粒度二维轨迹，检测并修复 footprint 和节点容量冲突 | P4 读取无冲突基准计划 |
| P4 | `src/p4_dynamic_reschedule/` | 处理 `area_block`、`amr_delay`、`new_task`，冻结历史任务并重排未来任务池 | P5 读取动态重排后的运营指标 |
| P5 | `src/p5_experiment_analysis/` | 沿用 P1-P4 链路，分析 AMR 数量、任务负荷和瓶颈通道容量的敏感性 | 支撑报告中的管理解释 |

## 目录结构

```text
data/
  raw/                  # 原始仓库、任务、AMR 和动态事件数据
  processed/            # P1-P5 的结构化中间结果
docs/                   # 补充说明和接口文档
outputs/                # 各阶段图片、动画和实验图表
report/                 # LaTeX 报告源文件与 main.pdf
src/
  p0_data_scene/        # P0 场景图
  p1_candidate_paths/   # P1 路径规划
  p2_assignment/        # P2 任务分配与排序
  p3_schedule_conflicts/# P3 冲突检测与修复
  p4_dynamic_reschedule/# P4 动态重排
  p5_experiment_analysis/# P5 灵敏度分析
```

## 环境准备

建议使用 Python 虚拟环境安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`requirements.txt` 中包含 `pandas`、`numpy`、`matplotlib`、`networkx`、`pulp` 和 `SALib`。P2 的 m1/m2 与 P5 的若干实验会调用 PuLP/CBC；P5 Stage5 使用 SALib 完成 Morris 与 Sobol 分析。

## 主流程复现

在仓库根目录依次运行：

```powershell
python .\src\p0_data_scene\plot_floor_plan.py

python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm basic_astar
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm vg
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm avg
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm davg

python .\src\p2_assignment\build_mixed_paths.py
python .\src\p2_assignment\assign_and_sequence_tasks.py --method m2 --p1-algorithm mixed
python .\src\p2_assignment\plot_assignment_results.py

python .\src\p3_schedule_conflicts\schedule_and_detect_conflicts.py --p1-algorithm mixed
python .\src\p3_schedule_conflicts\animate_p3_conflict_repair.py

python .\src\p4_dynamic_reschedule\dynamic_reschedule.py
python .\src\p4_dynamic_reschedule\plot_p4_results.py
```

P5 分析可按阶段运行：

```powershell
python .\src\p5_experiment_analysis\stage1_baseline_analysis.py
python .\src\p5_experiment_analysis\stage2_amr_marginal_analysis.py
python .\src\p5_experiment_analysis\stage3_task_capacity_analysis.py
python .\src\p5_experiment_analysis\stage4_bottleneck_cluster_analysis.py
python .\src\p5_experiment_analysis\stage5_morris_sobol_analysis.py
```

其中 Stage5 会进行较多真实调度链路采样，耗时明显长于前序阶段。仓库中已经保留了主要处理结果和报告图表。

## 关键输入输出

原始输入：

```text
data/raw/nodes.csv              # AMR 初始点、取货点、分拣点、出库点、充电点等二维坐标
data/raw/amrs.csv               # AMR 初始位置、电量、速度系数
data/raw/tasks.csv              # 静态任务取货点、送货点、服务时间、时间窗、优先级
data/raw/floor_zones.csv        # 仓库功能区域
data/raw/floor_obstacles.csv    # 墙体、货架块、动态风险/封锁区域
data/raw/dynamic_events.csv     # area_block、amr_delay、new_task 动态事件
```

主流程输出：

```text
outputs/p0/warehouse_floor_plan.png

data/processed/p1/<algorithm>/path_cost.csv
data/processed/p1/<algorithm>/path_trajectory_samples.csv
data/processed/p1/mixed/path_cost.csv
data/processed/p1/mixed/path_trajectory_samples.csv

data/processed/p2/assignment_result.csv
data/processed/p2/amr_sequence_summary.csv

data/processed/p3/schedule_result.csv
data/processed/p3/trajectory_schedule.csv
data/processed/p3/conflict_log.csv
data/processed/p3/repair_summary.csv

data/processed/p4/reschedule_result.csv
data/processed/p4/dynamic_event_impact.csv
data/processed/p4/reschedule_summary.csv
data/processed/p4/trajectory_schedule.csv

data/processed/p5/
outputs/p5/
```

## 评阅导航

建议按以下顺序阅读：

1. `report/main.pdf`：完整模型、算法、实验结果和成员分工。
2. 本 README：理解项目整体架构和复现入口。
3. `src/p0_data_scene/README.md` 到 `src/p5_experiment_analysis/README.md`：查看各部分职责、算法、效果和代码结构。
4. `data/processed/` 与 `outputs/`：核对各阶段结构化结果和可视化证据。
