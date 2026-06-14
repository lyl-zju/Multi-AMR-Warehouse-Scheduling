import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, Rectangle


P3_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = P3_DIR.parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "p3"

sys.path.insert(0, str(P3_DIR))
from schedule_and_detect_conflicts import (  # noqa: E402
    load_inputs,
    run_all_modes,
)


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
    "inbound": {"color": "#2a9d8f", "marker": "s", "size": 90},
    "outbound": {"color": "#e76f51", "marker": "s", "size": 90},
    "shelf": {"color": "#b45309", "marker": "o", "size": 75},
    "sorting": {"color": "#457b9d", "marker": "D", "size": 85},
    "charger": {"color": "#8d5a97", "marker": "P", "size": 95},
    "junction": {"color": "#6c757d", "marker": ".", "size": 30},
}

AMR_COLORS = {
    "AMR1": "#2563eb",
    "AMR2": "#16a34a",
    "AMR3": "#ca8a04",
    "AMR4": "#dc2626",
    "AMR5": "#7c3aed",
    "AMR6": "#0891b2",
}

ACTIVE_ALPHA = 0.88
FINISHED_ALPHA = 0.18
ACTIVE_TRAIL_ALPHA = 0.45
FINISHED_TRAIL_ALPHA = 0.12


def load_floor_data():
    nodes = pd.read_csv(RAW_DATA_DIR / "nodes.csv")
    zones = pd.read_csv(RAW_DATA_DIR / "floor_zones.csv", skipinitialspace=True)
    obstacles = pd.read_csv(RAW_DATA_DIR / "floor_obstacles.csv", skipinitialspace=True)
    return nodes, zones, obstacles


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
            linewidth=1.4,
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
        ax.text(label_x, label_y, item.label, fontsize=7, ha="center", va=va, color=color, zorder=2)


def draw_floor(ax, nodes, zones, obstacles):
    floor = Rectangle(
        (-0.8, -0.8),
        13.6,
        7.4,
        facecolor="#fbfbf7",
        edgecolor="#111827",
        linewidth=1.8,
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
            linewidths=1.0,
            alpha=alpha,
            zorder=5,
        )

    important_types = {"inbound", "outbound", "shelf", "sorting", "charger"}
    for node in nodes.itertuples(index=False):
        if node.node_type in important_types:
            ax.text(float(node.x), float(node.y) + 0.22, node.node_id, fontsize=7, ha="center", zorder=6)

    legend_items = [
        Line2D([0], [0], marker="o", color="w", label="AMR footprint",
               markerfacecolor="#2563eb", markeredgecolor="#111827", markersize=9),
        Line2D([0], [0], marker="o", color="w", label="conflict",
               markerfacecolor="none", markeredgecolor="#ef4444", markersize=10),
        Rectangle((0, 0), 1, 1, facecolor="#f4a261", edgecolor="#a65f12", alpha=0.72, label="rack"),
        Rectangle((0, 0), 1, 1, facecolor="#f7b7bd", edgecolor="#c1121f", alpha=0.75, label="dynamic block"),
    ]
    ax.legend(handles=legend_items, loc="upper center", bbox_to_anchor=(0.5, -0.06), ncol=4, fontsize=7, frameon=False)
    ax.set_xlim(-1.0, 13.0)
    ax.set_ylim(-1.0, 6.8)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle="--", linewidth=0.45, alpha=0.24)
    ax.set_xlabel("x")
    ax.set_ylabel("y")


def build_amr_series(trajectory_schedule):
    series = {}
    for amr_id, group in trajectory_schedule.groupby("amr_id"):
        group = group.sort_values(["absolute_time", "sample_index"]).copy()
        collapsed = group.groupby("absolute_time", as_index=False).agg(
            {"x": "last", "y": "last", "footprint_radius": "last", "task_id": "last"}
        )
        series[str(amr_id)] = collapsed
    return series


def is_finished(group, t):
    return t > float(group["absolute_time"].max()) + 1e-9


def position_at(group, t):
    times = group["absolute_time"].to_numpy(dtype=float)
    xs = group["x"].to_numpy(dtype=float)
    ys = group["y"].to_numpy(dtype=float)
    radius = float(group["footprint_radius"].iloc[0])
    if t <= times[0]:
        return np.array([xs[0], ys[0]]), radius, str(group["task_id"].iloc[0])
    if t >= times[-1]:
        return np.array([xs[-1], ys[-1]]), radius, str(group["task_id"].iloc[-1])
    x = float(np.interp(t, times, xs))
    y = float(np.interp(t, times, ys))
    idx = int(np.searchsorted(times, t, side="right") - 1)
    return np.array([x, y]), radius, str(group["task_id"].iloc[idx])


def parse_location(value):
    x_text, y_text = str(value).split(",")
    return float(x_text), float(y_text)


def active_conflicts(conflict_log, t, tolerance=0.35):
    if conflict_log.empty:
        return []
    rows = []
    for conflict in conflict_log.itertuples(index=False):
        conflict_time = float(conflict.time)
        if abs(conflict_time - t) <= tolerance:
            rows.append(conflict)
    return rows


