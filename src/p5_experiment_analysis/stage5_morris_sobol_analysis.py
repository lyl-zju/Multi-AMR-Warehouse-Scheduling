from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[2] / ".mplconfig"))

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from p5_utils import configure_chinese_style, is_blank, read_csv_rows, write_csv_rows


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
P1_DIR = PROJECT_ROOT / "data" / "processed" / "p1" / "mixed"
P5_ROOT = PROJECT_ROOT / "data" / "processed" / "p5"
P5_DIR = P5_ROOT / "stage5_morris_sobol"
STAGE4_DIR = P5_ROOT / "stage4_bottleneck_cluster"
OUT_DIR = PROJECT_ROOT / "outputs" / "p5"
REPORT_FIGURE_DIR = WORKSPACE_ROOT / "report" / "figures"
TASK_STAGE_DIR = WORKSPACE_ROOT / "task" / "stage5"

P2_DIR = PROJECT_ROOT / "src" / "p2_assignment"
P3_DIR = PROJECT_ROOT / "src" / "p3_schedule_conflicts"
sys.path.insert(0, str(P2_DIR))
sys.path.insert(0, str(P3_DIR))

from assign_and_sequence_tasks import run as run_p2  # noqa: E402
import schedule_and_detect_conflicts as p3  # noqa: E402

from stage3_task_capacity_analysis import (  # noqa: E402
    AMR_FIELDNAMES,
    TASK_FIELDNAMES,
    build_amr_rows,
    make_nested_task_pool,
    patch_pulp_cbc_path_if_needed,
    write_csv_dicts,
)
from stage4_bottleneck_cluster_analysis import (  # noqa: E402
    parse_cell_id,
    relaxed_detector_factory,
)


try:
    from SALib.sample import morris as morris_sample
    from SALib.sample import sobol as sobol_sample
    from SALib.analyze import morris as morris_analyze
    from SALib.analyze import sobol as sobol_analyze
except Exception as exc:  # pragma: no cover - explicit runtime dependency check
    raise RuntimeError(
        "SALib is required for Stage5. Install it with: python3 -m pip install SALib"
    ) from exc


FACTORS = ["m", "n", "cap_cluster"]
LEVELS = {
    "m": [3, 4, 5, 6],
    "n": [12, 13, 14, 15],
    "cap_cluster": [1, 2, 3],
}
PROBLEM = {
    "num_vars": len(FACTORS),
    "names": FACTORS,
    "bounds": [[0.0, 1.0] for _ in FACTORS],
}
DEFAULT_SEED = 1
DEFAULT_POOL_AMR_COUNT = 4
DEFAULT_METHOD = "m2"
DEFAULT_P1_ALGORITHM = "mixed"
DEFAULT_P2_TIME_LIMIT = 30
DEFAULT_P3_MODE = "wait_reroute_resequence"
DEFAULT_MORRIS_TRAJECTORIES = 10
DEFAULT_MORRIS_LEVELS = 4
DEFAULT_SOBOL_N = 64
DEFAULT_SALIB_SEED = 20260619
SMOKE_MORRIS_TRAJECTORIES = 2
SMOKE_SOBOL_N = 8
POOL_AMR_COUNT = max(LEVELS["m"])


RUN_FIELDNAMES = [
    "run_key",
    "seed",
    "m",
    "n",
    "cap_cluster",
    "cluster_id",
    "method",
    "p1_algorithm",
    "p3_mode",
    "Cmax",
    "total_delay",
    "remaining_conflicts",
    "total_wait_added",
    "solve_time_p2",
    "solve_time_p3",
    "solve_time_total",
    "missing_path_count",
    "missing_transition_count",
    "missing_loaded_path_count",
    "energy_violation_count",
    "p2_feasible",
    "conflict_free",
    "feasible",
    "case_tasks_csv",
    "case_amrs_csv",
    "case_output_dir",
    "source",
]


