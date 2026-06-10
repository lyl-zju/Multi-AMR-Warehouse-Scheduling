# P1 候选路径生成与 DAVG 接口说明

P1 负责在二维仓库平面上为关键节点生成路径，输出路径成本、路径几何、时间轨迹采样和成本矩阵。后续 P2/P3/P4 不需要直接理解路径规划算法细节，只需要读取 P1 的输出表，或者在动态事件发生时调用 P1 的 DAVG 重规划能力。

## 当前算法

```text
basic_astar  基础 8 邻域 Grid A*
vg           Visibility Graph，普通可见图最短路
avg          Augmented Visibility Graph，加入转角状态和转角代价
davg         Dynamic Augmented Visibility Graph，加入动态区域代价和活动区域筛选
```

运行完整 P1 输出：

```powershell
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm davg
```

只调试某一对起终点：

```powershell
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm davg --from-node IN1 --to-node P5
```

注意：单对模式会覆盖 `data/processed/p1/davg/` 下的输出。跑 P2/P3 前请重新运行不带起终点的完整生成命令。

## AVG 输出

AVG 的核心思想是：在普通 VG 的可见图上，把搜索状态从 `current_vertex` 扩展为 `(previous_vertex, current_vertex)`。这样每次从 `current` 走向 `next` 时，可以计算转角代价。

AVG 成本定义：

```text
travel_time = distance / DEFAULT_AMR_SPEED
turn_cost = TURN_COST_PER_RADIAN * sum(turn_angles)
dynamic_cost = 0
total_cost = distance + turn_cost
```

AVG 输出目录：

```text
data/processed/p1/avg/
```

主要文件：

```text
path_cost.csv
path_grid_cells.csv
path_trajectory_samples.csv
path_node_occupancy.csv
path_edge_occupancy.csv
path_cost_matrix_time.csv
path_cost_matrix_total.csv
```

`path_cost.csv` 一行是一条路径，关键字段如下：

| 字段 | 含义 |
| --- | --- |
| `path_uid` | 路径唯一编号，例如 `IN1__P5__path_1` |
| `from_node`, `to_node` | 起点和终点 |
| `algorithm` | `avg` |
| `path_geometry` | 路径折线点，格式为 `x,y;x,y;...` |
| `distance` | 路径几何长度 |
| `travel_time` | 通行时间 |
| `turn_count` | 转角数量 |
| `turn_cost` | 转角代价 |
| `dynamic_cost` | AVG 中为 `0` |
| `total_cost` | 综合成本，等于 `distance + turn_cost` |

`path_grid_cells.csv` 对 AVG 来说不是栅格，而是可见图 waypoint：

```text
path_uid, from_node, to_node, path_id, cell_index, row, col, x, y, is_dynamic
```

其中 `row` / `col` 为空，`x` / `y` 是 waypoint 坐标。

`path_trajectory_samples.csv` 是后续 P3 最应该使用的时间轨迹：

```text
path_uid, from_node, to_node, path_id, sample_index, offset_time, x, y, theta, speed, footprint_radius
```

其中 `offset_time` 是相对路径出发时刻的时间。如果某段路径在调度中从 `start_time = 20` 开始，则轨迹点的真实时间为：

```text
absolute_time = 20 + offset_time
```

## DAVG 输出

DAVG 在 AVG 基础上增加两个东西：

1. 活动区域筛选：建图时优先使用起终点走廊附近的障碍角点，并始终保留 `dynamic_block` 的角点；
2. 动态区域代价：路径段越靠近 `dynamic_block`，`dynamic_cost` 越高。

DAVG 成本定义：

```text
travel_time = distance / DEFAULT_AMR_SPEED
turn_cost = TURN_COST_PER_RADIAN * sum(turn_angles)
dynamic_cost = exposure cost around dynamic_block
total_cost = distance + turn_cost + dynamic_cost
```

DAVG 输出目录：

```text
data/processed/p1/davg/
```

DAVG 和 AVG 使用同一套输出文件和字段。区别主要体现在：

| 字段 | AVG | DAVG |
| --- | --- | --- |
| `algorithm` | `avg` | `davg` |
| `dynamic_cost` | 恒为 `0` | 靠近动态障碍时大于 `0` |
| `total_cost` | `distance + turn_cost` | `distance + turn_cost + dynamic_cost` |
| `path_grid_cells.is_dynamic` | 通常为 `0` | waypoint 靠近动态区域时为 `1` |

当前 DAVG 中的动态区域来自：

```text
data/raw/floor_obstacles.csv
```

其中 `obstacle_type = dynamic_block` 的矩形会被当作动态/风险区域。当前 `data/raw/dynamic_events.csv` 仍是旧路网格式，例如 `edge_block e10`，不包含二维矩形几何，因此 P1 的 DAVG 暂时不直接读取它。

## P1 读取接口

下游模块可以直接读 CSV，也可以使用轻量接口：

```python
from src.p1_candidate_paths.path_interface import (
    load_p1_outputs,
    get_best_path,
    get_pair_trajectory,
)

outputs = load_p1_outputs(algorithm="davg")
path = get_best_path(outputs, "IN1", "P5")
trajectory = get_pair_trajectory(outputs, "IN1", "P5", start_time=20.0)
```

