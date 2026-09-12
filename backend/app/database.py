"""Database configuration and session management."""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text, event
from app.config import settings


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
        await session.commit()
        print("Default admin account created (admin / admin). Password change required on first login.")


async def close_db():
    """Close database connections."""
    await engine.dispose()

