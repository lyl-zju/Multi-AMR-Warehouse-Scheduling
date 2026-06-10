# P0 仓库二维场景与基础数据

P0 负责构造和展示基础算例场景。本分支采用“二维仓库平面图 + 障碍物”的建模方向：仓库不再只显示为抽象通道边，而是在统一坐标系中展示墙体、货架、功能区域、动态障碍、AMR 初始位置和任务点。

## 输入

```text
data/raw/nodes.csv
data/raw/amrs.csv
data/raw/tasks.csv
data/raw/floor_zones.csv
data/raw/floor_obstacles.csv
```

其中：

- `nodes.csv`：保留任务点、充电点、取送货点等关键位置坐标；
- `floor_zones.csv`：定义入库区、分拣区、出库区、充电区等功能区域；
- `floor_obstacles.csv`：定义墙体、货架块、临时封锁区等矩形障碍物。

`edges.csv` 暂时保留在数据目录中，供原路网方案和后续接口对照使用。后续 P1 迁移到二维路径规划后，可以从平面图障碍物生成路径几何、轨迹采样和通行代价。

## 脚本

```powershell
python .\src\p0_data_scene\plot_warehouse_network.py
```

脚本名暂时保留 `plot_warehouse_network.py`，方便沿用原来的运行入口；实际输出已经改为二维仓库平面图。

## 输出

```text
outputs/p0/warehouse_floor_plan.png
```

## 报告用途

用于报告的算例场景部分，展示：

- 仓库二维边界和墙体；
- 货架、分拣区、出库区、入库区、充电区；
- 临时动态障碍区域；
- AMR 初始位置；
- 任务取货点和送货点。

这张图对应“二维平面图建模方案”的 P0 场景输入。后续 P1-P4 仍沿用原来的模块划分，但路径数据会逐步从 `edge_sequence` / `node_sequence` 扩展为 `path_geometry` / `trajectory_samples` / `occupied_area_by_time`。
