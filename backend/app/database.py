"""Database configuration and session management."""
import logging
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text, event
from app.config import settings

logger = logging.getLogger(__name__)


# Create async engine with SQLite-specific settings
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,
    future=True,
)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Set SQLite pragmas and register custom functions on each new connection."""
    import re as re_module
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
    cursor.execute("PRAGMA temp_store=MEMORY")
    try:
        cursor.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
    except Exception:
        pass  # Some SQLite builds may not support mmap_size
    cursor.close()

    def _regexp(pattern, string):
        if string is None:
            return False
        try:
            return bool(re_module.search(pattern, string))
        except re_module.error:
            return False

    dbapi_connection.create_function("regexp", 2, _regexp)


_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "REPLAC")


def install_savepoint_fix(sync_engine) -> None:
    """Make a leading SAVEPOINT behave like a nested transaction.

    The driver only emits BEGIN before the first write, so SELECTs autocommit and
    concurrent read-then-write sessions wait on busy_timeout instead of failing
    with SQLITE_BUSY_SNAPSHOT. The catch: a SAVEPOINT issued before any write in
    the transaction starts its own transaction, and its RELEASE commits. So when a
    savepoint opens with no write yet, open the transaction first with BEGIN
    IMMEDIATE (takes the write lock now, waiting on busy_timeout).
    Not the docs' isolation_level=None + BEGIN-on-every-transaction recipe: that
    puts every SELECT in a snapshot and turns concurrent writes into instant
    "database is locked".
    """
    @event.listens_for(sync_engine, "begin")
    def _txn_begin(conn):
        conn.info["_sqlite_txn_open"] = False

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _txn_write(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip()[:6].upper() in _WRITE_VERBS:
            conn.info["_sqlite_txn_open"] = True

    @event.listens_for(sync_engine, "savepoint")
    def _txn_savepoint(conn, name):
        if not conn.info.get("_sqlite_txn_open"):
            conn.info["_sqlite_txn_open"] = True
            conn.exec_driver_sql("BEGIN IMMEDIATE")


install_savepoint_fix(engine.sync_engine)


# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class for all database models."""
    pass


