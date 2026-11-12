"""Private local configuration and owner-only atomic token storage."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat
import tempfile
from urllib.parse import urlsplit


class SafetyError(Exception):
    """Messages are fixed public reason codes, never broker/HTTP error text."""


REPOSITORY = Path(__file__).resolve().parents[1]


def private_path(path):
    path = Path(path).absolute()
    # State must not be in any Git working tree, including this repository.
    resolved = path.resolve()
    if resolved == REPOSITORY or REPOSITORY in resolved.parents:
        raise SafetyError('PRIVATE_STATE_OUTSIDE_REPOSITORY_REQUIRED')
    if any(parent.is_symlink() for parent in (path,*path.parents)):
        raise SafetyError('PRIVATE_PATH_SYMLINK')
    for parent in (resolved.parent, *resolved.parent.parents):
        marker=parent/'.git'
        if marker.is_file() or (marker/'HEAD').is_file():
            raise SafetyError('PRIVATE_STATE_OUTSIDE_GIT_REQUIRED')
    if path.is_symlink() or path.parent.is_symlink():
        raise SafetyError('PRIVATE_PATH_SYMLINK')
    return path


def private_read(path):
    path = private_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>65536 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise SafetyError('PRIVATE_FILE_MODE_REQUIRED')
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise SafetyError('PRIVATE_DIRECTORY_MODE_REQUIRED')
        with os.fdopen(fd, 'r') as stream:
            fd = None
            value = json.load(stream)
        if not isinstance(value,dict):
            raise SafetyError('INVALID_PRIVATE_FILE')
        return value
    finally:
        if fd is not None:
            os.close(fd)


def private_write(path, value):
    path = private_path(path)
    # Do not create or chmod external directories implicitly.
    info = path.parent.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise SafetyError('PRIVATE_DIRECTORY_MODE_REQUIRED')
    fd, temporary = tempfile.mkstemp(prefix='.oauth-', dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory = os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True, repr=False)
class Config:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    redirect_uri: str
    demo_account_id: int | None = field(repr=False)
    expected_broker: str
    token_path: Path = field(repr=False)
    application_approved: bool = False
    scope: str = 'accounts'
    expected_broker_name: str | None = None
    expected_demo_login: int | None = field(default=None,repr=False)
    discovery_only: bool = field(default=False,repr=False)
    expected_currency: str | None = None
    expected_account_mode: str | None = None

    def __post_init__(self):
        url = urlsplit(self.redirect_uri)
        if self.scope != 'accounts':
            raise SafetyError('VIEW_SCOPE_REQUIRED')
        if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
                or url.path != '/oauth/callback' or url.query or url.fragment or url.username or url.password):
            raise SafetyError('LOOPBACK_CALLBACK_REQUIRED')
        if type(self.discovery_only) is not bool:
            raise SafetyError('INVALID_DISCOVERY_MODE')
        if not self.discovery_only and (type(self.demo_account_id) is not int or self.demo_account_id <= 0):
            raise SafetyError('DEMO_ACCOUNT_ALLOWLIST_REQUIRED')
        if not all(isinstance(s,str) and s.strip() and len(s)<4096 for s in
                   (self.client_id,self.client_secret,self.expected_broker)):
            raise SafetyError('CONFIGURATION_REQUIRED')
        if type(self.application_approved) is not bool:
            raise SafetyError('INVALID_APPROVAL_FLAG')
        if self.expected_broker_name is not None and (not isinstance(self.expected_broker_name,str) or not self.expected_broker_name.strip() or len(self.expected_broker_name)>128):
            raise SafetyError('INVALID_BROKER_NAME')
        if self.expected_demo_login is not None and (type(self.expected_demo_login) is not int or self.expected_demo_login<=0):
            raise SafetyError('INVALID_DISPLAY_LOGIN')
        if self.expected_currency is not None and self.expected_currency not in ('USD','GBP','EUR','AUD','CAD','CHF','JPY','NZD'):
            raise SafetyError('INVALID_EXPECTED_CURRENCY')
        if self.expected_account_mode is not None and self.expected_account_mode not in ('HEDGED','NETTED','SPREAD_BETTING'):
            raise SafetyError('INVALID_EXPECTED_ACCOUNT_MODE')
        private_path(self.token_path)

    def require_approved(self):
        if not self.application_approved:
            raise SafetyError('APPLICATION_APPROVAL_REQUIRED')

    @classmethod
    def load(cls,path,*,discovery=False):
        data = private_read(path)
        allowed = {'client_id','client_secret','redirect_uri','demo_account_id',
                   'expected_broker','token_path','application_approved','scope','expected_broker_name','expected_demo_login',
                   'expected_currency','expected_account_mode'}
        if set(data)-allowed:
            raise SafetyError('UNKNOWN_CONFIGURATION_FIELD')
        try:
            data['token_path'] = Path(data['token_path'])
            if not data['token_path'].is_absolute():
                raise SafetyError('ABSOLUTE_TOKEN_PATH_REQUIRED')
            data.setdefault('demo_account_id',None)
            return cls(**data,discovery_only=discovery)
        except (KeyError, TypeError, ValueError):
            raise SafetyError('INVALID_CONFIGURATION') from None
