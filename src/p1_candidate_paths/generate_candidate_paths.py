import argparse

from planners import avg, basic_astar, davg, vg
from planners.common import (
    DEFAULT_P1_ALGORITHM,
    IMPLEMENTED_ALGORITHMS,
    P1_ALGORITHMS,
    add_manual_endpoint_nodes,
    build_key_nodes,
    load_raw_data,
    p1_processed_dir,
    save_outputs,
)


PLANNER_GENERATORS = {
    "basic_astar": basic_astar.generate_outputs,
    "vg": vg.generate_outputs,
    "avg": avg.generate_outputs,
    "davg": davg.generate_outputs,
}


def parse_args():
    parser = argparse.ArgumentParser(description="Generate P1 candidate paths with a selected planner.")
    parser.add_argument(
        "--algorithm",
        choices=P1_ALGORITHMS,
        default=DEFAULT_P1_ALGORITHM,
        help="P1 path planning method to generate.",
    )
    parser.add_argument("--from-node", help="Optional source node for generating one path only.")
    parser.add_argument("--to-node", help="Optional target node for generating one path only.")
    return parser.parse_args()


def main():
    args = parse_args()
    algorithm = args.algorithm
    if algorithm not in IMPLEMENTED_ALGORITHMS:
        raise SystemExit(
            f"P1 algorithm '{algorithm}' is not implemented yet. "
            "Available generators now: basic_astar, vg, avg, davg."
        )
    if bool(args.from_node) != bool(args.to_node):
        raise SystemExit("Specify both --from-node and --to-node, or neither.")

    nodes, amrs, tasks, _, obstacles = load_raw_data()
    key_nodes = build_key_nodes(nodes, amrs, tasks)
    key_nodes = add_manual_endpoint_nodes(key_nodes, nodes, args.from_node, args.to_node)
    outputs, messages = PLANNER_GENERATORS[algorithm](
        obstacles,
        key_nodes,
        from_node=args.from_node,
        to_node=args.to_node,
    )

    processed_data_dir = p1_processed_dir(algorithm)

    # P1 planner outputs:
    # - key_nodes.csv: important AMR/task/charger nodes used as path endpoints.
    # - path_cost.csv: one best path per ordered endpoint pair for this algorithm.
    # - path_grid_cells.csv: Grid A* cells or VG waypoints used by each path.
    # - path_trajectory_samples.csv: time-stamped 2D trajectory samples for P3/P4 migration.
    # - path_cost_matrix_time.csv: best travel-time matrix for P2.
    # - path_cost_matrix_total.csv: best total-cost matrix for P2.
    path_cost = save_outputs(processed_data_dir, key_nodes, outputs)

    for message in messages:
        print(message)
    print(f"Algorithm: {algorithm}")
    if args.from_node and args.to_node:
        print(f"Pair mode: {args.from_node} -> {args.to_node}")
        print("Note: pair mode overwrites this algorithm's P1 outputs with one path only.")
    print(f"Key nodes: {len(key_nodes)}")
    print(f"Candidate paths: {len(path_cost)}")
    print(f"Saved: {processed_data_dir / 'key_nodes.csv'}")
    print(f"Saved: {processed_data_dir / 'path_cost.csv'}")
    print(f"Saved: {processed_data_dir / 'path_grid_cells.csv'}")
    print(f"Saved: {processed_data_dir / 'path_trajectory_samples.csv'}")
    print(f"Saved: {processed_data_dir / 'path_cost_matrix_time.csv'}")
    print(f"Saved: {processed_data_dir / 'path_cost_matrix_total.csv'}")


if __name__ == "__main__":
    main()
