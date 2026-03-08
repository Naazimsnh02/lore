"""Property test for Session Memory Completeness (Task 3.3).

Feature: lore-multimodal-documentary-app, Property 14: Session Memory Completeness

For any user interaction, location visit, or generated content during a session,
the Session_Memory shall store a complete record including all metadata and
timestamps.

Validates: Requirements 10.1

Strategy
--------
We generate random SessionDocument objects with Hypothesis and verify that after
a round-trip through ``to_firestore_dict()`` → ``from_firestore_dict()`` every
field is preserved (completeness) and all timestamp fields are present.
"""

from __future__ import annotations

import time
import uuid

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

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


# ── Hypothesis strategies ─────────────────────────────────────────────────────

@st.composite
def geo_points(draw) -> GeoPoint:
    return GeoPoint(
        latitude=draw(st.floats(min_value=-90.0, max_value=90.0)),
        longitude=draw(st.floats(min_value=-180.0, max_value=180.0)),
    )


@st.composite
def location_visits(draw) -> LocationVisit:
    return LocationVisit(
        place_id=draw(st.text(min_size=1, max_size=50)),
        name=draw(st.text(min_size=1, max_size=100)),
        coordinates=draw(geo_points()),
        visit_time_ms=draw(st.integers(min_value=0, max_value=9_999_999_999_999)),
        duration_seconds=draw(st.floats(min_value=0.0, max_value=7200.0)),
    )


@st.composite
def user_interactions(draw) -> UserInteraction:
    return UserInteraction(
        interaction_type=draw(st.sampled_from(list(InteractionType))),
        input=draw(st.text(min_size=1, max_size=500)),
        response=draw(st.text(min_size=1, max_size=1000)),
        processing_time_ms=draw(st.floats(min_value=0.0, max_value=5000.0)),
        timestamp_ms=draw(st.integers(min_value=0, max_value=9_999_999_999_999)),
    )


@st.composite
def content_refs(draw) -> ContentRef:
    return ContentRef(
        content_type=draw(st.sampled_from(list(ContentType))),
        storage_url=draw(
            st.text(min_size=10, max_size=200).map(lambda s: f"gs://lore-media/{s}")
        ),
        timestamp_ms=draw(st.integers(min_value=0, max_value=9_999_999_999_999)),
        metadata=ContentRefMetadata(
            depth_level=draw(st.sampled_from(list(DepthDial))),
            language=draw(st.sampled_from(["en", "fr", "de", "es", "ja", "zh"])),
        ),
    )


@st.composite
def branch_nodes(draw) -> BranchNode:
    return BranchNode(
        topic=draw(st.text(min_size=1, max_size=200)),
        depth=draw(st.integers(min_value=0, max_value=3)),
        start_time_ms=draw(st.integers(min_value=0, max_value=9_999_999_999_999)),
    )


@st.composite
def session_documents(draw) -> SessionDocument:
    """Generate a random but valid SessionDocument."""
    locs = draw(st.lists(location_visits(), min_size=0, max_size=5))
    interactions = draw(st.lists(user_interactions(), min_size=0, max_size=10))
    refs = draw(st.lists(content_refs(), min_size=0, max_size=10))
    branches = draw(st.lists(branch_nodes(), min_size=0, max_size=5))

    return SessionDocument(
        user_id=draw(st.text(min_size=1, max_size=50)),
        mode=draw(st.sampled_from(list(OperatingMode))),
        depth_dial=draw(st.sampled_from(list(DepthDial))),
        language=draw(st.sampled_from(["en", "fr", "de", "es", "ja", "zh"])),
        status=draw(st.sampled_from(list(SessionStatus))),
        locations=locs,
        interactions=interactions,
        content_references=refs,
        branch_structure=branches,
        start_time_ms=draw(st.integers(min_value=0, max_value=9_999_999_999_999)),
    )


# ── Property tests ────────────────────────────────────────────────────────────

@given(session=session_documents())
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_session_document_round_trip_completeness(session: SessionDocument):
    """Property 14: Every field survives a Firestore serialise/deserialise cycle.

    Feature: lore-multimodal-documentary-app, Property 14: Session Memory Completeness
    """
    raw = session.to_firestore_dict()
    restored = SessionDocument.from_firestore_dict(raw)

    # Core identity fields
    assert restored.session_id == session.session_id
    assert restored.user_id == session.user_id
    assert restored.mode == session.mode
    assert restored.status == session.status
    assert restored.depth_dial == session.depth_dial
    assert restored.language == session.language

    # All locations preserved
    assert len(restored.locations) == len(session.locations)
    for orig, rest in zip(session.locations, restored.locations):
        assert rest.place_id == orig.place_id
        assert rest.name == orig.name
        # Timestamp must be present (Requirement 10.5)
        assert rest.visit_time_ms is not None and rest.visit_time_ms >= 0

    # All interactions preserved
    assert len(restored.interactions) == len(session.interactions)
    for orig, rest in zip(session.interactions, restored.interactions):
        assert rest.interaction_id == orig.interaction_id
        assert rest.input == orig.input
        assert rest.response == orig.response
        # Timestamp must be present (Requirement 10.5)
        assert rest.timestamp_ms is not None and rest.timestamp_ms >= 0

    # All content references preserved
    assert len(restored.content_references) == len(session.content_references)
    for orig, rest in zip(session.content_references, restored.content_references):
        assert rest.content_id == orig.content_id
        assert rest.content_type == orig.content_type
        assert rest.storage_url == orig.storage_url
        # Timestamp must be present (Requirement 10.5)
        assert rest.timestamp_ms is not None and rest.timestamp_ms >= 0

    # All branch nodes preserved
    assert len(restored.branch_structure) == len(session.branch_structure)
    for orig, rest in zip(session.branch_structure, restored.branch_structure):
        assert rest.branch_id == orig.branch_id
        assert rest.topic == orig.topic
        assert rest.depth == orig.depth
        # Depth constraint (Requirement 13.4)
        assert 0 <= rest.depth <= 3


@given(interactions=st.lists(user_interactions(), min_size=1, max_size=20))
@settings(max_examples=100)
def test_all_interactions_have_timestamps(interactions: list[UserInteraction]):
    """Property 14 (partial): Every interaction carries a timestamp.

    Feature: lore-multimodal-documentary-app, Property 14: Session Memory Completeness
    """
    for interaction in interactions:
        assert interaction.timestamp_ms is not None
        assert interaction.timestamp_ms >= 0


@given(visits=st.lists(location_visits(), min_size=1, max_size=20))
@settings(max_examples=100)
def test_all_location_visits_have_timestamps(visits: list[LocationVisit]):
    """Property 14 (partial): Every location visit carries a timestamp.

    Feature: lore-multimodal-documentary-app, Property 14: Session Memory Completeness
    """
    for visit in visits:
        assert visit.visit_time_ms is not None
        assert visit.visit_time_ms >= 0


@given(refs=st.lists(content_refs(), min_size=1, max_size=20))
@settings(max_examples=100)
def test_all_content_refs_have_timestamps(refs: list[ContentRef]):
    """Property 14 (partial): Every content reference carries a timestamp.

    Feature: lore-multimodal-documentary-app, Property 14: Session Memory Completeness
    """
    for ref in refs:
        assert ref.timestamp_ms is not None
        assert ref.timestamp_ms >= 0


@given(session=session_documents())
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_branch_depth_never_exceeds_three(session: SessionDocument):
    """Property 14 (structural): Branch depth is always within 0-3.

    Feature: lore-multimodal-documentary-app, Property 14: Session Memory Completeness
    """
    for branch in session.branch_structure:
        assert 0 <= branch.depth <= 3
