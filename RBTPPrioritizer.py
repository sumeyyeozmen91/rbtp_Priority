import streamlit as st
import pandas as pd
import io
import json
import re

st.set_page_config(page_title="RBTP Prioritizer", layout="wide")

st.title("RBTP – Risk-Based Test Prioritization")
st.caption("CSV yükle → RBTP_Priority hesapla → Priority ile yan yana yerleştir → indir")

# -------------------------------
# Helpers
# -------------------------------
PRIO_ORDER = ["Gating", "High", "Medium", "Low"]

def normalize_priority(val: str) -> str:
    if val is None:
        return ""
    s = str(val).strip().lower()
    if s in ["p0", "gating"]:
        return "Gating"
    if s in ["p1", "high"]:
        return "High"
    if s in ["p2", "medium"]:
        return "Medium"
    if s in ["p3", "low"]:
        return "Low"
    # unknown -> keep original (but title-case like)
    return str(val).strip()

def pick_column(df, candidates):
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    return None

def safe_text(x):
    return "" if pd.isna(x) else str(x)

def parse_steps_maybe(json_text: str) -> str:
    """
    Xray 'Manual Test Steps' bazen JSON listesi (Action/Expected).
    Bunu tek metne çevirip risk kelimelerini yakalamayı kolaylaştırır.
    """
    t = safe_text(json_text).strip()
    if not t:
        return ""
    if not (t.startswith("[") or t.startswith("{")):
        return t

    try:
        obj = json.loads(t)
        # list of steps
        if isinstance(obj, list):
            lines = []
            for step in obj:
                fields = step.get("fields", {}) if isinstance(step, dict) else {}
                act = fields.get("Action", "")
                exp = fields.get("Expected Result", fields.get("Expected", ""))
                if act:
                    lines.append(f"ACTION: {act}")
                if exp:
                    lines.append(f"EXPECTED: {exp}")
            return "\n".join(lines)
        # dict
        if isinstance(obj, dict):
            return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return t

    return t

def classify_rbtp(summary: str, repo_path: str, steps: str, expected: str,
                  mode_status_core: bool = False):
    """
    RBTP decision engine.
    Returns: (RBTP_Priority, RBTP_ChangeReason, RBTP_RiskType)
    """
    text = " | ".join([summary, repo_path, steps, expected]).lower()

    # ---- hard gating patterns (crash/core/availability) ----
    crash_patterns = [
        "crash", "crashlytics", "fatal", "exception", "sigabrt",
        "çök", "çöküyor", "uygulama kapan", "force close"
    ]
    core_block_patterns = [
        "açılmıyor", "cannot open", "won't open", "unable to open",
        "donuyor", "freeze", "stuck", "infinite loading", "loading forever",
        "server error", "500", "bad gateway", "timeout", "timed out",
        "sqlite", "db error", "connection refused"
    ]

    if any(p in text for p in crash_patterns):
        return ("Gating", "Crash detected → release blocker", "Crash")

    if any(p in text for p in core_block_patterns):
        return ("Gating", "Core flow blocked / app unusable → release blocker", "CoreFlow")

    # ---- privacy/security gating patterns ----
    privacy_patterns = [
        "privacy", "gizlilik", "block", "blocked", "engelle", "engelled",
        "unauthorized", "yetkisiz", "leak", "sız", "expose", "visibility",
        "only contacts", "everyone can see", "başkası görüyor", "wrong person"
    ]
    if any(p in text for p in privacy_patterns):
        return ("Gating", "Privacy/Security risk → must be Gating", "Privacy")

    # ---- status core switch (senin kararın) ----
    # Eğer Status core ise: /Status altında core akışlar gating
    if mode_status_core and repo_path.lower().startswith("/status"):
        # enhancement inside status
        enh = ["sticker", "emoji", "reaction", "gif", "search", "filter", "animation", "animasyon"]
        if any(p in text for p in enh):
            return ("Medium", "Enhancement inside core Status → Medium", "Enhancement")
        if "reply" in text or "yanıt" in text:
            return ("High", "Interaction inside core Status → High", "CoreInteraction")
        return ("Gating", "Status is CORE → core status flow must be Gating", "CoreFlow")

    # ---- channels heuristics (senin Channel örneklerinle uyumlu) ----
    if repo_path.lower().startswith("/channels"):
        # consumption (open/view/play/download/receive attachments) => gating
        consumption = ["open", "view", "play", "download", "receive", "watch", "listen", "open the", "click on"]
        attachment_markers = ["attachment", "audio", "video", "document", "media", "zip", "pdf", "jpg", "png"]
        if any(p in text for p in consumption) and any(p in text for p in attachment_markers):
            return ("Gating", "Channel content consumption broken → Gating", "CoreFlow")

        management = ["create", "edit", "delete", "follow", "unfollow", "avatar", "name", "channel name"]
        if any(p in text for p in management):
            return ("High", "Channel management interaction impacted → High", "CoreInteraction")

        return ("Medium", "Non-blocking channel scenario → Medium", "Enhancement")

    # ---- generic core interaction ----
    high_patterns = [
        "reply", "forward", "delete", "edit", "copy", "pin",
        "attach", "camera", "gallery", "document", "location",
        "call", "voice call", "video call", "message", "chat"
    ]
    if any(p in text for p in high_patterns):
        return ("High", "Core interaction degraded → High", "CoreInteraction")

    # ---- default ----
    return ("Medium", "Default non-blocking scenario → Medium", "Enhancement")