`get_pair_trajectory(..., start_time=20.0)` 会返回带 `absolute_time` 的轨迹采样表。

命令行检查：

```powershell
python -B .\src\p1_candidate_paths\path_interface.py --algorithm davg --from-node IN1 --to-node P5
```

## 给 P2 的适配建议

P2 任务分配只需要路径成本和时间，直接读取 DAVG 输出即可。

推荐使用：

```text
data/processed/p1/davg/path_cost.csv
data/processed/p1/davg/path_cost_matrix_total.csv
data/processed/p1/davg/path_cost_matrix_time.csv
```

字段建议：

| P2 用途 | 推荐字段 |
| --- | --- |
| 任务分配目标函数 | `total_cost` 或 `path_cost_matrix_total.csv` |
| 估计通行时间 | `travel_time` 或 `path_cost_matrix_time.csv` |
| 路径引用 | `path_uid` |
| 距离分析 | `distance` |

注意：DAVG 的 `total_cost` 已经包含动态区域代价。如果 P2 想体现“避开动态风险区域”，应优先用 `total_cost`；如果只想估计实际运动时间，应使用 `travel_time`。

当前 P2 已支持：

```powershell
python .\src\p2_assignment\assign_and_sequence_tasks.py --p1-algorithm davg
```

## 给 P3 的适配建议

P3 如果要做二维平面调度和冲突检测，不建议继续依赖 `path_edge_occupancy.csv`。

原因：

```text
path_edge_occupancy.csv  在二维规划中是空占位表
path_node_occupancy.csv  当前只记录起点和终点到达时刻
```

P3 应该改为使用：

```text
data/processed/p1/davg/path_trajectory_samples.csv
```

基本做法：

1. 从 P2 的任务结果中拿到 `transition_path_uid` 和 `loaded_path_uid`；
2. 在 `path_trajectory_samples.csv` 中按 `path_uid` 查轨迹；
3. 用调度出发时刻加上 `offset_time` 得到真实时间：

```text
absolute_time = path_start_time + offset_time
```

4. 用 `(absolute_time, x, y, footprint_radius)` 做二维时空占用；
5. 两台 AMR 在同一时间附近的圆形 footprint 重叠，则判为冲突。

建议 P3 使用的轨迹字段：

| 字段 | 用途 |
| --- | --- |
| `path_uid` | 对应具体路径 |
| `sample_index` | 轨迹采样顺序 |
| `offset_time` | 相对路径出发时刻 |
| `x`, `y` | AMR 中心位置 |
| `theta` | 朝向 |
| `speed` | 速度 |
| `footprint_radius` | AMR 占用半径 |

一个简单冲突判断可以是：

```text
abs(t_i - t_j) <= time_tolerance
and distance((x_i, y_i), (x_j, y_j)) < radius_i + radius_j + safety_margin
```

如果 P3 需要更精细，可以在两个采样点之间线性插值。

## 给 P4 的适配建议

P4 的目标是动态事件发生后，对受影响路径做局部重规划。当前 P1 已经有 DAVG 重规划能力，但还没有给 P4 封装成正式函数；后续建议 P4 这样接：

1. 动态事件发生；
2. 将事件转换为二维动态障碍物，例如一行 `dynamic_block`：

```text
obstacle_id,x,y,width,height,obstacle_type,label
LIVE_BLOCK_01,6.5,3.0,1.0,0.6,dynamic_block,Live Block
```

3. 判断哪些 AMR 的未来轨迹会靠近或穿过该障碍；
4. 对受影响 AMR，从当前位置 `current_position` 到下一个目标点调用 DAVG；
5. 用新路径的 `path_geometry`、`path_trajectory_samples`、`travel_time`、`total_cost` 更新后续调度。

当前可以参考的演示脚本：

```powershell
python .\src\p1_candidate_paths\animate_davg_replanning.py
```

输出：

```text
outputs/p1/davg/replanning/davg_replanning_IN1_OUT2.gif
outputs/p1/davg/replanning/davg_replanning_IN1_OUT2.events.csv
```

这个脚本展示了：

```text
AMR 沿 DAVG 路径运动
随机动态障碍物出现
DAVG 重新规划当前点到目标点的路径
动态障碍物过期后再次重规划
```

P4 后续如果要正式接入，建议新增一个稳定包装函数，例如：

```python
replan_davg_path(
    start_point=(x, y),
    goal_point=(x, y),
    base_obstacles=obstacles,
    dynamic_obstacles=new_blocks,
)
```

返回值建议包含：

```text
path_geometry
trajectory_samples
distance
travel_time
turn_cost
dynamic_cost
total_cost
planning_status
```

## 当前接口边界

已经满足：

```text
P1 直接生成 DAVG 候选路径、路径成本、轨迹采样
P2 可以直接读取 DAVG 成本表和成本矩阵
P3 可以使用 P1 的轨迹采样做二维冲突检测
P4 可以参考 DAVG planner 和动画脚本实现动态重规划
```

还需要后续模块适配：

```text
P3 当前代码仍主要使用旧 edge/node occupancy，尚未真正使用 trajectory samples
P4 当前 dynamic_events.csv 仍是旧 edge_id 格式，尚未转换为二维 dynamic_block
P4 还需要一个正式的 DAVG replan wrapper，而不是只使用演示脚本
```

