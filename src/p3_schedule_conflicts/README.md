# P3 时空轨迹展开与冲突修复

P3 位于 P2 静态调度之后，负责把任务级计划推进到轨迹级执行层。P2 已经决定每个任务由哪台 AMR 执行、按什么顺序执行、每段移动走哪条候选路径；但任务级可行不代表二维时空中一定无冲突。P3 的作用就是展开 AMR footprint 轨迹，检测并修复多 AMR 间的空间冲突和节点容量冲突。

## 本部分在做什么

P3 的核心原则是：

```text
P2 决定 AMR 分配、任务顺序和初始路径选择；
P3 不跨 AMR 重分配任务，只做执行层面的轨迹冲突检测与局部修复。
```

具体工作包括：

1. 读取 P2 的 `assignment_result.csv`。
2. 读取 P1 mixed 的路径成本与轨迹采样。
3. 按 AMR 和任务顺序展开 `transition`、等待、服务和 `loaded` 轨迹。
4. 以 0.1 秒粒度检测 AMR footprint 冲突和关键节点容量冲突。
5. 通过等待、换路和同车相邻换序生成候选修复动作。
6. 输出修复后的任务时间表、最终轨迹表、冲突日志和模式摘要。

## 输入

默认使用 P1 mixed 路径库和 P2 mixed 调度结果：

```text
data/processed/p2/assignment_result.csv
data/processed/p1/mixed/path_cost.csv
data/processed/p1/mixed/path_trajectory_samples.csv
```

含义：

| 文件 | 作用 |
| --- | --- |
| `assignment_result.csv` | P2 输出的任务分配、顺序、时间估计、路径选择和电量结果 |
| `path_cost.csv` | 同 OD 换路候选和路径成本 |
| `path_trajectory_samples.csv` | 相对出发时刻的二维轨迹采样点 |

## 采用的模型与算法

### 轨迹展开

P3 将每个任务拆成若干轨迹段：

| 段类型 | 含义 |
| --- | --- |
| `transition` | 从当前节点空驶到任务取货点 |
| `wait_at_pickup` | 早到取货点后的等待 |
| `service_at_pickup` | 取货服务时间 |
| `loaded` | 从取货点载货运行到送货点 |
| `repair_wait_at_node` | 修复冲突时在节点等待 |
| `mid_path_wait_transition` | 空驶路径中途等待 |
| `mid_path_wait_loaded` | 载货路径中途等待 |

每个轨迹点包含：

```text
amr_id, task_id, sequence_order, segment_type,
path_uid, absolute_time, x, y, theta, speed, footprint_radius
```

### 冲突检测

P3 检测两类冲突：

| 冲突类型 | 判定方式 |
| --- | --- |
| footprint 空间冲突 | 两台 AMR 在相近时刻的中心距离小于 `footprint_radius_1 + footprint_radius_2 + safety_margin` |
| 节点容量冲突 | `P*`、`SORT*`、`OUT*` 等关键节点同一时刻最多允许一台 AMR 占用或作业 |

当前参数：

```text
footprint_radius = 0.25
safety_margin = 0.10
clearance threshold = 0.60
TIME_TOLERANCE = 0.25
TRAJECTORY_SAMPLE_STEP = 0.10
```

### 修复策略

P3 支持四类局部动作：

| 动作 | 说明 |
| --- | --- |
| `wait_before_task` | 在任务开始前等待 |
| `mid_path_wait` | 在冲突点前路径中途停车让行 |
| `reroute` | 从 P1 mixed 中选择同 OD 的候选路径替换当前路径 |
| `resequence` | 同一 AMR 内相邻任务交换，不跨 AMR 重分配 |

P3 比较三种修复模式：

```text
wait
wait_reroute
wait_reroute_resequence
```

选择规则为：

```text
先最小化 remaining_conflicts；
若剩余冲突相同，再最小化目标函数 objective。
```

目标函数：

```text
J = 100 * total_delay
  + 5 * Cmax
  + total_wait_added
  + 2 * reroute_count
  + 20 * resequence_count
  + 20 * initial_wait_added
```

在达到 0 冲突后，P3 还会压缩已插入等待时间；只有缩短后仍保持 0 冲突的方案才会被接受。

