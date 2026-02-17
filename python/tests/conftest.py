"""Shared fixtures for spatial algorithm tests."""

import pytest


@pytest.fixture
def linear_graph():
    """A simple linear graph: 0 - 1 - 2 - 3 - 4.

    Returns (adj_list, weights) where each node has weight 10.
    """
    adj = [
        [1],        # 0
        [0, 2],     # 1
        [1, 3],     # 2
        [2, 4],     # 3
        [3],        # 4
    ]
    weights = [10, 10, 10, 10, 10]
    return adj, weights


@pytest.fixture
def grid_graph():
    """A 3x3 grid graph (nodes 0-8), each with weight 10.

    Layout:
        0 - 1 - 2
        |   |   |
        3 - 4 - 5
        |   |   |
        6 - 7 - 8
    """
    adj = [
        [1, 3],        # 0
        [0, 2, 4],     # 1
        [1, 5],        # 2
        [0, 4, 6],     # 3
        [1, 3, 5, 7],  # 4
        [2, 4, 8],     # 5
        [3, 7],        # 6
        [4, 6, 8],     # 7
        [5, 7],        # 8
    ]
    weights = [10] * 9
    return adj, weights
