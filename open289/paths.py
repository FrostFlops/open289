"""Where open289 keeps its files (state/ for locks, stop requests and simulator state; data/ for logs and downloads).

A source checkout (pyproject.toml next to the package) keeps them in the checkout. An installed package uses
$OPEN289_HOME, else %LOCALAPPDATA%\\open289 on Windows and ~/.local/share/open289 elsewhere, so nothing is written
into site-packages.
"""
import os

PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def home():
    env = os.environ.get('OPEN289_HOME')
    if env:
        return os.path.abspath(env)
    if os.path.exists(os.path.join(PKG_PARENT, 'pyproject.toml')):
        return PKG_PARENT
    base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return os.path.join(base, 'open289')


def root():
    return home()


def state_dir():
    return os.path.join(home(), 'state')


def data_dir():
    return os.path.join(home(), 'data')
