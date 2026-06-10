import argparse
import random
from dataclasses import dataclass
from math import hypot
from pathlib import Path

import matplotlib.animation as animation
import matplotlib.pyplot as plt
import pandas as pd

from planners.common import DEFAULT_AMR_SPEED, PROJECT_ROOT, RAW_DATA_DIR, inflate_obstacles
from planners.davg import (
    astar_dynamic_augmented_visibility_path,
    is_dynamic_rect,
    path_metrics as davg_path_metrics,
)
from plot_candidate_paths import draw_base_floor


OBSTACLE_COLUMNS = ["obstacle_id", "x", "y", "width", "height", "obstacle_type", "label"]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "p1" / "davg" / "replanning"


@dataclass
class PlannedPath:
    points: list
    metrics: dict
    used_full_graph: bool


@dataclass
class Snapshot:
    time: float
    position: tuple
    path: list
    trail: list
    dynamic_obstacles: list
    replan_count: int
    event_text: str
    metrics: dict


def parse_args():
    parser = argparse.ArgumentParser(description="Animate real-time DAVG replanning with random dynamic obstacles.")
    parser.add_argument("--from-node", default="IN1", help="AMR start node.")
    parser.add_argument("--to-node", default="OUT2", help="AMR goal node.")
    parser.add_argument("--duration", type=float, default=22.0, help="Simulation horizon in seconds.")
    parser.add_argument("--dt", type=float, default=0.5, help="Simulation time step in seconds.")
    parser.add_argument("--speed", type=float, default=DEFAULT_AMR_SPEED, help="AMR speed in world units per second.")
    parser.add_argument("--spawn-every", type=float, default=3.0, help="Seconds between random dynamic obstacle insertions.")
    parser.add_argument("--obstacle-ttl", type=float, default=7.0, help="Lifetime of each random dynamic obstacle.")
    parser.add_argument("--max-dynamic-obstacles", type=int, default=3, help="Maximum active random obstacles.")
    parser.add_argument("--seed", type=int, default=7, help="Random seed for reproducible demos.")
    parser.add_argument(
        "--keep-preset-dynamic-blocks",
        action="store_true",
        help="Keep dynamic_block rows already present in floor_obstacles.csv.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output GIF path. Defaults to outputs/p1/davg/replanning/davg_replanning_<from>_<to>.gif.",
    )
    return parser.parse_args()


def load_scene(keep_preset_dynamic_blocks=False):
    nodes = pd.read_csv(RAW_DATA_DIR / "nodes.csv")
    zones = pd.read_csv(RAW_DATA_DIR / "floor_zones.csv", skipinitialspace=True)
    obstacles = pd.read_csv(RAW_DATA_DIR / "floor_obstacles.csv", skipinitialspace=True)

    # The animation demonstrates obstacles appearing online. By default, remove
    # pre-authored dynamic blocks so all red blocks in the GIF are spawned during
    # the simulation rather than fixed before planning starts.
    if not keep_preset_dynamic_blocks:
        obstacles = obstacles[obstacles["obstacle_type"] != "dynamic_block"].copy()

    return nodes, zones, obstacles[OBSTACLE_COLUMNS].copy()


def node_position(nodes, node_id):
    rows = nodes[nodes["node_id"].astype(str) == str(node_id)]
    if rows.empty:
        available = ", ".join(nodes["node_id"].astype(str).tolist())
        raise ValueError(f"Unknown node '{node_id}'. Available nodes: {available}")

    row = rows.iloc[0]
    return float(row["x"]), float(row["y"])


def active_dynamic_dataframe(dynamic_obstacles):
    if not dynamic_obstacles:
        return pd.DataFrame(columns=OBSTACLE_COLUMNS)
    return pd.DataFrame(dynamic_obstacles)[OBSTACLE_COLUMNS]


def combine_obstacles(static_obstacles, dynamic_obstacles):
    dynamic_df = active_dynamic_dataframe(dynamic_obstacles)
    if dynamic_df.empty:
        return static_obstacles.copy()
    return pd.concat([static_obstacles, dynamic_df], ignore_index=True)


