"""P2 公共层：数据加载、时间线推演、字典序评价、结果输出。"""

from .data_io import ProblemData, PathLibrary, Task, Amr, load_problem
from .evaluator import Solution, Evaluation, evaluate, lex_to_scalar
from .timeline import simulate_amr, compute_leg

__all__ = [
    "ProblemData", "PathLibrary", "Task", "Amr", "load_problem",
    "Solution", "Evaluation", "evaluate", "lex_to_scalar",
    "simulate_amr", "compute_leg",
]
