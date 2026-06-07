from collections import defaultdict
from itertools import islice
from pathlib import Path

import networkx as nx
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"

K_CANDIDATE_PATHS = 3
TURN_COST_PER_TURN = 0.5
RISK_WEIGHT = 1.0
NARROW_EDGE_PENALTY = 1.0
DYNAMIC_EDGE_PENALTY = 2.0
LOCKABLE_EDGE_PENALTY = 1.0


def load_raw_data():
    nodes = pd.read_csv(RAW_DATA_DIR / "nodes.csv")
    edges = pd.read_csv(RAW_DATA_DIR / "edges.csv")
    amrs = pd.read_csv(RAW_DATA_DIR / "amrs.csv")
    tasks = pd.read_csv(RAW_DATA_DIR / "tasks.csv")
    return nodes, edges, amrs, tasks


def edge_total_weight(edge):
    weight = float(edge.travel_time) + RISK_WEIGHT * float(edge.risk)
    if edge.edge_type == "narrow":
        weight += NARROW_EDGE_PENALTY
    if edge.edge_type == "dynamic":
        weight += DYNAMIC_EDGE_PENALTY
    if int(edge.lockable) == 1:
        weight += LOCKABLE_EDGE_PENALTY
    return weight


def build_graph(edges):
    graph = nx.DiGraph()

    for edge in edges.itertuples(index=False):
        attrs = {
            "edge_id": edge.edge_id,
            "distance": float(edge.length),
            "travel_time": float(edge.travel_time),
            "capacity": int(edge.capacity),
            "risk": float(edge.risk),
            "edge_type": edge.edge_type,
            "directed": int(edge.directed),
            "lockable": int(edge.lockable),
            "weight_time": float(edge.travel_time),
            "weight_total": edge_total_weight(edge),
        }
        graph.add_edge(edge.from_node, edge.to_node, **attrs)

        if int(edge.directed) == 0:
            reverse_attrs = attrs.copy()
            reverse_attrs["reverse"] = 1
            graph.add_edge(edge.to_node, edge.from_node, **reverse_attrs)

    return graph


def build_key_nodes(nodes, amrs, tasks):
    sources = defaultdict(set)

    for node_id in amrs["init_node"].dropna():
        sources[node_id].add("amr_init")
    for node_id in tasks["pickup"].dropna():
        sources[node_id].add("pickup")
    for node_id in tasks["delivery"].dropna():
        sources[node_id].add("delivery")
    for node_id in nodes.loc[nodes["node_type"] == "charger", "node_id"]:
        sources[node_id].add("charger")

    source_order = ["amr_init", "pickup", "delivery", "charger"]
    records = []
    for node in nodes.itertuples(index=False):
        if node.node_id not in sources:
            continue
        ordered_sources = [name for name in source_order if name in sources[node.node_id]]
        records.append(
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "x": node.x,
                "y": node.y,
                "source": ";".join(ordered_sources),
            }
        )

    return pd.DataFrame(records)


def turn_count_for_path(node_sequence, positions):
    turns = 0
    for i in range(1, len(node_sequence) - 1):
        x0, y0 = positions[node_sequence[i - 1]]
        x1, y1 = positions[node_sequence[i]]
        x2, y2 = positions[node_sequence[i + 1]]
        dx1, dy1 = x1 - x0, y1 - y0
        dx2, dy2 = x2 - x1, y2 - y1
        cross = dx1 * dy2 - dy1 * dx2
        if abs(cross) > 1e-9:
            turns += 1
    return turns


def edge_occupancy_offsets(graph, node_sequence):
    offsets = []
    elapsed_time = 0.0

    for step_index, (from_node, to_node) in enumerate(zip(node_sequence[:-1], node_sequence[1:])):
        edge = graph[from_node][to_node]
        start_offset = elapsed_time
        end_offset = elapsed_time + edge["travel_time"]
        offsets.append(
            {
                "step_index": step_index,
                "edge_id": edge["edge_id"],
                "from_node_on_edge": from_node,
                "to_node_on_edge": to_node,
                "offset_start": round(start_offset, 3),
                "offset_end": round(end_offset, 3),
                "travel_time": round(edge["travel_time"], 3),
                "capacity": edge["capacity"],
                "edge_type": edge["edge_type"],
                "lockable": edge["lockable"],
            }
        )
        elapsed_time = end_offset

    return offsets


