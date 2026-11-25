"""Errors that are an expected answer to a request, not a bug."""


class AnalysisError(Exception):
    """The request cannot be answered (unknown ticker, period not loaded, missing line item,
    no price, invalid parameter). The message is shown to the user as-is."""
