import streamlit as st
import pandas as pd
import io
import json

st.set_page_config(page_title="RBTP Prioritizer", layout="wide")

st.title("RBTP – Risk-Based Test Prioritization")
st.caption("CSV yükle → RBTP_Priority hesapla → Priority ile yan yana yerleştir → indir")

# -------------------------------
# Helpers
# -------------------------------
def safe_text(x):
    return "" if pd.isna(x) else str(x)

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
    return str(val).strip()

def pick_column(df, candidates):
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    return None

def parse_steps_maybe(json_text: str) -> str:
    t = safe_text(json_text).strip()
    if not t:
        return ""
    if not (t.startswith("[") or t.startswith("{")):
        return t
    try:
        obj = json.loads(t)
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
        if isinstance(obj, dict):
            return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return t
    return t

def reorder_priority_columns(df, priority_col, rbtp_col):
    cols = list(df.columns)
    if priority_col in cols and rbtp_col in cols:
        cols.remove(rbtp_col)
        cols.insert(cols.index(priority_col) + 1, rbtp_col)
    return df[cols]


# -------------------------------
# RBTP Engine (Discover/Channel WhatsApp-like)
# -------------------------------
def classify_rbtp(summary: str, repo_path: str, steps: str, expected: str):
    """
    WhatsApp-like table for Discover:
      - Discover open edilemiyor -> Gating
      - Search çalışmıyor        -> Gating  (no negative-word dependency)
      - Back çalışmıyor          -> Gating
      - Hero click çalışmıyor    -> Gating
      - Popular list yanlış      -> High
    Plus generic crash/privacy/core interaction fallbacks.
    """
    s = (summary or "").lower()
    p = (repo_path or "").lower()
    a = (steps or "").lower()
    e = (expected or "").lower()
    text = " | ".join([s, p, a, e])

    # 0) Crash / app unusable
    crash_kw = ["crash", "crashlytics", "fatal", "exception", "sigabrt",
                "çök", "çöküyor", "force close", "uygulama kapan", "kapanıyor"]
    if any(k in text for k in crash_kw):
        return ("Gating", "Crash detected → release blocker", "Crash")

    # 1) Privacy/Security
    privacy_kw = ["privacy", "gizlilik", "block", "blocked", "engelle", "engelled",
                  "yetkisiz", "unauthorized", "leak", "sız", "expose", "visibility", "wrong person"]
    if any(k in text for k in privacy_kw):
        return ("Gating", "Privacy/Security risk → must be Gating", "Privacy")

    # 2) Discover/Channel detection
    is_discover = (
        "/discover" in p
        or "/channel" in p
        or "discover" in text
        or "keşfet" in text
        or "channel" in text
        or "kanal" in text
    )

    if is_discover:
        # Cosmetic-only search cases (if explicitly UI/cosmetic)
        search_cosmetic = [
            "placeholder", "hint text", "ui", "color", "renk", "ikon", "icon",
            "alignment", "hizalama", "padding", "font", "spelling", "typo"
        ]

        # 2.1 Discover open broken -> Gating
        open_patterns = [
            "discover open", "open discover", "keşfet aç", "keşfet ekran",
            "discover tab", "keşfet tab", "start discover", "keşfete başla",
            "cannot open", "unable to open", "does not open", "won't open",
            "açılmıyor", "görüntülenemiyor", "not visible", "görünmüyor"
        ]
        if any(k in text for k in open_patterns):
            return ("Gating", "Discover access/open broken → Gating (core entry)", "DiscoverCore")

        # ✅ 2.2 Search scenarios -> default Gating (no negative keyword needed)
        is_search_case = (
            "/search" in p
            or " search" in f" {text}"
            or "arama" in text
            or "ara " in text
            or "arat" in text
        )
        if is_search_case and not any(k in text for k in search_cosmetic):
            return ("Gating", "Discover search is primary mechanism → Gating", "DiscoverCore")

        # 2.3 Back broken -> Gating (needs negative cue)
        back_patterns = ["back button", "geri", "navigate back", "geri dön", "geri buton", "landing page back"]
        back_negative = ["çalışmıyor", "doesn't work", "not working", "cannot", "can't",
                         "tıklanamaz", "stuck", "kill", "donuyor", "çıkamıyor"]
        if any(k in text for k in back_patterns) and any(n in text for n in back_negative):
            return ("Gating", "Back navigation broken → user can get stuck → Gating", "DiscoverCore")

        # 2.4 Hero click broken -> Gating (needs negative cue)
        hero_patterns = ["hero", "banner", "carousel", "slider", "kampanya", "duyuru"]
        click_patterns = ["click", "tap", "tıkla", "tıklama", "tıklan", "open", "navigate"]
        click_negative = ["çalışmıyor", "tıklanamaz", "doesn't work", "not working", "cannot", "can't"]
        if any(h in text for h in hero_patterns) and any(c in text for c in click_patterns) and any(n in text for n in click_negative):
            return ("Gating", "Hero click broken → key entry/engagement surface → Gating", "DiscoverCore")

        # 2.5 Popular list wrong -> High
        popular_patterns = ["popular", "popüler", "recommended", "önerilen", "featured", "öne çıkan"]
        wrong_patterns = ["wrong", "missing", "not shown", "doesn't appear", "görünmüyor",
                          "yanlış", "eklenen", "listede yok", "does not appear"]
        if any(k in text for k in popular_patterns) and any(w in text for w in wrong_patterns):
            return ("High", "Curation/placement issue (business/growth) → High", "ContentCuration")

        # 2.6 Other discover/channel management -> High
        manage_kw = ["follow", "unfollow", "subscribe", "unsubscribe", "create", "edit", "delete", "avatar", "name"]
        if any(k in text for k in manage_kw):
            return ("High", "Channel/Discover management interaction affected → High", "CoreInteraction")

        # 2.7 Default discover/channel -> Medium
        return ("Medium", "Non-blocking Discover/Channel scenario → Medium", "Enhancement")

    # 3) Generic fallback (non-discover)
    core_interaction_kw = [
        "reply", "forward", "delete", "edit", "attach", "camera", "gallery",
        "message", "chat", "call", "voice call", "video call"
    ]
    if any(k in text for k in core_interaction_kw):
        return ("High", "Core interaction degraded → High", "CoreInteraction")

    return ("Medium", "Default non-blocking scenario → Medium", "Enhancement")


