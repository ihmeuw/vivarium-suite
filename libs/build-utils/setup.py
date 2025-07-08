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

    setup(
        name=about["__title__"],
        description=about["__summary__"],
        long_description=long_description,
        long_description_content_type="text/x-rst",
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
        packages=[],  # No Python packages, just for versioning
        include_package_data=True,
        zip_safe=False,
        use_scm_version={
            "write_to": "_version.py",
            "write_to_template": '__version__ = "{version}"\n',
            "tag_regex": r"^(?P<prefix>v)?(?P<version>[^\+]+)(?P<suffix>.*)?$",
        },
        setup_requires=setup_requires,
    )