def render_animation(name, trajectory_schedule, conflict_log, fps, speedup, output_stem=None):
    nodes, zones, obstacles = load_floor_data()
    amr_series = build_amr_series(trajectory_schedule)
    start_time = float(trajectory_schedule["absolute_time"].min())
    end_time = float(trajectory_schedule["absolute_time"].max())
    frame_dt = speedup / fps
    times = np.arange(start_time, end_time + frame_dt, frame_dt)

    fig, ax = plt.subplots(figsize=(12.5, 7.2), dpi=120)
    draw_floor(ax, nodes, zones, obstacles)
    title = ax.set_title("", fontsize=12, pad=12)

    artists = {}
    for amr_id in sorted(amr_series):
        color = AMR_COLORS.get(amr_id, "#334155")
        circle = Circle((0, 0), 0.25, facecolor=color, edgecolor="#111827", linewidth=1.0, alpha=ACTIVE_ALPHA, zorder=9)
        ax.add_patch(circle)
        label = ax.text(0, 0, amr_id, fontsize=7, color="white", ha="center", va="center", weight="bold", zorder=10)
        task_label = ax.text(0, 0, "", fontsize=6.5, color="#111827", ha="center", zorder=10)
        trail, = ax.plot([], [], color=color, linewidth=1.25, alpha=ACTIVE_TRAIL_ALPHA, zorder=3)
        artists[amr_id] = {"circle": circle, "label": label, "task_label": task_label, "trail": trail, "history": []}

    conflict_marker = Circle((0, 0), 0.55, facecolor="none", edgecolor="#ef4444", linewidth=2.4, alpha=0.0, zorder=11)
    ax.add_patch(conflict_marker)
    conflict_text = ax.text(0, 0, "", fontsize=8, color="#b91c1c", ha="center", va="bottom", weight="bold", zorder=12)

    def update(t):
        for amr_id, group in amr_series.items():
            xy, radius, task_id = position_at(group, t)
            item = artists[amr_id]
            finished = is_finished(group, t)
            alpha = FINISHED_ALPHA if finished else ACTIVE_ALPHA
            trail_alpha = FINISHED_TRAIL_ALPHA if finished else ACTIVE_TRAIL_ALPHA
            item["circle"].center = tuple(xy)
            item["circle"].radius = radius
            item["circle"].set_alpha(alpha)
            item["label"].set_position(xy)
            item["label"].set_alpha(alpha)
            item["task_label"].set_position((xy[0], xy[1] + radius + 0.12))
            item["task_label"].set_text("done" if finished else task_id)
            item["task_label"].set_alpha(0.45 if finished else 1.0)
            item["history"].append(xy)
            history = np.array(item["history"])
            item["trail"].set_data(history[:, 0], history[:, 1])
            item["trail"].set_alpha(trail_alpha)

        conflicts = active_conflicts(conflict_log, t)
        if conflicts:
            x, y = parse_location(conflicts[0].location)
            conflict_marker.center = (x, y)
            conflict_marker.set_alpha(1.0)
            conflict_text.set_position((x, y + 0.7))
            conflict_text.set_text(f"{conflicts[0].conflict_id}")
        else:
            conflict_marker.set_alpha(0.0)
            conflict_text.set_text("")

        title.set_text(f"P3 {name} | t = {t:.1f} | conflicts = {len(conflict_log)}")
        return []

    animation = FuncAnimation(fig, update, frames=times, blit=False)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{output_stem or ("p3_" + name)}.gif"
    animation.save(output_path, writer=PillowWriter(fps=fps))
    plt.close(fig)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Animate P3 original, wait, wait+reroute, and wait+reroute+resequence results.")
    parser.add_argument("--p1-algorithm", default="mixed", choices=("basic_astar", "vg", "avg", "davg", "mixed"))
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--speedup", type=float, default=2.0)
    args = parser.parse_args()

    assignment, trajectory_samples, path_cost = load_inputs(args.p1_algorithm)
    original, wait, wait_reroute, wait_reroute_resequence, selected = run_all_modes(assignment, trajectory_samples, path_cost)

    original_path = render_animation("original", original["trajectory"], original["conflicts"], args.fps, args.speedup, "p3_original")
    wait_path = render_animation("wait_repair", wait["trajectory"], wait["conflicts"], args.fps, args.speedup, "p3_wait_repair")
    reroute_path = render_animation("wait_reroute_repair", wait_reroute["trajectory"], wait_reroute["conflicts"], args.fps, args.speedup, "p3_wait_reroute_repair")
    resequence_path = render_animation("wait_reroute_resequence_repair", wait_reroute_resequence["trajectory"], wait_reroute_resequence["conflicts"], args.fps, args.speedup, "p3_wait_reroute_resequence_repair")

    print(f"Original conflicts: {len(original['conflicts'])}")
    print(f"Wait repair conflicts: {len(wait['conflicts'])}")
    print(f"Wait+reroute repair conflicts: {len(wait_reroute['conflicts'])}")
    print(f"Wait+reroute+resequence repair conflicts: {len(wait_reroute_resequence['conflicts'])}")
    print(f"Selected P3 mode: {selected['mode']}")
    print(f"Saved: {original_path}")
    print(f"Saved: {wait_path}")
    print(f"Saved: {reroute_path}")
    print(f"Saved: {resequence_path}")


if __name__ == "__main__":
    main()

