# P3 时空冲突检测与修复

P3 位于 P1 路径规划和 P2 任务调度之后，负责把 P2 给出的静态任务计划展开为可执行的时空轨迹，并检测、修复多 AMR 之间的运行冲突。

当前 P3 的核心原则是：

```text
P2 决定 AMR 分配、任务顺序和初始路径选择；
P3 在不跨 AMR 重分配任务的前提下，做执行层面的冲突检测与局部修复。
```

## 输入

默认使用 P1 mixed 路径库和 P2 mixed 调度结果：

```text
data/processed/p2/assignment_result.csv
data/processed/p1/mixed/path_cost.csv
data/processed/p1/mixed/path_trajectory_samples.csv
```

其中：

```text
assignment_result.csv
P2 输出的 AMR 分配、任务顺序、路径选择和任务时间窗。

path_cost.csv
P1 mixed 候选路径成本表，用于同 OD 换路候选。

path_trajectory_samples.csv
P1 mixed 路径几何采样点，用于展开 P3 时空轨迹。
```

## 运行

在项目根目录运行：

```powershell
python .\src\p3_schedule_conflicts\schedule_and_detect_conflicts.py
```

如需显式指定 P1 算法：

```powershell
python .\src\p3_schedule_conflicts\schedule_and_detect_conflicts.py --p1-algorithm mixed
```

生成 GIF：

```powershell
python .\src\p3_schedule_conflicts\animate_p3_conflict_repair.py
```

## 输出

P3 输出目录：

```text
data/processed/p3/
```

主要输出文件：

```text
schedule_result.csv
trajectory_schedule.csv
conflict_log.csv
repair_summary.csv
```

### schedule_result.csv

最终选中方案的修复后任务时间表。

重要约定：

```text
表头严格沿用 data/processed/p2/assignment_result.csv。
```

这样 P4 可以继续按 P2 的表结构读取，但其中的 `estimated_*` 时间、等待、delay、路径选择和 `assignment_method` 已经是 P3 修复后的结果。新版 P4 会通过 `normalize_p3_schedule()` 将这些字段映射为内部使用的 `start_time`、`finish_time`、`delay`、`waiting_time` 和 `schedule_status`。

### trajectory_schedule.csv

P3 给 P4 的时空轨迹表，只包含最终实际使用的轨迹和等待段。

字段：

```text
amr_id
task_id
sequence_order
segment_type
path_uid
sample_index
absolute_time
offset_time
x
y
theta
speed
footprint_radius
```

P3 会按 0.1 秒粒度展开轨迹。P4 如果要做动态仿真、滚动调度或二次冲突检测，应优先读取该文件。

常见 `segment_type`：

```text
transition
loaded
wait_at_pickup
service_at_pickup
repair_wait_at_node
mid_path_wait_transition
mid_path_wait_loaded
```

### conflict_log.csv

修复过程日志，不是最终执行轨迹。

它记录每轮修复尝试：

```text
mode
iteration
conflict_id
conflict_time
conflict_type
before_conflicts
action
target_amr
target_task
target_sequence_order
segment_type
old_path_uid
new_path_uid
wait_added
after_conflicts
objective
```

用途主要是调试和报告说明。P4 一般不需要依赖该文件。

### repair_summary.csv

三种修复模式的结果摘要和最终选择：

```text
wait
wait_reroute
wait_reroute_resequence
```

字段包括：

```text
mode
selected
initial_conflicts
remaining_conflicts
total_delay
Cmax
total_wait_added
reroute_count
resequence_count
initial_wait_added
objective
selection_rule
```

P4 如果需要知道最终采用哪种模式，可读取：

```text
selected == 1
```

当前稳定结果中，三种模式都能将冲突修到 0，最终选中：

```text
wait_reroute_resequence
```

但当前数据下：

```text
reroute_count = 0
resequence_count = 0
```

说明代码支持换路和同车局部换序，但本实例的最终修复没有实际触发换路或换序；主要由冲突点前的路径中途暂停让行完成。

## 冲突检测模型

空间 footprint 冲突：

```text
distance < footprint_radius_1 + footprint_radius_2 + safety_margin
```

当前参数：

