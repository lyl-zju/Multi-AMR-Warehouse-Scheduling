from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import shutil
import sys
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[2] / ".mplconfig"))

import matplotlib.pyplot as plt
import numpy as np

from p5_utils import configure_chinese_style, is_blank, read_csv_rows, write_csv_rows


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
P5_ROOT = PROJECT_ROOT / "data" / "processed" / "p5"
P5_DIR = P5_ROOT / "stage3_task_capacity"
OUT_DIR = PROJECT_ROOT / "outputs" / "p5"
REPORT_FIGURE_DIR = WORKSPACE_ROOT / "report" / "figures"
P2_DIR = PROJECT_ROOT / "src" / "p2_assignment"

sys.path.insert(0, str(P2_DIR))
from assign_and_sequence_tasks import run as run_p2  # noqa: E402


DEFAULT_SEEDS = [0, 1, 2]
DEFAULT_MAX_N = 30
DEFAULT_HORIZON = 55.0
DEFAULT_AMR_COUNT = 4
DEFAULT_METHOD = "m2"
DEFAULT_P1_ALGORITHM = "mixed"
DEFAULT_TIME_LIMIT = 30
SMOKE_MAX_N = 8
SMOKE_SEEDS = [0]

AMR_INIT_POOL = ["IN1", "IN2", "CHG1", "CHG2"]
PICKUP_POOL = [f"P{i}" for i in range(1, 10)]
SORT_POOL = ["SORT1", "SORT2"]
OUT_POOL = ["OUT1", "OUT2"]
TASK_FIELDNAMES = [
    "task_id",
    "pickup",
    "delivery",
    "service_time",
    "earliest_start",
    "latest_finish",
    "priority",
]
AMR_FIELDNAMES = ["amr_id", "init_node", "battery", "speed"]
RAW_FIELDNAMES = [
    "run_id",
    "seed",
    "n",
    "method",
    "p1_algorithm",
    "amr_count",
    "H",
    "Cmax",
    "total_delay",
    "late_count",
    "priority_late_count",
    "missing_path_count",
    "missing_transition_count",
    "missing_loaded_path_count",
    "energy_violation_count",
    "solve_time",
    "feasible",
    "capacity_satisfied",
    "F2",
    "F3",
    "case_tasks_csv",
    "case_amrs_csv",
    "case_output_dir",
    "solver_info",
    "source",
]
CURVE_FIELDNAMES = [
    "n",
    "seed_count",
    "Cmax_min",
    "Cmax_median",
    "Cmax_max",
    "Cmax_mean",
    "total_delay_median",
    "late_count_median",
    "feasible_all",
    "capacity_satisfied_all",
    "capacity_satisfied_seed_count",
    "violating_seed_count",
    "forward_delta_Cmax",
    "elasticity",
]


def parse_args():
    parser = argparse.ArgumentParser(description="P5 stage3 task-load capacity sensitivity analysis.")
    parser.add_argument("--method", choices=["m2"], default=DEFAULT_METHOD, help="P2 optimizer used for each re-optimization.")
    parser.add_argument("--p1-algorithm", choices=["mixed"], default=DEFAULT_P1_ALGORITHM, help="Fixed P1 path source.")
    parser.add_argument("--amr-count", type=int, default=DEFAULT_AMR_COUNT, help="Fixed AMR count.")
    parser.add_argument("--max-n", type=int, default=DEFAULT_MAX_N, help="Maximum prefix task count.")
    parser.add_argument("--seeds", default=",".join(str(seed) for seed in DEFAULT_SEEDS), help="Comma-separated seed list.")
    parser.add_argument("--horizon", type=float, default=DEFAULT_HORIZON, help="Explicit business horizon H.")
    parser.add_argument("--time-limit", type=int, default=DEFAULT_TIME_LIMIT, help="P2 solver time limit per case.")
    parser.add_argument("--skip-run", action="store_true", help="Reuse existing raw stage3 sweep CSV.")
    parser.add_argument("--smoke", action="store_true", help="Run a small prefix-checking smoke experiment.")
    return parser.parse_args()


def parse_seeds(value: str) -> list[int]:
    seeds = []
    for part in str(value).split(","):
        part = part.strip()
        if part:
            seeds.append(int(part))
    if not seeds:
        raise ValueError("at least one seed is required")
    return seeds


