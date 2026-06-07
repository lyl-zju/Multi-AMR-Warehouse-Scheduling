# 多 AMR 仓储调度简易框架

## 目录结构

```text
amr_warehouse_framework/
  data/raw/
    nodes.csv      # 仓库节点：入库区、货架、分拣台、出库区、充电点、路口
    edges.csv      # 通道数据：长度、通行时间、容量、风险、通道类型
    amrs.csv       # AMR 初始位置、电量、速度
    tasks.csv      # 任务取货点、送货点、服务时间、时间窗、优先级
    dynamic_events.csv # 动态事件：通道封锁、AMR 延误、临时新增任务
  data/processed/
    key_nodes.csv
    path_cost.csv
    path_edge_occupancy.csv
    path_node_occupancy.csv
    path_cost_matrix_time.csv
    path_cost_matrix_total.csv
    assignment_result.csv
    amr_sequence_summary.csv
    schedule_result.csv
    edge_occupancy_schedule.csv
    node_occupancy_schedule.csv
    conflict_log.csv
    reschedule_result.csv
    dynamic_event_impact.csv
    reschedule_summary.csv
  src/
    p0_data_scene/
      plot_warehouse_network.py
      README.md
    p1_candidate_paths/
      generate_candidate_paths.py
      plot_candidate_paths.py
      README.md
    p2_assignment/
      assign_and_sequence_tasks.py
      README.md
    p3_schedule_conflicts/
      schedule_and_detect_conflicts.py
      README.md
    p4_dynamic_reschedule/
      dynamic_reschedule.py
      README.md
    p5_experiment_analysis/
      README.md
  outputs/
    warehouse_network.png
    cost_matrix_time.png
    cost_matrix_total.png
    paths/
      candidate_paths_*.png
```

## 环境依赖

需要 Python，并安装以下库：

```powershell
pip install pandas networkx matplotlib numpy
```

如果你使用 Anaconda，一般这些库已经装好。

## P0：绘制仓库路网图

在项目目录下运行：

```powershell
python .\src\p0_data_scene\plot_warehouse_network.py
```

输出：

```text
outputs/warehouse_network.png
```

这张图用于报告 6.1 算例场景，展示仓库路网、AMR 初始位置、任务取货点和送货点。

## P1：候选路径生成

运行：

```powershell
python .\src\p1_candidate_paths\generate_candidate_paths.py
```

该脚本会读取：

- `data/raw/nodes.csv`
- `data/raw/edges.csv`
- `data/raw/amrs.csv`
- `data/raw/tasks.csv`

然后自动生成关键节点集合：

- AMR 初始节点
- 任务取货节点
- 任务送货节点
- 充电节点

输出：

```text
data/processed/key_nodes.csv
data/processed/path_cost.csv
data/processed/path_edge_occupancy.csv
data/processed/path_node_occupancy.csv
data/processed/path_cost_matrix_time.csv
data/processed/path_cost_matrix_total.csv
```

其中 `path_cost.csv` 是 P1 的核心输出，后续 P2 任务分配模块可以直接使用。`path_edge_occupancy.csv` 是给 P3/P4 做通道冲突检测和动态重排用的结构化通道占用明细，`path_node_occupancy.csv` 是给 P3 做路口占用检测用的节点到达明细。

### P1 与后续模块的数据链路

P1 不直接决定最终调度，它是后面调度模型的底层数据供应模块。

| P1 输出                     | 给谁用                       | 用途                                      |
| --------------------------- | ---------------------------- | ----------------------------------------- |
| `distance`                | P2 任务分配与排序            | 估计 AMR 空驶距离、载货距离、任务衔接距离 |
| `travel_time`             | P2/P3 任务排序与时间调度     | 计算任务开始时间、完成时间、等待时间      |
| `total_cost`              | P2 任务分配与排序            | 判断哪台 AMR 做哪个任务、任务之间如何排序 |
| `node_sequence`           | P3/P4 路径解释与动态重排     | 展示 AMR 经过哪些节点                     |
| `edge_sequence`           | P3/P4 冲突检测与通道封锁分析 | 判断路径是否经过某条通道                  |
| `edge_occupancy_offset`   | P3 冲突检测                  | 根据出发时间推算通道占用时间段            |
| `node_occupancy_offset`   | P3 路口冲突检测              | 根据出发时间推算到达各节点的时刻          |
| `path_edge_occupancy.csv` | P3/P4 冲突消解与动态重排     | 逐边判断通道占用、容量冲突、封锁影响      |
| `path_node_occupancy.csv` | P3 冲突消解                  | 逐节点判断路口占用冲突                    |

