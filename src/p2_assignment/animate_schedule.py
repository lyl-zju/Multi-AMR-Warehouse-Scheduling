"""P2 调度执行动画：AMR footprint 圆点沿任务链轨迹随时间移动，输出 GIF。

数据来源：P2 的求解结果 + P1 的 path_trajectory_samples.csv。
每台 AMR 按其任务链依次播放 空驶轨迹 -> 取货等待/服务 -> 载货轨迹，
时间轴使用 P2 估算的 estimated 时间（与甘特图一致）。

用法（项目根目录，建议先 build_mixed_paths.py）：
    python .\\src\\p2_assignment\\animate_schedule.py
    python .\\src\\p2_assignment\\animate_schedule.py --method m3 --p1-algorithm mixed --fps 12

输出：
    outputs/p2/schedule_animation_<method>.gif
"""

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.patches import Circle

P2_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(P2_DIR))

from assign_and_sequence_tasks import run, SOLVER_LABELS, p1_path_cost_csv  # noqa: E402
from plot_assignment_results import draw_floor, amr_color, _node_xy  # noqa: E402

PROJECT_ROOT = P2_DIR.parents[1]
OUT_DIR = PROJECT_ROOT / "outputs" / "p2"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
plt.rcParams["axes.unicode_minus"] = False

TIME_STEP = 0.25       # 仿真时间步长
FOOTPRINT_FALLBACK = 0.25


def load_trajectories(p1_algorithm):
    csv = p1_path_cost_csv(p1_algorithm).parent / "path_trajectory_samples.csv"
    traj = pd.read_csv(csv)
    by_uid = {}
    for uid, g in traj.groupby("path_uid"):
        g = g.sort_values("offset_time")
        by_uid[uid] = (g["offset_time"].to_numpy(),
                       g[["x", "y"]].to_numpy(),
                       float(g["footprint_radius"].iloc[0]))
    return by_uid


def build_motion(data, evaluation, traj_by_uid):
    """每台 AMR 生成 position(t) 采样函数所需的分段轨迹表。

    段类型：move（带轨迹几何，按段时长拉伸轨迹时间）/ hold（停在某点）。
    """
    motions = {}
    for amr_id in data.amr_ids:
        amr = data.amrs[amr_id]
        segs = []
        t_cursor, pos = 0.0, np.array(_node_xy(amr.init_node), dtype=float)
        for leg in evaluation.timelines[amr_id].legs:
            task = data.tasks[leg.task_id]
            # 空驶
            seg = _move_seg(leg.trans_path, t_cursor, leg.trans_time,
                            pos, _node_xy(task.pickup), traj_by_uid)
            segs.append(seg)
            t_cursor += leg.trans_time
            pos = seg["pts"][-1]
            # 等待 + 服务（停在取货点）
            hold = leg.waiting + task.service_time
            if hold > 1e-9:
                segs.append({"type": "hold", "t0": t_cursor, "t1": t_cursor + hold,
                             "pts": np.array([pos]), "task": leg.task_id})
                t_cursor += hold
            # 载货
            seg = _move_seg(leg.loaded_path, t_cursor, leg.loaded_time,
                            pos, _node_xy(task.delivery), traj_by_uid)
            seg["task"] = leg.task_id
            segs.append(seg)
            t_cursor += leg.loaded_time
            pos = seg["pts"][-1]
        motions[amr_id] = segs
    return motions


def _move_seg(path, t0, duration, fallback_from, fallback_to, traj_by_uid):
    uid = path["path_uid"] if path else None
    if uid in traj_by_uid:
        offsets, pts, radius = traj_by_uid[uid]
        # P1 轨迹是基准速度时间，按 P2 实际段时长缩放
        scale = duration / offsets[-1] if offsets[-1] > 0 else 1.0
        return {"type": "move", "t0": t0, "t1": t0 + duration,
                "offsets": offsets * scale, "pts": pts, "radius": radius}
    pts = np.array([fallback_from, fallback_to], dtype=float)
    return {"type": "move", "t0": t0, "t1": t0 + duration,
            "offsets": np.array([0.0, max(duration, 1e-9)]), "pts": pts,
            "radius": FOOTPRINT_FALLBACK}