SAMPLE_FIELDNAMES = [
    "sample_id",
    "design",
    "x_m",
    "x_n",
    "x_cap_cluster",
    "m",
    "n",
    "cap_cluster",
    "run_key",
    "Cmax",
    "total_delay",
    "remaining_conflicts",
    "feasible",
    "mapping_rule",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P5 stage5 Morris and Sobol global sensitivity analysis.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Nested task-pool seed.")
    parser.add_argument("--method", choices=["m2"], default=DEFAULT_METHOD, help="P2 method.")
    parser.add_argument("--p1-algorithm", choices=["mixed"], default=DEFAULT_P1_ALGORITHM, help="P1 path source.")
    parser.add_argument("--p2-time-limit", type=int, default=DEFAULT_P2_TIME_LIMIT, help="P2 time limit per case.")
    parser.add_argument("--p3-mode", choices=["wait", "wait_reroute", "wait_reroute_resequence"], default=DEFAULT_P3_MODE)
    parser.add_argument("--morris-trajectories", type=int, default=DEFAULT_MORRIS_TRAJECTORIES)
    parser.add_argument("--morris-levels", type=int, default=DEFAULT_MORRIS_LEVELS)
    parser.add_argument("--sobol-n", type=int, default=DEFAULT_SOBOL_N)
    parser.add_argument("--salib-seed", type=int, default=DEFAULT_SALIB_SEED)
    parser.add_argument("--skip-run", action="store_true", help="Reuse existing Stage5 runs and only rebuild analysis outputs.")
    parser.add_argument("--smoke", action="store_true", help="Use small sample counts for pipeline validation.")
    return parser.parse_args()


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def as_float(value, default=0.0) -> float:
    if value is None or str(value).strip() == "":
        return default
    return float(value)


def as_int(value, default=0) -> int:
    if value is None or str(value).strip() == "":
        return default
    return int(float(value))


def run_key(seed: int, m: int, n: int, cap_cluster: int) -> str:
    return f"s{seed}_m{m:02d}_n{n:02d}_cap{cap_cluster}"


def relative_to_project(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def map_to_level(value: float, levels: list[int]) -> int:
    clipped = min(max(float(value), 0.0), 1.0)
    if len(levels) == 1:
        return int(levels[0])
    idx = int(round(clipped * (len(levels) - 1)))
    idx = min(max(idx, 0), len(levels) - 1)
    return int(levels[idx])


def map_sample_row(row: np.ndarray, design: str, sample_id: int, seed: int) -> dict:
    mapped = {name: map_to_level(row[idx], LEVELS[name]) for idx, name in enumerate(FACTORS)}
    key = run_key(seed, mapped["m"], mapped["n"], mapped["cap_cluster"])
    return {
        "sample_id": sample_id,
        "design": design,
        "x_m": round(float(row[0]), 10),
        "x_n": round(float(row[1]), 10),
        "x_cap_cluster": round(float(row[2]), 10),
        "m": mapped["m"],
        "n": mapped["n"],
        "cap_cluster": mapped["cap_cluster"],
        "run_key": key,
        "Cmax": "",
        "total_delay": "",
        "remaining_conflicts": "",
        "feasible": "",
        "mapping_rule": "round unit-hypercube coordinate to nearest configured discrete level",
    }


def build_samples(args: argparse.Namespace) -> tuple[np.ndarray, list[dict], np.ndarray, list[dict]]:
    morris_trajectories = SMOKE_MORRIS_TRAJECTORIES if args.smoke else args.morris_trajectories
    sobol_n = SMOKE_SOBOL_N if args.smoke else args.sobol_n

    morris_x = morris_sample.sample(
        PROBLEM,
        N=morris_trajectories,
        num_levels=args.morris_levels,
        seed=args.salib_seed,
    )
    sobol_x = sobol_sample.sample(
        PROBLEM,
        N=sobol_n,
        calc_second_order=True,
        seed=args.salib_seed + 17,
    )

    morris_rows = [
        map_sample_row(row, "morris", idx + 1, args.seed)
        for idx, row in enumerate(morris_x)
    ]
    sobol_rows = [
        map_sample_row(row, "sobol", idx + 1, args.seed)
        for idx, row in enumerate(sobol_x)
    ]
    return morris_x, morris_rows, sobol_x, sobol_rows


def load_stage4_cluster() -> dict:
    report_path = STAGE4_DIR / "stage4_validation_report.json"
    tests_path = STAGE4_DIR / "stage4_capacity_tests.csv"
    clusters_path = STAGE4_DIR / "stage4_clusters.csv"
    if not report_path.exists():
        raise RuntimeError("stage4_validation_report.json not found; run stage4 first")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "PASS":
        raise RuntimeError(f"stage4 validation is not PASS: {report.get('status')}")
    if not tests_path.exists():
        raise RuntimeError(f"stage4 capacity tests not found: {tests_path}")

    tests = read_csv_dicts(tests_path)
    ranked = [row for row in tests if as_int(row.get("rank")) == 1]
    if not ranked:
        ranked = sorted(tests, key=lambda row: as_float(row.get("Delta_Cmax")), reverse=True)[:1]
    if not ranked:
        raise RuntimeError("stage4 capacity tests are empty")
    best = ranked[0]
    cluster_id = str(best.get("cluster_id", "")).strip()
    cell_ids = str(best.get("cell_ids", "")).strip()
    if not cluster_id or not cell_ids:
        raise RuntimeError("best stage4 cluster is missing cluster_id or cell_ids")

    clusters = read_csv_dicts(clusters_path) if clusters_path.exists() else []
    cluster_row = next((row for row in clusters if str(row.get("cluster_id", "")) == cluster_id), {})
    return {
        "cluster_id": cluster_id,
        "cell_ids": cell_ids,
        "cluster_cells": {parse_cell_id(value) for value in cell_ids.split(";") if value},
        "stage4_delta_Cmax": as_float(best.get("Delta_Cmax")),
        "nearest_zone": best.get("nearest_zone", cluster_row.get("nearest_zone", "")),
        "nearest_obstacle_id": best.get("nearest_obstacle_id", cluster_row.get("nearest_obstacle_id", "")),
    }


def prepare_case_inputs(seed: int, m: int, n: int, pool: list[dict]) -> tuple[Path, Path]:
    case_dir = P5_DIR / "stage5_inputs" / f"s{seed}" / f"m_{m:02d}_n_{n:02d}"
    case_dir.mkdir(parents=True, exist_ok=True)
    task_rows = [dict(row) for row in pool[:n]]
    amr_rows = build_amr_rows(m)
    write_csv_dicts(case_dir / "tasks.csv", task_rows, TASK_FIELDNAMES)
    write_csv_dicts(case_dir / "amrs.csv", amr_rows, AMR_FIELDNAMES)
    return case_dir / "tasks.csv", case_dir / "amrs.csv"


def p3_metrics_from_result(result: dict) -> dict:
    metrics = p3.compute_metrics(result["schedule"], result["conflicts"], result["state"])
    return {
        "remaining_conflicts": int(metrics["remaining_conflicts"]),
        "total_delay": round(float(metrics["total_delay"]), 6),
        "Cmax": round(float(metrics["cmax"]), 6),
        "total_wait_added": round(float(metrics["total_wait_added"]), 6),
    }


def run_p3_with_cluster_capacity(
    assignment: pd.DataFrame,
    trajectory_samples: pd.DataFrame,
    path_cost: pd.DataFrame,
    cluster_cells: set[tuple[int, int]],
    cap_cluster: int,
    mode: str,
) -> dict:
    original_detector = p3.detect_trajectory_conflicts
    p3.detect_trajectory_conflicts = relaxed_detector_factory(cluster_cells, int(cap_cluster))
    try:
        result = p3.run_repair_mode(assignment, trajectory_samples, path_cost, mode)
        return p3.compress_repair_result(result, trajectory_samples)
    finally:
        p3.detect_trajectory_conflicts = original_detector


def run_single_case(
    seed: int,
    m: int,
    n: int,
    cap_cluster: int,
    pool: list[dict],
    cluster: dict,
    method: str,
    p1_algorithm: str,
    p2_time_limit: int,
    p3_mode: str,
) -> dict:
    key = run_key(seed, m, n, cap_cluster)
    tasks_csv, amrs_csv = prepare_case_inputs(seed, m, n, pool)
    out_dir = P5_DIR / "stage5_p2_outputs" / key
    start_total = time.perf_counter()
    data, _solution, evaluation, p2_seconds = run_p2(
        method=method,
        p1_algorithm=p1_algorithm,
        time_limit=p2_time_limit,
        tasks_csv=tasks_csv,
        amrs_csv=amrs_csv,
        out_dir=out_dir,
        write_outputs=True,
    )
    metrics = evaluation.metrics
    missing_path_count = int(metrics["missing_transition_count"]) + int(metrics["missing_loaded_path_count"])
    p2_feasible = int(
        len(data.task_ids) == n
        and missing_path_count == 0
        and int(metrics["energy_violation_count"]) == 0
        and math.isfinite(float(metrics["Cmax"]))
    )

    assignment = pd.read_csv(out_dir / "assignment_result.csv")
    trajectory_samples = pd.read_csv(P1_DIR / "path_trajectory_samples.csv")
    path_cost = pd.read_csv(P1_DIR / "path_cost.csv")
    p3_start = time.perf_counter()
    p3_result = run_p3_with_cluster_capacity(
        assignment=assignment,
        trajectory_samples=trajectory_samples,
        path_cost=path_cost,
        cluster_cells=cluster["cluster_cells"],
        cap_cluster=cap_cluster,
        mode=p3_mode,
    )
    p3_seconds = time.perf_counter() - p3_start
    p3_metrics = p3_metrics_from_result(p3_result)
    total_seconds = time.perf_counter() - start_total
    conflict_free = int(p3_metrics["remaining_conflicts"] == 0)
    # High-load Stage4 already records residual conflicts as an audited model
    # outcome. Keep that outcome instead of replacing it with a penalty value.
    feasible = int(p2_feasible == 1 and math.isfinite(p3_metrics["Cmax"]) and p3_metrics["Cmax"] > 0)

    return {
        "run_key": key,
        "seed": seed,
        "m": m,
        "n": n,
        "cap_cluster": cap_cluster,
        "cluster_id": cluster["cluster_id"],
        "method": method,
        "p1_algorithm": p1_algorithm,
        "p3_mode": p3_mode,
        "Cmax": p3_metrics["Cmax"],
        "total_delay": p3_metrics["total_delay"],
        "remaining_conflicts": p3_metrics["remaining_conflicts"],
        "total_wait_added": p3_metrics["total_wait_added"],
        "solve_time_p2": round(float(p2_seconds), 6),
        "solve_time_p3": round(float(p3_seconds), 6),
        "solve_time_total": round(float(total_seconds), 6),
        "missing_path_count": missing_path_count,
        "missing_transition_count": int(metrics["missing_transition_count"]),
        "missing_loaded_path_count": int(metrics["missing_loaded_path_count"]),
        "energy_violation_count": int(metrics["energy_violation_count"]),
        "p2_feasible": p2_feasible,
        "conflict_free": conflict_free,
        "feasible": feasible,
        "case_tasks_csv": relative_to_project(tasks_csv),
        "case_amrs_csv": relative_to_project(amrs_csv),
        "case_output_dir": relative_to_project(out_dir),
        "source": "p5_stage5_true_p2_p3_run",
    }


def load_existing_runs(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = read_csv_rows(path)
    return {str(row.get("run_key", "")): row for row in rows if row.get("run_key")}


def unique_sample_keys(*sample_sets: list[dict]) -> list[tuple[int, int, int, str]]:
    seen = set()
    out = []
    for rows in sample_sets:
        for row in rows:
            key = (int(row["m"]), int(row["n"]), int(row["cap_cluster"]), str(row["run_key"]))
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return sorted(out, key=lambda item: (item[1], item[0], item[2]))


def run_missing_cases(args: argparse.Namespace, morris_rows: list[dict], sobol_rows: list[dict], cluster: dict) -> list[dict]:
    runs_path = P5_DIR / "stage5_all_runs_cache.csv"
    cache = load_existing_runs(runs_path)
    if args.skip_run:
        missing = [row["run_key"] for row in morris_rows + sobol_rows if row["run_key"] not in cache]
        if missing:
            raise RuntimeError(f"--skip-run requested but Stage5 cache is missing cases: {sorted(set(missing))[:10]}")
    else:
        max_n = max(max(LEVELS["n"]), max(int(row["n"]) for row in morris_rows + sobol_rows))
        pool = make_nested_task_pool(max_n=max_n, seed=args.seed, amr_count=POOL_AMR_COUNT)
        for idx, (m, n, cap_cluster, key) in enumerate(unique_sample_keys(morris_rows, sobol_rows), start=1):
            if key in cache:
                continue
            print(f"[stage5] running {idx:02d}: seed={args.seed}, m={m}, n={n}, cap={cap_cluster}", flush=True)
            row = run_single_case(
                seed=args.seed,
                m=m,
                n=n,
                cap_cluster=cap_cluster,
                pool=pool,
                cluster=cluster,
                method=args.method,
                p1_algorithm=args.p1_algorithm,
                p2_time_limit=args.p2_time_limit,
                p3_mode=args.p3_mode,
            )
            cache[key] = {field: row.get(field, "") for field in RUN_FIELDNAMES}
            write_csv_rows(runs_path, list(cache.values()), RUN_FIELDNAMES)

    required_keys = {row["run_key"] for row in morris_rows + sobol_rows}
    rows = [cache[key] for key in sorted(required_keys)]
    return rows


def attach_outputs(sample_rows: list[dict], run_map: dict[str, dict]) -> list[dict]:
    out = []
    for row in sample_rows:
        item = dict(row)
        run = run_map[str(row["run_key"])]
        item["Cmax"] = run["Cmax"]
        item["total_delay"] = run["total_delay"]
        item["remaining_conflicts"] = run["remaining_conflicts"]
        item["feasible"] = run["feasible"]
        out.append(item)
    return out


def validate_run_rows(rows: list[dict]) -> list[str]:
    errors = []
    for row in rows:
        label = row.get("run_key", "")
        cmax = as_float(row.get("Cmax"), float("nan"))
        if not math.isfinite(cmax) or cmax <= 0:
            errors.append(f"{label}: invalid Cmax={row.get('Cmax')}")
        if as_int(row.get("missing_path_count")) != 0:
            errors.append(f"{label}: missing_path_count={row.get('missing_path_count')}")
        if as_int(row.get("missing_transition_count")) != 0:
            errors.append(f"{label}: missing_transition_count={row.get('missing_transition_count')}")
        if as_int(row.get("missing_loaded_path_count")) != 0:
            errors.append(f"{label}: missing_loaded_path_count={row.get('missing_loaded_path_count')}")
        if as_int(row.get("energy_violation_count")) != 0:
            errors.append(f"{label}: energy_violation_count={row.get('energy_violation_count')}")
        if as_int(row.get("feasible")) != 1:
            errors.append(f"{label}: infeasible P2/P3 response")
    return errors


def analyze_morris(x_values: np.ndarray, sample_rows: list[dict], num_levels: int, seed: int) -> list[dict]:
    y = np.array([float(row["Cmax"]) for row in sample_rows], dtype=float)
    result = morris_analyze.analyze(
        PROBLEM,
        x_values,
        y,
        num_levels=num_levels,
        print_to_console=False,
        seed=seed,
    )
    rows = []
    for idx, name in enumerate(FACTORS):
        rows.append(
            {
                "parameter": name,
                "mu_star": round(float(result["mu_star"][idx]), 6),
                "sigma": round(float(result["sigma"][idx]), 6),
                "mu_star_conf": round(float(result["mu_star_conf"][idx]), 6),
                "sample_count": len(sample_rows),
                "unit": "seconds of Cmax",
            }
        )
    return rows


def analyze_sobol(sample_rows: list[dict], seed: int) -> list[dict]:
    y = np.array([float(row["Cmax"]) for row in sample_rows], dtype=float)
    result = sobol_analyze.analyze(
        PROBLEM,
        y,
        calc_second_order=True,
        print_to_console=False,
        seed=seed,
    )
    rows = []
    for idx, name in enumerate(FACTORS):
        rows.append(
            {
                "parameter": name,
                "S1": round(float(result["S1"][idx]), 6),
                "S1_conf": round(float(result["S1_conf"][idx]), 6),
                "ST": round(float(result["ST"][idx]), 6),
                "ST_conf": round(float(result["ST_conf"][idx]), 6),
                "sample_count": len(sample_rows),
                "unit": "variance share of Cmax",
            }
        )
    return rows


def plot_morris(rows: list[dict], output_png: Path, output_pdf: Path) -> None:
    configure_chinese_style()
    x = np.array([float(row["mu_star"]) for row in rows], dtype=float)
    y = np.array([float(row["sigma"]) for row in rows], dtype=float)
    xerr = np.array([float(row["mu_star_conf"]) for row in rows], dtype=float)
    labels = [row["parameter"] for row in rows]

    fig, ax = plt.subplots(figsize=(6.7, 4.7), constrained_layout=True)
    color = "#356d9b"
    ax.errorbar(x, y, xerr=xerr, fmt="o", color=color, ecolor="#8ba9c2", capsize=4, markersize=7)
    for label, xi, yi in zip(labels, x, y):
        ax.annotate(label, (xi, yi), xytext=(6, 5), textcoords="offset points", fontsize=10, color="#111827")
    ax.set_xlim(left=0.0)
    ax.set_ylim(bottom=0.0)
    ax.set_xlabel(r"$\mu^\star$（Cmax 秒）")
    ax.set_ylabel(r"$\sigma$（Cmax 秒）")
    ax.set_title("Stage5 Morris：影响强度与非线性/交互筛选")
    ax.grid(True, linestyle=":", alpha=0.30)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=300, bbox_inches="tight")
    fig.savefig(output_pdf, bbox_inches="tight")
    plt.close(fig)


def plot_sobol(rows: list[dict], output_png: Path, output_pdf: Path) -> None:
    configure_chinese_style()
    ordered = sorted(rows, key=lambda row: float(row["ST"]), reverse=True)
    labels = [row["parameter"] for row in ordered]
    y_pos = np.arange(len(labels))
    s1 = np.array([float(row["S1"]) for row in ordered], dtype=float)
    st = np.array([float(row["ST"]) for row in ordered], dtype=float)
    s1_err = np.array([float(row["S1_conf"]) for row in ordered], dtype=float)
    st_err = np.array([float(row["ST_conf"]) for row in ordered], dtype=float)

    fig, ax = plt.subplots(figsize=(6.9, 4.5), constrained_layout=True)
    height = 0.34
    ax.barh(y_pos + height / 2, st, height=height, xerr=st_err, color="#c2410c", alpha=0.78, label="总效应 ST")
    ax.barh(y_pos - height / 2, s1, height=height, xerr=s1_err, color="#356d9b", alpha=0.82, label="一阶效应 S1")
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.axvline(0, color="#111827", linewidth=0.8)
    ax.set_xlabel("Sobol 指数（Cmax 方差份额）")
    ax.set_title("Stage5 Sobol：一阶与总效应方差分解")
    ax.grid(axis="x", linestyle=":", alpha=0.28)
    ax.legend(frameon=False, loc="lower right")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=300, bbox_inches="tight")
    fig.savefig(output_pdf, bbox_inches="tight")
    plt.close(fig)


def write_task_docs(morris_rows: list[dict], sobol_rows: list[dict], run_rows: list[dict], cluster: dict) -> None:
    TASK_STAGE_DIR.mkdir(parents=True, exist_ok=True)
    best_morris = max(morris_rows, key=lambda row: float(row["mu_star"]))
    best_sobol = max(sobol_rows, key=lambda row: float(row["ST"]))
    conflict_rows = [row for row in run_rows if as_int(row.get("remaining_conflicts")) > 0]
    code_text = f"""# Stage5 Morris + Sobol 代码说明

## 输入与口径

- 因子：`m`, `n`, `cap_cluster`
- 固定 seed：`{DEFAULT_SEED}`
- P2 方法：`m2`
- P1 路径源：`mixed`
- P3 修复模式：`wait_reroute_resequence`
- 瓶颈簇：`{cluster['cluster_id']}`，cell_ids=`{cluster['cell_ids']}`

## 实现流程

1. 使用 SALib 生成 Morris 与 Sobol 连续样本。
2. 将连续样本映射到离散水平：`m={LEVELS['m']}`, `n={LEVELS['n']}`, `cap_cluster={LEVELS['cap_cluster']}`。
3. 对唯一 `(m,n,cap_cluster,seed)` 组合调用真实 P2 + P3 流程。
4. P3 冲突检测器在 `{cluster['cluster_id']}` 上使用样本给定的局部容量。
5. 回填完整样本序列并分别计算 Morris `mu_star/sigma` 与 Sobol `S1/ST`。

## 输出

数据位于 `Multi-AMR-Warehouse-Scheduling/data/processed/p5/`，图位于
`Multi-AMR-Warehouse-Scheduling/outputs/p5/`，并同步到 `report/figures/`。
"""
    conclusion_text = f"""# Stage5 全局敏感度结论

Morris 最大影响因子为 `{best_morris['parameter']}`，`mu_star={best_morris['mu_star']}` 秒。
Sobol 总效应最大因子为 `{best_sobol['parameter']}`，`ST={best_sobol['ST']}`。

本阶段共缓存真实重跑 `{len(run_rows)}` 个唯一工况。`remaining_conflicts>0` 的工况数为
`{len(conflict_rows)}`；这些值保留为模型输出事实，没有用惩罚值或均值补齐。

Stage5 的结果只用于筛选主导因素，不替代 Stage6 中“加 AMR 还是扩瓶颈簇容量”的直接重优化对比。
"""
    (TASK_STAGE_DIR / "code_explanation.md").write_text(code_text, encoding="utf-8")
    (TASK_STAGE_DIR / "conclusion.md").write_text(conclusion_text, encoding="utf-8")


def write_report_section(morris_rows: list[dict], sobol_rows: list[dict], run_rows: list[dict], cluster: dict) -> None:
    section_path = WORKSPACE_ROOT / "report" / "sections" / "global_sensitivity.tex"
    best_morris = max(morris_rows, key=lambda row: float(row["mu_star"]))
    best_sobol = max(sobol_rows, key=lambda row: float(row["ST"]))
    run_count = len(run_rows)
    conflict_count = sum(1 for row in run_rows if as_int(row.get("remaining_conflicts")) > 0)
    morris_table = "\n".join(
        f"{row['parameter']} & {float(row['mu_star']):.3f} & {float(row['sigma']):.3f} & {float(row['mu_star_conf']):.3f} \\\\"
        for row in morris_rows
    )
    sobol_table = "\n".join(
        f"{row['parameter']} & {float(row['S1']):.3f} & {float(row['S1_conf']):.3f} & {float(row['ST']):.3f} & {float(row['ST_conf']):.3f} \\\\"
        for row in sobol_rows
    )
    text = rf"""\subsection{{Morris 与 Sobol 全局敏感度筛选}}

在单因素边际分析之后，本节将 AMR 数量、任务负荷和瓶颈簇容量放入同一个参数空间中进行全局筛选。响应变量仍为最大完工时间 $\Cmax$，不构造新的综合指标。参数向量为
\[
\theta=(m,n,q_C),
\]
其中 $m\in\{{3,4,5,6\}}$，$n\in\{{12,13,14,15\}}$，$q_C\in\{{1,2,3\}}$。瓶颈簇 $C$ 自动读取自 Stage 4 的最终容量放松排名，本轮为 {cluster['cluster_id']}，对应网格 {cluster['cell_ids']}。

所有样本均调用真实 P2--P3 流程：先用固定 seed=1 的嵌套任务池生成任务前缀，再按 $m$ 生成 AMR 输入，随后执行 M2 任务分配并在 P3 冲突检测中将 {cluster['cluster_id']} 的局部容量设置为 $q_C$。Morris 与 Sobol 的连续样本只用于设计实验点；实际求解前均映射到上述离散水平，并记录原始样本、映射值和运行结果。本阶段共形成 {run_count} 个唯一真实重跑工况，其中 {conflict_count} 个高负荷工况仍保留非零冲突记录；这些记录作为模型输出事实写入 CSV，不使用惩罚值或均值补齐。

\begin{{center}}
\small
\captionof{{table}}{{Morris 全局筛选结果}}
\label{{tab:stage5-morris}}
\begin{{tabular}}{{@{{}}lccc@{{}}}}
\toprule
因素 & $\mu^\star$ & $\sigma$ & $\mu^\star$ 置信宽度 \\
\midrule
{morris_table}
\bottomrule
\end{{tabular}}
\end{{center}}

\begin{{center}}
\includegraphics[width=0.72\linewidth]{{stage5_morris}}
\captionof{{figure}}{{Morris 影响强度与非线性/交互筛选}}
\label{{fig:stage5-morris}}
\end{{center}}

表~\ref{{tab:stage5-morris}} 和图~\ref{{fig:stage5-morris}} 显示，Morris 筛选中影响强度最大的因素是 {best_morris['parameter']}，其 $\mu^\star={float(best_morris['mu_star']):.3f}$ 秒。$\sigma$ 反映该因素在不同运行工况下的非线性或交互特征，因此不能仅按单因素曲线解释全局响应。

\begin{{center}}
\small
\captionof{{table}}{{Sobol 方差分解结果}}
\label{{tab:stage5-sobol}}
\begin{{tabular}}{{@{{}}lcccc@{{}}}}
\toprule
因素 & $S_1$ & $S_1$ 置信宽度 & $S_T$ & $S_T$ 置信宽度 \\
\midrule
{sobol_table}
\bottomrule
\end{{tabular}}
\end{{center}}

\begin{{center}}
\includegraphics[width=0.72\linewidth]{{stage5_sobol}}
\captionof{{figure}}{{Sobol 一阶效应与总效应}}
\label{{fig:stage5-sobol}}
\end{{center}}

Sobol 结果中总效应最大的因素为 {best_sobol['parameter']}，$S_T={float(best_sobol['ST']):.3f}$。该结果用于验证全局主导因素和交互强度，不直接替代 Stage 6 的工程干预重优化；后续仍需在同一高负荷实例下直接比较增加 AMR 与提升瓶颈簇容量对 $\Cmax$ 的实际改善。
"""
    section_path.write_text(text, encoding="utf-8")


def ensure_main_tex_input() -> None:
    main_path = WORKSPACE_ROOT / "report" / "main.tex"
    text = main_path.read_text(encoding="utf-8")
    marker = "\\input{sections/global_sensitivity}"
    if marker in text:
        return
    text = text.replace(
        "\\input{sections/bottleneck_cluster}\n",
        "\\input{sections/bottleneck_cluster}\n\\input{sections/global_sensitivity}\n",
    )
    main_path.write_text(text, encoding="utf-8")


def main() -> int:
    args = parse_args()
    patch_pulp_cbc_path_if_needed()
    cluster = load_stage4_cluster()
    morris_x, morris_rows, _sobol_x, sobol_rows = build_samples(args)

    P5_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv_rows(P5_DIR / "stage5_morris_samples.csv", morris_rows, SAMPLE_FIELDNAMES)
    write_csv_rows(P5_DIR / "stage5_sobol_samples.csv", sobol_rows, SAMPLE_FIELDNAMES)

    run_rows = run_missing_cases(args, morris_rows, sobol_rows, cluster)
    run_map = {str(row["run_key"]): row for row in run_rows}
    morris_rows = attach_outputs(morris_rows, run_map)
    sobol_rows = attach_outputs(sobol_rows, run_map)
    write_csv_rows(P5_DIR / "stage5_morris_samples.csv", morris_rows, SAMPLE_FIELDNAMES)
    write_csv_rows(P5_DIR / "stage5_sobol_samples.csv", sobol_rows, SAMPLE_FIELDNAMES)
    write_csv_rows(P5_DIR / "stage5_morris_runs.csv", run_rows, RUN_FIELDNAMES)
    write_csv_rows(P5_DIR / "stage5_sobol_runs.csv", run_rows, RUN_FIELDNAMES)

    errors = validate_run_rows(run_rows)
    if errors:
        report = {"status": "FAIL", "errors": errors, "cluster": cluster}
        (P5_DIR / "stage5_validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        for error in errors[:10]:
            print(f"[stage5] ERROR {error}", flush=True)
        return 1

    morris_results = analyze_morris(morris_x, morris_rows, args.morris_levels, args.salib_seed)
    sobol_results = analyze_sobol(sobol_rows, args.salib_seed + 31)
    write_csv_rows(P5_DIR / "stage5_morris_results.csv", morris_results, ["parameter", "mu_star", "sigma", "mu_star_conf", "sample_count", "unit"])
    write_csv_rows(P5_DIR / "stage5_sobol_results.csv", sobol_results, ["parameter", "S1", "S1_conf", "ST", "ST_conf", "sample_count", "unit"])

    morris_png = OUT_DIR / "stage5_morris.png"
    morris_pdf = OUT_DIR / "stage5_morris.pdf"
    sobol_png = OUT_DIR / "stage5_sobol.png"
    sobol_pdf = OUT_DIR / "stage5_sobol.pdf"
    plot_morris(morris_results, morris_png, morris_pdf)
    plot_sobol(sobol_results, sobol_png, sobol_pdf)
    REPORT_FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(morris_png, REPORT_FIGURE_DIR / "stage5_morris.png")
    shutil.copyfile(morris_pdf, REPORT_FIGURE_DIR / "stage5_morris.pdf")
    shutil.copyfile(sobol_png, REPORT_FIGURE_DIR / "stage5_sobol.png")
    shutil.copyfile(sobol_pdf, REPORT_FIGURE_DIR / "stage5_sobol.pdf")

    write_task_docs(morris_results, sobol_results, run_rows, cluster)
    write_report_section(morris_results, sobol_results, run_rows, cluster)
    ensure_main_tex_input()

    report = {
        "status": "PASS",
        "errors": [],
        "seed": args.seed,
        "method": args.method,
        "p1_algorithm": args.p1_algorithm,
        "p3_mode": args.p3_mode,
        "levels": LEVELS,
        "cluster": {
            key: value
            for key, value in cluster.items()
            if key != "cluster_cells"
        },
        "morris_sample_count": len(morris_rows),
        "sobol_sample_count": len(sobol_rows),
        "unique_run_count": len(run_rows),
        "conflict_nonzero_count": sum(1 for row in run_rows if as_int(row.get("remaining_conflicts")) > 0),
        "outputs": {
            "morris_samples": str(P5_DIR / "stage5_morris_samples.csv"),
            "morris_runs": str(P5_DIR / "stage5_morris_runs.csv"),
            "morris_results": str(P5_DIR / "stage5_morris_results.csv"),
            "sobol_samples": str(P5_DIR / "stage5_sobol_samples.csv"),
            "sobol_runs": str(P5_DIR / "stage5_sobol_runs.csv"),
            "sobol_results": str(P5_DIR / "stage5_sobol_results.csv"),
            "morris_figure": str(morris_png),
            "sobol_figure": str(sobol_png),
        },
    }
    (P5_DIR / "stage5_validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[stage5] PASS unique_runs={len(run_rows)} morris_samples={len(morris_rows)} sobol_samples={len(sobol_rows)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
