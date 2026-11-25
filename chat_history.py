"""In-Memory Thread-Safe Chat History Manager (Task 3).

CRITICAL NOTICE:
=============================================================================
EPHEMERAL IN-MEMORY STORAGE:
All chat histories and messages in this module are maintained strictly in RAM.
All chat history is COMPLETELY LOST ON RESTART.
No database is used for storing chat sessions or messages.
This storage is placed behind ChatHistoryStorageInterface so a persistent
database can seamlessly replace it in the future without changing caller code.
=============================================================================
"""
import abc
import copy
import logging
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("upian.chat_history")


class ChatHistoryStorageInterface(abc.ABC):
    """
    Abstract storage interface for chat session and message management.
    Decouples storage backend (in-memory, Redis, PostgreSQL, etc.) from API routes.
    """
    @abc.abstractmethod
    def create_chat(self, user_id: str, title: str = "", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Creates a new chat session for a user, evicting the oldest if limit is exceeded."""
        pass

    @abc.abstractmethod
    def get_chat(self, user_id: str, chat_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific chat session by user ID and chat ID."""
        pass

    @abc.abstractmethod
    def list_chats(self, user_id: str) -> List[Dict[str, Any]]:
        """Lists all chat summaries for a user in most-recently-updated order."""
        pass

    @abc.abstractmethod
    def append_message(
        self,
        user_id: str,
        chat_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Appends a message to a chat, enforcing message count and byte size caps."""
        pass

    @abc.abstractmethod
    def delete_chat(self, user_id: str, chat_id: str) -> bool:
        """Deletes a chat session for a user."""
        pass

    @abc.abstractmethod
    def clear_user_chats(self, user_id: str) -> int:
        """Clears all chats for a given user."""
        pass


class InMemoryChatHistory(ChatHistoryStorageInterface):
    """
    Thread-safe, in-memory implementation of ChatHistoryStorageInterface.
    
    Guarantees:
    - Up to 20 chats per user (configurable); evicts oldest beyond that.
    - Strict thread safety via re-entrant lock (RLock).
    - Capped message counts (default 100) and byte sizes (default 500 KB) per chat.
    - Explicit ephemeral notice: lost on process restart.
    """
    PERSISTENCE_NOTICE = (
        "IN-MEMORY EPHEMERAL STORAGE: Chat history is stored strictly in memory "
        "and is permanently lost on application restart."
    )

    def __init__(
        self,
        max_chats_per_user: int = 20,
        max_messages_per_chat: int = 100,
        max_chat_bytes: int = 512 * 1024,  # 500 KB cap per chat
        max_message_chars: int = 25000,
    ):
        self.max_chats_per_user = max_chats_per_user
        self.max_messages_per_chat = max_messages_per_chat
        self.max_chat_bytes = max_chat_bytes
        self.max_message_chars = max_message_chars

        # Storage map: user_id -> OrderedDict of {chat_id: chat_data}
        self._user_stores: Dict[str, OrderedDict] = {}
        self._lock = threading.RLock()

    @property
    def persistence_info(self) -> Dict[str, Any]:
        return {
            "type": "in_memory",
            "ephemeral": True,
            "lost_on_restart": True,
            "max_chats_per_user": self.max_chats_per_user,
            "max_messages_per_chat": self.max_messages_per_chat,
            "notice": self.PERSISTENCE_NOTICE,
        }

    def _get_user_store(self, user_id: str) -> OrderedDict:
        uid = str(user_id)
        if uid not in self._user_stores:
            self._user_stores[uid] = OrderedDict()
        return self._user_stores[uid]

    def create_chat(
        self,
        user_id: str,
        title: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            store = self._get_user_store(user_id)
            chat_id = "chat_" + uuid.uuid4().hex[:16]
            now = datetime.now(timezone.utc).isoformat() + "Z"

            # Check capacity and evict oldest if at max_chats_per_user
            while len(store) >= self.max_chats_per_user:
                oldest_id, oldest_chat = store.popitem(last=False)
                logger.info("Evicted oldest in-memory chat %s for user %s (capacity cap: %d)",
                            oldest_id, user_id, self.max_chats_per_user)

            chat_obj = {
                "id": chat_id,
                "user_id": str(user_id),
                "title": title.strip() or "New Conversation",
                "created_at": now,
                "updated_at": now,
                "messages": [],
                "summary": None,
                "total_bytes": 0,
                "metadata": metadata or {},
            }
            store[chat_id] = chat_obj
            return copy.deepcopy(chat_obj)

    def get_chat(self, user_id: str, chat_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            store = self._get_user_store(user_id)
            chat = store.get(chat_id)
            if not chat:
                return None
            return copy.deepcopy(chat)

    def list_chats(self, user_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            store = self._get_user_store(user_id)
            # Return summaries ordered by updated_at descending
            results = []
            for c in reversed(store.values()):
                results.append({
                    "id": c["id"],
                    "user_id": c["user_id"],
                    "title": c["title"],
                    "created_at": c["created_at"],
                    "updated_at": c["updated_at"],
                    "message_count": len(c["messages"]),
                    "has_summary": bool(c.get("summary")),
                    "total_bytes": c.get("total_bytes", 0),
                })
            return results

    def append_message(
        self,
        user_id: str,
        chat_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            store = self._get_user_store(user_id)
            if chat_id not in store:
                # Auto-create chat if it does not exist
                self.create_chat(user_id=user_id, title=content[:40])

            chat = store[chat_id]
            # Move to end (most recently active)
            store.move_to_end(chat_id)

            # Sanitize and cap content
            clean_content = str(content or "")[: self.max_message_chars]
            now = datetime.now(timezone.utc).isoformat() + "Z"

            msg_obj = {
                "id": "msg_" + uuid.uuid4().hex[:12],
                "role": role,
                "content": clean_content,
                "created_at": now,
                "metadata": metadata or {},
            }

            msg_bytes = len(clean_content.encode("utf-8"))
            chat["messages"].append(msg_obj)
            chat["total_bytes"] = chat.get("total_bytes", 0) + msg_bytes
            chat["updated_at"] = now

            # Enforce message count cap per chat
            while len(chat["messages"]) > self.max_messages_per_chat:
                popped = chat["messages"].pop(0)
                popped_bytes = len(popped["content"].encode("utf-8"))
                chat["total_bytes"] = max(0, chat["total_bytes"] - popped_bytes)

            # Enforce total byte size cap per chat
            while chat["total_bytes"] > self.max_chat_bytes and len(chat["messages"]) > 1:
                popped = chat["messages"].pop(0)
                popped_bytes = len(popped["content"].encode("utf-8"))
                chat["total_bytes"] = max(0, chat["total_bytes"] - popped_bytes)

            # Auto-title from first user message if default
            if chat["title"] in ("New Conversation", "") and role == "user":
                chat["title"] = clean_content[:45].strip()

            return copy.deepcopy(msg_obj)

    def set_chat_summary(self, user_id: str, chat_id: str, summary: str) -> bool:
        with self._lock:
            store = self._get_user_store(user_id)
            if chat_id not in store:
                return False
            store[chat_id]["summary"] = summary
            store[chat_id]["updated_at"] = datetime.now(timezone.utc).isoformat() + "Z"
            return True

    def delete_chat(self, user_id: str, chat_id: str) -> bool:
        with self._lock:
            store = self._get_user_store(user_id)
            if chat_id in store:
                del store[chat_id]
                return True
            return False

    def clear_user_chats(self, user_id: str) -> int:
        with self._lock:
            store = self._get_user_store(user_id)
            count = len(store)
            store.clear()
            return count


# Global singleton instance for in-memory chat history
chat_history_store = InMemoryChatHistory()
