# P1 二维候选路径生成

P1 是项目的路径基础层。它在二维仓库平面上为关键节点对生成候选路径、路径成本、栅格或可视图几何、轨迹采样和成本矩阵。P2 使用 P1 的成本表完成任务分配、排序和路径选择；P3/P4 使用 P1 的轨迹采样表展开二维时空轨迹并检测冲突。

## 本部分在做什么

P1 的目标不是直接完成多 AMR 调度，而是把“仓库二维空间中的可走路径”转化为下游模型可读取的结构化接口。

P1 对关键节点集合中的有序 OD 对生成路径。关键节点包括入库点、出库点、货架点、分拣点、充电点和 AMR 初始点。当前算例中，单一算法会为 17 个关键节点生成 `17 x 16 = 272` 条有向路径。

## 采用的求解算法

P1 实现了四类路径生成算法：

| 参数 | 算法 | 核心思想 | 作用 |
| --- | --- | --- | --- |
| `basic_astar` | Grid A* | 将仓库离散为二维栅格，在 8 邻域上用 A* 搜索最短路 | 栅格可达性基线，稳定但转角较多 |
| `vg` | Visibility Graph | 使用障碍物膨胀边界角点构造可视图，搜索连续空间最短折线 | 几何路径更短，折点更少 |
| `avg` | Augmented Visibility Graph | 在可视图上把上一顶点纳入状态，对转角大小加入惩罚 | 在距离略增的情况下偏好更平滑路线 |
| `davg` | Dynamic AVG | 在 AVG 基础上加入动态风险区域暴露代价和活跃可视图 | 用于动态风险或封锁区域规避 |

各算法的建模含义：

- `basic_astar` 使用欧氏距离启发函数，障碍物按 AMR footprint 进行安全膨胀，并禁止贴角穿越。
- `vg` 将连续平面绕障碍问题转化为可见角点之间的最短路问题。
- `avg` 的搜索状态从“当前顶点”扩展为“上一顶点 + 当前顶点”，从而在扩展下一条边时计算转角代价。
- `davg` 在路径靠近动态风险区域时增加暴露代价，若当前 OD 远离风险区，则会退化为 AVG。

## 运行

在项目根目录分别生成四类基础路径源：

```powershell
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm basic_astar
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm vg
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm avg
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm davg
```

调试单条 OD 路径：

```powershell
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm davg --from-node IN1 --to-node P5
```

注意：单条 OD 模式会覆盖对应算法目录下的 P1 输出，只适合调试。完整流程应不带 `--from-node` 和 `--to-node`。

查看已生成路径接口：

```powershell
python .\src\p1_candidate_paths\path_interface.py --algorithm basic_astar --from-node IN1 --to-node P5
```

## 输出

每种算法输出到：

```text
data/processed/p1/<algorithm>/
```

主要文件：

```text
key_nodes.csv
path_cost.csv
path_grid_cells.csv
path_trajectory_samples.csv
path_cost_matrix_time.csv
path_cost_matrix_total.csv
```

关键表说明：

| 文件 | 面向模块 | 说明 |
| --- | --- | --- |
| `path_cost.csv` | P2 | 每条路径的 `from_node`、`to_node`、`path_uid`、距离、时间、转角代价、动态代价、总成本和规划状态 |
| `path_trajectory_samples.csv` | P3/P4 | 以 `offset_time,x,y,theta,speed,footprint_radius` 表示相对出发时刻的二维轨迹样本 |
| `path_cost_matrix_time.csv` | P2 | OD 对之间的最短通行时间矩阵 |
| `path_cost_matrix_total.csv` | P2 | OD 对之间的最小综合成本矩阵 |

下游读取示例：

```python
from src.p1_candidate_paths.path_interface import load_p1_outputs, get_best_path, get_pair_trajectory

outputs = load_p1_outputs(algorithm="davg")
path = get_best_path(outputs, "IN1", "P5")
trajectory = get_pair_trajectory(outputs, "IN1", "P5", start_time=20.0)
```

## 达到的效果

报告中的 P1 对比实验显示，四种单算法在当前算例中都生成了 272 条路径，主要差异体现在距离和转角数：

| 算法 | 平均距离 | 平均转角数 | 平均动态代价 | 平均总成本 |
| --- | ---: | ---: | ---: | ---: |
| Grid A* | 6.463 | 9.206 | 0.000 | 6.463 |
| VG | 6.054 | 1.846 | 0.000 | 6.054 |
| AVG | 6.071 | 1.765 | 0.000 | 6.695 |
| DAVG | 6.071 | 1.765 | 0.000 | 6.695 |

结论是：Grid A* 稳定可达但路径更折线化；VG 能得到更短的连续空间折线；AVG/DAVG 用少量距离代价换取更平滑或更安全的路径偏好。当前静态算例中 DAVG 与 AVG 接近，是因为动态风险代价没有被触发。

代表性 OD 对也体现了该差异：`IN1 -> P5` 上，Grid A* 距离为 6.843、转角数为 5；VG/AVG/DAVG 的几何距离为 6.492、转角数为 2。`P8 -> OUT2` 上，连续可视图算法几乎走直线，而 Grid A* 受栅格方向限制产生更多转角。

## mixed 候选库边界

P1 本身分别生成四类单算法路径源。多算法融合得到的 `mixed` 多候选路径库由 P2 的 `build_mixed_paths.py` 完成：

```powershell
python .\src\p2_assignment\build_mixed_paths.py
```

融合后输出到：

```text
data/processed/p1/mixed/
```

这样设计的原因是：P1 负责路径生成，P2 负责把多种路径源统一成本口径、几何去重并转化为调度层候选路径集合。

## 代码简要解读

主要文件结构：

```text
generate_candidate_paths.py       # P1 主入口
path_interface.py                 # 下游读取 P1 输出的统一接口
plot_candidate_paths.py           # 路径结果可视化
run_p1_experiments.py             # P1 算法对比实验
animate_davg_replanning.py        # DAVG 动态路径示例动画
planners/
  common.py                       # 数据读取、关键节点构建、输出保存、公用几何工具
  basic_astar.py                  # 栅格 A*
  vg.py                           # 可视图最短路
  avg.py                          # 带转角代价的可视图
  davg.py                         # 带动态风险代价的可视图
```

核心流程：

1. `generate_candidate_paths.py` 解析 `--algorithm`，选择对应 `planners/*.py` 中的 `generate_outputs()`。
2. `common.load_raw_data()` 读取原始节点、任务、AMR 和障碍物。
3. `common.build_key_nodes()` 构造路径端点集合。
4. 对每个 OD 对调用对应规划器生成路径。
5. `common.save_outputs()` 保存成本表、轨迹样本和成本矩阵。
6. `path_interface.py` 提供 `load_p1_outputs()`、`get_best_path()` 和 `get_trajectory_samples()`，供 P2-P4 复用。

## 与后续模块的关系

P1 输出 `path_cost.csv` 后，P2 可以把路径通行时间和综合成本纳入任务级优化；P1 输出 `path_trajectory_samples.csv` 后，P3/P4 可以将路径按任务开始时间平移成绝对时空轨迹。也就是说，P1 决定了下游调度的“路径可选空间”和“轨迹检查基础”。
