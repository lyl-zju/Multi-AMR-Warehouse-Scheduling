# P2 任务分配与任务排序

负责决定：每个任务由哪台 AMR 执行、每台 AMR 内部的任务顺序、每段移动选用哪条 P1 候选路径，并给出考虑 `speed`、载货速度系数与电量链的开始/完成时间估算。

## 文件结构

```text
p2_assignment/
  assign_and_sequence_tasks.py   主入口（--method 切换算法，--p1-algorithm 切换 P1 路径源）
  build_mixed_paths.py           融合四种 P1 算法输出为 mixed 多候选路径库
  plot_assignment_results.py     甘特图/方法对比/任务分配空间图/候选选择图
  animate_schedule.py            调度执行动画 GIF
  p2core/                        公共层（所有算法共用，保证指标口径一致）
    constants.py                 命题规定固定参数（速度系数/耗电/电量阈值/F2,F3 权重）
    data_io.py                   数据加载 + 候选路径库 PathLibrary（过滤 planning_status != ok）
    timeline.py                  单 AMR 任务链时间表与电量链推演
    evaluator.py                 字典序评价元组 lex(可行性, F1, F2, F3) + 指标汇总
    output_writer.py             生成兼容 P3 的 assignment_result.csv
  solvers/                       四种求解方法
    m0_greedy.py                 修复版贪心（对照基线）
    m1_two_stage.py              两阶段：0-1 指派(分支定界) + 车内动态规划
    m2_milp.py                   联合 MILP + 目标规划序贯解法（默认）
    m3_insertion_ls.py           后悔值插入 + 局部搜索
  experiments/
    generate_instances.py        敏感性实验算例生成（固定种子）
    run_experiments.py           方法 x 算例批量对比，输出指标 CSV
    plot_experiment_curves.py    实验二/三敏感性曲线 + 实验五路径源对比图
```

## mixed 候选路径库

每个 P1 算法对同一 OD 只输出 1 条路径，候选路径选择（命题第 7 节）无从谈起。`build_mixed_paths.py` 把四种算法的输出融合为多候选库：path_uid 加算法前缀、统一成本口径（travel_time + 0.5×turn_count，原始成本保留在 source_total_cost）、几何去重后输出到 `data/processed/p1/mixed/`。P2/P3 用 `--p1-algorithm mixed` 即可消费，下游零改动。当前算例融合后平均每 OD 1.77 条候选（最多 4 条）。

## 方法与课程章节对应

| 方法 | 内容 | 课程章节 |
| --- | --- | --- |
| m0 | 修复版贪心：链式位置 + speed 修正 + 可行性剪枝 | 对照基线（无模型） |
| m1 | 阶段一 0-1 整数规划指派（CBC 分支定界）；阶段二 Held-Karp 动态规划排序（状态 = 已完成任务集合 + 末任务） | 第三章 整数规划、第八章 动态规划 |
| m2 | 联合 MILP（指派 x[k,j] + 顺序 u[k,i,j] + 时间/电量变量），按目标规划优先级因子序贯求解 5 层：缺失路径 → priority_late → late → F2 → F3；CBC = 分支定界 + 割平面（branch-and-cut）；m3 热启动 + 解析下界跳层加速 | 第三章 3.2/3.4（+3.3 割平面）、第五章 目标规划 |
| m3 | regret-2 插入构造 + relocate/swap/reroute 邻域下降 | 第四章 局部最优思想的离散化 + 课内拓展的元启发式 |

候选路径来自 P1 的二维平面图规划器（basic_astar / vg / avg / davg，对应图论最短路思想在连续空间的推广）。所有方法共用 `p2core/evaluator.py` 的字典序评价 `min lex(缺失数+电量违反, priority_late_count, late_count, F2, F3)`，与命题第 10 节一致。

## 运行

需要先运行 P1 生成对应算法的 path_cost，并安装 PuLP（m1/m2 需要）：

