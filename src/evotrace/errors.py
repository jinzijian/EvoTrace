class EvoTraceError(Exception):
    """A user-facing EvoTrace error."""


# Backward-compatible name for integrations built before the EvoTrace rename.
ScaleVerifierError = EvoTraceError
