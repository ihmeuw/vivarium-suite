import json
from pathlib import Path
import subprocess

import cookiecutter
from loguru import logger
import requests

from vivarium_cluster_tools.vadmin.utilities import HTTPError
from vivarium_cluster_tools.vadmin.oauth_utilities import OAuthConfig, OAuthError


def init(service: str, repo_name: str, output_root: str):
    repo_name = repo_name.replace(' ', '-').replace('_', '-')
    output_path = check_output_path(output_root, repo_name)

    oauth_token = authenticate(service)

    repo_url = create_repository(service, oauth_token, repo_name)
    try:
        repo_path = clone_repository(repo_url, output_path)
    except subprocess.CalledProcessError:
        delete_repository(service, oauth_token, repo_name)
        raise

    #
    # extra = {'project_name': repo_name}
    # extra.update(get_library_versions())
    #
    # output_root = output_root if output_root else '.'
    #
    # cookiecutter(TEMPLATE_URL,
    #              no_input=True,
    #              extra_context=extra,
    #              output_dir=output_root,)


def check_output_path(output_root: str, repo_name: str) -> Path:
    """Makes sure the output directory does not already exist.

    Parameters
    ----------
    output_root
        The root directory where the repository will be generated.
    repo_name
        The name of the new repository.

    Returns
    -------
        A ``Path`` object representing the new repository directory.

    """
    output_path = (Path(output_root) / repo_name).resolve()
    logger.debug(f'Checking if output path {str(output_path)} is valid.')
    if output_path.exists():
        raise FileExistsError(f'Repository directory {str(output_path)} already exists.')
    return output_path


def authenticate(service: str) -> str:
    """Verifies that the user has credentials to access the service.

    Parameters
    ----------
    service
        Either 'stash' or 'github'.  The service to retrieve credentials for.

    Returns
    -------
        The OAuth token for the requested service.

    Raises
    ------
    OAuthError
        If no local credentials for the service are found.

    """
    config = OAuthConfig()
    logger.debug(f'Checking if you have local credentials for {service}.')
    if not config.content[service]:
        raise OAuthError(f'No OAuth config for {service}.  You must run `vadmin oauth create {service}` before you '
                         f'can initialize research repositories.')
    return config.content[service]['token']


def create_repository(service: str, oauth_token: str, repo_name: str) -> str:
    """Creates a new remote repository.

    Parameters
    ----------
    service
        Either 'stash' or 'github'.  The service to generate the new repository
        on.
    oauth_token
        The user's credentials to access the service.
    repo_name
        The name of the new remote repository to create.

    Returns
    -------
        The ssh url that the repository can be cloned from.

    """
    if service == 'stash':
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {oauth_token}'}
        api_endpoint = 'https://stash.ihme.washington.edu/rest/api/1.0/projects/CSTE/repos'
        payload = {'name': repo_name,
                   'scmId': 'git'}
    else:  # service == 'github'
        headers = {'Authorization': f'token {oauth_token}'}
        api_endpoint = 'https://api.github.com/orgs/ihmeuw/repos'
        payload = {'name': repo_name}

    logger.debug(f'Creating the repository {repo_name} on {service}.')
    response = requests.post(api_endpoint, headers=headers, data=json.dumps(payload))
    content = parse_repo_creation_response(response)

    if service == 'stash':
        clone_ref = [ref['href'] for ref in response.json()['links']['clone'] if ref['name'] == 'ssh'].pop()
    else:  # service == github
        clone_ref = content['ssh_url']

    return clone_ref


def delete_repository(service: str, oauth_token: str, repo_name: str):
    """Deletes a remote repository.

    Parameters
    ----------
    service
        Either 'stash' or 'github'.  The service to delete the repository
        from.
    oauth_token
        The user's credentials to access the service.
    repo_name
        The name of the remote repository to delete.

    """
    if service == 'stash':
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {oauth_token}'}
        api_endpoint = f'https://stash.ihme.washington.edu/rest/api/1.0/projects/CSTE/repos/{repo_name}'
    else:  # service == github
        headers = {'Authorization': f'token {oauth_token}'}
        api_endpoint = f'https://api.github.com/repos/ihmeuw/{repo_name}'

    logger.debug(f'Deleting the repository {repo_name} on {service}.')
    response = requests.delete(api_endpoint, headers=headers)
    parse_repo_deletion_response(response)


def parse_repo_creation_response(response: requests.Response) -> dict:
    """Parses the response from repository creation.

    Parameters
    ----------
    response
        The server response from 'stash' or 'github' after requesting to
        create a new repository.

    Returns
    -------
        The content of the server response.

    Raises
    ------
    HTTPError
        If the repository creation was unsuccessful.

    """
    if response.status_code == 201:
        logger.info('Repository successfully created')
        return response.json()
    elif response.status_code == 409:
        raise HTTPError(f'Repository already exists.')
    else:
        raise HTTPError(f'Unknown response {response.status_code} when creating repo.\n'
                        f'Response details: {response.text}')


def parse_repo_deletion_response(response: requests.Response):
    """Parses the response from repository deletion.

    Parameters
    ----------
    response
        The server response from 'stash' or 'github' after requesting to
        delete an existing repository.

    Raises
    ------
    HTTPError
        If the repository deletion was unsuccessful.

    """
    if response.status_code in [202, 204]:
        logger.info('Repository successfully deleted.')
    else:
        raise HTTPError(f'Unknown response {response.status_code} when deleting repo.\n'
                        f'Response details: {response.text}')


def clone_repository(repository_url: str, output_dir: Path) -> Path:
    """Clones a repository using ``git``.

    Parameters
    ----------
    repository_url
        The clone url for the the repository.
    output_dir
        The local directory to clone the repository into.

    Returns
    -------
        The path to the new repository.

    """
    subprocess.run(['git', 'clone', repository_url, str(output_dir)], check=True)
    return output_dir


def get_library_versions():
    libraries = ['vivarium', 'vivarium_public_health', 'vivarium_cluster_tools']
    versions = {}

    for l in libraries:
        r = requests.get(f'https://pypi.org/pypi/{l}/json')
        versions[f'{l}_version'] = r.json()['info']['version']

    return versions
