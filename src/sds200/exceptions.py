class SDS200Error(Exception):
    """Base exception for the package.

    The historical name is retained for compatibility now that the package
    supports multiple SDS-series scanner models.
    """


# Model-neutral public alias for new applications.
SDSScannerError = SDS200Error


class ScannerNotFoundError(SDS200Error):
    """No matching scanner device was found."""


class ScannerConnectionError(SDS200Error):
    """The scanner control connection could not be established or maintained."""


class CommandTimeoutError(SDS200Error):
    """A command did not receive a matching response before its timeout."""


class ProtocolError(SDS200Error):
    """A scanner response violated the expected protocol."""


class CommandRejectedError(ProtocolError):
    """The scanner explicitly rejected a pending command with ERR or NG."""


class ProfileError(SDS200Error):
    """A saved connection profile is missing or invalid."""


class UnsupportedScannerModelError(SDS200Error):
    """A connected scanner is not a supported SDS-series model."""


class UnsupportedScannerFeatureError(SDS200Error):
    """A supported scanner model does not implement the requested feature."""


class CaptureFormatError(ProtocolError):
    """A replay capture is missing, malformed, or uses an unsupported schema."""


class ReplayMismatchError(ProtocolError):
    """A replayed command did not match the next command in the capture."""
