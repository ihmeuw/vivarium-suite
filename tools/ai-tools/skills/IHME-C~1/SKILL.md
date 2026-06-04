---
name: ihme-cluster-parallel-sims
description: Responsible-usage guide for running parallel simulations on IHME's shared Slurm cluster — getting off the login node, choosing an account/partition, right-sizing memory/CPU/time, putting outputs in the right storage tier, checking contention and fairshare before a big submission, fanning out with job arrays / Jobmon / psimulate, big-run notification etiquette, and disk-quota / HDF5 file-lock footguns. Use whenever the user is running Vivarium or other sims on the IHME cluster, submitting srun/sbatch/psimulate jobs, fanning out per-draw/per-sim/per-notebook work, or seeing jobs land PENDING, get OOM-killed, or hit their time limit. Complements the framework-clis skill (which CLIs to run) with how to run them responsibly on shared infrastructure.
---

# Running parallel simulations responsibly on the IHME cluster

The IHME cluster is a shared resource governed by the Slurm scheduler and a
fairshare policy. "Responsibly" here means two things at once: getting your own
work done efficiently, and not degrading the cluster for everyone else. Those
goals point the same way — oversized jobs, runaway fan-outs, and filled-up shared
disk slow down your work *and* your colleagues', and they get your account
fairshare-throttled so your next jobs start later.

The practices below are the ones that repeatedly matter for Vivarium-style
microsimulation work (single long sims, per-draw fan-outs, `psimulate` runs,
notebook regeneration). They are IHME-specific and infrastructure changes over
time, so treat concrete hostnames, partition names, and numbers as starting
points — **the canonical source of truth is the cluster documentation at
`docs.cluster.ihme.washington.edu`**, and you should measure resource use for
your own workload rather than trusting any number here.

## Related skills in this plugin

This skill covers *responsible shared-cluster usage*. For adjacent mechanics, defer
to the sibling skills rather than duplicating them: **framework-clis** (what
`simulate` / `psimulate` / `make_artifacts` do and how to invoke them),
**environments** (creating and activating the right conda env), and
**make-commands** / **pytest** (build and test targets). What this skill adds on
top is the part those don't cover: account/partition choice, right-sizing,
contention and fairshare diagnostics, storage tiers, big-run etiquette, and the
disk/file-lock footguns.

## 1. Get off the login node

The login/submit node (currently `gen-slurm-slogin-p01`) is shared by everyone,
and IHME login nodes kill long-running processes. Never run a simulation,
artifact build, or heavy notebook there. Grab an interactive shell on a compute
node first (`srun`; the older `qlogin` does the same thing):

```bash
srun --account <proj_account> -p i.q -c 1 --mem=8G -t <time> --pty bash
```

Then do your interactive development, debugging, and exploration on that node.

## 2. Always name an account, pick the right partition, and know your limits

- **`--account` is mandatory.** Every `srun`/`sbatch` must pass one. Omitting it
  fails with an unhelpful Slurm error. Use your team's standard project account
  for ordinary work and a higher-throughput project account (if your team has
  one) for big fan-outs.
- **Know your thread allowance.** By default every IHME user can run jobs using
  only up to ~10 threads at once. Higher limits come from the Computational
  Infrastructure trainings (Level 1 → 100 threads, Level 2 → unlimited), and
  team-storage access is granted through security groups via a helpdesk ticket.
  If jobs won't start or you can't reach a team directory, this is often why.
- **Match the partition to the job:**
  - A long-running queue (e.g. `long.q`) suits individual sims, single notebook
    executions, and slow test suites — longer time limits and less interactive
    churn.
  - A high-throughput queue (e.g. `all.q`) suits high-parallelism fan-outs
    (Slurm array jobs, `psimulate`). It has far more slots, but it is shared
    across the whole institute and is **often more contended in practice** (see
    §4).
  - An interactive queue (e.g. `i.q`) is for interactive shells, not batch jobs.

## 3. Right-size every job

This is the heart of running responsibly, and IHME's own docs stress it:
over-requesting memory "uses up cluster resources that could be used by others."
Ask for what the work needs — no more, no less.

