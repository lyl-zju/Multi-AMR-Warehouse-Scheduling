# P4 动态事件与滚动时域重排

P4 位于 P3 之后，是项目从“静态可执行计划”走向“动态仓储运行”的模块。P3 已经给出无冲突基准计划；P4 在通道封锁、AMR 延误和新增任务到来后，冻结已经执行或不应改动的历史任务，只对受影响的未来任务池做滚动时域局部重排，并重新生成可验证的二维轨迹。

## 本部分在做什么

P4 的核心任务是动态扰动后的计划恢复：

1. 读取 P3 修复后的任务级计划和二维轨迹。
2. 读取 `dynamic_events.csv` 中的动态事件。
3. 判断每个事件实际影响哪些 AMR、任务或空间区域。
4. 将受影响任务及其同车后续尾段释放到重排池。
5. 对未来任务池做后悔值插入、局部搜索、候选路径重评估和冲突等待修复。
6. 输出新的任务表、事件影响表、动态轨迹、可行性指标和可视化结果。

P4 不简单地把所有任务整体后移，也不推翻全部历史计划。它的特点是滚动时域：事件发生前的执行结果成为新初始状态，事件后的未来任务才进入重优化。

## 输入

```text
data/processed/p3/schedule_result.csv
data/processed/p3/trajectory_schedule.csv
data/raw/dynamic_events.csv
data/raw/tasks.csv
data/raw/amrs.csv
data/processed/p1/mixed/path_cost.csv
data/processed/p1/mixed/path_trajectory_samples.csv
```

其中 P3 的 `trajectory_schedule.csv` 很关键。封锁区影响判断、AMR 延误窗口判断和最终二次冲突检测都依赖轨迹级数据，而不是只看任务开始和完成时间。

## 动态事件类型

P4 当前处理三类事件：

| event_type | 参数 | 处理方式 |
| --- | --- | --- |
| `area_block` | `x,y,width,height,start_time,end_time` | 检查封锁时间窗内轨迹 footprint 是否进入封锁矩形，若影响未来任务则触发局部重排 |
| `amr_delay` | 目标 AMR、延误开始与结束时间 | 释放延误窗口重叠任务及其同车后续尾段，从延误结束后重新安排 |
| `new_task` | 新任务取货点、送货点、释放时间、优先级等 | 将新任务加入未来任务池，枚举 AMR 和插入位置 |

若新增任务没有显式 `latest_finish`，当前实现使用 `release_time + 30` 作为软期限，进入延期指标和目标函数。

## 采用的模型与算法

### 运筹学抽象

P4 可以抽象为带动态事件约束的动态 PDPTW / 多车辆路径调度问题。数学上可写为混合整数规划：

| 决策 | 含义 |
| --- | --- |
| 任务分配 | 动态事件后的未来任务由哪台 AMR 执行 |
| 任务排序 | 每台 AMR 的未来任务链如何衔接 |
| 路径选择 | 每段空驶和载货移动从 P1 mixed 中选择哪条路径 |
| 时间递推 | 任务开始、完成、等待、延期如何计算 |
| 稳定性 | 相对 P3 基准计划是否换车或大幅偏移 |

约束包括任务唯一分配、AMR 任务链、路径存在、电量安全、时间窗、封锁区避让、AMR 延误窗口避让和二维轨迹 footprint 无冲突。

### 当前代码求解策略

完整时空 MILP 会引入大量路径、时间和互斥变量，因此当前实现采用启发式滚动重排：

1. **事件影响识别**：用 P3/P4 轨迹判断封锁、延误、新任务是否影响当前计划。
2. **滚动冻结**：事件时刻前已完成或不受事件影响的任务固定。
3. **重排池构造**：受影响任务及其同车后续尾段进入未来任务池。
4. **后悔值插入**：优先安排“最好位置与次好位置差距大”的任务。
5. **新任务短名单修复**：枚举新增任务插入位置，保留有代表性的候选，做有限冲突修复后比较。
6. **局部搜索**：尝试跨车移动、同车移动和任务交换，若字典序目标改善则接受。
7. **路径重评估**：每次评价任务链时重新选择 P1 mixed 候选路径。
8. **冲突修复**：展开最终轨迹，复用 P3 的 footprint / 节点容量冲突检测，必要时给未来任务增加等待。

### 目标函数口径

P4 采用字典序目标规划，先看硬约束，再看服务质量、完工时间、运行成本和稳定性：

```text
min lex(
  hard_violation_count,
  total_delay,
  priority_late_count,
  late_count,
  Cmax,
  changed_tasks,
  F3
)
```

硬约束包括：

```text
unresolved_trajectory_conflict_count
blocked_area_violation_count
delayed_amr_violation_count
missing_loaded_path_count
missing_transition_count
energy_violation_count
```

只有硬约束为 0 的方案才具有运营意义。

## 运行

主流程：

```powershell
python .\src\p4_dynamic_reschedule\dynamic_reschedule.py
```

生成可视化图和 GIF：

```powershell
python .\src\p4_dynamic_reschedule\plot_p4_results.py
python .\src\p4_dynamic_reschedule\plot_p4_results.py --fps 8 --speedup 2
```

## 输出

结构化输出：

