from __future__ import annotations
from dataclasses import dataclass
from Crypto.PublicKey import RSA

# Keyring class error မတက်အောင် ယာယီသတ်မှတ်ပေးခြင်း
class Keyring:
    def list_contacts(self): return []
    def list_groups(self): return []

@dataclass
class ChatRef:
    kind: str
    name: str
    chat_id: str

def list_chats(keyring: Keyring) -> list[ChatRef]:
    refs: list[ChatRef] = []
    for c in keyring.list_contacts():
        refs.append(ChatRef(kind="contact", name=c.name, chat_id=c.chat_id))
    for g in keyring.list_groups():
        refs.append(ChatRef(kind="group", name=g.name, chat_id=g.chat_id))
    return refs

class ChatService:
    def __init__(self, me: str, keyring: Keyring, store: any, dns_client: any):
        self._me = me
        self._keyring = keyring
        self._store = store
        self._client = dns_client

    def poll(self, timeout: float = 1.0):
        try:
            messages = self._client.receive(timeout=timeout)
            for msg in messages:
                self._store.add_message(msg)
        except Exception:
            pass
