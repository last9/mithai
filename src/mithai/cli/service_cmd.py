"""mithai service — system service management (launchd on macOS, systemd on Linux)."""

import os
import platform
import shutil
import subprocess
from pathlib import Path

import click


MITHAI_HOME = Path.home() / ".mithai"
LABEL = "io.mithai.agent"

# ─── Paths ────────────────────────────────────────────────────────────────────

def _mithai_bin() -> str:
    """Return the path to the mithai binary."""
    path = shutil.which("mithai")
    if path:
        return path
    # Fallback: assume /usr/local/bin
    return "/usr/local/bin/mithai"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _is_linux() -> bool:
    return platform.system() == "Linux"


# ─── launchd (macOS) ─────────────────────────────────────────────────────────

def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _load_env_vars(env_path: Path) -> dict[str, str]:
    """Parse key=value pairs from an env file."""
    env_vars = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env_vars[k.strip()] = v.strip()
    return env_vars


def _generate_plist(mithai_bin: str, config_path: Path, env_path: Path) -> str:
    """Generate a launchd plist XML string."""
    env_vars = _load_env_vars(env_path)

    env_xml = ""
    for k, v in sorted(env_vars.items()):
        env_xml += f"    <key>{k}</key>\n    <string>{v}</string>\n"

    log_dir = MITHAI_HOME / "logs"
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{mithai_bin}</string>
    <string>run</string>
    <string>--config</string>
    <string>{config_path}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
{env_xml}  </dict>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>{MITHAI_HOME}</string>
  <key>StandardOutPath</key>
  <string>{log_dir / "mithai.log"}</string>
  <key>StandardErrorPath</key>
  <string>{log_dir / "mithai.err"}</string>
</dict>
</plist>
"""


def _launchctl(*args) -> tuple[int, str, str]:
    """Run a launchctl command."""
    result = subprocess.run(
        ["launchctl"] + list(args),
        capture_output=True, text=True, timeout=15,
    )
    return result.returncode, result.stdout, result.stderr


def _launchd_install(config_path: Path, env_path: Path):
    mithai_bin = _mithai_bin()
    plist = _plist_path()

    if plist.exists():
        raise click.ClickException(
            f"Service already installed at {plist}. "
            f"Run `mithai service uninstall` first."
        )

    # Create log directory
    log_dir = MITHAI_HOME / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Write plist
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(_generate_plist(mithai_bin, config_path, env_path))

    click.echo(f"  Plist: {plist}")
    click.echo(f"  Binary: {mithai_bin}")
    click.echo(f"  Config: {config_path}")
    click.echo(f"  Logs: {log_dir}/")

    # Load the agent
    rc, out, err = _launchctl("load", str(plist))
    if rc != 0:
        click.echo(f"  Warning: launchctl load returned: {err.strip()}")
    else:
        click.echo("  Service loaded and will start at login.")


def _launchd_uninstall():
    plist = _plist_path()
    if not plist.exists():
        raise click.ClickException("Service is not installed.")

    # Unload first
    _launchctl("unload", str(plist))
    plist.unlink()
    click.echo(f"  Removed {plist}")


def _launchd_start():
    plist = _plist_path()
    if not plist.exists():
        raise click.ClickException(
            "Service not installed. Run `mithai service install` first."
        )
    rc, out, err = _launchctl("load", str(plist))
    if rc != 0 and "already loaded" not in err.lower():
        raise click.ClickException(f"Failed to start: {err.strip()}")
    click.echo("  Service started.")


def _launchd_stop():
    plist = _plist_path()
    if not plist.exists():
        raise click.ClickException("Service not installed.")
    rc, out, err = _launchctl("unload", str(plist))
    if rc != 0:
        raise click.ClickException(f"Failed to stop: {err.strip()}")
    click.echo("  Service stopped.")


def _launchd_status():
    plist = _plist_path()
    if not plist.exists():
        click.echo("  Status: not installed")
        return

    result = subprocess.run(
        ["launchctl", "list", LABEL],
        capture_output=True, text=True, timeout=10,
    )

    if result.returncode == 0:
        # Parse PID and status from launchctl list output
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                pid, last_exit, label = parts[0], parts[1], parts[2]
                if label == LABEL:
                    if pid == "-":
                        click.echo("  Status: installed but not running")
                    else:
                        click.echo(f"  Status: running (PID {pid})")
                    if last_exit != "0":
                        click.echo(f"  Last exit code: {last_exit}")
                    break
        else:
            # Fallback: full output
            click.echo(f"  {result.stdout.strip()}")
    else:
        click.echo("  Status: installed but not loaded")

    # Show recent logs
    log_file = MITHAI_HOME / "logs" / "mithai.log"
    err_file = MITHAI_HOME / "logs" / "mithai.err"

    if log_file.exists():
        click.echo(f"\n  Recent logs ({log_file}):")
        lines = log_file.read_text().splitlines()[-10:]
        for line in lines:
            click.echo(f"    {line}")

    if err_file.exists():
        err_lines = err_file.read_text().splitlines()[-5:]
        if err_lines:
            click.echo(f"\n  Recent errors ({err_file}):")
            for line in err_lines:
                click.echo(f"    {line}")


# ─── systemd (Linux) ─────────────────────────────────────────────────────────

def _unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "mithai.service"


def _generate_unit(mithai_bin: str, config_path: Path, env_path: Path) -> str:
    """Generate a systemd user unit file."""
    return f"""\
