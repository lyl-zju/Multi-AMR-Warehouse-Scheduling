# P2 任务分配、任务排序与路径选择

P2 是项目的任务级静态调度核心。它读取 P1 的候选路径库，在时间窗、电量、路径成本、负载均衡和服务质量约束下，联合决定每个任务由哪台 AMR 执行、每台 AMR 内部任务顺序如何排列、每段空驶或载货移动选择哪条候选路径。

P2 输出的是任务级计划，后续 P3 会继续把该计划展开为二维时空轨迹并修复多 AMR 冲突。

## 本部分在做什么

P2 同时解决三类耦合决策：

| 决策 | 含义 |
| --- | --- |
| 任务分配 | 每个任务由哪台 AMR 执行 |
| 任务排序 | 每台 AMR 内部按什么顺序执行任务 |
| 路径选择 | 每段空驶衔接和载货运输选用哪条 P1 候选路径 |

这三类决策必须联合考虑。任务分配会改变 AMR 后续所在位置；任务排序会影响任务开始时间、完成时间和时间窗；路径选择会影响行驶时间、能耗和空驶成本。若只按最近 AMR 派单，后续任务链可能产生更长空驶、更差负载均衡或时间窗延期。

## 输入

默认主流程使用 P1 mixed 路径库：

```text
data/raw/tasks.csv
data/raw/amrs.csv
data/processed/p1/mixed/path_cost.csv
data/processed/p1/mixed/path_trajectory_samples.csv
```

若只想使用某一种 P1 路径源，也可以通过 `--p1-algorithm basic_astar|vg|avg|davg` 指定。

## mixed 多候选路径库

每个 P1 单算法对同一 OD 通常只输出一条路径，这会使路径选择变量退化。P2 因此提供 `build_mixed_paths.py`，把 `basic_astar`、`vg`、`avg`、`davg` 四类路径源融合成一个多候选库：

```powershell
python .\src\p2_assignment\build_mixed_paths.py
```

融合过程包括：

1. 给不同来源路径加算法前缀，保证 `path_uid` 唯一且可追溯。
2. 使用统一成本口径 `travel_time + 0.5 * turn_count` 重算综合成本。
3. 对同一 OD 下几何形状近似相同的路径进行去重。
4. 将成本表、轨迹样本和矩阵写入 `data/processed/p1/mixed/`。

当前算例融合后，平均每个 OD 约 1.77 条候选路径，最多 4 条候选。

## 采用的求解算法

P2 实现了四类方法，从基线到精确模型再到启发式扩展：

| 方法 | 文件 | 核心思想 | 对应知识点 |
| --- | --- | --- | --- |
| `m0` | `solvers/m0_greedy.py` | 修复版贪心，按任务时间窗依次追加到当前评价最优的 AMR 队尾 | 基线对照 |
| `m1` | `solvers/m1_two_stage.py` | 两阶段法：0-1 指派模型 + 车内 Held-Karp 动态规划排序 | 整数规划、动态规划 |
| `m2` | `solvers/m2_milp.py` | 联合 MILP，同时处理指派、排序、路径选择、时间和电量，按目标规划序贯求解 | 混合整数规划、目标规划、分支切割 |
| `m3` | `solvers/m3_insertion_ls.py` | regret-2 后悔值插入构造初始解，再用 relocate/swap/reroute 局部搜索改进 | 启发式优化、局部搜索 |

所有方法共用 `p2core/evaluator.py` 中的字典序评价：

```text
min lex(
  缺失路径数 + 电量违反数,
  高优先级延期任务数,
  延期任务数,
  F2,
  F3
)
```

其中：

```text
F2 = 30 * sum(priority_i * delay_i)
   + 20 * sum(delay_i)
   + 5 * Cmax

F3 = 2 * empty_cost
   + 10 * load_balance_penalty
   + total_energy
```

这种字典序目标规划避免用低层运行成本抵消高层可行性或服务质量损失。

## 运行

主流程推荐运行：

```powershell
python .\src\p2_assignment\build_mixed_paths.py
python .\src\p2_assignment\assign_and_sequence_tasks.py --method m2 --p1-algorithm mixed
```

切换方法：

```powershell
python .\src\p2_assignment\assign_and_sequence_tasks.py --method m0 --p1-algorithm mixed
python .\src\p2_assignment\assign_and_sequence_tasks.py --method m1 --p1-algorithm mixed
python .\src\p2_assignment\assign_and_sequence_tasks.py --method m2 --p1-algorithm mixed
python .\src\p2_assignment\assign_and_sequence_tasks.py --method m3 --p1-algorithm mixed
```

生成图表和动画：

