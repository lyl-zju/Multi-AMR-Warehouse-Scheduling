# P5 Processed Data Layout

P5 outputs are grouped by experiment stage:

- `stage1_baseline/`: baseline metrics, validation, hotspots, heatmap cells, and Gantt rows.
- `stage2_amr_marginal/`: AMR-count marginal analysis data and P2 rerun outputs.
- `stage3_task_capacity/`: task-load capacity sweep data, generated inputs, and P2 rerun outputs.
- `stage4_bottleneck_cluster/`: hotspot cluster summaries and capacity-relaxation tests.
- `stage5_morris_sobol/`: Morris/Sobol samples, cached true runs, sensitivity results, and Stage5 inputs/P2 outputs.

The P5 analysis scripts write directly to these stage folders.
