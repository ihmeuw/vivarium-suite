from pathlib import Path

from setuptools import setup

if __name__ == "__main__":
    base_dir = Path(__file__).parent

    about = {}
    with (base_dir / "__about__.py").open() as f:
        exec(f.read(), about)

    with (base_dir / "README.rst").open() as f:
        long_description = f.read()

    setup_requires = ["setuptools_scm"]

    # Define commonly-used pins here to be used in other repositories
    # e.g. vivarium_dependencies[numpy,pandas]>=1.2.0,<2.0.0
    extras_require = {
        "numpy": ["numpy"],
        "numpy_lt_2": ["numpy<2.0.0"],
        "pandas": [
            "pandas",
            "pandas-stubs<=2.2.3.250308",
        ],
        "pyyaml": [
            "pyyaml>=5.1",
            "types-PyYAML",
        ],
        "scipy": ["scipy"],
        "click": ["click"],
        "tables": ["tables"],
        "loguru": ["loguru"],
        "pyarrow": ["pyarrow"],
        "networkx": [
            "networkx",
            "networkx-stubs",
        ],
        "requests": [
            "requests",
            "types-requests",
        ],
        "docutils": [
            "docutils",
            "types-docutils",
        ],
        "ipython": ["ipython"],
        "jupyter": [
            "vivarium_dependencies[ipython]",
            "jupyter",
            "ipywidgets",
        ],
        "matplotlib": ["matplotlib"],
        # testing
        "pytest": ["pytest", "pytest-cov", "pytest-mock"],
        # formatting and linting
        "black": ["black==22.3.0"],
        "isort": ["isort==5.13.2"],
        "mypy": ["mypy"],
        # docs
        "sphinx": [
            "sphinx",
            "sphinx-autodoc-typehints",
            "sphinx-rtd-theme",
        ],
        "sphinx-click": ["sphinx-click"],
        # gbd
        "db_queries": ["db_queries>=31.0.4,< 32.0.0"],
        "db_tools": ["db_tools>=1.0.2,<2.0.0"],
        "get_draws": ["get_draws>=5.1.4,<6.0.0"],
        # convenience sets
        "lint": [
            "vivarium_dependencies[black]",
            "vivarium_dependencies[isort]",
            "vivarium_dependencies[mypy]",
        ],
        "interactive": [
            "vivarium_dependencies[jupyter,scipy,matplotlib]",
            "seaborn",
        ],
        "gbd": [
            "vivarium_dependencies[db_queries]",
            "vivarium_dependencies[db_tools]",
            "vivarium_dependencies[get_draws]",
        ],
    }

    setup(
        name=about["__title__"],
        description=about["__summary__"],
        long_description=long_description,
        license=about["__license__"],
        url=about["__uri__"],
        author=about["__author__"],
        author_email=about["__email__"],
        classifiers=[
            "Intended Audience :: Developers",
            "Intended Audience :: Education",
            "Intended Audience :: Science/Research",
            "License :: OSI Approved :: BSD License",
            "Natural Language :: English",
            "Operating System :: MacOS :: MacOS X",
            "Operating System :: POSIX",
            "Operating System :: POSIX :: BSD",
            "Operating System :: POSIX :: Linux",
            "Operating System :: Microsoft :: Windows",
            "Programming Language :: Python",
            "Programming Language :: Python :: Implementation :: CPython",
            "Topic :: Software Development :: Libraries",
            "Topic :: Software Development :: Build Tools",
        ],
        extras_require=extras_require,
        zip_safe=False,
        use_scm_version={
            "write_to": "_version.py",
            "write_to_template": '__version__ = "{version}"\n',
            "tag_regex": r"^(?P<prefix>v)?(?P<version>[^\+]+)(?P<suffix>.*)?$",
        },
        setup_requires=setup_requires,
    )
