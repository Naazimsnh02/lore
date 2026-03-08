"""Unit tests for the Branch Documentary Manager.

Tests cover Task 23.1 requirements:
  - Branch creation with depth tracking (Req 13.2)
  - Branch stack management (Req 13.3)
  - Return to parent functionality (Req 13.5)
  - Maximum depth limit enforcement (Req 13.4)
  - Session Memory persistence (Req 13.6)
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.services.branch_documentary.manager import BranchDocumentaryManager
from backend.services.branch_documentary.models import (
    MAX_BRANCH_DEPTH,
    BranchDepthExceeded,
    BranchDocumentary,
    BranchDocumentaryContext,
    BranchStackEntry,
    NoBranchToReturn,
)


class TestBranchDocumentaryManagerInit(unittest.TestCase):
    """Test manager initialisation and properties."""

    def test_init_defaults(self):
        mgr = BranchDocumentaryManager()
        self.assertEqual(mgr.current_depth, 0)
        self.assertTrue(mgr.is_at_root)
        self.assertIsNone(mgr.current_branch_id)
        self.assertIsNone(mgr.current_topic)
        self.assertEqual(mgr.max_depth, MAX_BRANCH_DEPTH)

    def test_init_custom_max_depth(self):
        mgr = BranchDocumentaryManager(max_depth=5)
        self.assertEqual(mgr.max_depth, 5)

    def test_set_root_context(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Ancient Rome", mode="voice", language="en")
        self.assertEqual(mgr.current_topic, "Ancient Rome")
        self.assertTrue(mgr.is_at_root)


class TestBranchCreation(unittest.IsolatedAsyncioTestCase):
    """Test branch creation workflow (Req 13.2)."""

    async def test_create_branch_basic(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")
        result = await mgr.create_branch("Colosseum")

        self.assertIsInstance(result, BranchDocumentary)
        self.assertEqual(result.context.topic, "Colosseum")
        self.assertEqual(result.context.depth, 1)
        self.assertIsNone(result.context.parent_branch_id)
        self.assertEqual(mgr.current_depth, 1)
        self.assertFalse(mgr.is_at_root)

    async def test_create_nested_branches(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")

        b1 = await mgr.create_branch("Colosseum")
        self.assertEqual(mgr.current_depth, 1)

        b2 = await mgr.create_branch("Gladiators")
        self.assertEqual(mgr.current_depth, 2)
        self.assertEqual(b2.context.parent_branch_id, b1.branch_id)
        self.assertEqual(b2.context.depth, 2)

        b3 = await mgr.create_branch("Spartacus")
        self.assertEqual(mgr.current_depth, 3)
        self.assertEqual(b3.context.parent_branch_id, b2.branch_id)

    async def test_create_branch_inherits_settings(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(
            topic="Rome", mode="lore", language="it", depth_dial="scholar"
        )

        result = await mgr.create_branch("Colosseum")
        self.assertEqual(result.context.language, "it")
        self.assertEqual(result.context.depth_dial, "scholar")
        self.assertEqual(result.context.mode, "lore")

    async def test_create_branch_custom_overrides(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome", language="en")

        result = await mgr.create_branch("Colosseum", language="it")
        self.assertEqual(result.context.language, "it")

    async def test_create_branch_records_stream_position(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")

        await mgr.create_branch("Colosseum", stream_position=45.5)
        stack = mgr.get_branch_stack()
        self.assertAlmostEqual(stack[0].stream_position, 45.5)

    async def test_create_branch_previous_topics_chain(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")

        b1 = await mgr.create_branch("Colosseum")
        self.assertEqual(b1.context.previous_topics, ["Rome"])

        b2 = await mgr.create_branch("Gladiators")
        self.assertEqual(b2.context.previous_topics, ["Rome", "Colosseum"])

    async def test_create_branch_unique_ids(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")

        b1 = await mgr.create_branch("Colosseum")
        await mgr.return_to_parent()
        b2 = await mgr.create_branch("Forum")

        self.assertNotEqual(b1.branch_id, b2.branch_id)


class TestBranchDepthLimit(unittest.IsolatedAsyncioTestCase):
    """Test branch depth enforcement (Req 13.4)."""

    async def test_depth_limit_at_max(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")

        await mgr.create_branch("Level 1")
        await mgr.create_branch("Level 2")
        await mgr.create_branch("Level 3")

        with self.assertRaises(BranchDepthExceeded) as ctx:
            await mgr.create_branch("Level 4")

        self.assertIn("3", str(ctx.exception))
        self.assertEqual(mgr.current_depth, 3)  # Unchanged

    async def test_depth_limit_custom(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1", max_depth=1)
        mgr.set_root_context(topic="Rome")

        await mgr.create_branch("Level 1")

        with self.assertRaises(BranchDepthExceeded):
            await mgr.create_branch("Level 2")

    async def test_can_branch_reflects_capacity(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1", max_depth=2)
        mgr.set_root_context(topic="Rome")

        self.assertTrue(mgr.can_branch())
        await mgr.create_branch("Level 1")
        self.assertTrue(mgr.can_branch())
        await mgr.create_branch("Level 2")
        self.assertFalse(mgr.can_branch())


class TestReturnToParent(unittest.IsolatedAsyncioTestCase):
    """Test return to parent navigation (Req 13.5)."""

    async def test_return_from_branch(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")

        await mgr.create_branch("Colosseum", stream_position=10.0)
        popped = await mgr.return_to_parent()

        self.assertEqual(popped.topic, "Colosseum")
        self.assertAlmostEqual(popped.stream_position, 10.0)
        self.assertTrue(mgr.is_at_root)
        self.assertEqual(mgr.current_depth, 0)

    async def test_return_from_nested_branches(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")

        await mgr.create_branch("Colosseum")
        await mgr.create_branch("Gladiators")
        await mgr.create_branch("Spartacus")

        popped3 = await mgr.return_to_parent()
        self.assertEqual(popped3.topic, "Spartacus")
        self.assertEqual(mgr.current_depth, 2)

        popped2 = await mgr.return_to_parent()
        self.assertEqual(popped2.topic, "Gladiators")
        self.assertEqual(mgr.current_depth, 1)

        popped1 = await mgr.return_to_parent()
        self.assertEqual(popped1.topic, "Colosseum")
        self.assertTrue(mgr.is_at_root)

    async def test_return_at_root_raises(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")

        with self.assertRaises(NoBranchToReturn):
            await mgr.return_to_parent()

    async def test_return_and_rebranch(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")

        await mgr.create_branch("Colosseum")
        await mgr.return_to_parent()

        # Should be able to branch again after returning
        b2 = await mgr.create_branch("Forum")
        self.assertEqual(mgr.current_depth, 1)
        self.assertEqual(b2.context.topic, "Forum")


class TestBranchPath(unittest.IsolatedAsyncioTestCase):
    """Test branch path and navigation helpers."""

    async def test_path_at_root(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")
        self.assertEqual(mgr.get_branch_path(), ["Rome"])

    async def test_path_nested(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")

        await mgr.create_branch("Colosseum")
        await mgr.create_branch("Gladiators")

        self.assertEqual(mgr.get_branch_path(), ["Rome", "Colosseum", "Gladiators"])

    async def test_path_no_root(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        self.assertEqual(mgr.get_branch_path(), [])

    async def test_get_parent_topic_at_depth_1(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")
        await mgr.create_branch("Colosseum")
        self.assertEqual(mgr.get_parent_topic(), "Rome")

    async def test_get_parent_topic_at_depth_2(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")
        await mgr.create_branch("Colosseum")
        await mgr.create_branch("Gladiators")
        self.assertEqual(mgr.get_parent_topic(), "Colosseum")


class TestSessionMemoryPersistence(unittest.IsolatedAsyncioTestCase):
    """Test Session Memory integration (Req 13.6)."""

    async def test_branch_persisted_on_create(self):
        mock_memory = AsyncMock()
        mgr = BranchDocumentaryManager(
            session_id="s1", user_id="u1", session_memory=mock_memory
        )
        mgr.set_root_context(topic="Rome")

        await mgr.create_branch("Colosseum")

        mock_memory.add_branch_node.assert_called_once()
        call_args = mock_memory.add_branch_node.call_args
        self.assertEqual(call_args[0][0], "s1")  # session_id
        node = call_args[0][1]
        self.assertEqual(node.topic, "Colosseum")
        self.assertEqual(node.depth, 1)

    async def test_branch_closed_on_return(self):
        mock_memory = AsyncMock()
        # Mock load_session to return a session with the branch
        from backend.services.session_memory.models import (
            BranchNode,
            SessionDocument,
        )

        branch_id = None

        async def mock_add(sid, node):
            nonlocal branch_id
            branch_id = node.branch_id

        mock_memory.add_branch_node = mock_add

        mock_session = SessionDocument(
            session_id="s1", user_id="u1", mode="voice"
        )
        mock_memory.load_session = AsyncMock(return_value=mock_session)
        mock_memory.update_session = AsyncMock()

        mgr = BranchDocumentaryManager(
            session_id="s1", user_id="u1", session_memory=mock_memory
        )
        mgr.set_root_context(topic="Rome")

        await mgr.create_branch("Colosseum")
        # Add the branch to mock session for the close call
        mock_session.branch_structure.append(
            BranchNode(
                branch_id=mgr.get_branch_stack()[0].branch_id,
                topic="Colosseum",
                depth=1,
            )
        )

        await mgr.return_to_parent()
        mock_memory.update_session.assert_called_once()

    async def test_memory_failure_graceful_degradation(self):
        mock_memory = AsyncMock()
        mock_memory.add_branch_node = AsyncMock(side_effect=Exception("Firestore down"))

        mgr = BranchDocumentaryManager(
            session_id="s1", user_id="u1", session_memory=mock_memory
        )
        mgr.set_root_context(topic="Rome")

        # Should not raise — graceful degradation
        result = await mgr.create_branch("Colosseum")
        self.assertIsNotNone(result)
        self.assertEqual(mgr.current_depth, 1)

    async def test_no_memory_still_works(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")

        result = await mgr.create_branch("Colosseum")
        self.assertIsNotNone(result)
        self.assertEqual(mgr.current_depth, 1)

        await mgr.return_to_parent()
        self.assertTrue(mgr.is_at_root)


class TestOrchestratorIntegration(unittest.IsolatedAsyncioTestCase):
    """Test Orchestrator content generation integration."""

    async def test_orchestrator_called_on_create(self):
        mock_orchestrator = AsyncMock()
        mock_stream = MagicMock()
        mock_orchestrator.branch_documentary_workflow = AsyncMock(return_value=mock_stream)

        mgr = BranchDocumentaryManager(
            session_id="s1", user_id="u1", orchestrator=mock_orchestrator
        )
        mgr.set_root_context(topic="Rome", mode="voice")

        result = await mgr.create_branch("Colosseum")
        self.assertEqual(result.stream, mock_stream)
        mock_orchestrator.branch_documentary_workflow.assert_called_once()

    async def test_orchestrator_failure_graceful(self):
        mock_orchestrator = AsyncMock()
        mock_orchestrator.branch_documentary_workflow = AsyncMock(
            side_effect=Exception("Generation failed")
        )

        mgr = BranchDocumentaryManager(
            session_id="s1", user_id="u1", orchestrator=mock_orchestrator
        )
        mgr.set_root_context(topic="Rome")

        # Should not raise — stream is None on failure
        result = await mgr.create_branch("Colosseum")
        self.assertIsNone(result.stream)
        self.assertEqual(mgr.current_depth, 1)

    async def test_no_orchestrator_returns_none_stream(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")

        result = await mgr.create_branch("Colosseum")
        self.assertIsNone(result.stream)


class TestReset(unittest.TestCase):
    """Test reset clears state."""

    def test_reset_clears_stack(self):
        mgr = BranchDocumentaryManager(session_id="s1", user_id="u1")
        mgr.set_root_context(topic="Rome")
        # Manually push a stack entry to simulate state
        mgr._branch_stack.append(
            BranchStackEntry(
                branch_id="b1", topic="Colosseum", depth=1
            )
        )

        mgr.reset()
        self.assertTrue(mgr.is_at_root)
        self.assertEqual(mgr.current_depth, 0)
        self.assertIsNone(mgr.current_topic)


if __name__ == "__main__":
    unittest.main()
