"""Error type with a stable machine-readable code (used for CLI JSON, exit codes and MCP results)."""

EXIT_CODES = {
    'INVALID_ARG': 2,
    'NOT_FOUND': 3,          # no IR3000 FC advertising (asleep, out of range, or connected to a phone)
    'BUSY': 4,               # another open289 process holds the adapter
    'METER_BUSY': 5,         # the meter answers EXECUTION_ERROR: a menu / result page / setup screen is open
    'DETACHED': 5,           # the adapter is not attached to a powered meter
    'NO_DATA': 5,            # the meter has no data for the request (ack 5)
    'TIMEOUT': 6,
    'BLE_ERROR': 6,
    'LINK_LOST': 6,
    'PROTOCOL_ERROR': 6,
    'METER_ERROR': 6,        # syntax error or another unexpected ack from the meter
    'WRITE_FAILED': 6,
    'UNSAFE_OPERATION': 7,   # refused: a command that could change or erase meter state without the opt-in flag
    'STOPPED': 0,
}


class Open289Error(Exception):
    def __init__(self, code, message, hint=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    @property
    def exit_code(self):
        return EXIT_CODES.get(self.code, 1)

    def to_dict(self):
        d = {'code': self.code, 'message': self.message}
        if self.hint:
            d['hint'] = self.hint
        return d
