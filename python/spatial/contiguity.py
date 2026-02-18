"""
Contiguity enforcement for graph-partitioned spatial data.

After balanced graph partitioning (e.g., PyMetis), a single label may end up
split into spatially disconnected components. This module provides
`repair_noncontiguous_parts` to greedily reassign orphan components to
neighboring labels, preserving population balance.
"""

from collections import deque, defaultdict


def _components(nodes, adj_list):
    """Return connected components induced by `nodes` in the adjacency graph.

    Parameters
    ----------
    nodes : iterable of int
        Subset of node IDs to consider.
    adj_list : list of list of int
        Full adjacency list for every node in the graph.

    Returns
    -------
    list of list of int
        Each inner list is one connected component (node IDs).
    """
    nodes = list(nodes)
    idx = {u: i for i, u in enumerate(nodes)}
    seen = set()
    comps = []
    for u in nodes:
        if u in seen:
            continue
        q = deque([u])
        seen.add(u)
        comp = [u]
        while q:
            x = q.popleft()
            for y in adj_list[x]:
                if y in idx and y not in seen:
                    seen.add(y)
                    q.append(y)
                    comp.append(y)
        comps.append(comp)
    return comps


def _label_pop(parts, weights):
    """Return a dict mapping each label to its total population weight."""
    totals = defaultdict(int)
    for i, p in enumerate(parts):
        totals[p] += weights[i]
    return totals


def _border_size(comp_nodes, parts, adj_list, neighbor_label):
    """Count edges from *comp_nodes* into nodes labelled *neighbor_label*.

    This serves as a rough proxy for the length of the shared spatial boundary.
    """
    s = set(comp_nodes)
    count = 0
    for u in comp_nodes:
        for v in adj_list[u]:
            if v not in s and parts[v] == neighbor_label:
                count += 1
    return count


def repair_noncontiguous_parts(adj_list, parts, weights, *, verbose=False, max_passes=5):
    """Enforce spatial contiguity on a graph partition.

    For every label that is split into multiple connected components, keeps
    the largest component and greedily reassigns orphan components to the
    adjacent label that minimises population imbalance (tie-broken by border
    size).

    Parameters
    ----------
    adj_list : list of list of int
        Adjacency list — ``adj_list[i]`` is the list of neighbours of node *i*.
    parts : list of int
        Label assignment for each node (e.g., from PyMetis).
    weights : list of int | float
        Population (or other balance metric) for each node.
    verbose : bool, optional
        Print diagnostic messages when components are reassigned.
    max_passes : int, optional
        Maximum number of full sweeps over labels (default 5).

    Returns
    -------
    list of int
        A new label assignment where each label forms a single connected
        component (when possible — isolated components with no adjacent
        alternative label are left in place).
    """
    print(f"[spatial] repair_noncontiguous_parts: {len(parts)} nodes, {len(set(parts))} labels")
    n = len(parts)
    parts = list(parts)
    weights = list(weights)

    for _ in range(max_passes):
        changed_any = False
        label_pop = _label_pop(parts, weights)

        labels = sorted(set(parts))
        for lbl in labels:
            nodes_lbl = [i for i in range(n) if parts[i] == lbl]
            if not nodes_lbl:
                continue
            comps = _components(nodes_lbl, adj_list)
            if len(comps) <= 1:
                continue

            # Keep the largest component; reassign the rest
            comps.sort(key=len, reverse=True)
            for comp in comps[1:]:
                comp_pop = sum(weights[i] for i in comp)

                # Candidate neighbour labels touching this component
                neighbor_labels = set()
                for u in comp:
                    for v in adj_list[u]:
                        if parts[v] != lbl:
                            neighbor_labels.add(parts[v])
                if not neighbor_labels:
                    if verbose:
                        print(
                            f"[contig-fix] label {lbl}: orphan component "
                            f"has no neighbouring labels; skipped"
                        )
                    continue

                # Choose neighbour that best balances populations
                best = None
                best_q = None
                for q in neighbor_labels:
                    pop_lbl_new = label_pop[lbl] - comp_pop
                    pop_q_new = label_pop[q] + comp_pop
                    imbalance = abs(pop_lbl_new - pop_q_new)
                    border = _border_size(comp, parts, adj_list, q)
                    cand = (imbalance, -border, q)
                    if best is None or cand < best:
                        best = cand
                        best_q = q

                for u in comp:
                    parts[u] = best_q
                label_pop[lbl] -= comp_pop
                label_pop[best_q] += comp_pop
                changed_any = True
                if verbose:
                    print(
                        f"[contig-fix] moved {len(comp)} node(s) "
                        f"from label {lbl} -> {best_q} (pop {comp_pop})"
                    )

        if not changed_any:
            break

    return parts
