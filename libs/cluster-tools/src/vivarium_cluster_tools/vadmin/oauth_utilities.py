from collections import namedtuple
from getpass import getpass
import json
from pathlib import Path
from pprint import pformat

import requests
from loguru import logger

from vivarium_cluster_tools.vadmin.utilities import VAdminError, HTTPError


class OAuthError(VAdminError, RuntimeError):
    """Error when dealing with OAuth services."""
    pass


#####################
# Main Entry Points #
#####################

def oauth_create(service: str):
    """Generates a new OAuth token for the service.

    Parameters
    ----------
    service
        One of 'stash' or 'github'.

    Raises
    ------
    OAuthError
        If a token for the service exists locally or remotely.
    HTTPError
        If there is a communication issue between ``vadmin`` and the
        provided service.

    """
    logger.info(f'Creating a new OAuth token for {service}.')

    config = OAuthConfig()
    logger.debug('Checking if local OAuth configuration is present.')
    if config.content[service]:
        raise OAuthError(f'Local token for {service} already present.')

    creators = {'stash': oauth_create_stash, 'github': oauth_create_github}
    creators[service](config)


def oauth_remove(service: str):
    """Removes an existing OAuth token for the service.

    Parameters
    ----------
    service
        One of 'stash' or 'github'.

    Raises
    ------
    OAuthError
        If a token for the service does not exist locally or remotely.
    HTTPError
        If there is a communication issue between ``vadmin`` and the
        provided service.

    """
    logger.info(f'Removing OAuth token for {service}.')
    config = OAuthConfig()
    logger.debug('Checking if local OAuth configuration is present.')
    if not config.content[service]:
        raise OAuthError(f'No local token for {service} present. You can '
                         f'delete tokens from the web page if necessary.')

    removers = {'stash': oauth_remove_stash, 'github': oauth_remove_github}
    removers[service](config)


def oauth_display():
    """Displays local OAuth information."""
    config = OAuthConfig()
    logger.info(f'Local oauth configuration:\n{pformat(config.content)}')

    # TODO: Implement display of remote OAuth tokens.


####################
# Shared Utilities #
####################

class OAuthConfig:
    """Wrapper around a user OAuth configuration."""

    def __init__(self):
        self._path = Path().home() / '.config' / 'vadmin_tokens.json'
        self._make_file()
        self._content = self._load_content()

    @property
    def path(self):
        """Path to local OAuth configuration file."""
        return self._path

    @property
    def content(self):
        """Content of local OAuth configuration file."""
        return self._content

    def _make_file(self):
        """Generates a local OAuth configuration file if it doesn't exist.

        This method is idempotent.
        """
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        if not self.path.exists():
            logger.info(f'No local OAuth configuration found. Creating configuration file at {str(self.path)}.')
            self.path.touch(mode=0o600)
            with self.path.open('w') as f:
                json.dump({'stash': {}, 'github': {}}, f)

    def _load_content(self) -> dict:
        """Loads OAuth configuration data from a local config file."""
        logger.debug(f'Loading OAuth configuration information from {str(self.path)}.')
        with self.path.open('r') as f:
            content = json.load(f)
        return content

    def update(self, service: str, content: dict):
        """Updates the local configuration file with new OAuth content.

        Parameters
        ----------
        service
            Either 'stash' or 'github'.  The service for which the
            configuration is being updated.
        content
            The new OAuth configuration information.

        """
        logger.debug(f'Updating local OAuth configuration at {str(self.path)} for {service} with content {content}.')
        self._content.update({service: content})
        with self.path.open('w') as f:
            json.dump(self.content, f)


User = namedtuple('User', ['name', 'password'])


def get_user(service: str) -> User:
    """Retrieves and wraps a user name and password for a service.

    Parameters
    ----------
    service
        One of 'stash' or 'github'.

    Returns
    -------
        A :ref:`User` with a ``name`` and ``password`` attributes.

    """
    username = input(f'Please enter your {service} user name: ')
    password = getpass(f'Please enter your {service} password: ')
    return User(username, password)


