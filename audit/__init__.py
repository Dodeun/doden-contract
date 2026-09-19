"""Tier 2 of the Platform Contract: the repository's own settings.

Tier 1 travels in a copy of a repository and is checked by the Project
itself, asking for no secret. Tier 2 cannot travel in a copy - a ruleset is
not a file - and reading it needs a credential. Giving every Project one
would destroy the property that makes tier 1 safe to publish, so tier 2 is
audited centrally instead: one token, in one place, that can read settings
and cannot write them.

This package is the audit's *judgement*, and it holds no credential. The
schedule, the list of Projects and the token live in the private platform
repository that invokes it. What is here can be run against recorded
settings, which is what the fixtures are.
"""


class AuditError(Exception):
    """The audit could not run - which is not the same as a Project failing.

    Exit status 2, never 1. A token that expired, a Project that has been
    renamed, a rate limit: none of them is a statement about a Project's
    settings, and reporting one as drift sends somebody to inspect a ruleset
    that was never the problem. It is also the more dangerous confusion of
    the two, because an audit that cannot see is indistinguishable from an
    audit that sees nothing wrong.
    """