## 运行

在项目根目录运行：

```powershell
python .\src\p3_schedule_conflicts\schedule_and_detect_conflicts.py --p1-algorithm mixed
```

生成 GIF：

```powershell
python .\src\p3_schedule_conflicts\animate_p3_conflict_repair.py
```

## 输出

输出目录：

```text
data/processed/p3/
outputs/p3/
```

结构化输出：

| 文件 | 作用 |
| --- | --- |
| `schedule_result.csv` | 修复后的任务级时间表，表头沿用 P2 输出格式 |
| `trajectory_schedule.csv` | 修复后的 0.1 秒粒度二维时空轨迹，是 P4 的主要轨迹接口 |
| `conflict_log.csv` | 每轮冲突修复日志 |
| `repair_summary.csv` | 三种修复模式的汇总指标和最终选择 |

动画输出：

```text
outputs/p3/p3_original.gif
outputs/p3/p3_wait_repair.gif
outputs/p3/p3_wait_reroute_repair.gif
outputs/p3/p3_wait_reroute_resequence_repair.gif
```

## 达到的效果

当前稳定结果：

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

三种模式都能把冲突修到 0，最终选中 `wait_reroute_resequence`。当前算例中：

```text
reroute_count = 0
resequence_count = 0
```

也就是说，代码支持换路和同车局部换序，但当前实例主要通过载货路径中途等待让行完成修复。最终轨迹中出现了 `mid_path_wait_loaded` 片段，说明等待并不是简单把整项任务整体后移，而是在冲突点前局部停车。

需要区分两个冲突统计口径：

| 口径 | 含义 |
| --- | --- |
| 21 | 去重后的冲突事件数，用于报告主结果 |
| 早期版本中的 248 | 按 0.1 秒采样点逐帧统计的原始冲突记录数，同一持续冲突会被多次记录 |

## 代码简要解读

主要文件：

```text
schedule_and_detect_conflicts.py      # P3 主流程
animate_p3_conflict_repair.py         # P3 轨迹和冲突动画
```

`schedule_and_detect_conflicts.py` 中的重要结构：

| 函数/类 | 作用 |
| --- | --- |
| `RepairState` | 保存当前修复状态，包括任务表、等待、换路和换序 |
| `load_inputs()` | 读取 P2 任务表、P1 路径成本和轨迹样本 |
| `build_schedule()` | 将任务级计划展开为任务时间表和轨迹表 |
| `expand_trajectory()` | 按实际时间缩放 P1 轨迹采样，生成绝对时间轨迹 |
| `detect_trajectory_conflicts()` | 检测 AMR footprint 空间冲突 |
| `detect_node_capacity_conflicts()` | 检测关键节点容量冲突 |
| `mid_wait_candidates()` | 生成路径中途等待候选 |
| `reroute_candidates()` | 生成同 OD 换路候选 |
| `resequence_candidates()` | 生成同车相邻换序候选 |
| `run_repair_mode()` | 对某一种模式迭代修复冲突 |
| `run_all_modes()` | 比较 `wait`、`wait_reroute`、`wait_reroute_resequence` 三种模式 |
| `p2_compatible_schedule()` | 输出表头兼容 P2 的 `schedule_result.csv` |

动画脚本 `animate_p3_conflict_repair.py` 会读取仓库底图、轨迹表和冲突日志，用 AMR 圆形 footprint、尾迹和冲突标记生成 GIF。

## 给 P4 的接口说明

P4 推荐读取：

```text
data/processed/p3/schedule_result.csv
data/processed/p3/trajectory_schedule.csv
data/processed/p3/repair_summary.csv
```

其中 `schedule_result.csv` 表头兼容 P2，但时间、等待、延期和方法标记已经是 P3 修复后的结果。P4 可以将字段映射为：

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

## 方法边界

P3 是启发式执行层修复模块，不声称求解全局最优多机器人路径协调问题。它的优势是结构清晰、动作可解释、输出可复核，并能稳定衔接 P4。若要进一步提高全局性，可以在当前框架上加入局部 MILP、CP-SAT、CBS/MAPF 或时空网络流模型，但这会显著增加变量规模和求解复杂度。
