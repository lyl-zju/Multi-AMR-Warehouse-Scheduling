# P0 二维仓库场景

P0 负责绘制二维仓库平面图，展示功能区域、墙体、货架块、动态风险区域、AMR 初始位置和任务点。

## 输入

```text
data/raw/nodes.csv
data/raw/amrs.csv
data/raw/tasks.csv
data/raw/floor_zones.csv
data/raw/floor_obstacles.csv
```

## 运行

```powershell
python .\src\p0_data_scene\plot_floor_plan.py
```

## 输出

```text
outputs/p0/warehouse_floor_plan.png
```