async def get_db() -> AsyncSession:
    """Dependency to get database session."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def _dedupe_live_hosts(connection):
    """Merge live duplicate hosts per (project, ip) BEFORE the unique index.

    Keeps the oldest row, re-points child rows to it and soft-deletes the rest.
    Host-scoped variables that would collide with the keeper's are dropped
    (keeper wins). Checklist claims keep their host_scope_key; they are leases.
    """
    dupes = connection.execute(text(
        "SELECT h.id, (SELECT MIN(k.id) FROM hosts k WHERE k.project_id = h.project_id"
        " AND k.ip_address = h.ip_address AND k.deleted_at IS NULL) AS keeper"
        " FROM hosts h WHERE h.deleted_at IS NULL AND h.ip_address IS NOT NULL"
    )).fetchall()
    dupes = [(hid, keeper) for hid, keeper in dupes if hid != keeper]
    if not dupes:
        return
    tables = {r[0] for r in connection.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
    for loser, keeper in sorted(dupes):
        params = {"loser": loser, "keeper": keeper}
        if "project_variables" in tables:
            connection.execute(text(
                "DELETE FROM project_variables WHERE host_id = :loser AND deleted_at IS NULL"
                " AND key IN (SELECT key FROM project_variables"
                " WHERE host_id = :keeper AND deleted_at IS NULL)"), params)
        for table in (
            "services", "executions", "discovered_files", "findings", "evidence",
            "checklist_claims", "flow_executions", "project_variables",
        ):
            if table in tables:
                connection.execute(text(
                    f"UPDATE {table} SET host_id = :keeper WHERE host_id = :loser"), params)
        connection.execute(text(
            "UPDATE hosts SET deleted_at = CURRENT_TIMESTAMP WHERE id = :loser"), params)
    logger.warning(f"[Migration] Merged {len(dupes)} duplicate host row(s) before unique index.")


def _apply_schema_migrations(connection):
    """Add any missing columns to existing tables (lightweight migration).

    Called via run_sync so ``connection`` is a sync SQLAlchemy Connection.
    """
    migrations = [
        ("hosts", "tags", "TEXT NOT NULL DEFAULT '[]'"),
        ("checklist_items", "target_filter", "TEXT NOT NULL DEFAULT '{}'"),
        ("checklist_items", "finding_template", "TEXT NOT NULL DEFAULT '{}'"),
        ("projects", "public_id", "TEXT"),
        ("projects", "deleted_at", "DATETIME"),
        ("projects", "created_by_user_id", "INTEGER"),
        ("hosts", "public_id", "TEXT"),
        ("hosts", "deleted_at", "DATETIME"),
        ("checklist_groups", "public_id", "TEXT"),
        ("checklist_groups", "updated_at", "DATETIME"),
        ("checklist_groups", "deleted_at", "DATETIME"),
        ("checklist_items", "public_id", "TEXT"),
        ("checklist_items", "deleted_at", "DATETIME"),
        ("executions", "public_id", "TEXT"),
        ("executions", "updated_at", "DATETIME"),
        ("executions", "deleted_at", "DATETIME"),
        ("executions", "started_by_user_id", "INTEGER"),
        ("project_variables", "public_id", "TEXT"),
        ("project_variables", "created_at", "DATETIME"),
        ("project_variables", "updated_at", "DATETIME"),
        ("project_variables", "deleted_at", "DATETIME"),
        ("services", "public_id", "TEXT"),
        ("discovered_files", "public_id", "TEXT"),
        ("flows", "public_id", "TEXT"),
        ("flow_steps", "public_id", "TEXT"),
        ("users", "role", "TEXT NOT NULL DEFAULT 'operator'"),
        ("users", "must_change_password", "BOOLEAN NOT NULL DEFAULT 0"),
        ("project_variables", "ad_domain_id", "INTEGER"),
        ("checklist_groups", "ad_domain_id", "INTEGER"),
        ("flow_steps", "target_mode", "TEXT NOT NULL DEFAULT 'inherit'"),
        ("flow_steps", "target_filter", "TEXT NOT NULL DEFAULT '{}'"),
        ("hosts", "excluded", "BOOLEAN NOT NULL DEFAULT 0"),
    ]

    for table, column, col_def in migrations:
        rows = connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
        existing_cols = {row[1] for row in rows}
        if column not in existing_cols:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))

    # Backfill timestamps/public IDs for pre-existing rows when columns were newly added.
    for table in (
        "projects",
        "hosts",
        "checklist_groups",
        "checklist_items",
        "executions",
        "project_variables",
        "services",
        "discovered_files",
        "flows",
        "flow_steps",
    ):
        cols = {row[1] for row in connection.execute(text(f"PRAGMA table_info({table})")).fetchall()}
        if "public_id" in cols:
            connection.execute(
                text(
                    f"UPDATE {table} SET public_id = lower(hex(randomblob(16))) "
                    "WHERE public_id IS NULL OR public_id = ''"
                )
            )
        if "updated_at" in cols:
            connection.execute(
                text(
                    f"UPDATE {table} SET updated_at = CURRENT_TIMESTAMP "
                    "WHERE updated_at IS NULL"
                )
            )
        if "created_at" in cols:
            connection.execute(
                text(
                    f"UPDATE {table} SET created_at = CURRENT_TIMESTAMP "
                    "WHERE created_at IS NULL"
                )
            )

    # Deterministic dedup BEFORE the scoped unique indexes: keep the latest
    # (updated_at, id) live row per scope, hard-delete shadowed dupes. Every
    # node computes the same winner, so no sync round-trip is needed.
    connection.execute(text(
        "DELETE FROM project_variables WHERE id IN ("
        " SELECT id FROM ("
        " SELECT id, ROW_NUMBER() OVER ("
        " PARTITION BY project_id, key, IFNULL(host_id, -1), IFNULL(ad_domain_id, -1)"
        " ORDER BY updated_at DESC, id DESC) AS rn"
        " FROM project_variables WHERE deleted_at IS NULL"
        " ) WHERE rn > 1)"
    ))

    _dedupe_live_hosts(connection)

    # Create indexes when missing.
    index_statements = [
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_public_id ON projects(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_hosts_public_id ON hosts(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_checklist_groups_public_id ON checklist_groups(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_checklist_items_public_id ON checklist_items(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_executions_public_id ON executions(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_project_variables_public_id ON project_variables(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_services_public_id ON services(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_discovered_files_public_id ON discovered_files(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_flows_public_id ON flows(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_flow_steps_public_id ON flow_steps(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_ad_domains_public_id ON ad_domains(public_id)",
        # Matches Host.excluded index=True; create_all only covers freshly made tables.
        "CREATE INDEX IF NOT EXISTS ix_hosts_excluded ON hosts(excluded)",
        # One live host per (project, ip) (mirrors Host.__table_args__).
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_hosts_project_ip_live ON "
        "hosts(project_id, ip_address) WHERE deleted_at IS NULL AND ip_address IS NOT NULL",
        # Scoped variable uniqueness (mirrors ProjectVariable.__table_args__).
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_vars_global ON "
        "project_variables(project_id, key) WHERE host_id IS NULL "
        "AND ad_domain_id IS NULL AND deleted_at IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_vars_host ON "
        "project_variables(project_id, host_id, key) WHERE host_id IS NOT NULL "
        "AND deleted_at IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_vars_domain ON "
        "project_variables(project_id, ad_domain_id, key) "
        "WHERE ad_domain_id IS NOT NULL AND host_id IS NULL AND deleted_at IS NULL",
    ]
    for stmt in index_statements:
        connection.execute(text(stmt))


async def init_db():
    """Initialize database tables and apply any pending schema migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_schema_migrations)
    await _seed_default_admin()


async def _seed_default_admin():
    """Create the default admin account on first boot (no users in DB)."""
    from app.models.user import User
    from app.core.auth import hash_password

    async with async_session_maker() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM users"))
        count = result.scalar()
        if count > 0:
            return

        admin = User(
            username="admin",
            email="admin@local",
            password_hash=hash_password("admin"),
            is_active=True,
            role="admin",
            must_change_password=True,
        )
        session.add(admin)
        await session.flush()
        # Same event shape api/users.py emits on user creation, so peers get the admin.
        from app.core.sync import sync_service
        from app.api.users import _user_sync_payload
        await sync_service.record_event(
            session,
            entity_type="users",
            entity_public_id=admin.public_id,
            operation="create",
            payload=_user_sync_payload(admin),
        )
        await session.commit()
        logger.info("Default admin account created (admin / admin). Password change required on first login.")


async def close_db():
    """Close database connections."""
    await engine.dispose()

