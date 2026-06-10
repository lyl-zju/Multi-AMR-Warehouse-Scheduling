# P4 动态扰动与滚动时域重排

负责处理动态事件，识别受影响任务，并生成重排结果接口。

## 输入

```text
data/processed/p3/schedule_result.csv
data/processed/p3/edge_occupancy_schedule.csv
data/raw/dynamic_events.csv
```

## 脚本

```powershell
python .\src\p4_dynamic_reschedule\dynamic_reschedule.py
```

## 输出

```text
data/processed/p4/reschedule_result.csv
data/processed/p4/dynamic_event_impact.csv
data/processed/p4/reschedule_summary.csv
```

## 报告用途

用于展示通道封锁、AMR 延误、临时新增任务对原调度的影响，以及重排前后变化。当前为滚动时域接口占位版。
