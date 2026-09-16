"""Tier 1 of the Platform Contract."""


class CheckerError(Exception):
    """The checker could not run - which is not the same as a Project failing.

    Exit status 2, never 1. A missing `docker compose`, or a schema keyword
    the validator does not implement, says nothing about the Project being
    checked; reporting it as a violation would have somebody editing a
    Compose file to fix a laptop.
    """
