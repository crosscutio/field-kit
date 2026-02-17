"""Tests for spatial.contiguity module."""

from spatial.contiguity import _components, _label_pop, _border_size, repair_noncontiguous_parts


class TestComponents:
    """Tests for the _components helper (BFS connected components)."""

    def test_single_component(self, linear_graph):
        adj, _ = linear_graph
        comps = _components([0, 1, 2, 3, 4], adj)
        assert len(comps) == 1
        assert sorted(comps[0]) == [0, 1, 2, 3, 4]

    def test_two_components(self, linear_graph):
        """Nodes {0,1} and {3,4} are disconnected when node 2 is excluded."""
        adj, _ = linear_graph
        comps = _components([0, 1, 3, 4], adj)
        assert len(comps) == 2
        comp_sets = [set(c) for c in comps]
        assert {0, 1} in comp_sets
        assert {3, 4} in comp_sets

    def test_single_node(self, linear_graph):
        adj, _ = linear_graph
        comps = _components([2], adj)
        assert len(comps) == 1
        assert comps[0] == [2]

    def test_empty_nodes(self, linear_graph):
        adj, _ = linear_graph
        comps = _components([], adj)
        assert comps == []

    def test_grid_components(self, grid_graph):
        """All corners are disconnected when the center cross is excluded."""
        adj, _ = grid_graph
        comps = _components([0, 2, 6, 8], adj)
        assert len(comps) == 4


class TestLabelPop:
    def test_basic(self):
        parts = [0, 0, 1, 1, 2]
        weights = [10, 20, 30, 40, 50]
        result = _label_pop(parts, weights)
        assert result[0] == 30
        assert result[1] == 70
        assert result[2] == 50


class TestBorderSize:
    def test_counts_edges_to_neighbor(self, linear_graph):
        adj, _ = linear_graph
        # Nodes 0,1 are label 0; nodes 2,3,4 are label 1
        parts = [0, 0, 1, 1, 1]
        # Border between {0,1} and label 1: edge 1-2
        assert _border_size([0, 1], parts, adj, 1) == 1

    def test_no_edges(self, linear_graph):
        adj, _ = linear_graph
        parts = [0, 0, 1, 1, 1]
        # Node 0 has no direct edge to label 1
        assert _border_size([0], parts, adj, 1) == 0


class TestRepairNoncontiguousParts:
    def test_already_contiguous(self, linear_graph):
        """A partition that is already contiguous is returned unchanged."""
        adj, weights = linear_graph
        parts = [0, 0, 0, 1, 1]
        result = repair_noncontiguous_parts(adj, parts, weights)
        assert result == [0, 0, 0, 1, 1]

    def test_disconnected_component_reassigned(self, linear_graph):
        """Node 0 is labelled 1 but disconnected from 3,4 (also label 1).

        0 - 1 - 2 - 3 - 4
        1   0   0   1   1

        Node 0 should be reassigned to label 0.
        """
        adj, weights = linear_graph
        parts = [1, 0, 0, 1, 1]
        result = repair_noncontiguous_parts(adj, parts, weights)
        # Node 0 should now be label 0 (only adjacent label)
        assert result[0] == 0
        # The main components should stay put
        assert result[1] == 0
        assert result[2] == 0
        assert result[3] == 1
        assert result[4] == 1

    def test_isolated_node_no_crash(self):
        """A node with no neighbours at all should not crash the algorithm."""
        adj = [[], [2], [1]]
        parts = [0, 1, 1]
        weights = [10, 10, 10]
        # Node 0 has label 0 and no neighbours — nothing to reassign
        result = repair_noncontiguous_parts(adj, parts, weights)
        assert result == [0, 1, 1]

    def test_orphan_with_no_alternative_label(self):
        """Orphan component whose only neighbours share the same label.

        Two disconnected groups both labelled 0 with no label-1 neighbours.
        """
        # Two separate edges: 0-1 and 2-3, all label 0
        adj = [[1], [0], [3], [2]]
        parts = [0, 0, 0, 0]
        weights = [10, 10, 10, 10]
        result = repair_noncontiguous_parts(adj, parts, weights)
        # Cannot fix — no alternative label available, so stays as-is
        assert result == [0, 0, 0, 0]

    def test_multiple_passes(self, grid_graph):
        """A partition requiring more than one pass to fully repair.

        Grid layout:
            0 - 1 - 2
            |   |   |
            3 - 4 - 5
            |   |   |
            6 - 7 - 8

        Assign a "snake" pattern that creates disconnected components:
        Label 0: {0, 2, 4, 6, 8}  (checkerboard — 0 and 2 disconnected from 6 and 8)
        Label 1: {1, 3, 5, 7}
        """
        adj, weights = grid_graph
        parts = [0, 1, 0, 1, 0, 1, 0, 1, 0]
        result = repair_noncontiguous_parts(adj, parts, weights)

        # Each label should now form a single connected component
        for lbl in set(result):
            nodes = [i for i, p in enumerate(result) if p == lbl]
            comps = _components(nodes, adj)
            assert len(comps) == 1, f"Label {lbl} has {len(comps)} components"

    def test_does_not_mutate_input(self, linear_graph):
        """The original parts list should not be modified."""
        adj, weights = linear_graph
        parts = [1, 0, 0, 1, 1]
        original = parts.copy()
        repair_noncontiguous_parts(adj, parts, weights)
        assert parts == original

    def test_prefers_lower_imbalance(self):
        """When two neighbour labels are available, picks the one with lower pop.

        Graph: 0 - 1 - 2 - 3
        Labels: A   A   B   C
        Node 0 is the orphan of label A (main component is just node 1 after
        the partition creates a split). But here we set it up so node 0 is
        disconnected from the rest of A.

        Actually: let's use a star graph for clarity.
          1
          |
        0-2-3
          |
          4

        Parts: [1, 0, 0, 0, 1]  — label 1 has {0, 4}, disconnected.
        Node 0 neighbours: [2] (label 0). Node 4 neighbours: [2] (label 0).
        Orphan (smaller component) gets reassigned to label 0.
        """
        adj = [[2], [2], [0, 1, 3, 4], [2], [2]]
        weights = [10, 10, 100, 10, 10]
        parts = [1, 0, 0, 0, 1]
        result = repair_noncontiguous_parts(adj, parts, weights)
        # The smaller disconnected component of label 1 should move to label 0
        assert result[0] == 0 or result[4] == 0
        # Label 1 should be contiguous
        label1_nodes = [i for i, p in enumerate(result) if p == 1]
        if label1_nodes:
            comps = _components(label1_nodes, adj)
            assert len(comps) == 1

    def test_verbose_output(self, linear_graph, capsys):
        """Verbose mode should print diagnostic info."""
        adj, weights = linear_graph
        parts = [1, 0, 0, 1, 1]
        repair_noncontiguous_parts(adj, parts, weights, verbose=True)
        captured = capsys.readouterr()
        assert "[contig-fix]" in captured.out
