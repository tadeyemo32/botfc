"""SSH deployer via paramiko – replaces SshDeployer.cpp + SshService.cpp."""

import os
import socket

import paramiko

from . import config as cfg

_BRAIN_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "brain",
    "botfc_brain.py",
)
_REMOTE_SCRIPT = "/home/nao/botfc_brain.py"
_PASSWORDS = [cfg.ROBOT_PASS, "Na0Na0", "nao"]


def _get_local_ip(target: str) -> str:
    """Detect the local IP that can reach *target* (UDP socket trick)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((target, 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _connect(host: str) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    for pw in _PASSWORDS:
        try:
            ssh.connect(host, username=cfg.ROBOT_USER, password=pw, timeout=5)
            return ssh
        except paramiko.AuthenticationException:
            continue
        except Exception as e:
            print(f"[Deployer] Connection error: {e}")
            raise
    raise RuntimeError(f"All passwords failed for {host}")


def deploy_and_run(host: str, trait: str = "balanced") -> bool:
    """Upload the brain script and launch it on the robot."""
    try:
        ssh = _connect(host)
    except Exception as e:
        print(f"[Deployer] Failed to connect to {host}: {e}")
        return False

    print(f"[Deployer] Connected to {host}. Uploading brain...")
    try:
        sftp = ssh.open_sftp()
        sftp.put(_BRAIN_PATH, _REMOTE_SCRIPT)
        sftp.close()
    except Exception as e:
        print(f"[Deployer] SFTP upload failed: {e}")
        ssh.close()
        return False

    server_ip = _get_local_ip(host)
    print(f"[Deployer] Upload complete. Server IP: {server_ip}")

    import time
    
    # 1. Kill synchronously and wait
    ssh.exec_command("pkill -f botfc_brain.py")
    time.sleep(1.0)
    
    # 2. Launch new brain and give the script a fraction of a second to detach
    cmd = (
        f"export PYTHONPATH={cfg.NAOQI_PATH} ; "
        f"nohup python {_REMOTE_SCRIPT}"
        f" --ip=127.0.0.1"
        f" --pport={cfg.ROBOT_PORT}"
        f" --trait={trait}"
        f" --server-ip={server_ip}"
        f" --server-port={cfg.SERVER_PORT}"
        f" > /home/nao/botfc_brain.log 2>&1 < /dev/null &"
    )
    ssh.exec_command(cmd)
    time.sleep(0.5)
    
    print("[Deployer] Brain launched.")
    ssh.close()
    return True


def stop_match(host: str) -> bool:
    """Kill the remote brain process."""
    try:
        ssh = _connect(host)
    except Exception as e:
        print(f"[Deployer] Failed to connect to {host}: {e}")
        return False

    print("[Deployer] Killing remote brain...")
    ssh.exec_command("pkill -f botfc_brain.py")
    ssh.close()
    return True


def test_connection(host: str) -> bool:
    """Quick SSH connectivity test."""
    try:
        ssh = _connect(host)
        ssh.close()
        return True
    except Exception:
        return False
