import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from plot_candidate_paths import (
    PROJECT_ROOT,
    draw_base_floor,
    parse_path_geometry,
    p1_processed_dir,
)


ALGORITHMS = ["basic_astar", "vg", "avg", "davg"]
ALGORITHM_LABELS = {
    "basic_astar": "Grid A*",
    "vg": "VG",
    "avg": "AVG",
    "davg": "DAVG",
}
ALGORITHM_COLORS = {
    "basic_astar": "#d81b60",
    "vg": "#1e88e5",
    "avg": "#43a047",
    "davg": "#fb8c00",
}
DEFAULT_PAIRS = [("IN1", "P5"), ("P5", "SORT1"), ("P8", "OUT2")]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "p1_experiments"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate non-destructive comparison figures for P1 algorithms."
    )
    parser.add_argument(
        "--pairs",
        nargs="*",
        default=[f"{start}:{goal}" for start, goal in DEFAULT_PAIRS],
        help="OD pairs to compare, formatted as FROM:TO.",
    )
    return parser.parse_args()


def parse_pairs(pair_tokens):
    pairs = []
    for token in pair_tokens:
        if ":" not in token:
            raise ValueError(f"Invalid pair '{token}'. Use FROM:TO, for example IN1:P5.")
        start, goal = token.split(":", 1)
        pairs.append((start.strip(), goal.strip()))
    return pairs


def load_floor_data():
    nodes = pd.read_csv(RAW_DATA_DIR / "nodes.csv")
    zones = pd.read_csv(RAW_DATA_DIR / "floor_zones.csv", skipinitialspace=True)
    obstacles = pd.read_csv(RAW_DATA_DIR / "floor_obstacles.csv", skipinitialspace=True)
    return nodes, zones, obstacles


def load_algorithm_paths():
    frames = []
    for algorithm in ALGORITHMS:
        path = p1_processed_dir(algorithm) / "path_cost.csv"
        frame = pd.read_csv(path)
        frame = frame[frame["planning_status"] == "ok"].copy()
        frame["source_algorithm"] = algorithm
        frame["waypoint_count"] = frame["path_geometry"].apply(lambda value: len(parse_path_geometry(value)))
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def summarize_algorithms(paths):
    grouped = paths.groupby("source_algorithm", sort=False)
    summary = grouped.agg(
        path_count=("path_uid", "count"),
        avg_distance=("distance", "mean"),
        avg_travel_time=("travel_time", "mean"),
        avg_turn_count=("turn_count", "mean"),
        avg_turn_cost=("turn_cost", "mean"),
        avg_dynamic_cost=("dynamic_cost", "mean"),
        max_dynamic_cost=("dynamic_cost", "max"),
        avg_total_cost=("total_cost", "mean"),
        avg_waypoint_count=("waypoint_count", "mean"),
        avg_trajectory_samples=("trajectory_sample_count", "mean"),
    )
    summary["dynamic_path_share"] = grouped["dynamic_cost"].apply(lambda values: (values > 0).mean())
    summary = summary.reset_index()
    summary.insert(1, "label", summary["source_algorithm"].map(ALGORITHM_LABELS))
    numeric_columns = summary.select_dtypes(include=[np.number]).columns
    summary[numeric_columns] = summary[numeric_columns].round(3)
    return summary


def save_algorithm_summary(paths, output_dir):
    summary = summarize_algorithms(paths)
    summary.to_csv(output_dir / "p1_algorithm_summary.csv", index=False, encoding="utf-8-sig")
    return summary