def plan_davg_path(start_point, goal_point, obstacles):
    """Plan with active-region DAVG and fall back to full graph if needed."""
    inflated_obstacles = inflate_obstacles(obstacles)
    dynamic_rects = [rect for rect in inflated_obstacles if is_dynamic_rect(rect)]

    points, _, _ = astar_dynamic_augmented_visibility_path(
        start_point,
        goal_point,
        inflated_obstacles,
        use_active_region=True,
    )
    used_full_graph = False

    # A dynamic obstacle can force a detour that lies just outside the active
    # corridor. Falling back keeps the demo robust while still showing DAVG's
    # active-region idea whenever possible.
    if not points:
        points, _, _ = astar_dynamic_augmented_visibility_path(
            start_point,
            goal_point,
            inflated_obstacles,
            use_active_region=False,
        )
        used_full_graph = True

    if not points:
        return None

    return PlannedPath(
        points=points,
        metrics=davg_path_metrics(points, dynamic_rects),
        used_full_graph=used_full_graph,
    )


def interpolate_along_path(points, distance):
    """Return the point reached after traveling distance along a polyline."""
    if not points:
        return None
    if len(points) == 1 or distance <= 0:
        return points[0]

    remaining = distance
    for left, right in zip(points[:-1], points[1:]):
        segment_length = hypot(right[0] - left[0], right[1] - left[1])
        if segment_length <= 1e-9:
            continue
        if remaining <= segment_length:
            ratio = remaining / segment_length
            return (
                left[0] + ratio * (right[0] - left[0]),
                left[1] + ratio * (right[1] - left[1]),
            )
        remaining -= segment_length

    return points[-1]


def advance_along_path(points, distance):
    """Advance along a polyline and return the new position plus remaining path."""
    if not points:
        return None, []
    if len(points) == 1 or distance <= 0:
        return points[0], list(points)

    remaining = distance
    for index, (left, right) in enumerate(zip(points[:-1], points[1:])):
        segment_length = hypot(right[0] - left[0], right[1] - left[1])
        if segment_length <= 1e-9:
            continue
        if remaining <= segment_length:
            ratio = remaining / segment_length
            position = (
                left[0] + ratio * (right[0] - left[0]),
                left[1] + ratio * (right[1] - left[1]),
            )
            return position, [position] + points[index + 1 :]
        remaining -= segment_length

    return points[-1], [points[-1]]


def path_length(points):
    return sum(hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points[:-1], points[1:]))


def point_inside_record_rect(point, rect, margin=0.0):
    x, y = point
    return (
        float(rect["x"]) - margin <= x <= float(rect["x"]) + float(rect["width"]) + margin
        and float(rect["y"]) - margin <= y <= float(rect["y"]) + float(rect["height"]) + margin
    )


def record_rects_overlap(left, right):
    return not (
        left["x"] + left["width"] < right["x"]
        or left["x"] > right["x"] + right["width"]
        or left["y"] + left["height"] < right["y"]
        or left["y"] > right["y"] + right["height"]
    )


def valid_dynamic_obstacle(candidate, current_point, goal_point, static_obstacles):
    """Reject spawned obstacles that would start on the AMR/goal or inside racks."""
    if point_inside_record_rect(current_point, candidate, margin=0.25):
        return False
    if point_inside_record_rect(goal_point, candidate, margin=0.25):
        return False

    for obstacle in static_obstacles.to_dict("records"):
        if obstacle["obstacle_type"] == "wall":
            continue
        if record_rects_overlap(candidate, obstacle):
            return False

    return True


