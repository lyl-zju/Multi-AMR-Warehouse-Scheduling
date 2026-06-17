# P4 动态扰动与滚动时域重排

P4 读取 P3 的冲突修复后时间表、二维轨迹和动态事件，识别受影响任务，并在滚动时域内重新安排未固定任务。当前实现不再是简单后移占位版，而是采用运筹学中的启发式重优化流程：受影响尾段识别、后悔值插入、局部搜索、候选路径重评估和冲突等待修复。

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

生成可视化分析图：

```powershell
python .\src\p4_dynamic_reschedule\plot_p4_results.py
python .\src\p4_dynamic_reschedule\plot_p4_results.py --fps 8 --speedup 2
```

## 输出

```text
data/processed/p4/reschedule_result.csv
data/processed/p4/dynamic_event_impact.csv
data/processed/p4/reschedule_summary.csv
data/processed/p4/trajectory_schedule.csv
data/processed/p4/p4_method_comparison.csv
data/processed/p4/p4_weight_sensitivity.csv
```

可视化输出：

```text
outputs/p4/p4_gantt_events.png
outputs/p4/p4_trajectory_map.png
outputs/p4/p4_event_impact_metrics.png
outputs/p4/p4_method_comparison.png
outputs/p4/p4_weight_sensitivity.png
outputs/p4/p4_dynamic_reschedule.gif
```

这些图分别用于说明：

```text
p4_gantt_events.png          对比 P3 基准计划和 P4 重排后计划，并叠加动态事件时间窗
p4_trajectory_map.png        在仓库平面图上展示 P4 最终轨迹、封锁区、新增任务和任务顺序
p4_event_impact_metrics.png  展示动态事件影响、可行性检查和目标函数指标
p4_method_comparison.png     展示默认滚动时域主结果；若单独运行慢基线实验，也可展示全局重排、仅等待和滚动时域的对比
p4_weight_sensitivity.png    记录目标口径表；默认快速刷新复用均衡权重可行解，完整敏感性实验需单独运行
p4_dynamic_reschedule.gif    沿用 P2/P3 的 footprint 动画风格，动态展示封锁、延误、新任务释放和最终重排轨迹
```

## 方法对比输出

`p4_method_comparison.csv` 记录当前刷新到的策略结果。默认刷新优先计算滚动时域主结果；全局重排和仅等待在最新 P3 输出下会显著拖慢计算，可作为单独实验运行：

```text
全局重排       对事件后的受影响任务池做后悔值插入和局部搜索，作为激进重优化基线
仅等待         保持原 AMR 分配和任务顺序，只通过等待和新任务末端插入修复扰动
滚动时域重排   对封锁/延误保持局部顺序并加等待，对新增任务做滚动窗口位置插入
```

表中 `冲突消解成功率` 先于延期和扰动任务数判定方案质量：若最终仍有轨迹冲突、封锁违规或 AMR 延误违规，则该方法在当前场景下记为 0。

基于最新 P3 输出重新计算后，默认滚动时域主结果为：

```text
滚动时域重排   冲突消解成功率 100%，总延期 41.79，Cmax 73.33，F2 3710.20
剩余轨迹冲突 0，封锁区违规 0，AMR 延误违规 0
```

这次新 P3 的关键变化是 AMR2 的 `T05` 与 `E02` 延误窗口相交。P4 因此不再把该任务视为不可改历史任务，而是释放 AMR2 的重叠任务及其后续尾段，从延误结束后重新安排，最终保证 `delayed_amr_violation_count = 0`。

`p4_weight_sensitivity.csv` 默认复用当前均衡权重可行解，仅用于保持输出结构完整：

```text
服务等级优先   reused_balanced_solution
均衡权重       selected_solution
稳定性优先     reused_balanced_solution
```

若要展示完整权重敏感性，应单独运行三种目标口径的慢实验；默认刷新以“新 P3 下快速生成可行滚动重排结果”为优先级。

