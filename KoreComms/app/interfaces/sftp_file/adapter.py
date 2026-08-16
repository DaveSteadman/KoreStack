"""Write KoreComms output to one configured SFTP file."""
from __future__ import annotations

import json
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

    def _write(self, content: str) -> str:
        cfg         = self._config()
        host        = str(cfg.get("host") or "").strip()
        username    = str(cfg.get("username") or "").strip()
        remote_path = self._remote_path(cfg.get("remote_path"))
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
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
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
