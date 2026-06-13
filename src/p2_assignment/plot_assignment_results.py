"""P2 结果可视化：甘特图、方法对比、任务分配空间图、候选路径选择图。

用法（项目根目录，需先运行 build_mixed_paths.py）：
    python .\\src\\p2_assignment\\plot_assignment_results.py
    python .\\src\\p2_assignment\\plot_assignment_results.py --p1-algorithm mixed --baseline m0 --optimized m3

输出：
    outputs/p2/gantt_<baseline>_vs_<optimized>.png   上下对照甘特图
    outputs/p2/assignment_map_<method>.png           任务链空间图（两方法各一张）
    outputs/p2/candidate_selection.png               候选路径选择示例图
    outputs/p2/method_comparison.png                 方法对比柱状图（需先跑实验一）
"""

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

P2_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(P2_DIR))

from assign_and_sequence_tasks import run, SOLVER_LABELS  # noqa: E402

PROJECT_ROOT = P2_DIR.parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
OUT_DIR = PROJECT_ROOT / "outputs" / "p2"
EXP_CSV = PROJECT_ROOT / "outputs" / "p2_experiments" / "p2_method_comparison.csv"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
plt.rcParams["axes.unicode_minus"] = False

AMR_COLORS = ["#2a9d8f", "#e76f51", "#457b9d", "#8d5a97", "#e9c46a", "#6c757d"]
PRIORITY_ALPHA = {1: 0.55, 2: 0.75, 3: 0.95}

ZONE_STYLE = {
    "inbound": {"facecolor": "#d9f3ee", "edgecolor": "#2a9d8f"},
    "outbound": {"facecolor": "#fde2d8", "edgecolor": "#e76f51"},
    "sorting": {"facecolor": "#dcebf6", "edgecolor": "#457b9d"},
    "charger": {"facecolor": "#eadcf0", "edgecolor": "#8d5a97"},
}
OBSTACLE_STYLE = {
    "rack": {"facecolor": "#f4a261", "edgecolor": "#a65f12", "alpha": 0.6},
    "wall": {"facecolor": "#4b5563", "edgecolor": "#111827", "alpha": 0.85},
    "dynamic_block": {"facecolor": "#f7b7bd", "edgecolor": "#c1121f", "alpha": 0.6},
}


def amr_color(data, amr_id):
    return AMR_COLORS[data.amr_ids.index(amr_id) % len(AMR_COLORS)]


def parse_geometry(text):
    pts = [p.split(",") for p in str(text).split(";") if p]
    return np.array([[float(x), float(y)] for x, y in pts])


# ---------------------------------------------------------------- 甘特图
def plot_gantt_pair(results, out_path):
    """results: [(label, data, solution, evaluation), ...] 上下两幅对照。"""
    fig, axes = plt.subplots(len(results), 1, figsize=(12, 3.1 * len(results)),
                             dpi=160, sharex=True)
    axes = np.atleast_1d(axes)
    tmax = max(ev.metrics["Cmax"] for _, _, _, ev in results) * 1.06

    for ax, (label, data, solution, evaluation) in zip(axes, results):
        for yi, amr_id in enumerate(data.amr_ids):
            tl = evaluation.timelines[amr_id]
            color = amr_color(data, amr_id)
            for leg in tl.legs:
                task = data.tasks[leg.task_id]
                # 空驶段：细灰条
                ax.barh(yi, leg.trans_time, left=leg.arrival_pickup - leg.trans_time,
                        height=0.28, color="#c9c9c9", zorder=2)
                # 等待段：斜纹
                if leg.waiting > 1e-9:
                    ax.barh(yi, leg.waiting, left=leg.arrival_pickup, height=0.28,
                            color="white", edgecolor="#999", hatch="///", zorder=2)
                # 服务+载货段：主色块，延期描红边
                work = task.service_time + leg.loaded_time
                is_late = leg.delay > 1e-9
                ax.barh(yi, work, left=leg.start_time, height=0.62,
                        color=color, alpha=PRIORITY_ALPHA.get(int(task.priority), 0.75),
                        edgecolor="#c1121f" if is_late else "#333",
                        linewidth=2.2 if is_late else 0.6, zorder=3)
                ax.text(leg.start_time + work / 2, yi, leg.task_id, ha="center",
                        va="center", fontsize=8.5, zorder=4,
                        color="white" if not is_late else "#7a0010",
                        fontweight="bold" if is_late else "normal")
            ax.axvline(tl.finish_time, color=color, lw=0.8, ls=":", zorder=1)

        m = evaluation.metrics
        ax.set_yticks(range(len(data.amr_ids)))
        ax.set_yticklabels(data.amr_ids)
        ax.invert_yaxis()
        ax.set_xlim(0, tmax)
        ax.grid(axis="x", lw=0.4, alpha=0.5)
        ax.set_title(
            f"{label}    Cmax={m['Cmax']:.1f}  延期任务={int(m['late_count'])}  "
            f"F2={m['F2']:.1f}  F3={m['F3']:.1f}",
            fontsize=11, loc="left")

    axes[-1].set_xlabel("时间")
    legend = [
        Rectangle((0, 0), 1, 1, color="#c9c9c9"),
        Rectangle((0, 0), 1, 1, facecolor="white", edgecolor="#999", hatch="///"),
        Rectangle((0, 0), 1, 1, color="#888"),
        Rectangle((0, 0), 1, 1, facecolor="#888", edgecolor="#c1121f", linewidth=2.2),
    ]
    axes[0].legend(legend, ["空驶", "等待", "服务+载货", "延期任务（红边）"],
                   loc="lower left", bbox_to_anchor=(0.0, 1.18), fontsize=8.5,
                   ncol=4, frameon=False)
    fig.suptitle("P2 调度甘特图对照", fontsize=13, y=1.04)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    print(f"已保存: {out_path}")


