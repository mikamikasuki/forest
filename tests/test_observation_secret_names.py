"""Secret-file exclusions must not depend on the case of the file name."""
import subprocess
import sys

import pytest

SECRET_NAMES = ('server.PEM', 'id_rsa.Key', 'bundle.P12', 'bundle.PFX',
                'Credentials.json', 'Secrets.json', 'SECRETS.JSON')


@pytest.mark.parametrize('name', SECRET_NAMES)
def test_observation_excludes_secret_names_in_any_case(name):
    from services.observation.files import allowed

    assert allowed(name) is False
    assert allowed('certs/' + name) is False


@pytest.mark.parametrize('name', ('server.pem', 'credentials.json', 'secrets.json'))
def test_observation_still_excludes_lowercase_secret_names(name):
    from services.observation.files import allowed

    assert allowed(name) is False


def test_observation_still_allows_ordinary_files():
    from services.observation.files import allowed

    assert allowed('notes.txt') is True
    assert allowed('src/Model.py') is True


def test_remote_listing_skips_secret_names_in_any_case(tmp_path):
    from runners.remote import RemoteRunner

    class LocalRunner(RemoteRunner):
        def command(self, argv, timeout=30):
            return subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True,
                                  check=True).stdout

    for name in SECRET_NAMES + ('notes.txt',):
        (tmp_path / name).write_text('x')
    runner = LocalRunner({'hostname': 'example.invalid', 'username': 'forest'})
    listing = runner.observe({'workdir': str(tmp_path)})
    assert [item['path'] for item in listing['files']] == ['notes.txt']
