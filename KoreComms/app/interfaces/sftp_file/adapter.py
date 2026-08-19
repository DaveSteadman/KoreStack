# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# adapter module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Primary types: SftpFileInterface.
# Function inventory:
# - _config: Implements the  config operation for this module.
# - _remote_path: Implements the  remote path operation for this module.
# - _add_verified_host_key: Registers the configured portable SSH host key.
# - _content_for_remote_path: Normalises typed file content before upload.
# - _write: Implements the  write operation for this module.
# - poll: Implements the poll operation for this module.
# - route_reply: Implements the route reply operation for this module.
# - send_new: Implements the send new operation for this module.
# ====================================================================================================

"""Write KoreComms output to one configured SFTP file."""
from __future__ import annotations

import base64
import json
import re
from pathlib import PurePosixPath

from app import crypto
from app.interfaces.common.base import BaseInterface

_SECRET_KEYS = ("password",)


class SftpFileInterface(BaseInterface):
    """Output-only interface which replaces one configured remote file per delivery."""

    def _config(self) -> dict:
        raw    = json.loads(self.config.get("config_json", "{}"))
        result = {}
        for key, value in raw.items():
            if key in _SECRET_KEYS and value:
                try:
                    result[key] = crypto.decrypt(value)
                except Exception:
                    result[key] = value
            else:
                result[key] = value
        return result

    @staticmethod
    def _remote_path(value: object) -> str:
        path = str(value or "").strip()
        if not path or not PurePosixPath(path).is_absolute():
            raise RuntimeError("SFTP destination file must be an absolute remote path")
        return path

    @staticmethod
    def _add_verified_host_key(client, host: str, port: int, value: object) -> None:
        """Register the portable, verified host key configured for this connection."""
        import paramiko

        parts = str(value or "").strip().split()
        if len(parts) == 2:
            key_type, encoded_key = parts
        elif len(parts) >= 3:
            _, key_type, encoded_key = parts[:3]
        else:
            raise RuntimeError("SFTP host key must be an OpenSSH public key, for example 'ssh-ed25519 AAAA...'")
        try:
            key = paramiko.PKey.from_type_string(key_type, base64.b64decode(encoded_key.encode("ascii")))
        except (TypeError, ValueError, UnicodeError, paramiko.SSHException) as exc:
            raise RuntimeError("SFTP host key is not a valid OpenSSH public key") from exc
        for known_host in {host, f"[{host}]:{port}"}:
            client.get_host_keys().add(known_host, key_type, key)

    @staticmethod
    def _content_for_remote_path(remote_path: str, content: str) -> str:
        """Extract and validate JSON when the configured destination is a JSON file."""
        if PurePosixPath(remote_path).suffix.casefold() != ".json":
            return content
        match     = re.search(r"```(?:json)?\s*(.*?)\s*```", content, flags=re.IGNORECASE | re.DOTALL)
        candidate = match.group(1) if match else content.strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "SFTP destination is a JSON file, but the agent output does not contain valid JSON"
            ) from exc
        if not isinstance(parsed, (dict, list)):
            raise RuntimeError("SFTP JSON destination must contain a JSON object or array")
        return json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"

    def _write(self, content: str) -> str:
        cfg         = self._config()
        host        = str(cfg.get("host") or "").strip()
        username    = str(cfg.get("username") or "").strip()
        remote_path = self._remote_path(cfg.get("remote_path"))
        content     = self._content_for_remote_path(remote_path, content)
        host_key    = str(cfg.get("host_key") or "").strip()
        trust_server = bool(cfg.get("trust_server"))
        if not host:
            raise RuntimeError("SFTP host is not configured")
        if not username:
            raise RuntimeError("SFTP username is not configured")

        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("SFTP support requires the 'paramiko' package") from exc

        client = paramiko.SSHClient()
        client.load_system_host_keys()
        if host_key and not trust_server:
            self._add_verified_host_key(client, host, int(cfg.get("port") or 22), host_key)
        client.set_missing_host_key_policy(
            paramiko.AutoAddPolicy() if trust_server else paramiko.RejectPolicy()
        )
        try:
            try:
                client.connect(
                    hostname        = host,
                    port            = int(cfg.get("port") or 22),
                    username        = username,
                    password        = str(cfg.get("password") or "") or None,
                    look_for_keys   = not bool(cfg.get("password")),
                    allow_agent     = not bool(cfg.get("password")),
                    timeout         = 20,
                    banner_timeout  = 20,
                    auth_timeout    = 20,
                )
            except (OSError, paramiko.SSHException) as exc:
                raise RuntimeError(
                    f"SFTP connection to {host}:{int(cfg.get('port') or 22)} failed: {exc}. "
                    "Configure the server's verified SSH public host key, or enable automatic server trust on this connection."
                ) from exc
            with client.open_sftp() as sftp:
                # Opening in wb mode truncates the existing destination before
                # writing the replacement UTF-8 payload.
                with sftp.open(remote_path, "wb") as remote_file:
                    remote_file.write(content.encode("utf-8"))
        finally:
            client.close()
        return remote_path

    def poll(self) -> list[dict]:
        return []

    def route_reply(self, conversation_id: int, content: str) -> None:
        self._write(content)

    def send_new(self, recipient: str, subject: str, content: str) -> dict:
        remote_path = self._write(content)
        thread_id   = f"sftp:{self.interface_id}:{remote_path}"
        return {
            "external_thread_id":  thread_id,
            "external_message_id": thread_id,
        }
