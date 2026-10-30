from __future__ import annotations

from pathlib import Path

from voice_agent.sandbox.runner import HostLayout, SandboxPolicy, build_command

LAYOUT = HostLayout(
    usr_merged={"/bin": True, "/lib": True, "/sbin": True},
    has_lib64=False,
    has_etc_alternatives=True,
)


def test_default_policy_keeps_only_usr_and_alternatives() -> None:
    cmd = build_command(SandboxPolicy(root=Path("/r")), ["true"], LAYOUT)
    assert "/etc/alternatives" in cmd
    assert "/var" not in cmd


def test_host_readonly_binds_host_dirs_read_only_and_never_home() -> None:
    cmd = build_command(SandboxPolicy(root=Path("/r"), host_readonly=True), ["true"], LAYOUT)
    assert "/etc/alternatives" not in cmd
    for host in ("/etc", "/var"):
        i = cmd.index(host)
        assert cmd[i - 1] == "--ro-bind" and cmd[i + 1] == host
    assert "/home" not in cmd
    assert "--unshare-all" in cmd and "--share-net" not in cmd
    assert cmd[cmd.index("--bind") + 2] == "/sandbox"