- **Most Vivarium sims are single-threaded.** The Python GIL pins the whole step
  loop to one core, so a sim gets no faster with extra CPUs — they just sit idle
  and waste a shared allocation. Use **`--cpus-per-task=1` per sim**. To go
  parallel, run *N concurrent single-CPU processes/jobs*, not one N-CPU job.
- **Set `--mem` and `--time` deliberately.** Over-requesting wastes shared
  capacity and makes you wait longer in the queue; under-requesting gets the job
  OOM-killed or timed out. Start from a known-good estimate and adjust using what
  the job actually used — `sacct` reports peak memory and elapsed time.
- **Give the slow tail headroom on `--time`.** In a fan-out, the slowest sim is
  what matters, not the average. A time limit set just above the *average* finish
  time means the tail of slow sims hits the wall and triggers scheduler/Jobmon
  retries — which re-run those sims from scratch, roughly doubling their compute.
  Setting the limit comfortably above the observed *slowest* run eliminates that
  churn. (Real lesson from a 600-sim run: average finish ~176 min but the slowest
  landed at 3:59:46 against a 4 h wall, so dozens of sims retried; a 6 h limit
  removed the waste.)

## 4. Check contention before a big fan-out

"Bigger partition" does **not** mean "faster start." A large institute-wide queue
can be more contended than a smaller team-oriented one. Before launching hundreds
of jobs, look at the actual load and pick the partition/account combo that is
genuinely free right now. See `references/ihme-cluster-cheatsheet.md` for the
copy-paste diagnostic recipe; the short version:

- Compare per-partition pending vs. running counts (`squeue -p <p> -h -t PD/R`).
  A large pending count with a pending:running ratio above ~1 signals contention.
- If your jobs sit **PENDING with `Reason=Priority`**, the scheduler is
  fairshare-throttling you, not waiting for free cores. `scontrol show job <id>`
  shows an estimated `StartTime`; if it's hours out, that partition won't place
  you soon. `sshare` shows your fairshare usage.
- **Use the IHME monitoring tools, not just `squeue`.** `slurmtool.ihme.washington.edu`
  shows per-project priority and reports (unused time, percent of failed jobs);
  the Grafana Slurm dashboard shows live usage across projects. High unused-time
  or failure rates mean your requests need tuning.
- **For very large runs, give notice.** Cluster priority is allocated per
  *cluster project* (request it in the `#cluster_requests` Slack channel) and only
  makes jobs *schedule* sooner — it does not make them run faster. Runs that would
  consume ≳50% of the cluster (~12,000 threads or ~80 TB RAM) for more than a few
  hours should be flagged in `#cluster_requests` about a week ahead.
- **Submit one test job first**, watch its `StartTime` estimate, and only then
  decide whether to fan the rest out here or on the alternate partition.

## 5. Fan out cleanly

- For many similar tasks (per-draw PAFs, per-sim runs, per-notebook regen), use a
  **Slurm job array** or a **workflow manager** (Jobmon, or `psimulate` for
  Vivarium) rather than hand-launching jobs in a shell loop. The manager handles
  scheduling, retries, and bookkeeping.
- **If co-located jobs seem slow, suspect shared bandwidth — not shared cores.**
  The cluster enforces per-job CPU and memory allocations, so a batch job and an
  interactive shell that land on the same node are each confined to what they
  requested; they do not fight over the same cores. Genuine slowdowns from
  co-located jobs are more likely shared memory bandwidth, CPU cache, or
  disk/network I/O. The robust fix is to request resources explicitly and avoid
  running heavy work in an under-resourced interactive shell — not to police node
  placement. (Confirm the cluster's exact resource-isolation behavior in the
  cluster docs.)
- **Skip per-worker checkpoints when the workflow already retries.** Jobmon and
  `psimulate` retry failed workers from scratch, so checkpoint-resume is usually
  unnecessary. Leaving it on can write very large per-worker checkpoint files
  (hundreds of MB each × hundreds of workers × scenarios = easily hundreds of GB)
  and blow your disk quota. For `psimulate`, pass `--backup-freq none` unless you
  specifically need resume.

## 6. Put outputs in the right place, and protect shared disk

Choosing the right storage tier is part of running responsibly. Per IHME
Scientific Computing guidance:

