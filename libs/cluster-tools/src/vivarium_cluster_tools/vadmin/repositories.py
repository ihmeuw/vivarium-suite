from collections import namedtuple
from getpass import getpass
import json
from pathlib import Path

import requests
from loguru import logger


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
    RuntimeError
        If a token for the service exists locally or remotely.

    """

    config = OAuthConfig()
    if config.content[service]:
        raise RuntimeError(f'Local token for {service} already present.')

    creators = {'stash': oauth_create_stash, 'github': oauth_create_github}
    creators[service](config)


def oauth_remove(service: str):
    config = OAuthConfig()
    if not config.content[service]:
        raise RuntimeError(f'No local token for {service} present. You can '
                           f'delete tokens from the web page if necessary.')

    removers = {'stash': oauth_remove_stash, 'github': oauth_remove_github}
    removers[service](config)


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
        return self._path

    @property
    def content(self):
        return self._content

    def _make_file(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        if not self.path.exists():
            self.path.touch(mode=0o600)
            with self.path.open('w') as f:
                json.dump({'stash': {}, 'github': {}}, f)

    def _load_content(self) -> dict:
        with self.path.open('r') as f:
            content = json.load(f)
        return content

    def update(self, service: str, content: dict):
        logger.info(f'Updating config for {service} with content {content}.')
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
        A ``User`` tuple with a name and password.

    """
    username = input(f'Please enter your {service} user name: ')
    password = getpass(f'Please enter your {service} password: ')
    return User(username, password)


def parse_token_creation_response(response: requests.Response) -> dict:
    if response.status_code in [200, 201]:
        logger.info(f'Token successfully created.')
    else:
        raise RuntimeError(f'Unknown response {response.status_code} while creating token.\n'
                           f'Response details: {response.text}')
    return response.json()


def parse_token_deletion_response(response: requests.Response) -> dict:
    if response.status_code == 204:
        logger.info(f'Token successfully deleted.')
    else:
        raise RuntimeError(f'Unknown response {response.status_code} while deleting token.\n'
                           f'Response details: {response.text}')
    return {}


############################
# Stash-specific utilities #
############################

def oauth_create_stash(config: OAuthConfig):
    user = get_user('stash')
    api_endpoint = f'https://stash.ihme.washington.edu/rest/access-tokens/1.0/users/{user.name}/'
    headers = {'Content-Type': 'application/json'}

    # First check to see if a token already exists
    response = requests.get(api_endpoint, headers=headers, auth=user)
    _check_stash_token(response)

    # Generate a new access token
    payload = {'name': 'vadmin',
               'permissions': ['PROJECT_ADMIN']}
    response = requests.put(api_endpoint, headers=headers, auth=user, data=json.dumps(payload))
    token_config = parse_token_creation_response(response)
    config.update('stash', token_config)


def _check_stash_token(response: requests.Response):
    if response.status_code == 200:
        tokens = response.json()['values']
        vadmin_token = [t for t in tokens if t['name'] == 'vadmin']
        if vadmin_token:
            raise RuntimeError(f'vadmin token already exists.\n'
                               f'Token properties: {vadmin_token[0]}')
    else:
        raise RuntimeError(f'Unknown response {response.status_code} while querying token.\n'
                           f'Response details: {response.text}')


def oauth_remove_stash(config: OAuthConfig):
    user = get_user('stash')
    token_id = config.content['stash']['id']
    api_endpoint = f'https://stash.ihme.washington.edu/rest/access-tokens/1.0/users/{user.name}/{token_id}'
    headers = {'Content-Type': 'application/json'}

    response = requests.delete(api_endpoint, auth=user, headers=headers)
    token_config = parse_token_deletion_response(response)
    config.update('stash', token_config)


#############################
# Github-specific utilities #
#############################

def oauth_create_github(config: OAuthConfig):
    user = get_user('github')
    api_endpoint = 'https://api.github.com/authorizations'

    payload = {
        'note': 'vadmin init',
        'scopes': ['user', 'repo']
    }
    response = requests.post(api_endpoint, auth=user, data=json.dumps(payload))
    token_config = parse_token_creation_response(response)
    config.update('github', token_config)


def oauth_remove_github(config: OAuthConfig):
    user = get_user('github')
    token_id = config.content['github']['id']
    api_endpoint = f'https://api.github.com/authorizations/{token_id}'

    response = requests.delete(api_endpoint, auth=user)
    token_config = parse_token_deletion_response(response)
    config.update('stash', token_config)
