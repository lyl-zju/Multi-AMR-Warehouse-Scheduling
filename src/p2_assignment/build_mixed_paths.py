"""构建 mixed 候选路径库：融合 P1 四种规划算法的输出，形成多候选路径集。

动机：每个 P1 算法对同一 OD 只输出 1 条路径，P2 的候选路径选择
（命题第 7 节）无从谈起。四个算法的路径几何各异，合并后即天然候选集。

处理步骤：
    1. path_uid 加算法前缀（如 vg::IN1__P5__path_1），全局唯一且可溯源；
    2. 统一成本口径 unified_cost = travel_time + TURN_WEIGHT * turn_count，
       写入 total_cost 列。各算法的原始 total_cost 构成不同
       （basic_astar 纯距离、avg 含转角、davg 含动态代价），直接混排会
       系统性偏向"算得便宜"的算法，故用各算法均可比的物理量重算；
       原始值保留在 source_total_cost 列。davg 的动态区域暴露在
       dynamic_cost 列中保留，可供下游附加惩罚；
    3. 几何去重：同一 OD 内将路径折线重采样为等距点列，最大偏差小于
       DEDUP_TOLERANCE 视为同一条路径，保留 unified_cost 较低者；
    4. 按 OD 重排 rank_by_total / path_id，同步导出对应轨迹样本。

输出（目录名 mixed 即第五个"伪算法"，P2/P3 用 --p1-algorithm mixed 消费，
下游代码零改动）：
    data/processed/p1/mixed/path_cost.csv
    data/processed/p1/mixed/path_trajectory_samples.csv

用法（项目根目录）：
    python .\\src\\p2_assignment\\build_mixed_paths.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
P1_DIR = PROJECT_ROOT / "data" / "processed" / "p1"
SOURCE_ALGORITHMS = ("basic_astar", "vg", "avg", "davg")
OUT_DIR = P1_DIR / "mixed"

TURN_WEIGHT = 0.5        # 每次转弯的统一惩罚（同 main 分支 P1 的转弯成本取值）
DEDUP_TOLERANCE = 0.30   # 几何去重阈值：重采样点最大偏差（米），约一个栅格
RESAMPLE_POINTS = 24


def parse_geometry(text):
    pts = [p.split(",") for p in str(text).split(";") if p]
    return np.array([[float(x), float(y)] for x, y in pts])


def resample_polyline(points, n=RESAMPLE_POINTS):
    """按弧长等距重采样折线，便于不同采样密度的路径互相比较。"""
    if len(points) == 1:
        return np.repeat(points, n, axis=0)
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = s[-1]
    if total <= 0:
        return np.repeat(points[:1], n, axis=0)
    targets = np.linspace(0.0, total, n)
    out = np.empty((n, 2))
    for i, t in enumerate(targets):
        idx = np.searchsorted(s, t, side="right") - 1
        idx = min(idx, len(seg) - 1)
        frac = (t - s[idx]) / seg[idx] if seg[idx] > 0 else 0.0
        out[i] = points[idx] + frac * (points[idx + 1] - points[idx])
    return out


def max_deviation(geom_a, geom_b):
    ra, rb = resample_polyline(geom_a), resample_polyline(geom_b)
    return float(np.linalg.norm(ra - rb, axis=1).max())


def load_sources():
    costs, trajs = [], []
    for alg in SOURCE_ALGORITHMS:
        cost_csv = P1_DIR / alg / "path_cost.csv"
        traj_csv = P1_DIR / alg / "path_trajectory_samples.csv"
        if not cost_csv.exists():
            print(f"[跳过] {alg}: 未找到 {cost_csv}")
            continue
        cost = pd.read_csv(cost_csv)
        cost = cost[cost["planning_status"] == "ok"].copy()
        cost["source_algorithm"] = alg
        cost["source_path_uid"] = cost["path_uid"]
        cost["path_uid"] = alg + "::" + cost["path_uid"]
        costs.append(cost)

        traj = pd.read_csv(traj_csv)
        traj["path_uid"] = alg + "::" + traj["path_uid"]
        trajs.append(traj)
    if not costs:
        raise FileNotFoundError("没有任何可用的 P1 算法输出，请先运行 P1。")
    return pd.concat(costs, ignore_index=True), pd.concat(trajs, ignore_index=True)


def dedup_group(group):
    """同一 OD 内按 unified_cost 升序，几何近似重复的只保留最优者。"""
    kept = []
    for row in group.sort_values("total_cost").itertuples(index=False):
        geom = parse_geometry(row.path_geometry)
        if all(max_deviation(geom, parse_geometry(k.path_geometry)) > DEDUP_TOLERANCE
               for k in kept):
            kept.append(row)
    return pd.DataFrame(kept)


def main():
    cost, traj = load_sources()

    cost["source_total_cost"] = cost["total_cost"]
    cost["total_cost"] = cost["travel_time"] + TURN_WEIGHT * cost["turn_count"]

    before = len(cost)
    cost = (cost.groupby(["from_node", "to_node"], sort=True, group_keys=False)
                .apply(dedup_group)
                .reset_index(drop=True))
    print(f"几何去重: {before} -> {len(cost)} 条")

    # 重排同 OD 内的候选编号
    cost = cost.sort_values(["from_node", "to_node", "total_cost"]).reset_index(drop=True)
    cost["rank_by_total"] = cost.groupby(["from_node", "to_node"]).cumcount() + 1
    cost["path_id"] = "path_" + cost["rank_by_total"].astype(str)
    cost["algorithm"] = "mixed(" + cost["source_algorithm"] + ")"

    traj = traj[traj["path_uid"].isin(set(cost["path_uid"]))].copy()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cost.to_csv(OUT_DIR / "path_cost.csv", index=False)
    traj.to_csv(OUT_DIR / "path_trajectory_samples.csv", index=False)

    per_od = cost.groupby(["from_node", "to_node"]).size()
    contrib = cost["source_algorithm"].value_counts()
    print(f"OD 对数: {per_od.index.nunique()}, "
          f"平均候选数: {per_od.mean():.2f}, 最多: {per_od.max()}")
    print("各算法贡献的候选数:")
    for alg, n in contrib.items():
        print(f"  {alg}: {n}")
    print(f"已保存: {OUT_DIR / 'path_cost.csv'}")
    print(f"已保存: {OUT_DIR / 'path_trajectory_samples.csv'}")


if __name__ == "__main__":
    main()
