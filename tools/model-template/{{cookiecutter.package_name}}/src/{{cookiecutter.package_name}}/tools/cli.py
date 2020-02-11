"""
Make model specifications

click application that takes a template model specification file
and locations for which to create model specs and uses jinja2 to
render model specs with the correct location parameters plugged in.

It will look for the model spec template in "model_spec.in" in the directory
``src/{{cookiecutter.package_name}}/model_specifications``.
Add location strings to the ``src/globals.py`` file. By default, specifications
for all locations will be built. You can choose to make a model specification
for a single location by specifying that location. However, the location
string must exist in the list in ``src/globals.py``.

The application will look for the model spec based on the python environment
that is active and these files don't need to be specified if the
default names and location are used.
"""
import click
from loguru import logger
from vivarium.framework.utilities import handle_exceptions

from {{cookiecutter.package_name}} import paths
import {{cookiecutter.package_name}}.globals as project_globals

from {{cookiecutter.package_name}}.tools import configure_logging_to_terminal
from {{cookiecutter.package_name}}.tools import build_model_specifications
from {{cookiecutter.package_name}}.tools import build_artifacts


@click.command()
@click.option('-t', '--template',
              default=str(paths.MODEL_SPEC_DIR / 'model_spec.in'),
              show_default=True,
              type=click.Path(exists=True, dir_okay=False),
              help='The model specification template file.')
@click.option('-l', '--location',
              default='all',
              show_default=True,
              type=click.Choice(project_globals.LOCATIONS + ['all']),
              help='Location to make specification for. Specify locations in globals.py')
@click.option('-o', '--output-dir',
              default=str(paths.MODEL_SPEC_DIR),
              show_default=True,
              type=click.Path(exists=True),
              help='Specify an output directory. Directory must exist.')
@click.option('-v', 'verbose',
              count=True,
              help='Configure logging verbosity.')
@click.option('--pdb', 'with_debugger',
              is_flag=True,
              help='Drop into python debugger if an error occurs.')
def make_specs(template: str, location: str, output_dir: str, verbose: int, with_debugger: bool) -> None:
    """Generate model specifications based on a template.

    The default template lives here:

    ``src/{{cookiecutter.package_name}}/model_specification/model_spec.in``
    """
    configure_logging_to_terminal(verbose)
    main = handle_exceptions(build_model_specifications, logger, with_debugger=with_debugger)
    main(template, location, output_dir)


@click.command()
@click.option('-l', '--location',
              default='all',
              show_default=True,
              type=click.Choice(project_globals.LOCATIONS + ['all']),
              help='Location to make artifact for.')
@click.option('-o', '--output-dir',
              default=str(paths.ARTIFACT_ROOT),
              show_default=True,
              type=click.Path(exists=True),
              help='Specify an output directory. Directory must exist.')
@click.option('-a', '--append',
              is_flag=True,
              help='Append to the artifact instead of overwriting.')
@click.option('-v', 'verbose',
              count=True,
              help='Configure logging verbosity.')
@click.option('--pdb', 'with_debugger',
              is_flag=True,
              help='Drop into python debugger if an error occurs.')
def make_artifacts(location: str, output_dir: str, append: bool, verbose: int, with_debugger: bool) -> None:
    configure_logging_to_terminal(verbose)
    main = handle_exceptions(build_artifacts, logger, with_debugger=with_debugger)
    main(location, output_dir, append, verbose)
