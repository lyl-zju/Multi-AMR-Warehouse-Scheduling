# P5 实验汇总、指标分析与管理解释

负责汇总 P1-P4 的输出，形成最终实验指标、对比图表和管理解释。

## 建议输入

```text
data/processed/p1/basic_astar/path_cost.csv
data/processed/p2/assignment_result.csv
data/processed/p3/schedule_result.csv
data/processed/p3/conflict_log.csv
data/processed/p4/reschedule_result.csv
data/processed/p4/reschedule_summary.csv
```

## 建议输出

```text
data/processed/p5/experiment_metrics.csv
outputs/p5/p5_*.png
```

## 后续可加入的程序

- 指标汇总脚本
- AMR 数量敏感性分析
- 任务密度敏感性分析
- 通道封锁实验对比
- 重排前后甘特图
- 瓶颈通道/影子价格分析

## 报告用途

用于汇总总完工时间、等待时间、冲突数、延期任务数、总延期、负载均衡、动态重排扰动等指标。
