"""scripts/stack/*.sh against stub ip/ssh/ping programs: no Pi, no laptop network needed."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
STACK = REPO / "scripts" / "stack"
TOKEN = "s3cret-token-value-that-must-never-be-printed"

FAKE_IP = """#!/usr/bin/env bash
case "$*" in
  *"-4 -o addr show"*) cat "$FAKE_DIR/ip_addr" ;;
  *"neigh show"*) cat "$FAKE_DIR/neigh" ;;
esac
"""
FAKE_PING = "#!/usr/bin/env bash\nexit 0\n"
FAKE_SSH = """#!/usr/bin/env bash
cmd="${@: -1}"
echo "$cmd" >>"$FAKE_DIR/ssh_calls"
[ -f "$FAKE_DIR/ssh_down" ] && exit 255
case "$cmd" in
  "echo pi-ok") echo pi-ok ;;
  "ip -o addr show"*) cat "$FAKE_DIR/pi_addrs" ;;
  *sha256sum*) cat "$FAKE_DIR/pi_sum" ;;
  "cat /proc/asound/cards") cat "$FAKE_DIR/cards" ;;
  *pwd*) [ -f "$FAKE_DIR/no_repo" ] && exit 1; echo /home/pi/repo ;;
  "test -x"*) [ -f "$FAKE_DIR/no_venv" ] && exit 1; exit 0 ;;
  *portaudio*) echo /home/pi/.local/portaudio/lib ;;
esac
"""
CARDS = """ 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
                      vc4-hdmi-0
 2 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
 3 [CD002AUDIO     ]: USB-Audio - CD002-AUDIO
                      Jieli Technology CD002-AUDIO at usb-0000:01:00.0-1.1, full speed
