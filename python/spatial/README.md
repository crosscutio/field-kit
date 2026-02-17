# Spatial Partitioning

Utilities for enforcing spatial contiguity on graph-partitioned geodata and repairing invalid geometries.

## Problem: disconnected partitions

Balanced graph partitioning algorithms like [PyMetis](https://github.com/inducer/pymetis) optimise for equal-weight clusters but have no spatial contiguity constraint. The result: a single label can end up split into multiple disconnected pieces.

```
Before repair                After repair

 A  A  B  B                   A  A  B  B
 A  B  B  B        →          A  B  B  B
 A  A  B  B                   A  A  B  B
 B  B  B  B                   B  B  B  B
 ↑
 This "B" island is
 disconnected from
 the main B cluster
```

For staff area planning (assigning supervisors to contiguous territories), disconnected clusters are not operationally viable. `repair_noncontiguous_parts` fixes this.

## Algorithm: `repair_noncontiguous_parts`

### How it works

1. **Detect splits.** For each label, run BFS to find connected components in the adjacency subgraph. If there's more than one component, the label is non-contiguous.

2. **Keep the largest.** The biggest component (by node count) stays. All smaller components are orphans.

3. **Reassign orphans.** For each orphan component, find every adjacent label (labels of nodes that share an edge with the orphan). Score each candidate by:
   - **Population imbalance** (lower is better): `|pop_source − pop_orphan − (pop_candidate + pop_orphan)|`
   - **Border size** (higher is better, used as tie-breaker): number of edges between the orphan and the candidate label

   Assign the orphan to the best candidate.

4. **Repeat.** Reassigning one orphan can create new disconnections in the source label. Run up to `max_passes` sweeps (default 5) until no changes occur.

### Worked example

```
Graph (5 nodes, linear):   0 — 1 — 2 — 3 — 4
Labels:                     1   0   0   1   1
Weights:                   10  10  10  10  10
```

- **Label 1** has nodes {0, 3, 4}. BFS finds two components: {3, 4} (largest) and {0} (orphan).
- **Orphan {0}**: only adjacent label is 0 (via edge 0→1). Reassign node 0 to label 0.
- **Result:** `[0, 0, 0, 1, 1]` — both labels are now contiguous.

## API reference

### `repair_noncontiguous_parts(adj_list, parts, weights, *, verbose=False, max_passes=5)`

Enforce spatial contiguity on a graph partition.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `adj_list` | `list[list[int]]` | Adjacency list — `adj_list[i]` is the list of neighbours of node `i` |
| `parts` | `list[int]` | Label assignment for each node (e.g., from PyMetis) |
| `weights` | `list[int \| float]` | Population or other balance metric for each node |
| `verbose` | `bool` | Print diagnostic messages when components are reassigned (default `False`) |
| `max_passes` | `int` | Maximum sweep iterations (default `5`) |

**Returns:** `list[int]` — new label assignment where each label forms a single connected component (when possible).

**Note:** The input `parts` list is not mutated.

### `fix_geometry(gdf)`

Repair invalid geometries in a GeoDataFrame. Tries `buffer(0)` first, then falls back to `shapely.validation.make_valid`. Drops rows with unfixable geometry.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `gdf` | `GeoDataFrame` | Input GeoDataFrame (not modified in place) |

**Returns:** `GeoDataFrame` — copy with repaired geometries.

### `flatten_column(gdf, column)`

Replace list-valued cells in a column with their first element. Useful for Shapefile export, which does not support list-type attributes.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `gdf` | `GeoDataFrame` | Modified in place and returned |
| `column` | `str` | Column name to flatten |

**Returns:** `GeoDataFrame` — the same object, modified in place.

## Usage example

```python
import pymetis
from libpysal.weights import Queen
from spatial.contiguity import repair_noncontiguous_parts
from spatial.geometry import fix_geometry

# 1. Fix invalid geometries
gdf = fix_geometry(gdf)

# 2. Build adjacency from spatial contiguity
w = Queen.from_dataframe(gdf, use_index=False)
adj_list = [list(w.neighbors[i]) for i in range(len(gdf))]

# 3. Partition with PyMetis
weights = gdf["population"].astype(int).tolist()
n_parts, parts = pymetis.part_graph(num_parts, adjacency=adj_list, vweights=weights)

# 4. Repair disconnected components
parts = repair_noncontiguous_parts(adj_list, parts, weights, verbose=True)

gdf["cluster"] = parts
```

## Dependencies

- **contiguity.py**: Python standard library only (`collections.deque`, `collections.defaultdict`)
- **geometry.py**: `geopandas`, `shapely` (consuming repo owns versions)
