# P4 动态扰动与滚动时域重排

P4 读取 P3 时间表、二维轨迹和动态事件，识别受影响任务并生成重排结果接口。

## 输入

```text
data/processed/p3/schedule_result.csv
data/processed/p3/trajectory_schedule.csv
data/raw/dynamic_events.csv
```

## 运行

```powershell
python .\src\p4_dynamic_reschedule\dynamic_reschedule.py
```

## 输出

```text
data/processed/p4/reschedule_result.csv
data/processed/p4/dynamic_event_impact.csv
data/processed/p4/reschedule_summary.csv
```

## 动态事件

```text
area_block  带 x,y,width,height,start_time,end_time 的二维区域封锁
amr_delay   AMR 时间窗延误
new_task    运行中新增任务
```

当前实现是接口占位版：区域封锁会检查轨迹 footprint 是否进入矩形区域，AMR 延误会后移该车受影响任务，新增任务会插入当前最早空闲 AMR。
