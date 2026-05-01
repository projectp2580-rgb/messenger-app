import streamlit as st

# ၁။ User တစ်ယောက်ချင်းစီအတွက် Session ခွဲခြင်း (ဒါရှိမှ တစ်ယောက်နဲ့တစ်ယောက် အကောင့်မရောမှာပါ)
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""

# ၂။ အကောင့်ဖွင့်ခြင်းနှင့် Login လုပ်ခြင်း UI
def show_login_page():
    st.title("Messenger App")
    st.subheader("အကောင့်အသစ်ပြုလုပ်ရန်")
    
    with st.container():
        new_user = st.text_input("Username (အမည်ရိုက်ပါ)", placeholder="ဥပမာ- K")
        if st.button("Start Using App"):
            if new_user:
                st.session_state.logged_in = True
                st.session_state.username = new_user
                st.rerun()
            else:
                st.error("ကျေးဇူးပြု၍ အမည်တစ်ခုခု ရိုက်ထည့်ပါ!")

# ၃။ App ရဲ့ Main UI (Login ဝင်ပြီးမှ ပေါ်မယ့်အပိုင်း)
def show_main_app():
    # Sidebar မှာ User နာမည်ပြခြင်း
    st.sidebar.title(f"👤 {st.session_state.username}")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.rerun()

    st.sidebar.divider()
    page = st.sidebar.radio("Navigation", ["Home", "Chats", "Settings"])

    if page == "Home":
        st.title(f"Welcome, {st.session_state.username}!")
        st.write("အခုဆိုရင် မင်းရဲ့ ကိုယ်ပိုင်အကောင့်နဲ့ App ကို သုံးနေပါပြီ။")
        st.info("ဒီ App မှာ တစ်ယောက်နဲ့တစ်ယောက် အကောင့်တွေ ရောမှာမဟုတ်တော့ပါဘူး။")

    elif page == "Chats":
        st.title("Messages")
        st.write("Chatting interface and message history will appear here.")
        # စာရိုက်တဲ့အကွက်
        st.chat_input("Write a message...")

# ၄။ Logic ကို စစ်ဆေးပြီး ဘယ် Page ပြရမလဲ ဆုံးဖြတ်ခြင်း
if not st.session_state.logged_in:
    show_login_page()
else:
    show_main_app()
