import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, Rectangle


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
P1_ALGORITHMS = ("basic_astar", "vg", "avg", "davg")
DEFAULT_P1_ALGORITHM = "basic_astar"

ZONE_STYLE = {
    "inbound": {"facecolor": "#d9f3ee", "edgecolor": "#2a9d8f"},
    "outbound": {"facecolor": "#fde2d8", "edgecolor": "#e76f51"},
    "sorting": {"facecolor": "#dcebf6", "edgecolor": "#457b9d"},
    "charger": {"facecolor": "#eadcf0", "edgecolor": "#8d5a97"},
}

OBSTACLE_STYLE = {
    "rack": {"facecolor": "#f4a261", "edgecolor": "#a65f12", "alpha": 0.72},
    "wall": {"facecolor": "#4b5563", "edgecolor": "#111827", "alpha": 0.86},
    "dynamic_block": {"facecolor": "#f7b7bd", "edgecolor": "#c1121f", "alpha": 0.75},
}

POINT_STYLE = {
    "inbound": {"color": "#2a9d8f", "marker": "s", "size": 130},
    "outbound": {"color": "#e76f51", "marker": "s", "size": 130},
    "shelf": {"color": "#b45309", "marker": "o", "size": 105},
    "sorting": {"color": "#457b9d", "marker": "D", "size": 125},
    "charger": {"color": "#8d5a97", "marker": "P", "size": 145},
    "junction": {"color": "#6c757d", "marker": ".", "size": 45},
}

PATH_COLORS = ["#e11d48", "#2563eb", "#16a34a"]


def p1_processed_dir(algorithm):
    return PROJECT_ROOT / "data" / "processed" / "p1" / algorithm


def p1_output_dir(algorithm):
    return PROJECT_ROOT / "outputs" / "p1" / algorithm


def parse_args():
    parser = argparse.ArgumentParser(description="Plot P1 candidate paths for a selected planner.")
    parser.add_argument(
        "--algorithm",
        choices=P1_ALGORITHMS,
        default=DEFAULT_P1_ALGORITHM,
        help="P1 path planning method to visualize.",
    )
    parser.add_argument("--from-node", help="Optional source node for plotting one pair only.")
    parser.add_argument("--to-node", help="Optional target node for plotting one pair only.")
    return parser.parse_args()


def load_data(algorithm):
    nodes = pd.read_csv(RAW_DATA_DIR / "nodes.csv")
    zones = pd.read_csv(RAW_DATA_DIR / "floor_zones.csv", skipinitialspace=True)
    obstacles = pd.read_csv(RAW_DATA_DIR / "floor_obstacles.csv", skipinitialspace=True)
    path_cost = pd.read_csv(p1_processed_dir(algorithm) / "path_cost.csv")
    return nodes, zones, obstacles, path_cost


def draw_rectangles(ax, rows, style_map, style_field, label_mode="center"):
    for item in rows.itertuples(index=False):
        item_type = getattr(item, style_field)
        style = style_map.get(item_type, {})
        rect = Rectangle(
            (float(item.x), float(item.y)),
            float(item.width),
            float(item.height),
            facecolor=style.get("facecolor", "#e5e7eb"),
            edgecolor=style.get("edgecolor", "#6b7280"),
            linewidth=1.6,
            alpha=style.get("alpha", 0.35),
            zorder=1,
        )
        ax.add_patch(rect)

        if label_mode == "none" or item_type == "wall":
            continue

        label_x = float(item.x) + float(item.width) / 2
        label_y = float(item.y) + float(item.height) / 2
        va = "center"
        color = "#111827"
        if label_mode == "obstacle":
            label_y = float(item.y) + float(item.height) + 0.08
            va = "bottom"
            color = style.get("edgecolor", "#111827")

        ax.text(label_x, label_y, item.label, fontsize=8, ha="center", va=va, color=color, zorder=2)


