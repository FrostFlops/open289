"""Single-client lock: the IR3000 FC accepts one BLE connection, so only one open289 process may talk to it."""
import os

from .errors import Open289Error
from .paths import state_dir


def lock_path():
    return os.path.join(state_dir(), 'open289.lock')


def _lock(fh, on):
    """Non-blocking lock / unlock of byte 0 (msvcrt on Windows, flock elsewhere); OSError when it is held."""
    if os.name == 'nt':
        import msvcrt
        fh.seek(0)                   # 'a+' starts at the end of the file: always lock byte 0
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK if on else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), (fcntl.LOCK_EX | fcntl.LOCK_NB) if on else fcntl.LOCK_UN)


class AdapterLock:
    def __init__(self):
        self.fh = None

    def acquire(self):
        path = lock_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.fh = open(path, 'a+')
        try:
            _lock(self.fh, True)
        except OSError:
            self.fh.close()
            self.fh = None
            raise Open289Error('BUSY', 'another open289 process is connected to the adapter',
                               'wait for it, or end a running live/log with "open289 stop", then retry')
        try:
            self.fh.seek(0)
            self.fh.truncate()
            self.fh.write(str(os.getpid()))
            self.fh.flush()
        except OSError:
            pass
        return self

    def release(self):
        if self.fh:
            try:
                _lock(self.fh, False)
            except OSError:
                pass
            self.fh.close()
            self.fh = None