def position_at(segs, t):
    """返回 (xy, radius, 状态文本)。"""
    if not segs:
        return None, FOOTPRINT_FALLBACK, ""
    if t <= segs[0]["t0"]:
        return segs[0]["pts"][0], segs[0].get("radius", FOOTPRINT_FALLBACK), "待命"
    for seg in segs:
        if t > seg["t1"]:
            continue
        if seg["type"] == "hold":
            return seg["pts"][0], FOOTPRINT_FALLBACK, f"服务 {seg.get('task', '')}"
        rel = t - seg["t0"]
        xy = np.array([np.interp(rel, seg["offsets"], seg["pts"][:, 0]),
                       np.interp(rel, seg["offsets"], seg["pts"][:, 1])])
        label = f"载货 {seg['task']}" if "task" in seg else "空驶"
        return xy, seg.get("radius", FOOTPRINT_FALLBACK), label
    return segs[-1]["pts"][-1], FOOTPRINT_FALLBACK, "完成"


def main():
    parser = argparse.ArgumentParser(description="P2 调度执行动画")
    parser.add_argument("--method", default="m3")
    parser.add_argument("--p1-algorithm", default="mixed")
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--speedup", type=float, default=2.0,
                        help="每秒动画播放多少单位仿真时间")
    args = parser.parse_args()

    print(f"求解 {args.method} ({args.p1_algorithm}) ...")
    data, solution, evaluation, _ = run(
        method=args.method, p1_algorithm=args.p1_algorithm, write_outputs=False)
    traj_by_uid = load_trajectories(args.p1_algorithm)
    motions = build_motion(data, evaluation, traj_by_uid)
    cmax = evaluation.metrics["Cmax"]

    fig, ax = plt.subplots(figsize=(12.5, 7), dpi=110)
    draw_floor(ax)
    title = ax.set_title("", fontsize=12)
    artists = {}
    for amr_id in data.amr_ids:
        color = amr_color(data, amr_id)
        circle = Circle((0, 0), FOOTPRINT_FALLBACK, facecolor=color,
                        edgecolor="black", lw=1.0, alpha=0.9, zorder=8)
        ax.add_patch(circle)
        text = ax.text(0, 0, amr_id, fontsize=7.5, ha="center", va="center",
                       zorder=9, color="white", fontweight="bold")
        status = ax.text(0, 0, "", fontsize=7, ha="center", zorder=9, color="#333")
        trail, = ax.plot([], [], color=color, lw=1.4, alpha=0.45, zorder=2)
        artists[amr_id] = (circle, text, status, trail, [])

    frame_dt = args.speedup / args.fps
    times = np.arange(0.0, cmax + 2.0, frame_dt)

    def update(t):
        for amr_id in data.amr_ids:
            circle, text, status, trail, history = artists[amr_id]
            xy, radius, label = position_at(motions[amr_id], t)
            circle.center = tuple(xy)
            circle.radius = radius
            text.set_position(xy)
            status.set_position((xy[0], xy[1] + radius + 0.12))
            status.set_text(label)
            history.append(xy)
            pts = np.array(history)
            trail.set_data(pts[:, 0], pts[:, 1])
        title.set_text(
            f"P2 调度执行动画 - {args.method} {SOLVER_LABELS[args.method]} "
            f"({args.p1_algorithm})    t = {t:.1f} / Cmax = {cmax:.1f}")
        return []

    anim = FuncAnimation(fig, update, frames=times, blit=False)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"schedule_animation_{args.method}.gif"
    print(f"渲染 {len(times)} 帧 ...")
    anim.save(out_path, writer=PillowWriter(fps=args.fps))
    plt.close(fig)
    print(f"已保存: {out_path}")


if __name__ == "__main__":
    main()
