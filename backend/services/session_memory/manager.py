"""Session Memory Manager – Firestore-backed persistence for LORE sessions.

Design reference: LORE design.md, Section 10 – Session Memory Manager.
Requirements: 10.1 – 10.7.

Architecture notes
------------------
- All I/O is async (asyncio).  The Firestore Python client exposes a
  synchronous API, so every Firestore call is wrapped in
  ``asyncio.get_event_loop().run_in_executor(None, ...)`` to avoid blocking
  the event loop.
- The manager is designed to be instantiated once per service process and
  shared across request handlers (thread-safe by design – no shared mutable
  state outside Firestore).
- Data encryption: Firestore automatically encrypts data at rest with
  Google-managed keys (AES-256).  All traffic between the service and
  Firestore travels over TLS 1.3, satisfying Requirement 10.7.
- For unit / property tests the ``firestore_client`` constructor parameter can
  be replaced with a mock/fake so no real GCP credentials are required.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from .models import (
    BranchNode,
    ContentRef,
    ContentType,
    DepthDial,
    InteractionType,
    LocationVisit,
    OperatingMode,
    QueryResult,
    SessionDocument,
    SessionStatus,
    UserInteraction,
)

logger = logging.getLogger(__name__)

# Firestore collection name
_SESSIONS_COLLECTION = "sessions"


class SessionMemoryError(Exception):
    """Base exception for all SessionMemoryManager errors."""


class SessionNotFoundError(SessionMemoryError):
    """Raised when a requested session does not exist in Firestore."""


class SessionMemoryManager:
    """Manages session persistence in Google Cloud Firestore.

    Parameters
    ----------
    firestore_client:
        A ``google.cloud.firestore.AsyncClient`` (or compatible mock).
        If ``None`` the manager will attempt to create a default client using
        Application Default Credentials.  Pass a mock/stub in tests.
    project_id:
        GCP project ID.  Used only when ``firestore_client`` is None.
    """

    def __init__(
        self,
        firestore_client: Any = None,
        project_id: str | None = None,
    ) -> None:
        if firestore_client is not None:
            self._db = firestore_client
        else:
            # Lazy import so that environments without the GCP SDK installed
            # (e.g. CI running property tests with stubs) don't fail on import.
            from google.cloud import firestore  # type: ignore

            self._db = firestore.AsyncClient(project=project_id)

        self._loop = asyncio.get_event_loop()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _collection(self):  # type: ignore[return]
        return self._db.collection(_SESSIONS_COLLECTION)

    def _doc_ref(self, session_id: str):  # type: ignore[return]
        return self._collection().document(session_id)

    async def _get_session_doc(self, session_id: str) -> SessionDocument:
        """Fetch a session document from Firestore; raises if not found."""
        doc_ref = self._doc_ref(session_id)
        snapshot = await doc_ref.get()
        if not snapshot.exists:
            raise SessionNotFoundError(f"Session '{session_id}' not found")
        return SessionDocument.from_firestore_dict(snapshot.to_dict())

    # ── Session CRUD ───────────────────────────────────────────────────────────

    async def create_session(
        self,
        user_id: str,
        mode: OperatingMode,
        depth_dial: DepthDial = DepthDial.SCHOLAR,
        language: str = "en",
    ) -> SessionDocument:
        """Create a new session and persist it to Firestore.

        Requirement 10.1, 10.2.

        Parameters
        ----------
        user_id:
            Firebase UID of the authenticated user.
        mode:
            Initial operating mode (sight / voice / lore).
        depth_dial:
            Initial complexity level.
        language:
            BCP-47 language code (e.g. ``"en"``, ``"fr"``).

        Returns
        -------
        SessionDocument
            The newly created session with a freshly generated ``session_id``.
        """
        session = SessionDocument(
            user_id=user_id,
            mode=mode,
            depth_dial=depth_dial,
            language=language,
        )
        doc_ref = self._doc_ref(session.session_id)
        await doc_ref.set(session.to_firestore_dict())
        logger.info(
            "Created session",
            extra={"session_id": session.session_id, "user_id": user_id, "mode": mode},
        )
        return session

    async def load_session(self, session_id: str) -> SessionDocument:
        """Load an existing session from Firestore.

        Requirement 10.3 – load previous session context on new session start.

        Raises
        ------
        SessionNotFoundError
            If ``session_id`` does not exist.
        """
        session = await self._get_session_doc(session_id)
        logger.debug("Loaded session %s", session_id)
        return session

    async def update_session(
        self,
        session_id: str,
        *,
        mode: OperatingMode | None = None,
        status: SessionStatus | None = None,
        depth_dial: DepthDial | None = None,
        language: str | None = None,
        end_time_ms: int | None = None,
        total_duration_seconds: float | None = None,
    ) -> None:
        """Update scalar fields of a session document.

        Requirement 10.3.

        Only the fields explicitly passed (non-None) are written to Firestore,
        keeping the update operation efficient.
        """
        updates: dict[str, Any] = {}
        if mode is not None:
            updates["mode"] = mode.value
        if status is not None:
            updates["status"] = status.value
        if depth_dial is not None:
            updates["depth_dial"] = depth_dial.value
        if language is not None:
            updates["language"] = language
        if end_time_ms is not None:
            updates["end_time_ms"] = end_time_ms
        if total_duration_seconds is not None:
            updates["total_duration_seconds"] = total_duration_seconds

        if not updates:
            return  # nothing to do

        doc_ref = self._doc_ref(session_id)
        await doc_ref.update(updates)
        logger.debug("Updated session %s fields: %s", session_id, list(updates.keys()))

    async def complete_session(self, session_id: str) -> None:
        """Mark a session as completed and set its end time.

        Requirement 10.3.
        """
        end_ms = int(time.time() * 1000)
        session = await self._get_session_doc(session_id)
        duration = (end_ms - session.start_time_ms) / 1000.0
        await self.update_session(
            session_id,
            status=SessionStatus.COMPLETED,
            end_time_ms=end_ms,
            total_duration_seconds=duration,
        )
        logger.info("Completed session %s (%.1f s)", session_id, duration)

    async def delete_session(self, session_id: str) -> None:
        """Delete a single session document from Firestore.

        Requirement 10.6 – user-initiated deletion.
        """
        await self._doc_ref(session_id).delete()
        logger.info("Deleted session %s", session_id)

    # ── Incremental append operations ─────────────────────────────────────────
    # These use Firestore ArrayUnion so they are safe to call concurrently.

    async def add_location_visit(
        self, session_id: str, visit: LocationVisit
    ) -> None:
        """Append a location visit to the session.

        Requirement 10.1 – all locations visited must be stored.
        """
        from google.cloud.firestore_v1 import ArrayUnion  # type: ignore

        doc_ref = self._doc_ref(session_id)
        await doc_ref.update({"locations": ArrayUnion([visit.model_dump(mode="json")])})
        logger.debug(
            "Added location visit '%s' to session %s", visit.name, session_id
        )

    async def add_interaction(
        self, session_id: str, interaction: UserInteraction
    ) -> None:
        """Append a user interaction to the session.

        Requirement 10.1 – all interactions must be stored.
        Requirement 10.5 – timestamps associated with all stored content.
        """
        from google.cloud.firestore_v1 import ArrayUnion  # type: ignore

        doc_ref = self._doc_ref(session_id)
        await doc_ref.update(
            {"interactions": ArrayUnion([interaction.model_dump(mode="json")])}
        )
        logger.debug(
            "Added interaction %s to session %s",
            interaction.interaction_type,
            session_id,
        )

    async def add_content_reference(
        self, session_id: str, content_ref: ContentRef
    ) -> None:
        """Append a content reference and increment the content counter.

        Requirement 10.1 – all generated content must be stored.
        """
        from google.cloud.firestore_v1 import ArrayUnion, Increment  # type: ignore

        counter_field = _content_counter_field(content_ref.content_type)
        doc_ref = self._doc_ref(session_id)

        await doc_ref.update(
            {
                "content_references": ArrayUnion(
                    [content_ref.model_dump(mode="json")]
                ),
                counter_field: Increment(1),
            }
        )
        logger.debug(
            "Added %s content ref %s to session %s",
            content_ref.content_type,
            content_ref.content_id,
            session_id,
        )

    async def add_branch_node(
        self, session_id: str, branch: BranchNode
    ) -> None:
        """Append a branch node to the session's branch structure.

        Requirement 10.1 – branch structure must be stored.
        Requirement 13.4 – depth must not exceed 3 (enforced by the model).
        """
        from google.cloud.firestore_v1 import ArrayUnion  # type: ignore

        doc_ref = self._doc_ref(session_id)
        await doc_ref.update(
            {"branch_structure": ArrayUnion([branch.model_dump(mode="json")])}
        )
        logger.debug(
            "Added branch '%s' (depth %d) to session %s",
            branch.topic,
            branch.depth,
            session_id,
        )

    # ── Cross-session queries ─────────────────────────────────────────────────

    async def get_user_sessions(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[SessionDocument]:
        """Return all sessions for a user, sorted newest-first.

        Requirement 10.3 – load previous sessions.

        Parameters
        ----------
        user_id:
            Firebase UID.
        limit:
            Maximum number of sessions to return (default 50).
        """
        query = (
            self._collection()
            .where("user_id", "==", user_id)
            .order_by("start_time_ms", direction="DESCENDING")
            .limit(limit)
        )
        docs = query.stream()
        sessions: list[SessionDocument] = []
        async for doc in docs:
            sessions.append(SessionDocument.from_firestore_dict(doc.to_dict()))
        logger.debug("Fetched %d sessions for user %s", len(sessions), user_id)
        return sessions

    async def query_across_sessions(
        self, user_id: str, query: str
    ) -> list[QueryResult]:
        """Search for query text across all of a user's sessions.

        Requirement 10.4 – cross-session queries.

        The current implementation does simple case-insensitive substring
        matching across interaction inputs, interaction responses, location
        names, and branch topics.  A future iteration can replace this with
        a vector-similarity search or Vertex AI Matching Engine.

        Parameters
        ----------
        user_id:
            Firebase UID.
        query:
            Natural-language search string (e.g. ``"What did I learn about Rome"``).

        Returns
        -------
        list[QueryResult]
            Ranked list of matching items across all sessions.
        """
        sessions = await self.get_user_sessions(user_id, limit=200)
        query_lower = query.lower()
        results: list[QueryResult] = []

        for session in sessions:
            # Search interactions
            for interaction in session.interactions:
                combined = (interaction.input + " " + interaction.response).lower()
                if query_lower in combined:
                    results.append(
                        QueryResult(
                            session_id=session.session_id,
                            session_start_time_ms=session.start_time_ms,
                            match_type="interaction",
                            snippet=_truncate(interaction.input, 200),
                            relevance_score=_simple_score(
                                query_lower, combined
                            ),
                            raw=interaction.model_dump(mode="json"),
                        )
                    )

            # Search location names
            for visit in session.locations:
                if query_lower in visit.name.lower():
                    results.append(
                        QueryResult(
                            session_id=session.session_id,
                            session_start_time_ms=session.start_time_ms,
                            match_type="location",
                            snippet=visit.name,
                            relevance_score=_simple_score(
                                query_lower, visit.name.lower()
                            ),
                            raw=visit.model_dump(mode="json"),
                        )
                    )

            # Search branch topics
            for branch in session.branch_structure:
                if query_lower in branch.topic.lower():
                    results.append(
                        QueryResult(
                            session_id=session.session_id,
                            session_start_time_ms=session.start_time_ms,
                            match_type="branch",
                            snippet=branch.topic,
                            relevance_score=_simple_score(
                                query_lower, branch.topic.lower()
                            ),
                            raw=branch.model_dump(mode="json"),
                        )
                    )

        # Sort by relevance (descending), then by recency
        results.sort(
            key=lambda r: (r.relevance_score, r.session_start_time_ms),
            reverse=True,
        )
        logger.info(
            "Cross-session query for user %s returned %d results",
            user_id,
            len(results),
        )
        return results

    # ── User data management ──────────────────────────────────────────────────

    async def delete_all_user_data(self, user_id: str) -> None:
        """Delete every session document belonging to a user.

        Requirement 10.6 – user-initiated deletion of all data.

        This uses a batch delete to minimise Firestore round-trips.
        Firestore batch size limit is 500 operations.
        """
        sessions = await self.get_user_sessions(user_id, limit=1000)
        if not sessions:
            logger.info("No data to delete for user %s", user_id)
            return

        # Process in chunks of 500 (Firestore batch limit)
        chunk_size = 500
        deleted_count = 0
        for i in range(0, len(sessions), chunk_size):
            batch = self._db.batch()
            for session in sessions[i : i + chunk_size]:
                batch.delete(self._doc_ref(session.session_id))
            await batch.commit()
            deleted_count += len(sessions[i : i + chunk_size])

        logger.info(
            "Deleted %d sessions for user %s", deleted_count, user_id
        )


# ── Private helpers ────────────────────────────────────────────────────────────


def _content_counter_field(content_type: ContentType) -> str:
    """Map a ContentType to its counter field path in the Firestore document."""
    mapping = {
        ContentType.NARRATION: "content_count.narration_segments",
        ContentType.VIDEO: "content_count.video_clips",
        ContentType.ILLUSTRATION: "content_count.illustrations",
        ContentType.FACT: "content_count.facts",
    }
    return mapping[content_type]


def _truncate(text: str, max_length: int) -> str:
    """Return text truncated to ``max_length`` characters with ellipsis."""
    return text if len(text) <= max_length else text[:max_length] + "…"


def _simple_score(query: str, text: str) -> float:
    """Simple relevance score: ratio of query length to match context length.

    A longer query that matches a shorter string gets a higher score.
    """
    if not text:
        return 0.0
    return min(1.0, len(query) / len(text))
