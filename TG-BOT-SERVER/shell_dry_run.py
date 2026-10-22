"""Conservative builders for explicit native dry-run shell previews."""

import re


_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_./:@+\\-]+$")


def _simple_tokens(command: str) -> list[str] | None:
    if not isinstance(command, str) or not command or any(ch in command for ch in "\r\n\x00'\"`$;&|<>(){}[]*?!"):
        return None
    tokens = command.split()
    if not tokens or any(not _SAFE_TOKEN.fullmatch(token) for token in tokens):
        return None
    return tokens


def build_dry_run_command(command: str) -> str | None:
    """Return an allowlisted native dry-run variant, or None if unsupported."""
    tokens = _simple_tokens(command)
    if tokens is None:
        return None

    if len(tokens) >= 3 and tokens[0] == "git" and tokens[1] == "clean":
        options = tokens[2:]
        before_separator = options[: options.index("--")] if "--" in options else options
        if any(token.startswith("--") for token in before_separator):
            return None
        if any(token.startswith("-") and not token.startswith("--") and "n" in token[1:].casefold() for token in before_separator):
            return None
        if any(
            token.startswith("-") and (not token[1:] or any(flag not in "fdixXq" for flag in token[1:]))
            for token in before_separator
        ):
            return None
        if not any(token.startswith("-") and "f" in token[1:] for token in before_separator):
            return None
        return " ".join([tokens[0], tokens[1], "-n", *options])

    if len(tokens) >= 3 and tokens[0] == "rsync":
        args = tokens[1:]
        if any(token.casefold() in {"-n", "--dry-run"} or (token.startswith("-") and not token.startswith("--") and "n" in token[1:]) for token in args):
            return None
        if "--" in args:
            return None
        return " ".join([tokens[0], "--dry-run", *args])

    return None