因此 P1 的接口可以理解为：

```text
仓库路网 + AMR 初始点 + 任务点
        ↓
关键节点集合 key_nodes.csv
        ↓
候选路径 path_cost.csv
        ↓
边占用明细 path_edge_occupancy.csv
节点占用明细 path_node_occupancy.csv
        ↓
给 P2/P3/P4 使用
```

### 核心接口字段

`path_cost.csv` 中最重要的字段如下：

| 字段                      | 含义                                       | 主要用途             |
| ------------------------- | ------------------------------------------ | -------------------- |
| `path_uid`              | 全局唯一路径编号，例如 `IN1__P5__path_1` | 后续模块引用具体路径 |
| `from_node`             | 起点                                       | 成本查询             |
| `to_node`               | 终点                                       | 成本查询             |
| `path_id`               | 同一起终点内部的候选路径编号               | 展示和排序           |
| `travel_time`           | 路径通行时间                               | 时间调度             |
| `distance`              | 路径距离                                   | 任务分配成本         |
| `total_cost`            | 综合成本                                   | 任务分配、任务排序   |
| `node_sequence`         | 节点序列                                   | 路径展示             |
| `edge_sequence`         | 边序列                                     | 封锁判断、冲突检测   |
| `edge_occupancy_offset` | 相对出发时间的边占用区间                   | 冲突检测             |
| `node_occupancy_offset` | 相对出发时间的节点到达时刻                 | 路口冲突检测         |

`edge_occupancy_offset` 的格式示例：

```text
e01@0-2;e03@2-4;e06@4-6
```

含义是：如果 AMR 在时间 `t0` 出发，那么它会在 `[t0+0, t0+2)` 占用 `e01`，在 `[t0+2, t0+4)` 占用 `e03`，在 `[t0+4, t0+6)` 占用 `e06`。

`path_edge_occupancy.csv` 则把上面的信息拆成逐行明细：

| 字段                                       | 含义                       |
| ------------------------------------------ | -------------------------- |
| `path_uid`                               | 所属候选路径               |
| `step_index`                             | 路径中的第几段边           |
| `edge_id`                                | 占用通道编号               |
| `from_node_on_edge`, `to_node_on_edge` | 本次经过该通道的方向       |
| `offset_start`, `offset_end`           | 相对出发时间的占用起止时刻 |
| `capacity`                               | 通道容量                   |
| `edge_type`                              | 通道类型                   |
| `lockable`                               | 是否可能被封锁             |

`node_occupancy_offset` 的格式示例：

```text
IN1@0;J1@2;J2@4;P2@6
```

含义是：如果 AMR 在时间 `t0` 出发，那么它会在 `t0+0` 位于 `IN1`，在 `t0+2` 到达 `J1`，在 `t0+4` 到达 `J2`。`path_node_occupancy.csv` 把这些节点到达时刻拆成逐行明细，后续可以用来检查同一时刻是否有多台 AMR 到达同一路口。

### 路径生成逻辑

对任意两个关键节点，脚本会生成若干候选路径：

1. `dijkstra_time`：按通行时间最短生成路径
2. `dijkstra_total`：按综合成本最小生成路径
3. `k_shortest_total`：按综合成本生成前 3 条简单路径

重复路径会自动合并，最终每组起终点最多保留若干条不同候选路径。

### 综合成本定义

当前综合成本为：

```text
total_cost = travel_time
           + risk
           + narrow_edge_penalty
           + dynamic_edge_penalty
           + lockable_edge_penalty
           + turn_cost
```

其中：

- 窄通道会增加惩罚
- 动态通道会增加惩罚
- 可封锁通道会增加惩罚
- 每次转弯增加 `0.5` 的转弯成本

这些权重可以在 `src/p1_candidate_paths/generate_candidate_paths.py` 顶部修改。

## P1：候选路径可视化

先运行候选路径生成脚本，再运行：

