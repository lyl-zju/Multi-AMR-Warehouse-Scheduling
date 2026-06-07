from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "outputs"


NODE_STYLE = {
    "inbound": {"color": "#2a9d8f", "marker": "s", "size": 140},
    "outbound": {"color": "#e76f51", "marker": "s", "size": 140},
    "shelf": {"color": "#f4a261", "marker": "o", "size": 120},
    "sorting": {"color": "#457b9d", "marker": "D", "size": 130},
    "charger": {"color": "#8d5a97", "marker": "P", "size": 150},
    "junction": {"color": "#6c757d", "marker": "o", "size": 90},
}

EDGE_STYLE = {
    "normal": {"color": "#8a8f98", "linestyle": "-", "linewidth": 2.0},
    "narrow": {"color": "#d08c28", "linestyle": "--", "linewidth": 2.2},
    "dynamic": {"color": "#c1121f", "linestyle": ":", "linewidth": 2.8},
    "oneway": {"color": "#2b6cb0", "linestyle": "-", "linewidth": 2.3},
}


def load_data():
    nodes = pd.read_csv(DATA_DIR / "nodes.csv")
    edges = pd.read_csv(DATA_DIR / "edges.csv")
    amrs = pd.read_csv(DATA_DIR / "amrs.csv")
    tasks = pd.read_csv(DATA_DIR / "tasks.csv")
    return nodes, edges, amrs, tasks


def draw_edge(ax, x1, y1, x2, y2, style, directed):
    if directed:
        arrow = FancyArrowPatch(
            (x1, y1),
            (x2, y2),
            arrowstyle="-|>",
            mutation_scale=14,
            linewidth=style["linewidth"],
            linestyle=style["linestyle"],
            color=style["color"],
            shrinkA=12,
            shrinkB=12,
            zorder=1,
        )
        ax.add_patch(arrow)
    else:
        ax.plot(
            [x1, x2],
            [y1, y2],
            color=style["color"],
            linestyle=style["linestyle"],
            linewidth=style["linewidth"],
            zorder=1,
        )


def plot_network(nodes, edges, amrs, tasks):
    pos = {row.node_id: (row.x, row.y) for row in nodes.itertuples()}
    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=160)

    for edge in edges.itertuples():
        x1, y1 = pos[edge.from_node]
        x2, y2 = pos[edge.to_node]
        style = EDGE_STYLE.get(edge.edge_type, EDGE_STYLE["normal"])
        draw_edge(ax, x1, y1, x2, y2, style, bool(edge.directed))
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx, my + 0.1, edge.edge_id, fontsize=7, color="#4b5563", ha="center")

    for node_type, style in NODE_STYLE.items():
        group = nodes[nodes["node_type"] == node_type]
        if group.empty:
            continue
        ax.scatter(
            group["x"],
            group["y"],
            s=style["size"],
            c=style["color"],
            marker=style["marker"],
            edgecolors="white",
            linewidths=1.5,
            zorder=3,
        )

    for node in nodes.itertuples():
        ax.text(node.x, node.y + 0.25, node.node_id, fontsize=8, ha="center", va="bottom")

    for idx, amr in enumerate(amrs.itertuples()):
        x, y = pos[amr.init_node]
        ax.scatter(
            [x],
            [y - 0.32],
            s=180,
            marker="*",
            c="#111827",
            edgecolors="white",
            linewidths=1.0,
            zorder=4,
        )
        ax.text(x, y - 0.65, amr.amr_id, fontsize=8, ha="center", color="#111827")

    pickup_nodes = set(tasks["pickup"])
    delivery_nodes = set(tasks["delivery"])
    for node_id in pickup_nodes:
        x, y = pos[node_id]
        ax.scatter([x + 0.18], [y + 0.18], s=60, marker="^", c="#06b6d4", zorder=5)
    for node_id in delivery_nodes:
        x, y = pos[node_id]
        ax.scatter([x - 0.18], [y - 0.18], s=60, marker="v", c="#ef4444", zorder=5)

    node_legend = [
        Line2D([0], [0], marker=style["marker"], color="w", label=node_type,
               markerfacecolor=style["color"], markeredgecolor="white", markersize=9)
        for node_type, style in NODE_STYLE.items()
    ]
    edge_legend = [
        Line2D([0], [0], color=style["color"], lw=style["linewidth"],
               linestyle=style["linestyle"], label=f"{edge_type} edge")
        for edge_type, style in EDGE_STYLE.items()
    ]
    extra_legend = [
        Line2D([0], [0], marker="*", color="w", label="AMR initial node",
               markerfacecolor="#111827", markersize=12),
        Line2D([0], [0], marker="^", color="w", label="task pickup",
               markerfacecolor="#06b6d4", markersize=8),
        Line2D([0], [0], marker="v", color="w", label="task delivery",
               markerfacecolor="#ef4444", markersize=8),
    ]

    ax.legend(handles=node_legend + edge_legend + extra_legend, loc="upper center",
              bbox_to_anchor=(0.5, -0.08), ncol=5, fontsize=8, frameon=False)
    ax.set_title("Simplified AMR Warehouse Road Network", fontsize=15, pad=16)
    ax.set_xlabel("x coordinate")
    ax.set_ylabel("y coordinate")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.35)
    ax.set_xlim(-1, 13)
    ax.set_ylim(-1, 6.5)
    fig.tight_layout()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "warehouse_network.png"
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def main():
    nodes, edges, amrs, tasks = load_data()
    output_path = plot_network(nodes, edges, amrs, tasks)
    print(f"Saved warehouse network plot to: {output_path}")


if __name__ == "__main__":
    main()
