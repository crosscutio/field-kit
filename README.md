# crosscut-public

Open-source algorithms for geospatial health campaign planning, extracted from [Crosscut](https://www.crosscut.io/)'s private repositories.

Crosscut digitises mass drug administration (MDA) campaigns for neglected tropical diseases (NTDs). This involves modelling catchment areas, partitioning territory into balanced staff areas, and analysing survey data to recommend assessment protocols. The algorithms that power these workflows are general-purpose and useful beyond Crosscut — this repo makes them available under the MIT licence.

> **Name note:** `crosscut-public` is a placeholder. Rename candidates: *open-terrain*, *cross-section*, *cartograph*. GitHub supports renaming with automatic redirects.

## What's included

| Directory | Language | Contents |
|-----------|----------|----------|
| [`python/spatial/`](python/spatial/) | Python | Contiguity enforcement for graph-partitioned maps; geometry repair utilities |
| `python/sppa/` | Python | *(planned)* SPPA decision algorithm for survey protocol recommendations |
| `js/` | JavaScript | *(placeholder)* Future Node.js extractions |
| `rust/` | Rust | *(placeholder)* Future Rust extractions |

## Quick start

### Standalone clone

```bash
git clone https://github.com/crosscutio/crosscut-public.git
cd crosscut-public/python
pip install -e .
```

### As a git submodule (for private repos)

```bash
# Add to your repo
git submodule add https://github.com/crosscutio/crosscut-public.git crosscut-public
pip install --no-deps -e crosscut-public/python/

# In Python
from spatial.contiguity import repair_noncontiguous_parts
```

### Docker (e.g., in an AWS Lambda container)

```dockerfile
ADD crosscut-public crosscut-public
RUN pip install --no-deps -e crosscut-public/python/
```

## Running tests

```bash
cd python
pip install -e .
pytest tests/ -v
```

## Module documentation

- [**python/spatial/** — Spatial Partitioning](python/spatial/README.md): contiguity repair after graph partitioning, geometry fixing

## Licence

[MIT](LICENSE)
