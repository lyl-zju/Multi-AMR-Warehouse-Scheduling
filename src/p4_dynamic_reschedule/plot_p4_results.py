"""Visualize P4 dynamic rescheduling outputs.

Run from the project root:
    python .\src\p4_dynamic_reschedule\plot_p4_results.py
    python .\src\p4_dynamic_reschedule\plot_p4_results.py --fps 8 --speedup 2

Outputs:
    outputs/p4/p4_gantt_events.png
    outputs/p4/p4_trajectory_map.png
    outputs/p4/p4_event_impact_metrics.png
    outputs/p4/p4_method_comparison.png
    outputs/p4/p4_weight_sensitivity.png
    outputs/p4/p4_dynamic_reschedule.gif
"""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, Rectangle


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
P4_DIR = PROJECT_ROOT / "data" / "processed" / "p4"
OUT_DIR = PROJECT_ROOT / "outputs" / "p4"

AMR_COLORS = {
    "AMR1": "#2563eb",
    "AMR2": "#16a34a",
    "AMR3": "#ca8a04",
    "AMR4": "#dc2626",
    "AMR5": "#7c3aed",
    "AMR6": "#0891b2",
}
FALLBACK_COLORS = ["#2563eb", "#16a34a", "#ca8a04", "#dc2626", "#7c3aed", "#0891b2"]

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
NODE_STYLE = {
    "inbound": {"color": "#2a9d8f", "marker": "s", "size": 90},
    "outbound": {"color": "#e76f51", "marker": "s", "size": 90},
    "shelf": {"color": "#b45309", "marker": "o", "size": 75},
    "sorting": {"color": "#457b9d", "marker": "D", "size": 85},
    "charger": {"color": "#8d5a97", "marker": "P", "size": 95},
    "junction": {"color": "#6c757d", "marker": ".", "size": 30},
}

ACTIVE_ALPHA = 0.88
FINISHED_ALPHA = 0.18
ACTIVE_TRAIL_ALPHA = 0.45
FINISHED_TRAIL_ALPHA = 0.12


plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
plt.rcParams["axes.unicode_minus"] = False


def read_csv(path, **kwargs):
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, **kwargs)


def read_optional_csv(path, **kwargs):
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, **kwargs)


def to_num(series):
    return pd.to_numeric(series, errors="coerce")


def amr_color(amr_id):
    if amr_id in AMR_COLORS:
        return AMR_COLORS[amr_id]
    idx = abs(hash(amr_id)) % len(FALLBACK_COLORS)
    return FALLBACK_COLORS[idx]


def load_inputs():
    return {
        "nodes": read_csv(RAW_DIR / "nodes.csv"),
        "zones": read_csv(RAW_DIR / "floor_zones.csv", skipinitialspace=True),
        "obstacles": read_csv(RAW_DIR / "floor_obstacles.csv", skipinitialspace=True),
        "amrs": read_csv(RAW_DIR / "amrs.csv"),
        "tasks": read_csv(RAW_DIR / "tasks.csv"),
        "events": read_csv(RAW_DIR / "dynamic_events.csv"),
        "reschedule": read_csv(P4_DIR / "reschedule_result.csv"),
        "impact": read_csv(P4_DIR / "dynamic_event_impact.csv"),
        "summary": read_csv(P4_DIR / "reschedule_summary.csv"),
        "trajectory": read_csv(P4_DIR / "trajectory_schedule.csv"),
        "method_comparison": read_optional_csv(P4_DIR / "p4_method_comparison.csv"),
        "weight_sensitivity": read_optional_csv(P4_DIR / "p4_weight_sensitivity.csv"),
    }


def build_task_attrs(tasks, events):
    attrs = {
        str(row.task_id): {
            "earliest_start": float(row.earliest_start),
            "latest_finish": float(row.latest_finish),
            "priority": int(row.priority),
        }
        for row in tasks.itertuples(index=False)
    }
    for event in events.itertuples(index=False):
        if str(event.event_type) != "new_task":
            continue
        release_time = float(event.release_time)
        attrs[str(event.target_id)] = {
            "earliest_start": release_time,
            "latest_finish": release_time + 30.0,
            "priority": int(event.priority),
        }
    return attrs


def summary_value(summary, metric, default=0.0):
    rows = summary[summary["metric"].astype(str) == metric]
    if rows.empty:
        return default
    return float(rows.iloc[0]["value"])


