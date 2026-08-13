"""Submission intake: fetch a pinned entrant repository and bind its agent.py.

The public surface is deliberately small:

``contract``   loads ``agent.py`` and adapts it to the engine's seat protocol
``fetch``      resolves a GitHub HTTPS URL at an exact commit under a size cap
``validate``   the CLI that runs both and then smoke-tests the agent in-game
"""

from .contract import (
    ENTRYPOINT_ATTRIBUTE,
    ENTRYPOINT_FILENAME,
    IllegalActionError,
    SubmissionAgent,
    SubmissionError,
    bind_seat,
    load_entrypoint,
    load_module,
)
from .fetch import (
    MAX_REPO_BYTES,
    FetchError,
    checkout_pinned,
    directory_size,
    parse_github_https,
    parse_commit_sha,
)

__all__ = [
    "ENTRYPOINT_ATTRIBUTE",
    "ENTRYPOINT_FILENAME",
    "FetchError",
    "IllegalActionError",
    "MAX_REPO_BYTES",
    "SubmissionAgent",
    "SubmissionError",
    "bind_seat",
    "checkout_pinned",
    "directory_size",
    "load_entrypoint",
    "load_module",
    "parse_commit_sha",
    "parse_github_https",
]