## 动态事件

```text
area_block  带 x,y,width,height,start_time,end_time 的二维区域封锁
amr_delay   AMR 时间窗延误
new_task    运行中新增任务
```

## 当前算法

P4 以动态事件时间为滚动时域边界：

```text
1. 已经进入执行轨迹的任务固定，不再重排；
2. area_block 会检查 P3/P4 轨迹 footprint 是否进入封锁矩形；
3. amr_delay 会识别延误 AMR 在事件后的未来任务尾段；
4. new_task 会把新增任务插入未来计划；慢基线对比可作为单独实验运行；
5. 受影响任务及其同车后续尾段进入重排池；
6. 重排时只从 P1 mixed 候选路径中选择路径；
7. 目标按硬约束、延期服务质量、完工时间、路径成本、负载均衡、电量和稳定性字典序比较；
8. 最终轨迹重新检测 footprint / 节点容量冲突，必要时给未来任务增加等待。
```

新增任务事件没有显式 `latest_finish` 字段时，当前按 `release_time + 30` 作为动态任务软期限。

## 运筹学建模语言

P4 可以看作一个带扰动的动态多机器人取送货调度问题，即动态 PDPTW / 多车辆路径调度问题。P3 已经给出一组可执行的基准计划，P4 在事件时刻 `t_e` 到来后，不重新求解全部历史任务，而是在滚动时域内对尚未固定的任务集合做局部重优化。

### 完整数学抽象

P4 的滚动时域重排可以抽象为带候选路径选择、动态事件约束和时空冲突约束的动态 PDPTW。设事件时刻为 \(t_e\)，P3 已经给出基准可执行计划。P4 只重排尚未固定的任务。

集合定义：

```text
K                 AMR 集合
J                 全部任务集合，包含静态任务和动态新增任务
J_fix             在 t_e 前已经执行或进入执行轨迹的固定任务集合
J_free            t_e 后允许重排的任务集合，J_free = J \ J_fix
V                 仓库关键节点集合
Q_ab              P1 给出的从节点 a 到节点 b 的候选路径集合
B                 动态封锁区域集合
R                 AMR 延误窗口集合
Omega             由轨迹采样得到的潜在时空冲突对集合
```

任务参数：

```text
P_i               任务 i 的取货点
D_i               任务 i 的送货点
e_i               任务 i 的最早开始时间
l_i               任务 i 的期望最晚完成时间
s_i               任务 i 的取货服务时间
w_i               任务 i 的优先级权重
```

AMR 参数：

```text
a_k               滚动时域开始时 AMR k 的可用时间
n_k               滚动时域开始时 AMR k 所在节点
b_k               滚动时域开始时 AMR k 的剩余电量
v_k               AMR k 的速度系数
b_min             电量安全阈值
```

路径参数：

```text
tau_kq            AMR k 走候选路径 q 的实际通行时间
c_q               候选路径 q 的路径成本
g_kq              AMR k 走候选路径 q 的耗电量
X_q(r)            路径 q 在采样偏移 r 处的二维位置
rho               AMR footprint 半径
sigma             安全裕量
```

决策变量：

```text
x_ki in {0,1}     任务 i 是否分配给 AMR k
y_kij in {0,1}    在 AMR k 上任务 j 是否紧接任务 i 后执行
z_kijq in {0,1}   AMR k 从任务 i 的送货点到任务 j 的取货点是否选择路径 q
u_kiq in {0,1}    AMR k 执行任务 i 的 P_i -> D_i 是否选择路径 q
S_i >= 0          任务 i 的开始取货服务时间
C_i >= 0          任务 i 的完成送货时间
T_i >= 0          任务 i 的延期时间
L_i in {0,1}      任务 i 是否延期
Cmax >= 0         系统最大完工时间
H_i in {0,1}      任务 i 是否相对 P3 原计划换车
Delta_i >= 0      任务 i 相对 P3 原开始时间的偏移量
```

