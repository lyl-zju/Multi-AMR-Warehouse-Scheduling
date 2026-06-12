from math import hypot
from pathlib import Path
import warnings

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
P3_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "p3"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "p4"

RESCHEDULE_METHOD = "rolling_horizon_2d_interface_stub"
CHANGE_THRESHOLD = 1e-9


def load_inputs():
    schedule = pd.read_csv(P3_DATA_DIR / "schedule_result.csv")
    trajectory_schedule = pd.read_csv(P3_DATA_DIR / "trajectory_schedule.csv")
    dynamic_events = pd.read_csv(RAW_DATA_DIR / "dynamic_events.csv")
    return schedule, trajectory_schedule, dynamic_events


def to_float(value, default=None):
    if value is None or pd.isna(value) or str(value) == "":
        return default
    return float(value)


def overlaps(start_a, end_a, start_b, end_b):
    if start_a is None or end_a is None or start_b is None or end_b is None:
        return False
    return start_a < end_b and end_a > start_b


def point_to_rect_distance(x, y, rect_x, rect_y, width, height):
    dx = max(rect_x - x, 0.0, x - (rect_x + width))
    dy = max(rect_y - y, 0.0, y - (rect_y + height))
    return hypot(dx, dy)


def append_unique_text(old_text, new_text):
    parts = []
    for text in [old_text, new_text]:
        if text is None or pd.isna(text) or str(text) == "":
            continue
        for part in str(text).split(";"):
            clean = part.strip()
            if clean and clean not in parts:
                parts.append(clean)
    return "; ".join(parts)


def initialize_reschedule_table(schedule):
    table = schedule.copy()
    table["old_amr"] = table["amr_id"]
    table["new_amr"] = table["amr_id"]
    table["old_start"] = pd.to_numeric(table["start_time"], errors="coerce")
    table["new_start"] = table["old_start"]
    table["old_finish"] = pd.to_numeric(table["finish_time"], errors="coerce")
    table["new_finish"] = table["old_finish"]
    table["changed"] = 0
    table["affected_event_ids"] = ""
    table["change_reason"] = ""
    table["delay_added"] = 0.0
    table["reschedule_status"] = table["schedule_status"]
    table["reschedule_method"] = RESCHEDULE_METHOD
    return table


def mark_and_shift_tasks(table, event_id, amr_id, min_sequence_order, delay, reason):
    if delay <= CHANGE_THRESHOLD:
        return table

    mask = (table["new_amr"] == amr_id) & (table["sequence_order"].astype(float) >= min_sequence_order)
    shiftable = mask & table["new_start"].notna() & table["new_finish"].notna()

    table.loc[mask, "affected_event_ids"] = table.loc[mask, "affected_event_ids"].apply(
        lambda value: append_unique_text(value, event_id)
    )
    table.loc[mask, "change_reason"] = table.loc[mask, "change_reason"].apply(
        lambda value: append_unique_text(value, reason)
    )
    table.loc[mask, "changed"] = 1
    table.loc[shiftable, "new_start"] = table.loc[shiftable, "new_start"] + delay
    table.loc[shiftable, "new_finish"] = table.loc[shiftable, "new_finish"] + delay
    table.loc[shiftable, "delay_added"] = table.loc[shiftable, "delay_added"] + delay
    table.loc[mask & ~shiftable, "reschedule_status"] = "missing_path_after_event"
    return table


def impact_row(event, affected, impact_reason):
    if affected.empty:
        affected_tasks = ""
        affected_amrs = ""
        impact_count = 0
    else:
        affected_tasks = ";".join(sorted(set(affected["task_id"].astype(str))))
        affected_amrs = ";".join(sorted(set(affected["amr_id"].astype(str))))
        impact_count = len(set(affected["task_id"].astype(str)))

    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "start_time": event.start_time,
        "end_time": event.end_time,
        "affected_tasks": affected_tasks,
        "affected_amrs": affected_amrs,
        "impact_count": impact_count,
        "impact_reason": impact_reason,
    }


