# P1 候选路径生成与路径成本矩阵

负责为关键节点之间生成候选路径，并提供后续调度模块使用的成本、时间和占用接口。

## 输入

```text
data/raw/nodes.csv
data/raw/edges.csv
data/raw/amrs.csv
data/raw/tasks.csv
```

## 脚本

```powershell
python .\src\p1_candidate_paths\generate_candidate_paths.py
python .\src\p1_candidate_paths\plot_candidate_paths.py
```

## 输出

```text
data/processed/key_nodes.csv
data/processed/path_cost.csv
data/processed/path_edge_occupancy.csv
data/processed/path_node_occupancy.csv
data/processed/path_cost_matrix_time.csv
data/processed/path_cost_matrix_total.csv
outputs/paths/candidate_paths_*.png
outputs/cost_matrix_time.png
outputs/cost_matrix_total.png
```

## 报告用途

用于 6.2 路径模块，展示候选路径、路径成本矩阵、通道占用偏移和节点到达偏移。
