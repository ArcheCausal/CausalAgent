"""AsyncPostgresStore 的惰性装配和 cleanup 边界。

P2-U 只冻结 Store 与 checkpoint 的职责分离；真实连接、setup、重启保留由
P5 Docker 集成验收。这里不创建连接、不输出连接串，也不改变现有 migration。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator


class PostgresStoreDependencyError(RuntimeError):
    """当前环境没有可用的官方 Store 依赖。"""


@dataclass(frozen=True)
class PostgresStoreConfig:
    host: str
    port: int
    database: str
    user: str
    password: str | None
    connect_timeout_seconds: int
    pool_min_size: int
    pool_max_size: int

    @classmethod
    def from_environment(cls) -> "PostgresStoreConfig":
        def integer(name: str, default: int) -> int:
            raw = os.getenv(name, str(default))
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc

        return cls(
            host=os.getenv("CHECKPOINT_POSTGRES_HOST", "postgres-checkpoint"),
            port=integer("CHECKPOINT_POSTGRES_PORT", 5432),
            database=os.getenv("CHECKPOINT_POSTGRES_DATABASE", "causalagent_checkpoints"),
            user=os.getenv("CHECKPOINT_POSTGRES_USER", "causalagent_checkpoint"),
            password=os.getenv("CHECKPOINT_POSTGRES_PASSWORD") or None,
            connect_timeout_seconds=integer("CHECKPOINT_POSTGRES_CONNECT_TIMEOUT_SECONDS", 5),
            pool_min_size=integer("CHECKPOINT_POSTGRES_POOL_MIN_SIZE", 1),
            pool_max_size=integer("CHECKPOINT_POSTGRES_POOL_MAX_SIZE", 5),
        )

    def validate(self, *, require_credentials: bool = False) -> None:
        if not self.host.strip() or not self.database.strip() or not self.user.strip():
            raise ValueError("PostgreSQL host/database/user must be non-blank")
        if not 1 <= self.port <= 65535:
            raise ValueError("PostgreSQL port must be valid")
        if self.connect_timeout_seconds <= 0:
            raise ValueError("PostgreSQL connect timeout must be positive")
        if self.pool_min_size <= 0 or self.pool_max_size < self.pool_min_size:
            raise ValueError("PostgreSQL pool size is invalid")
        if require_credentials and not self.password:
            raise ValueError("PostgreSQL credentials are required")

    def connection_kwargs(self) -> dict[str, Any]:
        self.validate(require_credentials=True)
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "password": self.password,
            "connect_timeout": self.connect_timeout_seconds,
        }

    def connection_string(self) -> str:
        """使用 psycopg 的 conninfo 编码生成连接串，避免手工拼接凭据。"""

        try:
            from psycopg.conninfo import make_conninfo
        except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover
            raise PostgresStoreDependencyError("psycopg conninfo support is unavailable") from exc
        return make_conninfo(**self.connection_kwargs())


def build_async_postgres_store(*, pool: Any | None = None, config: PostgresStoreConfig | None = None) -> Any:
    """按当前 LangGraph Store API 惰性创建 Store，不在 import 时连接数据库。"""

    try:
        from langgraph.store.postgres import AsyncPostgresStore
    except (ImportError, ModuleNotFoundError):
        try:
            from langgraph.store.postgres.aio import AsyncPostgresStore
        except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover
            raise PostgresStoreDependencyError(
                "AsyncPostgresStore is unavailable in the installed LangGraph version"
            ) from exc

    if pool is not None:
        return AsyncPostgresStore(conn=pool)

    del config
    raise PostgresStoreDependencyError(
        "AsyncPostgresStore requires an owned pool here; use open_async_postgres_store() "
        "when the Store must own its connection lifecycle"
    )


@asynccontextmanager
async def open_async_postgres_store(
    *, config: PostgresStoreConfig | None = None
) -> AsyncIterator[Any]:
    """通过官方 ``from_conn_string`` 管理连接生命周期并完成 Store setup。"""

    try:
        from langgraph.store.postgres import AsyncPostgresStore
    except (ImportError, ModuleNotFoundError):
        try:
            from langgraph.store.postgres.aio import AsyncPostgresStore
        except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover
            raise PostgresStoreDependencyError(
                "AsyncPostgresStore is unavailable in the installed LangGraph version"
            ) from exc

    active_config = config or PostgresStoreConfig.from_environment()
    factory = getattr(AsyncPostgresStore, "from_conn_string", None)
    if not callable(factory):
        raise PostgresStoreDependencyError("AsyncPostgresStore.from_conn_string is unavailable")
    async with factory(active_config.connection_string()) as store:
        await setup_postgres_store(store)
        yield store


async def setup_postgres_store(store: Any) -> None:
    """调用官方 Store setup；与 checkpoint setup 分开。"""

    setup = getattr(store, "setup", None)
    if setup is None:
        raise PostgresStoreDependencyError("AsyncPostgresStore has no setup method")
    result = setup()
    if hasattr(result, "__await__"):
        await result


STORE_TABLE_NAMES = frozenset({"store", "store_migrations"})


def filter_checkpoint_cleanup_tables(table_names: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """只返回允许 cleanup 的 checkpoint 表，明确排除 Store 表。"""

    return tuple(name for name in table_names if name not in STORE_TABLE_NAMES)


STORE_PURGE_PAGE_SIZE = 100
STORE_PURGE_MAX_PAGES = 1000


async def verify_postgres_store_schema(store: Any) -> None:
    """确认官方 Store schema 已 setup 完成，避免清理过程遇到缺表。"""

    migrations = getattr(store, "MIGRATIONS", None)
    if not migrations:
        raise PostgresStoreDependencyError("AsyncPostgresStore MIGRATIONS is unavailable")
    pool = getattr(store, "conn", None)
    if pool is None:
        raise PostgresStoreDependencyError("AsyncPostgresStore connection is unavailable")
    expected_version = len(migrations) - 1
    async with pool.connection() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT v FROM store_migrations ORDER BY v DESC LIMIT 1"
            )
            row = await cursor.fetchone()
    actual_version = None if row is None else row["v"]
    if row is None or int(actual_version) < expected_version:
        raise RuntimeError(
            "PostgreSQL Store schema 未完成 setup: "
            f"expected>={expected_version}, actual={actual_version}"
        )


async def purge_store_namespace(store: Any, namespace: tuple[str, ...]) -> int:
    """用官方 Store API 清空一个 namespace 的全部条目，并复核结果为空。

    删除逐条按条目自身的 namespace 和 key 执行，不使用针对 Store 表的宽泛
    SQL；循环以一次返回空集的查询作为收敛证据。
    """

    normalized = tuple(str(part) for part in namespace)
    if not normalized or any(not part for part in normalized):
        raise ValueError("store namespace must contain non-empty parts")
    deleted = 0
    for _ in range(STORE_PURGE_MAX_PAGES):
        items = await store.asearch(normalized, limit=STORE_PURGE_PAGE_SIZE)
        if not items:
            return deleted
        for item in items:
            await store.adelete(tuple(item.namespace), item.key)
            deleted += 1
    raise RuntimeError("Store namespace cleanup did not converge")