任务唯一分配约束：

```text
sum_{k in K} x_ki = 1,                         for all i in J_free
```

AMR 序列约束。令 \(0_k\) 表示 AMR k 在滚动时域开始时的虚拟起点，令 \(\bar{0}_k\) 表示虚拟终点，则每台 AMR 的未来任务必须形成一条从 \(0_k\) 到 \(\bar{0}_k\) 的线性序列：

```text
sum_{j in J_free} y_k,0_k,j <= 1,              for all k in K
sum_{i in J_free} y_k,i,bar0_k <= 1,           for all k in K
sum_{j in J_free union {bar0_k}} y_kij = x_ki, for all k in K, i in J_free
sum_{i in J_free union {0_k}} y_kij = x_kj,    for all k in K, j in J_free
```

可使用 MTZ 顺序变量 \(o_{ki}\) 消除子回路：

```text
o_kj >= o_ki + 1 - M(1 - y_kij),               for all k in K, i,j in J_free
```

路径选择约束。虚拟终点只表示任务序列结束，不需要实际移动路径；其他相邻任务弧必须选择 P1 候选路径：

```text
sum_{q in Q_{D_i,P_j}} z_kijq = y_kij,         for all k in K, i,j in J_free
sum_{q in Q_{n_k,P_j}} z_k,0_k,j,q = y_k,0_k,j,for all k in K, j in J_free
sum_{q in Q_{P_i,D_i}} u_kiq = x_ki,           for all k in K, i in J_free
```

这表示 P4 只能从 P1 `mixed` 候选路径库中选路径，不能临时创造新路径。

时间递推约束：

```text
S_i >= e_i,                                    for all i in J_free
C_i >= S_i + s_i + sum_q tau_kq u_kiq - M(1 - x_ki),
                                                  for all k in K, i in J_free
S_j >= C_i + sum_q tau_kq z_kijq - M(1 - y_kij),
                                                  for all k in K, i,j in J_free
S_j >= a_k + sum_q tau_kq z_k,0_k,j,q - M(1 - y_k,0_k,j),
                                                  for all k in K, j in J_free
Cmax >= C_i,                                  for all i in J_free
```

延期软约束：

```text
T_i >= C_i - l_i,                              for all i in J_free
T_i >= 0,                                      for all i in J_free
T_i <= M L_i,                                  for all i in J_free
```

电量约束。基础层不插入充电任务，因此每台 AMR 在滚动时域内的耗电不能使电量低于安全阈值：

```text
sum_{i,j,q} g_kq z_kijq
+ sum_{i,q} g_kq u_kiq
+ sum_i service_energy_i x_ki
<= b_k - b_min,                                for all k in K
```

动态封锁约束。若路径 \(q\) 的采样点 \(X_q(r)\) 与封锁区域 \(B_m\) 相交，封锁时间窗为 \([\alpha_m,\beta_m]\)，则该采样点的绝对通过时间必须避开封锁窗口：

```text
departure(q) + r <= alpha_m + M h
departure(q) + r >= beta_m  - M(1 - h)
h in {0,1}
```

AMR 延误窗口约束。若 AMR k 在 \([\alpha,\beta]\) 内不可用，则其任务执行区间不能与该窗口重叠：

```text
C_i <= alpha + M h_ki
S_i >= beta  - M(1 - h_ki)
h_ki in {0,1}
```

