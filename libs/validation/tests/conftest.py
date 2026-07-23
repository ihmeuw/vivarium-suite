import pandas as pd

# Run the pandas 2 suite with pandas 3 semantics (copy-on-write, str dtype) so
# every CI leg exercises the future behavior ahead of the unpin (MIC-6773).
# Guarded to pandas >=2.1: the Jenkins validation env runs pandas 1.5, where
# these options don't exist.
_PANDAS_VERSION = tuple(int(part) for part in pd.__version__.split(".")[:2])
if (2, 1) <= _PANDAS_VERSION < (3, 0):
    pd.options.mode.copy_on_write = True
    pd.options.future.infer_string = True
