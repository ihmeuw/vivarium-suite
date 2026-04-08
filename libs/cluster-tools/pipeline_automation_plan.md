# Pipeline Automation Plan: Pre-Sim → Simulation → Post-Sim

## 1. Problem Statement

Currently, running a full simulation workflow requires manual orchestration of three discrete phases:

1. **Pre-simulation tests** — pytest suites and/or notebooks that validate data, artifacts, and model configuration before committing cluster resources.
2. **Parallel simulation** — launching `psimulate run` across locations/scenarios (PR #284 automates this part within `vivarium_gates_mncnh`).
3. **Post-simulation tests** — automated V&V (verification & validation) via pytest and/or result-analysis notebooks that verify simulation outputs.

Each phase may require a different conda environment (e.g., the simulation env doesn't have `vivarium_testing_utils[validation]` installed). There is no unified system that chains these phases, manages per-step environments, or provides a single point of configuration.

## 2. Design Goals

- **Dedicated `psimulate workflow` subcommand**: a new subcommand in the psimulate CLI group. `psimulate run`, `restart`, and `expand` are unchanged. The workflow subcommand is the sole entry point for pipeline orchestration.
- **Single YAML configuration**: the pipeline YAML is the only required argument — `psimulate workflow pipeline.yaml`. All configuration (step ordering, simulation config paths, compute resources, output directory) lives in this one file. Key top-level settings (`-o`, `-P`, `-q`) can also be overridden from the CLI.
- **Flexible step definition**: each step can be configured in one of three ways:
  1. **`command`** — a raw shell command (run via `conda run -n <env> <command>`).
  2. **`type` + `path`** (+ optional `args`) — a structured definition where the engine infers the command. Supported types: `pytest`, `notebook`, `python`, `shell`.
  3. **Bespoke steps by name** — recognized step names (e.g., `"simulation"`) that require no command/type/path. The engine knows what to do based on the name and the step's configuration fields.
- **YAML defines the full step ordering**: the pipeline YAML contains an ordered `steps` list. If a step named `"simulation"` is present, the engine uses the existing VCT code to generate all parallel simulation tasks and inserts them at that position in the DAG. The simulation step points to the model specification and branch configuration files within the YAML itself.
- **Powered by Jobmon DAG**: `psimulate workflow` builds a single Jobmon workflow containing all steps — pre-steps, the simulation tasks, and post-steps — with task dependencies that enforce ordering and halt-on-failure semantics natively.
- **Global project and queue**: `project` and `queue` are specified once at the top of the YAML and propagated to all steps. They can be overridden from the CLI.
- **Per-step compute resources**: each step specifies its own SLURM resources (memory, runtime, cores).
- **Per-step environment control**: each step can specify a conda environment, defaulting to the current active environment.
- **Clear success/failure semantics**: upstream failure prevents downstream steps from running (Jobmon's built-in DAG behavior).
- **Backward compatible**: `psimulate run`, `restart`, and `expand` are completely unchanged. The `workflow` subcommand is purely additive.

## 3. Does Jobmon Support Conditional Halt-on-Failure?

**Yes — this is a core Jobmon feature, not something we need to build.**

Jobmon's DAG execution enforces that a task only runs when **all** of its upstream dependencies have completed successfully (status `DONE`). If an upstream task fails, its downstream tasks are never scheduled.

Specifically, from Jobmon's documentation:

> *"As Tasks complete, Jobmon starts downstream Tasks which have all their upstream Tasks successfully completed."*

> *"If a job fails, then its downstreams are not launched, but other paths through the graph continue."*

This means for a linear pipeline `pre_test → simulation → post_test`:
- If the pre-test task fails → the simulation task is **never launched**.
- If the simulation task fails → the post-test task is **never launched**.

Jobmon also offers a stricter **fail-fast mode** (`workflow.run(fail_fast=True)`) that stops launching *all* new tasks (even on independent branches) as soon as any single task fails. This is useful if the pipeline has parallel independent branches and you want everything to stop immediately on any failure.

**Bottom line: halt-on-failure is handled entirely by Jobmon's task dependency graph. No custom orchestration logic is needed.**

## 4. Where This Feature Lives — and Why

### Decision: `vivarium_cluster_tools` (as a psimulate extension)

| Component | Repository | Rationale |
|-----------|-----------|-----------|
| **Pipeline engine** (YAML parser, Jobmon workflow builder, CLI subcommand) | `vivarium_cluster_tools` | `vivarium_cluster_tools` already owns `psimulate` and already depends on Jobmon (PR #302). The pipeline feature is a natural extension of psimulate: it builds a Jobmon workflow where the simulation step *is* a psimulate invocation, and pre/post steps are arbitrary cluster tasks. This keeps all cluster orchestration in one place. |
| **Project-specific pipeline config** (YAML files) | `vivarium_gates_mncnh` (or any model repo) | Each model repo defines its own `pipeline.yaml` specifying test paths, model spec, environments, etc. |
| **Project-specific V&V measures/formatters** | `vivarium_gates_mncnh` (already exists in `validation/`) | Model-specific, stays in the model repo. |

### Why not `vivarium_testing_utils`?
While `vivarium_testing_utils` provides testing utilities (FuzzyChecker, V&V framework, pytest plugin), the pipeline feature is fundamentally about **cluster job orchestration** — building Jobmon workflows, scheduling SLURM tasks, managing compute resources. This is squarely in `vivarium_cluster_tools`' domain. The testing utils repo provides the *payload* that runs inside pipeline steps, not the pipeline itself.

### Why not `vivarium_gates_mncnh`?
Model-specific config (the YAML) lives here, but the engine must be reusable across all vivarium projects.

## 5. Configuration Format: YAML

YAML is the right choice here because:
- It's already the standard config format across the vivarium ecosystem (model specs, branches, layered config trees).
- Human-readable and diff-friendly in PRs.
- Supports comments (unlike JSON).
- Python's `pyyaml` / `layered_config_tree` are already dependencies.

### 5.1 Pipeline Configuration Schema

The pipeline YAML is a self-contained configuration file. At the top level it defines global settings (project, queue, output directory, default environment) and an ordered `steps` list. Each step runs sequentially — every step depends on all tasks from the previous step.

Steps can be defined in three ways:

1. **Raw command**: provide `command` — the engine wraps it with `conda run -n <env> <command>`.
2. **Structured type + path**: provide `type` and `path` (and optional `args`) — the engine infers the full command. Supported types: `pytest`, `notebook` (→ papermill), `python` (→ python), `shell` (→ bash).
3. **Bespoke by name**: recognized step names like `"simulation"` need no command/type/path. The engine handles them internally based on the step's configuration fields (e.g., `model_specification`, `branch_configuration` for the simulation step).

For structured steps, `path` can be one or more file paths or a directory. The `args` field appends additional arguments to the inferred command.

```yaml
# pipeline.yaml — Example for vivarium_gates_mncnh
pipeline:
  name: "mncnh_main_sim"

  # ── Global settings (propagated to all steps) ──
  project: "proj_simscience_prod"
  queue: "all.q"
  output_directory: "/mnt/team/simulation_science/results/model_29"
  # Default conda environment for steps (null = current env)
  default_environment: null

  steps:

    # ────────────────────────────────────────────────
    # Step 1: Pre-simulation tests (type + path + args)
    # Inferred command: pytest tests/test_lbwsg.py tests/test_mortality.py tests/test_pregnancy.py --runslow
    # ────────────────────────────────────────────────
    - name: "pre_tests_pytest"
      type: pytest
      path:
        - tests/test_lbwsg.py
        - tests/test_mortality.py
        - tests/test_pregnancy.py
      args: "--runslow"
      resources:
        memory: 10   # GB
        runtime: "01:00:00"

    # ────────────────────────────────────────────────
    # Step 2: The simulation (bespoke step)
    # Recognized by name — no command/type/path needed.
    # The engine calls the existing VCT build_workflow()
    # to generate all parallel simulation tasks.
    # ────────────────────────────────────────────────
    - name: "simulation"
      model_specification: "src/vivarium_gates_mncnh/model_specifications/model_spec.yaml"
      branch_configuration: "src/vivarium_gates_mncnh/model_specifications/branches/branches.yaml"
      resources:
        memory: 5    # GB, per simulation task
        runtime: "03:00:00"

    # ────────────────────────────────────────────────
    # Step 3: Post-simulation V&V tests (type + path)
    # Inferred command: pytest tests/automated_v_and_v/ --results-dir {output_directory}
    # ────────────────────────────────────────────────
    - name: "post_tests_pytest"
      type: pytest
      path: tests/automated_v_and_v/
      args: "--results-dir {output_directory}"
      environment: "vivarium_gates_mncnh_artifact"
      resources:
        memory: 20
        runtime: "02:00:00"

    # ────────────────────────────────────────────────
    # Step 4: Post-simulation result notebooks (type + path)
    # Inferred command: papermill tests/model_notebooks/results/neonatal.ipynb
    #                   {output_directory}/executed/neonatal.ipynb
    #                   -p model_dir {output_directory}
    # ────────────────────────────────────────────────
    - name: "post_notebook_neonatal"
      type: notebook
      path: tests/model_notebooks/results/neonatal.ipynb
      args: "-p model_dir {output_directory}"
      environment: "vivarium_gates_mncnh_artifact"
      resources:
        memory: 20
        runtime: "02:00:00"

    # ────────────────────────────────────────────────
    # Step 5: Raw command example (command field)
    # ────────────────────────────────────────────────
    - name: "post_notebook_maternal"
      command: "papermill tests/model_notebooks/results/maternal.ipynb {output_directory}/executed/maternal.ipynb -p model_dir {output_directory}"
      environment: "vivarium_gates_mncnh_artifact"
      resources:
        memory: 20
        runtime: "02:00:00"
```

### 5.2 How It's Invoked

`psimulate workflow` is a new subcommand that takes the pipeline YAML as its sole required argument. All configuration lives in the YAML, but key settings can be overridden from the CLI:

```bash
# Standard usage — everything in the YAML:
psimulate workflow pipeline.yaml

# Override output directory from CLI:
psimulate workflow pipeline.yaml -o /results/model_29.1

# Override project and queue from CLI:
psimulate workflow pipeline.yaml -P proj_simscience_prod -q long.q

# With psimulate debug flags:
psimulate workflow --pdb -vvv pipeline.yaml

# Existing commands are UNCHANGED:
psimulate run model_spec.yaml branches.yaml -o /results -P proj -q all.q
```

CLI overrides take precedence over YAML values. This lets teams commit a canonical `pipeline.yaml` to the repo and override only what changes between runs (e.g., output directory).

`psimulate workflow` is not supported by `restart` or `expand`, which operate on already-running workflows.

### 5.3 Step Configuration

The pipeline has three kinds of steps:

#### 1. Bespoke Steps (by name)

Certain step names are recognized by the engine and handled internally. Currently the only bespoke step is `"simulation"`. It requires no `command`, `type`, or `path` — the engine uses the existing VCT `build_workflow()` code to generate all parallel simulation tasks. The simulation step's configuration includes paths to the model specification and branch configuration files, plus per-task SLURM resources:

```yaml
- name: "simulation"
  model_specification: "src/.../model_spec.yaml"
  branch_configuration: "src/.../branches.yaml"
  resources:
    memory: 5
    runtime: "03:00:00"
```

The `project` and `queue` for simulation tasks come from the top-level pipeline settings. Additional bespoke step names can be added in the future.

#### 2. Structured Steps (type + path + args)

Instead of writing a full command, users specify a `type` and `path`. The engine infers the shell command from the type. An optional `args` field appends additional arguments. This is the recommended approach for common tasks.

| Type | Inferred Command | Path Behavior |
|------|-----------------|---------------|
| `pytest` | `pytest <path(s)> <args>` | File(s) or directory. Multiple paths run in a single pytest invocation. |
| `notebook` | `papermill <path> <output_directory>/executed/<notebook_name> <args>` | Single notebook file. Output path defaults to `{output_directory}/executed/`. |
| `python` | `python <path> <args>` | Single Python file. |
| `shell` | `bash <path> <args>` | Single shell script. |

Examples:

```yaml
# Run specific test modules with extra args
- name: "pre_tests"
  type: pytest
  path:
    - tests/test_lbwsg.py
    - tests/test_mortality.py
  args: "--runslow -x"
  resources:
    memory: 10
    runtime: "01:00:00"

# Run all tests in a directory
- name: "post_v_and_v"
  type: pytest
  path: tests/automated_v_and_v/
  args: "--results-dir {output_directory}"
  environment: "vivarium_gates_mncnh_artifact"
  resources:
    memory: 20
    runtime: "02:00:00"

# Run a notebook
- name: "neonatal_results"
  type: notebook
  path: tests/model_notebooks/results/neonatal.ipynb
  args: "-p model_dir {output_directory}"
  environment: "vivarium_gates_mncnh_artifact"
  resources:
    memory: 20
    runtime: "02:00:00"

# Run a Python script
- name: "postprocess"
  type: python
  path: scripts/postprocess.py
  args: "--input {output_directory}"
  resources:
    memory: 8
    runtime: "00:30:00"
```

#### 3. Raw Command Steps

For maximum flexibility, users can provide a `command` field with the exact shell command to run. The engine wraps it with `conda run --no-banner -n <env> <command>`:

```yaml
- name: "custom_step"
  command: "papermill results/maternal.ipynb {output_directory}/executed/maternal.ipynb -p model_dir {output_directory}"
  environment: "vivarium_gates_mncnh_artifact"
  resources:
    memory: 20
    runtime: "02:00:00"
```

#### Validation Rules

Each step must satisfy exactly one of:
- **Bespoke**: `name` is a recognized bespoke name (e.g., `"simulation"`) — no `command`, `type`, or `path` required.
- **Structured**: `type` and `path` are provided, `command` is absent.
- **Raw command**: `command` is provided, `type` and `path` are absent.

Steps that mix `command` with `type`/`path`, or non-bespoke steps that provide none, are rejected at config validation time.

### 5.4 Placeholder Variables

The YAML supports runtime placeholder substitution for values only known at execution time:

| Placeholder | Replaced With |
|-------------|---------------|
| `{output_directory}` | The workflow's output directory (from YAML top-level or CLI override) |
| `{model_number}` | The model number (if provided via CLI) |
| `{timestamp}` | ISO-format timestamp of pipeline launch |

## 6. Architecture

### 6.1 Core Concept: `psimulate workflow` Builds a Full Pipeline DAG

`psimulate workflow pipeline.yaml` is a new subcommand that reads the pipeline YAML and builds a single Jobmon workflow containing all steps. It is independent of `psimulate run` — it does not augment an existing `run` command but rather orchestrates the full pipeline from scratch.

When the pipeline includes a step named `"simulation"`, the engine calls the existing VCT `build_workflow()` function internally to generate the parallel simulation tasks (using the model spec and branches paths from the simulation step's config). These simulation tasks are inserted into the pipeline's DAG at the marker position, alongside the pre/post tasks.

This means:
- **Dedicated subcommand**: `psimulate workflow` is its own entry point. `psimulate run`, `restart`, and `expand` are unchanged.
- **Self-contained config**: the pipeline YAML contains everything needed — step definitions, simulation config paths, output directory, project/queue.
- **Ordering** is enforced by the Jobmon DAG, not by sequential Python code.
- **Halt-on-failure** is Jobmon's native behavior — no custom orchestration needed.
- **Monitoring** comes free via Jobmon's web GUI (task status, logs, retries).
- **Resume** comes free via Jobmon's workflow resume (`workflow.run(resume=True)`).

### 6.2 Component Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                   vivarium_cluster_tools                      │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  psimulate workflow pipeline.yaml                       │ │
│  │    [-o /results] [-P proj] [-q all.q]                   │ │
│  └────────┬────────────────────────────────────────────────┘ │
│           │                                                  │
│           ▼                                                  │
│  ┌────────────────────┐   ┌───────────────────┐              │
│  │ PipelineConfig     │   │  CommandResolver   │              │
│  │ (YAML parser +     │   │  (type+path→cmd)   │              │
│  │  CLI overrides)    │   │                    │              │
│  └────────┬───────────┘   └────────┬──────────┘              │
│           │                        │                         │
│           └──────────┬─────────────┘                         │
│                      ▼                                       │
│           ┌──────────────────────┐                           │
│           │ PipelineWorkflow-    │                           │
│           │ Builder              │  Builds full DAG incl.    │
│           │                      │  simulation tasks         │
│           └──────────┬───────────┘                           │
│                      ▼                                       │
│           ┌──────────────────────┐                           │
│           │  Jobmon Workflow      │  Single DAG with all     │
│           │  (pre → sim → post)  │  tasks + dependencies    │
│           └──────────────────────┘                           │
└──────────────────────────────────────────────────────────────┘
           │
           │  Each task runs on SLURM:
           ▼
┌─────────────────────────────────────────────────────────┐
│  SLURM Cluster                                          │
│                                                         │
│  Task: "pre_tests_pytest"                               │
│    → conda run -n <env> pytest tests/test_lbwsg.py ...  │
│                                                         │
│  Task: "sim_task_0001"                (depends on above) │
│  Task: "sim_task_0002"                (depends on above) │
│  Task: "sim_task_..."     (standard psimulate sim tasks) │
│                                                         │
│  Task: "post_tests_pytest"    (depends on all sim tasks) │
│    → conda run -n <env> pytest tests/automated_v_and_v/ │
│                                                         │
│  Task: "post_notebook_neonatal"   (depends on sim tasks) │
│    → conda run -n <env> papermill neonatal.ipynb ...     │
└─────────────────────────────────────────────────────────┘
```

### 6.3 DAG Structure

With `psimulate workflow`, the DAG follows the order of the `steps` list. Steps before the `"simulation"` step are upstream of the simulation tasks; steps after are downstream.

```
pre_tests_pytest
       │
       ▼
  ┌─────────────────────────────┐
  │ sim_task_0001  sim_task_0002│  (standard psimulate tasks,
  │ sim_task_0003  sim_task_... │   one per draw/seed/branch)
  └─────────────────────────────┘
       │
       ├──────────────────────┐
       ▼                     ▼
post_tests_pytest   post_notebook_neonatal
                    post_notebook_maternal
```

Each step depends on ALL tasks from the previous step. Steps before the simulation marker are wired as upstream of ALL simulation tasks. Steps after the simulation marker depend on ALL simulation tasks.

Without `psimulate workflow`, using `psimulate run` produces a DAG with just the simulation tasks (same as today).

### 6.4 Key Classes

#### `PipelineConfig`
- Loads and validates the pipeline YAML.
- Parses top-level `project`, `queue`, `output_directory`.
- Applies CLI overrides (`-o`, `-P`, `-q`).
- Resolves placeholder variables (`{output_directory}`, `{timestamp}`).
- Parses the ordered `steps` list and identifies bespoke steps (e.g., `"simulation"`).
- Validates step configuration: each step must be exactly one of bespoke, structured (type+path), or raw command.
- For the simulation step, extracts `model_specification` and `branch_configuration` paths.
- Validates that referenced conda environments exist.
- Provides access to the steps-before-simulation and steps-after-simulation (split at the marker).

#### `CommandResolver`
- Given a step's `type`, `path`, and `args`, resolves the full shell command string.
- Maps types to commands: `pytest` → `pytest`, `notebook` → `papermill`, `python` → `python`, `shell` → `bash`.
- For `notebook` type, constructs the output path using `{output_directory}/executed/{notebook_name}`.
- Validates that referenced paths exist.
- Used by `PipelineWorkflowBuilder` when building tasks for structured steps.

#### `PipelineWorkflowBuilder`
- Takes a `PipelineConfig` and builds a complete Jobmon workflow from scratch.
- For the simulation step, calls the existing VCT `build_workflow()` with the model spec, branches, and simulation resources to generate parallel simulation tasks.
- For structured steps, uses `CommandResolver` to produce the shell command.
- For raw command steps, uses the command as-is.
- Creates a Jobmon task for each non-simulation step, wrapping the resolved command with `conda run --no-banner -n <env> <command>`.
- Uses global `project` and `queue` for all tasks. Uses per-step `resources` for memory, runtime, cores.
- Wires up `add_upstream()` dependencies in `steps` order: steps before marker → simulation tasks → steps after marker.
- Returns the complete Jobmon `Workflow`.

#### Task Template (for non-simulation steps)

All non-simulation steps share a single generic `TaskTemplate` with a `command_template` that wraps the user-provided command with `conda run`:

| Command Template |
|-----------------|
| `conda run --no-banner -n {env} {command}` |

Simulation tasks use the existing psimulate `TaskTemplate` from PR #302.

### 6.5 Pseudocode: How `PipelineWorkflowBuilder` Integrates with `build_workflow()`

This section shows how the pipeline feature plugs into the existing psimulate codebase. PR #302 refactors psimulate to use Jobmon instead of Redis Queue for simulation task orchestration. The `psimulate workflow` subcommand builds a complete DAG that may include simulation tasks by calling into existing VCT code.

#### 6.5.1 Existing `runner.main()` Flow (Post PR #302, Unchanged)

After PR #302, `runner.main()` builds a Jobmon workflow of parallel simulation tasks and submits it. This function is **not modified** by the pipeline feature — `psimulate run` continues to work exactly as before:

```python
# runner.py — main() after PR #302 (simplified, UNCHANGED)

def main(command, input_paths, native_specification, ...):
    """Entry point called by psimulate run/restart/expand."""

    # 1. Parse branches and build the parameter space
    keyspace = Keyspace.from_entry_point_args(input_paths.branch_configuration, ...)
    
    # 2. Build the list of simulation jobs (one per draw/seed/branch combo)
    jobs, num_already_completed = build_job_list(
        command, keyspace, finished_sim_metadata, input_paths, ...
    )

    # 3. Build the Jobmon workflow containing one task per simulation job
    workflow, sim_tasks = build_workflow(
        jobs=jobs,
        native_specification=native_specification,
        output_directory=output_directory,
    )

    # 4. Submit the workflow to Jobmon and block until completion
    status = workflow.run()

    # 5. Report results
    print(f"Workflow completed with status: {status}")
```

#### 6.5.2 `build_workflow()` — Creates Simulation Tasks (PR #302)

This function is the bridge between psimulate's job list and Jobmon's workflow API. It creates one Jobmon task per simulation job. It is reused by `PipelineWorkflowBuilder` when a simulation step is present:

```python
# psimulate/jobmon_config/workflow.py — from PR #302 (simplified pseudocode)

from jobmon.client.api import Tool, TaskTemplate, Workflow

def build_workflow(jobs, native_specification, output_directory, workflow=None):
    """Build a Jobmon workflow containing one task per simulation job.
    
    Parameters
    ----------
    jobs : list[dict]
        Each dict is a serialized JobParameters (model_spec, branch_config,
        input_draw, random_seed, results_path, ...).
    native_specification : NativeSpecification
        SLURM resources (project, queue, memory, runtime, etc.).
    output_directory : Path
        Where simulation results are written.
    workflow : Workflow, optional
        If provided, add tasks to this existing workflow instead of
        creating a new one. Used by PipelineWorkflowBuilder.
    
    Returns
    -------
    workflow : jobmon.client.api.Workflow
        A Jobmon workflow ready to be run via workflow.run().
    sim_tasks : list[jobmon.client.api.Task]
        The simulation tasks added to the workflow.
    """

    # --- Set up Jobmon Tool and TaskTemplate ---
    tool = Tool.get_tool(name="psimulate")
    
    sim_template = tool.get_task_template(
        template_name="vivarium_simulation",
        command_template=(
            "python -m vivarium_cluster_tools.psimulate.worker.run_simulation "
            "--model-spec {model_specification} "
            "--branch-config '{branch_configuration}' "
            "--input-draw {input_draw} "
            "--random-seed {random_seed} "
            "--results-path {results_path}"
        ),
        default_compute_resources={
            "queue": native_specification.queue,
            "project": native_specification.project,
            "memory": native_specification.peak_memory,
            "runtime": native_specification.max_runtime,
            "cores": 1,
        },
    )

    # --- Create the Workflow (or use existing) ---
    if workflow is None:
        workflow = tool.create_workflow(
            name=f"psimulate_{output_directory.name}",
            default_max_attempts=3,
        )

    # --- Create one Task per simulation job ---
    sim_tasks = []
    for job in jobs:
        task = sim_template.create_task(
            name=f"sim_draw{job['input_draw']}_seed{job['random_seed']}",
            model_specification=job["model_specification"],
            branch_configuration=json.dumps(job["branch_configuration"]),
            input_draw=job["input_draw"],
            random_seed=job["random_seed"],
            results_path=job["results_path"],
        )
        workflow.add_task(task)
        sim_tasks.append(task)

    return workflow, sim_tasks
```

#### 6.5.3 `workflow_main()` — Entry Point for `psimulate workflow`

This is a **new function** — it does not modify `runner.main()`. It is the entry point for the `psimulate workflow` subcommand:

```python
# psimulate/pipeline/runner.py — entry point for `psimulate workflow` (pseudocode)

def workflow_main(pipeline_path: Path, cli_overrides: dict):
    """Entry point for the `psimulate workflow` subcommand.

    This is a NEW function — it does not modify runner.main().
    It builds the full pipeline DAG from the YAML and submits it.
    """

    # 1. Load and validate the pipeline YAML
    pipeline_config = PipelineConfig.from_yaml(pipeline_path)

    # 2. Apply CLI overrides (output_directory, project, queue)
    pipeline_config.apply_overrides(cli_overrides)

    # 3. Resolve placeholder variables in step commands/args
    pipeline_config.resolve_placeholders()

    # 4. Build the full workflow DAG
    builder = PipelineWorkflowBuilder(pipeline_config)
    workflow = builder.build()

    # 5. Submit the workflow to Jobmon and block until completion
    status = workflow.run()

    # 6. Slack notification
    send_slack_notification(
        workflow_name=pipeline_config.name,
        status=status,
        monitoring_url=workflow.status_url,
        results_dir=pipeline_config.output_directory,
    )

    # 7. Print summary
    print(f"Workflow completed with status: {status}")
    print(f"Monitor: {workflow.status_url}")
```

#### 6.5.4 `PipelineConfig` — YAML Parsing and Validation

```python
# psimulate/pipeline/config.py (pseudocode)

from dataclasses import dataclass, field
from pathlib import Path
import yaml

BESPOKE_STEP_NAMES = {"simulation"}


@dataclass
class StepConfig:
    """Configuration for a single pipeline step."""
    name: str
    # Raw command mode
    command: str | None = None
    # Structured mode
    type: str | None = None         # pytest, notebook, python, shell
    path: str | list[str] | None = None
    args: str | None = None
    # Common fields
    environment: str | None = None  # None = use default
    resources: dict | None = None   # memory, runtime, cores
    # Bespoke step fields (simulation)
    model_specification: str | None = None
    branch_configuration: str | None = None

    @property
    def is_bespoke(self) -> bool:
        """True if this is a recognized bespoke step (e.g., 'simulation')."""
        return self.name in BESPOKE_STEP_NAMES

    @property
    def is_simulation(self) -> bool:
        return self.name == "simulation"

    @property
    def is_structured(self) -> bool:
        """True if the step uses type + path."""
        return self.type is not None and self.path is not None

    @property
    def is_raw_command(self) -> bool:
        """True if the step uses a raw command string."""
        return self.command is not None


@dataclass
class PipelineConfig:
    """Parsed and validated pipeline configuration."""
    name: str
    project: str
    queue: str
    output_directory: Path
    default_environment: str | None
    steps: list[StepConfig]

    @classmethod
    def from_yaml(cls, path: Path) -> "PipelineConfig":
        """Load, validate, and return a PipelineConfig from a YAML file."""
        with open(path) as f:
            raw = yaml.safe_load(f)

        pipeline = raw["pipeline"]

        # Parse each step
        steps = []
        for step_dict in pipeline["steps"]:
            step = StepConfig(
                name=step_dict["name"],
                command=step_dict.get("command"),
                type=step_dict.get("type"),
                path=step_dict.get("path"),
                args=step_dict.get("args"),
                environment=step_dict.get("environment"),
                resources=step_dict.get("resources"),
                model_specification=step_dict.get("model_specification"),
                branch_configuration=step_dict.get("branch_configuration"),
            )
            steps.append(step)

        config = cls(
            name=pipeline["name"],
            project=pipeline["project"],
            queue=pipeline["queue"],
            output_directory=Path(pipeline["output_directory"]),
            default_environment=pipeline.get("default_environment"),
            steps=steps,
        )
        config._validate()
        return config

    def apply_overrides(self, cli_overrides: dict):
        """Apply CLI overrides for output_directory, project, queue."""
        if cli_overrides.get("output_directory"):
            self.output_directory = Path(cli_overrides["output_directory"])
        if cli_overrides.get("project"):
            self.project = cli_overrides["project"]
        if cli_overrides.get("queue"):
            self.queue = cli_overrides["queue"]

    def _validate(self):
        """Validate the pipeline configuration. Raise on errors."""
        # At most one simulation step
        sim_steps = [s for s in self.steps if s.is_simulation]
        if len(sim_steps) > 1:
            raise ValueError(
                f"Pipeline can have at most one step named 'simulation', "
                f"found {len(sim_steps)}."
            )

        # Simulation step must have model_spec and branches
        for step in sim_steps:
            if not step.model_specification or not step.branch_configuration:
                raise ValueError(
                    "The 'simulation' step must provide "
                    "'model_specification' and 'branch_configuration' paths."
                )

        # Non-bespoke steps must have command OR type+path
        for step in self.steps:
            if step.is_bespoke:
                continue
            if step.is_raw_command and step.is_structured:
                raise ValueError(
                    f"Step '{step.name}': provide 'command' OR 'type'+'path', not both."
                )
            if not step.is_raw_command and not step.is_structured:
                raise ValueError(
                    f"Step '{step.name}': must provide 'command' or 'type'+'path'."
                )

        # Unique step names
        names = [s.name for s in self.steps]
        if len(names) != len(set(names)):
            raise ValueError("Step names must be unique.")

        # Validate conda environments exist
        available_envs = _get_conda_env_names()
        for step in self.steps:
            if step.environment and step.environment not in available_envs:
                raise ValueError(
                    f"Step '{step.name}' references conda environment "
                    f"'{step.environment}' which does not exist."
                )

    def resolve_placeholders(self):
        """Substitute {output_directory}, {timestamp}, etc. in step commands and args."""
        replacements = {
            "output_directory": str(self.output_directory),
            "timestamp": datetime.now().isoformat(),
        }
        for step in self.steps:
            if step.command:
                step.command = step.command.format(**replacements)
            if step.args:
                step.args = step.args.format(**replacements)

    @property
    def sim_step(self) -> StepConfig | None:
        """The simulation step, if present."""
        for step in self.steps:
            if step.is_simulation:
                return step
        return None

    @property
    def pre_sim_steps(self) -> list[StepConfig]:
        """Steps before the simulation marker (in order)."""
        result = []
        for step in self.steps:
            if step.is_simulation:
                break
            result.append(step)
        return result

    @property
    def post_sim_steps(self) -> list[StepConfig]:
        """Steps after the simulation marker (in order)."""
        result = []
        past_sim = False
        for step in self.steps:
            if step.is_simulation:
                past_sim = True
                continue
            if past_sim:
                result.append(step)
        return result
```

#### 6.5.5 `PipelineWorkflowBuilder` — Builds the Complete Workflow

This is the core class that builds the full pipeline DAG from scratch:

```python
# psimulate/pipeline/builder.py (pseudocode)

from jobmon.client.api import Tool, TaskTemplate, Workflow, Task
from vivarium_cluster_tools.psimulate.jobmon_config.workflow import build_workflow

# —— Command Resolution ——

COMMAND_RESOLVERS = {
    "pytest":   lambda path, args: f"pytest {_join_paths(path)} {args or ''}".strip(),
    "notebook": lambda path, args: f"papermill {path} {{output_directory}}/executed/{Path(path).name} {args or ''}".strip(),
    "python":   lambda path, args: f"python {path} {args or ''}".strip(),
    "shell":    lambda path, args: f"bash {path} {args or ''}".strip(),
}

def _join_paths(path) -> str:
    """Normalize path to a space-separated string."""
    if isinstance(path, list):
        return " ".join(str(p) for p in path)
    return str(path)

def resolve_command(step: StepConfig) -> str:
    """Resolve a step's configuration into a shell command string.

    For raw command steps, returns the command as-is.
    For structured steps, uses the type to infer the command.
    Not called for bespoke steps.
    """
    if step.is_raw_command:
        return step.command
    if step.is_structured:
        resolver = COMMAND_RESOLVERS[step.type]
        return resolver(step.path, step.args)
    raise ValueError(f"Step '{step.name}' has no command, type, or recognized bespoke name.")


# —— Workflow Builder ——

class PipelineWorkflowBuilder:
    """Builds a complete Jobmon workflow from a pipeline configuration.

    For the simulation step, delegates to the existing VCT build_workflow()
    to generate the parallel simulation tasks. For all other steps, creates
    Jobmon tasks from resolved shell commands. Wires up the full DAG:

        pre_step_1 -> pre_step_2 -> ... -> [all sim tasks] -> ... -> post_step_1 -> post_step_2
    """

    def __init__(self, pipeline_config: PipelineConfig):
        self.config = pipeline_config
        self.tool = Tool.get_tool(name="psimulate")

        # Generic task template for non-simulation steps
        self.command_template = self.tool.get_task_template(
            template_name="pipeline_command_step",
            command_template="conda run --no-banner -n {env} {command}",
        )

    def build(self) -> Workflow:
        """Build the full pipeline DAG and return the Jobmon Workflow."""

        # —— Create the workflow ——
        workflow = self.tool.create_workflow(
            name=self.config.name,
            default_max_attempts=3,
        )

        # —— Build simulation tasks (if simulation step exists) ——
        sim_tasks = []
        if self.config.sim_step is not None:
            sim_tasks = self._build_simulation_tasks(workflow)

        # —— Build pre/post step tasks ——
        pre_tasks = self._build_step_tasks(self.config.pre_sim_steps)
        post_tasks = self._build_step_tasks(self.config.post_sim_steps)

        # —— Wire pre-simulation steps in sequence ——
        for i in range(1, len(pre_tasks)):
            pre_tasks[i].add_upstream(pre_tasks[i - 1])

        # —— Wire last pre-step -> ALL simulation tasks ——
        if pre_tasks and sim_tasks:
            last_pre_task = pre_tasks[-1]
            for sim_task in sim_tasks:
                sim_task.add_upstream(last_pre_task)

        # —— Wire ALL simulation tasks -> first post-step ——
        if post_tasks and sim_tasks:
            first_post_task = post_tasks[0]
            for sim_task in sim_tasks:
                first_post_task.add_upstream(sim_task)

        # —— Wire pre -> post directly if no simulation step ——
        if pre_tasks and post_tasks and not sim_tasks:
            post_tasks[0].add_upstream(pre_tasks[-1])

        # —— Wire post-simulation steps in sequence ——
        for i in range(1, len(post_tasks)):
            post_tasks[i].add_upstream(post_tasks[i - 1])

        # —— Add all tasks to workflow ——
        for task in pre_tasks + post_tasks:
            workflow.add_task(task)

        return workflow

    def _build_simulation_tasks(self, workflow: Workflow) -> list[Task]:
        """Use the existing VCT code to build parallel simulation tasks.

        This delegates to the same build_workflow() function that
        `psimulate run` uses — we just extract the simulation tasks
        and add them to our pipeline workflow instead.
        """
        sim_step = self.config.sim_step
        native_spec = NativeSpecification(
            project=self.config.project,
            queue=self.config.queue,
            peak_memory=sim_step.resources.get("memory", 3),
            max_runtime=sim_step.resources.get("runtime", "03:00:00"),
        )

        # Parse branches -> keyspace -> job list (reuses existing VCT code)
        keyspace = Keyspace.from_entry_point_args(
            sim_step.branch_configuration, ...
        )
        jobs, _ = build_job_list(keyspace, ...)

        # Build simulation tasks using existing VCT build_workflow()
        _, sim_tasks = build_workflow(
            jobs=jobs,
            native_specification=native_spec,
            output_directory=self.config.output_directory,
            workflow=workflow,  # pass in our workflow so tasks are added to it
        )
        return sim_tasks

    def _build_step_tasks(self, steps: list[StepConfig]) -> list[Task]:
        """Create a Jobmon Task for each non-simulation step."""
        tasks = []
        for step in steps:
            # Resolve the command (handles both raw and structured steps)
            command = resolve_command(step)

            # Resolve environment: step -> pipeline default -> current env
            env = (
                step.environment
                or self.config.default_environment
                or os.environ.get("CONDA_DEFAULT_ENV", "base")
            )

            # Compute resources
            resources = step.resources or {}

            task = self.command_template.create_task(
                name=step.name,
                compute_resources={
                    "queue": self.config.queue,
                    "project": self.config.project,
                    "memory": resources.get("memory", 4),
                    "runtime": resources.get("runtime", "01:00:00"),
                    "cores": resources.get("cores", 1),
                },
                env=env,
                command=command,
            )
            tasks.append(task)

        return tasks
```

#### 6.5.6 Complete Execution Trace — Example

To illustrate the full flow, here's a trace for running the MNCNH pipeline:

```
$ psimulate workflow pipeline.yaml

+-------------------------------------------------------------------
| 1. CLI parses arguments
|    -> pipeline_path = "pipeline.yaml"
|    -> cli_overrides = {} (no overrides)
+-------------------------------------------------------------------
         |
         v
+-------------------------------------------------------------------
| 2. PipelineConfig.from_yaml("pipeline.yaml")
|    -> Parses YAML: 5 steps defined
|    -> Top-level: project="proj_simscience_prod", queue="all.q",
|      output_directory="/mnt/team/.../results/model_29"
|    -> Validates: at most 1 "simulation" step found
|    -> Validates: simulation step has model_spec + branches
|    -> Validates: conda envs "vivarium_gates_mncnh_artifact" exists
|    -> Validates: each non-bespoke step has command or type+path
|    -> Resolves: {output_directory} in args/commands
|    -> pre_sim_steps  = [pre_tests_pytest]
|    -> post_sim_steps = [post_tests_pytest, post_notebook_neonatal,
|                         post_notebook_maternal]
+-------------------------------------------------------------------
         |
         v
+-------------------------------------------------------------------
| 3. PipelineWorkflowBuilder.build()
|
|    a. Create Jobmon Workflow: "mncnh_main_sim"
|
|    b. Build simulation tasks (_build_simulation_tasks):
|       -> Parse branches.yaml -> Keyspace
|       -> build_job_list() -> 100 JobParameters
|       -> build_workflow(jobs, native_spec, output_dir, workflow)
|       -> 100 independent simulation tasks added to workflow
|
|    c. Build pre-step tasks (resolve_command for each):
|       -> "pre_tests_pytest": type=pytest, path=[3 files], args="--runslow"
|         Resolved: "pytest tests/test_lbwsg.py tests/test_mortality.py
|                    tests/test_pregnancy.py --runslow"
|         Wrapped:  conda run --no-banner -n mncnh <resolved>
|
|    d. Build post-step tasks:
|       -> "post_tests_pytest": type=pytest, path=dir, args="--results-dir ..."
|         Resolved: "pytest tests/automated_v_and_v/ --results-dir /mnt/..."
|       -> "post_notebook_neonatal": type=notebook, path=neonatal.ipynb
|         Resolved: "papermill .../neonatal.ipynb /mnt/.../executed/neonatal.ipynb
|                    -p model_dir /mnt/..."
|       -> "post_notebook_maternal": command=<raw>
|         Used as-is (already resolved placeholders)
|
|    e. Wire pre-step -> sim tasks:
|       -> sim_0001.add_upstream(pre_tests_pytest)
|       -> ... (all 100 sim tasks depend on pre_tests_pytest)
|
|    f. Wire sim tasks -> post-steps:
|       -> post_tests_pytest.add_upstream(sim_0001)
|       -> ... (post_tests_pytest depends on ALL 100 sim tasks)
|
|    g. Wire post-steps in sequence:
|       -> post_notebook_neonatal.add_upstream(post_tests_pytest)
|       -> post_notebook_maternal.add_upstream(post_tests_pytest)
|
|    h. Add all new tasks to workflow
|
|    Result: workflow has 104 tasks total
+-------------------------------------------------------------------
         |
         v
+-------------------------------------------------------------------
| 4. workflow.run()
|    -> Submits to Jobmon, which schedules tasks on SLURM
|    -> Blocks until all tasks complete (or fail)
|
|    Execution order enforced by DAG:
|      pre_tests_pytest (1 task on SLURM)
|        -> passes -> 100 sim tasks launch in parallel
|          -> all 100 pass -> post_tests_pytest launches
|            -> passes -> post_notebook_neonatal + post_notebook_maternal launch
|
|    If pre_tests_pytest fails:
|        X 100 sim tasks NEVER LAUNCH
|        X post-steps NEVER LAUNCH
|        -> workflow.run() returns "ERROR_FATAL"
+-------------------------------------------------------------------
         |
         v
+-------------------------------------------------------------------
| 5. Post-workflow: Slack notification + summary report
|    -> send_slack_notification(status="DONE", ...)
|    -> "psimulate workflow DONE: mncnh_main_sim"
+-------------------------------------------------------------------
```



### 6.6 Environment Handling

Each step can specify a conda environment name. The execution strategy:

1. If `environment` is `null` or matches the current active environment → the `conda run -n` wrapper still uses the current env name (for consistency).
2. Before building the workflow, verify all referenced environments exist (`conda env list`) and fail fast with a clear error if any don't.
3. The environment name is passed as a template argument to the Jobmon `command_template`, so it's part of the task's command string.

This approach:
- Doesn't require activation/deactivation (which is fragile in subprocesses).
- Works on SLURM nodes which may not have the user's shell config.
- Each task runs in its own isolated environment.

### 6.7 CLI Interface

`psimulate workflow` is a new Click subcommand added to the existing `psimulate` group:

```bash
psimulate workflow [OPTIONS] PIPELINE_YAML

Options:
  -o, --output-directory PATH   Override output directory from YAML
  -P, --project TEXT            Override SLURM project from YAML
  -q, --queue TEXT              Override SLURM queue from YAML
  --pdb                         Drop to debugger on error
  -v, --verbose                 Increase verbosity (use -vvv for max)
```

The subcommand is registered alongside `run`, `restart`, `expand`, and `test`:

```python
@psimulate.command()
@click.argument("pipeline_yaml", type=click.Path(exists=True))
@click.option("-o", "--output-directory", type=click.Path(), default=None)
@click.option("-P", "--project", type=str, default=None)
@click.option("-q", "--queue", type=str, default=None)
@click.option("--pdb", is_flag=True, default=False)
@click.option("-v", "--verbose", count=True)
def workflow(pipeline_yaml, output_directory, project, queue, pdb, verbose):
    """Run a multi-step pipeline defined in a YAML configuration file."""
    cli_overrides = {
        "output_directory": output_directory,
        "project": project,
        "queue": queue,
    }
    workflow_main(
        pipeline_path=Path(pipeline_yaml),
        cli_overrides=cli_overrides,
    )
```

Existing subcommands (`run`, `restart`, `expand`, `test`) are completely unchanged.

## 7. Interaction with Existing Work

### PR #284 (`run_main_sim.py`)
The `run_main_sim.py` script from PR #284 handles multi-location orchestration (per-location iteration, psimulate invocation, completion checking). It operates independently of the pipeline feature — it can be run directly as a standalone script, or it can be included as a step in the pipeline YAML:

```yaml
- name: "run_all_locations"
  type: python
  path: src/vivarium_gates_mncnh/tools/run_main_sim.py
  args: "-a model_29.0.3 -l Ethiopia"
  environment: "vivarium_gates_mncnh_simulation"
  resources:
    memory: 4
    runtime: "48:00:00"
```

Note: if multi-location orchestration is used as a step, it replaces the simulation step's role. In that case the `"simulation"` bespoke step would not be needed, since the script handles the simulation internally. However, the recommended pattern for single-location runs is to use the `"simulation"` bespoke step and let psimulate insert its standard flat simulation tasks.

The git tagging and `MODEL_RESULTS_DIR` update logic remain project-specific (run manually or as a command step in the pipeline).

### PR #302 (Jobmon Refactor in `vivarium_cluster_tools`)
The pipeline directly extends PR #302's foundation:
- The existing `build_workflow()` function creates simulation tasks and a Jobmon workflow. The `psimulate workflow` subcommand calls `build_workflow()` internally when a simulation step is present, passing in the pipeline's workflow so simulation tasks are added to the same DAG.
- The existing `Tool`, `TaskTemplate`, and `Workflow` patterns from PR #302's `jobmon_config/workflow.py` are reused directly.
- Task-level metadata (JSON files) and the Jobmon monitoring URL are available to downstream pipeline steps via placeholder variables.

### `vivarium_testing_utils` (unchanged)
The testing utils repo is unchanged. It continues to provide:
- `FuzzyChecker` — used inside pytest test suites that the pipeline launches.
- `automated_validation/` — used inside V&V test suites.
- `pytest_plugin.py` — provides markers and fixtures for test suites.

These are *payloads* that run inside pipeline steps, not part of the pipeline engine.

## 8. Error Handling and Reporting

### Halt-on-Failure (Jobmon Native)

Since the pipeline is a Jobmon DAG, failure propagation is automatic:

| Scenario | Behavior |
|----------|----------|
| Pre-test task fails | Simulation tasks never launch. Post-test tasks never launch. Workflow finishes with error status. |
| One simulation location fails | Other location tasks may still run (parallel paths). Post-test tasks do NOT launch (they depend on ALL sim tasks). |
| All simulations succeed, post-test fails | Results are preserved. Workflow finishes with error status. |
| All steps succeed | Workflow finishes with `DONE` status. |

For the case where you want post-tests to run even on partial simulation success, this can be configured by **not** adding sim→post dependencies (making post-tests independent), or by using a sentinel "gate" task. This is an advanced configuration option, not the default.

### Monitoring and Visibility

Since every step is a Jobmon task:
- The **Jobmon web GUI** shows the full pipeline DAG, task statuses, logs, and retry history.
- The pipeline CLI prints the monitoring URL at launch.
- SLURM stdout/stderr logs are captured per-task in the output directory.

### Summary Report

The CLI prints a summary after the workflow completes:

```
═══════════════════════════════════════════════════════
Pipeline Summary: mncnh_main_sim
═══════════════════════════════════════════════════════
  pre_tests_pytest:        DONE
  simulation_ethiopia:     DONE
  simulation_nigeria:      DONE
  post_tests_pytest:       DONE
  post_notebook_neonatal:  DONE
  post_notebook_maternal:  ERROR (see logs)
  post_notebook_hgb:       DONE
═══════════════════════════════════════════════════════
Workflow status: ERROR_FATAL
Monitor: https://jobmon.ihme.washington.edu/#/workflow/12345
Results: /mnt/team/.../results/model29.0.3
═══════════════════════════════════════════════════════
```

### Slack Notification on Completion

After every psimulate workflow completes — whether launched via `psimulate run` or `psimulate workflow` — psimulate sends a Slack notification to the user who launched it. This runs as post-`workflow.run()` Python code in the launcher process, **not** as a Jobmon task. This is deliberate: a Jobmon task added as the last DAG node would only run if all upstream tasks succeed, meaning failures (the most important case to notify about) would never trigger a notification.

Since `workflow.run()` is a blocking call that returns the workflow's final status, the notification code executes immediately after it returns:

```python
# In workflow_main() or runner.main(), after workflow completes:
status = workflow.run()
send_slack_notification(
    user=os.environ["USER"],
    workflow_name=workflow.name,
    status=status,
    monitoring_url=workflow.status_url,
    results_dir=output_dir,
)
```

#### What the Slack Message Contains

The notification includes:
- **Workflow name** (e.g., `mncnh_main_sim`)
- **Final status**: `DONE` (all tasks succeeded) or `ERROR` (one or more tasks failed)
- **Monitoring URL**: link to the Jobmon web GUI for detailed task-level status
- **Results directory**: path to simulation output
- **Duration**: total wall-clock time from launch to completion

Example Slack message:
```
✅ psimulate workflow DONE: mncnh_main_sim
Duration: 4h 23m
Results: /mnt/team/.../results/model29.0.3
Monitor: https://jobmon.ihme.washington.edu/#/workflow/12345
```

or on failure:
```
❌ psimulate workflow ERROR: mncnh_main_sim
Duration: 1h 12m
3 of 8042 tasks failed
Monitor: https://jobmon.ihme.washington.edu/#/workflow/12345
```

#### How the User Is Identified

The Slack recipient is determined by mapping the Unix username (`$USER`) to a Slack user ID. This mapping can be:
1. **Environment variable**: `SLACK_USER_ID` set by the user (simplest).
2. **Config file**: A shared mapping file (e.g., `~/.psimulate/slack.yaml` or a team-level config).
3. **Slack email lookup**: Query the Slack API using `{username}@uw.edu` to find the Slack user ID.

Option 1 is recommended for the initial implementation due to simplicity. If `SLACK_USER_ID` is not set, the notification is skipped with a warning printed to stdout (not an error — notification failure should never block the workflow).

#### Slack Integration

The notification uses a Slack incoming webhook URL or bot token. The webhook URL is stored as:
- An environment variable (`PSIMULATE_SLACK_WEBHOOK`), or
- A config entry in a shared psimulate config file.

The implementation is a simple HTTP POST — no Slack SDK dependency is needed:

```python
import json
import urllib.request

def send_slack_notification(user, workflow_name, status, monitoring_url, results_dir):
    webhook_url = os.environ.get("PSIMULATE_SLACK_WEBHOOK")
    slack_user_id = os.environ.get("SLACK_USER_ID")
    if not webhook_url or not slack_user_id:
        print("Slack notification skipped (PSIMULATE_SLACK_WEBHOOK or SLACK_USER_ID not set)")
        return
    emoji = "✅" if status == "DONE" else "❌"
    message = f"{emoji} psimulate workflow {status}: {workflow_name}\n"
    message += f"Monitor: {monitoring_url}\n"
    if results_dir:
        message += f"Results: {results_dir}\n"
    payload = {"text": f"<@{slack_user_id}> {message}"}
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"Slack notification failed: {e}")
```

#### Scope: Always On (Not Pipeline-Specific)

Slack notifications fire for **every** psimulate workflow, regardless of how it was launched. This means:
- `psimulate run model_spec.yaml branches.yaml -P proj -q all.q` → notifies when sim finishes
- `psimulate workflow pipeline.yaml` → notifies when the full pipeline finishes

This is a psimulate-level feature, not a pipeline-level feature. It requires no YAML configuration.

#### Edge Case: Launcher Process Killed

If the launcher process is killed (SSH disconnect, node reboot), the notification won't fire since the post-`workflow.run()` code never executes. Mitigation:
- Run psimulate inside `tmux` or `screen` (already common practice for long-running cluster jobs).
- The Jobmon web GUI remains available for manual status checking regardless.

## 9. Future Extensions (Not in Initial Scope)

These are explicitly **out of scope** for the initial implementation but the design should not preclude them:

- **Richer notification channels**: Email notifications, Microsoft Teams integration, or custom webhook payloads beyond the initial Slack support.
- **Multi-model pipelines**: Orchestrate pipelines across multiple models (e.g., PAF sim → main sim) in a single DAG.
- **CI/CD integration**: Trigger pipelines from GitHub Actions or Jenkins.
- **Dynamic step generation**: Steps that generate new steps at runtime (e.g., "for each location, run these post-tests").
- **Resource auto-scaling**: Use Jobmon's resource retry feature to automatically increase memory/runtime on failure.
- **Per-location post-tests**: Run location-specific post-tests that depend on only that location's simulation (requires multi-location support in the pipeline YAML).
- **Inline `locations` list on simulation step**: Allow specifying `locations: ["ethiopia", "nigeria", ...]` directly on the simulation step. The pipeline builder would fan out into one psimulate invocation per location. This would give per-location visibility in the DAG.

## 10. Implementation Milestones

Each milestone is a self-contained unit of work that can be reviewed, tested, and merged independently. They are ordered by dependency (each builds on previous ones).

---

### Milestone 1: Pipeline Configuration Schema & Parser
**Effort**: Small
**Repo**: `vivarium_cluster_tools`

**Deliverables:**
- Define the YAML schema (as documented in Section 5.1).
- Implement `PipelineConfig` class in a new `psimulate/pipeline/` module:
  - Load and validate YAML structure (required fields, step names).
  - Parse top-level `project`, `queue`, `output_directory`, `default_environment`.
  - Parse the ordered `steps` list with three modes: raw `command`, structured `type`+`path`+`args`, and bespoke by name.
  - Validate that at most one step is named `"simulation"`.
  - Validate that the simulation step provides `model_specification` and `branch_configuration`.
  - Validate that non-bespoke steps have either `command` or `type`+`path` (not both).
  - Resolve placeholder variables (`{output_directory}`, `{timestamp}`, etc.).
  - Validate that referenced conda environments exist.
  - Apply CLI overrides (`-o`, `-P`, `-q`).
  - Provide typed access to step configurations (steps before/after simulation).
- Implement `CommandResolver` (or `resolve_command()` function):
  - Maps `type` to command prefix: `pytest` → `pytest`, `notebook` → `papermill`, `python` → `python`, `shell` → `bash`.
  - Handles `path` (single or list) and appends `args`.
  - For `notebook` type, constructs output path using `{output_directory}/executed/{notebook_name}`.
- Unit tests for config loading, validation, placeholder resolution, command resolution, and error cases.

**Acceptance criteria:**
- Invalid YAML produces clear error messages.
- Missing required fields are caught at load time.
- Multiple `"simulation"` steps are rejected with a clear error.
- Simulation step without `model_specification` or `branch_configuration` is rejected.
- Non-bespoke steps that mix `command` with `type`/`path` are rejected.
- Non-bespoke steps with neither `command` nor `type`+`path` are rejected.
- Placeholder values are correctly substituted.
- Steps can appear in any order (the `"simulation"` step can be first, last, or anywhere).
- CLI overrides take precedence over YAML values.
- `CommandResolver` produces correct commands for all four types.
- A pipeline with no simulation step is valid (steps-only pipeline).

---

### Milestone 2: Task Template for Non-Simulation Steps
**Effort**: Small
**Repo**: `vivarium_cluster_tools`

**Deliverables:**
- Define a single generic Jobmon `TaskTemplate` for non-simulation steps:
  - `command_template` wraps the user-provided command with `conda run`.
- The template correctly parameterizes environment name, command string, and resources.
- Unit tests that verify correct command string construction.

**Acceptance criteria:**
- The template produces the correct shell command: `conda run --no-banner -n <env> <command>`.
- Conda environment wrapping works correctly (including the null/current env case).

---

### Milestone 3: Pipeline Workflow Builder & `psimulate workflow` Subcommand
**Effort**: Medium
**Repo**: `vivarium_cluster_tools`

**Dependencies**: Milestones 1 and 2. PR #302 merged.

**Deliverables:**
- Implement `PipelineWorkflowBuilder` class:
  - Creates the Jobmon workflow from scratch (not augmenting an existing one).
  - For the simulation step, calls the existing VCT `build_workflow()` with the model spec, branches, and simulation resources, passing in the pipeline's workflow.
  - For structured steps, uses `CommandResolver` to produce the shell command.
  - For raw command steps, uses the command as-is.
  - Wraps each non-simulation step with `conda run --no-banner -n <env> <command>`.
  - Wires up `add_upstream()` dependencies in step order, with simulation tasks at the marker position.
  - Assigns per-step compute resources.
  - Returns the complete workflow.
- Add `workflow` Click subcommand to `cli.py` with options: `-o`, `-P`, `-q`, `--pdb`, `-v`.
- Implement `workflow_main()` entry point (does **not** modify `runner.main()`):
  - Loads `PipelineConfig` from YAML.
  - Applies CLI overrides.
  - Resolves placeholders.
  - Builds and submits the workflow.
  - Sends Slack notification.
- Unit tests with mocked Jobmon API (verifying correct task count, dependencies, resources).

**Acceptance criteria:**
- `psimulate workflow pipeline.yaml` builds a DAG following the `steps` order.
- `psimulate run`, `restart`, and `expand` are completely unchanged.
- Steps before the `"simulation"` step are upstream of ALL simulation tasks.
- Steps after the `"simulation"` step depend on ALL simulation tasks.
- Per-step resources are correctly assigned.
- Pipeline with only pre-simulation steps works.
- Pipeline with only post-simulation steps works.
- Pipeline with no simulation step works (steps-only pipeline).
- CLI overrides (`-o`, `-P`, `-q`) take precedence over YAML values.
- Structured steps (type+path) resolve to correct commands.

---

### Milestone 5: Slack Notification on Completion
**Effort**: Small
**Repo**: `vivarium_cluster_tools`

**Dependencies**: PR #302 merged.

**Deliverables:**
- Implement `send_slack_notification()` function in a new `psimulate/notifications.py` module.
- Integrate into `workflow_main()` and `runner.main()`: call after `workflow.run()` returns, passing workflow status, name, monitoring URL, and results directory.
- Use `PSIMULATE_SLACK_WEBHOOK` and `SLACK_USER_ID` environment variables for configuration.
- Graceful degradation: if env vars are not set, skip notification with a stdout warning.
- Notification failures are caught and logged but never raise exceptions.
- Unit tests with mocked HTTP calls.

**Acceptance criteria:**
- After any psimulate workflow completes (via `run` or `workflow`), a Slack message is sent to the user.
- Notification fires on both success (`DONE`) and failure (`ERROR`) statuses.
- Missing `PSIMULATE_SLACK_WEBHOOK` or `SLACK_USER_ID` skips notification gracefully.
- A failed Slack API call does not cause psimulate to exit with an error.
- Works with both `psimulate run` and `psimulate workflow`.

---

### Milestone 5: MNCNH Project Integration
**Effort**: Small-Medium
**Repo**: `vivarium_gates_mncnh`

**Dependencies**: Milestone 3, PR #284 merged.

**Deliverables:**
- Create `pipeline.yaml` for the MNCNH project (in project root or `src/vivarium_gates_mncnh/model_specifications/`).
- Integration test: run `psimulate workflow pipeline.yaml` on a minimal config on the cluster.
- Update project documentation/README with pipeline usage.

**Acceptance criteria:**
- `psimulate workflow pipeline.yaml` runs the full MNCNH workflow.
- Pre-tests run and halt the pipeline if they fail.
- Post-simulation V&V tests run after simulation completes.
- The full pipeline is visible in the Jobmon web GUI.

---

### Milestone 6: Documentation & Examples
**Effort**: Small
**Repo**: `vivarium_cluster_tools`

**Dependencies**: Milestone 3.

**Deliverables:**
- User guide: how to write a `pipeline.yaml` for a new project.
- Configuration reference: all YAML fields, defaults, step configuration (command, type+path+args, bespoke).
- Example pipeline YAML for a minimal vivarium model.
- Document the `psimulate workflow` subcommand in the psimulate CLI docs.
- Add to existing Sphinx docs.

**Acceptance criteria:**
- A new vivarium project developer can write a pipeline config by reading the docs alone.

---

### Summary Table

| # | Milestone | Repo | Size | Depends On |
|---|-----------|------|------|------------|
| 1 | Config Schema & Parser | `vivarium_cluster_tools` | S | — |
| 2 | Task Template (non-sim steps) | `vivarium_cluster_tools` | S | — |
| 3 | Workflow Builder & `psimulate workflow` subcommand | `vivarium_cluster_tools` | M | 1, 2, PR #302 |
| 4 | Slack Notification on Completion | `vivarium_cluster_tools` | S | PR #302 |
| 5 | MNCNH Project Integration | `vivarium_gates_mncnh` | S-M | 3, PR #284 |
| 6 | Documentation & Examples | `vivarium_cluster_tools` | S | 3, 4 |

Milestones 1, 2, and 4 can be developed in parallel since they are independent. Milestone 3 depends on 1 and 2. Milestones 5 and 6 can proceed in parallel once 3 is done.