def draw_base_floor(ax, nodes, zones, obstacles):
    floor = Rectangle(
        (-0.8, -0.8),
        13.6,
        7.4,
        facecolor="#fbfbf7",
        edgecolor="#111827",
        linewidth=2.0,
        zorder=0,
    )
    ax.add_patch(floor)

    draw_rectangles(ax, zones, ZONE_STYLE, "zone_type")
    draw_rectangles(ax, obstacles, OBSTACLE_STYLE, "obstacle_type", label_mode="obstacle")

    for node_type, style in POINT_STYLE.items():
        group = nodes[nodes["node_type"] == node_type]
        if group.empty:
            continue
        alpha = 0.35 if node_type == "junction" else 0.95
        ax.scatter(
            group["x"],
            group["y"],
            s=style["size"],
            c=style["color"],
            marker=style["marker"],
            edgecolors="white",
            linewidths=1.2,
            alpha=alpha,
            zorder=5,
        )

    important_types = {"inbound", "outbound", "shelf", "sorting", "charger"}
    for node in nodes.itertuples(index=False):
        if node.node_type in important_types:
            ax.text(float(node.x), float(node.y) + 0.24, node.node_id, fontsize=8, ha="center", va="bottom", zorder=6)


def parse_path_geometry(path_geometry):
    if pd.isna(path_geometry) or not str(path_geometry).strip():
        return []

    points = []
    for token in str(path_geometry).split(";"):
        x_text, y_text = token.split(",")
        points.append((float(x_text), float(y_text)))
    return points


def draw_path(ax, points, color, label):
    if not points:
        return Line2D([0], [0], color=color, lw=4, label=label)

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    ax.plot(xs, ys, color=color, linewidth=3.5, alpha=0.9, zorder=8)

    if len(points) >= 2:
        arrow = FancyArrowPatch(
            points[-2],
            points[-1],
            arrowstyle="-|>",
            mutation_scale=18,
            linewidth=3.5,
            color=color,
            alpha=0.9,
            zorder=9,
        )
        ax.add_patch(arrow)

    ax.scatter([points[0][0]], [points[0][1]], s=220, facecolors="none", edgecolors="#111827", linewidths=2.2, zorder=10)
    ax.scatter([points[-1][0]], [points[-1][1]], s=220, marker="s", facecolors="none", edgecolors=color, linewidths=2.2, zorder=10)
    return Line2D([0], [0], color=color, lw=4, label=label)


def algorithm_label(algorithm):
    return {
        "basic_astar": "Basic Grid A*",
        "vg": "Visibility Graph",
        "avg": "Augmented Visibility Graph",
        "davg": "Dynamic Augmented Visibility Graph",
    }[algorithm]


def plot_pair_paths(nodes, zones, obstacles, pair_paths, output_path, algorithm):
    fig, ax = plt.subplots(figsize=(12.5, 7), dpi=160)
    draw_base_floor(ax, nodes, zones, obstacles)

    handles = []
    for idx, path in enumerate(pair_paths.itertuples(index=False)):
        points = parse_path_geometry(path.path_geometry)
        path_label = getattr(path, "path_uid", path.path_id)
        label = (
            f"{path_label}: cost={path.total_cost:g}, "
            f"time={path.travel_time:g}, {path.algorithm}"
        )
        handles.append(draw_path(ax, points, PATH_COLORS[idx % len(PATH_COLORS)], label))

    from_node = pair_paths.iloc[0]["from_node"]
    to_node = pair_paths.iloc[0]["to_node"]
    ax.set_title(f"{algorithm_label(algorithm)} Path: {from_node} to {to_node}", fontsize=15, pad=16)
    ax.set_xlabel("x coordinate")
    ax.set_ylabel("y coordinate")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.28)
    ax.set_xlim(-1.0, 13.0)
    ax.set_ylim(-1.0, 6.8)
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=1, fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def select_demo_pairs(path_cost, max_pairs=3, from_node=None, to_node=None):
    if bool(from_node) != bool(to_node):
        raise ValueError("Specify both --from-node and --to-node, or neither.")
    if from_node and to_node:
        available = set(zip(path_cost["from_node"], path_cost["to_node"]))
        pair = (from_node, to_node)
        if pair not in available:
            raise ValueError(f"No plotted path found for pair {from_node}->{to_node}.")
        return [pair]

    preferred_pairs = [
        ("IN1", "P5"),
        ("P5", "SORT1"),
        ("P8", "OUT2"),
        ("CHG2", "P3"),
    ]
    available = set(zip(path_cost["from_node"], path_cost["to_node"]))
    selected = [pair for pair in preferred_pairs if pair in available]

    if len(selected) < max_pairs:
        counts = (
            path_cost.groupby(["from_node", "to_node"])
            .size()
            .reset_index(name="path_count")
            .sort_values(["path_count", "from_node", "to_node"], ascending=[False, True, True])
        )
        for row in counts.itertuples(index=False):
            pair = (row.from_node, row.to_node)
            if pair not in selected:
                selected.append(pair)
            if len(selected) >= max_pairs:
                break

    return selected[:max_pairs]


