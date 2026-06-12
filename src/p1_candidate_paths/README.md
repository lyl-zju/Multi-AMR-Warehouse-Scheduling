# P1 二维候选路径生成

P1 在二维仓库平面上为关键节点生成候选路径，输出路径成本、路径几何、轨迹采样和成本矩阵，供 P2-P4 直接读取。

## 算法

```text
basic_astar  基础 8 邻域 Grid A*
vg           Visibility Graph
avg          Augmented Visibility Graph，加入转角代价
davg         Dynamic Augmented Visibility Graph，加入动态区域代价
```

## 运行

```powershell
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm basic_astar
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm vg
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm avg
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm davg
```

调试单条路径：

```powershell
python .\src\p1_candidate_paths\generate_candidate_paths.py --algorithm davg --from-node IN1 --to-node P5
```

## 输出

```text
data/processed/p1/<algorithm>/key_nodes.csv
data/processed/p1/<algorithm>/path_cost.csv
data/processed/p1/<algorithm>/path_grid_cells.csv
data/processed/p1/<algorithm>/path_trajectory_samples.csv
data/processed/p1/<algorithm>/path_cost_matrix_time.csv
data/processed/p1/<algorithm>/path_cost_matrix_total.csv
```

`path_cost.csv` 面向 P2，主要使用 `travel_time` 和 `total_cost`。  
`path_trajectory_samples.csv` 面向 P3/P4，使用 `offset_time,x,y,theta,speed,footprint_radius` 表示相对出发时刻的二维轨迹样本。

## 读取接口

```python
from src.p1_candidate_paths.path_interface import load_p1_outputs, get_best_path, get_pair_trajectory

outputs = load_p1_outputs(algorithm="davg")
path = get_best_path(outputs, "IN1", "P5")
trajectory = get_pair_trajectory(outputs, "IN1", "P5", start_time=20.0)
```