- **Code / config / small files** → your home (`/ihme/homes/$USER`) or
  `/ihme/code/<user>/`. Home directories are small and quota-limited — **never
  write large outputs here.** (A full home quota can kill a job with no clear
  message.)
- **Intermediate and run outputs** → `/ihme/scratch`.
- **Shared / team outputs** → your team directory, `/mnt/team/<team>/` (access is
  granted via a security group; open a helpdesk ticket).
- **Final / archival data** → the J: drive (`/snfs1`). J: is archival and gets
  unstable under heavy job I/O, so don't point live jobs at it.

(`/ihme/...` and `/mnt/share/...` are aliases for the same shared storage; what
matters is choosing scratch/team/archive over your home directory.)

- **Check free space before big builds:** `df -h /ihme/scratch` and your team
  directory — production runs can land several GB of intermediates (raw
  artifacts, per-draw CSVs). `/tmp` is node-local and **invisible after the job
  ends**, so use it only as scratch you copy back from, never as a batch job's
  output location.
- **HDF5 on NFS locks.** Writing an artifact while any reader (e.g. a `psimulate`
  worker loading it during setup) holds it open trips
  `HDF5ExtError: ... unable to lock the file`. Set
  `export HDF5_USE_FILE_LOCKING=FALSE` for the writer, and never run two writers
  against the same file at once.
- **Clean up** checkpoint/backup files and stale per-draw intermediates during and
  after long runs.

## 7. Executing notebooks at scale

Run notebooks with `jupyter nbconvert --to notebook --execute --inplace
--allow-errors`. Without `--allow-errors`, nbconvert aborts at the first failing
cell and writes nothing, hiding the error. With it, execution continues and the
traceback is embedded in the failing cell for reviewers to see. The trade-off is
that nbconvert then always exits `0`, so derive pass/fail by scanning the executed
notebook for cells with `output_type == "error"` rather than trusting the exit
code — that scan is what should drive Slurm-dependency and monitoring logic.

## 8. Reusable sbatch template

`conda activate` does not work in `sbatch` scripts (the conda hook isn't sourced).
Put the env's `bin` on `PATH` instead:

```bash
#!/bin/bash
#SBATCH --job-name=<task>
#SBATCH --partition=long.q
#SBATCH --account=<proj_account>
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --output=/ihme/scratch/users/<user>/run_logs/<task>.out
#SBATCH --error=/ihme/scratch/users/<user>/run_logs/<task>.err
set -uo pipefail
cd /ihme/code/<user>/<project>
ENV=/ihme/homes/<user>/.conda/envs/<env>
export PATH="$ENV/bin:$PATH"
```

Command-line `--partition` / `--account` flags override the `#SBATCH` directives,
so you can keep one template and redirect it to a less-contended partition at
submit time:

```bash
sbatch --partition=all.q --account <proj_account> run_task.sbatch
```

## 9. Responsible also means readable: inclusive language

Responsible sim-science work extends to how we write about it. In comments,
docstrings, commit messages, and notebook prose, prefer plain language over
developer idioms that lean on metaphors of mental illness or disability — e.g.
"consistency check" or "smoke test" instead of "sanity check", "stub"/"placeholder"
instead of "dummy", "allowlist/blocklist" instead of "whitelist/blacklist",
"primary/replica" instead of "master/slave". The full substitution table is in
the cheat-sheet. The point isn't ceremony; it's that future readers span a range
of lived experiences and plain language simply says what we mean.

## Sources of truth

When in doubt, defer to the institutional sources rather than this skill:

- **`docs.cluster.ihme.washington.edu`** — canonical cluster docs (partitions,
  projects/priorities, resource isolation).
- **`slurmtool.ihme.washington.edu`** and the Grafana Slurm dashboard —
  priority, usage, and job-efficiency reports.
- **`#cluster_requests`** (Slack) — priority requests and large-run notifications.
- **helpdesk.ihme.washington.edu** — account, cluster-project, and team-storage
  access.

For the full command list, the contention/fairshare diagnostic scripts,
resource-budget heuristics, the storage map, the footgun catalog, and the
inclusive-language table, read `references/ihme-cluster-cheatsheet.md`.
