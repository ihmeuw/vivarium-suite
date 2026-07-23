"""{{ cookiecutter.package_name }}

{{ cookiecutter.package_description }}

"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("{{cookiecutter.package_name}}")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"
