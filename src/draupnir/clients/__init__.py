"""Clients for external services (SEC, Yahoo, ...).

The only package that performs network I/O. One module per service; each exposes
plain functions/classes returning Python or pandas objects, never raw HTTP responses.
"""
