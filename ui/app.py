import base64
import html
import json
import os
import re

import requests
import streamlit as st

TIMEOUT = 90  # a sleeping free-tier server can take up to a minute to wake up


def read_api_url() -> str:
    """Where the API lives: Streamlit secrets when deployed, otherwise the environment."""
    try:
        value = st.secrets["EXAMPREP_API_URL"]
    except Exception:  # no secrets file, or the key is missing
        value = os.getenv("EXAMPREP_API_URL", "https://examprep-ai-5n25.onrender.com")
    return str(value).strip().rstrip("/")


st.set_page_config(page_title="VisaPrep AI", page_icon="🛂", layout="wide")

API_URL = read_api_url()

# Layout tweaks: user messages on the right and AI messages on the left (no avatar
# icons), plain history items without background or border, and a profile card.
st.markdown(
    """
    <style>
    [data-testid="stChatMessageAvatarUser"],
    [data-testid="stChatMessageAvatarAssistant"] {
        display: none;
    }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        flex-direction: row-reverse;
        text-align: right;
    }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
        [data-testid="stChatMessageContent"] {
        flex: 0 1 auto;
        max-width: 75%;
        margin-left: auto;
        margin-right: 0;
    }
    [class*="st-key-session_"] button,
    [class*="st-key-plan_"] button {
        background: transparent;
        border: none;
        box-shadow: none;
        justify-content: flex-start;
        padding-left: 0;
    }
    [class*="st-key-session_"] button:hover,
    [class*="st-key-plan_"] button:hover {
        background: transparent;
        opacity: 0.7;
    }
    [class*="st-key-session_"] button > div,
    [class*="st-key-plan_"] button > div {
        justify-content: flex-start;
    }
    [class*="st-key-session_"] button p,
    [class*="st-key-plan_"] button p {
        text-align: left;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    [class*="st-key-session_cur_"] button p,
    [class*="st-key-plan_cur_"] button p {
        font-weight: 700;
    }
    [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {
        min-height: calc(100vh - 8rem);
    }
    [data-testid="stSidebarContent"],
    [data-testid="stSidebarUserContent"],
    [data-testid="stSidebarUserContent"] > div,
    [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {
        background-color: inherit;
    }
    [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"]
        > [data-testid="stLayoutWrapper"]:has(> .st-key-profile) {
        position: sticky;
        bottom: 0;
        margin-top: auto;
        padding: 0.5rem 0;
        background-color: inherit;
        z-index: 1;
    }
    .st-key-profile button {
        justify-content: flex-start;
    }
    .st-key-profile button::before {
        content: "";
        flex: none;
        width: 28px;
        height: 28px;
        border-radius: 50%;
        background: var(--avatar-image) center / cover no-repeat;
        margin-right: 0.6rem;
    }
    .st-key-profile button p {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


AVATAR_COLORS = ["#E4572E", "#17BEBB", "#7B6CF6", "#F2A541", "#3D9970", "#D6457E"]


def avatar_uri(email: str) -> str:
    """A round avatar image (SVG) showing the first letter of the email name."""
    name = email.split("@")[0].strip()
    letter = next((char for char in name if char.isalnum()), "?").upper()
    color = AVATAR_COLORS[ord(letter) % len(AVATAR_COLORS)]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" '
        'viewBox="0 0 64 64">'
        f'<circle cx="32" cy="32" r="32" fill="{color}"/>'
        '<text x="32" y="32" text-anchor="middle" dominant-baseline="central" '
        'font-family="Arial, sans-serif" font-size="30" font-weight="600" '
        f'fill="#ffffff">{html.escape(letter)}</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def escape_markdown(text: str) -> str:
    """Backslash-escape characters that Streamlit's markdown would interpret."""
    return re.sub(r"([\\`*_\[\]()#~:$<>])", r"\\\1", text)


def render_profile_card(email: str):
    """Bottom-of-sidebar card: round letter avatar and name; click for email + Logout."""
    name = email.split("@")[0]
    st.sidebar.markdown(
        f'<style>.st-key-profile {{ --avatar-image: url("{avatar_uri(email)}"); }}</style>',
        unsafe_allow_html=True,
    )
    with st.sidebar.container(key="profile"):
        with st.popover(escape_markdown(name), use_container_width=True):
            st.caption("Signed in as")
            st.text(email)
            if st.button("Logout", key="logout", use_container_width=True):
                logout()
                st.rerun()


# ---------- session state ----------
def init_state():
    defaults = {
        "token": None,  # JWT; None means not logged in
        "email": None,
        "mode": "Chat",  # "Chat" or "Visa" (Visa needs a login)
        "messages": [],  # [{"role": "user"/"assistant", "content": "..."}]
        "session_id": None,  # str (uuid) for guests, int for logged-in users
        "visa_plan": None,  # the visa plan being viewed (detail response)
        "pending_email": None,  # sign-up waiting for its email code
        "register_done": None,  # email that just finished verification
        "show_forgot": False,  # login tab shows the "forgot password" form
        "reset_done": False,  # a password reset just succeeded
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_state()


# ---------- API helpers ----------
def error_text(response: requests.Response) -> str:
    """Extract a readable message from a FastAPI error response."""
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list) and detail:  # 422 validation errors come as a list
        first = detail[0]
        if isinstance(first, dict) and first.get("msg"):
            field = str(first["loc"][-1]) if first.get("loc") else "input"
            return f"{field}: {first['msg']}"
        return "Invalid input. Please check your data."
    return f"Request failed ({response.status_code})"


def api_request(
    method: str, path: str, **kwargs
) -> tuple[requests.Response | None, str | None]:
    """Every API call goes through here. Returns (response, error_message)."""
    headers = kwargs.pop("headers", {})
    if st.session_state.token:
        headers["Authorization"] = f"Bearer {st.session_state.token}"
    try:
        response = requests.request(
            method, f"{API_URL}{path}", headers=headers, timeout=TIMEOUT, **kwargs
        )
    except requests.exceptions.ConnectionError:
        return None, f"Cannot reach the API at {API_URL}. Is the server running?"
    except requests.exceptions.Timeout:
        return None, "The request timed out. Please try again."

    if response.status_code == 401 and st.session_state.token:
        logout()  # the token has expired
        return None, "Your session expired. Please log in again."
    if not response.ok:
        return None, error_text(response)
    return response, None


def logout():
    st.session_state.token = None
    st.session_state.email = None
    new_chat()
    new_plan()


def new_chat():
    st.session_state.messages = []
    st.session_state.session_id = None


def new_plan():
    st.session_state.visa_plan = None


def load_session(session_id: int):
    response, error = api_request("GET", f"/chat/sessions/{session_id}")
    if response is None:
        st.sidebar.error(error)
        return
    data = response.json()
    st.session_state.session_id = data["id"]
    st.session_state.messages = [
        {"role": m["role"], "content": m["content"]} for m in data["messages"]
    ]


def open_plan(plan_id: int):
    """Open a saved visa plan, freshly fetched so checklist ticks are current."""
    response, error = api_request("GET", f"/visa/plans/{plan_id}")
    if response is None:
        st.sidebar.error(error)
        return
    st.session_state.visa_plan = response.json()


def stream_message(text: str) -> str | None:
    """Send the message and stream the reply into the page as it arrives.

    Returns an error message, or None on success. On success both the user message
    and the full reply are added to the chat history.
    """
    if st.session_state.token:
        path = "/chat/authenticated/stream"
    else:
        path = "/chat/guest/stream"
    payload = {"message": text, "session_id": st.session_state.session_id}

    with st.spinner(
        "Thinking... (if the server was asleep, the first reply can take up to a minute)"
    ):
        response, error = api_request("POST", path, json=payload, stream=True)
    if response is None:
        return error
    response.encoding = "utf-8"

    problem: dict[str, str | None] = {"message": None}

    def pieces():
        """Yield the text pieces of the NDJSON stream; remember an error if one comes."""
        try:
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                event = json.loads(line)
                if event["type"] == "delta":
                    yield event["text"]
                elif event["type"] == "error":
                    problem["message"] = event["detail"]
                    return
        except requests.exceptions.RequestException:
            problem["message"] = "The connection was lost while the reply was coming."
        finally:
            response.close()

    reply = str(st.write_stream(pieces()))
    if problem["message"]:
        return problem["message"]

    session_id = response.headers["X-Session-Id"]
    st.session_state.session_id = (
        int(session_id) if st.session_state.token else session_id
    )
    st.session_state.messages.append({"role": "user", "content": text})
    st.session_state.messages.append({"role": "assistant", "content": reply})
    return None


# ---------- sidebar ----------
def render_login_tab():
    if st.session_state.show_forgot:
        render_forgot_form()
        return

    with st.form("login_form"):
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        submitted = st.form_submit_button("Login")
    if submitted:
        response, error = api_request(
            "POST", "/auth/login", json={"email": email, "password": password}
        )
        if response is None:
            st.error(error)
        else:
            st.session_state.token = response.json()["access_token"]
            st.session_state.email = email
            new_chat()  # guest chat is separate, start fresh after login
            st.rerun()  # rerun the whole app, which also closes the dialog

    if st.button("Forgot password?", key="forgot_open", type="tertiary"):
        st.session_state.show_forgot = True
        st.rerun(scope="fragment")


def render_forgot_form():
    st.markdown("**Reset your password**")
    st.caption("Enter your account email and we will send you a reset link.")
    with st.form("forgot_form"):
        email = st.text_input("Email", key="forgot_email")
        submitted = st.form_submit_button("Send reset link")
    if submitted:
        if not email.strip():
            st.warning("Please enter your email.")
        else:
            response, error = api_request(
                "POST", "/auth/forgot-password", json={"email": email.strip()}
            )
            if response is None:
                st.error(error)
            else:
                st.success(response.json()["message"])

    if st.button("Back to login", key="forgot_back", type="tertiary"):
        st.session_state.show_forgot = False
        st.rerun(scope="fragment")


def render_register_tab():
    if st.session_state.register_done:
        st.success(
            f"{st.session_state.register_done} is verified and your account is ready. "
            "A welcome email is on its way. Please log in from the Login tab."
        )
        if st.button("Create another account", key="register_again", type="tertiary"):
            st.session_state.register_done = None
            st.rerun(scope="fragment")
        return

    if st.session_state.pending_email:
        render_otp_step()
        return

    with st.form("register_form"):
        email = st.text_input("Email", key="register_email")
        password = st.text_input("Password", type="password", key="register_password")
        confirm = st.text_input(
            "Confirm password", type="password", key="register_confirm"
        )
        st.caption("Password must be at least 8 characters.")
        submitted = st.form_submit_button("Create account")
    if submitted and password != confirm:
        st.error("Passwords do not match.")
    elif submitted:
        response, error = api_request(
            "POST", "/auth/register", json={"email": email, "password": password}
        )
        if response is None:
            st.error(error)
        elif response.status_code == 202:  # a code was emailed, waiting for it
            st.session_state.pending_email = email
            st.rerun(scope="fragment")
        else:  # email verification is switched off on the server
            st.success("Account created. Now switch to the Login tab.")


def render_otp_step():
    email = st.session_state.pending_email
    st.info(f"We sent a 6-digit code to {email}. It expires in 10 minutes.")
    with st.form("otp_form"):
        otp = st.text_input("6-digit code", max_chars=6, key="otp_code")
        submitted = st.form_submit_button("Verify email")
    if submitted:
        code = otp.strip()
        if not (code.isdigit() and len(code) == 6):
            st.warning("Please enter the 6-digit code from your email.")
        else:
            response, error = api_request(
                "POST", "/auth/verify-email", json={"email": email, "otp": code}
            )
            if response is None:
                st.error(error)
            else:
                st.session_state.register_done = email
                st.session_state.pending_email = None
                st.rerun(scope="fragment")

    left, right = st.columns(2)
    if left.button("Resend code", key="otp_resend", use_container_width=True):
        response, error = api_request("POST", "/auth/resend-otp", json={"email": email})
        if response is None:
            st.error(error)
        else:
            st.success(response.json()["message"])
    if right.button("Use another email", key="otp_other", use_container_width=True):
        st.session_state.pending_email = None
        st.rerun(scope="fragment")


@st.dialog("Welcome to VisaPrep AI")
def auth_dialog():
    """Popup with Login and Register tabs, opened from the sidebar Login button."""
    login_tab, register_tab = st.tabs(["Login", "Register"])
    with login_tab:
        render_login_tab()
    with register_tab:
        render_register_tab()


def render_history():
    if st.sidebar.button("➕ New chat", use_container_width=True):
        new_chat()
        st.rerun()

    st.sidebar.subheader("History")
    response, error = api_request("GET", "/chat/sessions")
    if response is None:
        st.sidebar.error(error)
        return
    sessions = response.json()
    if not sessions:
        st.sidebar.caption("No chats yet.")
    for item in sessions:
        label = item["title"] or f"Chat #{item['id']}"
        is_current = item["id"] == st.session_state.session_id
        if st.sidebar.button(
            label,
            key=f"session_cur_{item['id']}" if is_current else f"session_{item['id']}",
            use_container_width=True,
        ):
            load_session(item["id"])
            st.rerun()


def render_plan_list():
    if st.sidebar.button("➕ New visa plan", use_container_width=True):
        new_plan()
        st.rerun()

    st.sidebar.subheader("My visa plans")
    response, error = api_request("GET", "/visa/plans")
    if response is None:
        st.sidebar.error(error)
        return
    plans = response.json()
    if not plans:
        st.sidebar.caption("No visa plans yet.")

    current = st.session_state.visa_plan
    current_id = current["id"] if current else None

    for item in plans:
        status = (
            f"{item['documents_collected']}/{item['documents_total']} docs"
            if item["documents_total"]
            else "no checklist"
        )
        is_current = item["id"] == current_id
        if st.sidebar.button(
            f"{item['country']} · {item['purpose'].title()} · {status}",
            key=f"plan_cur_{item['id']}" if is_current else f"plan_{item['id']}",
            use_container_width=True,
        ):
            open_plan(item["id"])
            st.rerun()


def render_sidebar():
    st.sidebar.title("🛂 VisaPrep AI")
    if st.session_state.token:
        st.sidebar.radio(
            "Mode",
            ["Chat", "Visa"],
            key="mode",
            horizontal=True,
            label_visibility="collapsed",
            format_func=lambda mode: {
                "Chat": "💬 Chat",
                "Visa": "🛂 Visa",
            }[mode],
        )
        st.sidebar.divider()
        if st.session_state.mode == "Visa":
            render_plan_list()
        else:
            render_history()
        render_profile_card(st.session_state.email)
    else:
        st.sidebar.info("You are chatting as a guest. History is not saved.")
        if st.sidebar.button("➕ New chat", use_container_width=True):
            new_chat()
            st.rerun()
        st.sidebar.caption("Log in to save your chats and build visa plans.")
        st.sidebar.divider()
        if st.sidebar.button("🔑 Login", use_container_width=True):
            auth_dialog()


# ---------- chat mode ----------
def render_chat():
    st.title("VisaPrep AI")
    if not st.session_state.messages:
        st.caption("Ask anything about visas, travel, or your application to start.")

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Type your question...")
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            error = stream_message(prompt)
            if error:
                st.error(error)
        if not error:
            st.rerun()  # refresh the sidebar history and redraw the messages


# ---------- visa mode ----------
VISA_PURPOSES = [
    "tourism",
    "business",
    "work",
    "study",
    "family",
    "medical",
    "transit",
]


def render_plan_creator():
    st.title("Visa Application Plan")
    st.caption(
        "Tell us where you are going and about your situation, and AI will build "
        "a personalized roadmap, document checklist, and interview tips."
    )
    with st.form("create_plan_form"):
        country = st.text_input(
            "Destination country",
            placeholder="e.g. Germany",
            max_chars=100,
            help="The country you are applying to visit.",
        )
        nationality = st.text_input(
            "Your nationality",
            placeholder="e.g. Bangladeshi",
            max_chars=100,
            help="The passport you will travel with.",
        )
        left, middle, right = st.columns(3)
        purpose = left.selectbox("Purpose of travel", VISA_PURPOSES, index=0)
        travel_date = middle.text_input(
            "Planned travel date",
            placeholder="e.g. 2026-06-01",
            max_chars=40,
            help="Optional. Rough dates are fine.",
        )
        duration_of_stay = right.text_input(
            "Duration of stay",
            placeholder="e.g. 14 days",
            max_chars=40,
            help="Optional. How long you plan to stay.",
        )
        background = st.text_area(
            "Anything we should know about your situation?",
            placeholder=(
                "Optional. For example: previous visas, refusals, employment, "
                "funds, family in the destination country..."
            ),
            max_chars=2000,
            height=120,
        )
        submitted = st.form_submit_button("Generate visa plan")

    if submitted:
        if len(country.strip()) < 2 or len(nationality.strip()) < 2:
            st.warning("Please enter your destination country and nationality.")
            return
        with st.spinner("Building your visa plan..."):
            response, error = api_request(
                "POST",
                "/visa/plans",
                json={
                    "country": country.strip(),
                    "nationality": nationality.strip(),
                    "purpose": purpose,
                    "travel_date": travel_date.strip() or None,
                    "duration_of_stay": duration_of_stay.strip() or None,
                    "background": background.strip() or None,
                },
            )
        if response is None:
            st.error(error)
            return
        st.session_state.visa_plan = response.json()
        st.rerun()


def toggle_document(plan: dict, document: dict, collected: bool) -> bool:
    """Persist one checklist tick. Returns True when the server accepted it."""
    response, error = api_request(
        "PATCH",
        f"/visa/plans/{plan['id']}/documents/{document['id']}",
        json={"collected": collected},
    )
    if response is None:
        st.error(error)
        return False
    document["collected"] = response.json()["collected"]
    return True


def render_plan_detail(plan: dict):
    title = f"{plan['country']} · {plan['purpose'].title()} visa"
    st.title(title)
    meta_bits = [f"Nationality: {plan['nationality']}"]
    if plan.get("travel_date"):
        meta_bits.append(f"Travel: {plan['travel_date']}")
    if plan.get("duration_of_stay"):
        meta_bits.append(f"Stay: {plan['duration_of_stay']}")
    st.caption(" · ".join(meta_bits))

    st.subheader("Overview")
    st.write(plan["overview"])

    st.subheader("Roadmap")
    for step in plan["roadmap"]:
        st.markdown(f"**{step['step']}. {step['title']}**")
        st.caption(step["detail"])

    st.subheader("Document checklist")
    st.caption("Tick documents off as you collect them — progress is saved.")
    if not plan["documents"]:
        st.info("No documents were returned for this plan.")
    for document in plan["documents"]:
        label = document["title"]
        if not document["is_required"]:
            label += " *(optional)*"
        checked = st.checkbox(
            label,
            value=document["collected"],
            key=f"doc_{document['id']}",
            help=f"{document['category']} — {document['description']}",
        )
        if checked != document["collected"] and not toggle_document(
            plan, document, checked
        ):
            # the server refused the change; drop the widget state so the
            # stored value is shown again on the next run
            st.session_state.pop(f"doc_{document['id']}", None)
            st.rerun()

    collected = sum(1 for d in plan["documents"] if d["collected"])
    if plan["documents"]:
        st.progress(
            collected / len(plan["documents"]),
            text=f"{collected} of {len(plan['documents'])} documents collected",
        )

    if plan.get("background"):
        with st.expander("Your situation notes"):
            st.write(plan["background"])

    if plan.get("interview_tips"):
        st.subheader("Interview tips")
        for tip in plan["interview_tips"]:
            st.markdown(f"- {tip}")

    st.caption(
        "AI-generated guidance — always confirm the final requirements on the "
        "official embassy or consulate website."
    )


def render_visa_mode():
    if st.session_state.visa_plan:
        render_plan_detail(st.session_state.visa_plan)
    else:
        render_plan_creator()


def render_reset_password(token: str):
    """Page opened from the link in the reset email."""
    st.title("Reset your password")
    st.caption("Choose a new password for your account.")
    with st.form("reset_form"):
        new_password = st.text_input("New password", type="password", key="reset_new")
        confirm = st.text_input(
            "Confirm new password", type="password", key="reset_confirm"
        )
        st.caption("Password must be at least 8 characters.")
        submitted = st.form_submit_button("Reset password")
    if submitted and new_password != confirm:
        st.error("Passwords do not match.")
    elif submitted:
        response, error = api_request(
            "POST",
            "/auth/reset-password",
            json={"token": token, "new_password": new_password},
        )
        if response is None:
            st.error(error)
        else:
            st.session_state.reset_done = True
            st.query_params.clear()  # drop the token from the address bar
            st.rerun()


render_sidebar()
reset_token = st.query_params.get("reset_token")
if reset_token:
    render_reset_password(reset_token)
else:
    if st.session_state.reset_done:
        st.session_state.reset_done = False
        st.success("Your password was updated. Please log in with the new password.")
    if st.session_state.token and st.session_state.mode == "Visa":
        render_visa_mode()
    else:
        render_chat()