```powershell
python .\src\p1_candidate_paths\plot_candidate_paths.py
```

输出：

```text
outputs/paths/candidate_paths_IN1_P5.png
outputs/paths/candidate_paths_P5_SORT1.png
outputs/paths/candidate_paths_P8_OUT2.png
outputs/cost_matrix_time.png
outputs/cost_matrix_total.png
```

这些图可以用于报告 6.2 路径模块对比：

- 候选路径图：展示同一起终点的 2-3 条备选路线
- 通行时间矩阵：展示关键节点之间的最短通行时间
- 综合成本矩阵：展示考虑风险、窄通道、动态通道和转弯后的路径成本

矩阵热力图中的灰色空格表示当前路网设定下不可达。例如 `OUT1` 和 `OUT2` 目前是单向出库终点，所以从出库点返回其他节点没有路径。如果后续 P2 需要 AMR 完成出库任务后继续执行下一单，可以在 `edges.csv` 中增加出库区返回主路网的通道。

## P2：任务分配与任务排序

P2 回答两个问题：

1. 哪台 AMR 执行哪个任务？
2. 每台 AMR 内部按什么顺序执行任务？

当前脚本先搭好输入输出接口，具体优化算法暂时使用 `baseline_interface_stub` 占位。后续可以替换为 0-1 指派模型、混合整数规划、遗传算法、禁忌搜索或其他启发式算法。

运行前需要先运行 P1：

```powershell
python .\src\p1_candidate_paths\generate_candidate_paths.py
```

然后运行 P2：

```powershell
python .\src\p2_assignment\assign_and_sequence_tasks.py
```

P2 输入：

```text
data/raw/tasks.csv
data/raw/amrs.csv
data/processed/path_cost.csv
```

P2 输出：

```text
data/processed/assignment_result.csv
data/processed/amr_sequence_summary.csv
```

### assignment_result.csv

这是 P2 给 P3 的主接口。分工文档中的核心字段是：

```text
amr_id, task_id, sequence_order
```

当前框架在这三个字段外，又额外保留了路径和估计时间信息，方便 P3 直接生成时间表：

| 字段                                 | 含义                                       | 给谁用   |
| ------------------------------------ | ------------------------------------------ | -------- |
| `amr_id`                           | 执行该任务的 AMR                           | P3       |
| `assigned_amr`                     | 与 `amr_id` 相同，保留给报告表述         | P2/P3    |
| `task_id`                          | 任务编号                                   | P3       |
| `sequence_order`                   | 该任务在对应 AMR 内的执行顺序              | P3       |
| `predecessor_task_id`              | 同一 AMR 的前一个任务                      | P3       |
| `start_node`                       | 执行本任务前 AMR 所在节点                  | P3       |
| `pickup`, `delivery`             | 任务取货点和送货点                         | P3       |
| `transition_path_uid`              | 从 `start_node` 到 `pickup` 的空驶路径 | P3       |
| `loaded_path_uid`                  | 从 `pickup` 到 `delivery` 的载货路径   | P3       |
| `transition_travel_time`           | 空驶时间                                   | P3       |
| `loaded_travel_time`               | 载货运输时间                               | P3       |
| `transition_cost`, `loaded_cost` | 空驶和载货综合成本                         | P2/P5    |
| `estimated_start_time`             | 简化估计的任务开始时间                     | P3/P5    |
| `estimated_finish_time`            | 简化估计的任务完成时间                     | P3/P5    |
| `estimated_delay`                  | 简化估计的延期时间                         | P5       |
| `transition_status`                | 空驶衔接路径是否存在                       | P3/P4    |
| `loaded_path_status`               | 载货路径是否存在                           | P3/P4    |
| `assignment_method`                | 当前分配方法                               | 报告说明 |

如果 `transition_status = missing_path`，表示当前任务排序中，前一任务送达点到本任务取货点在当前路网下不可达。这个字段是故意保留的，因为它能帮助后续正式算法加入“路径可达性约束”。

### amr_sequence_summary.csv

这个文件用于快速检查每台 AMR 的任务序列：