def format_edge_occupancy_offset(occupancy_rows):
    return ";".join(
        f"{row['edge_id']}@{row['offset_start']:g}-{row['offset_end']:g}"
        for row in occupancy_rows
    )


def node_occupancy_offsets(graph, node_sequence):
    offsets = []
    elapsed_time = 0.0

    offsets.append(
        {
            "node_index": 0,
            "node_id": node_sequence[0],
            "offset_time": round(elapsed_time, 3),
        }
    )

    for node_index, (from_node, to_node) in enumerate(zip(node_sequence[:-1], node_sequence[1:]), start=1):
        edge = graph[from_node][to_node]
        elapsed_time += edge["travel_time"]
        offsets.append(
            {
                "node_index": node_index,
                "node_id": to_node,
                "offset_time": round(elapsed_time, 3),
            }
        )

    return offsets


def format_node_occupancy_offset(occupancy_rows):
    return ";".join(
        f"{row['node_id']}@{row['offset_time']:g}"
        for row in occupancy_rows
    )


def path_metrics(graph, node_sequence, positions):
    edge_ids = []
    distance = 0.0
    travel_time = 0.0
    risk_cost = 0.0
    narrow_count = 0
    dynamic_count = 0
    lockable_count = 0
    capacities = []
    edge_weight_total = 0.0

    for from_node, to_node in zip(node_sequence[:-1], node_sequence[1:]):
        edge = graph[from_node][to_node]
        edge_ids.append(edge["edge_id"])
        distance += edge["distance"]
        travel_time += edge["travel_time"]
        risk_cost += edge["risk"]
        narrow_count += int(edge["edge_type"] == "narrow")
        dynamic_count += int(edge["edge_type"] == "dynamic")
        lockable_count += int(edge["lockable"] == 1)
        capacities.append(edge["capacity"])
        edge_weight_total += edge["weight_total"]

    turn_count = turn_count_for_path(node_sequence, positions)
    turn_cost = turn_count * TURN_COST_PER_TURN
    total_cost = edge_weight_total + turn_cost

    edge_occupancy_rows = edge_occupancy_offsets(graph, node_sequence)
    node_occupancy_rows = node_occupancy_offsets(graph, node_sequence)

    return {
        "node_sequence": "->".join(node_sequence),
        "edge_sequence": "->".join(edge_ids),
        "edge_occupancy_offset": format_edge_occupancy_offset(edge_occupancy_rows),
        "node_occupancy_offset": format_node_occupancy_offset(node_occupancy_rows),
        "edge_occupancy_rows": edge_occupancy_rows,
        "node_occupancy_rows": node_occupancy_rows,
        "distance": round(distance, 3),
        "travel_time": round(travel_time, 3),
        "turn_count": turn_count,
        "turn_cost": round(turn_cost, 3),
        "risk_cost": round(risk_cost, 3),
        "narrow_count": narrow_count,
        "dynamic_count": dynamic_count,
        "lockable_count": lockable_count,
        "bottleneck_capacity": min(capacities) if capacities else None,
        "total_cost": round(total_cost, 3),
    }


def add_path(candidate_map, node_sequence, algorithm):
    key = tuple(node_sequence)
    candidate_map[key].add(algorithm)


def generate_paths_for_pair(graph, source, target):
    candidates = defaultdict(set)

    for weight_name, algorithm in [
        ("weight_time", "dijkstra_time"),
        ("weight_total", "dijkstra_total"),
    ]:
        try:
            path = nx.shortest_path(graph, source=source, target=target, weight=weight_name)
            add_path(candidates, path, algorithm)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            pass

    try:
        iterator = nx.shortest_simple_paths(graph, source, target, weight="weight_total")
        for path in islice(iterator, K_CANDIDATE_PATHS):
            add_path(candidates, path, "k_shortest_total")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        pass

    return candidates


