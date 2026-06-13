"""命题规定中固定的全局参数（见 docs/项目命题规定.md 第 5/6/10 节）。"""

# 5.2 载货速度系数：载货时有效速度为空驶速度的 90%
LOADED_SPEED_FACTOR = 0.90

# 6.2 耗电模型
EMPTY_ENERGY_RATE = 1.00
LOADED_ENERGY_RATE = 1.20
SERVICE_ENERGY_RATE = 0.20

# 6.1 电量规则
MAX_BATTERY = 100.0
BATTERY_SAFETY_THRESHOLD = 10.0

# 10 多目标优化准则：F2 时间效率层权重
F2_W_PRIORITY_DELAY = 30.0
F2_W_TOTAL_DELAY = 20.0
F2_W_CMAX = 5.0

# 10 多目标优化准则：F3 运行成本层权重
F3_W_EMPTY_COST = 2.0
F3_W_LOAD_BALANCE = 10.0
F3_W_ENERGY = 1.0

# 衔接路径缺失时的代用通行时间/距离（仅用于让时间链可以继续推演，
# 缺失数本身在字典序最高层被惩罚，因此该取值不影响方法间排序的首要比较）
MISSING_SURROGATE_TIME = 10.0
MISSING_SURROGATE_DISTANCE = 10.0