| 字段                          | 含义               |
| ----------------------------- | ------------------ |
| `amr_id`                    | AMR 编号           |
| `task_count`                | 分配到的任务数量   |
| `task_sequence`             | 任务执行顺序       |
| `missing_transition_count`  | 不可达任务衔接数量 |
| `missing_loaded_path_count` | 不可达载货路径数量 |
| `estimated_finish_time`     | 简化估计完成时间   |
| `estimated_total_delay`     | 简化估计总延期     |

## P3：时间调度、路径占用与冲突检测

P3 回答五个问题：

1. 每个任务什么时候开始？
2. 每个任务什么时候完成？
3. AMR 在什么时间占用哪些通道？
4. AMR 在什么时间到达哪些路口或节点？
5. 是否存在通道或路口冲突？

当前脚本先搭好输入输出接口，具体冲突消解算法暂时使用 `not_repaired_interface_stub` 占位。也就是说：脚本会检测冲突并记录冲突，但暂时不会真正调整等待、换路或重排任务。

运行前需要先运行 P1 和 P2：

```powershell
python .\src\p1_candidate_paths\generate_candidate_paths.py
python .\src\p2_assignment\assign_and_sequence_tasks.py
```

然后运行 P3：

```powershell
python .\src\p3_schedule_conflicts\schedule_and_detect_conflicts.py
```

P3 输入：

```text
data/processed/assignment_result.csv
data/processed/path_cost.csv
data/processed/path_edge_occupancy.csv
data/processed/path_node_occupancy.csv
data/raw/edges.csv
data/raw/nodes.csv
```

P3 输出：

```text
data/processed/schedule_result.csv
data/processed/edge_occupancy_schedule.csv
data/processed/node_occupancy_schedule.csv
data/processed/conflict_log.csv
```

### schedule_result.csv

这是 P3 给 P4/P5 的任务级时间表。分工文档中的核心字段是：

```text
amr_id, task_id, start_time, finish_time, path_id, waiting_time, delay
```

当前框架额外保留了路径和状态字段：

| 字段                    | 含义                   | 给谁用 |
| ----------------------- | ---------------------- | ------ |
| `amr_id`              | AMR 编号               | P4/P5  |
| `task_id`             | 任务编号               | P4/P5  |
| `sequence_order`      | 该 AMR 内部任务顺序    | P4/P5  |
| `start_time`          | 任务取货服务开始时间   | P4/P5  |
| `finish_time`         | 任务完成时间           | P4/P5  |
| `path_id`             | 载货路径编号           | P4/P5  |
| `path_uid`            | 载货路径全局编号       | P4     |
| `transition_path_uid` | 空驶衔接路径编号       | P4     |
| `loaded_path_uid`     | 载货路径编号           | P4     |
| `waiting_time`        | 到达取货点后的等待时间 | P5     |
| `delay`               | 延期时间               | P5     |
| `is_delayed`          | 是否延期               | P5     |
| `schedule_status`     | 是否成功生成时间表     | P3/P4  |

如果 `schedule_status = missing_path`，表示当前 P2 排序中存在不可达路径，因此该任务暂时无法生成完整时间表。当前路网里 `OUT1/OUT2` 是单向出库终点，所以可能会出现这种情况。

### edge_occupancy_schedule.csv

这是通道占用明细表，由 P1 的 `path_edge_occupancy.csv` 加上 P3 的实际出发时间展开得到。

| 字段                         | 含义                                      |
| ---------------------------- | ----------------------------------------- |
| `amr_id`                   | 占用该通道的 AMR                          |
| `task_id`                  | 对应任务                                  |
| `segment_type`             | `transition` 空驶段或 `loaded` 载货段 |
| `path_uid`                 | 对应候选路径                              |
| `edge_id`                  | 被占用通道                                |
| `start_time`, `end_time` | 绝对占用时间段                            |
| `capacity`                 | 通道容量                                  |
| `edge_type`                | 通道类型                                  |
| `lockable`                 | 是否可能被封锁                            |

P3 的通道冲突检测就基于这张表：如果同一时间段内占用同一 `edge_id` 的 AMR 数量超过 `capacity`，就记录冲突。

### node_occupancy_schedule.csv

这是节点/路口到达明细表，由 P1 的 `path_node_occupancy.csv` 加上实际出发时间展开得到。

