"""
轻量 SqliteSaver —— 用标准库 sqlite3 实现 LangGraph checkpoint 持久化。

LangGraph 的 checkpointer 只需要三个方法：get_tuple / put / list。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointTuple,
    CheckpointMetadata,
)


class SqliteSaver(BaseCheckpointSaver):
    """基于 sqlite3 的 checkpoint 持久化存储。

    用法：替换 MemorySaver —— builder.compile(checkpointer=SqliteSaver("data/checkpoints.db"))
    """

    def __init__(self, db_path: str = "data/checkpoints.db"):
        super().__init__()
        self._db_path = db_path
        self._lock = threading.Lock()

        # 确保目录存在
        import os
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    parent_id TEXT,
                    checkpoint BLOB NOT NULL,
                    metadata BLOB NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (thread_id, checkpoint_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_thread
                ON checkpoints(thread_id, created_at DESC)
            """)

    def get_tuple(self, config: dict) -> CheckpointTuple | None:
        thread_id = config.get("configurable", {}).get("thread_id", "")
        checkpoint_id = config.get("configurable", {}).get("checkpoint_id")

        with self._conn() as conn:
            if checkpoint_id:
                row = conn.execute(
                    "SELECT * FROM checkpoints WHERE thread_id=? AND checkpoint_id=?",
                    (thread_id, checkpoint_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM checkpoints WHERE thread_id=? ORDER BY created_at DESC LIMIT 1",
                    (thread_id,),
                ).fetchone()

            if not row:
                return None

            return CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": row["thread_id"],
                        "checkpoint_id": row["checkpoint_id"],
                    }
                },
                checkpoint=Checkpoint(**json.loads(row["checkpoint"])),
                metadata=CheckpointMetadata(**json.loads(row["metadata"])),
                parent_config=(
                    {"configurable": {"thread_id": thread_id, "checkpoint_id": row["parent_id"]}}
                    if row["parent_id"]
                    else None
                ),
            )

    def put(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict,
    ) -> dict:
        thread_id = config.get("configurable", {}).get("thread_id", "")
        checkpoint_id = checkpoint.get("id", "")

        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO checkpoints
                   (thread_id, checkpoint_id, parent_id, checkpoint, metadata)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    thread_id,
                    checkpoint_id,
                    config.get("configurable", {}).get("checkpoint_id"),
                    json.dumps(checkpoint, default=str),
                    json.dumps(metadata, default=str),
                ),
            )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            }
        }

    def list(self, config: dict, *, limit: int | None = None, before: str | None = None) -> list[CheckpointTuple]:
        thread_id = config.get("configurable", {}).get("thread_id", "")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE thread_id=? ORDER BY created_at DESC LIMIT ?",
                (thread_id, limit or 100),
            ).fetchall()

        result = []
        for row in rows:
            result.append(CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": row["thread_id"],
                        "checkpoint_id": row["checkpoint_id"],
                    }
                },
                checkpoint=Checkpoint(**json.loads(row["checkpoint"])),
                metadata=CheckpointMetadata(**json.loads(row["metadata"])),
                parent_config=(
                    {"configurable": {"thread_id": thread_id, "checkpoint_id": row["parent_id"]}}
                    if row["parent_id"]
                    else None
                ),
            ))
        return result

    # ── async 兼容（LangGraph 内部走 async 路径）──
    async def aget_tuple(self, config: dict) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def aput(
        self, config: dict, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: dict
    ) -> dict:
        return self.put(config, checkpoint, metadata, new_versions)

    async def alist(self, config: dict, *, limit: int | None = None, before: str | None = None) -> list[CheckpointTuple]:
        return self.list(config, limit=limit, before=before)

    async def aput_writes(
        self, config: dict, writes: list, task_id: str, task_path: str = ""
    ) -> None:
        thread_id = config.get("configurable", {}).get("thread_id", "")
        checkpoint_id = config.get("configurable", {}).get("checkpoint_id", "")
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS writes (
                       thread_id TEXT, checkpoint_id TEXT, task_id TEXT, task_path TEXT,
                       writes BLOB, PRIMARY KEY (thread_id, checkpoint_id, task_id, task_path))"""
            )
            conn.execute(
                "INSERT OR REPLACE INTO writes VALUES (?, ?, ?, ?, ?)",
                (thread_id, checkpoint_id, task_id, task_path, json.dumps(writes, default=str)),
            )

    async def adelete_thread(self, thread_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM checkpoints WHERE thread_id=?", (thread_id,))
            conn.execute("DELETE FROM writes WHERE thread_id=?", (thread_id,))

    async def adelete_for_runs(self, run_ids: list[str]) -> None:
        pass  # 不需要按 run_id 删除，checkpoint 粒度已足够

    async def acopy_thread(self, source_thread_id: str, dest_thread_id: str) -> None:
        with self._conn() as conn:
            for row in conn.execute(
                "SELECT * FROM checkpoints WHERE thread_id=?", (source_thread_id,)
            ).fetchall():
                conn.execute(
                    "INSERT OR REPLACE INTO checkpoints VALUES (?, ?, ?, ?, ?, ?)",
                    (dest_thread_id, row["checkpoint_id"], row["parent_id"],
                     row["checkpoint"], row["metadata"], row["created_at"]),
                )

    async def aprune(self, *, max_age_seconds: int | None = None) -> None:
        if max_age_seconds:
            with self._conn() as conn:
                conn.execute(
                    "DELETE FROM checkpoints WHERE datetime(created_at) < datetime('now', ?)",
                    (f"-{max_age_seconds} seconds",),
                )
