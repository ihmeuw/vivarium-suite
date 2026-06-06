# IHME cluster cheat-sheet

Companion to `SKILL.md`. Concrete commands, diagnostics, and heuristics for
working on the IHME Slurm cluster. Hostnames, partition names, account names,
and numbers are environment-specific and change — the canonical source of truth
is **`docs.cluster.ihme.washington.edu`**; verify there and measure for your own
workload.

## Contents

1. Contention diagnostics (run before a big submission)
2. Fairshare / "why is my job PENDING" diagnostics
3. Partition + account selection by workload
4. Resource-budget heuristics (examples — measure your own)
5. Footgun catalog
6. Storage map, access tiers, and sources of truth

## 1. Contention diagnostics (run before a big submission)

A larger partition is not necessarily a faster start — institute-wide queues are
shared widely and are often more contended than smaller team queues. Check before
you fan out:

```bash
# Per-partition load and pending depth. Swap in the partitions you're choosing between.
for p in all.q long.q; do
  total=$(squeue -p $p -h | wc -l)
  pend=$(squeue -p $p -h -t PD | wc -l)
  run=$(squeue -p $p -h -t R | wc -l)
  echo "$p: total=$total running=$run pending=$pend"
done

# CPU allocation breakdown: CPUS(A/I/O/T) = allocated / idle / other / total
sinfo -o "%P %a %D %C"
```

Read it like this: a large pending count (e.g. thousands on a big institute queue,
hundreds on a team queue) combined with a pending:running ratio above ~1 means the
queue is backed up. Idle CPUs near zero in `sinfo` confirms it. Prefer the
partition with real idle capacity, or submit a single probe job (below) first.

## 2. Fairshare / "why is my job PENDING" diagnostics

If jobs sit PENDING with `Reason=Priority`, the scheduler is fairshare-throttling
your account — it is **not** waiting for free cores, so "the cluster looks empty"
won't help.

```bash
scontrol show job <jobid> | grep -E 'JobState|Reason|StartTime|Priority|Account|Partition|QOS'
sshare -u <user> -p          # your fairshare slice and usage
```

- `StartTime` hours out → that partition won't place you soon; try the alternate.
- High fairshare usage → your slice is spent; jobs from lower-usage accounts jump
  ahead.

Cluster priority is allocated per **cluster project**, not per user or per job,
and **only schedules jobs sooner — it does not make them run faster.** Priority
is requested via `#cluster_requests` (Slack, human action) — state the project,
the end date, and why. Also use `#cluster_requests` to give ~a week's notice for
runs that would consume ≳50% of the cluster (~12,000 threads or ~80 TB RAM) for
more than a few hours. These are human-to-human communications; the agent should
surface the recommendation and let the user decide to post.

**Probe-then-commit:** submit one test job, watch its `StartTime` estimate via
`scontrol show job`, then decide whether to fan the whole batch out here or on the
alternate partition.

## 3. Partition + account selection by workload

These are starting heuristics; re-evaluate per submission with §1–2. Account
choice is also a matter of **team convention** — confirm with your team which
account is appropriate for each type of work.

| Workload | Suggested combo | Why |
|---|---|---|
| A handful of short-to-medium jobs (e.g. notebook regen) | team long-running queue + standard project account | The big institute queue's project quota is consumed fast by others' large runs; the team queue's quota is usually less contested. |
| High-fanout array jobs (100+ tasks, each ≤ ~5 min) | high-throughput queue + high-throughput project account | Per-task wall is small enough that even hours of queue wait beat serializing on a smaller queue's slot count. |
| Multi-hour individual sims (single notebook / single `psimulate` sim) | team long-running queue + standard project account | Fewer short-runtime users churning slots; less interactive contention. |
| When in doubt | submit one probe job | Decide from its `StartTime` estimate. |

## 4. Resource-budget heuristics (examples — measure your own)

From real Vivarium microsimulation work. Numbers depend heavily on model
complexity and `step_size` (stepping time scales ~linearly with step count), so
use these as orders of magnitude and confirm with `sacct` peak usage.

- **Single sim / single notebook execution:** `--cpus-per-task=1`, `--mem=16G` is
  comfortable. Sims are single-threaded (GIL), so extra CPUs idle. Wall-clock
  ranges from a couple minutes to a few hours depending on the model.
- **Slow test suite:** `--mem=24G --cpus-per-task=1`, time limit ~6 h. Runs
  serially — multi-CPU allocations are wasted.
