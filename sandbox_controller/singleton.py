"""持有 MySQL 命名锁，拒绝误启动的第二个控制进程。"""

from __future__ import annotations

import threading
from typing import Any

import mysql.connector

from app.db import write_connection_config


class ControllerSingleton:
    def __init__(self) -> None:
        self.connection: Any = None
        self.name = "causalagent:sandbox-controller"
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            if self.connection is not None:
                raise RuntimeError("sandbox controller singleton is already acquired")
            # A dedicated connection must not return to a business pool or
            # silently reconnect: GET_LOCK belongs to this exact MySQL session.
            connection = mysql.connector.connect(**write_connection_config(), autocommit=True)
            try:
                with connection.cursor(dictionary=True) as cursor:
                    cursor.execute("SELECT GET_LOCK(%s, 0) AS acquired", (self.name,))
                    if cursor.fetchone()["acquired"] != 1:
                        raise RuntimeError("sandbox controller requires one API process and one instance")
                self.connection = connection
            except BaseException:
                connection.close()
                raise

    def ensure_owned(self) -> None:
        with self._lock:
            if self.connection is None:
                raise RuntimeError("sandbox controller singleton lock is not held")
            with self.connection.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT IS_USED_LOCK(%s) = CONNECTION_ID() AS owned", (self.name,))
                if cursor.fetchone()["owned"] != 1:
                    raise RuntimeError("sandbox controller singleton lock was lost")

    def close(self) -> None:
        with self._lock:
            if self.connection is None:
                return
            connection, self.connection = self.connection, None
            try:
                # A terminated connection has already released its named lock.
                if connection.is_connected():
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT RELEASE_LOCK(%s)", (self.name,))
                        cursor.fetchone()
            finally:
                connection.close()
