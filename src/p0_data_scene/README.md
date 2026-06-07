# P0 仓库场景与基础数据

负责构造和展示基础算例场景。

## 输入

```text
data/raw/nodes.csv
data/raw/edges.csv
data/raw/amrs.csv
data/raw/tasks.csv
```

## 脚本

```powershell
python .\src\p0_data_scene\plot_warehouse_network.py
```

## 输出

```text
outputs/warehouse_network.png
```

## 报告用途

用于 6.1 算例场景，展示仓库路网、AMR 初始位置、任务取货点和送货点。