def spawn_dynamic_obstacle(rng, path, current_point, goal_point, static_obstacles, now, index, ttl):
    """Place a temporary obstacle near the current planned route ahead of the AMR."""
    total_length = path_length(path)
    if total_length <= 1.5:
        return None

    # Try several positions ahead on the current path. This makes the obstacle
    # likely to matter, so the animation visibly triggers a replan.
    for _ in range(30):
        target_distance = min(
            total_length - 0.7,
            rng.uniform(1.2, min(3.2, max(1.3, total_length - 0.4))),
        )
        center = interpolate_along_path(path, target_distance)
        if center is None:
            continue

        width = rng.uniform(0.5, 0.8)
        height = rng.uniform(0.4, 0.7)
        jitter_x = rng.uniform(-0.18, 0.18)
        jitter_y = rng.uniform(-0.18, 0.18)
        x = min(max(center[0] - width / 2.0 + jitter_x, -0.45), 12.05 - width)
        y = min(max(center[1] - height / 2.0 + jitter_y, -0.45), 5.95 - height)

        candidate = {
            "obstacle_id": f"SIM_DYN_{index:02d}",
            "x": round(x, 3),
            "y": round(y, 3),
            "width": round(width, 3),
            "height": round(height, 3),
            "obstacle_type": "dynamic_block",
            "label": f"Live Block {index}",
            "spawn_time": now,
            "expire_time": now + ttl,
        }
        if valid_dynamic_obstacle(candidate, current_point, goal_point, static_obstacles):
            return candidate

    return None


def simulate_replanning(nodes, static_obstacles, args):
    rng = random.Random(args.seed)
    start_point = node_position(nodes, args.from_node)
    goal_point = node_position(nodes, args.to_node)
    position = start_point
    trail = [position]
    active_dynamic_obstacles = []
    snapshots = []
    event_rows = []
    planned = None
    replan_count = 0
    next_spawn_time = args.spawn_every
    obstacle_index = 1
    current_time = 0.0
    event_text = "Initial DAVG plan"

    while current_time <= args.duration:
        reached_goal = hypot(position[0] - goal_point[0], position[1] - goal_point[1]) <= args.speed * args.dt
        if reached_goal:
            position = goal_point
            trail.append(position)
            event_text = "Goal reached"

        expired = [
            obstacle
            for obstacle in active_dynamic_obstacles
            if obstacle["expire_time"] <= current_time
        ]
        if expired:
            active_dynamic_obstacles = [
                obstacle
                for obstacle in active_dynamic_obstacles
                if obstacle["expire_time"] > current_time
            ]
            planned = None
            event_text = f"Removed {len(expired)} expired obstacle(s)"

        if not reached_goal and current_time + 1e-9 >= next_spawn_time:
            if planned is not None and len(active_dynamic_obstacles) >= args.max_dynamic_obstacles:
                active_dynamic_obstacles.pop(0)

            if planned is not None:
                spawned = spawn_dynamic_obstacle(
                    rng,
                    planned.points,
                    position,
                    goal_point,
                    static_obstacles,
                    current_time,
                    obstacle_index,
                    args.obstacle_ttl,
                )
                if spawned is not None:
                    active_dynamic_obstacles.append(spawned)
                    obstacle_index += 1
                    planned = None
                    event_text = f"Added {spawned['obstacle_id']} and replanned"
            next_spawn_time += args.spawn_every

        if not reached_goal and planned is None:
            obstacles = combine_obstacles(static_obstacles, active_dynamic_obstacles)
            planned = plan_davg_path(position, goal_point, obstacles)
            replan_count += 1
            if planned is None:
                event_text = "No feasible DAVG path"
            elif planned.used_full_graph:
                event_text = f"Replan {replan_count}: full graph fallback"
            elif event_text == "Initial DAVG plan":
                event_text = f"Replan {replan_count}: active-region DAVG"

            event_rows.append(
                {
                    "time": round(current_time, 3),
                    "event": event_text,
                    "position_x": round(position[0], 3),
                    "position_y": round(position[1], 3),
                    "active_dynamic_obstacles": len(active_dynamic_obstacles),
                    "replan_count": replan_count,
                    "total_cost": planned.metrics["total_cost"] if planned else None,
                    "dynamic_cost": planned.metrics["dynamic_cost"] if planned else None,
                }
            )

        snapshots.append(
            Snapshot(
                time=round(current_time, 3),
                position=position,
                path=planned.points if planned else [],
                trail=list(trail),
                dynamic_obstacles=[dict(obstacle) for obstacle in active_dynamic_obstacles],
                replan_count=replan_count,
                event_text=event_text,
                metrics=planned.metrics if planned else {},
            )
        )

        if reached_goal or planned is None:
            break

        # Move along the latest planned route for one time step. At the next
        # frame, DAVG will reuse the path unless a dynamic obstacle appears or
        # disappears.
        step_distance = args.speed * args.dt
        position, remaining_points = advance_along_path(planned.points, step_distance)
        planned.points = remaining_points
        current_obstacles = combine_obstacles(static_obstacles, active_dynamic_obstacles)
        current_inflated = inflate_obstacles(current_obstacles)
        current_dynamic_rects = [rect for rect in current_inflated if is_dynamic_rect(rect)]
        planned.metrics = davg_path_metrics(remaining_points, current_dynamic_rects)
        trail.append(position)
        current_time += args.dt
        event_text = "Following current DAVG path"

    return snapshots, pd.DataFrame(event_rows)