| 字段             | 含义             |
| ---------------- | ---------------- |
| `amr_id`       | 到达该节点的 AMR |
| `task_id`      | 对应任务         |
| `segment_type` | 空驶段或载货段   |
| `path_uid`     | 对应候选路径     |
| `node_id`      | 到达节点         |
| `time`         | 绝对到达时刻     |

当前默认节点容量为 `1`，如果同一时刻多台 AMR 到达同一节点，就记录为路口冲突。

### conflict_log.csv

这是 P3 的冲突摘要表：

| 字段               | 含义                                     |
| ------------------ | ---------------------------------------- |
| `conflict_id`    | 冲突编号                                 |
| `time`           | 冲突时间或时间段                         |
| `location`       | 冲突位置，可能是通道或节点               |
| `location_type`  | `edge` 或 `node`                     |
| `involved_amrs`  | 涉及 AMR                                 |
| `involved_tasks` | 涉及任务                                 |
| `capacity`       | 位置容量                                 |
| `active_count`   | 同时占用数量                             |
| `repair_action`  | 当前为占位值，后续可改成等待、换路、重排 |

### 报告中如何展示 P3 成果

P3 在报告中不要只展示代码或完整 CSV，而是展示“任务顺序如何变成时间表，以及如何发现冲突”。建议展示以下 4 类内容。

#### 1. AMR 任务时间表

使用 `schedule_result.csv` 做一张精简表，回答：

> 每台 AMR 什么时候开始做哪个任务，什么时候完成？

建议表格字段：

| AMR  | 任务 | 顺序 | 取货点 | 送货点 | 开始时间 | 完成时间 | 等待时间 | 延期 | 状态      |
| ---- | ---- | ---: | ------ | ------ | -------: | -------: | -------: | ---: | --------- |
| AMR1 | T01  |    1 | P1     | SORT1  |        4 |       13 |        0 |    0 | scheduled |
| AMR1 | T06  |    2 | P2     | SORT1  |       22 |       33 |        0 |    0 | scheduled |

如果篇幅有限，正文展示前 6-10 行，完整结果放附录或说明文件路径。

#### 2. AMR 调度甘特图

推荐后续把 `schedule_result.csv` 画成甘特图：

- 横轴：时间
- 纵轴：AMR
- 色块：任务执行区间
- 空白：等待或空驶时间
- 红色边框或标记：延期任务或不可调度任务

这张图是 P3 最直观的展示图，回答：

> 多台 AMR 的任务在时间上是如何展开的？

当前框架暂未生成甘特图，但 `schedule_result.csv` 已经包含绘图所需字段：

```text
amr_id, task_id, start_time, finish_time, waiting_time, delay, schedule_status
```

#### 3. 通道/节点占用示例

使用 `edge_occupancy_schedule.csv` 和 `node_occupancy_schedule.csv` 展示一个局部示例，回答：

> AMR 的路径如何转化成具体时间段内的通道和路口占用？

通道占用示例表：

| AMR  | 任务 | 阶段       | 路径              | 通道 | 开始 | 结束 | 通道类型 | 容量 |
| ---- | ---- | ---------- | ----------------- | ---- | ---: | ---: | -------- | ---: |
| AMR1 | T01  | transition | IN1__P1__path_1   | e01  |    0 |    2 | normal   |    2 |
| AMR1 | T01  | loaded     | P1__SORT1__path_1 | e10  |    6 |    8 | narrow   |    1 |

节点占用示例表：

| AMR  | 任务 | 阶段       | 路径             | 节点 | 到达时间 |
| ---- | ---- | ---------- | ---------------- | ---- | -------: |
| AMR1 | T01  | transition | IN1__P1__path_1  | J1   |        2 |
| AMR3 | T05  | transition | CHG1__P6__path_1 | J1   |        2 |

这个例子可以直接引出冲突检测：如果同一时刻有多台 AMR 到达同一路口，就形成路口冲突。

#### 4. 冲突检测结果表

使用 `conflict_log.csv` 展示检测到的冲突，回答：

> 当前任务时间表下，多台 AMR 是否会同时占用同一通道或路口？

建议表格字段：

| 冲突编号 | 时间 | 位置 | 类型 | 涉及 AMR  | 涉及任务 | 容量 | 同时占用数 | 处理方式 |
| -------- | ---- | ---- | ---- | --------- | -------- | ---: | ---------: | -------- |
| C001     | 2    | J1   | node | AMR1;AMR3 | T01;T05  |    1 |          2 | 待修复   |