```text
footprint_radius = 0.25
safety_margin = 0.10
clearance threshold = 0.60
TIME_TOLERANCE = 0.25
TRAJECTORY_SAMPLE_STEP = 0.10
```

节点容量冲突：

```text
P*, SORT*, OUT* 同一时刻最多只能有一台 AMR 占用或工作。
```

机器人完成全部任务后，其轨迹结束；结束后的机器人不再参与后续冲突检测。GIF 中完成机器人会淡化显示。

## 修复策略

P3 当前支持以下修复动作：

```text
wait_before_task
任务开始前等待。

mid_path_wait
路径中途暂停，用于模拟快到冲突点前的局部让行。

reroute
从 P1 mixed 中选择同 OD 候选路径替换当前路径。

resequence
同一 AMR 内相邻任务交换，不跨 AMR 重分配任务。
```

P3 比较三种修复模式：

```text
wait
wait_reroute
wait_reroute_resequence
```

选择规则：

```text
先最小化 remaining_conflicts；
再最小化目标函数 objective。
```

当前目标函数：

```text
J = 100 * total_delay
  + 5 * Cmax
  + total_wait_added
  + 2 * reroute_count
  + 20 * resequence_count
  + 20 * initial_wait_added
```

P3 会优先为空间冲突生成冲突点前的中途停车候选，当前提前量包括：

```text
0.8s, 1.0s, 1.5s, 2.0s, 3.0s, 4.0s
```

当某种修复模式已经达到 0 冲突后，P3 还会压缩已插入的等待时间；只有重新检测后仍保持 0 冲突的缩短方案才会被接受。

## 当前结果

当前稳定输出的关键指标：

```text
Original conflicts: 21
Wait conflicts: 0
Wait+reroute conflicts: 0
Wait+reroute+resequence conflicts: 0
Selected P3 mode: wait_reroute_resequence
total_delay: 0.0
Cmax: 46.596970
total_wait_added: 16.0
initial_wait_added: 0.0
objective: 248.984850
```

`initial_conflicts = 21` 表示去重后的冲突事件数。早期版本曾出现 248，是按 0.1 秒采样点逐帧统计的原始冲突记录数。同一场持续一段时间的冲突会在多个采样点被重复检测，因此原始记录数会更大。

当前 AMR2 不再开局长时间等待。AMR2 的首个任务 T02 按原计划开始执行，在载货路径中途通过 `mid_path_wait_loaded` 暂停让行：

```text
T02 estimated_start_time = 3.589
T02 estimated_finish_time = 18.162333
T02 estimated_delay = 0.0
T02 estimated_waiting = 4.0
```

## GIF 输出

动画输出目录：

```text
outputs/p3/
```

当前 GIF：

```text
p3_original.gif
p3_wait_repair.gif
p3_wait_reroute_repair.gif
p3_wait_reroute_resequence_repair.gif
```

含义：

```text
p3_original.gif
P2 原始计划直接展开后的冲突情况。

p3_wait_repair.gif
只等待修复后的结果。

p3_wait_reroute_repair.gif
等待 + 换路修复后的结果。

p3_wait_reroute_resequence_repair.gif
等待 + 换路 + 同车局部换序修复后的结果。
```

## 给 P4 的读取建议

P4 推荐读取：

```text
data/processed/p3/repair_summary.csv
查看 selected == 1，确认最终采用的 P3 修复模式。

data/processed/p3/schedule_result.csv
读取最终任务级时间表，表头兼容 P2。

data/processed/p3/trajectory_schedule.csv
读取最终 0.1 秒粒度时空轨迹。
```

P4 不建议直接使用 P2 的 `assignment_result.csv` 作为最终执行计划，因为 P2 没有处理多 AMR 时空冲突。

新版 P4 可以直接读取当前 P3 的 `schedule_result.csv` 和 `trajectory_schedule.csv`。如果使用旧版 P4，需做以下字段映射：

```text
start_time      <- estimated_start_time
finish_time     <- estimated_finish_time
delay           <- estimated_delay
waiting_time    <- estimated_waiting
schedule_status <- transition_status == ok 且 loaded_path_status == ok
path_uid        <- loaded_path_uid
```

更完整的接口说明见：

```text
docs/P3_冲突检测修复与P4接口说明.md
```