def obstacles_for_snapshot(static_obstacles, snapshot):
    return combine_obstacles(static_obstacles, snapshot.dynamic_obstacles)


def draw_snapshot(ax, nodes, zones, static_obstacles, goal_point, snapshot):
    ax.clear()
    draw_base_floor(ax, nodes, zones, obstacles_for_snapshot(static_obstacles, snapshot))

    if snapshot.path:
        xs = [point[0] for point in snapshot.path]
        ys = [point[1] for point in snapshot.path]
        ax.plot(xs, ys, color="#2563eb", linewidth=3.0, linestyle="--", alpha=0.95, zorder=8, label="current DAVG plan")

    if snapshot.trail:
        trail_xs = [point[0] for point in snapshot.trail]
        trail_ys = [point[1] for point in snapshot.trail]
        ax.plot(trail_xs, trail_ys, color="#16a34a", linewidth=3.2, alpha=0.85, zorder=9, label="AMR trail")

    ax.scatter(
        [snapshot.position[0]],
        [snapshot.position[1]],
        s=260,
        c="#ef4444",
        edgecolors="white",
        linewidths=1.8,
        zorder=12,
        label="AMR",
    )
    ax.scatter(
        [goal_point[0]],
        [goal_point[1]],
        s=260,
        marker="*",
        c="#f59e0b",
        edgecolors="#111827",
        linewidths=1.0,
        zorder=11,
        label="goal",
    )

    metric_text = ""
    if snapshot.metrics:
        metric_text = (
            f"\ncost={snapshot.metrics['total_cost']:g}, "
            f"dynamic={snapshot.metrics['dynamic_cost']:g}"
        )
    ax.text(
        0.02,
        0.98,
        (
            f"t={snapshot.time:g}s | replans={snapshot.replan_count} | "
            f"live obstacles={len(snapshot.dynamic_obstacles)}"
            f"{metric_text}\n{snapshot.event_text}"
        ),
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.92},
        zorder=20,
    )

    ax.legend(loc="lower right", fontsize=8, frameon=True)


def render_animation(nodes, zones, static_obstacles, goal_point, snapshots, output_path, fps):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12.5, 7), dpi=140)

    def update(frame_index):
        draw_snapshot(ax, nodes, zones, static_obstacles, goal_point, snapshots[frame_index])
        return []

    anim = animation.FuncAnimation(fig, update, frames=len(snapshots), interval=1000 / fps, blit=False)
    writer = animation.PillowWriter(fps=fps)
    anim.save(output_path, writer=writer)
    plt.close(fig)


def main():
    args = parse_args()
    nodes, zones, static_obstacles = load_scene(
        keep_preset_dynamic_blocks=args.keep_preset_dynamic_blocks,
    )
    goal_point = node_position(nodes, args.to_node)
    snapshots, events = simulate_replanning(nodes, static_obstacles, args)

    output_path = args.output
    if output_path is None:
        output_path = DEFAULT_OUTPUT_DIR / f"davg_replanning_{args.from_node}_{args.to_node}.gif"
    if output_path.suffix.lower() != ".gif":
        output_path = output_path.with_suffix(".gif")

    fps = max(1, int(round(1.0 / args.dt)))
    render_animation(nodes, zones, static_obstacles, goal_point, snapshots, output_path, fps=fps)

    events_path = output_path.with_suffix(".events.csv")
    events.to_csv(events_path, index=False)

    print(f"Snapshots: {len(snapshots)}")
    print(f"Replans: {snapshots[-1].replan_count if snapshots else 0}")
    print(f"Saved: {output_path}")
    print(f"Saved: {events_path}")


if __name__ == "__main__":
    main()
