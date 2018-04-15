from setuptools import setup, find_packages


setup(
    name='vivarium_cluster_tools',
    version='0.1',
    packages=find_packages(where='src'),
    package_dir={'': 'src'},
    include_package_data=True,
    entry_points="""
        [console_scripts]
        psimulate=vivarium_cluster_tools.cli:psimulate
    """,
    install_requires=[
        'pandas',
        'numpy',
        'pyyaml',
        'drmaa',
        'redis',
        'rq',
        'vivarium',
        'click',
    ],
)