def process_area_block(table, trajectory_schedule, event):
    event_start = to_float(event.start_time)
    event_end = to_float(event.end_time)
    rect_x = to_float(event.x)
    rect_y = to_float(event.y)
    width = to_float(event.width)
    height = to_float(event.height)

    if None in [event_start, event_end, rect_x, rect_y, width, height]:
        empty = pd.DataFrame(columns=["task_id", "amr_id"])
        return table, impact_row(event, empty, "area block missing geometry or time window")

    window = trajectory_schedule[
        trajectory_schedule["absolute_time"].apply(
            lambda value: overlaps(to_float(value), to_float(value), event_start, event_end)
            or event_start <= to_float(value, event_start - 1.0) <= event_end
        )
    ].copy()

    if window.empty:
        return table, impact_row(event, window, "no trajectory samples in area block time window")

    affected = window[
        window.apply(
            lambda row: point_to_rect_distance(
                to_float(row["x"]),
                to_float(row["y"]),
                rect_x,
                rect_y,
                width,
                height,
            )
            <= to_float(row["footprint_radius"], 0.0),
            axis=1,
        )
    ].copy()

    if affected.empty:
        return table, impact_row(event, affected, "no trajectory footprint intersects blocked area")

    grouped = (
        affected.groupby(["amr_id", "task_id", "sequence_order"], as_index=False)
        .agg({"absolute_time": "min"})
        .sort_values(["amr_id", "sequence_order"])
    )
    for row in grouped.itertuples(index=False):
        delay = max(0.0, event_end - float(row.absolute_time))
        reason = f"area_block:{event.target_id}"
        table = mark_and_shift_tasks(table, event.event_id, row.amr_id, float(row.sequence_order), delay, reason)

    return table, impact_row(event, affected, f"blocked area {event.target_id} intersects trajectory samples")


def process_amr_delay(table, event):
    event_start = to_float(event.start_time)
    event_end = to_float(event.end_time)
    delay = max(0.0, event_end - event_start)
    amr_id = event.target_id

    affected = table[
        (table["new_amr"] == amr_id)
        & (
            (table["new_start"].fillna(float("inf")) >= event_start)
            | (
                (table["new_start"].fillna(float("inf")) < event_end)
                & (table["new_finish"].fillna(float("-inf")) > event_start)
            )
        )
    ].copy()

    if affected.empty:
        return table, impact_row(event, affected, "no task overlaps delayed AMR window")

    min_sequence_order = float(affected["sequence_order"].astype(float).min())
    table = mark_and_shift_tasks(
        table,
        event.event_id,
        amr_id,
        min_sequence_order,
        delay,
        f"amr_delay:{amr_id}",
    )
    return table, impact_row(event, affected, f"AMR {amr_id} delayed by {delay:g} time units")


