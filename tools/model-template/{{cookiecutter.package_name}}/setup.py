#!/usr/bin/env python
import os

from setuptools import find_packages, setup

if __name__ == "__main__":

    base_dir = os.path.dirname(__file__)
    src_dir = os.path.join(base_dir, "src")

    about = {}
    with open(os.path.join(src_dir, "{{cookiecutter.package_name}}", "__about__.py")) as f:
        exec(f.read(), about)

    with open(os.path.join(base_dir, "README.rst")) as f:
        long_description = f.read()

    install_requirements = [
        "gbd_mapping=={{cookiecutter.gbd_mapping_version}}",
        "vivarium=={{cookiecutter.vivarium_version}}",
        "vivarium_public_health=={{cookiecutter.vivarium_public_health_version}}",
        "click",
        "jinja2",
        "loguru",
        "numpy",
        "pandas",
        "pyyaml",
        "scipy",
        "tables",
    ]

    setup_requires = ["setuptools_scm"]

    data_requirements = ["vivarium_inputs[data]=={{cookiecutter.vivarium_inputs_version}}"]
    cluster_requirements = ["vivarium_cluster_tools=={{cookiecutter.vivarium_cluster_tools_version}}"]
    test_requirements = ["pytest"]
    lint_requirements = ["black", "isort"]

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
            "dev": test_requirements + cluster_requirements + lint_requirements,
        },
        zip_safe=False,
        use_scm_version={
            "write_to": "src/{{cookiecutter.package_name}}/_version.py",
            "write_to_template": '__version__ = "{version}"\n',
            "tag_regex": r"^(?P<prefix>v)?(?P<version>[^\+]+)(?P<suffix>.*)?$",
        },
        setup_requires=setup_requires,
        entry_points="""
            [console_scripts]
            make_artifacts={{cookiecutter.package_name}}.tools.cli:make_artifacts
            make_results={{cookiecutter.package_name}}.tools.cli:make_results
        """,
    )