def plot_demo_candidate_paths(nodes, zones, obstacles, path_cost, algorithm, from_node=None, to_node=None):
    path_output_dir = p1_output_dir(algorithm) / "paths"
    path_output_dir.mkdir(parents=True, exist_ok=True)
    selected_pairs = select_demo_pairs(path_cost, from_node=from_node, to_node=to_node)
    saved_paths = []

    for from_node, to_node in selected_pairs:
        pair_paths = (
            path_cost[(path_cost["from_node"] == from_node) & (path_cost["to_node"] == to_node)]
            .sort_values(["rank_by_total", "travel_time"])
            .head(3)
        )
        output_path = path_output_dir / f"candidate_paths_{from_node}_{to_node}.png"
        plot_pair_paths(nodes, zones, obstacles, pair_paths, output_path, algorithm)
        saved_paths.append(output_path)

    return saved_paths


def plot_cost_matrix(matrix_path, title, output_path):
    matrix = pd.read_csv(matrix_path, index_col=0)
    values = matrix.to_numpy(dtype=float)
    masked_values = np.ma.masked_invalid(values)

    cmap = plt.cm.YlOrRd.copy()
    cmap.set_bad(color="#f2f4f7")

    fig, ax = plt.subplots(figsize=(9.5, 8), dpi=160)
    image = ax.imshow(masked_values, cmap=cmap)
    ax.set_title(title, fontsize=14, pad=14)
    ax.set_xticks(range(len(matrix.columns)))
    ax.set_yticks(range(len(matrix.index)))
    ax.set_xticklabels(matrix.columns, rotation=45, ha="right", fontsize=8)
    ax.set_yticklabels(matrix.index, fontsize=8)

    for i in range(values.shape[0]):
        for j in range(values.shape[1]):
            value = values[i, j]
            if np.isfinite(value):
                ax.text(j, i, f"{value:g}", ha="center", va="center", fontsize=6.5)

    fig.colorbar(image, ax=ax, shrink=0.78, label="cost")
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def main():
    args = parse_args()
    algorithm = args.algorithm
    processed_data_dir = p1_processed_dir(algorithm)
    output_dir = p1_output_dir(algorithm)

    nodes, zones, obstacles, path_cost = load_data(algorithm)
    saved_path_plots = plot_demo_candidate_paths(
        nodes,
        zones,
        obstacles,
        path_cost,
        algorithm,
        from_node=args.from_node,
        to_node=args.to_node,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    matrix_time_path = processed_data_dir / "path_cost_matrix_time.csv"
    matrix_total_path = processed_data_dir / "path_cost_matrix_total.csv"
    time_output_path = output_dir / "cost_matrix_time.png"
    total_output_path = output_dir / "cost_matrix_total.png"

    plot_cost_matrix(matrix_time_path, f"{algorithm_label(algorithm)} Travel Time Matrix", time_output_path)
    plot_cost_matrix(matrix_total_path, f"{algorithm_label(algorithm)} Total Cost Matrix", total_output_path)

    # Visualization outputs:
    # - outputs/p1/<algorithm>/paths/candidate_paths_*.png overlays path geometry.
    # - outputs/p1/<algorithm>/cost_matrix_time.png visualizes the travel-time matrix.
    # - outputs/p1/<algorithm>/cost_matrix_total.png visualizes the total-cost matrix.
    for output_path in saved_path_plots:
        print(f"Saved: {output_path}")
    print(f"Saved: {time_output_path}")
    print(f"Saved: {total_output_path}")


if __name__ == "__main__":
    main()
