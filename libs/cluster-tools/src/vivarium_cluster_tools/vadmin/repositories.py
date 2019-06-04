from collections import namedtuple
from getpass import getpass
import json
from pathlib import Path

import requests


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
        self.path.touch(mode=0o600)

    def _load_content(self) -> dict:
        with self.path.open('r') as f:
            try:
                content = json.load(f)
            except json.decoder.JSONDecodeError:  # The json file exists, but is empty
                content = {}
        return content

    def update_from_http_response(self, response: requests.Response, service: str):
        if response.status_code == 200:
            print(f'{service} token successfully created.\n'
                  f'Token details: {response.json()}\n')

            self._content.update({service: response.json()})
            with self.path.open('w') as f:
                json.dump(self.content, f)
        else:
            raise RuntimeError(f'Unknown response {response.status_code} while creating token.\n'
                               f'Response details: {response.json()}')


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
    if service in config.content:
        raise RuntimeError(f'Local token for {service} already present.')

    creators = {'stash': oauth_create_stash, 'github': oauth_create_github}
    creators[service](config)


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
    config.update_from_http_response(response)


def _check_stash_token(response):
    if response.status_code == 200:
        tokens = response.json()['values']
        vadmin_token = [t for t in tokens if t['name'] == 'vadmin']
        if vadmin_token:
            raise RuntimeError(f'vadmin token already exists.\n'
                               f'Token properties: {vadmin_token[0]}')
    else:
        raise RuntimeError(f'Unknown response {response.status_code} while querying token.\n'
                           f'Response details: {response.json()}')


def oauth_create_github(config):
    user = get_user('github')
    api_endpoint = 'https://api.github.com/authorizations'

    payload = {
        'note': 'vadmin init',
        'scopes': ['user', 'repo']
    }
    response = requests.post(api_endpoint, auth=user, data=json.dumps(payload))
    config.update_from_http_response(response)