def generate_path_cost_outputs(graph, key_nodes, nodes):
    positions = {row.node_id: (float(row.x), float(row.y)) for row in nodes.itertuples(index=False)}
    key_node_ids = key_nodes["node_id"].tolist()
    path_records = []
    occupancy_records = []
    node_occupancy_records = []

    for from_node in key_node_ids:
        for to_node in key_node_ids:
            if from_node == to_node:
                continue
            candidates = generate_paths_for_pair(graph, from_node, to_node)
            pair_records = []
            for node_sequence, algorithms in candidates.items():
                metrics = path_metrics(graph, list(node_sequence), positions)
                metrics.update(
                    {
                        "from_node": from_node,
                        "to_node": to_node,
                        "algorithm": "+".join(sorted(algorithms)),
                    }
                )
                pair_records.append(metrics)

            pair_records.sort(key=lambda item: (item["total_cost"], item["travel_time"], item["distance"]))
            for rank, record in enumerate(pair_records, start=1):
                path_uid = f"{from_node}__{to_node}__path_{rank}"
                record["path_uid"] = path_uid
                record["path_id"] = f"path_{rank}"
                record["rank_by_total"] = rank
                for occupancy_row in record["edge_occupancy_rows"]:
                    occupancy_records.append(
                        {
                            "path_uid": path_uid,
                            "from_node": from_node,
                            "to_node": to_node,
                            "path_id": record["path_id"],
                            **occupancy_row,
                        }
                    )
                for occupancy_row in record["node_occupancy_rows"]:
                    node_occupancy_records.append(
                        {
                            "path_uid": path_uid,
                            "from_node": from_node,
                            "to_node": to_node,
                            "path_id": record["path_id"],
                            **occupancy_row,
                        }
                    )
                record.pop("edge_occupancy_rows")
                record.pop("node_occupancy_rows")
                path_records.append(record)

    columns = [
        "path_uid",
        "from_node",
        "to_node",
        "path_id",
        "rank_by_total",
        "algorithm",
        "node_sequence",
        "edge_sequence",
        "edge_occupancy_offset",
        "node_occupancy_offset",
        "distance",
        "travel_time",
        "turn_count",
        "turn_cost",
        "risk_cost",
        "narrow_count",
        "dynamic_count",
        "lockable_count",
        "bottleneck_capacity",
        "total_cost",
    ]
    occupancy_columns = [
        "path_uid",
        "from_node",
        "to_node",
        "path_id",
        "step_index",
        "edge_id",
        "from_node_on_edge",
        "to_node_on_edge",
        "offset_start",
        "offset_end",
        "travel_time",
        "capacity",
        "edge_type",
        "lockable",
    ]
    node_occupancy_columns = [
        "path_uid",
        "from_node",
        "to_node",
        "path_id",
        "node_index",
        "node_id",
        "offset_time",
    ]
    return (
        pd.DataFrame(path_records, columns=columns),
        pd.DataFrame(occupancy_records, columns=occupancy_columns),
        pd.DataFrame(node_occupancy_records, columns=node_occupancy_columns),
    )


def build_cost_matrix(path_cost, key_nodes, value_column):
    node_ids = key_nodes["node_id"].tolist()
    best_paths = (
        path_cost.sort_values(["from_node", "to_node", value_column, "total_cost", "travel_time"])
        .drop_duplicates(["from_node", "to_node"], keep="first")
    )
    matrix = best_paths.pivot(index="from_node", columns="to_node", values=value_column)
    matrix = matrix.reindex(index=node_ids, columns=node_ids)

    for node_id in node_ids:
        matrix.loc[node_id, node_id] = 0.0

    return matrix


def main():
    nodes, edges, amrs, tasks = load_raw_data()
    graph = build_graph(edges)
    key_nodes = build_key_nodes(nodes, amrs, tasks)
    path_cost, path_edge_occupancy, path_node_occupancy = generate_path_cost_outputs(graph, key_nodes, nodes)

    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_nodes.to_csv(PROCESSED_DATA_DIR / "key_nodes.csv", index=False)
    path_cost.to_csv(PROCESSED_DATA_DIR / "path_cost.csv", index=False)
    path_edge_occupancy.to_csv(PROCESSED_DATA_DIR / "path_edge_occupancy.csv", index=False)
    path_node_occupancy.to_csv(PROCESSED_DATA_DIR / "path_node_occupancy.csv", index=False)

    matrix_time = build_cost_matrix(path_cost, key_nodes, "travel_time")
    matrix_total = build_cost_matrix(path_cost, key_nodes, "total_cost")
    matrix_time.to_csv(PROCESSED_DATA_DIR / "path_cost_matrix_time.csv")
    matrix_total.to_csv(PROCESSED_DATA_DIR / "path_cost_matrix_total.csv")

    print(f"Key nodes: {len(key_nodes)}")
    print(f"Candidate paths: {len(path_cost)}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'key_nodes.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'path_cost.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'path_edge_occupancy.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'path_node_occupancy.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'path_cost_matrix_time.csv'}")
    print(f"Saved: {PROCESSED_DATA_DIR / 'path_cost_matrix_total.csv'}")


if __name__ == "__main__":
    main()