def draw_floor(ax, nodes, zones, obstacles):
    ax.add_patch(
        Rectangle(
            (-0.8, -0.8),
            13.6,
            7.4,
            facecolor="#fbfbf7",
            edgecolor="#111827",
            linewidth=1.8,
            zorder=0,
        )
    )

    for frame, style_map, field in (
        (zones, ZONE_STYLE, "zone_type"),
        (obstacles, OBSTACLE_STYLE, "obstacle_type"),
    ):
        for item in frame.itertuples(index=False):
            style = style_map.get(getattr(item, field), {})
            ax.add_patch(
                Rectangle(
                    (float(item.x), float(item.y)),
                    float(item.width),
                    float(item.height),
                    facecolor=style.get("facecolor", "#e5e7eb"),
                    edgecolor=style.get("edgecolor", "#6b7280"),
                    alpha=style.get("alpha", 0.35),
                    linewidth=1.2,
                    zorder=1,
                )
            )
            label = getattr(item, "label", "")
            if label and getattr(item, field) != "wall":
                ax.text(
                    float(item.x) + float(item.width) / 2,
                    float(item.y) + float(item.height) / 2,
                    label,
                    fontsize=7,
                    ha="center",
                    va="center",
                    color="#111827",
                    zorder=2,
                )

    for node_type, style in NODE_STYLE.items():
        group = nodes[nodes["node_type"].astype(str) == node_type]
        if group.empty:
            continue
        ax.scatter(
            group["x"],
            group["y"],
            s=style["size"],
            c=style["color"],
            marker=style["marker"],
            edgecolors="white",
            linewidths=1.0,
            alpha=0.4 if node_type == "junction" else 0.95,
            zorder=5,
        )

    important = {"inbound", "outbound", "shelf", "sorting", "charger"}
    for node in nodes.itertuples(index=False):
        if str(node.node_type) in important:
            ax.text(float(node.x), float(node.y) + 0.22, node.node_id, fontsize=7, ha="center", zorder=6)

    ax.set_xlim(-1.0, 13.0)
    ax.set_ylim(-1.0, 6.8)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.grid(True, linestyle="--", linewidth=0.45, alpha=0.25)


def add_time_events(ax, events, ymax):
    for event in events.itertuples(index=False):
        event_type = str(event.event_type)
        event_id = str(event.event_id)
        if pd.notna(event.start_time) and pd.notna(event.end_time):
            start = float(event.start_time)
            end = float(event.end_time)
            color = "#c1121f" if event_type == "area_block" else "#f59e0b"
            ax.axvspan(start, end, color=color, alpha=0.12, zorder=0)
            ax.text((start + end) / 2, ymax, event_id, ha="center", va="bottom", fontsize=8, color=color)
        elif pd.notna(event.release_time):
            release = float(event.release_time)
            ax.axvline(release, color="#2563eb", linestyle="--", linewidth=1.1, alpha=0.8, zorder=1)
            ax.text(release, ymax, event_id, ha="center", va="bottom", fontsize=8, color="#2563eb")


