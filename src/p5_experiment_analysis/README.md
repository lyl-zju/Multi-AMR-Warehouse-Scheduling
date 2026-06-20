# P5 灵敏度分析与管理解释

P5 沿用 P1-P4 的调度链路和评价口径，对多 AMR 仓库调度系统做资源敏感性分析。它不再只评价某一个调度方案，而是回答运营层面的三个问题：需要多少 AMR 才能稳定服务，当前任务波次容量边界在哪里，局部通道瓶颈和全局资源配置哪个更关键。

## 本部分在做什么

P5 围绕三个可解释变量展开：

| 变量 | 管理含义 |
| --- | --- |
| AMR 数量 `m` | 车队运输资源是否充足 |
| 任务负荷 `n` | 一个业务时域内能稳定处理多少任务 |
| 瓶颈簇容量 `q_C` | 局部通道通行能力是否限制系统表现 |

分析流程分为五个 stage：

| 阶段 | 脚本 | 作用 |
| --- | --- | --- |
| Stage1 | `stage1_baseline_analysis.py` | 校验 P2-P4 基线结果，绘制轨迹占用热图 |
| Stage2 | `stage2_amr_marginal_analysis.py` | 枚举 AMR 数量，分析资源边际收益和服务阈值 |
| Stage3 | `stage3_task_capacity_analysis.py` | 构造嵌套任务池，扫描任务负荷容量边界 |
| Stage4 | `stage4_bottleneck_cluster_analysis.py` | 识别瓶颈空间簇，测试容量放松后的影子价值 |
| Stage5 | `stage5_morris_sobol_analysis.py` | 使用 Morris 和 Sobol 做全局灵敏度筛选 |

## 输入

P5 会读取前序模块结果和原始数据：

```text
data/raw/
data/processed/p2/
data/processed/p3/
data/processed/p4/
data/processed/p1/mixed/
```

其中 Stage1 直接校验当前 P2/P3/P4 主结果；Stage2-Stage5 会在不同参数组合下调用 P2/P3 等求解流程重新生成局部实验结果。

## 采用的方法

### Stage1 基线校验与热点识别

Stage1 固定当前仓库布局、12 个静态任务、4 台 AMR 和动态事件，读取 P2 静态排程、P3 冲突修复、P4 动态重排结果。它主要做两件事：

1. 检查任务覆盖、方法标记、路径状态、硬约束、时间单调性等接口一致性。
2. 将 P3/P4 轨迹投影到 1 米网格上，统计轨迹占用热点。

基线关键结果：

| 模块 | 任务 / AMR | `Cmax` |
| --- | --- | ---: |
| 静态排程 | 12 / 4 | 40.840111 |
| 冲突修复 | 12 / 4 | 46.596970 |
| 动态重排 | 13 / 4 | 61.918082 |

### Stage2 AMR 数量边际分析

Stage2 固定仓库布局、任务集合、时间窗、电量规则和 mixed 路径库，只改变 AMR 数量 `m`。对每个 `m` 重新运行调度并计算：

```text
Cmax
total_delay
late_count
path_missing_count
energy_violation_count
resource_utilization
```

使用前向有限差分衡量增加一台 AMR 的边际收益：

```text
Delta Cmax(m) = Cmax(m) - Cmax(m + 1)
```

报告结论：2 台和 3 台 AMR 均存在服务风险，4 台 AMR 时总延期、延期任务数、路径缺失和电量违规首次同时降为 0，因此 4 台 AMR 是当前算例的服务阈值；5 台及以上主要提供裕度，而不是改变可行性状态。

### Stage3 任务容量边界

Stage3 固定 4 台 AMR 和 mixed 路径库，构造嵌套任务池并逐点重优化。任务规模 `n` 增大时，只是在同一个任务池前缀上增加新任务，而不是换一批样本，因此不同 `n` 之间具有可比性。

容量判定采用保守 all-seeds 规则：

```text
所有随机种子下均满足 Cmax <= H 且无硬约束违规，才认为该 n 稳定可服务。
```

当前业务时域 `H = 55s`。报告结论：固定 4 台 AMR 时，`n = 14` 仍能在所有种子下满足业务时域；`n = 15` 开始越过容量边界。因此当前配置下的保守任务容量边界为 14 个任务。

### Stage4 瓶颈空间簇与容量影子价值

Stage4 先根据轨迹占用、等待、冲突和延期信息识别高压力网格，再将相邻热点网格合并成瓶颈空间簇。随后对候选瓶颈簇进行容量放松重优化，观察 `Cmax`、冲突等待和目标函数改善量。

报告结论：C03 是边界工况下的主导空间瓶颈。容量放松后：

```text
Cmax 减少 14.727837 秒
冲突等待减少 49.0 秒
目标函数下降 10364.363885
```

这说明 C03 所在的 RACK_E 与 Z_SORT 汇入区域是当前边界工况下最值得优先关注的局部通道。

### Stage5 Morris 与 Sobol 全局灵敏度

Stage5 将 `m`、`n`、`q_C` 放入同一全局灵敏度框架，响应变量为：