# -------------------------------
# UI
# -------------------------------
with st.sidebar:
    st.header("Ayarlar")
    sep = st.selectbox("CSV ayıracı", options=[";", ","], index=0)
    st.markdown("---")
    st.subheader("Kolon eşleştirme")
    st.caption("Dosyan farklı kolon isimleri kullanıyorsa buradan seç.")

uploaded = st.file_uploader("CSV yükle", type=["csv"])

if not uploaded:
    st.info("Bir CSV yükleyince RBTP_Priority hesaplayıp indirilebilir dosya üreteceğim.")
    st.stop()

try:
    df = pd.read_csv(uploaded, sep=sep, dtype=str, keep_default_na=False)
except Exception as ex:
    st.error(f"CSV okunamadı: {ex}")
    st.stop()

st.success(f"Yüklendi: {len(df):,} satır, {len(df.columns)} kolon")

default_summary = pick_column(df, ["Summary"])
default_repo = pick_column(df, ["Custom field (Test Repository Path)", "Test Repository Path", "RepositoryPath", "Repository Path"])
default_steps = pick_column(df, ["Custom field (Manual Test Steps)", "Manual Test Steps", "Actions", "Action"])
default_expected = pick_column(df, ["Custom field (Scenario Expected Result)", "Scenario Expected Result", "Expected", "Expected Result"])
default_priority = pick_column(df, ["Priority"])

summary_col = st.sidebar.selectbox("Summary kolonu", options=df.columns,
                                  index=(df.columns.get_loc(default_summary) if default_summary else 0))
repo_col = st.sidebar.selectbox("RepositoryPath kolonu", options=df.columns,
                                index=(df.columns.get_loc(default_repo) if default_repo else 0))
steps_col = st.sidebar.selectbox("Actions/Steps kolonu", options=["(yok)"] + list(df.columns),
                                 index=(1 + df.columns.get_loc(default_steps) if default_steps else 0))
expected_col = st.sidebar.selectbox("Expected kolonu", options=["(yok)"] + list(df.columns),
                                    index=(1 + df.columns.get_loc(default_expected) if default_expected else 0))
priority_col = st.sidebar.selectbox("Mevcut Priority kolonu", options=["(yok)"] + list(df.columns),
                                    index=(1 + df.columns.get_loc(default_priority) if default_priority else 0))

summaries = df[summary_col].map(safe_text)
repo_paths = df[repo_col].map(safe_text)

if steps_col != "(yok)":
    steps_text = df[steps_col].map(parse_steps_maybe)
else:
    steps_text = pd.Series([""] * len(df))

if expected_col != "(yok)":
    expected_text = df[expected_col].map(safe_text)
else:
    expected_text = pd.Series([""] * len(df))

if priority_col != "(yok)":
    df["CurrentPriority"] = df[priority_col].map(normalize_priority)
else:
    df["CurrentPriority"] = ""

rbtp = [
    classify_rbtp(summaries.iat[i], repo_paths.iat[i], steps_text.iat[i], expected_text.iat[i])
    for i in range(len(df))
]

df["RBTP_Priority"] = [x[0] for x in rbtp]
df["RBTP_ChangeReason"] = [x[1] for x in rbtp]
df["RBTP_RiskType"] = [x[2] for x in rbtp]
df["RBTP_Changed"] = (df["CurrentPriority"] != "") & (df["CurrentPriority"] != df["RBTP_Priority"])

if priority_col != "(yok)":
    df = reorder_priority_columns(df, priority_col, "RBTP_Priority")

st.subheader("Önizleme (ilk 50 satır)")
st.dataframe(df.head(50), use_container_width=True)

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

st.subheader("İndir")
out = io.StringIO()
df.to_csv(out, sep=";", index=False)
st.download_button(
    label="RBTP çıktısını indir (CSV ;)",
    data=out.getvalue().encode("utf-8"),
    file_name="RBTP_Output.csv",
    mime="text/csv"
)

st.caption("Çıktı ';' ile üretilir (TR Excel uyumlu).")