def plot_metric_summary(summary, output_path):
    metrics = [
        ("avg_distance", "Average distance"),
        ("avg_turn_count", "Average turns"),
        ("avg_dynamic_cost", "Average dynamic cost"),
        ("avg_total_cost", "Average total cost"),
    ]
    labels = summary["label"].tolist()
    colors = [ALGORITHM_COLORS[algorithm] for algorithm in summary["source_algorithm"]]

    fig, axes = plt.subplots(2, 2, figsize=(12.5, 8.5), dpi=160)
    for ax, (metric, title) in zip(axes.flat, metrics):
        values = summary[metric].to_numpy(dtype=float)
        bars = ax.bar(labels, values, color=colors, alpha=0.86)
        ax.set_title(title, fontsize=12, pad=10)
        ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.35)
        ax.set_axisbelow(True)
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                f"{value:g}",
                ha="center",
                va="bottom",
                fontsize=9,
            )
    fig.suptitle("P1 algorithm-level metric comparison", fontsize=15, y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def pair_rows(paths, start, goal):
    rows = paths[(paths["from_node"] == start) & (paths["to_node"] == goal)].copy()
    rows["label"] = rows["source_algorithm"].map(ALGORITHM_LABELS)
    return rows.sort_values("source_algorithm")


def plot_pair_path_comparison(nodes, zones, obstacles, paths, start, goal, output_path):
    rows = pair_rows(paths, start, goal)
    if rows.empty:
        return False

    fig, axes = plt.subplots(2, 2, figsize=(13.2, 8.6), dpi=160)
    for ax, algorithm in zip(axes.flat, ALGORITHMS):
        draw_base_floor(ax, nodes, zones, obstacles)
        row = rows[rows["source_algorithm"] == algorithm]
        color = ALGORITHM_COLORS[algorithm]
        if not row.empty:
            record = row.iloc[0]
            points = parse_path_geometry(record["path_geometry"])
            if points:
                xs = [point[0] for point in points]
                ys = [point[1] for point in points]
                ax.plot(xs, ys, color=color, linewidth=3.4, alpha=0.9, zorder=9)
                ax.scatter(xs[0], ys[0], s=150, facecolors="white", edgecolors="#111827", linewidths=2, zorder=10)
                ax.scatter(xs[-1], ys[-1], s=150, marker="s", facecolors="white", edgecolors=color, linewidths=2, zorder=10)
            metric_text = (
                f"d={record['distance']:.3f}, t={record['travel_time']:.3f}\n"
                f"turn={record['turn_count']:.0f}, dyn={record['dynamic_cost']:.3f}, total={record['total_cost']:.3f}"
            )
        else:
            metric_text = "not available"
        ax.text(
            0.02,
            0.98,
            metric_text,
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=8.5,
            bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.92, "boxstyle": "round,pad=0.28"},
            zorder=20,
        )
        ax.set_title(ALGORITHM_LABELS[algorithm], fontsize=12, color=color, pad=8)
        ax.set_xlabel("x")
        ax.set_ylabel("y")
        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, linestyle="--", linewidth=0.45, alpha=0.28)
        ax.set_xlim(-1.0, 13.0)
        ax.set_ylim(-1.0, 6.8)

    fig.suptitle(f"P1 path geometry comparison: {start} to {goal}", fontsize=15, y=0.99)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return True


def save_pair_metrics(paths, pairs, output_dir):
    pair_frames = []
    for start, goal in pairs:
        rows = pair_rows(paths, start, goal)
        if rows.empty:
            continue
        pair_frames.append(
            rows[
                [
                    "from_node",
                    "to_node",
                    "label",
                    "source_algorithm",
                    "distance",
                    "travel_time",
                    "turn_count",
                    "turn_cost",
                    "dynamic_cost",
                    "total_cost",
                    "trajectory_sample_count",
                    "waypoint_count",
                ]
            ]
        )
    if not pair_frames:
        return pd.DataFrame()
    pair_metrics = pd.concat(pair_frames, ignore_index=True)
    pair_metrics.to_csv(output_dir / "p1_pair_metrics.csv", index=False, encoding="utf-8-sig")
    return pair_metrics