# ---------------------------------------------------------- 任务分配空间图
def draw_floor(ax):
    zones = pd.read_csv(RAW_DIR / "floor_zones.csv", skipinitialspace=True)
    obstacles = pd.read_csv(RAW_DIR / "floor_obstacles.csv", skipinitialspace=True)
    nodes = pd.read_csv(RAW_DIR / "nodes.csv")
    ax.add_patch(Rectangle((-0.8, -0.8), 13.6, 7.4, facecolor="#fbfbf7",
                           edgecolor="#111827", lw=1.6, zorder=0))
    for df, style_map, field in ((zones, ZONE_STYLE, "zone_type"),
                                 (obstacles, OBSTACLE_STYLE, "obstacle_type")):
        for it in df.itertuples(index=False):
            st = style_map.get(getattr(it, field), {})
            ax.add_patch(Rectangle((it.x, it.y), it.width, it.height,
                                   facecolor=st.get("facecolor", "#eee"),
                                   edgecolor=st.get("edgecolor", "#999"),
                                   alpha=st.get("alpha", 0.3), lw=1.1, zorder=1))
    for n in nodes.itertuples(index=False):
        if n.node_type == "junction":
            continue
        ax.scatter(n.x, n.y, s=26, color="#555", zorder=4)
        ax.text(n.x, n.y + 0.13, n.node_id, fontsize=7, ha="center", zorder=5)
    ax.set_xlim(-1.0, 13.0)
    ax.set_ylim(-1.0, 6.8)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    return pd.read_csv(RAW_DIR / "nodes.csv")