```text
Y = Cmax(m, n, q_C)
```

参数水平：

```text
m   in {3, 4, 5, 6}
n   in {12, 13, 14, 15}
q_C in {1, 2, 3}
```

Morris 用有限差分基本效应筛选主导因素；Sobol 用方差分解验证一阶效应和总效应。

报告结论：

| 方法 | 主导因素 | 说明 |
| --- | --- | --- |
| Morris | `m` 的 `mu* = 30.711` 最大 | AMR 数量是最直接资源杠杆，任务量是次级压力源 |
| Sobol | `m` 的 `ST = 0.880` 最大 | AMR 数量解释了最多 `Cmax` 波动，`q_C` 的全局解释力较弱 |

综合判断：优先评估 AMR 数量扩容和任务负荷控制，再对 C03 做局部容量放松；局部通道改造不能替代全局资源配置。

## 运行

按阶段运行：

```powershell
python .\src\p5_experiment_analysis\stage1_baseline_analysis.py
python .\src\p5_experiment_analysis\stage2_amr_marginal_analysis.py
python .\src\p5_experiment_analysis\stage3_task_capacity_analysis.py
python .\src\p5_experiment_analysis\stage4_bottleneck_cluster_analysis.py
python .\src\p5_experiment_analysis\stage5_morris_sobol_analysis.py
```

Stage5 采样量较大，会多次调用真实调度链路，耗时明显长于前四个阶段。

部分脚本支持参数，例如 Stage1：

```powershell
python .\src\p5_experiment_analysis\stage1_baseline_analysis.py --write-report
```

具体参数可用：

```powershell
python .\src\p5_experiment_analysis\<script_name>.py --help
```

## 输出

处理后数据：

```text
data/processed/p5/stage1_baseline/
data/processed/p5/stage2_amr_marginal/
data/processed/p5/stage3_task_capacity/
data/processed/p5/stage4_bottleneck_cluster/
data/processed/p5/stage5_morris_sobol/
```

可视化输出：

```text
outputs/p5/stage1_baseline_dashboard.png
outputs/p5/stage2_amr_marginal.png
outputs/p5/stage3_capacity_boundary.png
outputs/p5/stage4_bottleneck_cluster.png
outputs/p5/stage5_morris.png
outputs/p5/stage5_sobol.png
```

报告图同步写入：

```text
report/figures/
```

`data/processed/p5/README.md` 记录了 P5 处理后数据的分阶段组织方式。

## 达到的效果

P5 将前序调度链路转换为运营判断：

| 问题 | 结论 |
| --- | --- |
| 当前系统是否可行 | 基线 P2-P4 均通过接口与硬约束校验 |
| AMR 数量是否足够 | 4 台 AMR 是当前算例服务阈值，5 台及以上主要增加裕度 |
| 当前任务容量边界 | 固定 4 台 AMR 时，保守容量边界为 14 个任务 |
| 哪个局部通道最关键 | C03 是边界工况下主导空间瓶颈 |
| 全局主导因素是什么 | AMR 数量主导 `Cmax` 波动，其次为任务负荷，瓶颈簇容量偏局部修复 |

## 代码简要解读

主要文件：

```text
p5_utils.py
stage1_baseline_analysis.py
stage2_amr_marginal_analysis.py
stage3_task_capacity_analysis.py
stage4_bottleneck_cluster_analysis.py
stage5_morris_sobol_analysis.py
```

公共工具 `p5_utils.py` 提供：

| 函数 | 作用 |
| --- | --- |
| `read_csv_rows()` / `write_csv_rows()` | 统一 CSV 读写 |
| `group_rows()` / `sort_rows()` | 字典行分组和排序 |
| `build_occupancy_histogram()` | 将轨迹点投影到网格热图 |
| `draw_floor_context()` | 绘制仓库底图 |
| `add_top_hotspot_labels()` | 在热图上标注高占用热点 |

各 stage 入口：

| 脚本 | 关键逻辑 |
| --- | --- |
| `stage1_baseline_analysis.py` | `validate_p2()`、`validate_p3()`、`validate_p4()` 校验前序结果，`plot_dashboard()` 绘制基线看板 |
| `stage2_amr_marginal_analysis.py` | 改写 AMR 数量，批量运行 P2，计算资源前沿、边际收益和车辆负载 |
| `stage3_task_capacity_analysis.py` | 生成嵌套任务池，按 `seed` 和 `n` 扫描容量边界 |
| `stage4_bottleneck_cluster_analysis.py` | 识别热点网格、连通簇和容量放松收益 |
| `stage5_morris_sobol_analysis.py` | 构造 Morris/Sobol 样本，运行缺失工况，输出灵敏度指标和图表 |

## 与前序模块的关系

P5 不是独立于调度链路的统计分析。它复用 P1 的 mixed 路径库、P2 的任务级调度器、P3 的冲突修复逻辑和 P4 的动态重排结果，将这些模块的输出转化为资源阈值、容量边界、瓶颈通道和全局灵敏度排序。换句话说，P1-P4 回答“当前怎么调度”，P5 回答“资源和约束怎么影响调度表现”。