[Unit]
Description=Mithai AI Operations Agent
After=network.target

[Service]
Type=simple
ExecStart={mithai_bin} run --config {config_path}
EnvironmentFile={env_path}
WorkingDirectory={MITHAI_HOME}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def _systemctl(*args) -> tuple[int, str, str]:
    """Run a systemctl --user command."""
    result = subprocess.run(
        ["systemctl", "--user"] + list(args),
        capture_output=True, text=True, timeout=15,
    )
    return result.returncode, result.stdout, result.stderr


def _systemd_install(config_path: Path, env_path: Path):
    mithai_bin = _mithai_bin()
    unit = _unit_path()

    if unit.exists():
        raise click.ClickException(
            f"Service already installed at {unit}. "
            f"Run `mithai service uninstall` first."
        )

    # Write unit file
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(_generate_unit(mithai_bin, config_path, env_path))

    click.echo(f"  Unit: {unit}")
    click.echo(f"  Binary: {mithai_bin}")
    click.echo(f"  Config: {config_path}")

    # Reload and enable
    _systemctl("daemon-reload")
    rc, out, err = _systemctl("enable", "mithai.service")
    if rc != 0:
        click.echo(f"  Warning: enable failed: {err.strip()}")
    else:
        click.echo("  Service enabled (will start on login).")


def _systemd_uninstall():
    unit = _unit_path()
    if not unit.exists():
        raise click.ClickException("Service is not installed.")

    _systemctl("stop", "mithai.service")
    _systemctl("disable", "mithai.service")
    unit.unlink()
    _systemctl("daemon-reload")
    click.echo(f"  Removed {unit}")


def _systemd_start():
    unit = _unit_path()
    if not unit.exists():
        raise click.ClickException(
            "Service not installed. Run `mithai service install` first."
        )
    rc, out, err = _systemctl("start", "mithai.service")
    if rc != 0:
        raise click.ClickException(f"Failed to start: {err.strip()}")
    click.echo("  Service started.")


def _systemd_stop():
    unit = _unit_path()
    if not unit.exists():
        raise click.ClickException("Service not installed.")
    rc, out, err = _systemctl("stop", "mithai.service")
    if rc != 0:
        raise click.ClickException(f"Failed to stop: {err.strip()}")
    click.echo("  Service stopped.")


def _systemd_status():
    unit = _unit_path()
    if not unit.exists():
        click.echo("  Status: not installed")
        return

    rc, out, err = _systemctl("status", "mithai.service")
    # systemctl status returns non-zero for inactive services
    click.echo(f"  {out.strip()}")

    # Show recent journal logs
    rc2, logs, _ = _systemctl(
        "status", "mithai.service", "--no-pager", "-n", "10"
    )
    if rc2 == 0 and logs:
        click.echo(f"\n  Recent logs:")
        for line in logs.strip().splitlines():
            click.echo(f"    {line}")


# ─── CLI commands ─────────────────────────────────────────────────────────────

@click.group()
def service():
    """Manage mithai as a system service."""
    pass


@service.command()
@click.option("--config", "config_path", default=None,
              help="Path to config.yaml (default: ~/.mithai/config.yaml)")
@click.option("--env", "env_path", default=None,
              help="Path to env file (default: ~/.mithai/env)")
def install(config_path, env_path):
    """Install mithai as a system service (launchd on macOS, systemd on Linux)."""
    config = Path(config_path) if config_path else MITHAI_HOME / "config.yaml"
    env = Path(env_path) if env_path else MITHAI_HOME / "env"

    if not config.exists():
        raise click.ClickException(
            f"Config not found at {config}. Run `mithai init` first."
        )

    click.echo("Installing mithai service...")

    if _is_macos():
        _launchd_install(config, env)
    elif _is_linux():
        _systemd_install(config, env)
    else:
        raise click.ClickException(
            f"Unsupported platform: {platform.system()}. "
            f"Only macOS (launchd) and Linux (systemd) are supported."
        )

    click.echo("\n  Run `mithai service start` to start the service.")


@service.command()
def start():
    """Start the mithai service."""
    if _is_macos():
        _launchd_start()
    elif _is_linux():
        _systemd_start()
    else:
        raise click.ClickException(f"Unsupported platform: {platform.system()}")


@service.command()
def stop():
    """Stop the mithai service."""
    if _is_macos():
        _launchd_stop()
    elif _is_linux():
        _systemd_stop()
    else:
        raise click.ClickException(f"Unsupported platform: {platform.system()}")


@service.command()
def status():
    """Show mithai service status and recent logs."""
    if _is_macos():
        _launchd_status()
    elif _is_linux():
        _systemd_status()
    else:
        raise click.ClickException(f"Unsupported platform: {platform.system()}")


@service.command()
def uninstall():
    """Remove the mithai service."""
    if _is_macos():
        _launchd_uninstall()
    elif _is_linux():
        _systemd_uninstall()
    else:
        raise click.ClickException(f"Unsupported platform: {platform.system()}")
