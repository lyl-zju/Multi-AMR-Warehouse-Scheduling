from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[2] / ".mplconfig"))

import matplotlib.pyplot as plt
import numpy as np

from p5_utils import configure_chinese_style, is_blank, read_csv_rows, to_float, to_int, write_csv_rows


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
P5_ROOT = PROJECT_ROOT / "data" / "processed" / "p5"
P5_DIR = P5_ROOT / "stage2_amr_marginal"
OUT_DIR = PROJECT_ROOT / "outputs" / "p5"
TASK_STAGE_DIR = WORKSPACE_ROOT / "task" / "stage2"
P2_DIR = PROJECT_ROOT / "src" / "p2_assignment"

sys.path.insert(0, str(P2_DIR))
from assign_and_sequence_tasks import run as run_p2  # noqa: E402


AMR_COUNTS = [2, 3, 4, 5, 6]
METHODS = ["m2", "m3"]
PRIMARY_METHOD = "m2"
ROBUST_METHOD = "m3"
P1_ALGORITHM = "mixed"
TASK_COUNT = 12
M2_TIME_LIMIT = 30
M3_TIME_LIMIT = 30
WEAK_DELTA_RATIO = 0.50
AMR_INIT_POOL = ["IN1", "IN2", "CHG1", "CHG2"]
SERVICE_DELAY_EPS = 1e-9


def patch_pulp_cbc_path_if_needed():
    """PuLP 3.0.2 on this macOS arm64 host may point to a missing osx/arm64 CBC.

    The package still ships an osx/64 CBC binary, and it runs through Rosetta on
    this machine. Patch only inside this P5 script so upstream P2 code remains
    untouched.
    """
    try:
        import pulp
        from pulp.apis import coin_api
    except Exception:
        return

    current = Path(getattr(coin_api, "pulp_cbc_path", ""))
    if current.exists():
        return
    fallback = Path(pulp.__file__).resolve().parent / "solverdir" / "cbc" / "osx" / "64" / "cbc"
    if fallback.exists():
        coin_api.pulp_cbc_path = str(fallback)
        fallback_path = str(fallback)

        class PatchedPULP_CBC_CMD(pulp.COIN_CMD):
            name = "PULP_CBC_CMD"
            pulp_cbc_path = fallback_path

            def __init__(
                self,
                mip=True,
                msg=True,
                timeLimit=None,
                gapRel=None,
                gapAbs=None,
                presolve=None,
                cuts=None,
                strong=None,
                options=None,
                warmStart=False,
                keepFiles=False,
                path=None,
                threads=None,
                logPath=None,
                timeMode="elapsed",
                maxNodes=None,
            ):
                super().__init__(
                    mip=mip,
                    msg=msg,
                    timeLimit=timeLimit,
                    gapRel=gapRel,
                    gapAbs=gapAbs,
                    presolve=presolve,
                    cuts=cuts,
                    strong=strong,
                    options=options,
                    warmStart=warmStart,
                    keepFiles=keepFiles,
                    path=fallback_path,
                    threads=threads,
                    logPath=logPath,
                    timeMode=timeMode,
                    maxNodes=maxNodes,
                )

        pulp.PULP_CBC_CMD = PatchedPULP_CBC_CMD


def parse_args():
    parser = argparse.ArgumentParser(description="Stage 2 AMR marginal improvement analysis.")
    parser.add_argument("--m2-time-limit", type=int, default=M2_TIME_LIMIT, help="MILP time limit per level.")
    parser.add_argument("--skip-run", action="store_true", help="Reuse existing P5 stage2 raw result CSV.")
    return parser.parse_args()


def as_float(row, key, default=0.0):
    value = row.get(key, "")
    if is_blank(value):
        return default
    return float(value)


def as_int(row, key, default=0):
    value = row.get(key, "")
    if is_blank(value):
        return default
    return int(float(value))


def render_template(path: Path, **kwargs) -> str:
    return path.read_text(encoding="utf-8").format(**kwargs)


