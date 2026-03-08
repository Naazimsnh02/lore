"""Property-based tests for Branch Documentary depth limit.

Feature: lore-multimodal-documentary-app, Property 16: Branch Documentary Depth Limit.
Validates: Requirement 13.4 — maximum nesting depth of 3.

For any branch documentary creation request, if the current branch depth is 3,
the system shall reject the request and prevent deeper nesting.

Uses Hypothesis to generate random branch creation sequences and verify depth
enforcement across 100+ scenarios.
"""

from __future__ import annotations

import asyncio
import unittest

from hypothesis import given, settings, strategies as st

from backend.services.branch_documentary.manager import BranchDocumentaryManager
from backend.services.branch_documentary.models import (
    MAX_BRANCH_DEPTH,
    BranchDepthExceeded,
)


# ── Strategies ───────────────────────────────────────────────────────────────

# Random topic strings
topic_st = st.text(
    alphabet=st.characters(categories=("L", "N", "Z")),
    min_size=1,
    max_size=50,
).filter(lambda t: t.strip())

# Random branch action sequences: "create" or "return"
action_st = st.sampled_from(["create", "return"])

# Sequence of actions to replay
action_sequence_st = st.lists(
    st.tuples(action_st, topic_st),
    min_size=1,
    max_size=20,
)


# ── Test class ───────────────────────────────────────────────────────────────


class TestBranchDepthLimitProperty(unittest.TestCase):
    """Property 16: Branch Documentary Depth Limit.

    For any branch documentary creation request, if the current branch
    depth is 3, the system shall reject the request and prevent deeper
    nesting.
    """

    @given(topics=st.lists(topic_st, min_size=4, max_size=10))
    @settings(max_examples=120, deadline=5000)
    def test_depth_never_exceeds_max(self, topics: list[str]) -> None:
        """Creating branches beyond max depth always raises BranchDepthExceeded."""

        async def _run():
            mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
            mgr.set_root_context(topic="Root Topic")

            # Fill to max depth
            for i in range(MAX_BRANCH_DEPTH):
                await mgr.create_branch(topics[i])
                self.assertEqual(mgr.current_depth, i + 1)

            # Every additional attempt should raise
            for extra_topic in topics[MAX_BRANCH_DEPTH:]:
                with self.assertRaises(BranchDepthExceeded):
                    await mgr.create_branch(extra_topic)

                # Depth must remain at max
                self.assertEqual(mgr.current_depth, MAX_BRANCH_DEPTH)

        asyncio.run(_run())

    @given(actions=action_sequence_st)
    @settings(max_examples=150, deadline=5000)
    def test_depth_invariant_holds_through_sequence(
        self, actions: list[tuple[str, str]]
    ) -> None:
        """For any sequence of create/return actions, depth is always in [0, max_depth]."""

        async def _run():
            mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
            mgr.set_root_context(topic="Root")

            for action, topic in actions:
                if action == "create":
                    try:
                        await mgr.create_branch(topic)
                    except BranchDepthExceeded:
                        pass  # Expected when at max depth
                else:  # "return"
                    try:
                        await mgr.return_to_parent()
                    except Exception:
                        pass  # NoBranchToReturn at root

                # INVARIANT: depth always in valid range
                self.assertGreaterEqual(mgr.current_depth, 0)
                self.assertLessEqual(mgr.current_depth, MAX_BRANCH_DEPTH)

        asyncio.run(_run())

    @given(max_d=st.integers(min_value=1, max_value=10), extra=st.integers(min_value=1, max_value=5))
    @settings(max_examples=100, deadline=5000)
    def test_custom_depth_limit_enforced(self, max_d: int, extra: int) -> None:
        """Custom max_depth is always enforced regardless of the limit value."""

        async def _run():
            mgr = BranchDocumentaryManager(
                session_id="s1", user_id="u1", max_depth=max_d
            )
            mgr.set_root_context(topic="Root")

            # Fill to custom max depth
            for i in range(max_d):
                await mgr.create_branch(f"Topic {i}")

            self.assertEqual(mgr.current_depth, max_d)
            self.assertFalse(mgr.can_branch())

            # Additional attempts should all fail
            for j in range(extra):
                with self.assertRaises(BranchDepthExceeded):
                    await mgr.create_branch(f"Extra {j}")

                self.assertEqual(mgr.current_depth, max_d)

        asyncio.run(_run())

    @given(
        create_count=st.integers(min_value=0, max_value=MAX_BRANCH_DEPTH),
        return_count=st.integers(min_value=0, max_value=MAX_BRANCH_DEPTH),
    )
    @settings(max_examples=120, deadline=5000)
    def test_return_then_rebranch_respects_depth(
        self, create_count: int, return_count: int
    ) -> None:
        """After returning, re-branching still respects the depth limit."""

        async def _run():
            mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
            mgr.set_root_context(topic="Root")

            # Create up to create_count branches
            for i in range(create_count):
                await mgr.create_branch(f"Branch {i}")

            # Return up to return_count times (capped at current depth)
            returns_done = 0
            for _ in range(return_count):
                if mgr.is_at_root:
                    break
                await mgr.return_to_parent()
                returns_done += 1

            expected_depth = create_count - returns_done
            self.assertEqual(mgr.current_depth, expected_depth)

            # Try to fill remaining capacity
            remaining = MAX_BRANCH_DEPTH - expected_depth
            for i in range(remaining):
                await mgr.create_branch(f"Refill {i}")

            self.assertEqual(mgr.current_depth, MAX_BRANCH_DEPTH)

            # One more should fail
            with self.assertRaises(BranchDepthExceeded):
                await mgr.create_branch("Overflow")

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