def add_new_task(table, event):
    release_time = to_float(event.release_time, 0.0)
    service_time = to_float(event.service_time, 0.0)
    scheduled = table[table["new_finish"].notna()]

    if scheduled.empty:
        chosen_amr = table["new_amr"].dropna().sort_values().iloc[0]
        new_start = release_time
    else:
        finish_by_amr = scheduled.groupby("new_amr")["new_finish"].max().sort_values()
        chosen_amr = finish_by_amr.index[0]
        new_start = max(release_time, float(finish_by_amr.iloc[0]))

    sequence_order = int(table.loc[table["new_amr"] == chosen_amr, "sequence_order"].astype(float).max()) + 1
    new_finish = new_start + service_time

    new_row = {column: None for column in table.columns}
    new_row.update(
        {
            "amr_id": chosen_amr,
            "task_id": event.target_id,
            "sequence_order": sequence_order,
            "pickup": event.pickup,
            "delivery": event.delivery,
            "start_time": None,
            "finish_time": None,
            "path_id": None,
            "path_uid": None,
            "old_amr": "",
            "new_amr": chosen_amr,
            "old_start": None,
            "new_start": new_start,
            "old_finish": None,
            "new_finish": new_finish,
            "delay": None,
            "is_delayed": None,
            "changed": 1,
            "affected_event_ids": event.event_id,
            "change_reason": "new_task_insert_stub",
            "delay_added": 0.0,
            "reschedule_status": "new_task_inserted_stub",
            "reschedule_method": RESCHEDULE_METHOD,
            "notes": "new dynamic task inserted with placeholder logic",
        }
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        table.loc[len(table)] = new_row

    affected = pd.DataFrame([{"task_id": event.target_id, "amr_id": chosen_amr}])
    return table, impact_row(event, affected, "new task inserted with placeholder assignment")


def apply_dynamic_events(schedule, trajectory_schedule, dynamic_events):
    table = initialize_reschedule_table(schedule)
    impact_rows = []

    for event in dynamic_events.itertuples(index=False):
        if event.event_type == "area_block":
            table, impact = process_area_block(table, trajectory_schedule, event)
        elif event.event_type == "amr_delay":
            table, impact = process_amr_delay(table, event)
        elif event.event_type == "new_task":
            table, impact = add_new_task(table, event)
        else:
            empty = pd.DataFrame(columns=["task_id", "amr_id"])
            impact = impact_row(event, empty, "unsupported event type")
        impact_rows.append(impact)

    return table, pd.DataFrame(impact_rows)


def build_summary(original_schedule, reschedule_result, event_impact):
    original_finish = pd.to_numeric(original_schedule["finish_time"], errors="coerce")
    new_finish = pd.to_numeric(reschedule_result["new_finish"], errors="coerce")
    changed = pd.to_numeric(reschedule_result["changed"], errors="coerce").fillna(0)
    delay_added = pd.to_numeric(reschedule_result["delay_added"], errors="coerce").fillna(0.0)

    rows = [
        {
            "metric": "OriginalScheduledTasks",
            "value": int((original_schedule["schedule_status"] == "scheduled").sum()),
            "description": "Number of tasks with a valid pre-event schedule",
        },
        {
            "metric": "DynamicEvents",
            "value": len(event_impact),
            "description": "Number of dynamic events loaded from dynamic_events.csv",
        },
        {
            "metric": "AffectedEvents",
            "value": int((event_impact["impact_count"] > 0).sum()),
            "description": "Number of dynamic events that affected at least one task",
        },
        {
            "metric": "ChangedTasks",
            "value": int((changed > 0).sum()),
            "description": "Number of tasks changed or inserted by the placeholder rescheduler",
        },
        {
            "metric": "NewTasks",
            "value": int((reschedule_result["reschedule_status"] == "new_task_inserted_stub").sum()),
            "description": "Number of new tasks inserted after dynamic events",
        },
        {
            "metric": "OriginalCmax",
            "value": float(original_finish.max()) if original_finish.notna().any() else None,
            "description": "Maximum original finish time",
        },
        {
            "metric": "NewCmax",
            "value": float(new_finish.max()) if new_finish.notna().any() else None,
            "description": "Maximum finish time after placeholder rescheduling",
        },
        {
            "metric": "TotalAddedDelay",
            "value": float(delay_added.sum()),
            "description": "Total time shift added by dynamic event handling",
        },
    ]
    return pd.DataFrame(rows)


def select_output_columns(table):
    columns = [
        "task_id",
        "old_amr",
        "new_amr",
        "old_start",
        "new_start",
        "old_finish",
        "new_finish",
        "changed",
        "affected_event_ids",
        "change_reason",
        "delay_added",
        "reschedule_status",
        "reschedule_method",
        "sequence_order",
        "pickup",
        "delivery",
        "path_uid",
        "transition_path_uid",
        "loaded_path_uid",
        "notes",
    ]
    return table.reindex(columns=columns)


def main():
    schedule, trajectory_schedule, dynamic_events = load_inputs()
    reschedule_table, event_impact = apply_dynamic_events(schedule, trajectory_schedule, dynamic_events)
    reschedule_result = select_output_columns(reschedule_table)
    summary = build_summary(schedule, reschedule_result, event_impact)

    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    reschedule_result.to_csv(PROCESSED_DATA_DIR / "reschedule_result.csv", index=False)
    event_impact.to_csv(PROCESSED_DATA_DIR / "dynamic_event_impact.csv", index=False)
    summary.to_csv(PROCESSED_DATA_DIR / "reschedule_summary.csv", index=False)

    print(f"Dynamic events: {len(dynamic_events)}")
    print(f"Changed or inserted tasks: {(reschedule_result['changed'].astype(float) > 0).sum()}")
    print(f"Event impact rows: {len(event_impact)}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'reschedule_result.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'dynamic_event_impact.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'reschedule_summary.csv'}")


if __name__ == "__main__":
    main()