"""
PI_ADDRS = """2: eth0    inet 169.254.7.7/16 brd 169.254.255.255 scope link eth0
2: eth0    inet6 fe80::7:7/64 scope link
3: wlan0    inet6 fe80::9:9/64 scope link
"""


def sha12(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:12]


@pytest.fixture
def world(tmp_path: Path):
    fake = tmp_path / "fake"
    bins = tmp_path / "bin"
    home = tmp_path / "home"
    fake.mkdir()
    bins.mkdir()
    (home / ".config").mkdir(parents=True)
    (home / ".ssh").mkdir()
    (home / ".ssh" / "key").write_text("k")
    (home / ".config" / "lan_speech_token").write_text(TOKEN)
    for name, body in (("ip", FAKE_IP), ("ping", FAKE_PING), ("ssh", FAKE_SSH)):
        (bins / name).write_text(body)
        (bins / name).chmod(0o755)
    (fake / "ip_addr").write_text(
        "2: enx1    inet 169.254.1.1/16 brd 169.254.255.255 scope link enx1\n"
        "3: wlan0 inet 192.168.1.5/24\n"
    )
    (fake / "neigh").write_text(
        "169.254.7.7 lladdr aa:bb REACHABLE\nfe80::7:7 lladdr aa:bb STALE\n"
    )
    (fake / "pi_addrs").write_text(PI_ADDRS)
    (fake / "pi_sum").write_text(sha12(TOKEN) + "\n")
    (fake / "cards").write_text(CARDS)
    (tmp_path / "config").mkdir()
    env = {
        "PATH": f"{bins}:{os.environ['PATH']}",
        "HOME": str(home),
        "FAKE_DIR": str(fake),
        "STACK_REPO": str(tmp_path),
        "PI_KEY": str(home / ".ssh" / "key"),
    }

    class World:
        pass

    w = World()
    w.tmp, w.fake, w.env, w.pi_env = tmp_path, fake, env, tmp_path / "config" / "pi.env"  # type: ignore[attr-defined]
    return w


def detect(w, **extra):
    env = {**w.env, **extra}
    return subprocess.run(
        ["bash", str(STACK / "pi_detect.sh")], env=env, capture_output=True, text=True, timeout=30
    )


def test_detect_success_writes_env_and_never_prints_token(world):
    r = detect(world)
    assert r.returncode == 0, r.stderr
    assert TOKEN not in r.stdout + r.stderr
    assert sha12(TOKEN) in r.stdout
    cfg = dict(
        line.split("=", 1)
        for line in world.pi_env.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert cfg["CABLE_IF"] == "enx1" and cfg["LAPTOP_IP"] == "169.254.1.1"
    assert cfg["PI_V4"] == "169.254.7.7" and cfg["PI_V6"] == "fe80::7:7"
    assert cfg["PI_AUDIO_DEVICE"] == "CD002-AUDIO"
    assert cfg["PI_REPO"] == "/home/pi/repo"
    assert cfg["PORT_PI_SPEAKER"] == "8780" and cfg["DETECTED_AT"]
    assert TOKEN not in world.pi_env.read_text()
    assert not list(world.pi_env.parent.glob("pi.env.*")), "temp file left behind"


def assert_failed(r, world, step: str, hint_word: str = "fix:"):
    assert r.returncode != 0
    assert "failed at step" in r.stderr and step in r.stderr
    assert hint_word in r.stderr
    assert not world.pi_env.exists()
    assert not list(world.pi_env.parent.glob("pi.env.*"))


def test_no_cable_address(world):
    (world.fake / "ip_addr").write_text("3: wlan0 inet 192.168.1.5/24\n")
    assert_failed(detect(world), world, "cable interface")


def test_pi_does_not_answer(world):
    (world.fake / "ssh_down").write_text("")
    assert_failed(detect(world), world, "find the Pi")


def test_unreadable_key(world):
    assert_failed(detect(world, PI_KEY="/nonexistent"), world, "find the Pi")


def test_token_mismatch_prints_prefixes_only(world):
    (world.fake / "pi_sum").write_text("aaaaaaaaaaaa\n")
    r = detect(world)
    assert_failed(r, world, "session token")
    assert "aaaaaaaaaaaa" in r.stderr and sha12(TOKEN) in r.stderr
    assert TOKEN not in r.stdout + r.stderr


def test_token_missing_on_pi(world):
    (world.fake / "pi_sum").write_text("")
    assert_failed(detect(world), world, "session token")


def test_no_usb_speaker(world):
    (world.fake / "cards").write_text(" 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n")
    assert_failed(detect(world), world, "Pi loudspeaker")


def test_two_usb_cards_need_a_choice_but_pattern_wins(world):
    two = CARDS + " 4 [UACDemoV10    ]: USB-Audio - UACDemoV1.0\n"
    (world.fake / "cards").write_text(two)
    r = detect(world)
    assert r.returncode == 0 and "CD002-AUDIO" in world.pi_env.read_text()
    world.pi_env.unlink()
    r = detect(world, PI_AUDIO_PATTERN="NOPE")
    assert_failed(r, world, "Pi loudspeaker")
    assert "UACDemoV1.0" in r.stderr


def test_missing_repo_and_venv(world):
    (world.fake / "no_repo").write_text("")
    assert_failed(detect(world), world, "Pi repository")
    (world.fake / "no_repo").unlink()
    (world.fake / "no_venv").write_text("")
    assert_failed(detect(world), world, "Pi repository")


def test_failure_leaves_an_existing_file_unchanged(world):
    world.pi_env.write_text("CABLE_IF=old\nPI_V4=169.254.9.9\n")
    (world.fake / "pi_sum").write_text("bbbbbbbbbbbb\n")
    r = detect(world)
    assert r.returncode != 0
    assert world.pi_env.read_text() == "CABLE_IF=old\nPI_V4=169.254.9.9\n"
    assert not list(world.pi_env.parent.glob("pi.env.*"))


def test_known_address_from_old_file_is_tried(world):
    (world.fake / "neigh").write_text("")
    world.pi_env.write_text("PI_V4=169.254.7.7\n")
    r = detect(world)
    assert r.returncode == 0, r.stderr


def pick(cards: str, pattern: str = "CD002", preferred: str = "") -> subprocess.CompletedProcess:
    script = f'PI_DETECT_SOURCED=1; source "{STACK}/pi_detect.sh"; pick_audio "$1" "$2" "$3"'
    return subprocess.run(
        ["bash", "-c", script, "x", cards, pattern, preferred], capture_output=True, text=True
    )


def test_pick_audio_parsing():
    assert pick(CARDS).stdout.strip() == "CD002-AUDIO"
    assert pick(CARDS, "ZZ").stdout.strip() == "CD002-AUDIO"  # only one USB card
    assert pick(CARDS, "ZZ", "CD002-AUDIO").returncode == 0
    none = pick(" 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n")
    assert none.returncode == 1 and none.stdout.strip() == ""


def stack_env(w, **extra):
    w.pi_env.write_text(
        "CABLE_IF=enx1\nLAPTOP_IP=169.254.1.1\nPI_V4=169.254.7.7\nPI_V6=fe80::7:7\nPI_USER=pi\nPI_KEY=/k\n"
        "PI_REPO=/home/pi/repo\nPI_AUDIO_DEVICE=CD002-AUDIO\nPI_LD_LIBRARY_PATH=/ld\n"
    )
    (w.tmp / "pipeline").mkdir(exist_ok=True)
    return {**w.env, "DRY": "1", "ROUTER_MODEL": "my-router", **extra}


def run_script(name: str, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(STACK / name), *args], env=env, capture_output=True, text=True, timeout=30
    )


def test_up_dry_prints_commands_from_pi_env(world):
    r = run_script("up.sh", stack_env(world), "all")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "my-router" in out and "--host 169.254.1.1" in out
    assert "pi@169.254.7.7" in out and "--device CD002-AUDIO" in out
    assert (
        out.index("llama-server")
        < out.index("lan_speech")
        < out.index("pi_speaker.py")
        < out.index("agent_server")
    )
    assert not (world.tmp / "out" / "stack" / "pids" / "router").exists()
    assert TOKEN not in out


def test_up_dry_gives_the_router_the_token_file_not_the_token(world):
    r = run_script("up.sh", stack_env(world), "laptop")
    assert "--api-key-file" in r.stdout and "lan_speech_token" in r.stdout
    assert TOKEN not in r.stdout


def test_up_dry_gives_the_agent_the_pi_address_from_pi_env(world):
    r = run_script("up.sh", stack_env(world), "laptop")
    assert "PI_DEVICE_URL=http://169.254.7.7:8790/mcp" in r.stdout


def test_up_dry_runs_detect_when_env_is_stale(world):
    env = stack_env(world, PI_ENV_MAX_AGE_MIN="0")
    r = run_script("up.sh", env, "pi")
    assert "pi_detect.sh" in r.stdout and "older than 0 min" in r.stdout


def test_up_dry_laptop_has_no_pi(world):
    r = run_script("up.sh", stack_env(world), "laptop")
    assert "pi_speaker" not in r.stdout and "agent_server" in r.stdout


def test_up_fails_loudly_on_empty_setting(world):
    stack_env(world)
    world.pi_env.write_text("CABLE_IF=enx1\n")
    r = run_script("up.sh", {**world.env, "DRY": "1"}, "pi")
    assert r.returncode != 0 and "LAPTOP_IP is empty" in r.stderr


def test_down_dry_names_pid_files_and_pi(world):
    env = stack_env(world)
    pids = world.tmp / "out" / "stack" / "pids"
    pids.mkdir(parents=True)
    (pids / "router").write_text("12345")
    r = run_script("down.sh", env, "all")
    assert "kill -TERM -- -12345" in r.stdout and "pi_speaker.pid" in r.stdout
    assert "pkill" not in r.stdout


def test_down_stale_pid_file_is_removed(world):
    env = stack_env(world, DRY="0")
    pids = world.tmp / "out" / "stack" / "pids"
    pids.mkdir(parents=True)
    (pids / "router").write_text("999999")
    r = run_script("down.sh", env, "laptop")
    assert "not running" in r.stdout and not (pids / "router").exists()


def test_talk_dry(world):
    r = run_script("talk.sh", stack_env(world))
    assert (
        "doc_chat" in r.stdout
        and "ws://169.254.1.1:8770" in r.stdout
        and "ws://169.254.7.7:8780" in r.stdout
    )


def test_status_dry(world):
    r = run_script("status.sh", stack_env(world))
    assert r.returncode == 0 and "169.254.7.7" in r.stdout


def make_pi_repo(tmp: Path, clips: int = 6, mode: int = 0o600) -> dict:
    repo = tmp / "pirepo"
    (repo / "config").mkdir(parents=True)
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("")
    (repo / ".venv" / "bin" / "python").chmod(0o755)
    (repo / "tests" / "fixtures" / "pi_echo").mkdir(parents=True)
    for i in range(1, clips + 1):
        (repo / "tests" / "fixtures" / "pi_echo" / f"reply-{i:02d}.wav").write_text("")
    (repo / "config" / "pi.env").write_text(
        "PI_REPO=/x\nPI_AUDIO_DEVICE=CD002-AUDIO\nPORT_PI_SPEAKER=8780\nPORT_PI_DEVICE=8790\n"
    )
    tok = tmp / "tok"
    tok.write_text("t")
    tok.chmod(mode)
    cards = tmp / "cards"
    cards.write_text(CARDS)
    return {
        "PATH": os.environ["PATH"],
        "STACK_REPO": str(repo),
        "TOKEN_FILE": str(tok),
        "CARDS_FILE": str(cards),
    }


def test_pi_check_all_good(tmp_path):
    r = run_script("pi_check.sh", make_pi_repo(tmp_path))
    assert r.returncode == 0, r.stdout
    assert "FAIL" not in r.stdout


def test_pi_check_lists_every_problem(tmp_path):
    env = make_pi_repo(tmp_path, clips=4, mode=0o644)
    Path(env["CARDS_FILE"]).write_text(" 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n")
    (Path(env["STACK_REPO"]) / ".venv" / "bin" / "python").unlink()
    r = run_script("pi_check.sh", env)
    assert r.returncode == 1
    for needle in (
        "no .venv/bin/python",
        "mode is 644",
        "CD002-AUDIO not in",
        "reply-05.wav reply-06.wav",
    ):
        assert needle in r.stdout, needle


def test_no_hardcoded_addresses_in_stack_files():
    files = [*STACK.glob("*.sh"), *(REPO / "deploy").rglob("*"), REPO / "Makefile"]
    pat = re.compile(r"169\.254\.\d+\.\d+|fe80::[0-9a-f]|enp\d")
    bad = [str(f) for f in files if f.is_file() and pat.search(f.read_text())]
    assert not bad


def test_units_read_pi_env_and_use_the_wrapper():
    for unit, role in (("pi-speaker", "speaker"), ("pi-device-mcp", "device")):
        text = (REPO / "deploy" / "systemd" / f"{unit}.service").read_text()
        assert "EnvironmentFile=@REPO@/config/pi.env" in text
        assert f"pi_service.sh {role}" in text
        assert "LAN_SPEECH_TOKEN" not in text