def load_mixed_paths():
    mixed_path = PROJECT_ROOT / "data" / "processed" / "p1" / "mixed" / "path_cost.csv"
    if not mixed_path.exists():
        return pd.DataFrame()
    mixed = pd.read_csv(mixed_path)
    return mixed[mixed["planning_status"] == "ok"].copy()


def save_mixed_diversity(mixed, output_dir):
    if mixed.empty:
        return pd.DataFrame(), pd.DataFrame()

    counts = (
        mixed.groupby(["from_node", "to_node"])
        .size()
        .reset_index(name="candidate_count")
        .sort_values(["candidate_count", "from_node", "to_node"], ascending=[False, True, True])
    )
    distribution = counts["candidate_count"].value_counts().sort_index().reset_index()
    distribution.columns = ["candidate_count", "od_pair_count"]
    counts.to_csv(output_dir / "p1_mixed_candidate_counts.csv", index=False, encoding="utf-8-sig")
    distribution.to_csv(output_dir / "p1_mixed_candidate_distribution.csv", index=False, encoding="utf-8-sig")
    return counts, distribution


def plot_mixed_candidate_heatmap(counts, output_path):
    if counts.empty:
        return False

    from_nodes = sorted(counts["from_node"].unique())
    to_nodes = sorted(counts["to_node"].unique())
    matrix = counts.pivot(index="from_node", columns="to_node", values="candidate_count").reindex(
        index=from_nodes,
        columns=to_nodes,
    )
    matrix.to_csv(output_path.with_suffix(".csv"), encoding="utf-8-sig")

    values = matrix.to_numpy(dtype=float)
    masked = np.ma.masked_invalid(values)
    cmap = plt.cm.Blues.copy()
    cmap.set_bad(color="#f3f4f6")

    fig, ax = plt.subplots(figsize=(11.5, 9.5), dpi=160)
    image = ax.imshow(masked, cmap=cmap, vmin=0, vmax=max(4, np.nanmax(values)))
    ax.set_title("Mixed path library candidate count by OD pair", fontsize=14, pad=14)
    ax.set_xlabel("to_node")
    ax.set_ylabel("from_node")
    ax.set_xticks(range(len(to_nodes)))
    ax.set_yticks(range(len(from_nodes)))
    ax.set_xticklabels(to_nodes, rotation=45, ha="right", fontsize=7)
    ax.set_yticklabels(from_nodes, fontsize=7)
    for i in range(values.shape[0]):
        for j in range(values.shape[1]):
            value = values[i, j]
            if np.isfinite(value):
                ax.text(j, i, f"{int(value)}", ha="center", va="center", fontsize=6.5)
    fig.colorbar(image, ax=ax, shrink=0.78, label="candidate count")
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return True


def main():
    args = parse_args()
    pairs = parse_pairs(args.pairs)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    nodes, zones, obstacles = load_floor_data()
    paths = load_algorithm_paths()
    summary = save_algorithm_summary(paths, OUTPUT_DIR)
    plot_metric_summary(summary, OUTPUT_DIR / "p1_algorithm_metric_summary.png")

    pair_metrics = save_pair_metrics(paths, pairs, OUTPUT_DIR)
    for start, goal in pairs:
        output_path = OUTPUT_DIR / f"p1_path_comparison_{start}_{goal}.png"
        plot_pair_path_comparison(nodes, zones, obstacles, paths, start, goal, output_path)

    mixed = load_mixed_paths()
    counts, distribution = save_mixed_diversity(mixed, OUTPUT_DIR)
    plot_mixed_candidate_heatmap(counts, OUTPUT_DIR / "p1_mixed_candidate_count_heatmap.png")

    print(f"Saved experiment outputs to: {OUTPUT_DIR}")
    print("Algorithm summary:")
    print(summary.to_string(index=False))
    if not pair_metrics.empty:
        print("Pair metrics:")
        print(pair_metrics.to_string(index=False))
    if not distribution.empty:
        print("Mixed candidate distribution:")
        print(distribution.to_string(index=False))


if __name__ == "__main__":
    main()
