# Fixed sources for the code hash

`test_the_hash_of_fixed_sources_matches_the_stored_digests` hashes each `.py` and `.ipynb`
file here and compares the result with `digests.json`. The digests must be the same on every
Python version in `libs/dagger/python_versions.json`, so the test checks on each version of
the CI matrix that the canonical text did not change between versions.

When a change to `vivarium.dagger.backend.code_hash` changes the hash on purpose, or a fixed
source is added or edited, regenerate `digests.json` from `libs/dagger` on each supported
Python version and commit it only if all versions agree:

```console
$ for python in python3.11 python3.12 python3.13; do
>   $python -I -c '
> import importlib.util, json, pathlib
> spec = importlib.util.spec_from_file_location(
>     "code_hash", "src/vivarium/dagger/backend/code_hash.py")
> m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
> d = pathlib.Path("tests/backend/data/code_hash")
> out = {}
> for f in sorted(d.iterdir()):
>     h = {".py": m.python_file_hash, ".ipynb": m.notebook_hash}.get(f.suffix)
>     if h:
>         r = h(f)
>         out[f.name] = {"digest": r.digest, "parsed": r.parsed}
> print(json.dumps(out, indent=2))' > /tmp/digests-$python.json
> done
$ diff /tmp/digests-python3.11.json /tmp/digests-python3.12.json \
>   && diff /tmp/digests-python3.12.json /tmp/digests-python3.13.json \
>   && cp /tmp/digests-python3.13.json tests/backend/data/code_hash/digests.json
```

The module is loaded from its file, so each interpreter needs only the standard library.
A change that alters the digest of an unchanged source makes every stored hash in a pipeline
state stale, so every step that is hashed re-runs once after the change is released.
