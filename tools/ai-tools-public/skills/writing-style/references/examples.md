# Before and after

Each pair shows a typical draft and a rewrite that follows the `writing-style` rules.
The rewrites keep every fact in the draft.

## PR description

Before:

```text
## Summary
This PR has refactored the config loading logic so that it's now lazily evaluated;
previously the whole config tree was being parsed at import time, which was causing
noticeable slowdowns (e.g. ~2s on cold start) for CLI commands that don't even need
the config. Under the hood, we now cache the parsed result.
```

After:

```text
## Summary
This PR makes config loading lazy. Before this change, the package parsed the whole
config tree at import time. That added about 2 seconds to a cold start, also for CLI
commands that do not use the config.

Now `load_config()` parses the tree on the first call and caches the result. Later
calls return the cached result.
```

The rewrite removes the present perfect, the semicolon, the contraction, the "-ing"
verb, "e.g.", and the metaphor. It gives the cache one name and names the function
that does the work.

## Ticket background (wiki markup)

Before:

```text
h3. Background
When running the export with large inputs, memory usage is observed to spike and
eventually the job gets killed by the scheduler, i.e. OOM. This has been seen
intermittently by several people.
```

After:

```text
h3. Background
The export job uses too much memory when the input is large. The scheduler then
stops the job because it is out of memory (OOM). Several people saw this failure.
It does not occur on every run.
```

The rewrite names the actor in each sentence ("the scheduler stops the job"). It
replaces "is observed to" and "has been seen" with the active voice and simple
tenses. It does not add a count or a date that the draft did not give.

## Commit message body

Before:

```text
Fix flaky test

The test was failing sometimes because the temp dir wasn't being cleaned up between
runs, so leftover files got picked up by the glob.
```

After:

```text
Clean the temporary directory between test runs

The glob in `test_collect` found files that an earlier run left in the temporary
directory. The test then failed on some runs. The fixture now removes the
directory after each test.
```

The subject says what the commit does instead of what the problem was. The body uses
the simple past for the cause and the simple present for the fix.

## PR comment for findings not addressed

Before:

```text
Not addressed: a couple of minor things from review that we decided to punt on for
now; see tickets. Also the docs build is still kind of flaky but that's pre-existing.
```

After:

```text
Filed as tickets:
- PROJ-412: `merge_frames` copies the index twice.
- PROJ-413: The `--dry-run` flag has no test.

Carried out unresolved:
- The docs build failed once in three runs. The same failure occurs on `main`, so
  this PR did not cause it.
```

The rewrite lists each item and gives each a reason. A reviewer who arrives cold can
act on each line.

## Chat reply that explains a bug

Before:

```text
So basically what's happening is that the retry wrapper is swallowing the exception
and then the caller ends up getting None back, which it then tries to index into.
```

After:

```text
The retry wrapper catches the exception and returns `None`. The caller then indexes
into that `None`, which causes the `TypeError` that you see.
```

## Reproduction steps

Before:

```text
To reproduce, you'll want to set up a fresh env and then run the pipeline with the
small config, and you should see the error pop up near the end of stage 2.
```

After:

```text
1. Create a new environment from `environment.yml`.
2. Run `pipeline run configs/small.yaml`.
3. Wait for stage 2 to finish. The error occurs near the end of stage 2.
```

Each step has one instruction in the imperative, and each sentence has fewer than 20
words.
