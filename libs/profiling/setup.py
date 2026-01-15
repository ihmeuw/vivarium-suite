#!/usr/bin/env python
import json
import os
import sys

from packaging.version import parse
from setuptools import find_packages, setup

f = open("python_versions.json")
supported_python_versions = json.load(f)
f.close()

python_versions = [parse(v) for v in supported_python_versions]
min_version = min(python_versions)
max_version = max(python_versions)
if not (
    min_version <= parse(".".join([str(v) for v in sys.version_info[:2]])) <= max_version
):
    py_version = ".".join([str(v) for v in sys.version_info[:3]])
    # Python 3.5 does not support f-strings
    error = (
        "\n----------------------------------------\n"
        "Error: This repo requires python {min_version}-{max_version}.\n"
        "You are running python {py_version}".format(
            min_version=min_version.base_version,
            max_version=max_version.base_version,
            py_version=py_version,
        )
    )
    print(error, file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":

    base_dir = os.path.dirname(__file__)
    src_dir = os.path.join(base_dir, "src")

    about = {}
    with open(os.path.join(src_dir, "vivarium_profiling", "__about__.py")) as f:
        exec(f.read(), about)

    with open(os.path.join(base_dir, "README.rst")) as f:
        long_description = f.read()

    install_requirements = [
        "vivarium_dependencies[pandas,numpy,scipy,click,tables,loguru]",
        "vivarium_build_utils>=2.0.4,<3.0.0",
        "gbd_mapping>=4.1.6",
        "vivarium>=3.4.12",
        "vivarium_public_health>=4.3.5",
        "jinja2",
        "pyyaml",
        "memory_profiler",
        "matplotlib",
        "seaborn",
        "scalene",
    ]

    setup_requires = ["setuptools_scm"]

    data_requirements = ["vivarium_inputs>=5.2.6"]
    interactive_requirements = ["vivarium_dependencies[interactive]"]
    cluster_requirements = ["vivarium_cluster_tools>=2.1.17"]
    test_requirements = ["vivarium_dependencies[pytest]"]
    lint_requirements = ["vivarium_dependencies[lint]"]

    setup(
        name=about["__title__"],
        description=about["__summary__"],
        long_description=long_description,
        license=about["__license__"],
        url=about["__uri__"],
        author=about["__author__"],
        author_email=about["__email__"],
        package_dir={"": "src"},
        packages=find_packages(where="src"),
        include_package_data=True,
        install_requires=install_requirements,
        extras_require={
            "test": test_requirements,
            "cluster": cluster_requirements,
            "data": data_requirements + cluster_requirements,
            "interactive": interactive_requirements,
            "dev": test_requirements
            + cluster_requirements
            + lint_requirements
            + interactive_requirements,
        },
        zip_safe=False,
        use_scm_version={
            "write_to": "src/vivarium_profiling/_version.py",
            "write_to_template": '__version__ = "{version}"\n',
            "tag_regex": r"^(?P<prefix>v)?(?P<version>[^\+]+)(?P<suffix>.*)?$",
        },
        setup_requires=setup_requires,
        entry_points="""
            [console_scripts]
            make_artifacts=vivarium_profiling.tools.cli:make_artifacts
            run_benchmark=vivarium_profiling.tools.cli:run_benchmark
            profile_sim=vivarium_profiling.tools.cli:profile_sim
            summarize=vivarium_profiling.tools.cli:summarize
        """,
    )
