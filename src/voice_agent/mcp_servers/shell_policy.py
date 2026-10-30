from __future__ import annotations

import re
import shlex

BLOCKED = frozenset(
    {"sudo", "su", "doas", "ssh", "scp", "sftp", "docker", "podman", "systemctl", "shutdown"}
    | {"reboot", "poweroff", "halt", "mount", "umount", "chroot", "nc", "ncat", "telnet"}
)
_SEGMENT = re.compile(r"\|\||&&|[;|&\n]")
_PIPE_TO_SHELL = re.compile(r"\b(curl|wget)\b[^|;&\n]*\|\s*(ba|z|da)?sh\b")
_FORK_BOMB = re.compile(r":\(\)\s*\{")
_WIPE_TARGETS = ("/", "/*", "~", "~/", "$HOME")


def check_command(command: str) -> str | None:
    """The refusal sentence for a command the agent may not run, or None (first fence only)."""
    if _PIPE_TO_SHELL.search(command) or _FORK_BOMB.search(command):
        return "I can't run that: it downloads and runs code, or it is a fork bomb."
    for segment in _SEGMENT.split(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        while words and "=" in words[0]:
            words = words[1:]
        if not words:
            continue
        name = words[0].lstrip("({`$").rsplit("/", 1)[-1]
        if name in BLOCKED or name.startswith("mkfs"):
            return f"I can't run {name}: it needs privileges or reaches outside the sandbox."
        rest = words[1:]
        recursive = any(w.startswith("-") and "r" in w.lower() for w in rest)
        if name == "rm" and recursive and any(w in _WIPE_TARGETS for w in rest):
            return "I can't delete the root or home folder."
        if name == "dd" and any(w.startswith("of=/dev/") for w in rest):
            return "I can't write to a device."
    return None
