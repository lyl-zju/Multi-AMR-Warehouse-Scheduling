# P4 动态扰动与滚动时域重排

P4 读取 P3 的冲突修复后时间表、二维轨迹和动态事件，识别受影响任务，并在滚动时域内重新安排未固定任务。当前实现不再是简单后移占位版，而是采用运筹学中的启发式重优化流程：受影响尾段识别、后悔值插入、局部搜索、候选路径重评估和冲突等待修复。

## 输入

```text
data/processed/p3/schedule_result.csv
data/processed/p3/trajectory_schedule.csv
data/raw/dynamic_events.csv
```

## 运行

```powershell
python .\src\p4_dynamic_reschedule\dynamic_reschedule.py
```

## 输出

```text
data/processed/p4/reschedule_result.csv
data/processed/p4/dynamic_event_impact.csv
data/processed/p4/reschedule_summary.csv
data/processed/p4/trajectory_schedule.csv
```

## 动态事件

```text
area_block  带 x,y,width,height,start_time,end_time 的二维区域封锁
amr_delay   AMR 时间窗延误
new_task    运行中新增任务
```

## 当前算法

P4 以动态事件时间为滚动时域边界：

```text
1. 已经进入执行轨迹的任务固定，不再重排；
2. area_block 会检查 P3/P4 轨迹 footprint 是否进入封锁矩形；
3. amr_delay 会识别与延误时间窗重叠的任务；
4. new_task 会把新增任务插入未来计划；
5. 受影响任务及其同车后续尾段进入重排池；
6. 重排时只从 P1 mixed 候选路径中选择路径；
7. 目标按硬约束、延期服务质量、完工时间、路径成本、负载均衡、电量和稳定性字典序比较；
8. 最终轨迹重新检测 footprint / 节点容量冲突，必要时给未来任务增加等待。
```

新增任务事件没有显式 `latest_finish` 字段时，当前按 `release_time + 30` 作为动态任务软期限。

## 运筹学建模语言

P4 可以看作一个带扰动的动态多机器人取送货调度问题，即动态 PDPTW / 多车辆路径调度问题。P3 已经给出一组可执行的基准计划，P4 在事件时刻 `t_e` 到来后，不重新求解全部历史任务，而是在滚动时域内对尚未固定的任务集合做局部重优化。

### 决策对象

在每个滚动时域内，P4 重新决定：

```text
x[k,j]              任务 j 是否由 AMR k 执行
seq[k]              AMR k 的未来任务执行顺序
p_trans[k,j]        AMR k 执行任务 j 前的空驶候选路径
p_load[k,j]         任务 j 的 pickup -> delivery 载货候选路径
S[j], C[j]          任务 j 的开始时间和完成时间
W[k,j]              为避免动态封锁或轨迹冲突增加的等待时间
```

其中，路径变量只能从 P1 `mixed` 路径库中取值，不允许临时生成新路径。已经进入执行轨迹的任务被视为固定决策，不再改变 AMR、顺序和路径。

### 约束条件

P4 保留基础层硬约束：

```text
1. 每个静态任务和动态新增任务必须被且仅被一台 AMR 执行；
2. 每台 AMR 的未来任务形成一条线性序列；
3. 相邻任务必须存在 delivery -> pickup 衔接路径；
4. 每个任务必须存在 pickup -> delivery 载货路径；
5. S[j] >= earliest_start[j]；
6. AMR 实际行驶时间考虑 speed 和 loaded_speed_factor；
7. area_block 时间窗内，轨迹 footprint 不能进入封锁矩形；
8. amr_delay 时间窗内，目标 AMR 不能执行受影响任务；
9. 重排后二维轨迹 footprint 和 P/SORT/OUT 节点容量冲突必须为 0；
10. 电量不得低于 battery_safety_threshold；
11. 基础层不插入充电任务。
```

在实现中，`area_block` 通过候选路径轨迹采样与矩形封锁区的时空相交检测处理；若某条候选路径在封锁时间窗内不可行，则优先尝试其他候选路径，必要时推迟该段移动到封锁结束后。`amr_delay` 通过提高对应 AMR 的可用时间和识别重排池处理。

### 目标函数

P4 使用字典序目标规划思想，而不是单一最短路目标。候选方案先比较硬约束违反数，再比较服务质量和运行效率：

```text
min lex(
  hard_violation_count,
  priority_late_count,
  late_count,
  F2,
  F3,
  stability_penalty
)
```

其中：

```text
F2 = 30 * priority_delay
   + 20 * total_delay
   + 5  * Cmax

F3 = 2  * empty_cost
   + 10 * load_balance_penalty
   + 1  * total_energy_used
```

`stability_penalty` 表示相对 P3 原计划的扰动代价，包括换车和开始时间偏移。这个项让 P4 更接近滚动重调度的常见原则：只在必要时改变未来计划，避免为了局部成本收益大幅推翻原执行方案。

### 与目标规划和 MILP 的关系

从数学建模角度看，P4 可以写成一个带动态事件约束的混合整数规划问题：任务分配、执行顺序、候选路径选择、等待时间和开始完成时间都可以作为决策变量，约束则包括单车序列可行性、时空衔接、封锁区避让、延误窗口和电量安全阈值等。

其中，目标层采用的是目标规划 / 字典序多目标优化思想：先保证可行性，再压低优先级任务延误、总延误、完工时间、空载成本和稳定性损失。

但需要说明的是，当前代码实现并没有调用通用 MILP 求解器去求解完整模型，而是采用“滚动时域 + 后悔值插入 + 局部搜索 + 冲突修复”的启发式算法。也就是说：

```text
数学表达上：可以建成 MILP + 目标规划
代码求解上：当前版本是启发式重优化
```

### 求解策略

当前实现采用启发式而非完整 MILP。原因是 P4 还要结合二维轨迹冲突检测，直接建立完整时空 MILP 会显著增加建模和求解复杂度。具体流程是：

```text
1. 事件影响识别：
   根据 area_block / amr_delay / new_task 找到受影响任务。

2. 滚动时域冻结：
   事件时刻前已经进入轨迹的任务固定，剩余任务才允许重排。

3. 重排池构造：
   对 area_block 和 amr_delay，取受影响任务及其同车后续尾段；
   对 new_task，在未来计划中评估新增任务的插入位置。

4. 后悔值插入：
   对待排任务枚举 AMR 和插入位置，比较最好与次好插入代价，
   优先安排后悔值大的任务。

5. 局部搜索：
   在插入解基础上尝试跨车移动、同车移动和任务交换，
   若字典序目标改善则接受。

6. 路径重评估：
   每次评价任务链时重新选择 transition / loaded 候选路径，
   并检查封锁时间窗下的路径可行性。

7. 冲突修复：
   展开最终二维轨迹，调用 P3 的 footprint / 节点容量冲突检测；
   若存在冲突，则只给未来任务增加等待，直到冲突数为 0 或达到迭代上限。
```

因此，P4 的运筹学定位不是重新做全局静态调度，而是一个带稳定性约束的动态滚动重排启发式：在保留既有执行计划的基础上，对扰动后的未来任务做局部组合优化。

## 验证指标

`reschedule_summary.csv` 输出基础层验收指标，包括：

```text
missing_loaded_path_count
missing_transition_count
unresolved_trajectory_conflict_count
blocked_area_violation_count
delayed_amr_violation_count
all_dynamic_tasks_scheduled
energy_violation_count
priority_late_count
late_count
priority_delay
total_delay
Cmax
empty_cost
load_balance_penalty
total_energy_used
F2
F3
```
