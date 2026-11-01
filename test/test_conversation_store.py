from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from services.conversation_store import ConversationStore


class ConversationHistoryTests(unittest.TestCase):
    def setUp(self):
        artifacts = Path(__file__).resolve().parents[1] / "artifacts" / "frontend"
        artifacts.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=artifacts, prefix="conversation-test-")
        self.addCleanup(self.directory.cleanup)
        self.sync = mock.patch("services.conversation_store._sync_unified_chat_task")
        self.sync.start()
        self.addCleanup(self.sync.stop)
        self.store = ConversationStore(Path(self.directory.name) / "conversations.json")
        self.identity = {"id": "fixture-owner", "role": "user"}
        self.id = self.store.create(self.identity, title="Fixture")["id"]

    def request(self, messages):
        payload = {"conversation_id": self.id, "messages": copy.deepcopy(messages), "model": "auto"}
        before = copy.deepcopy(payload)
        result = self.store.ensure_from_chat_payload(self.identity, payload)
        self.assertEqual(payload, before, "Upstream must still receive the complete context")
        return result[1]

    def messages(self):
        return self.store.get(self.identity, self.id)["messages"]

    def record(self, text):
        self.store.record_chat_result(self.identity, self.id, {"choices": [{"message": {"content": text}}]})

    def test_full_context_appends_only_new_turn(self):
        self.request([{"role": "user", "content": "first"}])
        self.record("answer")
        previous_ids = [item["id"] for item in self.messages()]
        context = [{"role": item["role"], "content": item["content"]} for item in self.messages()]
        added = self.request(context + [{"role": "user", "content": "second"}])
        self.record("second answer")
        self.assertEqual([item["content"] for item in self.messages()], ["first", "answer", "second", "second answer"])
        self.assertEqual([item["content"] for item in added], ["second"])
        self.assertEqual([item["id"] for item in self.messages()[:2]], previous_ids)
        self.assertEqual(len(ConversationStore(self.store.path).get(self.identity, self.id)["messages"]), 4)

    def test_truncated_context_matches_saved_suffix(self):
        self.store.append_messages(self.identity, self.id, [{"role": "system", "content": "policy"}, {"role": "user", "content": "first"}, {"role": "assistant", "content": "answer"}])
        added = self.request([{"role": "user", "content": "first"}, {"role": "assistant", "content": "answer"}, {"role": "user", "content": "next"}])
        self.assertEqual([item["content"] for item in added], ["next"])
        self.assertEqual(len(self.messages()), 4)

    def test_failed_diagnostic_remains_but_does_not_break_matching(self):
        self.request([{"role": "user", "content": "first"}])
        self.store.record_chat_result(self.identity, self.id, {}, error="fixture upstream failure")
        self.request([{"role": "user", "content": "first"}, {"role": "user", "content": "retry"}])
        self.assertEqual(len(self.messages()), 3)
        self.assertEqual(self.messages()[1]["metadata"]["status"], "failed")
        self.assertEqual(self.messages()[-1]["content"], "retry")

    def test_intentional_repeated_user_text_is_not_removed(self):
        self.request([{"role": "user", "content": "repeat"}])
        self.record("answer")
        self.request([{"role": "user", "content": "repeat"}, {"role": "assistant", "content": "answer"}, {"role": "user", "content": "repeat"}])
        self.assertEqual([item["content"] for item in self.messages()], ["repeat", "answer", "repeat"])

    def test_incremental_clients_keep_single_message_semantics(self):
        self.request([{"role": "user", "content": "repeat"}])
        self.request([{"role": "user", "content": "repeat"}])
        self.assertEqual(len(self.messages()), 2)

    def test_context_replay_does_not_rewrite_existing_messages(self):
        context = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "answer"}]
        self.store.append_messages(self.identity, self.id, context)
        original = self.messages()
        self.assertEqual(self.request(context), [])
        self.assertEqual(self.messages(), original)

    def test_role_is_part_of_context_identity(self):
        self.store.append_messages(self.identity, self.id, [{"role": "assistant", "content": "same"}])
        self.request([{"role": "system", "content": "same"}, {"role": "user", "content": "next"}])
        self.assertEqual(len(self.messages()), 3)


if __name__ == "__main__":
    unittest.main()
