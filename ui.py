"""Streamlit chat UI.

Run with ``streamlit run app.py`` from the ``python-messenger`` folder.

Behaviour:

* Sent messages are right-aligned in light-blue bubbles, received
  messages are left-aligned in light-grey bubbles.
* Each bubble shows the time it was sent, in 12-hour format, as a
  small caption inside the bubble (e.g. ``4:15 PM``).
* The Edit / Delete / Reply controls are hidden by default. They only
  appear when the user opens a per-message context menu, either by
  long-pressing the bubble (mouse hold or finger hold) or by tapping
  the small "⋯" handle next to the bubble.
* All control labels are bilingual English / Burmese.
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
import time

import streamlit as st
from streamlit.components.v1 import html as components_html
from streamlit_autorefresh import st_autorefresh

from Crypto import Random
from chat import ChatService, list_chats
from Crypto.PublicKey import RSA
from broadcasts import BroadcastStore
from feature_requests import (
    STATUS_OPEN,
    STATUS_PLANNED,
    STATUS_REPLIED,
    FeatureRequestStore,
)
from profiles import (
    ALLOWED_MIME,
    MAX_AVATAR_BYTES,
    ProfileStore,
    UserProfile,
    guess_mime,
)
from qrcodes import (
    decode_qr_from_image_bytes,
    make_profile_png,
    make_profile_uri,
    parse_profile_uri,
)
from store import Store, StoredMessage, contact_chat_id, group_chat_id
from transport import DNSTunnelClient, DNSTunnelServer
from tunnel_settings import TunnelPresetStore, TunnelSettingsStore
from version import compute_version

DEFAULT_DOMAIN = os.environ.get("MESSENGER_DOMAIN", "tun.local")
DEFAULT_RESOLVER = os.environ.get("MESSENGER_RESOLVER", "127.0.0.1:5353")
EMBEDDED_SERVER_BIND = ("127.0.0.1", 5353)

# Comma-separated list of usernames that get the Admin Panel. Override
# with the ``MESSENGER_ADMINS`` env var; defaults to "admin" plus the
# project owner ``pyaesoneoo`` so they get the panel out of the box.
ADMIN_USERNAMES = {
    name.strip().lower()
    for name in os.environ.get("MESSENGER_ADMINS", "admin,pyaesoneoo").split(",")
    if name.strip()
}

# Usernames that get the small blue verified badge next to their name in
# the sidebar / chat headers. Comma-separated env override too.
VERIFIED_USERNAMES = {
    name.strip().lower()
    for name in os.environ.get("MESSENGER_VERIFIED", "pyaesoneoo").split(",")
    if name.strip()
}


def _is_verified(username: str | None) -> bool:
    return bool(username) and username.lower() in VERIFIED_USERNAMES


# Twitter-style filled blue checkmark, sized via the ``size`` argument.
def _verified_badge_html(size: int = 16, title: str = "Verified") -> str:
    s = int(size)
    return (
        f'<span title="{html.escape(title)}" '
        f'style="display:inline-flex;vertical-align:middle;'
        f'margin-left:4px;line-height:1;">'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" '
        f'viewBox="0 0 24 24" aria-label="{html.escape(title)}">'
        f'<path fill="#1D9BF0" d="M22.25 12c0-1.43-.88-2.67-2.19-3.34.46-1.39.2-2.9-.81-3.91s-2.52-1.27-3.91-.81c-.66-1.31-1.91-2.19-3.34-2.19s-2.67.88-3.33 2.19c-1.4-.46-2.91-.2-3.92.81s-1.26 2.52-.8 3.91c-1.31.67-2.2 1.91-2.2 3.34s.89 2.67 2.2 3.34c-.46 1.39-.21 2.9.8 3.91s2.52 1.27 3.91.81c.67 1.31 1.91 2.19 3.34 2.19s2.68-.88 3.34-2.19c1.39.46 2.9.2 3.91-.81s1.27-2.52.81-3.91c1.31-.67 2.19-1.91 2.19-3.34z"/>'
        f'<path fill="#FFFFFF" d="M9.64 16.95l-3.59-3.59 1.42-1.41 2.17 2.17 5.66-5.66 1.41 1.41z"/>'
        f'</svg></span>'
    )

# 4-digit secret PIN that gates the Admin Panel. Even an admin
# username must enter this PIN before the panel reveals its contents.
# Override with the ``MESSENGER_ADMIN_PIN`` env var.
ADMIN_PIN = os.environ.get("MESSENGER_ADMIN_PIN", "0000").strip()

POLL_INTERVAL_MS = 1500
SERVER_LOCK = threading.Lock()


# --- bilingual labels ------------------------------------------------------

L = {
    "title": "Data-Free Messenger",
    "subtitle": "DNS tunnel + mesh fallback, end-to-end encrypted.",
    "profile_section": "My profile / ကျွန်ုပ်၏ပရိုဖိုင်",
    "your_username": "Your username / အသုံးပြုသူအမည်",
    "username_help": "The name others address you by. Lowercase, no spaces.",
    "welcome_title": "Welcome / ကြိုဆိုပါတယ်",
    "welcome_blurb": "Pick a username to set up your account. We'll generate your encryption key on this device — no password to remember, nothing sent over the internet.",
    "welcome_field": "Choose a username / username ရွေးပါ",
    "welcome_field_help": "Lowercase letters and numbers, no spaces.",
    "welcome_submit": "Create my account / အကောင့်ပြုလုပ်",
    "welcome_one_time": "You'll only see this screen once. Next time you open the app, it will jump straight into your chats.",
    "welcome_invalid": "Please use lowercase letters, digits, dots, dashes, or underscores only.",
    "logged_in_as": "Signed in as / အကောင့်ဖြင့်ဝင်ထား",
    "account_settings": "Account & settings / အကောင့်နှင့်ဆက်တင်",
    "rename_username": "Change username / username ပြောင်း",
    "rename_help": "Your encryption key stays the same so friends can keep messaging you.",
    "rename_btn": "Save / သိမ်း",
    "logout": "Log out / ထွက်",
    "logout_confirm": "This forgets the account on this device. Your contacts stay saved. Continue?",
    "logout_yes": "Yes, log out / ဟုတ်ကဲ့၊ ထွက်",
    "logout_cancel": "Cancel / ပယ်ဖျက်",
    "logged_out_toast": "Logged out. Your contacts and chats are still here.",
    "my_key": "My encryption key / ကျွန်ုပ်၏သော့",
    "my_key_help": "Share this with friends so they can save it under your username. Anyone holding it can encrypt messages to you.",
    "show_my_key": "Show advanced (raw key) / အဆင့်မြင့်ကြည့်",
    "hide_my_key": "Hide / ဝှက်",
    "copy_hint": "Tap to select, then copy. / ရွေးပြီးကူး",
    "key_locked_caption": "Auto-generated and stored locally; never sent over DNS.",
    "show_my_qr": "Show my QR / QR ပြ",
    "hide_my_qr": "Hide QR / QR ကိုဝှက်",
    "qr_caption": "Friends scan this to add you. Username + key only — no internet involved.",
    "share_link": "Share my profile link / ပရိုဖိုင်လင့်ခ်ဝေမျှ",
    "hide_share_link": "Hide link / လင့်ခ်ကိုဝှက်",
    "share_link_caption": "Send this link to a friend. Tapping it on their device adds you as a contact.",
    "share_link_copied": "Copied! / ကူးပြီး",
    "share_link_copy_btn": "Copy link / လင့်ခ်ကူး",
    "share_link_copy_web_btn": "Copy web link / ဝက်ဘ်လင့်ခ်ကူး",
    "share_link_web_caption": "Web version: opens this app and pre-fills your details in the friend's Add Contact form.",
    "scan_qr": "Add by QR / QR ဖြင့်ထည့်",
    "scan_qr_help": "Take a photo of your friend's QR code. Decoded fully on this device.",
    "scan_qr_use_camera": "Use camera / ကင်မရာသုံး",
    "scan_qr_upload": "Upload image / ပုံတင်",
    "scan_qr_decoding": "Reading QR code...",
    "scan_qr_no_code": "Couldn't read a QR code from that image. Try again with better lighting or a closer shot.",
    "scan_qr_bad_payload": "That QR code isn't a Data-Free Messenger profile.",
    "scan_qr_added": "Added '{name}'. Tap their name to start chatting.",
    "server_settings": "Server settings / ဆာဗာဆက်တင်များ",
    "resolver_label": "DNS resolver host:port",
    "chats": "Chats / စကားပြောများ",
    "contact_book": "Contact book / မိတ်ဆွေစာအုပ်",
    "no_chats": "No contacts yet. Add one below.",
    "new_contact": "Add contact / မိတ်ဆွေထည့်",
    "new_group": "New group / အုပ်စုအသစ်",
    "local_name": "Group name (on this device) / အုပ်စုအမည်",
    "their_username": "Friend's username / မိတ်ဆွေ၏ username",
    "their_key": "Friend's key (base64) / မိတ်ဆွေ၏သော့",
    "their_key_help": "Paste the key your friend shared with you.",
    "members": "Members' usernames (comma-separated) / အဖွဲ့ဝင်များ",
    "key_mode": "Key / သော့",
    "from_passphrase": "From passphrase / စကားဝှက်မှ",
    "import_key": "Import base64 / သော့ထည့်သွင်း",
    "passphrase": "Passphrase / စကားဝှက်",
    "base64_key": "Base64 key",
    "add_contact_btn": "Save to contact book / မိတ်ဆွေထည့်",
    "add_group_btn": "Add group / အုပ်စုထည့်",
    "show_key": "Show their key / သူတို့၏သော့ကိုပြ",
    "select_chat": "Select a contact or group from the sidebar to start chatting.",
    "no_messages": "No messages in this chat yet.",
    "deleted": "(message deleted) / (ဖျက်ပြီး)",
    "edited": "edited / တည်းဖြတ်ထား",
    "reply": "Reply / ပြန်ဖြေ",
    "edit": "Edit / တည်းဖြတ်",
    "delete": "Delete / ဖျက်",
    "save": "Save / သိမ်း",
    "cancel": "Cancel / ပယ်ဖျက်",
    "replying_to": "Replying to / ဆက်ပြန်ဖြေ",
    "input_placeholder": "Write a message / စာပို့ရန်ရေးပါ",
    "set_username_first": "Pick a username in the sidebar to start chatting.",
    "actions_hint": "Hold a message to edit, delete or reply / မက်ဆေ့ချ်ကိုဖိထားပါ",
    "e2e_note": "All messages are end-to-end encrypted before being sent over DNS. The tunnel only sees opaque ciphertext.",
    # Feature requests (user side)
    "feature_request": "Request a new feature / အင်္ဂါရပ်အသစ်တောင်းဆို",
    "feature_request_help": "Tell the admin what you'd like to see in the next update. They'll see this privately.",
    "feature_request_placeholder": "What would make this app better for you? / ဘာတွေထပ်ထည့်ပေးချင်ပါသလဲ",
    "feature_request_submit": "Send to admin / အက်ဒမင်သို့ပို့",
    "feature_request_empty": "Please write your idea before sending. / အကြံဥာဏ်ကိုရေးပြီးမှပို့ပါ",
    "feature_request_sent": "Thanks! Your request was sent to the admin. / ကျေးဇူးတင်ပါတယ်",
    "feature_request_my_history": "My past requests / ကျွန်ုပ်ပို့ထားသောတောင်းဆိုမှု",
    "feature_request_no_history": "You haven't sent any requests yet.",
    # Admin panel
    "admin_panel": "Admin panel / အက်ဒမင်",
    "admin_user_requests": "User requests / အသုံးပြုသူများ၏တောင်းဆိုမှု",
    "admin_no_requests": "No feature requests yet.",
    "admin_open": "Open / မဆောင်ရွက်ရသေး",
    "admin_planned": "Planned / စီစဉ်ထား",
    "admin_replied": "Replied / ဖြေပြီး",
    "admin_filter_all": "All",
    "admin_filter_open": "Open",
    "admin_filter_planned": "Planned",
    "admin_filter_replied": "Replied",
    "admin_mark_planned": "Mark as planned / စီစဉ်ထားအဖြစ်မှတ်",
    "admin_reply_btn": "Reply / ပြန်ဖြေ",
    "admin_reply_placeholder": "Write a short reply to the user / အဖြေတိုတိုရေးပါ",
    "admin_reply_send": "Send reply / အဖြေပို့",
    "admin_reply_cancel": "Cancel / ပယ်ဖျက်",
    "admin_reopen": "Reopen / ပြန်ဖွင့်",
    "admin_request_replied_at": "Replied {when}",
    "admin_request_meta": "From @{user} · {when}",
    "admin_replied_toast": "Reply sent.",
    "admin_planned_toast": "Marked as planned.",
    # Admin PIN gate
    "admin_pin_locked": "Locked / လော့ခ်ချထား",
    "admin_pin_prompt": "Enter the 4-digit admin PIN / အက်ဒမင် PIN ၄လုံးထည့်ပါ",
    "admin_pin_label": "Admin PIN",
    "admin_pin_unlock": "Unlock / ဖွင့်",
    "admin_pin_lock": "Lock panel / ပြန်လော့ခ်ချ",
    "admin_pin_wrong": "Wrong PIN. Try again.",
    "admin_pin_format": "PIN must be 4 digits.",
    "admin_pin_unset": "Admin PIN is not configured. Set MESSENGER_ADMIN_PIN to enable the panel.",
    "admin_pin_help": "PIN is set with the MESSENGER_ADMIN_PIN environment variable.",
    "admin_unlocked_toast": "Admin panel unlocked.",
    "admin_locked_toast": "Admin panel locked.",
    # Broadcasts
    "admin_broadcast": "Broadcast announcement / အသိပေးချက်",
    "admin_broadcast_help": "Type a short announcement and it will appear at the top of every user's screen until they dismiss it.",
    "admin_broadcast_placeholder": "What would you like everyone to know? / အသုံးပြုသူအားလုံးကိုဘာပြောချင်ပါသလဲ",
    "admin_broadcast_send": "Send broadcast / အသိပေးချက်ပို့",
    "admin_broadcast_empty": "Please write a message before sending. / မက်ဆေ့ချ်ထည့်ပြီးမှပို့ပါ",
    "admin_broadcast_sent": "Broadcast sent to all users. / အသုံးပြုသူအားလုံးသို့ပို့ပြီး",
    "admin_broadcast_recent": "Recent broadcasts / လတ်တလောအသိပေးချက်များ",
    "admin_broadcast_no_recent": "No broadcasts sent yet.",
    "admin_broadcast_delete": "Delete / ဖျက်",
    "admin_broadcast_deleted": "Broadcast removed.",
    "admin_broadcast_meta": "By @{user} · {when}",
    "broadcast_banner_title": "Announcement / အသိပေးချက်",
    "broadcast_banner_dismiss": "Got it / နားလည်ပြီ",
    "broadcast_banner_from": "From @{user}",
    # Update watcher
    "update_banner_text": "A new version is available — refresh to see new features. / အသစ်ထွက်ပြီ — အသစ်များကြည့်ရန်ပြန်ဖွင့်ပါ",
    "update_banner_button": "Refresh now / ယခုပြန်ဖွင့်",
    # Admin tunnel / bug-host panel
    "admin_tunnel": "Tunnel / SNI Bug Host",
    "admin_tunnel_help": (
        "Configure the host every user's data routes through. "
        "Update this whenever the carrier blocks the current host so "
        "all users keep data-free access. / "
        "လူတိုင်းအသုံးပြုသော Bug Host ပြောင်းရန်။"
    ),
    "admin_tunnel_bug_host": "Bug host (Host header) / Bug Host",
    "admin_tunnel_bug_host_help": (
        "Hostname your carrier zero-rates, e.g. zero.facebook.com or "
        "free.basics.org. No https://, no path."
    ),
    "admin_tunnel_sni": "SNI host (TLS handshake) / SNI",
    "admin_tunnel_sni_help": (
        "Hostname sent in the TLS SNI extension. Leave blank to reuse "
        "the bug host."
    ),
    "admin_tunnel_proxy": "Upstream proxy URL (optional) / Proxy URL",
    "admin_tunnel_proxy_help": (
        "Front the connection through this CDN/proxy. Must start with "
        "http:// or https://"
    ),
    "admin_tunnel_enabled": "Route all API requests through this host / အသုံးပြု",
    "admin_tunnel_save": "Save & apply to all users / သိမ်းပြီးအားလုံးအတွက်",
    "admin_tunnel_clear": "Disable tunnel / တုံးခွင်ပိတ်",
    "admin_tunnel_saved": "Tunnel updated. All users will pick this up on their next refresh.",
    "admin_tunnel_cleared": "Tunnel disabled.",
    "admin_tunnel_status_active": "Active — routing via {host}",
    "admin_tunnel_status_inactive": "Inactive — direct connection.",
    "admin_tunnel_last_update": "Last updated {when} by @{user}",
    # Bug-host preset library
    "admin_tunnel_presets": "Bug host presets / Preset များ",
    "admin_tunnel_presets_help": (
        "One-tap switch when a host gets blocked. Tap Apply to load it "
        "into the form above, then Save & apply. / "
        "Host ပိတ်လို့မရတဲ့အခါ တစ်ချက်နှိပ်ပြောင်းရန်။"
    ),
    "admin_tunnel_presets_empty": "No presets saved yet. Add one below.",
    "admin_tunnel_preset_apply": "Apply / အသုံးပြု",
    "admin_tunnel_preset_delete": "Delete / ဖျက်",
    "admin_tunnel_preset_applied": "Loaded preset \u201c{name}\u201d. Now tap Save & apply.",
    "admin_tunnel_preset_deleted": "Preset removed.",
    "admin_tunnel_preset_meta": "{host} · added {when}",
    "admin_tunnel_preset_add": "Save current host as preset / လက်ရှိ Host ကို Preset အဖြစ်သိမ်း",
    "admin_tunnel_preset_name": "Preset name / နာမည်",
    "admin_tunnel_preset_name_ph": "e.g. MPT — Free Basics",
    "admin_tunnel_preset_save_btn": "Add preset / Preset ထည့်",
    "admin_tunnel_preset_added": "Preset \u201c{name}\u201d saved.",
    # Profile management
    "profile_picture": "Profile picture / ပရိုဖိုင်ဓာတ်ပုံ",
    "profile_picture_help": "PNG, JPG, WEBP or GIF, up to 2 MB. Stored on this device only.",
    "profile_no_avatar": "No photo yet — tap below to upload one.",
    "profile_uploader": "Choose a photo / ဓာတ်ပုံရွေးပါ",
    "profile_preview": "Preview / ကြိုကြည့်",
    "profile_save_avatar": "Save as my profile picture / သိမ်း",
    "profile_replace_avatar": "Replace photo / ဓာတ်ပုံပြောင်း",
    "profile_remove_avatar": "Remove photo / ဓာတ်ပုံဖျက်",
    "profile_avatar_too_big": "Image is too large. Max size is 2 MB.",
    "profile_avatar_bad_format": "Unsupported format. Use PNG, JPG, WEBP or GIF.",
    "profile_avatar_saved": "Profile picture updated.",
    "profile_avatar_removed": "Profile picture removed.",
    "profile_bio": "Bio / အကြောင်းအရာ",
    "profile_bio_help": "A short line about yourself. Shown next to your name.",
    "profile_bio_placeholder": "Say a few words about yourself / ကိုယ့်အကြောင်းတိုတိုရေးပါ",
    "profile_bio_save": "Save bio / အကြောင်းသိမ်း",
    "profile_bio_saved": "Bio updated.",
    "profile_bio_empty_caption": "No bio yet.",
    "profile_section_header": "My profile / ကျွန်ုပ်ပရိုဖိုင်",
}


# --- service plumbing -------------------------------------------------------


@st.cache_resource
def _embedded_server() -> DNSTunnelServer:
    """Local DNS tunnel server kept alive across reruns."""
    with SERVER_LOCK:
        srv = DNSTunnelServer(tunnel_domain=DEFAULT_DOMAIN, bind=EMBEDDED_SERVER_BIND)
        srv.start()
    return srv


@st.cache_resource
def _keyring() -> Keyring:
    return Keyring()


@st.cache_resource
def _store() -> Store:
    s = Store()
    s.load()
    return s


@st.cache_resource
def _feature_requests() -> FeatureRequestStore:
    return FeatureRequestStore()


@st.cache_resource
def _profiles() -> ProfileStore:
    return ProfileStore()


@st.cache_resource
def _broadcasts() -> BroadcastStore:
    return BroadcastStore()


@st.cache_resource
def _tunnel_settings_store() -> TunnelSettingsStore:
    return TunnelSettingsStore()


@st.cache_resource
def _tunnel_presets() -> TunnelPresetStore:
    return TunnelPresetStore()


def _is_admin(username: str | None) -> bool:
    return bool(username) and username.lower() in ADMIN_USERNAMES


def _service(me: str, resolver: str) -> ChatService:
    host, _, port = resolver.partition(":")
    # Pull the latest admin-edited bug-host config on every render so a
    # rotation by the admin propagates to every signed-in user without
    # any restart on their side.
    tunnel = _tunnel_settings_store().get()
    client = DNSTunnelClient(
        tunnel_domain=DEFAULT_DOMAIN,
        resolver=(host, int(port)),
        timeout=2.0,
        tunnel=tunnel,
    )
    return ChatService(me=me, keyring=_keyring(), store=_store(), dns_client=client)


# --- styling ---------------------------------------------------------------

_CHAT_CSS = """
<style>
.msg-row { display: flex; margin: 6px 0; width: 100%; }
.msg-row.sent { justify-content: flex-end; }
.msg-row.received { justify-content: flex-start; }
.msg-bubble {
  max-width: 100%;
  padding: 10px 14px;
  border-radius: 18px;
  font-size: 15px;
  line-height: 1.45;
  word-wrap: break-word;
  white-space: pre-wrap;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
  user-select: none;
  -webkit-user-select: none;
  cursor: pointer;
  position: relative;
  transition: transform 120ms ease, box-shadow 120ms ease;
}
.msg-bubble.lp-pressing {
  transform: scale(0.97);
  box-shadow: 0 0 0 2px rgba(99,102,241,0.45);
}
.msg-bubble.sent {
  background-color: #DCEEFF;
  color: #102a43;
  border-bottom-right-radius: 4px;
}
.msg-bubble.received {
  background-color: #ECECEC;
  color: #1f2937;
  border-bottom-left-radius: 4px;
}
.msg-bubble.deleted { font-style: italic; opacity: 0.65; }
.msg-time {
  display: block;
  font-size: 11px;
  color: #6b7280;
  margin-top: 4px;
  text-align: right;
}
.msg-bubble.received .msg-time { text-align: left; }
.msg-via {
  font-size: 10px;
  color: #9ca3af;
  margin-left: 6px;
}
.msg-edited {
  font-size: 10px;
  font-style: italic;
  color: #9ca3af;
  margin-left: 6px;
}
.msg-tick {
  display: inline-block;
  margin-left: 6px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: -2px;
  color: #94a3b8;
  vertical-align: baseline;
}
.msg-tick.read {
  color: #2563eb;
}
.msg-quote {
  border-left: 3px solid #60a5fa;
  padding: 4px 8px;
  margin-bottom: 6px;
  background: rgba(255,255,255,0.55);
  border-radius: 6px;
  font-size: 12.5px;
  color: #374151;
}
.msg-quote .quoted-sender { font-weight: 600; color: #1d4ed8; }
.reply-banner {
  background: #EEF2FF;
  border-left: 4px solid #6366F1;
  padding: 8px 12px;
  border-radius: 6px;
  margin-bottom: 6px;
  font-size: 13px;
  color: #312e81;
}
.actions-hint {
  text-align: center;
  font-size: 11px;
  color: #9ca3af;
  padding: 4px 0 8px;
}

/* Make the per-message popover trigger a discreet "⋯" handle */
[data-testid="stPopover"] > div > button,
[data-testid="stPopover"] button[kind] {
  opacity: 0.35;
  background: transparent !important;
  border: none !important;
  color: #6b7280 !important;
  font-size: 16px !important;
  padding: 0 6px !important;
  min-height: 26px !important;
  box-shadow: none !important;
}
[data-testid="stPopover"] > div > button:hover,
[data-testid="stPopover"] button[kind]:hover {
  opacity: 1 !important;
  color: #111827 !important;
}
</style>
"""


# A tiny JS helper that lives in a hidden iframe. It walks the parent
# document, finds every chat bubble, and turns long-press on a bubble
# into a click on the matching "⋯" popover trigger that sits in the
# same flex row. This gives us native-feeling Telegram-style long-press
# without writing a custom Streamlit component.
_LONG_PRESS_JS = """
<script>
(function() {
  if (window.__lpInstalled) return;
  window.__lpInstalled = true;
  let doc;
  try { doc = window.parent.document; } catch (e) { return; }
  const HOLD_MS = 450;
  const MOVE_TOLERANCE = 8;
  let timer = null;
  let pressed = null;
  let startX = 0, startY = 0;

  function findTrigger(bubble) {
    const row = bubble.closest('[data-testid="stHorizontalBlock"]');
    if (!row) return null;
    return row.querySelector('[data-testid="stPopover"] button');
  }

  function fire(bubble) {
    const btn = findTrigger(bubble);
    if (btn) {
      btn.click();
      if (navigator.vibrate) { try { navigator.vibrate(18); } catch (e) {} }
    }
  }

  function clear() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (pressed) { pressed.classList.remove('lp-pressing'); pressed = null; }
  }

  function attach(bubble) {
    if (bubble.dataset.lpAttached) return;
    bubble.dataset.lpAttached = "1";

    const start = (e) => {
      clear();
      pressed = bubble;
      bubble.classList.add('lp-pressing');
      const t = (e.touches && e.touches[0]) || e;
      startX = t.clientX; startY = t.clientY;
      timer = setTimeout(() => {
        if (pressed === bubble) {
          fire(bubble);
        }
        clear();
      }, HOLD_MS);
    };
    const move = (e) => {
      if (!pressed) return;
      const t = (e.touches && e.touches[0]) || e;
      if (Math.abs(t.clientX - startX) > MOVE_TOLERANCE ||
          Math.abs(t.clientY - startY) > MOVE_TOLERANCE) {
        clear();
      }
    };
    bubble.addEventListener('mousedown', start);
    bubble.addEventListener('touchstart', start, {passive: true});
    bubble.addEventListener('mousemove', move);
    bubble.addEventListener('touchmove', move, {passive: true});
    bubble.addEventListener('mouseup', clear);
    bubble.addEventListener('mouseleave', clear);
    bubble.addEventListener('touchend', clear);
    bubble.addEventListener('touchcancel', clear);
    bubble.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      fire(bubble);
    });
  }

  function scan() {
    doc.querySelectorAll('.msg-bubble[data-msgid]').forEach(attach);
  }

  scan();
  const obs = new MutationObserver(scan);
  obs.observe(doc.body, {childList: true, subtree: true});
})();
</script>
"""


# --- UI entry point --------------------------------------------------------


def render() -> None:
    st.set_page_config(
        page_title=L["title"], page_icon="\U0001F4AC", layout="wide"
    )
    st.markdown(_CHAT_CSS, unsafe_allow_html=True)
    components_html(_LONG_PRESS_JS, height=0)
    _render_version_watcher()

    _embedded_server()  # ensure local server is up

    store = _store()
    keyring = _keyring()
    # Force-load both from disk so `store.me` and `keyring.profile()`
    # reflect what was persisted by an earlier session, not the empty
    # in-memory defaults.
    store.load()

    # Auto-restore the signed-in account from the on-disk profile so
    # the user never has to re-enter their username on subsequent opens.
    profile = keyring.profile()
    if profile is not None:
        st.session_state.username = profile.username
        if store.me != profile.username:
            store.set_me(profile.username)

    st.session_state.setdefault("selected_chat", None)
    st.session_state.setdefault("reply_to", None)
    st.session_state.setdefault("editing_id", None)

    # Deep-link entry point. Opening the app at
    # ``?add=<username>&key=<base64>`` stashes those values so the
    # "Add contact" form pre-fills and auto-expands on the next render.
    _consume_add_contact_link()

    # First run / logged-out: a single full-screen setup card. No
    # sidebar, no chat pane -- there's nothing to show until they pick
    # a username.
    if profile is None:
        _render_first_run_setup(store, keyring)
        return

    with st.sidebar:
        _render_sidebar(store, keyring)

    me = st.session_state.username
    resolver = st.session_state.get("resolver", DEFAULT_RESOLVER)

    st_autorefresh(interval=POLL_INTERVAL_MS, key="poll_tick")
    service = _service(me, resolver)
    new_msgs = service.poll(timeout=1.0)
    if new_msgs:
        st.toast(f"{len(new_msgs)} new update(s)")

    _render_broadcast_banner(me)
    _render_chat_pane(service, store, keyring)


def _render_version_watcher() -> None:
    """Tell the front-end which build is currently running.

    The fingerprint changes whenever any ``*.py`` file is edited (and
    the Streamlit server reloads), so the moment a redeploy happens
    every connected user sees a sticky banner offering to refresh into
    the new version. The browser remembers the version it last opened
    in ``localStorage`` so a single device only nags once per build.
    """
    version = compute_version()
    payload = {
        "version": version,
        "text": L["update_banner_text"],
        "button": L["update_banner_button"],
    }
    components_html(
        f"""
        <script>
        (function() {{
          const CFG = {json.dumps(payload)};
          const KEY = 'messenger_app_version';
          const PARENT = window.parent;
          if (!PARENT || !PARENT.document) return;
          const stored = PARENT.localStorage.getItem(KEY);
          if (!stored) {{
            PARENT.localStorage.setItem(KEY, CFG.version);
            return;
          }}
          if (stored === CFG.version) return;

          const doc = PARENT.document;
          let bar = doc.getElementById('app-version-banner');
          if (!bar) {{
            bar = doc.createElement('div');
            bar.id = 'app-version-banner';
            bar.style.cssText = [
              'position:fixed', 'top:0', 'left:0', 'right:0',
              'z-index:2147483647',
              'background:linear-gradient(90deg,#1d9bf0,#2563eb)',
              'color:#ffffff', 'padding:10px 16px',
              'display:flex', 'align-items:center', 'justify-content:center',
              'gap:12px', 'flex-wrap:wrap',
              'font-family:system-ui,-apple-system,Segoe UI,sans-serif',
              'font-size:14px', 'font-weight:500',
              'box-shadow:0 2px 10px rgba(0,0,0,0.18)'
            ].join(';');
            const msg = doc.createElement('span');
            msg.textContent = '\u2728  ' + CFG.text;
            const btn = doc.createElement('button');
            btn.id = 'app-version-refresh';
            btn.textContent = CFG.button;
            btn.style.cssText = [
              'background:#ffffff', 'color:#1d4ed8', 'border:none',
              'border-radius:999px', 'padding:6px 16px',
              'font-weight:700', 'font-size:13px', 'cursor:pointer',
              'box-shadow:0 1px 4px rgba(0,0,0,0.15)'
            ].join(';');
            btn.onclick = function() {{
              PARENT.localStorage.setItem(KEY, CFG.version);
              PARENT.location.reload();
            }};
            bar.appendChild(msg);
            bar.appendChild(btn);
            doc.body.appendChild(bar);
            // Push the Streamlit app down so the banner doesn't cover it.
            const root = doc.querySelector('section.main') || doc.body;
            root.style.paddingTop = (bar.offsetHeight + 6) + 'px';
          }}
        }})();
        </script>
        """,
        height=0,
    )


def _render_broadcast_banner(me: str) -> None:
    """Show the latest unread admin broadcast at the top of the screen.

    Stays visible across reruns until the user taps *Got it*; the
    dismissal is recorded per-username so each account on this device
    can acknowledge independently.
    """
    bc = _broadcasts().latest_unread_for(me)
    if bc is None:
        return
    badge_html = (
        _verified_badge_html(size=14) if _is_verified(bc.author) else ""
    )
    meta = L["broadcast_banner_from"].format(user=bc.author)
    st.markdown(
        f'<div style="border:1px solid #BFDBFE;background:#EFF6FF;'
        f'border-left:5px solid #1D9BF0;border-radius:8px;'
        f'padding:12px 14px;margin:0 0 14px;">'
        f'<div style="display:flex;align-items:center;gap:6px;'
        f'font-size:12px;color:#1E40AF;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:0.04em;">'
        f'\U0001F4E2 {html.escape(L["broadcast_banner_title"])}</div>'
        f'<div style="font-size:14.5px;color:#0F172A;margin-top:6px;'
        f'white-space:pre-wrap;line-height:1.45;">'
        f'{html.escape(bc.text)}</div>'
        f'<div style="display:flex;align-items:center;gap:4px;'
        f'font-size:11px;color:#475569;margin-top:6px;">'
        f'{html.escape(meta)}{badge_html} · '
        f'{html.escape(_format_when(bc.ts))}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if st.button(
        f"\u2713 {L['broadcast_banner_dismiss']}",
        key=f"dismiss_bc_{bc.id}",
        use_container_width=False,
    ):
        _broadcasts().dismiss(me, bc.id)
        st.rerun()


# --- first-run setup (shown only once) -------------------------------------


_VALID_USERNAME = re.compile(r"^[a-z0-9._-]{2,32}$")


def _render_first_run_setup(store: Store, keyring: Keyring) -> None:
    """Welcome / account-creation screen. Shown only when there is no
    profile on disk, i.e. either the very first launch or right after
    a manual log-out."""
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown(f"## \U0001F4AC {L['welcome_title']}")
        st.write(L["welcome_blurb"])
        with st.form("first_run_setup", clear_on_submit=False):
            username = st.text_input(
                L["welcome_field"],
                key="setup_username_input",
                help=L["welcome_field_help"],
                placeholder="e.g. pyae",
            )
            submitted = st.form_submit_button(
                L["welcome_submit"], type="primary", use_container_width=True
            )
            if submitted:
                cleaned = (username or "").strip().lower()
                if not cleaned or not _VALID_USERNAME.match(cleaned):
                    st.error(L["welcome_invalid"])
                else:
                    keyring.ensure_profile(cleaned)
                    store.set_me(cleaned)
                    st.session_state.username = cleaned
                    st.rerun()
        st.caption(L["welcome_one_time"])
        st.caption(L["e2e_note"])


# --- sidebar ---------------------------------------------------------------


def _render_sidebar(store: Store, keyring: Keyring) -> None:
    st.header(L["title"])
    st.caption(L["subtitle"])

    _render_profile_section(store, keyring)

    if _is_admin(st.session_state.get("username")):
        _render_admin_panel()

    with st.expander(L["server_settings"], expanded=False):
        st.text_input(
            L["resolver_label"],
            value=st.session_state.get("resolver", DEFAULT_RESOLVER),
            key="resolver",
        )

    st.divider()
    st.subheader(L["chats"])
    chats = list_chats(keyring)
    if not chats:
        st.caption(L["no_chats"])
    else:
        for ref in chats:
            icon = "\U0001F464" if ref.kind == "contact" else "\U0001F465"
            label = f"{icon} {ref.name}"
            preview = store.last_message(ref.chat_id)
            if preview is not None and not preview.deleted:
                snippet = preview.text if len(preview.text) <= 35 else preview.text[:32] + "..."
                label += f"\n\n_{snippet}_"
            if st.button(label, key=f"chat-{ref.chat_id}", use_container_width=True):
                st.session_state.selected_chat = ref.chat_id
                st.session_state.reply_to = None
                st.session_state.editing_id = None
                st.rerun()

    st.divider()
    st.subheader(L["contact_book"])
    _render_scan_qr_form(keyring)
    _render_new_contact_form(keyring)
    _render_new_group_form(keyring)
    _render_show_key_button(keyring)
    st.caption(L["e2e_note"])


def _render_profile_section(store: Store, keyring: Keyring) -> None:
    profile = keyring.profile()
    if profile is None:
        return

    user_profile = _profiles().get(profile.username)
    _render_profile_card(profile.username, user_profile)

    key_b64 = keyring.export_my_key_b64() or ""

    if st.session_state.get("show_my_qr"):
        png = make_profile_png(profile.username, key_b64)
        st.image(png, caption=f"@{profile.username}", width=240)
        st.caption(L["qr_caption"])
        if st.button(
            f"\U0001F441 {L['hide_my_qr']}",
            key="hide_my_qr_btn",
            use_container_width=True,
        ):
            st.session_state.show_my_qr = False
            st.rerun()
    else:
        if st.button(
            f"\U0001F4F1 {L['show_my_qr']}",
            key="show_my_qr_btn",
            use_container_width=True,
            type="primary",
        ):
            st.session_state.show_my_qr = True
            st.rerun()

    _render_share_link(profile.username, key_b64)

    with st.expander(L["show_my_key"], expanded=False):
        st.caption(L["my_key_help"])
        st.caption(L["key_locked_caption"])
        st.code(key_b64, language=None)
        st.caption(L["copy_hint"])

    _render_account_settings(store, keyring, profile.username)


def _avatar_data_url(profile: UserProfile) -> str | None:
    """Inline base64 data URL for a stored avatar, or ``None``."""
    if not profile.has_avatar:
        return None
    import base64 as _b64

    mime = profile.avatar_mime or "image/png"
    encoded = _b64.b64encode(profile.avatar).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _avatar_initial(username: str) -> str:
    return (username or "?").strip()[:1].upper() or "?"


def _avatar_color(username: str) -> str:
    """Stable, friendly background colour derived from the username."""
    palette = [
        "#6366F1", "#0EA5E9", "#14B8A6", "#F59E0B",
        "#EF4444", "#A855F7", "#EC4899", "#10B981",
    ]
    seed = sum(ord(c) for c in (username or "").lower())
    return palette[seed % len(palette)]


def _avatar_html(
    username: str, profile: UserProfile, size: int = 64
) -> str:
    """Circular avatar markup -- uses the uploaded image if present,
    otherwise falls back to a coloured initial badge."""
    src = _avatar_data_url(profile)
    if src:
        return (
            f'<div style="width:{size}px;height:{size}px;border-radius:50%;'
            f'overflow:hidden;flex-shrink:0;'
            f'box-shadow:0 1px 3px rgba(0,0,0,0.12);'
            f'background:#E5E7EB;">'
            f'<img src="{src}" alt="{html.escape(username)}" '
            f'style="width:100%;height:100%;object-fit:cover;display:block;"/>'
            f'</div>'
        )
    bg = _avatar_color(username)
    initial = _avatar_initial(username)
    font = max(int(size * 0.45), 14)
    return (
        f'<div style="width:{size}px;height:{size}px;border-radius:50%;'
        f'background:{bg};color:#FFFFFF;display:flex;'
        f'align-items:center;justify-content:center;flex-shrink:0;'
        f'font-weight:700;font-size:{font}px;letter-spacing:-1px;'
        f'box-shadow:0 1px 3px rgba(0,0,0,0.12);">'
        f'{html.escape(initial)}'
        f'</div>'
    )


def _render_profile_card(username: str, profile: UserProfile) -> None:
    """The compact profile card shown at the top of the sidebar.

    Avatar, username and bio sit in a single row so the user always
    sees who they are signed in as before doing anything else.
    """
    bio = (profile.bio or "").strip()
    bio_html = (
        f'<div style="font-size:12.5px;color:#374151;'
        f'margin-top:2px;line-height:1.35;white-space:pre-wrap;">'
        f'{html.escape(bio)}</div>'
        if bio
        else f'<div style="font-size:11.5px;color:#9CA3AF;font-style:italic;'
        f'margin-top:2px;">{html.escape(L["profile_bio_empty_caption"])}</div>'
    )
    badge_html = _verified_badge_html(size=16) if _is_verified(username) else ""
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:12px;'
        f'padding:6px 0 10px;">'
        f'{_avatar_html(username, profile, size=56)}'
        f'<div style="min-width:0;">'
        f'<div style="font-size:14.5px;font-weight:700;color:#111827;'
        f'overflow:hidden;text-overflow:ellipsis;'
        f'display:flex;align-items:center;">'
        f'<span>@{html.escape(username)}</span>{badge_html}</div>'
        f'<div style="font-size:11px;color:#6b7280;">'
        f'{html.escape(L["logged_in_as"])}</div>'
        f'{bio_html}'
        f'</div></div>',
        unsafe_allow_html=True,
    )


def _render_profile_editor(current_username: str) -> None:
    """Profile-management form: avatar upload + bio.

    Lives inside the *Account & settings* expander so the sidebar stays
    tidy. The user can preview a picked image before saving and
    overwrite an existing photo as many times as they like.
    """
    profiles = _profiles()
    profile = profiles.get(current_username)

    st.markdown(f"**\U0001F5BC\uFE0F {L['profile_picture']}**")
    st.caption(L["profile_picture_help"])

    cols = st.columns([1, 2])
    with cols[0]:
        st.markdown(
            f'<div style="display:flex;justify-content:center;'
            f'padding:6px 0;">'
            f'{_avatar_html(current_username, profile, size=96)}'
            f'</div>',
            unsafe_allow_html=True,
        )
    with cols[1]:
        if profile.has_avatar:
            st.caption(L["profile_replace_avatar"])
        else:
            st.caption(L["profile_no_avatar"])

    upload = st.file_uploader(
        L["profile_uploader"],
        type=["png", "jpg", "jpeg", "webp", "gif"],
        key=f"avatar_uploader_{current_username}",
        label_visibility="collapsed",
    )

    if upload is not None:
        data = upload.getvalue()
        mime = guess_mime(upload.name, getattr(upload, "type", None))
        st.markdown(L["profile_preview"])
        if mime in ALLOWED_MIME and data:
            import base64 as _b64

            preview_src = (
                f"data:{mime};base64,"
                f"{_b64.b64encode(data).decode('ascii')}"
            )
            st.markdown(
                f'<div style="display:flex;justify-content:center;'
                f'padding:8px 0;">'
                f'<div style="width:120px;height:120px;border-radius:50%;'
                f'overflow:hidden;background:#E5E7EB;'
                f'box-shadow:0 2px 6px rgba(0,0,0,0.12);">'
                f'<img src="{preview_src}" '
                f'style="width:100%;height:100%;object-fit:cover;'
                f'display:block;"/>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

        save_cols = st.columns([1, 1])
        if save_cols[0].button(
            L["profile_save_avatar"],
            key="avatar_save_btn",
            type="primary",
            use_container_width=True,
        ):
            try:
                profiles.set_avatar(current_username, data, mime)
                st.toast(L["profile_avatar_saved"])
                st.rerun()
            except ValueError as exc:
                msg = str(exc)
                if "too large" in msg:
                    st.error(L["profile_avatar_too_big"])
                elif "unsupported" in msg:
                    st.error(L["profile_avatar_bad_format"])
                else:
                    st.error(msg)

    if profile.has_avatar:
        if st.button(
            f"\U0001F5D1 {L['profile_remove_avatar']}",
            key="avatar_remove_btn",
            use_container_width=True,
        ):
            profiles.clear_avatar(current_username)
            st.toast(L["profile_avatar_removed"])
            st.rerun()

    st.divider()

    st.markdown(f"**\U0001F4DD {L['profile_bio']}**")
    st.caption(L["profile_bio_help"])
    with st.form(f"bio_form_{current_username}", clear_on_submit=False):
        bio = st.text_area(
            L["profile_bio"],
            value=profile.bio,
            key=f"bio_input_{current_username}",
            placeholder=L["profile_bio_placeholder"],
            label_visibility="collapsed",
            height=90,
            max_chars=240,
        )
        if st.form_submit_button(
            L["profile_bio_save"],
            type="primary",
            use_container_width=True,
        ):
            profiles.set_bio(current_username, bio or "")
            st.toast(L["profile_bio_saved"])
            st.rerun()


def _render_share_link(username: str, key_b64: str) -> None:
    """Toggleable deep link with one-tap copy buttons.

    Two flavours are offered:

    * ``messenger://<user>/<key>`` -- the native deep link, for apps
      that handle the custom scheme.
    * A web URL built from ``window.location`` plus
      ``?add=<user>&key=<key>``, which opens the running Streamlit
      instance and auto-fills the friend's Add Contact form.
    """
    uri = make_profile_uri(username, key_b64)
    if st.session_state.get("show_share_link"):
        st.caption(L["share_link_caption"])
        st.code(uri, language=None)
        st.caption(L["share_link_web_caption"])
        components_html(
            f"""
            <div style="display:flex;flex-direction:column;gap:6px;">
              <button id="copy-profile-link"
                  style="padding:8px 12px;border-radius:6px;
                         border:1px solid #c7d2fe;background:#EEF2FF;
                         color:#312e81;font-size:13px;font-weight:600;
                         cursor:pointer;">
                \U0001F4CB {html.escape(L["share_link_copy_btn"])}
              </button>
              <button id="copy-web-link"
                  style="padding:8px 12px;border-radius:6px;
                         border:1px solid #bbf7d0;background:#ECFDF5;
                         color:#065f46;font-size:13px;font-weight:600;
                         cursor:pointer;">
                \U0001F310 {html.escape(L["share_link_copy_web_btn"])}
              </button>
            </div>
            <div id="copy-profile-status"
                 style="font-size:11px;color:#16a34a;margin-top:4px;
                        text-align:center;height:14px;"></div>
            <script>
              const uri = {json.dumps(uri)};
              const username = {json.dumps(username)};
              const keyB64 = {json.dumps(key_b64)};
              const status = document.getElementById("copy-profile-status");
              const okMsg = {json.dumps(L["share_link_copied"])};

              function buildWebLink() {{
                const parent = window.parent;
                const origin = parent.location.origin;
                const pathname = parent.location.pathname;
                const params = new URLSearchParams({{
                  add: username,
                  key: keyB64,
                }});
                return origin + pathname + "?" + params.toString();
              }}

              async function copy(text) {{
                try {{
                  await navigator.clipboard.writeText(text);
                }} catch (e) {{
                  const ta = document.createElement("textarea");
                  ta.value = text;
                  document.body.appendChild(ta);
                  ta.select();
                  document.execCommand("copy");
                  ta.remove();
                }}
                status.textContent = okMsg;
                setTimeout(() => {{ status.textContent = ""; }}, 1800);
              }}

              document.getElementById("copy-profile-link")
                .addEventListener("click", () => copy(uri));
              document.getElementById("copy-web-link")
                .addEventListener("click", () => copy(buildWebLink()));
            </script>
            """,
            height=110,
        )
        if st.button(
            f"\U0001F441 {L['hide_share_link']}",
            key="hide_share_link_btn",
            use_container_width=True,
        ):
            st.session_state.show_share_link = False
            st.rerun()
    else:
        if st.button(
            f"\U0001F517 {L['share_link']}",
            key="show_share_link_btn",
            use_container_width=True,
        ):
            st.session_state.show_share_link = True
            st.rerun()


def _render_account_settings(store: Store, keyring: Keyring, current_username: str) -> None:
    """Tucked-away expander for changing the username or logging out.

    Hidden by default so the user is never *prompted* for their
    credentials -- they only ever interact with this if they explicitly
    want to."""
    with st.expander(f"\u2699\ufe0f {L['account_settings']}", expanded=False):
        _render_profile_editor(current_username)

        st.divider()

        st.markdown(f"**{L['rename_username']}**")
        st.caption(L["rename_help"])
        with st.form("rename_form", clear_on_submit=False):
            new_name = st.text_input(
                L["your_username"],
                value=current_username,
                key="rename_input",
            )
            if st.form_submit_button(L["rename_btn"], use_container_width=True):
                cleaned = (new_name or "").strip().lower()
                if not cleaned or not _VALID_USERNAME.match(cleaned):
                    st.error(L["welcome_invalid"])
                elif cleaned != current_username:
                    keyring.ensure_profile(cleaned)  # preserves the key
                    store.set_me(cleaned)
                    _profiles().rename(current_username, cleaned)
                    st.session_state.username = cleaned
                    st.rerun()

        st.divider()

        if st.session_state.get("confirm_logout"):
            st.warning(L["logout_confirm"])
            cols = st.columns(2)
            if cols[0].button(
                L["logout_yes"], key="logout_yes_btn", use_container_width=True, type="primary"
            ):
                _do_logout(store, keyring)
                st.toast(L["logged_out_toast"])
                st.rerun()
            if cols[1].button(
                L["logout_cancel"], key="logout_cancel_btn", use_container_width=True
            ):
                st.session_state.confirm_logout = False
                st.rerun()
        else:
            if st.button(
                f"\U0001F6AA {L['logout']}",
                key="logout_btn",
                use_container_width=True,
            ):
                st.session_state.confirm_logout = True
                st.rerun()

        st.divider()
        _render_feature_request_form(current_username)


def _render_feature_request_form(current_username: str) -> None:
    """User-facing 'Request New Feature' form.

    Submitted entries are stored privately in the feature_requests
    table and surfaced only inside the admin's Admin Panel -- so from
    the user's perspective it behaves like a hidden system message
    addressed straight to the admin.
    """
    fr_store = _feature_requests()
    st.markdown(f"**\U0001F4A1 {L['feature_request']}**")
    st.caption(L["feature_request_help"])
    with st.form("feature_request_form", clear_on_submit=True):
        text = st.text_area(
            L["feature_request"],
            key="feature_request_text",
            placeholder=L["feature_request_placeholder"],
            label_visibility="collapsed",
            height=110,
        )
        submitted = st.form_submit_button(
            L["feature_request_submit"],
            use_container_width=True,
            type="primary",
        )
        if submitted:
            if not text or not text.strip():
                st.error(L["feature_request_empty"])
            else:
                try:
                    fr_store.submit(current_username, text)
                    st.toast(L["feature_request_sent"])
                except ValueError as exc:
                    st.error(str(exc))

    history = fr_store.list_for_user(current_username)
    with st.expander(L["feature_request_my_history"], expanded=False):
        if not history:
            st.caption(L["feature_request_no_history"])
            return
        for req in history:
            badge = {
                STATUS_OPEN: L["admin_open"],
                STATUS_PLANNED: L["admin_planned"],
                STATUS_REPLIED: L["admin_replied"],
            }.get(req.status, req.status)
            st.markdown(
                f"<div style='border-left:3px solid #c7d2fe;"
                f"padding:6px 10px;margin:6px 0;background:#F8FAFC;"
                f"border-radius:6px;font-size:13px;'>"
                f"<div style='font-size:11px;color:#6b7280;'>"
                f"{html.escape(_format_when(req.ts))} · "
                f"<b>{html.escape(badge)}</b></div>"
                f"<div>{html.escape(req.text)}</div>"
                + (
                    f"<div style='margin-top:6px;padding:6px 8px;"
                    f"background:#ECFDF5;border-radius:4px;"
                    f"font-size:12.5px;color:#065f46;'>"
                    f"<b>{html.escape(L['admin_replied'])}:</b> "
                    f"{html.escape(req.reply_text)}</div>"
                    if req.reply_text
                    else ""
                )
                + "</div>",
                unsafe_allow_html=True,
            )


_PIN_FORMAT = re.compile(r"^\d{4}$")


def _admin_unlocked() -> bool:
    return bool(st.session_state.get("admin_unlocked"))


def _render_admin_panel() -> None:
    """Sidebar Admin Panel. Only rendered for admin usernames.

    The contents are always gated behind a 4-digit PIN -- being signed
    in as an admin username is necessary but not sufficient.
    """
    fr_store = _feature_requests()
    unlocked = _admin_unlocked()
    if unlocked:
        open_count = fr_store.count_open()
        badge = f" ({open_count})" if open_count else ""
        header = f"\U0001F6E1\uFE0F {L['admin_panel']}{badge}"
    else:
        header = (
            f"\U0001F510 {L['admin_panel']} "
            f"\u2014 {L['admin_pin_locked']}"
        )

    with st.expander(header, expanded=False):
        if not unlocked:
            _render_admin_pin_gate()
            return

        tab_requests, tab_broadcast, tab_tunnel = st.tabs(
            [L["admin_user_requests"], L["admin_broadcast"], L["admin_tunnel"]]
        )
        with tab_requests:
            _render_admin_user_requests(fr_store)
        with tab_broadcast:
            _render_admin_broadcast()
        with tab_tunnel:
            _render_admin_tunnel()

        st.divider()
        if st.button(
            f"\U0001F512 {L['admin_pin_lock']}",
            key="admin_lock_btn",
            use_container_width=True,
        ):
            st.session_state.admin_unlocked = False
            st.session_state.pop("admin_pin_input", None)
            st.toast(L["admin_locked_toast"])
            st.rerun()


def _render_admin_broadcast() -> None:
    """Admin-only composer for app-wide announcements.

    Sends a broadcast that appears as a dismissable banner at the top
    of every other user's screen until they tap *Got it*. Recent
    broadcasts are listed below the form so the admin can clean them
    up without digging through the database.
    """
    bc_store = _broadcasts()
    me = st.session_state.get("username") or ""

    st.caption(L["admin_broadcast_help"])
    with st.form("admin_broadcast_form", clear_on_submit=True):
        text = st.text_area(
            L["admin_broadcast"],
            key="admin_broadcast_text",
            placeholder=L["admin_broadcast_placeholder"],
            label_visibility="collapsed",
            height=110,
            max_chars=1500,
        )
        submitted = st.form_submit_button(
            f"\U0001F4E2 {L['admin_broadcast_send']}",
            type="primary",
            use_container_width=True,
        )
        if submitted:
            if not text or not text.strip():
                st.error(L["admin_broadcast_empty"])
            else:
                try:
                    bc_store.submit(me, text)
                    st.toast(L["admin_broadcast_sent"])
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    st.divider()
    st.markdown(f"**{L['admin_broadcast_recent']}**")
    recents = bc_store.list_recent(limit=20)
    if not recents:
        st.caption(L["admin_broadcast_no_recent"])
        return

    for bc in recents:
        meta = L["admin_broadcast_meta"].format(
            user=bc.author, when=_format_when(bc.ts)
        )
        st.markdown(
            f"<div style='border:1px solid #E5E7EB;border-radius:8px;"
            f"padding:8px 10px;margin:6px 0;background:#FFFFFF;'>"
            f"<div style='font-size:11px;color:#6b7280;margin-bottom:4px;'>"
            f"{html.escape(meta)}</div>"
            f"<div style='font-size:13.5px;color:#111827;"
            f"white-space:pre-wrap;'>{html.escape(bc.text)}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if st.button(
            f"\U0001F5D1 {L['admin_broadcast_delete']}",
            key=f"bc_del_{bc.id}",
            use_container_width=False,
        ):
            bc_store.delete(bc.id)
            st.toast(L["admin_broadcast_deleted"])
            st.rerun()


def _render_admin_pin_gate() -> None:
    """4-digit PIN entry form shown when the admin panel is locked."""
    if not ADMIN_PIN:
        st.warning(L["admin_pin_unset"])
        return

    st.caption(L["admin_pin_prompt"])
    with st.form("admin_pin_form", clear_on_submit=True):
        pin = st.text_input(
            L["admin_pin_label"],
            key="admin_pin_input",
            type="password",
            max_chars=4,
            placeholder="\u2022\u2022\u2022\u2022",
            help=L["admin_pin_help"],
        )
        submitted = st.form_submit_button(
            f"\U0001F511 {L['admin_pin_unlock']}",
            type="primary",
            use_container_width=True,
        )
        if submitted:
            entered = (pin or "").strip()
            if not _PIN_FORMAT.match(entered):
                st.error(L["admin_pin_format"])
            elif entered != ADMIN_PIN:
                st.error(L["admin_pin_wrong"])
            else:
                st.session_state.admin_unlocked = True
                st.toast(L["admin_unlocked_toast"])
                st.rerun()


def _render_admin_tunnel() -> None:
    """Admin-only editor for the SNI / bug-host tunnel.

    The values live in a single SQLite row that every client reads on
    each render, so when the admin saves a new host, the next render on
    every other user's device picks it up automatically. That's the
    whole point: when a carrier blocks the current bug host, an admin
    can rotate it from anywhere and keep data-free access alive for
    everyone without anyone reinstalling.
    """
    store = _tunnel_settings_store()
    presets = _tunnel_presets()
    me = st.session_state.get("username") or ""

    # If the admin just tapped "Apply" on a preset, push its values
    # into the form's input keys *before* the widgets are declared --
    # Streamlit only honours session_state pre-seeding that happens
    # ahead of the matching widget call.
    pending_id = st.session_state.pop("admin_tunnel_apply_preset", None)
    if pending_id:
        preset = presets.get(pending_id)
        if preset is not None:
            st.session_state["admin_tunnel_bug_host_in"] = preset.bug_host
            st.session_state["admin_tunnel_sni_in"] = preset.sni_host
            st.session_state["admin_tunnel_proxy_in"] = preset.proxy_url
            st.toast(
                L["admin_tunnel_preset_applied"].format(name=preset.name)
            )

    current = store.get()

    st.caption(L["admin_tunnel_help"])

    if current.is_active:
        st.success(
            L["admin_tunnel_status_active"].format(host=current.bug_host)
        )
    else:
        st.info(L["admin_tunnel_status_inactive"])

    if current.updated_at:
        st.caption(
            L["admin_tunnel_last_update"].format(
                when=time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(current.updated_at)
                ),
                user=current.updated_by or "?",
            )
        )

    with st.form("admin_tunnel_form", clear_on_submit=False):
        bug_host = st.text_input(
            L["admin_tunnel_bug_host"],
            value=current.bug_host,
            help=L["admin_tunnel_bug_host_help"],
            placeholder="zero.facebook.com",
            key="admin_tunnel_bug_host_in",
        )
        sni_host = st.text_input(
            L["admin_tunnel_sni"],
            value=current.sni_host,
            help=L["admin_tunnel_sni_help"],
            placeholder="zero.facebook.com",
            key="admin_tunnel_sni_in",
        )
        proxy_url = st.text_input(
            L["admin_tunnel_proxy"],
            value=current.proxy_url,
            help=L["admin_tunnel_proxy_help"],
            placeholder="https://cdn.example.workers.dev",
            key="admin_tunnel_proxy_in",
        )
        enabled = st.checkbox(
            L["admin_tunnel_enabled"],
            value=current.enabled,
            key="admin_tunnel_enabled_in",
        )

        col_save, col_clear = st.columns(2)
        with col_save:
            save = st.form_submit_button(
                f"\U0001F4BE {L['admin_tunnel_save']}",
                type="primary",
                use_container_width=True,
            )
        with col_clear:
            clear = st.form_submit_button(
                f"\U0001F6AB {L['admin_tunnel_clear']}",
                use_container_width=True,
            )

        if save:
            try:
                store.update(
                    bug_host=bug_host,
                    sni_host=sni_host,
                    proxy_url=proxy_url,
                    enabled=enabled,
                    updated_by=me,
                )
                st.toast(L["admin_tunnel_saved"])
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        elif clear:
            store.update(
                bug_host=current.bug_host,
                sni_host=current.sni_host,
                proxy_url=current.proxy_url,
                enabled=False,
                updated_by=me,
            )
            st.toast(L["admin_tunnel_cleared"])
            st.rerun()

    _render_admin_tunnel_presets(presets, current, me)


def _render_admin_tunnel_presets(
    presets: TunnelPresetStore,
    current,
    me: str,
) -> None:
    """List, apply, delete, and add bug-host presets.

    Apply doesn't immediately push the preset to all users; it loads
    the values into the form above so the admin can review (and tweak
    SNI/proxy if needed) before tapping *Save & apply*. That avoids
    accidental mass-rotations.
    """
    st.divider()
    st.markdown(f"**\U0001F516 {L['admin_tunnel_presets']}**")
    st.caption(L["admin_tunnel_presets_help"])

    items = presets.list_all()
    if not items:
        st.caption(L["admin_tunnel_presets_empty"])
    else:
        for preset in items:
            when = (
                time.strftime(
                    "%Y-%m-%d", time.localtime(preset.created_at)
                )
                if preset.created_at
                else "?"
            )
            with st.container(border=True):
                st.markdown(f"**{preset.name}**")
                st.caption(
                    L["admin_tunnel_preset_meta"].format(
                        host=preset.bug_host, when=when
                    )
                )
                col_apply, col_del = st.columns(2)
                with col_apply:
                    if st.button(
                        f"\u26A1 {L['admin_tunnel_preset_apply']}",
                        key=f"tunnel_preset_apply_{preset.id}",
                        use_container_width=True,
                        type="primary",
                    ):
                        st.session_state["admin_tunnel_apply_preset"] = (
                            preset.id
                        )
                        st.rerun()
                with col_del:
                    if st.button(
                        f"\U0001F5D1\uFE0F {L['admin_tunnel_preset_delete']}",
                        key=f"tunnel_preset_del_{preset.id}",
                        use_container_width=True,
                    ):
                        presets.delete(preset.id)
                        st.toast(L["admin_tunnel_preset_deleted"])
                        st.rerun()

    with st.expander(L["admin_tunnel_preset_add"], expanded=False):
        with st.form("admin_tunnel_preset_add_form", clear_on_submit=True):
            name = st.text_input(
                L["admin_tunnel_preset_name"],
                placeholder=L["admin_tunnel_preset_name_ph"],
                key="admin_tunnel_preset_name_in",
            )
            submitted = st.form_submit_button(
                f"\u2795 {L['admin_tunnel_preset_save_btn']}",
                use_container_width=True,
                type="primary",
            )
            if submitted:
                # Snapshot whatever's currently typed in the editor
                # form (falling back to the saved row) so admins can
                # bookmark a working host without re-entering it.
                bug = (
                    st.session_state.get("admin_tunnel_bug_host_in")
                    or current.bug_host
                )
                sni = (
                    st.session_state.get("admin_tunnel_sni_in")
                    or current.sni_host
                )
                proxy = (
                    st.session_state.get("admin_tunnel_proxy_in")
                    or current.proxy_url
                )
                try:
                    saved = presets.add(
                        name=name,
                        bug_host=bug,
                        sni_host=sni,
                        proxy_url=proxy,
                        created_by=me,
                    )
                    st.toast(
                        L["admin_tunnel_preset_added"].format(name=saved.name)
                    )
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def _render_admin_user_requests(fr_store: FeatureRequestStore) -> None:
    requests = fr_store.list_all()
    if not requests:
        st.caption(L["admin_no_requests"])
        return

    filter_choice = st.radio(
        "filter",
        [
            L["admin_filter_all"],
            L["admin_filter_open"],
            L["admin_filter_planned"],
            L["admin_filter_replied"],
        ],
        key="admin_req_filter",
        horizontal=True,
        label_visibility="collapsed",
    )
    status_filter = {
        L["admin_filter_open"]: STATUS_OPEN,
        L["admin_filter_planned"]: STATUS_PLANNED,
        L["admin_filter_replied"]: STATUS_REPLIED,
    }.get(filter_choice)
    if status_filter is not None:
        requests = [r for r in requests if r.status == status_filter]

    if not requests:
        st.caption(L["admin_no_requests"])
        return

    for req in requests:
        _render_admin_request_card(fr_store, req)


def _render_admin_request_card(
    fr_store: FeatureRequestStore, req
) -> None:
    badge_label, badge_bg, badge_fg = {
        STATUS_OPEN: (L["admin_open"], "#FEF3C7", "#92400E"),
        STATUS_PLANNED: (L["admin_planned"], "#DBEAFE", "#1E40AF"),
        STATUS_REPLIED: (L["admin_replied"], "#DCFCE7", "#166534"),
    }.get(req.status, (req.status, "#E5E7EB", "#374151"))

    meta = L["admin_request_meta"].format(
        user=req.username, when=_format_when(req.ts)
    )
    st.markdown(
        f"<div style='border:1px solid #E5E7EB;border-radius:8px;"
        f"padding:10px 12px;margin:8px 0;background:#FFFFFF;'>"
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:center;margin-bottom:4px;'>"
        f"<span style='font-size:11px;color:#6b7280;'>"
        f"{html.escape(meta)}</span>"
        f"<span style='font-size:10px;font-weight:700;"
        f"padding:2px 8px;border-radius:999px;"
        f"background:{badge_bg};color:{badge_fg};'>"
        f"{html.escape(badge_label)}</span>"
        f"</div>"
        f"<div style='font-size:13.5px;color:#111827;"
        f"white-space:pre-wrap;'>{html.escape(req.text)}</div>"
        + (
            f"<div style='margin-top:8px;padding:6px 10px;"
            f"background:#ECFDF5;border-radius:6px;"
            f"font-size:12.5px;color:#065f46;'>"
            f"<b>{html.escape(L['admin_replied'])}</b> "
            f"<span style='color:#6b7280;font-size:11px;'>· "
            f"{html.escape(_format_when(req.reply_ts) if req.reply_ts else '')}"
            f"</span><br>{html.escape(req.reply_text)}</div>"
            if req.reply_text
            else ""
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    reply_key = f"admin_reply_open_{req.id}"

    if st.session_state.get(reply_key):
        with st.form(f"admin_reply_form_{req.id}", clear_on_submit=True):
            reply_text = st.text_area(
                L["admin_reply_btn"],
                key=f"admin_reply_text_{req.id}",
                placeholder=L["admin_reply_placeholder"],
                label_visibility="collapsed",
                height=80,
            )
            cols = st.columns([1, 1])
            send = cols[0].form_submit_button(
                L["admin_reply_send"],
                type="primary",
                use_container_width=True,
            )
            cancel = cols[1].form_submit_button(
                L["admin_reply_cancel"], use_container_width=True
            )
            if send:
                try:
                    fr_store.reply(req.id, reply_text)
                    st.session_state[reply_key] = False
                    st.toast(L["admin_replied_toast"])
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            elif cancel:
                st.session_state[reply_key] = False
                st.rerun()
    else:
        cols = st.columns([1, 1])
        with cols[0]:
            if st.button(
                f"\u21A9\uFE0F {L['admin_reply_btn']}",
                key=f"admin_reply_btn_{req.id}",
                use_container_width=True,
            ):
                st.session_state[reply_key] = True
                st.rerun()
        with cols[1]:
            if req.status == STATUS_PLANNED:
                if st.button(
                    f"\u21BA {L['admin_reopen']}",
                    key=f"admin_reopen_btn_{req.id}",
                    use_container_width=True,
                ):
                    fr_store.reopen(req.id)
                    st.rerun()
            else:
                if st.button(
                    f"\U0001F4CC {L['admin_mark_planned']}",
                    key=f"admin_planned_btn_{req.id}",
                    use_container_width=True,
                ):
                    fr_store.mark_planned(req.id)
                    st.toast(L["admin_planned_toast"])
                    st.rerun()


def _format_when(ts: float) -> str:
    """Compact 'date · time' for request cards."""
    lt = time.localtime(ts)
    try:
        return time.strftime("%b %d · %-I:%M %p", lt)
    except ValueError:
        return time.strftime("%b %d · %I:%M %p", lt).replace(" 0", " ")


def _do_logout(store: Store, keyring: Keyring) -> None:
    keyring.forget_profile()
    store.clear_me()
    # Reset every screen-level session flag so the next render starts
    # cleanly on the welcome card.
    for k in (
        "username", "selected_chat", "reply_to", "editing_id",
        "show_my_qr", "show_my_key", "confirm_logout",
        "qr_camera", "qr_upload",
        "admin_unlocked", "admin_pin_input",
    ):
        st.session_state.pop(k, None)


def _consume_add_contact_link() -> None:
    qp = st.query_params
    add_user = qp.get("add")
    add_key = qp.get("key")
    if not (add_user and add_key):
        return
    st.session_state.pending_contact = {
        "username": add_user.strip(),
        "key": add_key.strip(),
    }
    for k in ("add", "key"):
        try:
            del st.query_params[k]
        except KeyError:
            pass


def _render_new_contact_form(keyring: Keyring) -> None:
    pending = st.session_state.pop("pending_contact", None)
    if pending:
        st.session_state["nc_username"] = pending["username"]
        st.session_state["nc_key"] = pending["key"]
        expanded = True
    else:
        expanded = bool(st.session_state.pop("auto_expand_add_contact", False))
    with st.expander(L["new_contact"], expanded=expanded):
        with st.form("new_contact", clear_on_submit=True):
            username = st.text_input(L["their_username"], key="nc_username")
            key_b64 = st.text_input(
                L["their_key"], key="nc_key", help=L["their_key_help"]
            )
            submitted = st.form_submit_button(
                L["add_contact_btn"], use_container_width=True, type="primary"
            )
            if submitted:
                if not username or not key_b64:
                    st.error("Username and key are required.")
                else:
                    try:
                        keyring.save_contact(username.strip(), key_b64.strip())
                        st.success(f"Saved '{username.strip()}' to contact book.")
                        st.session_state.selected_chat = contact_chat_id(username.strip())
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))


def _render_scan_qr_form(keyring: Keyring) -> None:
    with st.expander(f"\U0001F4F7 {L['scan_qr']}", expanded=False):
        st.caption(L["scan_qr_help"])
        mode = st.radio(
            "source",
            [L["scan_qr_use_camera"], L["scan_qr_upload"]],
            key="scan_source",
            horizontal=True,
            label_visibility="collapsed",
        )
        if mode == L["scan_qr_use_camera"]:
            shot = st.camera_input(
                L["scan_qr_use_camera"],
                key="qr_camera",
                label_visibility="collapsed",
            )
            data = shot.getvalue() if shot is not None else None
        else:
            up = st.file_uploader(
                L["scan_qr_upload"],
                type=["png", "jpg", "jpeg", "webp"],
                key="qr_upload",
                label_visibility="collapsed",
            )
            data = up.getvalue() if up is not None else None

        if not data:
            return

        with st.spinner(L["scan_qr_decoding"]):
            payload = decode_qr_from_image_bytes(data)

        if not payload:
            st.error(L["scan_qr_no_code"])
            return

        parsed = parse_profile_uri(payload)
        if parsed is None:
            st.error(L["scan_qr_bad_payload"])
            with st.expander("Raw QR payload", expanded=False):
                st.code(payload)
            return

        try:
            keyring.save_contact(parsed.username, parsed.key_b64)
        except ValueError as exc:
            st.error(str(exc))
            return

        st.success(L["scan_qr_added"].format(name=parsed.username))
        st.session_state.selected_chat = contact_chat_id(parsed.username)
        # Reset the inputs so we don't re-decode the same shot on every rerun.
        st.session_state.pop("qr_camera", None)
        st.session_state.pop("qr_upload", None)
        st.rerun()


def _render_new_group_form(keyring: Keyring) -> None:
    with st.expander(L["new_group"], expanded=False):
        with st.form("new_group"):
            gname = st.text_input(L["local_name"], key="ng_name")
            members_raw = st.text_input(L["members"], key="ng_members")
            mode = st.radio(
                L["key_mode"],
                [L["from_passphrase"], L["import_key"]],
                key="ng_mode",
                horizontal=True,
            )
            passphrase = (
                st.text_input(L["passphrase"], key="ng_pass", type="password")
                if mode == L["from_passphrase"]
                else None
            )
            key_b64 = (
                st.text_input(L["base64_key"], key="ng_key")
                if mode == L["import_key"]
                else None
            )
            submitted = st.form_submit_button(L["add_group_btn"])
            if submitted:
                members = [m.strip() for m in members_raw.split(",") if m.strip()]
                if not gname or not members:
                    st.error("Group name and at least one member are required.")
                elif mode == L["from_passphrase"] and not passphrase:
                    st.error("Passphrase is required.")
                elif mode == L["import_key"] and not key_b64:
                    st.error("Base64 key is required.")
                else:
                    try:
                        keyring.add_group(
                            gname,
                            members=members,
                            passphrase=passphrase,
                            key_b64=key_b64,
                        )
                        st.success(f"Added group '{gname}'.")
                        st.session_state.selected_chat = group_chat_id(gname)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))


def _render_show_key_button(keyring: Keyring) -> None:
    selected = st.session_state.get("selected_chat")
    if not selected:
        return
    if selected.startswith("contact:"):
        if st.button(f"\U0001F511 {L['show_key']}", use_container_width=True):
            cname = selected.split(":", 1)[1]
            try:
                st.code(keyring.export_b64(contact=cname))
            except KeyError:
                st.warning("That contact no longer exists.")
    elif selected.startswith("group:"):
        if st.button(f"\U0001F511 {L['show_key']}", use_container_width=True):
            gname = selected.split(":", 1)[1]
            try:
                st.code(keyring.export_b64(group=gname))
            except KeyError:
                st.warning("That group no longer exists.")


# --- main chat pane --------------------------------------------------------


def _render_chat_pane(service: ChatService, store: Store, keyring: Keyring) -> None:
    chat_id = st.session_state.get("selected_chat")
    if not chat_id:
        st.info(L["select_chat"])
        return

    kind, _, name = chat_id.partition(":")
    if kind == "contact":
        try:
            contact = keyring.get_contact(name)
        except KeyError:
            st.error("Contact no longer exists.")
            return
        badge = _verified_badge_html(size=20) if _is_verified(contact.name) else ""
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:6px;'
            f'font-size:1.5rem;font-weight:600;margin:0.5rem 0 0.25rem;">'
            f'\U0001F464 {html.escape(contact.name)}{badge}</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"DM with `{contact.remote_user}`  -  key id `{crypto.key_id(contact.key)}`"
        )
    else:
        try:
            group = keyring.get_group(name)
        except KeyError:
            st.error("Group no longer exists.")
            return
        st.subheader(f"\U0001F465 {group.name}")
        st.caption(
            f"Members: {', '.join(group.members) or '(none)'}  -  key id `{crypto.key_id(group.key)}`"
        )

    # Viewing the chat is the trigger for sending Telegram-style read
    # receipts back to whoever sent us the message. Idempotent -- only
    # un-acked inbound messages actually get a packet sent.
    try:
        service.mark_chat_read(chat_id)
    except Exception:
        pass

    history = store.history(chat_id)
    if not history:
        st.caption(L["no_messages"])
    else:
        st.markdown(f'<div class="actions-hint">{html.escape(L["actions_hint"])}</div>',
                    unsafe_allow_html=True)

    history_by_id = {m.id: m for m in history}
    for msg in history:
        _render_message(service, msg, history_by_id)

    _render_reply_banner(history_by_id)
    prompt = st.chat_input(L["input_placeholder"], key=f"input-{chat_id}")
    if prompt:
        try:
            reply_to = st.session_state.get("reply_to")
            if kind == "contact":
                service.send_to_contact(name, prompt, reply_to=reply_to)
            else:
                service.send_to_group(name, prompt, reply_to=reply_to)
            st.session_state.reply_to = None
            st.rerun()
        except Exception as exc:
            st.error(f"Failed to send: {exc}")


# --- single message --------------------------------------------------------


def _render_message(
    service: ChatService,
    msg: StoredMessage,
    history_by_id: dict[str, StoredMessage],
) -> None:
    # Edit mode replaces both the bubble and the trigger.
    if st.session_state.get("editing_id") == msg.id and msg.outbound and not msg.deleted:
        _render_edit_mode(service, msg)
        return

    align = "sent" if msg.outbound else "received"
    bubble_html = _bubble_html(msg, history_by_id, align)

    # Layout: bubble + trigger column, switched by alignment.
    if msg.outbound:
        col_bubble, col_trigger = st.columns([11, 1], gap="small")
        with col_bubble:
            st.markdown(bubble_html, unsafe_allow_html=True)
        with col_trigger:
            if msg.deleted:
                st.empty()
            else:
                _render_action_popover(service, msg)
    else:
        col_trigger, col_bubble = st.columns([1, 11], gap="small")
        with col_trigger:
            if msg.deleted:
                st.empty()
            else:
                _render_action_popover(service, msg)
        with col_bubble:
            st.markdown(bubble_html, unsafe_allow_html=True)


def _bubble_html(
    msg: StoredMessage,
    history_by_id: dict[str, StoredMessage],
    align: str,
) -> str:
    quote_html = ""
    if msg.reply_to and msg.reply_to in history_by_id:
        original = history_by_id[msg.reply_to]
        snippet = original.text if not original.deleted else L["deleted"]
        if len(snippet) > 80:
            snippet = snippet[:77] + "..."
        quote_html = (
            f'<div class="msg-quote">'
            f'<span class="quoted-sender">{html.escape(original.sender)}</span>: '
            f'{html.escape(snippet)}'
            f'</div>'
        )

    if msg.deleted:
        return (
            f'<div class="msg-row {align}">'
            f'<div class="msg-bubble {align} deleted" data-msgid="{html.escape(msg.id)}">'
            f'{html.escape(L["deleted"])}'
            f'<span class="msg-time">{_format_ts(msg.ts)}</span>'
            f'</div></div>'
        )

    edited_tag = (
        f'<span class="msg-edited">{html.escape(L["edited"])}</span>'
        if msg.edited else ""
    )
    via_tag = (
        f'<span class="msg-via">via {html.escape(msg.delivered_via)}</span>'
        if msg.delivered_via else ""
    )
    tick_tag = _tick_html(msg)
    return (
        f'<div class="msg-row {align}">'
        f'<div class="msg-bubble {align}" data-msgid="{html.escape(msg.id)}">'
        f'{quote_html}'
        f'{html.escape(msg.text)}'
        f'<span class="msg-time">{_format_ts(msg.ts)}{via_tag}{edited_tag}{tick_tag}</span>'
        f'</div></div>'
    )


def _tick_html(msg: StoredMessage) -> str:
    """Telegram-style delivery indicator. Only outbound messages get one.

    Two states, rendered next to the timestamp:

    * ✓        — message reached the server (``status`` is ``sent`` or
                 ``delivered``).
    * ✓✓ blue  — recipient opened the chat (``status`` is ``read``).
                 The tooltip includes the read time when available.
    """
    if not msg.outbound or msg.deleted:
        return ""
    if msg.status == "read":
        when = (
            f"Read {_format_ts(msg.read_at)} / ဖတ်ပြီး {_format_ts(msg.read_at)}"
            if msg.read_at else "Read / ဖတ်ပြီး"
        )
        return (
            f'<span class="msg-tick read" title="{html.escape(when)}">'
            f'\u2713\u2713</span>'
        )
    return (
        '<span class="msg-tick" title="Sent / ပို့ပြီး">\u2713</span>'
    )


def _render_action_popover(service: ChatService, msg: StoredMessage) -> None:
    """Per-message context menu, opened by long-press or by the ⋯ handle."""
    with st.popover("\u22EF", use_container_width=False):
        st.caption(_format_ts(msg.ts))
        if st.button(
            f"\u21A9\uFE0F  {L['reply']}",
            key=f"reply-{msg.id}",
            use_container_width=True,
        ):
            st.session_state.reply_to = msg.id
            st.rerun()
        if msg.outbound:
            if st.button(
                f"\u270F\uFE0F  {L['edit']}",
                key=f"edit-{msg.id}",
                use_container_width=True,
            ):
                st.session_state.editing_id = msg.id
                st.rerun()
            if st.button(
                f"\U0001F5D1  {L['delete']}",
                key=f"del-{msg.id}",
                use_container_width=True,
                type="primary",
            ):
                service.delete_message(msg.id)
                if st.session_state.get("reply_to") == msg.id:
                    st.session_state.reply_to = None
                st.rerun()


def _render_edit_mode(service: ChatService, msg: StoredMessage) -> None:
    st.markdown(
        '<div class="msg-row sent"><div class="msg-bubble sent" '
        'style="background:#FEF3C7;">'
        f'<b>{html.escape(L["edit"])}</b>'
        '</div></div>',
        unsafe_allow_html=True,
    )
    new_text = st.text_area(
        L["edit"],
        value=msg.text,
        key=f"edit-text-{msg.id}",
        label_visibility="collapsed",
    )
    cols = st.columns([6, 2, 2])
    with cols[1]:
        if st.button(L["save"], key=f"save-{msg.id}", use_container_width=True, type="primary"):
            if new_text.strip() and new_text != msg.text:
                service.edit_message(msg.id, new_text)
            st.session_state.editing_id = None
            st.rerun()
    with cols[2]:
        if st.button(L["cancel"], key=f"cancel-{msg.id}", use_container_width=True):
            st.session_state.editing_id = None
            st.rerun()


def _render_reply_banner(history_by_id: dict[str, StoredMessage]) -> None:
    rid = st.session_state.get("reply_to")
    if not rid or rid not in history_by_id:
        return
    target = history_by_id[rid]
    snippet = target.text if not target.deleted else L["deleted"]
    if len(snippet) > 100:
        snippet = snippet[:97] + "..."
    cols = st.columns([10, 1])
    with cols[0]:
        st.markdown(
            f'<div class="reply-banner">'
            f'<b>{html.escape(L["replying_to"])}</b> '
            f'<i>{html.escape(target.sender)}</i>: '
            f'{html.escape(snippet)}'
            f'</div>',
            unsafe_allow_html=True,
        )
    with cols[1]:
        if st.button(L["cancel"], key="cancel-reply"):
            st.session_state.reply_to = None
            st.rerun()


# --- helpers ---------------------------------------------------------------


def _format_ts(ts: float) -> str:
    """12-hour wall-clock time, e.g. ``4:15 PM``."""
    lt = time.localtime(ts)
    try:
        return time.strftime("%-I:%M %p", lt)
    except ValueError:
        # Some libc builds (notably musl/Windows) reject the %-I flag.
        s = time.strftime("%I:%M %p", lt)
        return s.lstrip("0") or "12:00 AM"


if __name__ == "__main__":
    render()
