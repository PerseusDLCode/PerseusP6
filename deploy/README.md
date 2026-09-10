# VM Deployment Setup

This directory contains the scripts and configuration for serving the
MinimumViablePerseus static site on your own server. No build ever runs on
this host: `build-corpus.yml`/`build-global.yml` (GitHub Actions) freeze the
site's pages in CI and push them to GHCR as OCI artifacts, tagged by branch
(`main` → `latest`, `dev` → `staging`). `cron-deploy.sh` only pulls whichever
artifacts changed and swaps them live.

## Prerequisites

- Podman (on the VM) or Docker (locally), both with the `compose` subcommand
  — only used here to run the `serve` (nginx) container, never to build
  anything
- [`oras`](https://oras.land) CLI, for pulling artifacts from GHCR
- `zstd` on `PATH` (GNU tar's `--zstd` shells out to it)
- `python3`, used to parse `oras manifest fetch`'s JSON output
- Public GHCR packages (or `GHCR_USER`/`GHCR_TOKEN` for private ones)

## Environment variables

| Variable          | Default                    | Description                                                              |
|--------------------|-----------------------------|----------------------------------------------------------------------------|
| `REGISTRY`         | `ghcr.io/perseusdlcode`     | GHCR namespace holding the artifacts                                       |
| `SHARDS`           | `0 1 2 3 4`                 | Space-separated shard indices to pull — must match build-corpus.yml's `SHARD_COUNT` (0-indexed) |
| `TAG`              | `latest`                    | Which branch's alias to pull (`latest` = main/production, `staging` = dev) |
| `ORAS_BIN`         | `oras`                      | Path to the `oras` CLI                                                     |
| `BUILD_DIR`        | `./build`                   | Symlink `serve` mounts; points at whichever blue-green directory is live   |
| `STATE_DIR`        | `./state`                   | Directory holding one last-deployed-digest file per artifact               |
| `CONTAINER_CMD`    | `podman`                    | Container runtime (set to `docker` for local testing)                      |
| `COMPOSE_PROJECT`  | `perseus`                   | podman/docker compose project name — set explicitly if running alongside other compose projects on the same host |
| `ENV_FILE`         | `<script dir>/.env`         | Optional file to source for the above                                      |
| `GHCR_USER` / `GHCR_TOKEN` | *(unset)*            | Optional; if set, logs in to GHCR for private pulls                        |
| `SERVE_CTR`        | `mvp-serve`                 | (compose.yaml) nginx container name                                        |
| `SERVE_PORT`       | `8000`                      | (compose.yaml) host port for nginx                                         |

`BUILD_DIR` and `STATE_DIR` default to paths relative to wherever the script
is invoked from (cron's default working directory is the user's home), not
to the script's own location — set them to absolute paths in `.env` if
that's not what you want.

`BUILD_DIR-a` and `BUILD_DIR-b` (e.g. `./build-a`, `./build-b`) are created
automatically alongside `BUILD_DIR` — see "How it works" below.

## One-time setup

```bash
# Clone the repo (cron-deploy.sh expects compose.yaml alongside it)
git clone https://github.com/PerseusDLCode/MinimumViablePerseus /home/perseus/MinimumViablePerseus

# Create the env file
cat > /home/perseus/MinimumViablePerseus/deploy/.env << 'EOF'
CONTAINER_CMD=podman
BUILD_DIR=/home/perseus/build
STATE_DIR=/home/perseus/state
EOF
```

## Cron

Add this line to your crontab (`crontab -e`):

```
*/10 * * * * /usr/bin/flock -n /home/perseus/deploy.lock /home/perseus/MinimumViablePerseus/deploy/cron-deploy.sh >> /home/perseus/deploy.log 2>&1
```

## How it works

1. The nginx serve container is defined declaratively in `compose.yaml`
   and managed via `$CONTAINER_CMD compose`.
2. Before anything else, the script checks that `SHARDS` has as many entries
   as CI actually published (see "Making the GHCR packages public" below,
   `mvp-shard-count`) and refuses to deploy — loudly, exit 1 — on a
   mismatch, rather than silently serving a site missing whichever shards
   this host never fetches.
3. It then resolves the current digest of every shard artifact
   (`mvp-shard-0` … `mvp-shard-N`) and the global-pages artifact
   (`mvp-global`) at `$TAG`, and compares each to what's recorded in
   `STATE_DIR`.
4. Rollback uses a blue-green pair of directories, `BUILD_DIR-a` and
   `BUILD_DIR-b`, with `BUILD_DIR` a symlink pointing at whichever one is
   currently served. If any digest changed, it fully repopulates the
   *inactive* directory from scratch — the live, served directory is never
   touched during the pull:
   - Pulls every shard and the global artifact, pinned to the digest
     resolved in step 3 (not the mutable tag again — the tag can move
     between resolving it and pulling it, which would otherwise silently
     desync the recorded state from what actually got extracted).
   - Extracts each artifact's tarball into the inactive directory.
5. Before flipping traffic to it, the freshly repopulated inactive
   directory is sanity-checked: `index.html` must exist, and its total file
   count must not have dropped below half of what's currently live (a
   from-scratch rebuild landing far below that almost always means a
   corrupt/partial pull, not a real shrink of the corpus). Either check
   failing aborts the deploy the same as a resolution failure below.
6. On success: `BUILD_DIR` is flipped to the directory that was just
   validated, `compose up -d --force-recreate serve` restarts nginx so it
   picks up the new symlink target, and the new digests are recorded.
7. On failure — an unresolvable digest, a `SHARDS` mismatch, or a failed
   validation — nothing is touched: the symlink still points at the
   last-good build, so there is no restore step. The state files are left
   unchanged (retries next tick).

Because the pull never writes into the live directory, there's always
exactly one full extra copy of the site on disk (the inactive slot) — no
more, no less — and no per-tick full-tree copy the way an rsync-based
snapshot would require.

## Making the GHCR packages public

There's one package per shard (`mvp-shard-0` … `mvp-shard-N`), one manifest
package per shard (`mvp-shard-N-manifest`), `mvp-global`, and
`mvp-shard-count` (a one-line text artifact recording `SHARD_COUNT`, used to
sanity-check `SHARDS` before every deploy — see "How it works" above). After
the first push of each, go to:

```
https://github.com/orgs/perseusdlcode/packages/container/<package-name>/settings
```

and set visibility to **public** so the VM can pull without authentication.