当前 `repair_action = not_repaired_interface_stub`，表示本阶段只完成冲突检测，真正的等待、换路或重排策略后续再实现。

#### 5. P3 指标摘要

最后建议放一个小的指标汇总表，帮助读者快速理解 P3 结果：

| 指标           | 含义                                          | 来源文件                        |
| -------------- | --------------------------------------------- | ------------------------------- |
| 任务数         | 总任务数量                                    | `schedule_result.csv`         |
| 成功调度任务数 | `schedule_status = scheduled` 的任务数量    | `schedule_result.csv`         |
| 缺失路径任务数 | `schedule_status = missing_path` 的任务数量 | `schedule_result.csv`         |
| 通道占用记录数 | AMR 占用通道的记录数量                        | `edge_occupancy_schedule.csv` |
| 节点占用记录数 | AMR 到达节点的记录数量                        | `node_occupancy_schedule.csv` |
| 冲突数         | 检测到的通道/节点冲突数量                     | `conflict_log.csv`            |

在当前算例中，脚本运行后会打印这些关键信息，例如：

```text
Scheduled task rows: 12
Edge occupancy rows: 55
Node occupancy rows: 75
Detected conflicts: 3
Unscheduled rows: 2
```

因此，P3 在报告中的逻辑可以写成：

```text
P2 任务分配与排序结果
        ↓
结合 P1 的路径时间和边/节点占用偏移
        ↓
生成 AMR 时间表 schedule_result.csv
        ↓
展开通道和节点占用明细
        ↓
检测通道冲突和路口冲突 conflict_log.csv
```

一句话总结：

> P3 的作用是把“任务顺序”变成“可检查冲突的时间表”，是任务分配结果走向多 AMR 协同调度结果的关键一步。

## P4：动态扰动与滚动时域重排

P4 回答三个问题：

1. 动态事件发生后，哪些任务和 AMR 会受影响？
2. 原计划是否需要改变？
3. 改变之后新增了多少延期和扰动？

当前脚本先搭好输入输出接口，具体滚动时域优化算法暂时使用 `rolling_horizon_interface_stub` 占位。也就是说：脚本会识别受影响任务，并用简单规则生成重排结果，但暂时不会真正做全局优化、局部优化或多候选路径换路。

### 动态事件输入

P4 使用：

```text
data/raw/dynamic_events.csv
```

当前样例包含三类事件：

| event_id | event_type     | 含义                      |
| -------- | -------------- | ------------------------- |
| E01      | `edge_block` | 某条通道在一段时间内封锁  |
| E02      | `amr_delay`  | 某台 AMR 在一段时间内延误 |
| E03      | `new_task`   | 运行过程中临时新增任务    |

`dynamic_events.csv` 的字段如下：

| 字段                           | 含义                                                     |
| ------------------------------ | -------------------------------------------------------- |
| `event_id`                   | 动态事件编号                                             |
| `event_type`                 | 事件类型，如 `edge_block`、`amr_delay`、`new_task` |
| `target_type`                | 目标类型，如 `edge`、`amr`、`task`                 |
| `target_id`                  | 目标编号，如 `e10`、`AMR2`、`T13`                  |
| `start_time`, `end_time`   | 事件生效时间窗                                           |
| `release_time`               | 新任务释放时间                                           |
| `pickup`, `delivery`       | 新任务的取货点和送货点                                   |
| `service_time`, `priority` | 新任务服务时间和优先级                                   |
| `description`                | 事件说明                                                 |

### 运行方式

运行前需要先运行 P1、P2、P3：

```powershell
python .\src\p1_candidate_paths\generate_candidate_paths.py
python .\src\p2_assignment\assign_and_sequence_tasks.py
python .\src\p3_schedule_conflicts\schedule_and_detect_conflicts.py
```

然后运行 P4：

```powershell
python .\src\p4_dynamic_reschedule\dynamic_reschedule.py
```

P4 输入：

```text
data/processed/schedule_result.csv
data/processed/edge_occupancy_schedule.csv
data/raw/dynamic_events.csv
```

P4 输出：

