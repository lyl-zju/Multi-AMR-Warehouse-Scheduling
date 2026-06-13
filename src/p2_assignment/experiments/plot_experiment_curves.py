"""敏感性实验曲线图：从 p2_method_comparison.csv 生成实验二/三/五的图。

用法（项目根目录，先跑完 run_experiments.py --exp all）：
    python .\\src\\p2_assignment\\experiments\\plot_experiment_curves.py

输出：
    outputs/p2/sensitivity_amr_count.png      实验二：AMR 数量敏感性
    outputs/p2/sensitivity_task_density.png   实验三：任务密度敏感性
    outputs/p2/path_source_comparison.png     实验五：路径源对比
"""

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXP_CSV = PROJECT_ROOT / "outputs" / "p2_experiments" / "p2_method_comparison.csv"
OUT_DIR = PROJECT_ROOT / "outputs" / "p2"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
plt.rcParams["axes.unicode_minus"] = False

METHOD_STYLE = {
    "m0": {"color": "#9aa0a6", "marker": "s", "label": "m0 贪心"},
    "m1": {"color": "#e9c46a", "marker": "^", "label": "m1 两阶段 IP+DP"},
    "m2": {"color": "#c1121f", "marker": "o", "label": "m2 联合 MILP"},
    "m3": {"color": "#2a9d8f", "marker": "D", "label": "m3 插入+局部搜索"},
}
PANELS = [
    ("Cmax", "Cmax 最大完成时间", False),
    ("total_delay", "总延期", False),
    ("load_balance_penalty", "负载不均衡", False),
    ("solve_time", "求解时间 (秒, 对数轴)", True),
]


def _line_panels(df, xcol, xlabel, title, out_path):
    fig, axes = plt.subplots(1, len(PANELS), figsize=(16, 3.4), dpi=160)
    for ax, (col, ptitle, logy) in zip(axes, PANELS):
        for method, style in METHOD_STYLE.items():
            sub = df[df["method"] == method]
            if sub.empty:
                continue
            agg = sub.groupby(xcol)[col].mean()
            ax.plot(agg.index, agg.values, marker=style["marker"],
                    color=style["color"], label=style["label"], lw=1.8, ms=5)
        if logy:
            ax.set_yscale("log")
        ax.set_title(ptitle, fontsize=10.5)
        ax.set_xlabel(xlabel)
        ax.grid(lw=0.4, alpha=0.5)
    axes[0].legend(fontsize=8)
    fig.suptitle(title, fontsize=12.5)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    print(f"已保存: {out_path}")


def plot_exp2(df, out_path):
    sub = df[df["instance"].str.startswith("amr_n")]
    if sub.empty:
        print("[跳过] 实验二曲线：无 amr_n* 记录")
        return
    _line_panels(sub, "amr_count", "AMR 数量",
                 "实验二：AMR 数量敏感性（12 任务固定）", out_path)


def plot_exp3(df, out_path):
    sub = df[df["instance"].str.startswith("tasks_n")]
    if sub.empty:
        print("[跳过] 实验三曲线：无 tasks_n* 记录")
        return
    _line_panels(sub, "task_count", "任务数量",
                 "实验三：任务密度敏感性（4 台 AMR 固定，3 种子均值）", out_path)


def plot_exp5(df, out_path):
    sub = df[df["method"].str.startswith("m3@")].copy()
    if sub.empty:
        print("[跳过] 实验五图：无 m3@* 记录")
        return
    sub["source"] = sub["method"].str.split("@").str[1]
    sub = sub.set_index("source")
    order = ["basic_astar", "vg", "avg", "davg", "mixed"]
    sub = sub.reindex([s for s in order if s in sub.index.values])

    panels = [("F2", "时间效率层 F2"), ("F3", "运行成本层 F3"),
              ("Cmax", "Cmax"), ("empty_cost", "空驶成本")]
    fig, axes = plt.subplots(1, len(panels), figsize=(15, 3.4), dpi=160)
    colors = ["#9aa0a6"] * (len(sub) - 1) + ["#c1121f"]  # mixed 高亮
    for ax, (col, title) in zip(axes, panels):
        vals = sub[col].astype(float)
        ax.bar(vals.index, vals.values,
               color=colors[: len(vals)] if "mixed" in vals.index else "#9aa0a6")
        for i, v in enumerate(vals.values):
            ax.text(i, v, f"{v:.1f}", ha="center", va="bottom", fontsize=8.5)
        ax.set_title(title, fontsize=10.5)
        ax.tick_params(axis="x", rotation=20)
        ax.grid(axis="y", lw=0.4, alpha=0.5)
    fig.suptitle("实验五：P1 路径源对比（固定 m3，红色为 mixed 融合候选库）",
                 fontsize=12.5)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    print(f"已保存: {out_path}")


def main():
    if not EXP_CSV.exists():
        print(f"未找到 {EXP_CSV}，请先运行 run_experiments.py")
        return 1
    df = pd.read_csv(EXP_CSV)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plot_exp2(df, OUT_DIR / "sensitivity_amr_count.png")
    plot_exp3(df, OUT_DIR / "sensitivity_task_density.png")
    plot_exp5(df, OUT_DIR / "path_source_comparison.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