```powershell
python .\src\p2_assignment\plot_assignment_results.py
python .\src\p2_assignment\animate_schedule.py
```

## 输出

```text
data/processed/p2/assignment_result.csv
data/processed/p2/amr_sequence_summary.csv
```

`assignment_result.csv` 是 P3 的主要输入，包含：

```text
task_id
amr_id
sequence_order
start_node
pickup
delivery
transition_path_uid
loaded_path_uid
estimated_start_time
estimated_finish_time
estimated_waiting
estimated_delay
energy_used
battery_after
assignment_method
```

`transition_travel_time` 和 `loaded_travel_time` 已按 AMR 速度系数和载货速度系数修正，不再是 P1 的原始基准时间。

可视化输出位于：

```text
outputs/p2/
```

主要包括甘特图、任务链空间图、候选路径选择图、方法对比图、敏感性曲线和调度动画 GIF。

## 达到的效果

在报告基础算例中，P2 使用 `m2 + mixed` 得到的静态调度结果为：

| AMR | 任务序列 | 完成时间 | 剩余电量 |
| --- | --- | ---: | ---: |
| AMR1 | T06 -> T03 -> T07 | 40.293444 | 54.7776 |
| AMR2 | T02 -> T05 -> T10 | 40.840111 | 48.0854 |
| AMR3 | T01 -> T09 -> T12 | 38.944691 | 67.5520 |
| AMR4 | T04 -> T08 -> T11 | 38.596970 | 43.4962 |

可行性检查结果：

| 指标 | 结果 |
| --- | --- |
| 空驶路径缺失 | 0 |
| 载货路径缺失 | 0 |
| 电量阈值违反 | 0 |
| 延期任务数 | 0 |
| 静态 `Cmax` | 40.840111 |

这说明 P2 已经生成一份任务级可行、路径级可达、电量安全且负载较均衡的静态计划。不过，P2 不逐时刻比较 AMR 的二维 footprint，因此任务级可行不等于轨迹级无冲突；这一部分由 P3 继续处理。

## 代码简要解读

文件结构：

```text
p2_assignment/
  assign_and_sequence_tasks.py       # 主入口
  build_mixed_paths.py               # 构建 P1 mixed 多候选路径库
  plot_assignment_results.py         # 甘特图、空间图、候选图、方法对比图
  animate_schedule.py                # 调度执行动画
  p2core/
    constants.py                     # 速度、电量、F2/F3 权重等固定参数
    data_io.py                       # Task、Amr、PathLibrary、ProblemData 和数据加载
    timeline.py                      # 任务链时间、电量、路径段推演
    evaluator.py                     # Solution、Evaluation 和字典序指标
    output_writer.py                 # 输出 P3 兼容的 assignment_result.csv
  solvers/
    m0_greedy.py
    m1_two_stage.py
    m2_milp.py
    m3_insertion_ls.py
  experiments/
    generate_instances.py
    run_experiments.py
    plot_experiment_curves.py
```

核心流程：

1. `assign_and_sequence_tasks.py` 根据 `--method` 和 `--p1-algorithm` 选择路径库和求解器。
2. `data_io.load_problem()` 读取任务、AMR 和路径成本，并过滤不可用路径。
3. 对应 `solvers/*.py` 生成 `Solution`，即每台 AMR 的任务序列和路径选择。
4. `timeline.simulate_amr()` 将任务链展开为任务开始、完成、等待、电量和路径段。
5. `evaluator.evaluate()` 统一计算可行性、延期、`Cmax`、空驶、负载均衡和能耗。
6. `output_writer.py` 写出 `assignment_result.csv` 和 `amr_sequence_summary.csv`。

## 对比实验入口

```powershell
python .\src\p2_assignment\experiments\generate_instances.py
python .\src\p2_assignment\experiments\run_experiments.py --exp 1
python .\src\p2_assignment\experiments\run_experiments.py --exp 2
python .\src\p2_assignment\experiments\run_experiments.py --exp 3
python .\src\p2_assignment\experiments\run_experiments.py --exp 4
python .\src\p2_assignment\experiments\run_experiments.py --exp 5
python .\src\p2_assignment\experiments\plot_experiment_curves.py
```

实验结果主要写入：

```text
outputs/p2_experiments/p2_method_comparison.csv
outputs/p2/
```

## 与后续模块的关系

P2 是任务级优化模块，负责给 P3 一个高质量静态任务链。P3 会读取 `assignment_result.csv` 和 P1 mixed 轨迹样本，将每个任务段展开为绝对时间轨迹并检测冲突。若 P3 发现轨迹冲突，会在不跨 AMR 重分配任务的前提下加入等待、换路或同车局部换序进行修复。
