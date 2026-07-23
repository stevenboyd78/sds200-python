class SDS200Error(Exception):
    """Base exception for the package."""


class ScannerNotFoundError(SDS200Error):
    """No matching scanner device was found."""


class ScannerConnectionError(SDS200Error):
    """The scanner control connection could not be established or maintained."""


class CommandTimeoutError(SDS200Error):
    """A command did not receive a matching response before its timeout."""


class ProtocolError(SDS200Error):
    """A scanner response violated the expected protocol."""