def reorder_priority_columns(df, priority_col, rbtp_col):
    cols = list(df.columns)
    if priority_col in cols and rbtp_col in cols:
        cols.remove(rbtp_col)
        cols.insert(cols.index(priority_col) + 1, rbtp_col)
    return df[cols]


# -------------------------------
# UI
# -------------------------------
with st.sidebar:
    st.header("Ayarlar")
    sep = st.selectbox("CSV ayıracı", options=[";", ","], index=0)
    status_core = st.checkbox("Status CORE kabul et ( /Status core akışlar = Gating )", value=False)
    st.markdown("---")
    st.subheader("Kolon eşleştirme")
    st.caption("Dosyan farklı kolon isimleri kullanıyorsa buradan seç.")

uploaded = st.file_uploader("CSV yükle", type=["csv"])

if not uploaded:
    st.info("Bir CSV yükleyince RBTP_Priority hesaplayıp indirilebilir dosya üreteceğim.")
    st.stop()

# Read CSV
try:
    df = pd.read_csv(uploaded, sep=sep, dtype=str, keep_default_na=False)
except Exception as e:
    st.error(f"CSV okunamadı: {e}")
    st.stop()

st.success(f"Yüklendi: {len(df):,} satır, {len(df.columns)} kolon")

# Auto-detect columns
default_summary = pick_column(df, ["Summary", "summary"])
default_repo = pick_column(df, ["Custom field (Test Repository Path)", "Test Repository Path", "RepositoryPath", "Repository Path"])
default_steps = pick_column(df, ["Custom field (Manual Test Steps)", "Manual Test Steps", "Actions", "Action"])
default_expected = pick_column(df, ["Custom field (Scenario Expected Result)", "Scenario Expected Result", "Expected", "Expected Result"])
default_priority = pick_column(df, ["Priority", "CurrentPriority"])

# Let user override
summary_col = st.sidebar.selectbox("Summary kolonu", options=df.columns, index=(df.columns.get_loc(default_summary) if default_summary else 0))
repo_col = st.sidebar.selectbox("RepositoryPath kolonu", options=df.columns, index=(df.columns.get_loc(default_repo) if default_repo else 0))
steps_col = st.sidebar.selectbox("Actions/Steps kolonu", options=["(yok)"] + list(df.columns),
                                 index=(1 + df.columns.get_loc(default_steps) if default_steps else 0))
expected_col = st.sidebar.selectbox("Expected kolonu", options=["(yok)"] + list(df.columns),
                                    index=(1 + df.columns.get_loc(default_expected) if default_expected else 0))
priority_col = st.sidebar.selectbox("Mevcut Priority kolonu", options=["(yok)"] + list(df.columns),
                                    index=(1 + df.columns.get_loc(default_priority) if default_priority else 0))

# Build derived texts
summaries = df[summary_col].map(safe_text)
repo_paths = df[repo_col].map(safe_text)

steps_text = ""
if steps_col != "(yok)":
    steps_text = df[steps_col].map(parse_steps_maybe)
else:
    steps_text = pd.Series([""] * len(df))

expected_text = ""
if expected_col != "(yok)":
    expected_text = df[expected_col].map(safe_text)
else:
    expected_text = pd.Series([""] * len(df))

# Normalize current priority if exists
if priority_col != "(yok)":
    df["CurrentPriority"] = df[priority_col].map(normalize_priority)
else:
    df["CurrentPriority"] = ""

# Compute RBTP
rbtp = [
    classify_rbtp(summaries.iat[i], repo_paths.iat[i], steps_text.iat[i], expected_text.iat[i],
                  mode_status_core=status_core)
    for i in range(len(df))
]

df["RBTP_Priority"] = [x[0] for x in rbtp]
df["RBTP_ChangeReason"] = [x[1] for x in rbtp]
df["RBTP_RiskType"] = [x[2] for x in rbtp]

df["RBTP_Changed"] = (df["CurrentPriority"] != "") & (df["CurrentPriority"] != df["RBTP_Priority"])

# Move RBTP_Priority next to Priority (if provided)
if priority_col != "(yok)":
    df = reorder_priority_columns(df, priority_col, "RBTP_Priority")

# Preview
st.subheader("Önizleme")
st.dataframe(df.head(50), use_container_width=True)

# Stats
st.subheader("Dağılım")
c1, c2, c3 = st.columns(3)
with c1:
    st.write("RBTP_Priority dağılımı")
    st.write(df["RBTP_Priority"].value_counts())
with c2:
    st.write("RBTP_RiskType dağılımı")
    st.write(df["RBTP_RiskType"].value_counts())
with c3:
    if priority_col != "(yok)":
        st.write("Değişen (RBTP_Changed=True)")
        st.write(int(df["RBTP_Changed"].sum()))

# Download
st.subheader("İndir")
out = io.StringIO()
df.to_csv(out, sep=";", index=False)
st.download_button(
    label="RBTP çıktısını indir (CSV ;)",
    data=out.getvalue().encode("utf-8"),
    file_name="RBTP_Output.csv",
    mime="text/csv"
)

st.caption("Not: Çıktı her zaman ';' ile yazılır (TR Excel uyumlu). İstersen koddan değiştirebilirsin.")