def plot_gantt_events(reschedule, tasks, events, summary, out_path):
    task_attrs = build_task_attrs(tasks, events)
    amrs = sorted(set(reschedule["new_amr"].dropna().astype(str)) | set(reschedule["old_amr"].dropna().astype(str)))
    amrs = [amr for amr in amrs if amr]
    ypos = {amr: idx for idx, amr in enumerate(amrs)}

    max_time = max(
        to_num(reschedule["new_finish"]).max(),
        to_num(reschedule["old_finish"]).max(),
        to_num(events["end_time"]).max(),
        to_num(events["release_time"]).max(),
    )
    max_time = float(max_time) + 6.0

    fig, axes = plt.subplots(2, 1, figsize=(13.5, 6.9), dpi=160, sharex=True)
    panels = [
        ("P3 baseline before dynamic insertion", "old_amr", "old_start", "old_finish"),
        ("P4 final rolling reschedule", "new_amr", "new_start", "new_finish"),
    ]

    for ax, (title, amr_col, start_col, finish_col) in zip(axes, panels):
        ax.set_title(title, loc="left", fontsize=11, pad=8)
        add_time_events(ax, events, len(amrs) - 0.1)
        for row in reschedule.itertuples(index=False):
            amr = str(getattr(row, amr_col))
            if not amr or amr == "nan":
                continue
            start = pd.to_numeric(getattr(row, start_col), errors="coerce")
            finish = pd.to_numeric(getattr(row, finish_col), errors="coerce")
            if pd.isna(start) or pd.isna(finish):
                continue
            task_id = str(row.task_id)
            y = ypos[amr]
            attr = task_attrs.get(task_id, {"latest_finish": np.inf, "priority": 1})
            is_late = float(finish) > float(attr["latest_finish"]) + 1e-9
            is_changed = str(getattr(row, "changed", "0")) == "1"
            edge = "#c1121f" if is_late else "#111827"
            lw = 2.2 if is_late or is_changed else 0.7
            hatch = "//" if is_changed else None
            ax.barh(
                y,
                float(finish) - float(start),
                left=float(start),
                height=0.62,
                color=amr_color(amr),
                edgecolor=edge,
                linewidth=lw,
                alpha=0.88 if not is_changed else 0.98,
                hatch=hatch,
                zorder=3,
            )
            x_text = float(start) + (float(finish) - float(start)) / 2
            ax.text(
                x_text,
                y,
                task_id,
                ha="center",
                va="center",
                fontsize=8,
                color="white",
                fontweight="bold" if is_changed else "normal",
                zorder=4,
            )
            if np.isfinite(attr["latest_finish"]):
                ax.plot([attr["latest_finish"], attr["latest_finish"]], [y - 0.38, y + 0.38], color="#7f1d1d", lw=0.8)

        ax.set_yticks(range(len(amrs)))
        ax.set_yticklabels(amrs)
        ax.invert_yaxis()
        ax.grid(axis="x", linestyle="--", linewidth=0.45, alpha=0.35)
        ax.set_xlim(0, max_time)

    axes[-1].set_xlabel("time")
    legend_items = [
        Rectangle((0, 0), 1, 1, color="#888", label="task interval"),
        Rectangle((0, 0), 1, 1, facecolor="#888", edgecolor="#c1121f", linewidth=2.0, label="late task"),
        Rectangle((0, 0), 1, 1, facecolor="#888", edgecolor="#111827", hatch="//", label="changed/new task"),
        Line2D([0], [0], color="#2563eb", linestyle="--", label="new task release"),
        Rectangle((0, 0), 1, 1, facecolor="#c1121f", alpha=0.12, label="area block window"),
    ]
    axes[0].legend(handles=legend_items, loc="lower left", bbox_to_anchor=(0.0, 1.16), ncol=5, frameon=False, fontsize=8)
    fig.suptitle(
        "P4 Gantt and dynamic-event timeline  "
        f"Cmax={summary_value(summary, 'Cmax'):.1f}, "
        f"late={summary_value(summary, 'late_count'):.0f}, "
        f"changed={summary_value(summary, 'ChangedTasks'):.0f}",
        fontsize=13,
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def plot_trajectory_map(nodes, zones, obstacles, amrs, events, reschedule, trajectory, summary, out_path):
    node_xy = {str(row.node_id): (float(row.x), float(row.y)) for row in nodes.itertuples(index=False)}
    fig, ax = plt.subplots(figsize=(13.5, 7.3), dpi=160)
    draw_floor(ax, nodes, zones, obstacles)

    for event in events.itertuples(index=False):
        if str(event.event_type) != "area_block":
            continue
        if pd.isna(event.x) or pd.isna(event.y):
            continue
        rect = Rectangle(
            (float(event.x), float(event.y)),
            float(event.width),
            float(event.height),
            facecolor="#ef4444",
            edgecolor="#7f1d1d",
            alpha=0.20,
            hatch="///",
            linewidth=1.8,
            zorder=7,
        )
        ax.add_patch(rect)
        ax.text(
            float(event.x) + float(event.width) / 2,
            float(event.y) + float(event.height) + 0.12,
            f"{event.event_id}: block {event.start_time}-{event.end_time}",
            ha="center",
            fontsize=8,
            color="#7f1d1d",
            zorder=8,
        )

    trajectory = trajectory.copy()
    trajectory["absolute_time"] = to_num(trajectory["absolute_time"])
    trajectory = trajectory.sort_values(["amr_id", "absolute_time", "sample_index"])

    for (amr_id, task_id, segment_type, path_uid), segment in trajectory.groupby(
        ["amr_id", "task_id", "segment_type", "path_uid"], sort=False
    ):
        color = amr_color(str(amr_id))
        segment = segment.sort_values("absolute_time")
        xs = segment["x"].to_numpy(dtype=float)
        ys = segment["y"].to_numpy(dtype=float)
        if len(xs) == 0:
            continue
        if "loaded" in str(segment_type):
            ax.plot(xs, ys, color=color, lw=2.3, alpha=0.82, zorder=9)
        elif "transition" in str(segment_type):
            ax.plot(xs, ys, color=color, lw=1.45, linestyle=(0, (4, 3)), alpha=0.50, zorder=8)
        else:
            ax.scatter(xs[:: max(1, len(xs) // 8)], ys[:: max(1, len(ys) // 8)], s=9, color=color, alpha=0.38, zorder=10)

    for row in amrs.itertuples(index=False):
        xy = node_xy.get(str(row.init_node))
        if xy is None:
            continue
        ax.scatter(*xy, marker="*", s=220, color=amr_color(str(row.amr_id)), edgecolor="#111827", linewidth=1.0, zorder=14)
        ax.text(xy[0], xy[1] - 0.32, str(row.amr_id), fontsize=8, ha="center", color="#111827", zorder=15)

    for row in reschedule.itertuples(index=False):
        amr_id = str(row.new_amr)
        pickup_xy = node_xy.get(str(row.pickup))
        if pickup_xy is None:
            continue
        color = amr_color(amr_id)
        changed = str(row.changed) == "1"
        marker = "*" if changed else "o"
        size = 230 if changed else 135
        ax.scatter(
            pickup_xy[0],
            pickup_xy[1],
            s=size,
            marker=marker,
            facecolor=color,
            edgecolor="#c1121f" if changed else "#111827",
            linewidth=1.8 if changed else 0.8,
            zorder=16,
        )
        label = f"{int(row.sequence_order)}:{row.task_id}"
        ax.text(
            pickup_xy[0],
            pickup_xy[1] + (0.36 if changed else -0.34),
            label,
            fontsize=8,
            ha="center",
            va="center",
            color="#111827",
            fontweight="bold" if changed else "normal",
            zorder=17,
            bbox=dict(boxstyle="round,pad=0.16", fc="white", ec=color, alpha=0.82, lw=0.7),
        )

    handles = [
        Line2D([0], [0], color=amr_color(amr_id), lw=2.6, label=f"{amr_id} loaded")
        for amr_id in sorted(reschedule["new_amr"].dropna().astype(str).unique())
    ]
    handles += [
        Line2D([0], [0], color="#555", lw=1.6, linestyle=(0, (4, 3)), label="transition"),
        Rectangle((0, 0), 1, 1, facecolor="#ef4444", alpha=0.20, hatch="///", label="area block"),
        Line2D([0], [0], marker="*", markersize=10, linestyle="None", color="#c1121f", label="new/changed task"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=8, framealpha=0.92)
    ax.set_title(
        "P4 final trajectory map  "
        f"conflicts={summary_value(summary, 'unresolved_trajectory_conflict_count'):.0f}, "
        f"block violations={summary_value(summary, 'blocked_area_violation_count'):.0f}, "
        f"delay violations={summary_value(summary, 'delayed_amr_violation_count'):.0f}",
        fontsize=13,
    )
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def plot_event_impact_metrics(events, impact, summary, out_path):
    fig = plt.figure(figsize=(13.5, 7.2), dpi=160)
    grid = fig.add_gridspec(2, 2, width_ratios=[1.35, 1.0], height_ratios=[1.0, 1.0], wspace=0.28, hspace=0.42)
    ax_events = fig.add_subplot(grid[:, 0])
    ax_feasible = fig.add_subplot(grid[0, 1])
    ax_objective = fig.add_subplot(grid[1, 1])

    impact_by_id = {str(row.event_id): row for row in impact.itertuples(index=False)}
    event_rows = list(events.itertuples(index=False))
    y_positions = np.arange(len(event_rows))
    time_points = []
    for event in event_rows:
        if pd.notna(event.start_time) and pd.notna(event.end_time):
            time_points.extend([float(event.start_time), float(event.end_time)])
        elif pd.notna(event.release_time):
            time_points.append(float(event.release_time))
    x_min = min(time_points) - 0.7 if time_points else 0.0
    x_max = max(time_points) + 0.7 if time_points else 1.0
    label_x = x_min + 0.03 * (x_max - x_min)

    for y, event in zip(y_positions, event_rows):
        event_id = str(event.event_id)
        event_type = str(event.event_type)
        color = {"area_block": "#c1121f", "amr_delay": "#f59e0b", "new_task": "#2563eb"}.get(event_type, "#6b7280")
        if pd.notna(event.start_time) and pd.notna(event.end_time):
            start = float(event.start_time)
            end = float(event.end_time)
            ax_events.barh(y, end - start, left=start, height=0.42, color=color, alpha=0.72)
            time_text = f"{start:g}-{end:g}"
        else:
            start = float(event.release_time)
            ax_events.scatter([start], [y], s=130, color=color, marker="D", zorder=4)
            ax_events.axvline(start, color=color, linestyle="--", alpha=0.22)
            time_text = f"release {start:g}"

        impact_row = impact_by_id.get(event_id)
        affected = "" if impact_row is None else str(impact_row.affected_tasks)
        count = 0 if impact_row is None else int(impact_row.impact_count)
        label = f"{event_id}  {event_type}  {time_text}  impact={count}"
        if affected and affected != "nan":
            label += f"  tasks={affected}"
        ax_events.text(
            label_x,
            y + 0.30,
            label,
            fontsize=8.3,
            va="center",
            color="#111827",
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.78),
            clip_on=True,
        )

    ax_events.set_yticks(y_positions)
    ax_events.set_yticklabels([str(row.event_id) for row in event_rows])
    ax_events.invert_yaxis()
    ax_events.set_xlim(x_min, x_max)
    ax_events.set_xlabel("time")
    ax_events.set_title("Dynamic event impact timeline", loc="left", fontsize=12)
    ax_events.grid(axis="x", linestyle="--", linewidth=0.45, alpha=0.35)

    feasibility = [
        ("changed", summary_value(summary, "ChangedTasks")),
        ("new tasks", summary_value(summary, "NewTasks")),
        ("conflicts", summary_value(summary, "unresolved_trajectory_conflict_count")),
        ("block viol.", summary_value(summary, "blocked_area_violation_count")),
        ("delay viol.", summary_value(summary, "delayed_amr_violation_count")),
        ("energy viol.", summary_value(summary, "energy_violation_count")),
    ]
    labels, values = zip(*feasibility)
    colors = ["#2563eb", "#2563eb"] + ["#16a34a" if value == 0 else "#c1121f" for value in values[2:]]
    ax_feasible.bar(labels, values, color=colors, alpha=0.86)
    ax_feasible.set_title("Feasibility checks", loc="left", fontsize=12)
    ax_feasible.tick_params(axis="x", rotation=30, labelsize=8)
    ax_feasible.grid(axis="y", linestyle="--", linewidth=0.45, alpha=0.35)
    for idx, value in enumerate(values):
        ax_feasible.text(idx, value + 0.05, f"{value:g}", ha="center", fontsize=8)

    objective = [
        ("late_count", summary_value(summary, "late_count")),
        ("priority_late", summary_value(summary, "priority_late_count")),
        ("total_delay", summary_value(summary, "total_delay")),
        ("Cmax", summary_value(summary, "Cmax")),
        ("F2 / 100", summary_value(summary, "F2") / 100.0),
        ("F3", summary_value(summary, "F3")),
    ]
    labels, values = zip(*objective)
    ax_objective.bar(labels, values, color=["#c1121f", "#c1121f", "#f59e0b", "#457b9d", "#8d5a97", "#2a9d8f"], alpha=0.84)
    ax_objective.set_title("Objective components", loc="left", fontsize=12)
    ax_objective.tick_params(axis="x", rotation=28, labelsize=8)
    ax_objective.grid(axis="y", linestyle="--", linewidth=0.45, alpha=0.35)
    for idx, value in enumerate(values):
        ax_objective.text(idx, value + max(values) * 0.015, f"{value:.1f}", ha="center", fontsize=8)

    fig.suptitle("P4 dynamic rescheduling impact and performance", fontsize=13, y=0.98)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def plot_method_comparison(method_comparison, out_path):
    if method_comparison.empty:
        return

    table = method_comparison.copy()
    display = pd.DataFrame({
        "方法": table["method"].astype(str),
        "新增延期": to_num(table["added_delay"]).map(lambda value: f"{value:.2f}"),
        "总延期": to_num(table["total_delay"]).map(lambda value: f"{value:.2f}"),
        "Cmax": to_num(table["Cmax"]).map(lambda value: f"{value:.2f}"),
        "F2": to_num(table["F2"]).map(lambda value: f"{value:.0f}"),
        "冲突消解成功率": to_num(table["conflict_resolution_success_rate"]).map(lambda value: f"{value * 100:.0f}%"),
    })

    fig = plt.figure(figsize=(13.5, 6.2), dpi=160)
    grid = fig.add_gridspec(2, 1, height_ratios=[1.0, 1.05], hspace=0.34)
    ax_table = fig.add_subplot(grid[0, 0])
    ax_bar = fig.add_subplot(grid[1, 0])
    ax_table.axis("off")
    ax_table.set_title("P4 dynamic rescheduling method comparison", loc="left", fontsize=13, pad=14)

    mpl_table = ax_table.table(
        cellText=display.values,
        colLabels=display.columns,
        cellLoc="center",
        colLoc="center",
        loc="center",
        colWidths=[0.20, 0.16, 0.16, 0.14, 0.16, 0.18],
    )
    mpl_table.auto_set_font_size(False)
    mpl_table.set_fontsize(10)
    mpl_table.scale(1.0, 1.55)

    for (row, col), cell in mpl_table.get_celld().items():
        cell.set_edgecolor("#d1d5db")
        cell.set_linewidth(0.75)
        if row == 0:
            cell.set_facecolor("#f3f4f6")
            cell.set_text_props(weight="bold", color="#111827")
        else:
            success = float(table.iloc[row - 1]["conflict_resolution_success_rate"])
            cell.set_facecolor("#f7fbf7" if success >= 1.0 else "#fff7ed")
            if col == 5:
                cell.set_text_props(color="#166534" if success >= 1.0 else "#c2410c", weight="bold")

    wait = table[table["method_id"].astype(str) == "wait_only"]
    rolling = table[table["method_id"].astype(str) == "rolling_horizon"]
    if not wait.empty and not rolling.empty:
        wait_row = wait.iloc[0]
        rolling_row = rolling.iloc[0]
        metrics = [
            ("总延期", "total_delay"),
            ("Cmax", "Cmax"),
            ("F2", "F2"),
            ("F3", "F3"),
        ]
        labels = [item[0] for item in metrics]
        improvements = []
        for _label, col in metrics:
            base = float(wait_row[col])
            value = float(rolling_row[col])
            improvements.append((base - value) / base * 100.0 if base else 0.0)
        colors = ["#16a34a" if value >= 0 else "#c1121f" for value in improvements]
        ax_bar.bar(labels, improvements, color=colors, alpha=0.86)
        ax_bar.axhline(0, color="#111827", linewidth=0.8)
        ax_bar.set_ylabel("improvement vs wait-only (%)")
        ax_bar.set_title("Rolling-horizon improvement over wait-only baseline", loc="left", fontsize=11)
        ax_bar.grid(axis="y", linestyle="--", linewidth=0.45, alpha=0.35)
        for idx, value in enumerate(improvements):
            va = "bottom" if value >= 0 else "top"
            y = value + (0.8 if value >= 0 else -0.8)
            ax_bar.text(idx, y, f"{value:.1f}%", ha="center", va=va, fontsize=9)

    ax_table.text(
        0.0,
        0.02,
        "Feasibility is the first gate; among feasible methods, lower total delay, Cmax and F2 indicate better rolling performance.",
        transform=ax_table.transAxes,
        fontsize=8.5,
        color="#4b5563",
        ha="left",
    )
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def plot_weight_sensitivity(weight_sensitivity, out_path):
    if weight_sensitivity.empty:
        return

    table = weight_sensitivity.copy()
    order = ["service_priority", "balanced", "stability_priority"]
    table["profile_id"] = table["profile_id"].astype(str)
    table = table.set_index("profile_id").reindex(order).dropna(how="all").reset_index()
    labels = table["profile"].astype(str).tolist()
    colors = ["#c1121f", "#2a9d8f", "#9aa0a6"][: len(table)]
    panels = [
        ("total_delay", "总延期", "{:.1f}"),
        ("priority_late_count", "加权延期任务数", "{:.0f}"),
        ("Cmax", "Cmax", "{:.1f}"),
        ("F2", "时间效率目标 F2", "{:.0f}"),
        ("F3", "运行成本目标 F3", "{:.0f}"),
    ]

    fig, axes = plt.subplots(1, len(panels), figsize=(16, 3.8), dpi=160)
    for ax, (col, title, fmt) in zip(axes, panels):
        values = to_num(table[col]).fillna(0.0).to_numpy(dtype=float)
        ax.bar(labels, values, color=colors, alpha=0.88)
        ax.set_title(title, fontsize=10.5)
        ax.tick_params(axis="x", rotation=20, labelsize=8)
        ax.grid(axis="y", linestyle="--", linewidth=0.45, alpha=0.35)
        ymax = max(values) if len(values) else 1.0
        offset = ymax * 0.025 if ymax else 0.1
        for idx, value in enumerate(values):
            ax.text(idx, value + offset, fmt.format(value), ha="center", va="bottom", fontsize=8.5)

    fig.suptitle(
        "P4 rolling-horizon objective-profile sensitivity  "
        "balanced profile keeps feasibility while minimizing the main time-efficiency indicators",
        fontsize=12.5,
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def build_amr_series(trajectory):
    series = {}
    for amr_id, group in trajectory.groupby("amr_id"):
        group = group.sort_values(["absolute_time", "sample_index"]).copy()
        collapsed = group.groupby("absolute_time", as_index=False).agg(
            {
                "x": "last",
                "y": "last",
                "footprint_radius": "last",
                "task_id": "last",
                "segment_type": "last",
            }
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
        return np.array([xs[0], ys[0]]), radius, str(group["task_id"].iloc[0]), "idle"
    if t >= times[-1]:
        return np.array([xs[-1], ys[-1]]), radius, str(group["task_id"].iloc[-1]), "done"

    x = float(np.interp(t, times, xs))
    y = float(np.interp(t, times, ys))
    idx = int(np.searchsorted(times, t, side="right") - 1)
    task_id = str(group["task_id"].iloc[idx])
    segment = str(group["segment_type"].iloc[idx])
    if "loaded" in segment:
        label = f"load {task_id}"
    elif "service" in segment or "wait" in segment:
        label = f"hold {task_id}"
    elif "transition" in segment:
        label = f"empty {task_id}"
    else:
        label = task_id
    return np.array([x, y]), radius, task_id, label


def event_label_at(events, t):
    labels = []
    for event in events.itertuples(index=False):
        event_type = str(event.event_type)
        event_id = str(event.event_id)
        if pd.notna(event.start_time) and pd.notna(event.end_time):
            start = float(event.start_time)
            end = float(event.end_time)
            if start <= t <= end:
                labels.append(f"{event_id} {event_type} active")
        elif pd.notna(event.release_time):
            release = float(event.release_time)
            if abs(t - release) <= 0.5:
                labels.append(f"{event_id} new task released")
            elif t > release:
                labels.append(f"{event_id} new task in plan")
    return "; ".join(labels)


def render_dynamic_reschedule_gif(nodes, zones, obstacles, events, reschedule, trajectory, summary, out_path, fps=8, speedup=2.0):
    amr_series = build_amr_series(trajectory)
    start_time = float(trajectory["absolute_time"].min())
    end_time = float(trajectory["absolute_time"].max())
    frame_dt = speedup / fps
    times = np.arange(start_time, end_time + frame_dt, frame_dt)
    node_xy = {str(row.node_id): (float(row.x), float(row.y)) for row in nodes.itertuples(index=False)}

    fig, ax = plt.subplots(figsize=(12.5, 7.2), dpi=120)
    draw_floor(ax, nodes, zones, obstacles)
    title = ax.set_title("", fontsize=12, pad=12)

    block_artists = []
    for event in events.itertuples(index=False):
        if str(event.event_type) != "area_block" or pd.isna(event.x) or pd.isna(event.y):
            continue
        rect = Rectangle(
            (float(event.x), float(event.y)),
            float(event.width),
            float(event.height),
            facecolor="#f7b7bd",
            edgecolor="#c1121f",
            linewidth=1.6,
            hatch="///",
            alpha=0.0,
            zorder=7,
        )
        ax.add_patch(rect)
        text = ax.text(
            float(event.x) + float(event.width) / 2,
            float(event.y) + float(event.height) + 0.10,
            "",
            fontsize=7.5,
            ha="center",
            color="#991b1b",
            zorder=8,
        )
        block_artists.append((event, rect, text))

    new_task_artists = []
    for event in events.itertuples(index=False):
        if str(event.event_type) != "new_task":
            continue
        pickup_xy = node_xy.get(str(event.pickup))
        if pickup_xy is None:
            continue
        marker = ax.scatter(
            [pickup_xy[0]],
            [pickup_xy[1]],
            marker="*",
            s=260,
            facecolor="#facc15",
            edgecolor="#c1121f",
            linewidth=1.8,
            alpha=0.0,
            zorder=16,
        )
        text = ax.text(
            pickup_xy[0],
            pickup_xy[1] + 0.42,
            "",
            fontsize=8,
            ha="center",
            color="#7f1d1d",
            weight="bold",
            zorder=17,
            bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="#c1121f", alpha=0.0),
        )
        new_task_artists.append((event, marker, text))

    delay_ring = Circle((0, 0), 0.55, facecolor="none", edgecolor="#f59e0b", linewidth=2.4, alpha=0.0, zorder=15)
    ax.add_patch(delay_ring)
    delay_text = ax.text(0, 0, "", fontsize=8, color="#92400e", ha="center", va="bottom", weight="bold", zorder=16)

    artists = {}
    for amr_id in sorted(amr_series):
        color = amr_color(amr_id)
        circle = Circle((0, 0), 0.25, facecolor=color, edgecolor="#111827", linewidth=1.0, alpha=ACTIVE_ALPHA, zorder=12)
        ax.add_patch(circle)
        label = ax.text(0, 0, amr_id, fontsize=7, color="white", ha="center", va="center", weight="bold", zorder=13)
        task_label = ax.text(0, 0, "", fontsize=6.7, color="#111827", ha="center", zorder=14)
        trail, = ax.plot([], [], color=color, linewidth=1.25, alpha=ACTIVE_TRAIL_ALPHA, zorder=6)
        artists[amr_id] = {
            "circle": circle,
            "label": label,
            "task_label": task_label,
            "trail": trail,
            "history": [],
            "last_xy": np.array([0.0, 0.0]),
        }

    legend_items = [
        Line2D([0], [0], marker="o", color="w", label="AMR footprint", markerfacecolor="#2563eb",
               markeredgecolor="#111827", markersize=9),
        Line2D([0], [0], color="#2563eb", linewidth=1.25, alpha=ACTIVE_TRAIL_ALPHA, label="AMR trail"),
        Rectangle((0, 0), 1, 1, facecolor="#f7b7bd", edgecolor="#c1121f", alpha=0.75, hatch="///",
                  label="active area block"),
        Line2D([0], [0], marker="o", color="w", label="AMR delay", markerfacecolor="none",
               markeredgecolor="#f59e0b", markersize=10),
        Line2D([0], [0], marker="*", color="w", label="released new task", markerfacecolor="#facc15",
               markeredgecolor="#c1121f", markersize=11),
    ]
    ax.legend(handles=legend_items, loc="upper center", bbox_to_anchor=(0.5, -0.06), ncol=5, fontsize=7, frameon=False)

    t13_rows = reschedule[reschedule["task_id"].astype(str) == "T13"]
    t13_text = ""
    if not t13_rows.empty:
        t13 = t13_rows.iloc[0]
        t13_text = f"T13 -> {t13['new_amr']} start {float(t13['new_start']):.1f}"

    def update(t):
        for event, rect, text in block_artists:
            start = float(event.start_time)
            end = float(event.end_time)
            if start <= t <= end:
                rect.set_alpha(0.75)
                text.set_text(f"{event.event_id} active")
                text.set_alpha(1.0)
            elif t > end:
                rect.set_alpha(0.18)
                text.set_text(f"{event.event_id} cleared")
                text.set_alpha(0.75)
            else:
                rect.set_alpha(0.0)
                text.set_text("")
                text.set_alpha(0.0)

        for event, marker, text in new_task_artists:
            release = float(event.release_time)
            if t >= release:
                marker.set_alpha(1.0)
                text.set_text(f"{event.event_id}: {event.target_id} released")
                text.get_bbox_patch().set_alpha(0.82)
            else:
                marker.set_alpha(0.0)
                text.set_text("")
                text.get_bbox_patch().set_alpha(0.0)

        active_delay = None
        for event in events.itertuples(index=False):
            if str(event.event_type) != "amr_delay":
                continue
            start = float(event.start_time)
            end = float(event.end_time)
            if start <= t <= end:
                active_delay = event
                break

        delay_ring.set_alpha(0.0)
        delay_text.set_text("")

        for amr_id, group in amr_series.items():
            xy, radius, _task_id, status = position_at(group, t)
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
            item["task_label"].set_text(status)
            item["task_label"].set_alpha(0.45 if finished else 1.0)
            item["history"].append(xy)
            item["last_xy"] = xy

            history = np.array(item["history"])
            item["trail"].set_data(history[:, 0], history[:, 1])
            item["trail"].set_alpha(trail_alpha)

            if active_delay is not None and str(active_delay.target_id) == amr_id:
                delay_ring.center = tuple(xy)
                delay_ring.radius = radius + 0.24
                delay_ring.set_alpha(1.0)
                delay_text.set_position((xy[0], xy[1] + radius + 0.46))
                delay_text.set_text(f"{active_delay.event_id} delay")

        event_text = event_label_at(events, t)
        if t13_text and t >= 26.0:
            event_text = f"{event_text}; {t13_text}" if event_text else t13_text
        title.set_text(
            "P4 dynamic rolling reschedule | "
            f"t = {t:.1f} / Cmax = {summary_value(summary, 'Cmax'):.1f} | "
            f"{event_text}"
        )
        return []

    animation = FuncAnimation(fig, update, frames=times, blit=False)
    animation.save(out_path, writer=PillowWriter(fps=fps))
    plt.close(fig)


def parse_args():
    parser = argparse.ArgumentParser(description="Plot and animate P4 dynamic rescheduling results.")
    parser.add_argument("--fps", type=int, default=8, help="GIF frames per second.")
    parser.add_argument("--speedup", type=float, default=2.0, help="Simulated time units per playback second.")
    return parser.parse_args()


def main():
    args = parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = load_inputs()

    outputs = [
        OUT_DIR / "p4_gantt_events.png",
        OUT_DIR / "p4_trajectory_map.png",
        OUT_DIR / "p4_event_impact_metrics.png",
        OUT_DIR / "p4_method_comparison.png",
        OUT_DIR / "p4_weight_sensitivity.png",
        OUT_DIR / "p4_dynamic_reschedule.gif",
    ]

    plot_gantt_events(
        data["reschedule"],
        data["tasks"],
        data["events"],
        data["summary"],
        outputs[0],
    )
    plot_trajectory_map(
        data["nodes"],
        data["zones"],
        data["obstacles"],
        data["amrs"],
        data["events"],
        data["reschedule"],
        data["trajectory"],
        data["summary"],
        outputs[1],
    )
    plot_event_impact_metrics(
        data["events"],
        data["impact"],
        data["summary"],
        outputs[2],
    )
    plot_method_comparison(
        data["method_comparison"],
        outputs[3],
    )
    plot_weight_sensitivity(
        data["weight_sensitivity"],
        outputs[4],
    )
    render_dynamic_reschedule_gif(
        data["nodes"],
        data["zones"],
        data["obstacles"],
        data["events"],
        data["reschedule"],
        data["trajectory"],
        data["summary"],
        outputs[5],
        fps=args.fps,
        speedup=args.speedup,
    )

    for output in outputs:
        print(f"Saved: {output}")


if __name__ == "__main__":
    main()