```powershell
pip install pulp        # 或 .venv\Scripts\pip install pulp
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm basic_astar
python .\src\p2_assignment\assign_and_sequence_tasks.py --method m2 --p1-algorithm basic_astar
```

输入：

```text
data/raw/tasks.csv
data/raw/amrs.csv
data/processed/p1/<algorithm>/path_cost.csv
```

输出（接口与原框架一致，P3 直接消费）：

```text
data/processed/p2/assignment_result.csv
data/processed/p2/amr_sequence_summary.csv
```

注意：`transition_travel_time` / `loaded_travel_time` 等时间列写入的是按 `speed` 和载货速度系数（0.90）修正后的实际时间（命题第 5 节），不再是 P1 基准时间。新增列：`estimated_waiting`、`energy_used`、`battery_after`。`assignment_method` 列带 P1 算法后缀（如 `m2_milp_goal_programming+basic_astar`）。

## 对比实验

```powershell
python .\src\p2_assignment\experiments\generate_instances.py
python .\src\p2_assignment\experiments\run_experiments.py --exp 1   # 方法对比
python .\src\p2_assignment\experiments\run_experiments.py --exp 2   # AMR 数量敏感性
python .\src\p2_assignment\experiments\run_experiments.py --exp 3   # 任务密度敏感性
python .\src\p2_assignment\experiments\run_experiments.py --exp 4   # 消融 + 优先级层调换
python .\src\p2_assignment\experiments\run_experiments.py --exp 5   # P1 路径源对比（P1xP2 联动）
python .\src\p2_assignment\experiments\plot_experiment_curves.py    # 实验曲线图
```

结果汇总在 `outputs/p2_experiments/p2_method_comparison.csv`，每行 = 算例 x 方法的全套指标（F1/F2/F3 分量、缺失路径数、电量违反数、solve_time 等），按 (instance, method) 去重追加，中断后重跑自动续算。实验 1-4 默认用 mixed 候选库（`run_experiments.py` 顶部 `P1_ALGORITHM`）。

## 可视化

```powershell
python .\src\p2_assignment\plot_assignment_results.py               # 甘特图/空间图/候选图/方法对比
python .\src\p2_assignment\animate_schedule.py                      # 调度执行动画 GIF
```

输出到 `outputs/p2/`：

| 文件 | 内容 |
| --- | --- |
| `gantt_m0_vs_m3.png` | 上下对照甘特图（空驶/等待/服务载货分段，延期红边） |
| `assignment_map_<m>.png` | 任务链空间图（平面图底 + 各 AMR 路径与执行顺序） |
| `candidate_selection.png` | mixed 候选库中 P2 的择路示例 |
| `method_comparison.png` | m0~m3 四指标柱状对比（需先跑实验一） |
| `sensitivity_*.png` | 实验二/三敏感性曲线（需先跑实验二/三） |
| `path_source_comparison.png` | 实验五路径源对比（需先跑实验五） |
| `schedule_animation_m3.gif` | AMR footprint 沿轨迹随时间移动的执行动画 |

## 与边权图分支（main）的差异

本分支 P1 输出二维平面图候选路径，与 main 的差异及 P2 的适配：

1. `path_cost.csv` 无 `edge_sequence`/`node_sequence`/占用偏移列（P3 改用轨迹样本做冲突检测），P2 输出也相应去掉这两列；
2. 新增 `planning_status` 列，P2 加载时过滤掉规划失败的行；
3. 每个 OD 对当前只有 1 条候选路径（main 上约 3 条），m3 的 reroute 邻域在此数据下自动退化为空操作，待 P1 输出多候选后无需改代码即可恢复；
4. OUT1/OUT2 在二维平面图中可以返回主路网（OUT -> * 的路径存在），main 上的"OUT 死端导致 missing>=1"问题在本分支不存在，验收指标 missing_transition_count = 0 可以达成。
