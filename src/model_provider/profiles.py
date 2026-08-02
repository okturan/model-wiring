"""SQLite credential-profile metadata and cross-process refresh leases."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .contracts import CredentialProfile
from .errors import ProfileError, RefreshError

SCHEMA_VERSION = 2


def default_profile_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".config"
    return base / "model-provider-kit" / "profiles.sqlite3"


class ProfileRegistry:
    """Stores only non-secret credential metadata.

    A short-lived SQLite connection is used for each operation so instances are
    safe to share across threads and processes.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_profile_path()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        with sqlite3.connect(self.path) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = FULL;
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS credential_profiles (
                    id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    auth_kind TEXT NOT NULL,
                    billing_kind TEXT NOT NULL,
                    secret_ref TEXT,
                    secret_store TEXT,
                    account_label TEXT,
                    priority INTEGER NOT NULL,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    scopes_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS credential_profiles_provider
                ON credential_profiles(provider_id, enabled, billing_kind, priority, id);
                CREATE TABLE IF NOT EXISTS refresh_leases (
                    profile_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_usage (
                    profile_id TEXT PRIMARY KEY,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at REAL,
                    FOREIGN KEY(profile_id) REFERENCES credential_profiles(id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS credential_pool_state (
                    pool_id TEXT PRIMARY KEY,
                    cursor INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                """
            )
            current = connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            if current and int(current[0]) > SCHEMA_VERSION:
                raise ProfileError(
                    f"profile database schema {current[0]} is newer than supported {SCHEMA_VERSION}"
                )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def upsert(self, profile: CredentialProfile) -> None:
        value = profile.to_dict()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO credential_profiles(
                    id, provider_id, auth_kind, billing_kind, secret_ref,
                    secret_store, account_label, priority, enabled, scopes_json,
                    metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    provider_id = excluded.provider_id,
                    auth_kind = excluded.auth_kind,
                    billing_kind = excluded.billing_kind,
                    secret_ref = excluded.secret_ref,
                    secret_store = excluded.secret_store,
                    account_label = excluded.account_label,
                    priority = excluded.priority,
                    enabled = excluded.enabled,
                    scopes_json = excluded.scopes_json,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    value["id"],
                    value["provider_id"],
                    value["auth_kind"],
                    value["billing_kind"],
                    value["secret_ref"],
                    value["secret_store"],
                    value["account_label"],
                    value["priority"],
                    int(value["enabled"]),
                    json.dumps(value["scopes"], sort_keys=True),
                    json.dumps(value["metadata"], sort_keys=True),
                    time.time(),
                ),
            )

    def get(self, profile_id: str) -> CredentialProfile:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM credential_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        if row is None:
            raise ProfileError(f"unknown credential profile: {profile_id}")
        return _row_to_profile(row)

    def list(
        self,
        *,
        provider_id: str | None = None,
        billing_kind: str | None = None,
        enabled_only: bool = False,
    ) -> tuple[CredentialProfile, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        if provider_id:
            clauses.append("provider_id = ?")
            parameters.append(provider_id)
        if billing_kind:
            clauses.append("billing_kind = ?")
            parameters.append(billing_kind)
        if enabled_only:
            clauses.append("enabled = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM credential_profiles {where}
                ORDER BY priority ASC, id ASC""",
                parameters,
            ).fetchall()
        return tuple(_row_to_profile(row) for row in rows)

    def delete(self, profile_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM credential_profiles WHERE id = ?", (profile_id,)
            )
        return cursor.rowcount > 0

    def claim_from_pool(
        self,
        profile_ids: tuple[str, ...],
        *,
        pool_id: str,
        strategy: str,
    ) -> CredentialProfile:
        """Atomically choose and record one enabled profile.

        Dynamic pool choice is deliberately separate from deterministic model
        selection. The returned profile ID can then be included in a plan.
        """

        if not profile_ids:
            raise ProfileError("credential pool is empty")
        placeholders = ",".join("?" for _ in profile_ids)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    f"""SELECT p.*, COALESCE(u.use_count, 0) AS use_count,
                               u.last_used_at AS last_used_at
                        FROM credential_profiles AS p
                        LEFT JOIN profile_usage AS u ON u.profile_id = p.id
                        WHERE p.id IN ({placeholders}) AND p.enabled = 1
                        ORDER BY p.priority ASC, p.id ASC""",
                    profile_ids,
                ).fetchall()
                if len(rows) != len(set(profile_ids)):
                    found = {str(row["id"]) for row in rows}
                    absent = sorted(set(profile_ids) - found)
                    raise ProfileError(
                        "credential pool contains unknown or disabled profiles: "
                        + ", ".join(absent)
                    )
                row = _choose_pool_row(connection, rows, pool_id, strategy)
                now = time.time()
                connection.execute(
                    """INSERT INTO profile_usage(profile_id, use_count, last_used_at)
                       VALUES (?, 1, ?)
                       ON CONFLICT(profile_id) DO UPDATE SET
                           use_count = profile_usage.use_count + 1,
                           last_used_at = excluded.last_used_at""",
                    (row["id"], now),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return _row_to_profile(row)

    def usage(self, profile_id: str) -> dict[str, int | float | None]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT use_count, last_used_at FROM profile_usage WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        return {
            "use_count": int(row["use_count"]) if row else 0,
            "last_used_at": float(row["last_used_at"])
            if row and row["last_used_at"]
            else None,
        }

    @contextmanager
    def refresh_lease(
        self,
        profile_id: str,
        *,
        ttl_seconds: float = 30.0,
        wait_seconds: float = 15.0,
    ) -> Iterator[None]:
        owner = f"{os.getpid()}:{uuid4()}"
        deadline = time.monotonic() + wait_seconds
        acquired = False
        while time.monotonic() < deadline:
            now = time.time()
            with self._connect() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    row = connection.execute(
                        "SELECT owner, expires_at FROM refresh_leases WHERE profile_id = ?",
                        (profile_id,),
                    ).fetchone()
                    if row is None or float(row["expires_at"]) <= now:
                        connection.execute(
                            """INSERT INTO refresh_leases(profile_id, owner, expires_at)
                            VALUES (?, ?, ?)
                            ON CONFLICT(profile_id) DO UPDATE SET
                                owner = excluded.owner,
                                expires_at = excluded.expires_at""",
                            (profile_id, owner, now + ttl_seconds),
                        )
                        connection.execute("COMMIT")
                        acquired = True
                    else:
                        connection.execute("ROLLBACK")
                except sqlite3.Error:
                    try:
                        connection.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise
            if acquired:
                break
            time.sleep(0.05)
        if not acquired:
            raise RefreshError(
                f"timed out waiting for OAuth refresh lease: {profile_id}"
            )
        try:
            yield
        finally:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM refresh_leases WHERE profile_id = ? AND owner = ?",
                    (profile_id, owner),
                )


def _row_to_profile(row: sqlite3.Row) -> CredentialProfile:
    return CredentialProfile(
        id=str(row["id"]),
        provider_id=str(row["provider_id"]),
        auth_kind=str(row["auth_kind"]),
        billing_kind=str(row["billing_kind"]),
        secret_ref=row["secret_ref"],
        secret_store=row["secret_store"],
        account_label=row["account_label"],
        priority=int(row["priority"]),
        enabled=bool(row["enabled"]),
        scopes=tuple(json.loads(row["scopes_json"])),
        metadata=dict(json.loads(row["metadata_json"])),
    )


def _choose_pool_row(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
    pool_id: str,
    strategy: str,
) -> sqlite3.Row:
    if strategy == "fill_first":
        return rows[0]
    if strategy == "least_used":
        return min(
            rows,
            key=lambda row: (
                int(row["use_count"]),
                float(row["last_used_at"] or 0),
                int(row["priority"]),
                str(row["id"]),
            ),
        )
    if strategy == "round_robin":
        state = connection.execute(
            "SELECT cursor FROM credential_pool_state WHERE pool_id = ?", (pool_id,)
        ).fetchone()
        cursor = int(state["cursor"]) if state else 0
        selected = rows[cursor % len(rows)]
        connection.execute(
            """INSERT INTO credential_pool_state(pool_id, cursor, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(pool_id) DO UPDATE SET
                   cursor = excluded.cursor,
                   updated_at = excluded.updated_at""",
            (pool_id, cursor + 1, time.time()),
        )
        return selected
    raise ProfileError(
        f"unsupported credential pool strategy {strategy!r}; "
        "expected fill_first, round_robin, or least_used"
    )