def as_float(row: dict, key: str, default=0.0) -> float:
    value = row.get(key, "")
    if is_blank(value):
        return default
    return float(value)


def as_int(row: dict, key: str, default=0) -> int:
    value = row.get(key, "")
    if is_blank(value):
        return default
    return int(float(value))


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv_dicts(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def relative_to_project(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def patch_pulp_cbc_path_if_needed():
    """Keep m2 available on macOS arm64 hosts where PuLP points at a missing CBC binary."""
    try:
        import pulp
        from pulp.apis import coin_api
    except Exception:
        return

    current = Path(getattr(coin_api, "pulp_cbc_path", ""))
    if current.exists():
        return
    fallback = Path(pulp.__file__).resolve().parent / "solverdir" / "cbc" / "osx" / "64" / "cbc"
    if not fallback.exists():
        return

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


def build_amr_rows(amr_count: int) -> list[dict]:
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


def make_nested_task_pool(max_n: int, seed: int, amr_count: int) -> list[dict]:
    """Generate a baseline-anchored nested task pool for P5 only.

    The first tasks are the raw baseline tasks, so n=12 remains comparable with
    the stage1/stage2 baseline. Additional tasks are generated once per seed,
    and every smaller n is a strict prefix of the same maximum pool.
    """
    base_rows = read_csv_dicts(RAW_DIR / "tasks.csv")
    rows = [dict(row) for row in base_rows[: min(max_n, len(base_rows))]]
    if len(rows) >= max_n:
        return rows

    rng = random.Random(20260619 + int(seed) * 1009 + max_n * 17)
    out_used = sum(1 for row in rows if str(row.get("delivery", "")).startswith("OUT"))
    max_out = max(amr_count + 1, out_used)
    release = max(as_float(row, "earliest_start", 0.0) for row in rows) if rows else 0.0

    for idx in range(len(rows), max_n):
        if out_used < max_out and rng.random() < 0.16:
            delivery = rng.choice(OUT_POOL)
            out_used += 1
        else:
            delivery = rng.choice(SORT_POOL)

        release += rng.choice([0, 1, 1, 2, 2, 3])
        slack = rng.randint(25, 40)
        rows.append(
            {
                "task_id": f"T{idx + 1:02d}",
                "pickup": rng.choice(PICKUP_POOL),
                "delivery": delivery,
                "service_time": rng.choice([2, 2, 3]),
                "earliest_start": int(release),
                "latest_finish": int(release + slack),
                "priority": rng.randint(1, 3),
            }
        )
    return rows


def validate_prefix_pools(pools: dict[int, list[dict]], n_values: list[int]) -> tuple[list[dict], list[str]]:
    validation_rows = []
    errors = []
    for seed, pool in pools.items():
        task_ids = [str(row["task_id"]) for row in pool]
        if len(task_ids) != len(set(task_ids)):
            errors.append(f"seed {seed}: duplicated task ids in maximum pool")
            validation_rows.append({"check": f"seed {seed} unique pool ids", "status": "FAIL", "detail": "duplicated task ids"})
        else:
            validation_rows.append({"check": f"seed {seed} unique pool ids", "status": "PASS", "detail": f"{len(task_ids)} tasks"})

        for left, right in zip(n_values[:-1], n_values[1:]):
            left_ids = task_ids[:left]
            right_prefix = task_ids[:right][:left]
            if left_ids != right_prefix:
                errors.append(f"seed {seed}: T({left}) is not prefix of T({right})")
                validation_rows.append({"check": f"seed {seed} prefix {left}->{right}", "status": "FAIL", "detail": "prefix mismatch"})
            else:
                validation_rows.append({"check": f"seed {seed} prefix {left}->{right}", "status": "PASS", "detail": "ok"})
    return validation_rows, errors


def prepare_case_inputs(seed: int, n: int, pool: list[dict], amr_count: int) -> tuple[Path, Path]:
    case_dir = P5_DIR / "stage3_inputs" / f"seed_{seed}" / f"n_{n:02d}"
    case_dir.mkdir(parents=True, exist_ok=True)
    task_rows = [dict(row) for row in pool[:n]]
    amr_rows = build_amr_rows(amr_count)
    write_csv_dicts(case_dir / "tasks.csv", task_rows, TASK_FIELDNAMES)
    write_csv_dicts(case_dir / "amrs.csv", amr_rows, AMR_FIELDNAMES)
    return case_dir / "tasks.csv", case_dir / "amrs.csv"


def run_single_case(seed: int, n: int, pool: list[dict], method: str, p1_algorithm: str, amr_count: int, horizon: float, time_limit: int) -> dict:
    tasks_csv, amrs_csv = prepare_case_inputs(seed, n, pool, amr_count)
    out_dir = P5_DIR / "stage3_p2_outputs" / f"{method}_seed_{seed}_n_{n:02d}"
    data, solution, evaluation, solve_seconds = run_p2(
        method=method,
        p1_algorithm=p1_algorithm,
        time_limit=time_limit,
        tasks_csv=tasks_csv,
        amrs_csv=amrs_csv,
        out_dir=out_dir,
        write_outputs=True,
    )
    metrics = evaluation.metrics
    missing_path_count = int(metrics["missing_transition_count"]) + int(metrics["missing_loaded_path_count"])
    feasible = int(
        len(data.task_ids) == n
        and missing_path_count == 0
        and int(metrics["energy_violation_count"]) == 0
        and math.isfinite(float(metrics["Cmax"]))
    )
    capacity_satisfied = int(feasible == 1 and float(metrics["Cmax"]) <= float(horizon) + 1e-9)
    return {
        "run_id": f"stage3_{method}_s{seed}_n{n:02d}",
        "seed": seed,
        "n": n,
        "method": method,
        "p1_algorithm": p1_algorithm,
        "amr_count": amr_count,
        "H": round(float(horizon), 6),
        "Cmax": round(float(metrics["Cmax"]), 6),
        "total_delay": round(float(metrics["total_delay"]), 6),
        "late_count": int(float(metrics["late_count"])),
        "priority_late_count": round(float(metrics["priority_late_count"]), 6),
        "missing_path_count": missing_path_count,
        "missing_transition_count": int(metrics["missing_transition_count"]),
        "missing_loaded_path_count": int(metrics["missing_loaded_path_count"]),
        "energy_violation_count": int(metrics["energy_violation_count"]),
        "solve_time": round(float(solve_seconds), 6),
        "feasible": feasible,
        "capacity_satisfied": capacity_satisfied,
        "F2": round(float(metrics["F2"]), 6),
        "F3": round(float(metrics["F3"]), 6),
        "case_tasks_csv": relative_to_project(tasks_csv),
        "case_amrs_csv": relative_to_project(amrs_csv),
        "case_output_dir": relative_to_project(out_dir),
        "solver_info": json.dumps(getattr(solution, "info", {}) or {}, ensure_ascii=False),
        "source": "p5_stage3_self_run",
    }


def run_stage3_experiments(seeds: list[int], n_values: list[int], method: str, p1_algorithm: str, amr_count: int, horizon: float, time_limit: int):
    pools = {seed: make_nested_task_pool(max(n_values), seed, amr_count) for seed in seeds}
    validation_rows, errors = validate_prefix_pools(pools, n_values)
    rows = []
    for seed in seeds:
        for n in n_values:
            rows.append(run_single_case(seed, n, pools[seed], method, p1_algorithm, amr_count, horizon, time_limit))
    return rows, validation_rows, errors


def median(values: list[float]) -> float:
    return float(np.median(np.array(values, dtype=float))) if values else 0.0


def build_capacity_curve(raw_rows: list[dict]) -> list[dict]:
    grouped: dict[int, list[dict]] = {}
    for row in raw_rows:
        grouped.setdefault(as_int(row, "n"), []).append(row)

    rows = []
    for n in sorted(grouped):
        group = grouped[n]
        cmax_values = [as_float(row, "Cmax") for row in group]
        delay_values = [as_float(row, "total_delay") for row in group]
        late_values = [as_float(row, "late_count") for row in group]
        feasible_values = [as_int(row, "feasible") for row in group]
        capacity_values = [as_int(row, "capacity_satisfied") for row in group]
        rows.append(
            {
                "n": n,
                "seed_count": len(group),
                "Cmax_min": round(min(cmax_values), 6),
                "Cmax_median": round(median(cmax_values), 6),
                "Cmax_max": round(max(cmax_values), 6),
                "Cmax_mean": round(float(np.mean(cmax_values)), 6),
                "total_delay_median": round(median(delay_values), 6),
                "late_count_median": round(median(late_values), 6),
                "feasible_all": int(all(value == 1 for value in feasible_values)),
                "capacity_satisfied_all": int(all(value == 1 for value in capacity_values)),
                "capacity_satisfied_seed_count": int(sum(capacity_values)),
                "violating_seed_count": int(len(capacity_values) - sum(capacity_values)),
                "forward_delta_Cmax": "",
                "elasticity": "",
            }
        )

    for idx, row in enumerate(rows[:-1]):
        current = float(row["Cmax_median"])
        nxt = float(rows[idx + 1]["Cmax_median"])
        delta = nxt - current
        row["forward_delta_Cmax"] = round(delta, 6)
        row["elasticity"] = round(float(row["n"]) * delta / current, 6) if current > 1e-9 else ""
    return rows


def summarize_capacity(raw_rows: list[dict], curve_rows: list[dict], horizon: float, seeds: list[int], n_values: list[int], method: str, p1_algorithm: str, amr_count: int) -> list[dict]:
    satisfied = [row for row in curve_rows if int(row["capacity_satisfied_all"]) == 1]
    n_capacity = max((int(row["n"]) for row in satisfied), default=None)
    first_crossing = next((int(row["n"]) for row in curve_rows if int(row["capacity_satisfied_all"]) == 0), None)
    status = "bounded" if first_crossing is not None else "no_crossing_in_test_range"
    capacity_cmax = ""
    if n_capacity is not None:
        capacity_cmax = next(row["Cmax_median"] for row in curve_rows if int(row["n"]) == n_capacity)
    max_solve_time = max((as_float(row, "solve_time") for row in raw_rows), default=0.0)
    total_solve_time = sum(as_float(row, "solve_time") for row in raw_rows)
    return [
        {
            "H": round(float(horizon), 6),
            "capacity_rule": "all_seeds",
            "n_capacity": "" if n_capacity is None else n_capacity,
            "first_crossing_n": "" if first_crossing is None else first_crossing,
            "status": status,
            "capacity_Cmax_median": capacity_cmax,
            "max_tested_n": max(n_values),
            "min_tested_n": min(n_values),
            "seed_count": len(seeds),
            "seeds": ",".join(str(seed) for seed in seeds),
            "method": method,
            "p1_algorithm": p1_algorithm,
            "amr_count": amr_count,
            "raw_case_count": len(raw_rows),
            "max_solve_time": round(max_solve_time, 6),
            "total_solve_time": round(total_solve_time, 6),
            "source": "p5_stage3_self_run",
        }
    ]


def append_result_validations(raw_rows: list[dict], curve_rows: list[dict], seeds: list[int], n_values: list[int], validation_rows: list[dict], errors: list[str]) -> None:
    required = {(seed, n) for seed in seeds for n in n_values}
    present = {(as_int(row, "seed"), as_int(row, "n")) for row in raw_rows}
    missing = sorted(required - present)
    if missing:
        errors.append(f"missing stage3 cases: {missing}")
        validation_rows.append({"check": "stage3 case coverage", "status": "FAIL", "detail": str(missing)})
    else:
        validation_rows.append({"check": "stage3 case coverage", "status": "PASS", "detail": f"{len(required)} cases"})

    bad_source = [row.get("run_id", "") for row in raw_rows if row.get("source") != "p5_stage3_self_run"]
    if bad_source:
        errors.append("stage3 raw rows contain non-P5 sources")
        validation_rows.append({"check": "P5 self-run source", "status": "FAIL", "detail": str(bad_source[:5])})
    else:
        validation_rows.append({"check": "P5 self-run source", "status": "PASS", "detail": "no P2 experiment result reuse"})

    if len(curve_rows) != len(n_values):
        errors.append("capacity curve n coverage mismatch")
        validation_rows.append({"check": "capacity curve coverage", "status": "FAIL", "detail": f"{len(curve_rows)} rows"})
    else:
        validation_rows.append({"check": "capacity curve coverage", "status": "PASS", "detail": f"{len(curve_rows)} n-levels"})

    invalid_cmax = []
    missing_path_cases = []
    energy_violation_cases = []
    for row in raw_rows:
        label = row.get("run_id", "")
        if as_int(row, "missing_path_count") != 0:
            missing_path_cases.append(label)
        if as_int(row, "energy_violation_count") != 0:
            energy_violation_cases.append(label)
        if not math.isfinite(as_float(row, "Cmax")) or as_float(row, "Cmax") <= 0:
            invalid_cmax.append(label)

    if invalid_cmax:
        errors.append(f"invalid Cmax cases: {invalid_cmax[:8]}")
        validation_rows.append({"check": "Cmax numeric validity", "status": "FAIL", "detail": str(invalid_cmax[:8])})
    else:
        validation_rows.append({"check": "Cmax numeric validity", "status": "PASS", "detail": f"{len(raw_rows)} rows"})

    # Missing paths and energy violations are legitimate high-load infeasible
    # observations for capacity analysis. They set feasible=0 and can cause a
    # capacity crossing, but should not make the P5 experiment invalid.
    validation_rows.append(
        {
            "check": "high-load missing path observations",
            "status": "PASS",
            "detail": f"{len(missing_path_cases)} infeasible observations recorded",
        }
    )
    validation_rows.append(
        {
            "check": "high-load energy observations",
            "status": "PASS",
            "detail": f"{len(energy_violation_cases)} infeasible observations recorded",
        }
    )


def plot_capacity_boundary(curve_rows: list[dict], summary_rows: list[dict], output_png: Path, output_pdf: Path) -> None:
    configure_chinese_style()
    n_values = np.array([int(row["n"]) for row in curve_rows], dtype=float)
    cmax_min = np.array([float(row["Cmax_min"]) for row in curve_rows], dtype=float)
    cmax_median = np.array([float(row["Cmax_median"]) for row in curve_rows], dtype=float)
    cmax_max = np.array([float(row["Cmax_max"]) for row in curve_rows], dtype=float)
    summary = summary_rows[0]
    horizon = float(summary["H"])
    n_capacity = None if is_blank(summary.get("n_capacity")) else int(float(summary["n_capacity"]))
    first_crossing = None if is_blank(summary.get("first_crossing_n")) else int(float(summary["first_crossing_n"]))

    fig, ax = plt.subplots(figsize=(7.2, 4.5), constrained_layout=True)
    main_color = "#356d9b"
    band_color = "#9db8cf"
    horizon_color = "#b64b4b"
    feasible_color = "#2f855a"
    crossing_color = "#d95f02"
    grid_color = "#d7dce2"

    ax.fill_between(n_values, cmax_min, cmax_max, color=band_color, alpha=0.26, linewidth=0, label="min-max 范围")
    ax.plot(n_values, cmax_median, color=main_color, marker="o", linewidth=2.0, markersize=4.2, label="中位数")
    ax.axhline(horizon, color=horizon_color, linestyle="--", linewidth=1.35, label=f"H={horizon:g}s")

    y_span = max(cmax_max.max() - min(cmax_min.min(), horizon), 1.0)
    if n_capacity is not None:
        cap_row = next(row for row in curve_rows if int(row["n"]) == n_capacity)
        cap_y = float(cap_row["Cmax_median"])
        ax.scatter([n_capacity], [cap_y], s=82, facecolor="white", edgecolor=feasible_color, linewidth=1.8, zorder=5, label="最后可行")
        ax.annotate(
            f"容量 n={n_capacity}",
            xy=(n_capacity, cap_y),
            xytext=(n_capacity + 0.45, cap_y - 0.16 * y_span),
            arrowprops={"arrowstyle": "->", "color": feasible_color, "lw": 1.0},
            color=feasible_color,
            fontsize=9,
            ha="left",
            va="top",
        )
    if first_crossing is not None:
        cross_row = next(row for row in curve_rows if int(row["n"]) == first_crossing)
        cross_y = float(cross_row["Cmax_median"])
        ax.scatter([first_crossing], [cross_y], s=76, facecolor="white", edgecolor=crossing_color, linewidth=1.8, zorder=5, label="首次越界")
        ax.annotate(
            f"首次越界 n={first_crossing}",
            xy=(first_crossing, cross_y),
            xytext=(first_crossing + 0.45, cross_y + 0.12 * y_span),
            arrowprops={"arrowstyle": "->", "color": crossing_color, "lw": 1.0},
            color=crossing_color,
            fontsize=9,
            ha="left",
            va="bottom",
        )
    else:
        ax.text(
            0.98,
            0.08,
            "测试范围内未达到容量边界",
            transform=ax.transAxes,
            color="#243447",
            fontsize=9,
            ha="right",
            va="bottom",
        )

    ax.set_title("固定 4 台 AMR 下的任务负荷容量边界")
    ax.set_xlabel("任务数量 n")
    ax.set_ylabel("最大完工时间 Cmax（秒）")
    ax.set_xticks(n_values[:: max(1, int(math.ceil(len(n_values) / 12)))])
    ax.grid(axis="y", color=grid_color, linewidth=0.8, alpha=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    handles, labels = ax.get_legend_handles_labels()
    unique = {}
    for handle, label in zip(handles, labels):
        unique[label] = handle
    ax.legend(unique.values(), unique.keys(), frameon=False, loc="upper left", fontsize=8)

    output_png.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=300, bbox_inches="tight")
    fig.savefig(output_pdf, bbox_inches="tight")
    plt.close(fig)

    REPORT_FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(output_png, REPORT_FIGURE_DIR / "task_capacity.png")
    shutil.copyfile(output_pdf, REPORT_FIGURE_DIR / "task_capacity.pdf")


def main():
    args = parse_args()
    seeds = SMOKE_SEEDS if args.smoke else parse_seeds(args.seeds)
    max_n = min(args.max_n, SMOKE_MAX_N) if args.smoke else args.max_n
    n_values = list(range(1, max_n + 1))
    if max_n < 2:
        raise ValueError("--max-n must be at least 2")
    if args.method == "m2":
        patch_pulp_cbc_path_if_needed()

    raw_csv = P5_DIR / "stage3_task_sweep_raw.csv"
    validation_rows: list[dict] = []
    errors: list[str] = []
    if args.skip_run and raw_csv.exists():
        raw_rows = read_csv_rows(raw_csv)
        validation_rows.append({"check": "raw result reuse", "status": "PASS", "detail": str(raw_csv)})
    else:
        raw_rows, validation_rows, errors = run_stage3_experiments(
            seeds=seeds,
            n_values=n_values,
            method=args.method,
            p1_algorithm=args.p1_algorithm,
            amr_count=args.amr_count,
            horizon=args.horizon,
            time_limit=args.time_limit,
        )
        write_csv_rows(raw_csv, raw_rows, RAW_FIELDNAMES)

    curve_rows = build_capacity_curve(raw_rows)
    summary_rows = summarize_capacity(
        raw_rows=raw_rows,
        curve_rows=curve_rows,
        horizon=args.horizon,
        seeds=seeds,
        n_values=n_values,
        method=args.method,
        p1_algorithm=args.p1_algorithm,
        amr_count=args.amr_count,
    )
    append_result_validations(raw_rows, curve_rows, seeds, n_values, validation_rows, errors)

    P5_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv_rows(P5_DIR / "stage3_task_capacity_curve.csv", curve_rows, CURVE_FIELDNAMES)
    write_csv_rows(P5_DIR / "stage3_capacity_summary.csv", summary_rows, list(summary_rows[0].keys()))
    write_csv_rows(P5_DIR / "stage3_validation_log.csv", validation_rows, ["check", "status", "detail"])

    output_png = OUT_DIR / "stage3_capacity_boundary.png"
    output_pdf = OUT_DIR / "stage3_capacity_boundary.pdf"
    plot_capacity_boundary(curve_rows, summary_rows, output_png, output_pdf)

    report = {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "source": "p5_stage3_self_run",
        "method": args.method,
        "p1_algorithm": args.p1_algorithm,
        "amr_count": args.amr_count,
        "H": args.horizon,
        "capacity_rule": "all_seeds",
        "raw_result_csv": str(raw_csv),
        "capacity_curve_csv": str(P5_DIR / "stage3_task_capacity_curve.csv"),
        "capacity_summary_csv": str(P5_DIR / "stage3_capacity_summary.csv"),
        "figure_png": str(output_png),
        "figure_pdf": str(output_pdf),
        "summary": summary_rows[0],
    }
    with (P5_DIR / "stage3_validation_report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    if errors:
        print("Stage 3 validation failed:")
        for error in errors:
            print(f"- {error}")
        print(f"Validation log: {P5_DIR / 'stage3_validation_log.csv'}")
        return 1

    print("Stage 3 validation passed.")
    print(f"Raw sweep: {raw_csv}")
    print(f"Curve: {P5_DIR / 'stage3_task_capacity_curve.csv'}")
    print(f"Summary: {P5_DIR / 'stage3_capacity_summary.csv'}")
    print(f"Figure: {output_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