def parse_token_creation_response(response: requests.Response) -> dict:
    """Parses the response from OAuth token creation.

    Parameters
    ----------
    response
        The server response from 'stash' or 'github' after requesting a new
        OAuth token.

    Returns
    -------
        The content of the server response.

    Raises
    ------
    HTTPError
        If the token creation was unsuccessful.

    """
    if response.status_code in [200, 201]:
        logger.info(f'Token successfully created.')
    else:
        raise HTTPError(f'Unknown response {response.status_code} while creating token.\n'
                        f'Response details: {response.text}')
    return response.json()


def parse_token_deletion_response(response: requests.Response) -> dict:
    """Parses the response from OAuth token deletion.

    Parameters
    ----------
    response
        The server response from 'stash' or 'github' after requesting the
        deltion of an OAuth token.

    Returns
    -------
        The content of the server response.

    Raises
    ------
    HTTPError
        If the token deletion was unsuccessful.

    """
    if response.status_code == 204:
        logger.info(f'Token successfully deleted.')
    else:
        raise HTTPError(f'Unknown response {response.status_code} while deleting token.\n'
                        f'Response details: {response.text}')
    return {}


############################
# Stash-specific utilities #
############################

def oauth_create_stash(config: OAuthConfig):
    """Creates a new OAuth token on stash."""
    user = get_user('stash')
    api_endpoint = f'https://stash.ihme.washington.edu/rest/access-tokens/1.0/users/{user.name}/'
    headers = {'Content-Type': 'application/json'}

    # First check to see if a token already exists
    logger.debug('Checking to see if OAuth token already exists on stash.')
    response = requests.get(api_endpoint, headers=headers, auth=user)
    _check_stash_token(response)

    # Generate a new access token
    logger.debug('Generating new OAuth token on stash.')
    payload = {'name': 'vadmin',
               'permissions': ['PROJECT_ADMIN']}
    response = requests.put(api_endpoint, headers=headers, auth=user, data=json.dumps(payload))
    token_config = parse_token_creation_response(response)
    config.update('stash', token_config)


def _check_stash_token(response: requests.Response):
    """Checks if an OAuth token for ``vadmin`` already exists on stash.

    Raises
    ------
    OAuthError
        If the token already exists.
    HttpError
        If the server request is unsuccessful.

    """
    if response.status_code == 200:
        tokens = response.json()['values']
        vadmin_token = [t for t in tokens if t['name'] == 'vadmin']
        if vadmin_token:
            raise OAuthError(f'vadmin token already exists.\n'
                             f'Token properties: {vadmin_token[0]}')
    else:
        raise HTTPError(f'Unknown response {response.status_code} while querying token.\n'
                        f'Response details: {response.text}')


def oauth_remove_stash(config: OAuthConfig):
    """Removes an OAuth token from stash."""
    user = get_user('stash')
    token_id = config.content['stash']['id']
    api_endpoint = f'https://stash.ihme.washington.edu/rest/access-tokens/1.0/users/{user.name}/{token_id}'
    headers = {'Content-Type': 'application/json'}

    logger.debug('Removing OAuth token from stash.')
    response = requests.delete(api_endpoint, auth=user, headers=headers)
    token_config = parse_token_deletion_response(response)
    config.update('stash', token_config)


#############################
# Github-specific utilities #
#############################

def oauth_create_github(config: OAuthConfig):
    """Creates a new OAuth token on github."""
    user = get_user('github')
    api_endpoint = 'https://api.github.com/authorizations'

    payload = {
        'note': 'vadmin init',
        'scopes': ['user', 'repo', 'delete_repo']
    }
    logger.debug('Generating new OAuth token on github.')
    response = requests.post(api_endpoint, auth=user, data=json.dumps(payload))
    token_config = parse_token_creation_response(response)
    config.update('github', token_config)


def oauth_remove_github(config: OAuthConfig):
    """Creates a new OAuth token on github."""
    user = get_user('github')
    token_id = config.content['github']['id']
    api_endpoint = f'https://api.github.com/authorizations/{token_id}'

    response = requests.delete(api_endpoint, auth=user)
    token_config = parse_token_deletion_response(response)
    config.update('github', token_config)
