from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "p0"


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


def load_data():
    nodes = pd.read_csv(DATA_DIR / "nodes.csv")
    amrs = pd.read_csv(DATA_DIR / "amrs.csv")
    tasks = pd.read_csv(DATA_DIR / "tasks.csv")
    zones = pd.read_csv(DATA_DIR / "floor_zones.csv", skipinitialspace=True)
    obstacles = pd.read_csv(DATA_DIR / "floor_obstacles.csv", skipinitialspace=True)
    return nodes, amrs, tasks, zones, obstacles


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

        ax.text(
            label_x,
            label_y,
            item.label,
            fontsize=8,
            ha="center",
            va=va,
            color=color,
            zorder=2,
        )


def plot_floor_plan(nodes, amrs, tasks, zones, obstacles):
    pos = {row.node_id: (float(row.x), float(row.y)) for row in nodes.itertuples(index=False)}
    fig, ax = plt.subplots(figsize=(12.5, 7), dpi=160)

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
            ax.text(
                float(node.x),
                float(node.y) + 0.24,
                node.node_id,
                fontsize=8,
                ha="center",
                va="bottom",
                zorder=6,
            )

    for amr in amrs.itertuples(index=False):
        x, y = pos[amr.init_node]
        ax.scatter(
            [x],
            [y - 0.34],
            s=180,
            marker="*",
            c="#111827",
            edgecolors="white",
            linewidths=1.0,
            zorder=7,
        )
        ax.text(x, y - 0.67, amr.amr_id, fontsize=8, ha="center", color="#111827", zorder=8)

    pickup_nodes = set(tasks["pickup"])
    delivery_nodes = set(tasks["delivery"])
    for node_id in pickup_nodes:
        if node_id in pos:
            x, y = pos[node_id]
            ax.scatter([x + 0.18], [y + 0.18], s=62, marker="^", c="#06b6d4", zorder=8)
    for node_id in delivery_nodes:
        if node_id in pos:
            x, y = pos[node_id]
            ax.scatter([x - 0.18], [y - 0.18], s=62, marker="v", c="#ef4444", zorder=8)

    legend_items = [
        Rectangle((0, 0), 1, 1, facecolor="#f4a261", edgecolor="#a65f12", alpha=0.72, label="rack obstacle"),
    ]
    if (obstacles["obstacle_type"] == "dynamic_block").any():
        legend_items.append(
            Rectangle((0, 0), 1, 1, facecolor="#f7b7bd", edgecolor="#c1121f", alpha=0.75, label="dynamic blocked area")
        )
    legend_items.extend([
        Line2D([0], [0], marker="o", color="w", label="task / shelf point",
               markerfacecolor="#b45309", markeredgecolor="white", markersize=8),
        Line2D([0], [0], marker="*", color="w", label="AMR initial pose",
               markerfacecolor="#111827", markersize=12),
        Line2D([0], [0], marker="^", color="w", label="pickup point",
               markerfacecolor="#06b6d4", markersize=8),
        Line2D([0], [0], marker="v", color="w", label="delivery point",
               markerfacecolor="#ef4444", markersize=8),
    ])
    ax.legend(
        handles=legend_items,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.08),
        ncol=3,
        fontsize=8,
        frameon=False,
    )

    ax.set_title("Simplified 2D AMR Warehouse Floor Plan", fontsize=15, pad=16)
    ax.set_xlabel("x coordinate")
    ax.set_ylabel("y coordinate")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.28)
    ax.set_xlim(-1.0, 13.0)
    ax.set_ylim(-1.0, 6.8)
    fig.tight_layout()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "warehouse_floor_plan.png"
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def main():
    nodes, amrs, tasks, zones, obstacles = load_data()
    output_path = plot_floor_plan(nodes, amrs, tasks, zones, obstacles)
    print(f"Saved 2D warehouse floor plan to: {output_path}")


if __name__ == "__main__":
    main()
