"""Unit & Concurrency Tests for In-Memory Thread-Safe Chat History (Task 3)."""
import concurrent.futures
import threading
import pytest
from chat_history import (
    ChatHistoryStorageInterface,
    InMemoryChatHistory,
    chat_history_store,
)


def test_interface_compliance():
    assert issubclass(InMemoryChatHistory, ChatHistoryStorageInterface)
    store = InMemoryChatHistory()
    assert isinstance(store, ChatHistoryStorageInterface)


def test_ephemeral_notice_and_restart_statement():
    store = InMemoryChatHistory()
    info = store.persistence_info
    assert info["type"] == "in_memory"
    assert info["lost_on_restart"] is True
    assert "lost on application restart" in info["notice"].lower()


def test_chat_creation_and_retrieval():
    store = InMemoryChatHistory()
    user_id = "user_test_1"

    chat = store.create_chat(user_id=user_id, title="Quantum Discussion")
    assert chat["id"].startswith("chat_")
    assert chat["title"] == "Quantum Discussion"
    assert chat["messages"] == []

    retrieved = store.get_chat(user_id=user_id, chat_id=chat["id"])
    assert retrieved is not None
    assert retrieved["id"] == chat["id"]


def test_eviction_at_cap_of_20_chats():
    # Enforce exactly 20 chats
    store = InMemoryChatHistory(max_chats_per_user=20)
    user_id = "user_eviction_test"

    created_chat_ids = []
    # Create 20 chats
    for i in range(1, 21):
        chat = store.create_chat(user_id=user_id, title=f"Chat #{i}")
        created_chat_ids.append(chat["id"])

    # Verify exactly 20 chats exist
    chats = store.list_chats(user_id=user_id)
    assert len(chats) == 20
    # First chat created should be available
    assert store.get_chat(user_id=user_id, chat_id=created_chat_ids[0]) is not None

    # Now create 21st chat
    chat_21 = store.create_chat(user_id=user_id, title="Chat #21")

    # Verify still exactly 20 chats exist
    chats_after = store.list_chats(user_id=user_id)
    assert len(chats_after) == 20

    # Oldest chat (Chat #1) must be evicted
    assert store.get_chat(user_id=user_id, chat_id=created_chat_ids[0]) is None

    # Chat #2 through #21 must still exist
    assert store.get_chat(user_id=user_id, chat_id=created_chat_ids[1]) is not None
    assert store.get_chat(user_id=user_id, chat_id=chat_21["id"]) is not None


def test_message_capping_and_size_limits():
    # Chat with cap of 5 messages
    store = InMemoryChatHistory(max_messages_per_chat=5, max_chat_bytes=1000)
    user_id = "user_msg_cap_test"

    chat = store.create_chat(user_id=user_id, title="Message Cap Test")
    chat_id = chat["id"]

    for i in range(1, 10):
        store.append_message(user_id=user_id, chat_id=chat_id, role="user", content=f"Message {i}")

    retrieved = store.get_chat(user_id=user_id, chat_id=chat_id)
    # Must be capped at 5 messages
    assert len(retrieved["messages"]) == 5
    # Messages should be the latest (5, 6, 7, 8, 9)
    assert retrieved["messages"][0]["content"] == "Message 5"
    assert retrieved["messages"][-1]["content"] == "Message 9"


def test_concurrent_users_and_threads():
    store = InMemoryChatHistory(max_chats_per_user=20)
    num_users = 8
    chats_per_user = 25  # Exceeds 20, testing concurrent evictions
    messages_per_chat = 10

    def user_worker(uid):
        user_key = f"concurrent_user_{uid}"
        for c in range(chats_per_user):
            chat = store.create_chat(user_id=user_key, title=f"Threaded Chat {c}")
            for m in range(messages_per_chat):
                store.append_message(
                    user_id=user_key,
                    chat_id=chat["id"],
                    role="user" if m % 2 == 0 else "assistant",
                    content=f"User {uid} chat {c} message {m}",
                )
            # Occasional read and list
            _ = store.list_chats(user_id=user_key)
        return user_key

    # Run across concurrent thread pool
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(user_worker, i) for i in range(num_users)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == num_users

    # Verify each user has exactly 20 chats (cap maintained safely without corruption)
    for i in range(num_users):
        user_key = f"concurrent_user_{i}"
        chats = store.list_chats(user_id=user_key)
        assert len(chats) == 20
        # Check that individual messages inside the active chats are intact
        sample_chat = store.get_chat(user_id=user_key, chat_id=chats[0]["id"])
        assert sample_chat is not None
        assert len(sample_chat["messages"]) > 0
