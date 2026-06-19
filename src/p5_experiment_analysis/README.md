# P5 敏感度分析

P5 沿用 P1-P4 的调度链路和评价口径，对多 AMR 仓库调度系统开展敏感度分析。当前实现包括基线校验、AMR 数量边际分析、任务容量边界、瓶颈簇容量测试，以及 Morris/Sobol 全局敏感度筛选。

## 脚本

```text
stage1_baseline_analysis.py
stage2_amr_marginal_analysis.py
stage3_task_capacity_analysis.py
stage4_bottleneck_cluster_analysis.py
stage5_morris_sobol_analysis.py
```

公共路径、CSV 写入、报告图目录等辅助逻辑位于 `p5_utils.py`。

## 输出目录

```text
data/processed/p5/stage1_baseline/
data/processed/p5/stage2_amr_marginal/
data/processed/p5/stage3_task_capacity/
data/processed/p5/stage4_bottleneck_cluster/
data/processed/p5/stage5_morris_sobol/
outputs/p5/
report/figures/
```

`data/processed/p5/README.md` 记录了处理后数据的分阶段组织方式。

## 依赖

Stage5 使用 SALib 生成 Morris 与 Sobol 样本并完成敏感度指标估计。运行前请确认已安装 `requirements.txt` 中的依赖。

## 报告用途

P5 报告章节位于 `report/p5/`，并通过 `report/main.tex` 作为总报告的一个章节编译。