二维轨迹 footprint 冲突约束。对任意潜在冲突对 \(((k,q,r),(k',q',r')) in Omega\)，若

```text
||X_q(r) - X_q'(r')|| < 2 rho + sigma
```

则两者在时间上必须错开：

```text
departure(k,q)  + r  + epsilon <= departure(k',q') + r' + M h
departure(k',q') + r' + epsilon <= departure(k,q)  + r  + M(1 - h)
h in {0,1}
```

稳定性建模。设 P3 原计划中任务 i 的 AMR 为 \(k_i^0\)，开始时间为 \(S_i^0\)：

```text
H_i >= x_ki,                                   for all k != k_i^0
Delta_i >= S_i - S_i^0
Delta_i >= S_i^0 - S_i
```

目标函数采用字典序目标规划，而不是单一加权和：

```text
min lex(F0, F1a, F1b, F2, F3, F4)
```

其中：

```text
F0  = hard_violation_count
F1a = sum_i w_i L_i
F1b = sum_i L_i
F2  = 30 sum_i w_i T_i
    + 20 sum_i T_i
    + 5 Cmax
F3  = 2 sum_{k,i,j,q} c_q z_kijq
    + 10 (max_k load_k - min_k load_k)
    + sum energy
F4  = lambda_1 sum_i H_i
    + lambda_2 sum_i Delta_i
```

因此，数学意义上的 P4 是：在 P1 给定的候选路径集合上，对动态事件后的未固定任务集合 \(J_free\)，联合决定任务分配、任务顺序、候选路径选择和开始完成时间；在满足路径存在、电量、时间、封锁、延误和二维轨迹无冲突等硬约束的前提下，按字典序目标规划最小化延期、完工时间、路径成本、能耗、负载不均衡和对原计划的扰动。

### 与目标规划和 MILP 的关系

从数学建模角度看，P4 可以写成一个带动态事件约束的混合整数规划问题：任务分配、执行顺序、候选路径选择、等待时间和开始完成时间都可以作为决策变量，约束则包括单车序列可行性、时空衔接、封锁区避让、延误窗口和电量安全阈值等。

其中，目标层采用的是目标规划 / 字典序多目标优化思想：先保证可行性，再压低优先级任务延误、总延误、完工时间、空载成本和稳定性损失。

但需要说明的是，当前代码实现并没有调用通用 MILP 求解器去求解完整模型，而是采用“滚动时域 + 后悔值插入 + 局部搜索 + 冲突修复”的启发式算法。也就是说：

```text
数学表达上：可以建成 MILP + 目标规划
代码求解上：当前版本是启发式重优化
```

### 求解策略

当前实现采用启发式而非完整 MILP。原因是 P4 还要结合二维轨迹冲突检测，直接建立完整时空 MILP 会显著增加建模和求解复杂度。具体流程是：

```text
1. 事件影响识别：
   根据 area_block / amr_delay / new_task 找到受影响任务。

2. 滚动时域冻结：
   事件时刻前已经进入轨迹的任务固定，剩余任务才允许重排。

3. 重排池构造：
   对 area_block 和 amr_delay，取受影响任务及其同车后续尾段；
   对 new_task，在未来计划中评估新增任务的插入位置。

4. 后悔值插入：
   对待排任务枚举 AMR 和插入位置，比较最好与次好插入代价，
   优先安排后悔值大的任务。

5. 局部搜索：
   在插入解基础上尝试跨车移动、同车移动和任务交换，
   若字典序目标改善则接受。

6. 路径重评估：
   每次评价任务链时重新选择 transition / loaded 候选路径，
   并检查封锁时间窗下的路径可行性。

7. 冲突修复：
   展开最终二维轨迹，调用 P3 的 footprint / 节点容量冲突检测；
   若存在冲突，则只给未来任务增加等待，直到冲突数为 0 或达到迭代上限。
```

因此，P4 的运筹学定位不是重新做全局静态调度，而是一个带稳定性约束的动态滚动重排启发式：在保留既有执行计划的基础上，对扰动后的未来任务做局部组合优化。

## 验证指标

`reschedule_summary.csv` 输出基础层验收指标，包括：

```text
missing_loaded_path_count
missing_transition_count
unresolved_trajectory_conflict_count
blocked_area_violation_count
delayed_amr_violation_count
all_dynamic_tasks_scheduled
energy_violation_count
priority_late_count
late_count
priority_delay
total_delay
Cmax
empty_cost
load_balance_penalty
total_energy_used
F2
F3
```
