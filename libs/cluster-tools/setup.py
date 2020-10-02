import os
from setuptools import setup, find_packages

if __name__ == "__main__":

    base_dir = os.path.dirname(__file__)
    src_dir = os.path.join(base_dir, "src")

    about = {}
    with open(os.path.join(src_dir, "vivarium_cluster_tools", "__about__.py")) as f:
        exec(f.read(), about)

    with open(os.path.join(base_dir, "README.rst")) as f:
        long_description = f.read()

    install_requires = [
        'pandas<0.25',
        'numpy<=1.15.4',  # to match VPH
        'tables<=3.4.0',  # to match VPH
        'loguru',
        'pyyaml>=5.1',
        'drmaa',
        'redis<=3.0.1',
        'rq>=1.0, <=1.2.2',
        'vivarium>=0.9.3',
        'click',
    ]

    test_requirements = [
        'pytest',
        'pytest-mock',
    ]

    setup(
        name=about['__title__'],
        version=about['__version__'],

        description=about['__summary__'],
        long_description=long_description,
        url=about["__uri__"],

        author=about["__author__"],
        author_email=about["__email__"],

        package_dir={'': 'src'},
        packages=find_packages(where='src'),
        include_package_data=True,
        entry_points="""
            [console_scripts]
            psimulate=vivarium_cluster_tools.psimulate.cli:psimulate
            vparse=vivarium_cluster_tools.vparse.cli:vparse
        """,

        install_requires=install_requires,
        extras_require={
            'test': test_requirements,
            'dev': test_requirements,
        },

        zip_safe=False,
    )
