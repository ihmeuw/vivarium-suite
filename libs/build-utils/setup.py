import shutil
from pathlib import Path

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py


class CustomBuildPy(build_py):
    """Custom build command that copies resources into the package."""

    def run(self):
        # Run the standard build
        super().run()

        # Copy resources from repo root to package
        repo_root = Path(__file__).parent
        resources_src = repo_root / "resources"

        if resources_src.exists():
            # Find the built package directory
            build_lib = Path(self.build_lib)
            resources_dest = build_lib / "vivarium_build_utils" / "resources"

            if resources_dest.exists():
                shutil.rmtree(resources_dest)

            shutil.copytree(resources_src, resources_dest)
            print(f"Copied resources from {resources_src} to {resources_dest}")


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
        package_dir={"": "src"},
        packages=find_packages(where="src"),
        package_data={
            "vivarium_build_utils": [
                "resources/**/*",
            ]
        },
        include_package_data=True,
        cmdclass={
            "build_py": CustomBuildPy,
        },
        zip_safe=False,
        use_scm_version={
            "write_to": "src/vivarium_build_utils/_version.py",
            "write_to_template": '__version__ = "{version}"\n',
            "tag_regex": r"^(?P<prefix>v)?(?P<version>[^\+]+)(?P<suffix>.*)?$",
        },
        setup_requires=setup_requires,
    )