- **Per-draw fan-out (e.g. PAF or prevalence MC):** `--mem=16G
  --cpus-per-task=1` per task, ~1–20 min per draw depending on how heavy the
  component graph is. A 100-task array on a high-throughput queue can finish in
  ~25 min wall-clock.
- **`psimulate run` per sim (multi-year sim):** ~`-m 16 -r 04:00:00` is often
  comfortable, but **size the time limit to the slowest sim, not the average**
  (see SKILL.md §3). 100 parallel sims ~2.5–3 h wall-clock.
- **Parallel notebook regen:** run N concurrent single-CPU `nbconvert` processes
  (e.g. 4 × `--mem=16G --cpus-per-task=1`), not one N-CPU job.

## 5. Footgun catalog

- **No `--account` → cryptic failure.** Slurm errors with no useful message. Pass
  one on every `srun`/`sbatch`.
- **`conda activate` does nothing in `sbatch`.** The conda init hook isn't
  sourced. Use `export PATH="$ENV/bin:$PATH"` where `ENV` is the env directory.
- **Outputs in the wrong tier.** Don't write run outputs to your home directory
  (small, quota-limited) — use `/ihme/scratch` for intermediates and
  `/mnt/team/<team>/` for shared/team outputs (see §6). `/tmp` is node-local and
  vanishes when the job ends; use it only as scratch you copy back from, never as
  a batch job's final output location.
- **Multi-CPU allocations for single-threaded sims are wasted.** GIL pins the step
  loop to one core. Parallelize across processes/jobs, not cores within a job.
- **Time limit set to the average → retry churn.** The slow tail hits the wall and
  the workflow retries those sims from scratch (~2× their compute). Budget above
  the slowest observed run.
- **HDF5 file-lock on NFS.** A writer trips
  `HDF5ExtError: ... unable to lock the file` if any reader holds the file open
  (commonly `psimulate` workers loading an artifact during setup). Set
  `export HDF5_USE_FILE_LOCKING=FALSE` for the writer; never run two writers at
  once.
- **Disk quota blowouts from checkpoints.** Workflow managers that retry workers
  make per-worker checkpoint-resume unnecessary, but leaving it on can write
  hundreds of MB per worker (× hundreds of workers × scenarios = hundreds of GB).
  Disable it (`psimulate --backup-freq none`) unless you need resume. Diagnose a
  live run with `du -sh <run>/.../sim_backups/`; clean up stale `*.pkl` checkpoint
  files for completed workers.
- **Out-of-disk kills.** A full quota can terminate a build with no clear message.
  Run `df -h /ihme/scratch` and your team directory before large builds; if a
  target is nearly full, stage to node-local `/tmp` and copy the finished result
  to scratch/team storage.
- **Co-located jobs and the "shared node" concern.** Slurm enforces per-job CPU
  and memory allocations via cgroups, so a batch job and your interactive shell on
  the same node do *not* share cores or memory budgets. If you still see slowdowns,
  the cause is shared memory bandwidth / cache / I/O, or an under-resourced job —
  fix it by requesting resources explicitly. (Confirm the cluster's exact
  isolation config in the cluster docs.)

## 6. Storage map, access tiers, and sources of truth

**Where things go** (per IHME Scientific Computing guidance — `/ihme/...` and
`/mnt/share/...` are aliases of the same shared storage):

| Content | Location | Notes |
|---|---|---|
| Code / config / small files | `/ihme/homes/$USER`, `/ihme/code/<user>/` | Small, quota-limited. Never put large outputs here. |
| Intermediates / run outputs | `/ihme/scratch` | Working space; not permanent. |
| Shared / team outputs | `/mnt/team/<team>/` | Access via security group → helpdesk ticket. |
| Final / archival data | J: drive (`/snfs1`) | Archival; unstable under heavy job I/O — don't run live jobs against it. |

**Access tiers (Slurm):** every IHME user can run jobs using up to ~10 threads at
once by default. More requires the Computational Infrastructure trainings:
Level 1 → 100 threads; Level 2 → unlimited. Cluster-project membership and
team-storage security groups are granted via helpdesk tickets.

**Sources of truth** (defer to these over this cheat-sheet):

- `docs.cluster.ihme.washington.edu` — partitions, projects/priorities, resource
  isolation, storage.
- `slurmtool.ihme.washington.edu` + Grafana Slurm dashboard — priority, usage,
  job-efficiency reports.
- `#cluster_requests` (Slack, read-only reference) — priority requests and
  large-run notices; posting is a human action.
- IHME IT help desk — accounts, cluster projects, team-storage access (human-
  facing ticketing system; no programmatic API).