```text
data/processed/reschedule_result.csv
data/processed/dynamic_event_impact.csv
data/processed/reschedule_summary.csv
```

### reschedule_result.csv

这是 P4 给 P5 的主接口。分工文档中的核心字段是：

```text
task_id, old_amr, new_amr, old_start, new_start, changed
```

当前框架额外保留了对分析有用的字段：

| 字段                   | 含义                 | 给谁用 |
| ---------------------- | -------------------- | ------ |
| `task_id`            | 任务编号             | P5     |
| `old_amr`            | 原计划 AMR           | P5     |
| `new_amr`            | 重排后 AMR           | P5     |
| `old_start`          | 原开始时间           | P5     |
| `new_start`          | 重排后开始时间       | P5     |
| `old_finish`         | 原完成时间           | P5     |
| `new_finish`         | 重排后完成时间       | P5     |
| `changed`            | 是否被明显扰动       | P5     |
| `affected_event_ids` | 影响该任务的动态事件 | P4/P5  |
| `change_reason`      | 改变原因             | P4/P5  |
| `delay_added`        | 新增时间推迟量       | P5     |
| `reschedule_status`  | 重排状态             | P4/P5  |

当前占位逻辑：

- `edge_block`：如果某任务的通道占用与封锁时间窗重叠，则将该 AMR 当前及后续任务整体后移；
- `amr_delay`：如果某 AMR 在事件时间窗内延误，则将该 AMR 当前及后续任务整体后移；
- `new_task`：把临时任务插入到当前最早空闲的 AMR 后面；
- `changed = 1` 表示任务被动态事件影响或新插入。

### dynamic_event_impact.csv

这是动态事件影响识别表：

| 字段                         | 含义           |
| ---------------------------- | -------------- |
| `event_id`                 | 动态事件编号   |
| `event_type`               | 动态事件类型   |
| `target_id`                | 事件作用对象   |
| `start_time`, `end_time` | 事件时间窗     |
| `affected_tasks`           | 受影响任务     |
| `affected_amrs`            | 受影响 AMR     |
| `impact_count`             | 受影响任务数量 |
| `impact_reason`            | 影响原因       |

这张表用于回答：

> 哪些动态事件真正影响了当前计划？

### reschedule_summary.csv

这是 P4 指标摘要：

| 指标                       | 含义                           |
| -------------------------- | ------------------------------ |
| `OriginalScheduledTasks` | 原计划中成功生成时间表的任务数 |
| `DynamicEvents`          | 动态事件数量                   |
| `AffectedEvents`         | 实际影响任务的事件数量         |
| `ChangedTasks`           | 被改变或插入的任务数量         |
| `NewTasks`               | 新增任务数量                   |
| `OriginalCmax`           | 原计划最大完成时间             |
| `NewCmax`                | 重排后最大完成时间             |
| `TotalAddedDelay`        | 动态事件带来的总推迟量         |

### 报告中如何展示 P4 成果

P4 建议放在报告的“通道封锁与动态重排”实验部分。不要只展示 `reschedule_result.csv`，而要展示“扰动发生了什么、影响了谁、重排后变化多少”。

#### 1. 动态事件表

使用 `dynamic_events.csv` 展示实验设置：

| 事件 | 类型       | 对象 | 开始 | 结束 | 说明              |
| ---- | ---------- | ---- | ---: | ---: | ----------------- |
| E01  | edge_block | e10  |   18 |   24 | 通道 e10 临时封锁 |
| E02  | amr_delay  | AMR2 |   24 |   30 | AMR2 发生延误     |
| E03  | new_task   | T13  |   26 |    - | 新增紧急任务      |

这张表回答：

> 动态仓储环境中发生了什么扰动？

#### 2. 动态事件影响识别表

使用 `dynamic_event_impact.csv` 展示每个事件影响了哪些任务：

| 事件 | 类型       | 影响任务 | 影响 AMR | 影响数量 | 原因                      |
| ---- | ---------- | -------- | -------- | -------: | ------------------------- |
| E01  | edge_block | T06      | AMR1     |        1 | 通道 e10 与原占用时间重叠 |

这张表回答：

> 哪些任务必须被等待、换路或重排？

#### 3. 重排前后对比表

使用 `reschedule_result.csv` 展示核心结果：