```text
data/processed/p4/reschedule_result.csv
data/processed/p4/dynamic_event_impact.csv
data/processed/p4/reschedule_summary.csv
data/processed/p4/trajectory_schedule.csv
data/processed/p4/p4_method_comparison.csv
data/processed/p4/p4_weight_sensitivity.csv
```

可视化输出：

```text
outputs/p4/p4_gantt_events.png
outputs/p4/p4_trajectory_map.png
outputs/p4/p4_event_impact_metrics.png
outputs/p4/p4_method_comparison.png
outputs/p4/p4_weight_sensitivity.png
outputs/p4/p4_dynamic_reschedule.gif
```

各图用途：

| 文件 | 说明 |
| --- | --- |
| `p4_gantt_events.png` | 对比 P3 基准计划和 P4 重排计划，并叠加动态事件窗口 |
| `p4_trajectory_map.png` | 展示 P4 最终轨迹、封锁区、新增任务和任务顺序 |
| `p4_event_impact_metrics.png` | 展示事件影响、硬约束验证和目标函数指标 |
| `p4_method_comparison.png` | 展示滚动时域主结果或慢基线对比 |
| `p4_weight_sensitivity.png` | 记录不同目标权重口径下的结果 |
| `p4_dynamic_reschedule.gif` | 动态展示封锁、延误、新任务释放和最终重排轨迹 |

## 达到的效果

基于最新 P3 输出重新计算后，P4 当前主结果为：

```text
动态事件数: 3
受影响事件数: 2
新增动态任务数: 1
变更或插入任务数: 4
P3 基准 Cmax: 46.596970
P4 新 Cmax: 61.918082
P4 total_delay: 23.405971
F2: 2182.068048
F3: 507.179375
剩余轨迹冲突: 0
封锁区违规: 0
AMR 延误违规: 0
缺失路径数: 0
电量阈值违反数: 0
```

动态事件影响范围：

| 事件 | 类型 | 影响 |
| --- | --- | --- |
| E01 | `area_block` | 封锁窗口内没有轨迹 footprint 与封锁矩形相交，因此不触发任务重排 |
| E02 | `amr_delay` | AMR2 在 `[24, 30]` 延误，影响 T05 及同车后续 T10 |
| E03 | `new_task` | 新任务 T13 进入滚动窗口并最终插入 AMR4 |

最终重排中，T05 和 T10 因 AMR2 延误而后移；T13 被插入 AMR4 并排在 T11 前执行；T11 因 T13 前置被推迟。该方案比将 T13 插入 AMR2 延误尾段的候选解有更低总延期和更短 `Cmax`。

## 代码简要解读

主要文件：

```text
dynamic_reschedule.py       # 动态重排主流程
plot_p4_results.py          # 图表和 GIF 生成
```

`dynamic_reschedule.py` 中的重要结构：

| 函数/类 | 作用 |
| --- | --- |
| `TaskSpec`、`AmrSpec`、`AmrState`、`AreaBlock` | 描述任务、AMR、滚动状态和封锁区 |
| `normalize_p3_schedule()` | 将 P3 兼容 P2 的表字段转为 P4 内部字段 |
| `path_candidates()` / `choose_path()` | 从 P1 mixed 中读取并选择候选路径 |
| `schedule_sequences()` | 给定未来任务序列后递推开始时间、完成时间、电量和轨迹 |
| `metrics_tuple()` / `objective_score()` | 计算字典序指标和标量辅助评分 |
| `solve_by_regret_insertion()` | 后悔值插入构造未来任务序列 |
| `local_search()` | 对任务序列做局部改进 |
| `area_block_impact()` / `amr_delay_impact()` / `dynamic_task_from_event()` | 识别三类动态事件影响 |
| `build_amr_states()` | 根据固定任务构造事件时刻 AMR 状态 |
| `insert_new_task_by_position()` | 枚举并评估新增任务插入位置 |
| `repair_conflicts()` | 对最终轨迹做等待型冲突修复 |
| `run_rolling_reschedule()` | 按事件顺序执行滚动重排 |
| `build_reschedule_result()` / `build_summary()` | 写出结果表和汇总指标 |

`plot_p4_results.py` 负责读取 P4 输出，绘制甘特图、轨迹图、事件影响指标图、方法对比图、权重敏感性图和动态 GIF。

## 方法对比与慢实验

`p4_method_comparison.csv` 默认优先记录滚动时域主结果。全局重排和仅等待基线在最新 P3 输出下会显著拖慢计算，适合作为单独实验运行：

| 方法 | 含义 |
| --- | --- |
| 全局重排 | 对事件后的受影响任务池做更激进的后悔值插入和局部搜索 |
| 仅等待 | 保持原 AMR 分配和任务顺序，只通过等待和新任务末端插入修复 |
| 滚动时域重排 | 对封锁/延误保持局部稳定，对新增任务做滚动窗口位置插入 |

`p4_weight_sensitivity.csv` 当前默认复用均衡权重下的可行解，以保持输出结构完整。若要展示完整目标权重敏感性，需要单独运行较慢的三种目标口径实验。

## 与 P2/P3 的区别

P2 解决静态任务级联合调度；P3 在不跨车重分配任务的前提下做轨迹冲突修复；P4 发生在动态事件到来之后，允许对事件后的未来任务池进行局部重排。P4 的评价不能只看延期或 `Cmax`，必须先确认剩余轨迹冲突、封锁区违规和 AMR 延误违规均为 0。