def plot_assignment_map(label, data, solution, evaluation, out_path):
    fig, ax = plt.subplots(figsize=(12.5, 7), dpi=160)
    draw_floor(ax)

    for amr_id in data.amr_ids:
        tl = evaluation.timelines[amr_id]
        color = amr_color(data, amr_id)
        amr = data.amrs[amr_id]
        ax.scatter(*_node_xy(amr.init_node), s=180, marker="*", color=color,
                   edgecolor="black", zorder=6)
        step = 0
        for leg in tl.legs:
            for path, ls in ((leg.trans_path, (0, (4, 3))), (leg.loaded_path, "solid")):
                if path is None or path.get("path_geometry") is None:
                    continue
                geom = parse_geometry(path["path_geometry"])
                if len(geom) < 2:
                    continue
                ax.plot(geom[:, 0], geom[:, 1], color=color, lw=2.0,
                        ls=ls, alpha=0.85, zorder=3)
                # 路径中点标注执行顺序
                if ls == "solid":
                    step += 1
                    mid = geom[len(geom) // 2]
                    ax.annotate(f"{step}", mid, fontsize=8, fontweight="bold",
                                color="white", ha="center", va="center", zorder=7,
                                bbox=dict(boxstyle="circle,pad=0.18", fc=color,
                                          ec="black", lw=0.5))

    handles = [Line2D([0], [0], color=amr_color(data, a), lw=2.4,
                      label=f"{a} ({'->'.join(l.task_id for l in evaluation.timelines[a].legs)})")
               for a in data.amr_ids]
    handles += [Line2D([0], [0], color="#555", lw=2, ls=(0, (4, 3)), label="空驶段"),
                Line2D([0], [0], color="#555", lw=2, label="载货段")]
    ax.legend(handles=handles, loc="upper left", fontsize=8, framealpha=0.92)
    m = evaluation.metrics
    ax.set_title(f"任务分配空间图 - {label}    "
                 f"Cmax={m['Cmax']:.1f}  空驶成本={m['empty_cost']:.1f}",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    print(f"已保存: {out_path}")


_NODE_XY = None


def _node_xy(node_id):
    global _NODE_XY
    if _NODE_XY is None:
        nodes = pd.read_csv(RAW_DIR / "nodes.csv")
        _NODE_XY = {r.node_id: (r.x, r.y) for r in nodes.itertuples(index=False)}
    return _NODE_XY[node_id]


# ---------------------------------------------------------- 候选路径选择图
def plot_candidate_selection(data, solution, evaluation, out_path, max_examples=2):
    """挑选候选数>=2 且被选中路径并非唯一选择的移动段做展示。"""
    examples = []
    for amr_id in data.amr_ids:
        for leg in evaluation.timelines[amr_id].legs:
            for path in (leg.trans_path, leg.loaded_path):
                if path is None:
                    continue
                cands = data.paths.candidates(path["from_node"], path["to_node"])
                if len(cands) >= 2:
                    examples.append((path, cands, amr_id))
    if not examples:
        print("[跳过] 候选选择图：没有候选数>=2 的移动段（是否在用 mixed 库？）")
        return
    # 优先展示候选最多的段
    examples.sort(key=lambda e: -len(e[1]))
    examples = examples[:max_examples]

    fig, axes = plt.subplots(1, len(examples), figsize=(8.5 * len(examples), 5.5),
                             dpi=160)
    axes = np.atleast_1d(axes)
    for ax, (chosen, cands, amr_id) in zip(axes, examples):
        draw_floor(ax)
        for cand in cands:
            geom = parse_geometry(cand["path_geometry"])
            is_chosen = cand["path_uid"] == chosen["path_uid"]
            ax.plot(geom[:, 0], geom[:, 1],
                    color="#c1121f" if is_chosen else "#9aa0a6",
                    lw=3.0 if is_chosen else 1.4,
                    alpha=0.95 if is_chosen else 0.8, zorder=4 if is_chosen else 3)
            mid = geom[len(geom) // 3]
            src = cand.get("source_algorithm", cand.get("algorithm", ""))
            ax.text(mid[0], mid[1] + 0.12,
                    f"{src} c={cand['total_cost']:.1f}",
                    fontsize=8, color="#c1121f" if is_chosen else "#5f6368",
                    fontweight="bold" if is_chosen else "normal", zorder=6)
        ax.set_title(f"{chosen['from_node']} -> {chosen['to_node']}（{amr_id}，"
                     f"{len(cands)} 条候选，红色为 P2 选中）", fontsize=10.5)
    fig.suptitle("候选路径选择示例：P2 在 mixed 候选库中按调度目标择路", fontsize=12.5)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    print(f"已保存: {out_path}")


# ---------------------------------------------------------- 方法对比柱状图
def plot_method_comparison(out_path, instance="base"):
    if not EXP_CSV.exists():
        print(f"[跳过] 方法对比图：未找到 {EXP_CSV}，请先运行实验一")
        return
    df = pd.read_csv(EXP_CSV)
    df = df[(df["instance"] == instance) & df["method"].isin(["m0", "m1", "m2", "m3"])]
    if df.empty:
        print("[跳过] 方法对比图：实验 CSV 中没有 base 算例的 m0~m3 记录")
        return
    df = df.set_index("method").reindex(["m0", "m1", "m2", "m3"]).dropna(how="all")

    panels = [
        ("late_count", "延期任务数 (F1)", False),
        ("F2", "时间效率层 F2", False),
        ("F3", "运行成本层 F3", False),
        ("solve_time", "求解时间 (秒, 对数轴)", True),
    ]
    fig, axes = plt.subplots(1, 4, figsize=(15, 3.6), dpi=160)
    colors = ["#9aa0a6", "#e9c46a", "#c1121f", "#2a9d8f"]
    for ax, (col, title, logy) in zip(axes, panels):
        vals = df[col].astype(float)
        ax.bar(vals.index, vals.values, color=colors[: len(vals)])
        for i, v in enumerate(vals.values):
            ax.text(i, v, f"{v:.3g}", ha="center", va="bottom", fontsize=8.5)
        if logy:
            ax.set_yscale("log")
        ax.set_title(title, fontsize=10.5)
        ax.grid(axis="y", lw=0.4, alpha=0.5)
    fig.suptitle("P2 四种方法对比（base 算例）", fontsize=12.5)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    print(f"已保存: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="P2 结果可视化")
    parser.add_argument("--p1-algorithm", default="mixed")
    parser.add_argument("--baseline", default="m0", help="对照方法")
    parser.add_argument("--optimized", default="m3", help="优化方法（m2 很慢，默认 m3）")
    parser.add_argument("--time-limit", type=int, default=60)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for method in (args.baseline, args.optimized):
        print(f"求解 {method} ({args.p1_algorithm}) ...")
        data, solution, evaluation, _ = run(
            method=method, p1_algorithm=args.p1_algorithm,
            time_limit=args.time_limit, write_outputs=False)
        results.append((f"{method} {SOLVER_LABELS[method]}", data, solution, evaluation))

    plot_gantt_pair(results, OUT_DIR / f"gantt_{args.baseline}_vs_{args.optimized}.png")
    for (label, data, solution, evaluation), method in zip(results,
                                                           (args.baseline, args.optimized)):
        plot_assignment_map(label, data, solution, evaluation,
                            OUT_DIR / f"assignment_map_{method}.png")
    # 候选选择图用优化方法的结果
    _, data, solution, evaluation = results[-1]
    plot_candidate_selection(data, solution, evaluation,
                             OUT_DIR / "candidate_selection.png")
    plot_method_comparison(OUT_DIR / "method_comparison.png")


if __name__ == "__main__":
    main()
