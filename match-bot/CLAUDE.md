# match-bot conventions

## projects/ — private per-user workspace

`match-bot/projects/` is the workspace for real matching projects (client
data, configs, intermediate outputs). It is gitignored by this repository and
is expected to be its own git repository with a **private** remote, nested
inside this clone.

Each subfolder of `projects/` is one matching/geocoding project: put the
input files there and start a matching task from it.

Commit routing — when asked to commit or back up work:

- Project data (anything under `projects/`) → commit in the `projects/` repo
  (run git from inside `match-bot/projects/`) and push to its private remote.
- Tooling, skills, and docs changes → commit in this repository.

Never commit files from `projects/` to this repository; it is public.
