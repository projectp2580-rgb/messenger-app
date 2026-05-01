from __future__ import annotations

from dataclasses import dataclass

from crypto import Keyring
from store import Store, contact_chat_id, group_chat_id
from transport import DNSTunnelClient


@dataclass
class ChatRef:
    kind: str
    name: str
    chat_id: str


def list_chats(keyring: Keyring) -> list[ChatRef]:
    refs: list[ChatRef] = []
    for c in keyring.list_contacts():
        refs.append(
            ChatRef(kind="contact", name=c.name, chat_id=contact_chat_id(c.name))
        )
    for g in keyring.list_groups():
        refs.append(
            ChatRef(kind="group", name=g.name, chat_id=group_chat_id(g.name))
        )
    return refs


class ChatService:
    def __init__(
        self,
        me: str,
        keyring: Keyring,
        store: Store,
        dns_client: DNSTunnelClient,
    ):
        self._me = me
        self._keyring = keyring
        self._store = store
        self._client = dns_client

    def poll(self, timeout: float = 1.0) -> list:
        """Poll for new messages. Returns list of new message dicts."""
        try:
            messages = self._client.receive(timeout=timeout)
            for msg in messages:
                self._store.add_message(
                    chat_id=msg.get("chat_id", ""),
                    sender=msg.get("sender", "unknown"),
                    text=msg.get("text", ""),
                    outbound=False,
                    delivered_via=msg.get("via"),
                    reply_to=msg.get("reply_to"),
                    msg_id=msg.get("id"),
                )
            return messages
        except Exception:
            return []

    def send_to_contact(
        self, name: str, text: str, reply_to: str | None = None
    ) -> None:
        chat_id = contact_chat_id(name)
        try:
            contact = self._keyring.get_contact(name)
            self._client.send(
                recipient=contact.remote_user,
                text=text,
                key=contact.key,
                chat_id=chat_id,
                reply_to=reply_to,
            )
        except Exception:
            pass
        self._store.add_message(
            chat_id=chat_id,
            sender=self._me,
            text=text,
            outbound=True,
            reply_to=reply_to,
        )

    def send_to_group(
        self, name: str, text: str, reply_to: str | None = None
    ) -> None:
        chat_id = group_chat_id(name)
        try:
            group = self._keyring.get_group(name)
            for member in group.members:
                self._client.send(
                    recipient=member,
                    text=text,
                    key=group.key,
                    chat_id=chat_id,
                    reply_to=reply_to,
                )
        except Exception:
            pass
        self._store.add_message(
            chat_id=chat_id,
            sender=self._me,
            text=text,
            outbound=True,
            reply_to=reply_to,
        )

    def delete_message(self, msg_id: str) -> None:
        self._store.delete_message(msg_id)

    def edit_message(self, msg_id: str, new_text: str) -> None:
        self._store.edit_message(msg_id, new_text)

    def mark_chat_read(self, chat_id: str) -> None:
        for msg in self._store.history(chat_id):
            if not msg.outbound and msg.status != "read":
                self._store.mark_message_read(msg.id)
