from __future__ import annotations

import csv
import math
import os
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".mplconfig"))

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np


def configure_chinese_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial Unicode MS", "STHeiti", "Songti SC", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "axes.titleweight": "bold",
            "axes.labelsize": 11,
            "axes.titlesize": 13,
            "legend.fontsize": 9,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
        }
    )


def is_blank(value) -> bool:
    return value is None or str(value).strip() == ""


def to_float(value):
    if is_blank(value):
        return None
    return float(str(value).strip())


def to_int(value):
    if is_blank(value):
        return None
    return int(float(str(value).strip()))


def clean_row(row: dict[str, str]) -> dict[str, str]:
    return {
        str(key).strip(): ("" if value is None else str(value).strip())
        for key, value in row.items()
    }


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [clean_row(row) for row in csv.DictReader(handle)]


def write_csv_rows(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None and rows:
        fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or [])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def group_rows(rows: list[dict], key: str) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get(key, ""))].append(row)
    return buckets


def sort_key_text(value: str):
    if is_blank(value):
        return (1, "")
    text = str(value).strip()
    try:
        return (0, float(text))
    except ValueError:
        return (0, text)


def sort_rows(rows: list[dict], *keys: str) -> list[dict]:
    return sorted(rows, key=lambda row: tuple(sort_key_text(str(row.get(key, ""))) for key in keys))


def natural_amr_order(amr_id: str):
    text = str(amr_id).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return (text.rstrip(digits), int(digits) if digits else 0, text)


def cell_edges(values: np.ndarray, cell_size: float) -> np.ndarray:
    low = math.floor(float(np.min(values)) / cell_size) * cell_size
    high = math.ceil(float(np.max(values)) / cell_size) * cell_size
    if math.isclose(low, high):
        high = low + cell_size
    return np.arange(low, high + cell_size, cell_size)


def build_occupancy_histogram(points: list[dict], cell_size: float, bounds: tuple[float, float, float, float] | None = None):
    xs = np.array([to_float(p["x"]) for p in points if not is_blank(p.get("x"))], dtype=float)
    ys = np.array([to_float(p["y"]) for p in points if not is_blank(p.get("y"))], dtype=float)
    if xs.size == 0 or ys.size == 0:
        raise ValueError("trajectory points are empty; cannot build occupancy heatmap")

    if bounds is None:
        x_edges = cell_edges(xs, cell_size)
        y_edges = cell_edges(ys, cell_size)
    else:
        min_x, max_x, min_y, max_y = bounds
        x_edges = np.arange(min_x, max_x + cell_size, cell_size)
        y_edges = np.arange(min_y, max_y + cell_size, cell_size)

    hist, x_edges, y_edges = np.histogram2d(xs, ys, bins=[x_edges, y_edges])
    return hist, x_edges, y_edges


ZONE_STYLE = {
    "inbound": {"facecolor": "#f4f5f6", "edgecolor": "#9ca3af"},
    "sorting": {"facecolor": "#f4f5f6", "edgecolor": "#9ca3af"},
    "outbound": {"facecolor": "#f4f5f6", "edgecolor": "#9ca3af"},
    "charger": {"facecolor": "#f4f5f6", "edgecolor": "#9ca3af"},
}


def chinese_zone_label(zone: dict) -> str:
    zone_type = str(zone.get("zone_type", "")).strip().lower()
    mapping = {
        "inbound": "入库区",
        "sorting": "分拣区",
        "outbound": "出库区",
        "charger": "充电区",
    }
    label = str(zone.get("label", "")).strip()
    if zone_type in mapping:
        return mapping[zone_type]
    if label:
        return label
    return str(zone.get("zone_id", "")).strip()


def chinese_obstacle_label(obstacle: dict) -> str:
    obstacle_type = str(obstacle.get("obstacle_type", "")).strip().lower()
    obstacle_id = str(obstacle.get("obstacle_id", "")).strip()
    if obstacle_type == "rack":
        suffix = ""
        if "_" in obstacle_id:
            suffix = obstacle_id.split("_")[-1]
        elif obstacle_id:
            suffix = obstacle_id[-1]
        return f"货架{suffix}" if suffix else "货架"
    if obstacle_type == "wall":
        return "墙体"
    label = str(obstacle.get("label", "")).strip()
    return label or obstacle_id


def format_hotspot_text(rank: int, count: int) -> str:
    return f"热点{rank}\n{count}次"


def draw_floor_context(ax, zones: list[dict], obstacles: list[dict]) -> None:
    # 先画静态布局，再叠加轨迹热力图，避免热图遮住仓库边界。
    for zone in zones:
        x = to_float(zone["x"])
        y = to_float(zone["y"])
        width = to_float(zone["width"])
        height = to_float(zone["height"])
        zone_type = str(zone.get("zone_type", "")).strip().lower()
        style = ZONE_STYLE.get(zone_type, {"facecolor": "#f4f4f4", "edgecolor": "#9e9e9e"})
        ax.add_patch(
            Rectangle(
                (x, y),
                width,
                height,
                linewidth=1.0,
                facecolor=style["facecolor"],
                edgecolor=style["edgecolor"],
                alpha=0.18,
                zorder=1,
            )
        )
        label = str(zone.get("label", zone.get("zone_id", ""))).strip()
        if label:
            ax.text(
                x + width / 2.0,
                y + height / 2.0,
                chinese_zone_label(zone),
                ha="center",
                va="center",
                fontsize=7,
                color="#1f1f1f",
                fontweight="bold",
                zorder=4,
            )

    for obstacle in obstacles:
        x = to_float(obstacle["x"])
        y = to_float(obstacle["y"])
        width = to_float(obstacle["width"])
        height = to_float(obstacle["height"])
        ax.add_patch(
            Rectangle(
                (x, y),
                width,
                height,
                linewidth=0.9,
                facecolor="#4d4d4d",
                edgecolor="#222222",
                alpha=0.75,
                zorder=2,
            )
        )
        label = str(obstacle.get("label", obstacle.get("obstacle_id", ""))).strip()
        if label:
            ax.text(
                x + width / 2.0,
                y + height / 2.0,
                chinese_obstacle_label(obstacle),
                ha="center",
                va="center",
                fontsize=6,
                color="white",
                fontweight="bold",
                zorder=5,
            )

    ax.set_aspect("equal", adjustable="datalim")
    ax.set_facecolor("#fbfbfb")


def add_top_hotspot_labels(ax, hist: np.ndarray, x_edges: np.ndarray, y_edges: np.ndarray, top_k: int = 5) -> list[dict]:
    flat = hist.ravel()
    order = np.argsort(flat)[::-1]
    hotspots: list[dict] = []
    rank = 0
    for flat_index in order:
        count = float(flat[flat_index])
        if count <= 0:
            break
        ix, iy = np.unravel_index(flat_index, hist.shape)
        x0, x1 = float(x_edges[ix]), float(x_edges[ix + 1])
        y0, y1 = float(y_edges[iy]), float(y_edges[iy + 1])
        rank += 1
        hotspot = {
            "rank": rank,
            "cell_x": x0,
            "cell_y": y0,
            "count": int(count),
        }
        hotspots.append(hotspot)
        ax.text(
            (x0 + x1) / 2.0,
            (y0 + y1) / 2.0,
            format_hotspot_text(rank, int(count)),
            ha="center",
            va="center",
            fontsize=7,
            color="white",
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#1a1a1a", edgecolor="white", alpha=0.88),
            zorder=6,
        )
        if rank >= top_k:
            break
    return hotspots