def read_csv_dicts(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv_dicts(path: Path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_assignment_rows(case_output_dir: str):
    path = PROJECT_ROOT / case_output_dir / "assignment_result.csv"
    if not path.exists():
        return []
    return read_csv_rows(path)


def compute_resource_row(metric_row):
    """Derive engineering resource indicators from the actual P2 schedule output."""
    amr_count = int(metric_row["amr_count"])
    cmax = float(metric_row["Cmax"])
    assignment_rows = load_assignment_rows(metric_row["case_output_dir"])
    task_counts = {}
    active_time = 0.0
    waiting_time = 0.0
    max_task_count = 0
    min_task_count = 0
    if assignment_rows:
        for row in assignment_rows:
            amr_id = row.get("assigned_amr", "")
            task_counts[amr_id] = task_counts.get(amr_id, 0) + 1
            active_time += (
                as_float(row, "transition_travel_time")
                + as_float(row, "loaded_travel_time")
                + as_float(row, "service_time")
            )
            waiting_time += as_float(row, "estimated_waiting")
        counts = [task_counts.get(f"AMR{idx + 1}", 0) for idx in range(amr_count)]
        max_task_count = max(counts) if counts else 0
        min_task_count = min(counts) if counts else 0

    utilization = active_time / (amr_count * cmax) if amr_count > 0 and cmax > 0 else 0.0
    return {
        "method": metric_row["method"],
        "amr_count": amr_count,
        "task_count": int(metric_row["task_count"]),
        "Cmax": round(cmax, 6),
        "total_delay": round(float(metric_row["total_delay"]), 6),
        "late_count": int(metric_row["late_count"]),
        "service_feasible": int(
            float(metric_row["total_delay"]) <= SERVICE_DELAY_EPS
            and int(metric_row["late_count"]) == 0
            and int(metric_row["missing_transition_count"]) == 0
            and int(metric_row["missing_loaded_path_count"]) == 0
            and int(metric_row["energy_violation_count"]) == 0
        ),
        "total_active_time": round(active_time, 6),
        "total_waiting_time": round(waiting_time, 6),
        "resource_utilization": round(utilization, 6),
        "max_tasks_per_amr": max_task_count,
        "min_tasks_per_amr": min_task_count,
        "task_count_range": max_task_count - min_task_count,
        "solve_time": round(float(metric_row["solve_time"]), 6),
    }


def compute_workload_rows(metric_row):
    amr_count = int(metric_row["amr_count"])
    cmax = float(metric_row["Cmax"])
    summary_path = PROJECT_ROOT / metric_row["case_output_dir"] / "amr_sequence_summary.csv"
    if not summary_path.exists():
        return []
    rows = read_csv_rows(summary_path)
    out = []
    for idx in range(amr_count):
        amr_id = f"AMR{idx + 1}"
        row = next((item for item in rows if item.get("amr_id") == amr_id), None)
        if row is None:
            out.append(
                {
                    "method": metric_row["method"],
                    "amr_count": amr_count,
                    "amr_id": amr_id,
                    "task_count": 0,
                    "finish_time": 0.0,
                    "delay": 0.0,
                    "energy_used": 0.0,
                    "battery_end": 0.0,
                    "finish_share": 0.0,
                }
            )
            continue
        finish_time = as_float(row, "estimated_finish_time")
        out.append(
            {
                "method": metric_row["method"],
                "amr_count": amr_count,
                "amr_id": amr_id,
                "task_count": as_int(row, "task_count"),
                "finish_time": round(finish_time, 6),
                "delay": round(as_float(row, "estimated_total_delay"), 6),
                "energy_used": round(as_float(row, "total_energy_used"), 6),
                "battery_end": round(as_float(row, "battery_end"), 6),
                "finish_share": round(finish_time / cmax, 6) if cmax > 0 else 0.0,
            }
        )
    return out


def build_amr_rows(amr_count: int):
    base = read_csv_dicts(RAW_DIR / "amrs.csv")
    rows = [dict(row) for row in base[: min(amr_count, len(base))]]
    for idx in range(len(rows), amr_count):
        rows.append(
            {
                "amr_id": f"AMR{idx + 1}",
                "init_node": AMR_INIT_POOL[idx % len(AMR_INIT_POOL)],
                "battery": 90,
                "speed": 1.0,
            }
        )
    return rows


def prepare_case_inputs(amr_count: int):
    case_dir = P5_DIR / "stage2_inputs" / f"amr_{amr_count}"
    case_dir.mkdir(parents=True, exist_ok=True)
    task_rows = read_csv_dicts(RAW_DIR / "tasks.csv")
    if len(task_rows) != TASK_COUNT:
        raise RuntimeError(f"Stage2 expects {TASK_COUNT} raw tasks, got {len(task_rows)}")
    write_csv_dicts(case_dir / "tasks.csv", task_rows, list(task_rows[0].keys()))
    amr_rows = build_amr_rows(amr_count)
    write_csv_dicts(case_dir / "amrs.csv", amr_rows, ["amr_id", "init_node", "battery", "speed"])
    return case_dir / "tasks.csv", case_dir / "amrs.csv"


def run_single_case(amr_count: int, method: str, time_limit: int):
    tasks_csv, amrs_csv = prepare_case_inputs(amr_count)
    out_dir = P5_DIR / "stage2_p2_outputs" / f"{method}_amr_{amr_count}"
    data, solution, evaluation, solve_seconds = run_p2(
        method=method,
        p1_algorithm=P1_ALGORITHM,
        time_limit=time_limit,
        tasks_csv=tasks_csv,
        amrs_csv=amrs_csv,
        out_dir=out_dir,
        write_outputs=True,
    )
    metrics = evaluation.metrics
    row = {
        "run_id": f"stage2_{method}_m{amr_count}",
        "method": method,
        "p1_algorithm": P1_ALGORITHM,
        "task_count": len(data.task_ids),
        "amr_count": len(data.amr_ids),
        "solve_time": round(float(solve_seconds), 6),
        "missing_transition_count": metrics["missing_transition_count"],
        "missing_loaded_path_count": metrics["missing_loaded_path_count"],
        "energy_violation_count": metrics["energy_violation_count"],
        "priority_late_count": metrics["priority_late_count"],
        "late_count": metrics["late_count"],
        "priority_delay": metrics["priority_delay"],
        "total_delay": metrics["total_delay"],
        "Cmax": metrics["Cmax"],
        "empty_cost": metrics["empty_cost"],
        "loaded_cost": metrics["loaded_cost"],
        "total_energy_used": metrics["total_energy_used"],
        "total_waiting": metrics["total_waiting"],
        "load_balance_penalty": metrics["load_balance_penalty"],
        "F2": metrics["F2"],
        "F3": metrics["F3"],
        "solver_info": json.dumps(getattr(solution, "info", {}) or {}, ensure_ascii=False),
        "case_tasks_csv": str(tasks_csv.relative_to(PROJECT_ROOT)),
        "case_amrs_csv": str(amrs_csv.relative_to(PROJECT_ROOT)),
        "case_output_dir": str(out_dir.relative_to(PROJECT_ROOT)),
    }
    return row


def run_stage2_experiments(m2_time_limit: int):
    rows = []
    for amr_count in AMR_COUNTS:
        for method in METHODS:
            time_limit = m2_time_limit if method == "m2" else M3_TIME_LIMIT
            rows.append(run_single_case(amr_count, method, time_limit))
    return rows


def validate_rows(rows):
    validation_rows = []
    errors = []
    required = {(method, m) for method in METHODS for m in AMR_COUNTS}
    present = {(row.get("method", ""), as_int(row, "amr_count")) for row in rows}
    missing = sorted(required - present, key=lambda item: (item[0], item[1]))
    if missing:
        errors.append(f"missing self-run AMR cases: {missing}")
        validation_rows.append({"check": "self-run AMR case coverage", "status": "FAIL", "detail": str(missing)})
    else:
        validation_rows.append({"check": "self-run AMR case coverage", "status": "PASS", "detail": "m2/m3 x 2..6"})

    for row in rows:
        label = f"{row.get('method')}@m={as_int(row, 'amr_count')}"
        if as_int(row, "task_count") != TASK_COUNT:
            errors.append(f"{label}: task_count mismatch")
            validation_rows.append({"check": f"{label} task_count", "status": "FAIL", "detail": row.get("task_count", "")})
        else:
            validation_rows.append({"check": f"{label} task_count", "status": "PASS", "detail": str(TASK_COUNT)})

        for key in ("missing_transition_count", "missing_loaded_path_count", "energy_violation_count"):
            value = as_int(row, key)
            if value != 0:
                errors.append(f"{label}: {key}={value}")
                validation_rows.append({"check": f"{label} {key}", "status": "FAIL", "detail": str(value)})
            else:
                validation_rows.append({"check": f"{label} {key}", "status": "PASS", "detail": "0"})

        cmax = as_float(row, "Cmax")
        if not math.isfinite(cmax) or cmax <= 0:
            errors.append(f"{label}: invalid Cmax={cmax}")
            validation_rows.append({"check": f"{label} Cmax", "status": "FAIL", "detail": str(cmax)})
        else:
            validation_rows.append({"check": f"{label} Cmax", "status": "PASS", "detail": f"{cmax:.6f}"})

    return validation_rows, errors


def build_metric_rows(rows):
    metrics = []
    for row in rows:
        metrics.append(
            {
                "method": row.get("method", ""),
                "p1_algorithm": row.get("p1_algorithm", ""),
                "task_count": as_int(row, "task_count"),
                "amr_count": as_int(row, "amr_count"),
                "solve_time": round(as_float(row, "solve_time"), 6),
                "Cmax": round(as_float(row, "Cmax"), 6),
                "total_delay": round(as_float(row, "total_delay"), 6),
                "late_count": as_int(row, "late_count"),
                "priority_late_count": round(as_float(row, "priority_late_count"), 6),
                "missing_transition_count": as_int(row, "missing_transition_count"),
                "missing_loaded_path_count": as_int(row, "missing_loaded_path_count"),
                "energy_violation_count": as_int(row, "energy_violation_count"),
                "F2": round(as_float(row, "F2"), 6),
                "F3": round(as_float(row, "F3"), 6),
                "case_output_dir": row.get("case_output_dir", ""),
                "source": "p5_self_run",
            }
        )
    return sorted(metrics, key=lambda r: (r["method"], r["amr_count"]))


def rows_by_method(metrics, method):
    return [row for row in metrics if row["method"] == method]


def build_resource_frontier(metric_rows):
    return [compute_resource_row(row) for row in rows_by_method(metric_rows, PRIMARY_METHOD)]


def build_workload_rows(metric_rows):
    rows = []
    for metric_row in rows_by_method(metric_rows, PRIMARY_METHOD):
        rows.extend(compute_workload_rows(metric_row))
    return rows


def same_sign(left, right, eps=1e-9):
    if abs(left) <= eps and abs(right) <= eps:
        return True
    return left * right > -eps


def compute_marginals(primary_rows, robust_rows, frontier_rows):
    robust_map = {row["amr_count"]: row for row in robust_rows}
    frontier_map = {row["amr_count"]: row for row in frontier_rows}
    rows = []
    for left, right in zip(primary_rows[:-1], primary_rows[1:]):
        m = left["amr_count"]
        next_m = right["amr_count"]
        delta = left["Cmax"] - right["Cmax"]
        relative = delta / left["Cmax"] if left["Cmax"] else 0.0
        finite_diff_elasticity = m * relative
        left_frontier = frontier_map.get(m, {})
        right_frontier = frontier_map.get(next_m, {})
        left_feasible = int(left_frontier.get("service_feasible", 0))
        right_feasible = int(right_frontier.get("service_feasible", 0))
        delay_eliminated = int(left_feasible == 0 and right_feasible == 1)
        utilization_delta = (
            float(left_frontier.get("resource_utilization", 0.0))
            - float(right_frontier.get("resource_utilization", 0.0))
        )
        robust_delta = ""
        trend_same = ""
        if m in robust_map and next_m in robust_map:
            robust_delta_value = robust_map[m]["Cmax"] - robust_map[next_m]["Cmax"]
            robust_delta = round(robust_delta_value, 6)
            trend_same = int(same_sign(delta, robust_delta_value))
        rows.append(
            {
                "from_amr": m,
                "to_amr": next_m,
                "delta_Cmax": round(delta, 6),
                "forward_diff_seconds": round(delta, 6),
                "relative_improvement": round(relative, 6),
                "finite_diff_elasticity": round(finite_diff_elasticity, 6),
                "delay_eliminated": delay_eliminated,
                "from_utilization": round(float(left_frontier.get("resource_utilization", 0.0)), 6),
                "to_utilization": round(float(right_frontier.get("resource_utilization", 0.0)), 6),
                "utilization_drop": round(utilization_delta, 6),
                "m3_delta_Cmax": robust_delta,
                "trend_same": trend_same,
            }
        )
    return rows


def is_nonincreasing(values, eps=1e-9):
    return all(values[idx] >= values[idx + 1] - eps for idx in range(len(values) - 1))


def summarize(primary_rows, robust_rows, marginal_rows, frontier_rows):
    best = max(marginal_rows, key=lambda row: float(row["delta_Cmax"]))
    first_delta = float(marginal_rows[0]["delta_Cmax"]) if marginal_rows else 0.0
    weak = None
    if first_delta > 0:
        for row in marginal_rows[1:]:
            if float(row["delta_Cmax"]) <= first_delta * WEAK_DELTA_RATIO:
                weak = row
                break
    feasible_rows = [row for row in frontier_rows if int(row["service_feasible"]) == 1]
    min_service_amr = min((row["amr_count"] for row in feasible_rows), default=None)
    baseline = next(row for row in primary_rows if row["amr_count"] == 4)
    baseline_frontier = next(row for row in frontier_rows if row["amr_count"] == 4)
    first_feasible_crossing = next((row for row in marginal_rows if int(row["delay_eliminated"]) == 1), None)
    trend_consistency = all(str(row["trend_same"]) == "1" for row in marginal_rows)
    return {
        "best": best,
        "weak": weak,
        "trend_consistency": trend_consistency,
        "baseline": baseline,
        "baseline_frontier": baseline_frontier,
        "min_service_amr": min_service_amr,
        "first_feasible_crossing": first_feasible_crossing,
        "primary_monotone": is_nonincreasing([row["Cmax"] for row in primary_rows]),
        "robust_monotone": is_nonincreasing([row["Cmax"] for row in robust_rows]),
    }


def append_trend_validations(summary, validation_rows, errors):
    checks = [
        ("m2 monotone Cmax", summary["primary_monotone"], "m2 Cmax is not non-increasing as AMR count grows"),
        ("m3 monotone Cmax", summary["robust_monotone"], "m3 Cmax is not non-increasing as AMR count grows"),
        ("m2/m3 marginal trend", summary["trend_consistency"], "m2 and m3 marginal trend directions are inconsistent"),
        ("service feasible AMR threshold", summary["min_service_amr"] is not None, "no zero-delay feasible AMR count found"),
    ]
    for name, ok, error in checks:
        if ok:
            validation_rows.append({"check": name, "status": "PASS", "detail": "ok"})
        else:
            errors.append(error)
            validation_rows.append({"check": name, "status": "FAIL", "detail": error})


def plot_stage2(primary_rows, robust_rows, frontier_rows, marginal_rows, workload_rows, summary, output_png, output_pdf):
    configure_chinese_style()
    xs = [row["amr_count"] for row in primary_rows]
    y_primary = [row["Cmax"] for row in primary_rows]
    y_robust = [row["Cmax"] for row in robust_rows]
    delta_x = [row["to_amr"] for row in marginal_rows]
    deltas = [float(row["delta_Cmax"]) for row in marginal_rows]
    relatives = [float(row["relative_improvement"]) for row in marginal_rows]
    elasticities = [float(row["finite_diff_elasticity"]) for row in marginal_rows]
    delays = [float(row["total_delay"]) for row in frontier_rows]
    utilizations = [float(row["resource_utilization"]) * 100 for row in frontier_rows]

    main_color = "#4c78a8"
    robust_color = "#8a8f98"
    accent_color = "#d95f02"
    service_color = "#2f855a"
    delay_color = "#b64b4b"
    grid_color = "#d7dce2"

    fig, axes = plt.subplots(
        2,
        2,
        figsize=(10.8, 7.4),
        gridspec_kw={"hspace": 0.38, "wspace": 0.24},
        constrained_layout=False,
    )
    ax_frontier, ax_marginal, ax_service, ax_heatmap = axes.ravel()

    ax_frontier.plot(xs, y_primary, color=main_color, marker="o", linewidth=2.1, markersize=5.8, label="联合整数规划")
    ax_frontier.plot(xs, y_robust, color=robust_color, marker="s", linewidth=1.4, markersize=4.8, linestyle="--", label="趋势校验")
    ax_frontier.set_title("资源前沿")
    ax_frontier.set_xlabel("移动机器人数量（台）")
    ax_frontier.set_ylabel("最大完工时间（秒）")
    ax_frontier.set_xticks(xs)
    ax_frontier.grid(axis="y", color=grid_color, linewidth=0.8, alpha=0.65)
    ax_frontier.spines["top"].set_visible(False)
    ax_frontier.spines["right"].set_visible(False)
    ax_frontier.legend(frameon=False, loc="upper right", fontsize=8)

    min_service_amr = summary.get("min_service_amr")
    if min_service_amr is not None:
        y_service = next(row["Cmax"] for row in primary_rows if row["amr_count"] == min_service_amr)
        ax_frontier.scatter([min_service_amr], [y_service], s=76, facecolor="white", edgecolor=service_color, linewidth=1.8, zorder=5)
        ax_frontier.text(
            min_service_amr + 0.05,
            y_service + (max(y_primary) - min(y_primary)) * 0.08,
            "最低无延期",
            color=service_color,
            fontsize=8.5,
            ha="left",
            va="bottom",
        )

    if marginal_rows:
        first_delta = deltas[0]
        weak_idx = None
        if first_delta > 0:
            for idx, value in enumerate(deltas[1:], start=1):
                if value <= first_delta * WEAK_DELTA_RATIO:
                    weak_idx = idx
                    break
        if weak_idx is not None:
            m = delta_x[weak_idx]
            ax_frontier.axvline(m, color=accent_color, linewidth=1.1, linestyle=":", alpha=0.85)
            ax_frontier.text(
                m + 0.05,
                min(y_primary) + (max(y_primary) - min(y_primary)) * 0.08,
                "边际减弱",
                color=accent_color,
                fontsize=8.5,
                ha="left",
                va="bottom",
            )

    bars = ax_marginal.bar(delta_x, deltas, width=0.55, color=main_color, edgecolor="#2f5f8f", linewidth=0.8)
    ax_marginal.set_title("前向有限差分")
    ax_marginal.set_xlabel("增加后的数量（台）")
    ax_marginal.set_ylabel("下降量（秒）")
    ax_marginal.set_xticks(delta_x)
    ax_marginal.grid(axis="y", color=grid_color, linewidth=0.8, alpha=0.65)
    ax_marginal.spines["top"].set_visible(False)
    ax_marginal.spines["right"].set_visible(False)
    if deltas:
        max_delta = max(deltas)
        for idx, (bar, value) in enumerate(zip(bars, deltas)):
            color = accent_color if value == max_delta else "#243447"
            ax_marginal.text(
                bar.get_x() + bar.get_width() / 2,
                value + max(max_delta, 1.0) * 0.035,
                f"{value:.1f}秒\n{relatives[idx] * 100:.1f}%\n弹性{elasticities[idx]:.2f}",
                ha="center",
                va="bottom",
                fontsize=7.6,
                color=color,
                fontweight="bold" if value == max_delta else "normal",
            )

    ax_service.plot(xs, delays, color=delay_color, marker="o", linewidth=2.0, markersize=5.5, label="总延期")
    ax_service.fill_between(xs, delays, color=delay_color, alpha=0.10)
    ax_service.set_title("服务风险与利用率")
    ax_service.set_xlabel("移动机器人数量（台）")
    ax_service.set_ylabel("总延期（秒）")
    ax_service.set_xticks(xs)
    ax_service.grid(axis="y", color=grid_color, linewidth=0.8, alpha=0.65)
    ax_service.spines["top"].set_visible(False)
    ax_service.spines["right"].set_visible(False)
    ax_service_right = ax_service.twinx()
    ax_service_right.plot(xs, utilizations, color=service_color, marker="s", linewidth=1.7, markersize=4.8, label="资源利用率")
    ax_service_right.set_ylabel("利用率（%）")
    ax_service_right.spines["top"].set_visible(False)
    if min_service_amr is not None:
        ax_service.axvline(min_service_amr, color=service_color, linestyle=":", linewidth=1.1, alpha=0.85)
    lines, labels = ax_service.get_legend_handles_labels()
    lines_r, labels_r = ax_service_right.get_legend_handles_labels()
    ax_service.legend(lines + lines_r, labels + labels_r, frameon=False, loc="upper right", fontsize=8)

    heat = np.full((len(AMR_COUNTS), max(AMR_COUNTS)), np.nan)
    task_labels = [["" for _ in range(max(AMR_COUNTS))] for _ in AMR_COUNTS]
    for row in workload_rows:
        y_idx = AMR_COUNTS.index(int(row["amr_count"]))
        x_idx = int(str(row["amr_id"]).replace("AMR", "")) - 1
        heat[y_idx, x_idx] = float(row["finish_share"])
        task_labels[y_idx][x_idx] = str(row["task_count"])
    image = ax_heatmap.imshow(np.ma.masked_invalid(heat), cmap="Blues", vmin=0, vmax=1, aspect="auto")
    ax_heatmap.set_title("车辆负载摊开")
    ax_heatmap.set_xlabel("车辆编号")
    ax_heatmap.set_ylabel("资源水平")
    ax_heatmap.set_xticks(range(max(AMR_COUNTS)))
    ax_heatmap.set_xticklabels([str(idx) for idx in range(1, max(AMR_COUNTS) + 1)])
    ax_heatmap.set_yticks(range(len(AMR_COUNTS)))
    ax_heatmap.set_yticklabels([f"{m}台" for m in AMR_COUNTS])
    for y_idx in range(len(AMR_COUNTS)):
        for x_idx in range(max(AMR_COUNTS)):
            if not np.isnan(heat[y_idx, x_idx]):
                value = heat[y_idx, x_idx]
                color = "white" if value > 0.72 else "#243447"
                ax_heatmap.text(x_idx, y_idx, f"{task_labels[y_idx][x_idx]}项\n{value * 100:.0f}%", ha="center", va="center", fontsize=7.2, color=color)
    ax_heatmap.spines["top"].set_visible(False)
    ax_heatmap.spines["right"].set_visible(False)
    cbar = fig.colorbar(image, ax=ax_heatmap, fraction=0.046, pad=0.03)
    cbar.set_label("完工占比", fontsize=8)
    cbar.ax.tick_params(labelsize=7)

    fig.suptitle("固定 12 个任务下的移动机器人资源敏感度", fontsize=14, fontweight="bold", y=0.985)
    fig.align_ylabels([ax_frontier, ax_marginal, ax_service, ax_heatmap])
    output_png.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=300, bbox_inches="tight")
    fig.savefig(output_pdf, bbox_inches="tight")
    plt.close(fig)





def main():
    args = parse_args()
    patch_pulp_cbc_path_if_needed()
    raw_result_csv = P5_DIR / "stage2_self_run_raw.csv"
    if args.skip_run and raw_result_csv.exists():
        raw_rows = read_csv_rows(raw_result_csv)
    else:
        raw_rows = run_stage2_experiments(args.m2_time_limit)
        write_csv_rows(raw_result_csv, raw_rows, list(raw_rows[0].keys()) if raw_rows else [])

    validation_rows, errors = validate_rows(raw_rows)
    metrics = build_metric_rows(raw_rows)
    primary_rows = rows_by_method(metrics, PRIMARY_METHOD)
    robust_rows = rows_by_method(metrics, ROBUST_METHOD)
    frontier_rows = build_resource_frontier(metrics)
    workload_rows = build_workload_rows(metrics)
    marginal_rows = compute_marginals(primary_rows, robust_rows, frontier_rows)
    summary = summarize(primary_rows, robust_rows, marginal_rows, frontier_rows)
    append_trend_validations(summary, validation_rows, errors)

    output_png = OUT_DIR / "stage2_amr_marginal.png"
    output_pdf = OUT_DIR / "stage2_amr_marginal.pdf"
    plot_stage2(primary_rows, robust_rows, frontier_rows, marginal_rows, workload_rows, summary, output_png, output_pdf)

    P5_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv_rows(P5_DIR / "stage2_amr_metrics.csv", metrics, list(metrics[0].keys()) if metrics else [])
    write_csv_rows(P5_DIR / "stage2_resource_frontier.csv", frontier_rows, list(frontier_rows[0].keys()) if frontier_rows else [])
    write_csv_rows(P5_DIR / "stage2_workload_by_amr.csv", workload_rows, list(workload_rows[0].keys()) if workload_rows else [])
    write_csv_rows(
        P5_DIR / "stage2_amr_marginals.csv",
        marginal_rows,
        [
            "from_amr",
            "to_amr",
            "delta_Cmax",
            "forward_diff_seconds",
            "relative_improvement",
            "finite_diff_elasticity",
            "delay_eliminated",
            "from_utilization",
            "to_utilization",
            "utilization_drop",
            "m3_delta_Cmax",
            "trend_same",
        ],
    )
    write_csv_rows(P5_DIR / "stage2_validation_log.csv", validation_rows, ["check", "status", "detail"])

    report = {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "source": "p5_self_run",
        "raw_result_csv": str(raw_result_csv),
        "metrics": metrics,
        "resource_frontier": frontier_rows,
        "workload_by_amr": workload_rows,
        "marginals": marginal_rows,
        "summary": summary,
    }
    with (P5_DIR / "stage2_validation_report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

   

    if errors:
        print("Stage 2 validation failed:")
        for error in errors:
            print(f"- {error}")
        print(f"Validation log: {P5_DIR / 'stage2_validation_log.csv'}")
        return 1

    print("Stage 2 validation passed.")
    print(f"Figure: {output_png}")
    print(f"Metrics: {P5_DIR / 'stage2_amr_metrics.csv'}")
    print(f"Resource frontier: {P5_DIR / 'stage2_resource_frontier.csv'}")
    print(f"Workload by AMR: {P5_DIR / 'stage2_workload_by_amr.csv'}")
    print(f"Marginals: {P5_DIR / 'stage2_amr_marginals.csv'}")
    print(f"Conclusion md: {TASK_STAGE_DIR / 'conclusion.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
