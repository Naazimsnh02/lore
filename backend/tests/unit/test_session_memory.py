"""Unit tests for the Session Memory Manager (Task 3).

Tests cover:
- Firestore schema / data models (Task 3.1)
- SessionMemoryManager CRUD and cross-session query operations (Task 3.2)

All Firestore calls are replaced with an in-memory fake so no real GCP
credentials are required.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import sys

import pytest

from backend.services.session_memory.manager import (
    SessionMemoryManager,
    SessionNotFoundError,
)
from backend.services.session_memory.models import (
    BranchNode,
    ContentRef,
    ContentRefMetadata,
    ContentType,
    DepthDial,
    GeoPoint,
    InteractionType,
    LocationVisit,
    OperatingMode,
    QueryResult,
    SessionDocument,
    SessionStatus,
    UserInteraction,
)


# ── In-memory Firestore fake ───────────────────────────────────────────────────

class FakeDocSnapshot:
    def __init__(self, data: dict | None):
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict:
        return self._data or {}


class FakeDocRef:
    def __init__(self, store: dict[str, dict], doc_id: str):
        self._store = store
        self._id = doc_id

    async def set(self, data: dict) -> None:
        self._store[self._id] = dict(data)

    async def get(self) -> FakeDocSnapshot:
        return FakeDocSnapshot(self._store.get(self._id))

    async def update(self, updates: dict) -> None:
        if self._id not in self._store:
            raise KeyError(f"Document {self._id} not found")
        doc = self._store[self._id]
        for key, value in updates.items():
            # Handle nested field paths like "content_count.narration_segments"
            if "." in key:
                parts = key.split(".", 1)
                if parts[0] not in doc:
                    doc[parts[0]] = {}
                # Handle Increment-like objects
                if hasattr(value, "_value"):  # FakeIncrement
                    current = doc[parts[0]].get(parts[1], 0)
                    doc[parts[0]][parts[1]] = current + value._value
                else:
                    doc[parts[0]][parts[1]] = value
            elif hasattr(value, "_values"):  # FakeArrayUnion
                current = doc.get(key, [])
                doc[key] = current + list(value._values)
            elif hasattr(value, "_value"):  # FakeIncrement
                doc[key] = doc.get(key, 0) + value._value
            else:
                doc[key] = value

    async def delete(self) -> None:
        self._store.pop(self._id, None)


class FakeQuery:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def where(self, *args, **kwargs) -> "FakeQuery":
        # Simple equality filter on user_id
        if len(args) >= 3 and args[1] == "==":
            field, _, value = args[0], args[1], args[2]
            self._docs = [d for d in self._docs if d.get(field) == value]
        return self

    def order_by(self, field: str, direction: str = "ASCENDING") -> "FakeQuery":
        reverse = direction == "DESCENDING"
        self._docs = sorted(self._docs, key=lambda d: d.get(field, 0), reverse=reverse)
        return self

    def limit(self, n: int) -> "FakeQuery":
        self._docs = self._docs[:n]
        return self

    async def __aiter__(self):
        for doc in self._docs:
            snap = FakeDocSnapshot(doc)
            snap.to_dict = lambda d=doc: d  # type: ignore[method-assign]
            yield snap

    def stream(self) -> "FakeQuery":
        return self


class FakeBatch:
    def __init__(self, store: dict[str, dict]):
        self._store = store
        self._deletes: list[str] = []

    def delete(self, doc_ref: FakeDocRef) -> None:
        self._deletes.append(doc_ref._id)

    async def commit(self) -> None:
        for doc_id in self._deletes:
            self._store.pop(doc_id, None)


class FakeArrayUnion:
    def __init__(self, values):
        self._values = values


class FakeIncrement:
    def __init__(self, value):
        self._value = value


class FakeFirestoreClient:
    """Minimal in-memory Firestore client for unit tests."""

    def __init__(self):
        self._store: dict[str, dict] = {}

    def collection(self, name: str) -> "FakeFirestoreClient":
        return self  # simplified – only one collection

    def document(self, doc_id: str) -> FakeDocRef:
        return FakeDocRef(self._store, doc_id)

    def batch(self) -> FakeBatch:
        return FakeBatch(self._store)

    def where(self, *args, **kwargs) -> FakeQuery:
        return FakeQuery(list(self._store.values())).where(*args, **kwargs)

    def order_by(self, *args, **kwargs) -> FakeQuery:
        return FakeQuery(list(self._store.values())).order_by(*args, **kwargs)

    def stream(self) -> FakeQuery:
        return FakeQuery(list(self._store.values()))

    def limit(self, n: int) -> FakeQuery:
        return FakeQuery(list(self._store.values())).limit(n)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_firestore_v1(monkeypatch):
    """Inject FakeArrayUnion and FakeIncrement into sys.modules so that the
    ``from google.cloud.firestore_v1 import ArrayUnion/Increment`` calls inside
    the manager succeed without needing real GCP libraries installed."""
    fake_module = MagicMock()
    fake_module.ArrayUnion = FakeArrayUnion
    fake_module.Increment = FakeIncrement
    monkeypatch.setitem(sys.modules, "google.cloud.firestore_v1", fake_module)


@pytest.fixture
def fake_db() -> FakeFirestoreClient:
    return FakeFirestoreClient()


@pytest.fixture
def manager(fake_db) -> SessionMemoryManager:
    # Patch ArrayUnion and Increment so the manager's import of
    # google.cloud.firestore_v1 doesn't fail in the test environment.
    with (
        patch(
            "backend.services.session_memory.manager.ArrayUnion",
            new=FakeArrayUnion,
            create=True,
        ),
        patch(
            "backend.services.session_memory.manager.Increment",
            new=FakeIncrement,
            create=True,
        ),
    ):
        yield SessionMemoryManager(firestore_client=fake_db)


# ── Model tests (Task 3.1) ─────────────────────────────────────────────────────

class TestDataModels:
    def test_session_document_defaults(self):
        session = SessionDocument(user_id="user1", mode=OperatingMode.SIGHT)
        assert session.status == SessionStatus.ACTIVE
        assert session.depth_dial == DepthDial.SCHOLAR
        assert session.language == "en"
        assert session.locations == []
        assert session.interactions == []
        assert session.content_references == []
        assert session.branch_structure == []
        assert session.content_count.narration_segments == 0

    def test_session_document_round_trip(self):
        """SessionDocument must survive a serialise → deserialise round-trip."""
        session = SessionDocument(
            user_id="u1",
            mode=OperatingMode.VOICE,
            depth_dial=DepthDial.EXPERT,
            language="fr",
        )
        raw = session.to_firestore_dict()
        restored = SessionDocument.from_firestore_dict(raw)
        assert restored.session_id == session.session_id
        assert restored.user_id == session.user_id
        assert restored.mode == session.mode
        assert restored.depth_dial == session.depth_dial

    def test_branch_node_depth_constraint(self):
        """BranchNode depth must be clamped to 0-3 (Requirement 13.4)."""
        with pytest.raises(Exception):
            BranchNode(topic="too deep", depth=4)

    def test_content_ref_metadata_sources(self):
        meta = ContentRefMetadata(
            depth_level=DepthDial.SCHOLAR,
            language="en",
            sources=["https://example.com/source1"],
        )
        assert len(meta.sources) == 1

    def test_location_visit_has_timestamp(self):
        before = int(time.time() * 1000)
        visit = LocationVisit(
            place_id="place/abc",
            name="Eiffel Tower",
            coordinates=GeoPoint(latitude=48.8584, longitude=2.2945),
        )
        after = int(time.time() * 1000)
        # visit_time_ms should be within the test window
        assert before <= visit.visit_time_ms <= after

    def test_user_interaction_auto_id(self):
        i1 = UserInteraction(
            interaction_type=InteractionType.VOICE_INPUT,
            input="Tell me about Rome",
            response="Rome is...",
        )
        i2 = UserInteraction(
            interaction_type=InteractionType.VOICE_INPUT,
            input="Tell me about Paris",
            response="Paris is...",
        )
        assert i1.interaction_id != i2.interaction_id


# ── Manager CRUD tests (Task 3.2) ─────────────────────────────────────────────

class TestSessionMemoryManagerCRUD:
    @pytest.mark.asyncio
    async def test_create_session(self, manager, fake_db):
        session = await manager.create_session(
            user_id="user1",
            mode=OperatingMode.SIGHT,
        )
        assert session.user_id == "user1"
        assert session.mode == OperatingMode.SIGHT
        assert session.status == SessionStatus.ACTIVE
        # Document persisted to fake store
        assert session.session_id in fake_db._store

    @pytest.mark.asyncio
    async def test_load_session(self, manager):
        created = await manager.create_session("u1", OperatingMode.VOICE)
        loaded = await manager.load_session(created.session_id)
        assert loaded.session_id == created.session_id
        assert loaded.user_id == created.user_id

    @pytest.mark.asyncio
    async def test_load_nonexistent_session_raises(self, manager):
        with pytest.raises(SessionNotFoundError):
            await manager.load_session("nonexistent-id")

    @pytest.mark.asyncio
    async def test_update_session_mode(self, manager):
        session = await manager.create_session("u1", OperatingMode.SIGHT)
        await manager.update_session(session.session_id, mode=OperatingMode.LORE)
        updated = await manager.load_session(session.session_id)
        assert updated.mode == OperatingMode.LORE

    @pytest.mark.asyncio
    async def test_complete_session_sets_status_and_end_time(self, manager):
        session = await manager.create_session("u1", OperatingMode.VOICE)
        await manager.complete_session(session.session_id)
        updated = await manager.load_session(session.session_id)
        assert updated.status == SessionStatus.COMPLETED
        assert updated.end_time_ms is not None
        assert updated.total_duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_delete_session(self, manager, fake_db):
        session = await manager.create_session("u1", OperatingMode.SIGHT)
        await manager.delete_session(session.session_id)
        assert session.session_id not in fake_db._store

    @pytest.mark.asyncio
    async def test_delete_nonexistent_session_is_idempotent(self, manager):
        # Deleting a non-existent document should not raise
        await manager.delete_session("no-such-id")


# ── Append operation tests ─────────────────────────────────────────────────────

class TestAppendOperations:
    @pytest.mark.asyncio
    async def test_add_location_visit(self, manager):
        session = await manager.create_session("u1", OperatingMode.SIGHT)
        visit = LocationVisit(
            place_id="ChIJN1t_tDeuEmsRUsoyG83frY4",
            name="Sydney Opera House",
            coordinates=GeoPoint(latitude=-33.8568, longitude=151.2153),
        )
        await manager.add_location_visit(session.session_id, visit)
        updated = await manager.load_session(session.session_id)
        assert len(updated.locations) == 1
        assert updated.locations[0].name == "Sydney Opera House"

    @pytest.mark.asyncio
    async def test_add_interaction(self, manager):
        session = await manager.create_session("u1", OperatingMode.VOICE)
        interaction = UserInteraction(
            interaction_type=InteractionType.VOICE_INPUT,
            input="Tell me about the Colosseum",
            response="The Colosseum is an ancient amphitheatre...",
            processing_time_ms=350.0,
        )
        await manager.add_interaction(session.session_id, interaction)
        updated = await manager.load_session(session.session_id)
        assert len(updated.interactions) == 1
        assert updated.interactions[0].input == "Tell me about the Colosseum"

    @pytest.mark.asyncio
    async def test_add_content_reference_increments_counter(self, manager):
        session = await manager.create_session("u1", OperatingMode.LORE)
        ref = ContentRef(
            content_type=ContentType.NARRATION,
            storage_url="gs://lore-media/user1/session1/narr_001.mp3",
            metadata=ContentRefMetadata(
                depth_level=DepthDial.SCHOLAR, language="en"
            ),
        )
        await manager.add_content_reference(session.session_id, ref)
        updated = await manager.load_session(session.session_id)
        assert len(updated.content_references) == 1
        assert updated.content_count.narration_segments == 1

    @pytest.mark.asyncio
    async def test_add_branch_node(self, manager):
        session = await manager.create_session("u1", OperatingMode.VOICE)
        branch = BranchNode(topic="Roman Architecture", depth=1)
        await manager.add_branch_node(session.session_id, branch)
        updated = await manager.load_session(session.session_id)
        assert len(updated.branch_structure) == 1
        assert updated.branch_structure[0].topic == "Roman Architecture"


# ── Cross-session query tests (Task 3.2) ──────────────────────────────────────

class TestCrossSessionQuery:
    @pytest.mark.asyncio
    async def test_query_finds_interaction(self, manager):
        session = await manager.create_session("u1", OperatingMode.VOICE)
        # Manually inject interaction into fake_db to avoid patching here
        doc = manager._db._store[session.session_id]
        doc["interactions"] = [
            {
                "interaction_id": str(uuid.uuid4()),
                "timestamp_ms": int(time.time() * 1000),
                "interaction_type": "voice_input",
                "input": "Tell me about Rome last week",
                "response": "Rome was founded in 753 BC...",
                "processing_time_ms": 200.0,
            }
        ]
        results = await manager.query_across_sessions("u1", "Rome")
        assert len(results) >= 1
        assert results[0].match_type == "interaction"

    @pytest.mark.asyncio
    async def test_query_finds_location(self, manager):
        session = await manager.create_session("u1", OperatingMode.SIGHT)
        doc = manager._db._store[session.session_id]
        doc["locations"] = [
            {
                "place_id": "place/rome",
                "name": "Colosseum, Rome",
                "coordinates": {"latitude": 41.89, "longitude": 12.49},
                "visit_time_ms": int(time.time() * 1000),
                "duration_seconds": 120.0,
                "triggered_content_ids": [],
            }
        ]
        results = await manager.query_across_sessions("u1", "Rome")
        location_results = [r for r in results if r.match_type == "location"]
        assert len(location_results) >= 1

    @pytest.mark.asyncio
    async def test_query_returns_empty_for_no_match(self, manager):
        await manager.create_session("u1", OperatingMode.VOICE)
        results = await manager.query_across_sessions("u1", "xyzzy_no_match")
        assert results == []

    @pytest.mark.asyncio
    async def test_delete_all_user_data_removes_all_sessions(self, manager, fake_db):
        for _ in range(3):
            await manager.create_session("u_del", OperatingMode.VOICE)
        # Another user's sessions should not be deleted
        other_session = await manager.create_session("other_user", OperatingMode.SIGHT)

        await manager.delete_all_user_data("u_del")

        # Verify u_del sessions are gone
        remaining_u_del = [
            v for v in fake_db._store.values() if v.get("user_id") == "u_del"
        ]
        assert remaining_u_del == []

        # Other user's data intact
        assert other_session.session_id in fake_db._store
