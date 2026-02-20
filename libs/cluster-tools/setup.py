import json
import os
import sys

from packaging.version import parse
from setuptools import find_packages, setup

with open("python_versions.json", "r") as f:
    supported_python_versions = json.load(f)

python_versions = [parse(v) for v in supported_python_versions]
min_version = min(python_versions)
max_version = max(python_versions)
if not (
    min_version <= parse(".".join([str(v) for v in sys.version_info[:2]])) <= max_version
):
    py_version = ".".join([str(v) for v in sys.version_info[:3]])
    # NOTE: Python 3.5 does not support f-strings
    error = (
        "\n--------------------------------------------\n"
        "Error: Vivarium Cluster Tools runs under python {min_version}-{max_version}.\n"
        "You are running python {py_version}.\n".format(
            min_version=min_version.base_version,
            max_version=max_version.base_version,
            py_version=py_version,
        )
        + "--------------------------------------------\n"
    )
    print(error, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    base_dir = os.path.dirname(__file__)
    src_dir = os.path.join(base_dir, "src")

    about: dict[str, str] = {}
    with open(os.path.join(src_dir, "vivarium_cluster_tools", "__about__.py")) as f:
        exec(f.read(), about)

    with open(os.path.join(base_dir, "README.rst")) as f:
        long_description = f.read()

    install_requires = [
        "vivarium_dependencies[numpy_lt_2,pandas,pyyaml,click,tables,loguru,pyarrow,requests]",
        "vivarium_build_utils>=2.0.1,<3.0.0",
        "drmaa",
        "dill",
        "redis",
        "rq",
        "vivarium>=3.0.0",
        "psutil",
        "layered_config_tree",
    ]

    setup_requires = ["setuptools_scm"]

    lint_requirements = [
        "vivarium_dependencies[lint]",
        "types-setuptools",
        "types-psutil",
        "pyarrow-stubs",
    ]

    test_requirements = [
        "vivarium_dependencies[pytest]",
    ]

    doc_requirements = [
        "vivarium_dependencies[sphinx,sphinx-click,ipython,matplotlib]",
    ]

    interactive_requirements = [
        "vivarium_dependencies[interactive]",
    ]

    setup(
        name=about["__title__"],
        description=about["__summary__"],
        long_description=long_description,
        url=about["__uri__"],
        author=about["__author__"],
        author_email=about["__email__"],
        package_dir={"": "src"},
        packages=find_packages(where="src"),
        include_package_data=True,
        entry_points="""
            [console_scripts]
            psimulate=vivarium_cluster_tools.psimulate.cli:psimulate
            vipin=vivarium_cluster_tools.vipin.cli:vipin
        """,
        install_requires=install_requires,
        extras_require={
            "docs": doc_requirements,
            "interactive": interactive_requirements,
            "test": test_requirements,
            "dev": doc_requirements
            + interactive_requirements
            + test_requirements
            + lint_requirements,
        },
        zip_safe=False,
        use_scm_version={
            "write_to": "src/vivarium_cluster_tools/_version.py",
            "write_to_template": '__version__ = "{version}"\n',
            "tag_regex": r"^(?P<prefix>v)?(?P<version>[^\+]+)(?P<suffix>.*)?$",
        },
        setup_requires=setup_requires,
    )
