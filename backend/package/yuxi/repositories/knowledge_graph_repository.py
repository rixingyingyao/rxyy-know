from __future__ import annotations

from typing import Any

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    KnowledgeGraphEntity,
    KnowledgeGraphEntityMention,
    KnowledgeGraphTriple,
    KnowledgeGraphTripleMention,
)


class KnowledgeGraphRepository:
    async def count_by_kb_id(self, kb_id: str) -> tuple[int, int]:
        async with pg_manager.get_async_session_context() as session:
            entity_count = await session.scalar(
                select(func.count()).select_from(KnowledgeGraphEntity).where(KnowledgeGraphEntity.kb_id == kb_id)
            )
            triple_count = await session.scalar(
                select(func.count()).select_from(KnowledgeGraphTriple).where(KnowledgeGraphTriple.kb_id == kb_id)
            )
            return int(entity_count or 0), int(triple_count or 0)

    async def upsert_chunk_graph(
        self,
        *,
        kb_id: str,
        file_id: str,
        chunk_id: str,
        entities: list[dict[str, Any]],
        triples: list[dict[str, Any]],
    ) -> None:
        async with pg_manager.get_async_session_context() as session:
            if entities:
                entity_rows = [{key: value for key, value in entity.items() if key != "content"} for entity in entities]
                entity_stmt = insert(KnowledgeGraphEntity).values(entity_rows)
                await session.execute(
                    entity_stmt.on_conflict_do_update(
                        index_elements=["entity_id"],
                        set_={
                            "name": entity_stmt.excluded.name,
                            "attributes": entity_stmt.excluded.attributes,
                            "updated_at": func.now(),
                        },
                    )
                )
                await session.execute(
                    insert(KnowledgeGraphEntityMention)
                    .values(
                        [
                            {
                                "entity_id": entity["entity_id"],
                                "kb_id": kb_id,
                                "file_id": file_id,
                                "chunk_id": chunk_id,
                            }
                            for entity in entities
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=["entity_id", "chunk_id"])
                )

            if triples:
                triple_rows = [
                    {key: value for key, value in triple.items() if key not in {"text", "extractor_type"}}
                    for triple in triples
                ]
                triple_stmt = insert(KnowledgeGraphTriple).values(triple_rows)
                await session.execute(
                    triple_stmt.on_conflict_do_update(
                        index_elements=["triple_id"],
                        set_={
                            "content": triple_stmt.excluded.content,
                            "relation_type": triple_stmt.excluded.relation_type,
                            "updated_at": func.now(),
                        },
                    )
                )
                await session.execute(
                    insert(KnowledgeGraphTripleMention)
                    .values(
                        [
                            {
                                "triple_id": triple["triple_id"],
                                "kb_id": kb_id,
                                "file_id": file_id,
                                "chunk_id": chunk_id,
                                "text": triple.get("text"),
                                "extractor_type": triple.get("extractor_type"),
                            }
                            for triple in triples
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=["triple_id", "chunk_id"])
                )

    async def delete_file_references(self, file_id: str) -> tuple[list[str], list[str]]:
        async with pg_manager.get_async_session_context() as session:
            affected_entity_ids = list(
                (
                    await session.execute(
                        select(KnowledgeGraphEntityMention.entity_id)
                        .where(KnowledgeGraphEntityMention.file_id == file_id)
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            affected_triple_ids = list(
                (
                    await session.execute(
                        select(KnowledgeGraphTripleMention.triple_id)
                        .where(KnowledgeGraphTripleMention.file_id == file_id)
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )

            await session.execute(
                delete(KnowledgeGraphTripleMention).where(KnowledgeGraphTripleMention.file_id == file_id)
            )
            await session.execute(
                delete(KnowledgeGraphEntityMention).where(KnowledgeGraphEntityMention.file_id == file_id)
            )

            orphan_triple_ids: list[str] = []
            if affected_triple_ids:
                triple_has_mentions = exists().where(
                    KnowledgeGraphTripleMention.triple_id == KnowledgeGraphTriple.triple_id
                )
                orphan_triple_ids = list(
                    (
                        await session.execute(
                            select(KnowledgeGraphTriple.triple_id).where(
                                KnowledgeGraphTriple.triple_id.in_(affected_triple_ids),
                                ~triple_has_mentions,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if orphan_triple_ids:
                    await session.execute(
                        delete(KnowledgeGraphTriple).where(KnowledgeGraphTriple.triple_id.in_(orphan_triple_ids))
                    )

            orphan_entity_ids: list[str] = []
            if affected_entity_ids:
                entity_has_mentions = exists().where(
                    KnowledgeGraphEntityMention.entity_id == KnowledgeGraphEntity.entity_id
                )
                entity_has_triples = exists().where(
                    or_(
                        KnowledgeGraphTriple.source_entity_id == KnowledgeGraphEntity.entity_id,
                        KnowledgeGraphTriple.target_entity_id == KnowledgeGraphEntity.entity_id,
                    )
                )
                orphan_entity_ids = list(
                    (
                        await session.execute(
                            select(KnowledgeGraphEntity.entity_id).where(
                                KnowledgeGraphEntity.entity_id.in_(affected_entity_ids),
                                ~entity_has_mentions,
                                ~entity_has_triples,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if orphan_entity_ids:
                    await session.execute(
                        delete(KnowledgeGraphEntity).where(KnowledgeGraphEntity.entity_id.in_(orphan_entity_ids))
                    )

            return orphan_entity_ids, orphan_triple_ids

    async def delete_by_kb_id(self, kb_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(KnowledgeGraphTripleMention).where(KnowledgeGraphTripleMention.kb_id == kb_id))
            await session.execute(delete(KnowledgeGraphEntityMention).where(KnowledgeGraphEntityMention.kb_id == kb_id))
            await session.execute(delete(KnowledgeGraphTriple).where(KnowledgeGraphTriple.kb_id == kb_id))
            await session.execute(delete(KnowledgeGraphEntity).where(KnowledgeGraphEntity.kb_id == kb_id))
