from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
PATH_OUTPUT_DIR = OUTPUT_DIR / "paths"

NODE_COLORS = {
    "inbound": "#2a9d8f",
    "outbound": "#e76f51",
    "shelf": "#f4a261",
    "sorting": "#457b9d",
    "charger": "#8d5a97",
    "junction": "#6c757d",
}

EDGE_COLORS = {
    "normal": "#b4bac3",
    "narrow": "#d8a141",
    "dynamic": "#c1121f",
    "oneway": "#2b6cb0",
}

PATH_COLORS = ["#e11d48", "#2563eb", "#16a34a"]


def load_data():
    nodes = pd.read_csv(RAW_DATA_DIR / "nodes.csv")
    edges = pd.read_csv(RAW_DATA_DIR / "edges.csv")
    path_cost = pd.read_csv(PROCESSED_DATA_DIR / "path_cost.csv")
    return nodes, edges, path_cost


def positions(nodes):
    return {row.node_id: (float(row.x), float(row.y)) for row in nodes.itertuples(index=False)}


def draw_base_network(ax, nodes, edges):
    pos = positions(nodes)

    for edge in edges.itertuples(index=False):
        x1, y1 = pos[edge.from_node]
        x2, y2 = pos[edge.to_node]
        color = EDGE_COLORS.get(edge.edge_type, "#b4bac3")
        linestyle = ":" if edge.edge_type == "dynamic" else "--" if edge.edge_type == "narrow" else "-"
        linewidth = 2.0 if edge.edge_type != "dynamic" else 2.5

        if int(edge.directed) == 1:
            arrow = FancyArrowPatch(
                (x1, y1),
                (x2, y2),
                arrowstyle="-|>",
                mutation_scale=12,
                linewidth=linewidth,
                linestyle=linestyle,
                color=color,
                alpha=0.65,
                shrinkA=12,
                shrinkB=12,
                zorder=1,
            )
            ax.add_patch(arrow)
        else:
            ax.plot([x1, x2], [y1, y2], color=color, linestyle=linestyle,
                    linewidth=linewidth, alpha=0.65, zorder=1)

    for node_type, color in NODE_COLORS.items():
        group = nodes[nodes["node_type"] == node_type]
        if group.empty:
            continue
        ax.scatter(group["x"], group["y"], s=95, c=color, edgecolors="white",
                   linewidths=1.2, zorder=3)

    for node in nodes.itertuples(index=False):
        ax.text(node.x, node.y + 0.22, node.node_id, fontsize=8,
                ha="center", va="bottom", zorder=4)


def draw_path(ax, path_nodes, pos, color, label):
    for from_node, to_node in zip(path_nodes[:-1], path_nodes[1:]):
        x1, y1 = pos[from_node]
        x2, y2 = pos[to_node]
        arrow = FancyArrowPatch(
            (x1, y1),
            (x2, y2),
            arrowstyle="-|>",
            mutation_scale=18,
            linewidth=4.0,
            color=color,
            alpha=0.88,
            shrinkA=14,
            shrinkB=14,
            zorder=6,
        )
        ax.add_patch(arrow)

    start = path_nodes[0]
    end = path_nodes[-1]
    ax.scatter([pos[start][0]], [pos[start][1]], s=220, facecolors="none",
               edgecolors="#111827", linewidths=2.2, zorder=7)
    ax.scatter([pos[end][0]], [pos[end][1]], s=220, marker="s", facecolors="none",
               edgecolors=color, linewidths=2.2, zorder=7)
    return Line2D([0], [0], color=color, lw=4, label=label)


def plot_pair_paths(nodes, edges, pair_paths, output_path):
    pos = positions(nodes)
    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=160)
    draw_base_network(ax, nodes, edges)

    handles = []
    for idx, path in enumerate(pair_paths.itertuples(index=False)):
        path_nodes = str(path.node_sequence).split("->")
        path_label = getattr(path, "path_uid", path.path_id)
        label = (
            f"{path_label}: cost={path.total_cost:g}, "
            f"time={path.travel_time:g}, {path.algorithm}"
        )
        handles.append(draw_path(ax, path_nodes, pos, PATH_COLORS[idx % len(PATH_COLORS)], label))

    from_node = pair_paths.iloc[0]["from_node"]
    to_node = pair_paths.iloc[0]["to_node"]
    ax.set_title(f"Candidate Paths: {from_node} to {to_node}", fontsize=15, pad=16)
    ax.set_xlabel("x coordinate")
    ax.set_ylabel("y coordinate")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.35)
    ax.set_xlim(-1, 13)
    ax.set_ylim(-1, 6.5)
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.08),
              ncol=1, fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def select_demo_pairs(path_cost, max_pairs=3):
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


def plot_demo_candidate_paths(nodes, edges, path_cost):
    PATH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    selected_pairs = select_demo_pairs(path_cost)
    saved_paths = []

    for from_node, to_node in selected_pairs:
        pair_paths = (
            path_cost[(path_cost["from_node"] == from_node) & (path_cost["to_node"] == to_node)]
            .sort_values(["rank_by_total", "travel_time"])
            .head(3)
        )
        output_path = PATH_OUTPUT_DIR / f"candidate_paths_{from_node}_{to_node}.png"
        plot_pair_paths(nodes, edges, pair_paths, output_path)
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
    nodes, edges, path_cost = load_data()
    saved_path_plots = plot_demo_candidate_paths(nodes, edges, path_cost)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    matrix_time_path = PROCESSED_DATA_DIR / "path_cost_matrix_time.csv"
    matrix_total_path = PROCESSED_DATA_DIR / "path_cost_matrix_total.csv"
    time_output_path = OUTPUT_DIR / "cost_matrix_time.png"
    total_output_path = OUTPUT_DIR / "cost_matrix_total.png"

    plot_cost_matrix(matrix_time_path, "Shortest Travel Time Matrix", time_output_path)
    plot_cost_matrix(matrix_total_path, "Minimum Total Cost Matrix", total_output_path)

    for output_path in saved_path_plots:
        print(f"Saved: {output_path}")
    print(f"Saved: {time_output_path}")
    print(f"Saved: {total_output_path}")


if __name__ == "__main__":
    main()