| 任务 | 原 AMR | 新 AMR | 原开始 | 新开始 | 原完成 | 新完成 |             是否改变 | 原因           |
| ---- | ------ | ------ | -----: | -----: | -----: | -----: | -------------------: | -------------- |
| T06  | AMR1   | AMR1   |     22 |     28 |     33 |     39 |                    1 | edge_block:e10 |
| T13  | -      | AMR4   |      - |     37 |     39 |      1 | new_task_insert_stub |                |

这张表回答：

> 动态事件发生后，计划发生了哪些变化？

#### 4. 重排指标摘要

使用 `reschedule_summary.csv` 展示整体影响：

| 指标            | 数值 | 解释                   |
| --------------- | ---: | ---------------------- |
| OriginalCmax    |   40 | 原计划最大完成时间     |
| NewCmax         |   45 | 重排后最大完成时间     |
| ChangedTasks    |    4 | 被扰动或新增的任务数量 |
| TotalAddedDelay |   12 | 总推迟时间             |

这张表回答：

> 动态扰动让系统表现变差了多少？

#### 5. 可视化建议

如果后续继续扩展，P4 最适合展示两类图：

1. 重排前后甘特图对比

   - 上图：原始 `schedule_result.csv`
   - 下图：`reschedule_result.csv`
   - 用红色标记被改变任务
2. 通道封锁影响图

   - 在仓库路网图上高亮被封锁通道
   - 标出受影响 AMR 和任务
   - 标注封锁时间窗

当前框架暂未自动生成这两类图，但 `schedule_result.csv`、`reschedule_result.csv` 和 `dynamic_event_impact.csv` 已经包含绘图所需字段。

P4 在报告中的逻辑可以写成：

```text
原始调度结果 schedule_result.csv
        ↓
动态事件 dynamic_events.csv
        ↓
识别受影响任务 dynamic_event_impact.csv
        ↓
占位重排 reschedule_result.csv
        ↓
统计扰动影响 reschedule_summary.csv
```

一句话总结：

> P4 的作用是验证系统面对通道封锁、AMR 延误和临时任务时，能否识别受影响计划，并给出可解释的局部重排结果。

## 推荐运行顺序

```powershell
cd E:\learning\大二春夏\运筹学\大作业\amr_warehouse_framework
python .\src\p0_data_scene\plot_warehouse_network.py
python .\src\p1_candidate_paths\generate_candidate_paths.py
python .\src\p1_candidate_paths\plot_candidate_paths.py
python .\src\p2_assignment\assign_and_sequence_tasks.py
python .\src\p3_schedule_conflicts\schedule_and_detect_conflicts.py
python .\src\p4_dynamic_reschedule\dynamic_reschedule.py
```

运行完后，报告里可以放：

1. `outputs/warehouse_network.png`
2. 一张典型候选路径图
3. `outputs/cost_matrix_total.png`
4. `data/processed/path_cost.csv` 的前几行作为路径成本表
5. `data/processed/assignment_result.csv` 的前几行作为任务分配与排序结果表
6. `data/processed/schedule_result.csv` 的前几行作为时间调度结果表
7. `data/processed/conflict_log.csv` 作为冲突检测结果表
8. `data/processed/reschedule_result.csv` 作为动态重排结果表
9. `data/processed/dynamic_event_impact.csv` 作为动态事件影响识别表

## 后续接 P2/P3 的方式

P2 任务分配模块不需要重新算路径，只需要读取：

```text
data/processed/path_cost.csv
data/processed/path_cost_matrix_time.csv
data/processed/path_cost_matrix_total.csv
```

例如：

- AMR 初始点到任务取货点的成本，用于判断哪台 AMR 更适合接任务
- 任务取货点到送货点的成本，用于计算任务执行时间
- 一个任务送货点到下一个任务取货点的成本，用于计算任务衔接时间

P3 时间调度模块可以读取：

```text
data/processed/assignment_result.csv
data/processed/path_cost.csv
data/processed/path_edge_occupancy.csv
data/processed/path_node_occupancy.csv
```

这样 P3 可以从 `assignment_result.csv` 得到任务顺序，从 `path_edge_occupancy.csv` 和 `path_node_occupancy.csv` 推算通道和路口占用。
