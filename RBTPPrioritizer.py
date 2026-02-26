import streamlit as st
import pandas as pd
import io
import json

st.set_page_config(page_title="RBTP Prioritizer", layout="wide")

st.title("RBTP – Risk-Based Test Prioritization")
st.caption("CSV yükle → RBTP_Priority hesapla (TEST senaryosu odaklı) → Priority ile yan yana en sona koy → karşılaştırmalı dağılım → indir")

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

# -------------------------------
# TEST CASE prioritization engine (NOT bug-based)
# Produces: Gating / High / Medium / Low
# -------------------------------
def classify_rbtp(summary: str, repo_path: str, steps: str, expected: str,
                  mode_status_core: bool = False):
    """
    Returns: (RBTP_Priority, RBTP_ChangeReason, RBTP_RiskType)
    """
    text = " | ".join([safe_text(summary), safe_text(repo_path), safe_text(steps), safe_text(expected)]).lower()

    # --- 1) Risk flags (always push up) ---
    privacy_security = [
        "privacy", "gizlilik", "unauthorized", "yetkisiz", "leak", "sız",
        "blocked", "engelle", "visibility", "wrong person", "başkası görüyor",
        "e2e", "encryption", "şifre", "token", "authentication", "otp", "verification"
    ]
    data_loss = [
        "data loss", "lost", "kaybol", "silin", "deleted", "history",
        "backup", "restore", "icloud", "google drive"
    ]
    if any(p in text for p in privacy_security):
        return ("Gating", "Security/Privacy coverage is release-critical", "Privacy/Security")
    if any(p in text for p in data_loss):
        return ("Gating", "Data loss / backup-restore coverage is release-critical", "DataLoss")

    # --- 2) Feature tier (core-ness) ---
    tier0_core = [
        "login", "register", "otp", "sms", "verification",
        "chat", "message", "send", "receive", "delivered", "read",
        "call", "voice call", "video call", "incoming call", "outgoing call",
        "notification", "push"
    ]
    tier1_major = [
        "group", "admin", "status", "story",
        "search", "contacts", "contact sync",
        "channel", "discovery"
    ]
    tier2_nice = [
        "sticker", "emoji", "reaction", "wallpaper", "theme", "dark mode",
        "animation", "ui", "ux", "icon", "font", "typo", "alignment", "padding", "color",
        "localization", "çeviri", "metin"
    ]

    def get_tier():
        if any(k in text for k in tier0_core): return 0
        if any(k in text for k in tier1_major): return 1
        if any(k in text for k in tier2_nice): return 2
        return 1  # unknown -> Medium bandına yakın

    tier = get_tier()

    # --- 3) Scenario type: Smoke vs Variation vs Cosmetic ---
    # NOTE: "open/click/tap" her testte geçtiği için smoke kelimeleri daha spesifik tutuldu.
    smoke_actions = [
        # must-pass flows
        "send message", "send", "receive", "delivered", "read",
        "start call", "make call", "answer call", "incoming call", "outgoing call",
        "login", "otp", "verification", "register"
    ]

    variation_flags = [
        # important variations
        "background", "foreground", "screen lock", "locked",
        "network", "offline", "airplane", "roaming", "wifi", "4g", "5g",
        "bluetooth", "headset", "speaker",
        "camera", "gallery", "attachment", "document", "location", "audio", "video",
        "multi device", "dual sim"
    ]

    cosmetic_flags = [
        "ui", "ux", "typo", "spelling", "alignment", "padding", "margin",
        "icon", "color", "theme", "animation", "font", "localization", "çeviri", "metin"
    ]

    is_cosmetic = any(k in text for k in cosmetic_flags)
    is_smoke = any(k in text for k in smoke_actions)
    is_variation = any(k in text for k in variation_flags)

    # optional: status core knob (keeps your existing switch)
    if mode_status_core and safe_text(repo_path).lower().startswith("/status"):
        if is_smoke:
            return ("Gating", "Status is CORE + smoke flow", "CoreSmoke")
        if is_variation and not is_cosmetic:
            return ("High", "Status core + important variation", "CoreVariation")
        if is_cosmetic:
            return ("Low", "Status cosmetic", "Cosmetic")
        return ("Medium", "Status default coverage", "Default")

    # --- 4) Map to priority (test-based) ---
    # Tier-0 smoke => Gating
    if tier == 0 and is_smoke:
        return ("Gating", "Tier-0 core smoke test (must-pass)", "CoreSmoke")

    # Tier-0 important variations => High
    if tier == 0 and is_variation and not is_cosmetic:
        return ("High", "Tier-0 core + important variation", "CoreVariation")

    # Tier-1 smoke => High
    if tier == 1 and is_smoke:
        return ("High", "Tier-1 major feature smoke", "MajorSmoke")

    # Cosmetic => Low (Tier-2 cosmetic)
    if tier == 2 and is_cosmetic:
        return ("Low", "Cosmetic / UX / UI test", "Cosmetic")

    # Tier-2 non-cosmetic => Medium
    if tier == 2:
        return ("Medium", "Nice-to-have area, not smoke", "Enhancement")

    # Default => Medium
    return ("Medium", "Default coverage", "Default")

def move_cols_to_end(df, cols_in_order):
    cols = list(df.columns)
    for c in cols_in_order:
        if c in cols:
            cols.remove(c)
    cols.extend([c for c in cols_in_order if c in df.columns])
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
steps_col = st.sidebar.selectbox(
    "Actions/Steps kolonu",
    options=["(yok)"] + list(df.columns),
    index=(1 + df.columns.get_loc(default_steps) if default_steps else 0)
)
expected_col = st.sidebar.selectbox(
    "Expected kolonu",
    options=["(yok)"] + list(df.columns),
    index=(1 + df.columns.get_loc(default_expected) if default_expected else 0)
)
priority_col = st.sidebar.selectbox(
    "Mevcut Priority kolonu",
    options=["(yok)"] + list(df.columns),
    index=(1 + df.columns.get_loc(default_priority) if default_priority else 0)
)

# Build derived texts
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

# Priority handling (single output column "Priority")
if priority_col != "(yok)":
    df["Priority"] = df[priority_col].map(normalize_priority)
    if priority_col != "Priority" and priority_col in df.columns:
        df.drop(columns=[priority_col], inplace=True)
else:
    df["Priority"] = ""

# Compute RBTP
rbtp = [
    classify_rbtp(summaries.iat[i], repo_paths.iat[i], steps_text.iat[i], expected_text.iat[i],
                  mode_status_core=status_core)
    for i in range(len(df))
]

df["RBTP_Priority"] = [x[0] for x in rbtp]
df["RBTP_ChangeReason"] = [x[1] for x in rbtp]
df["RBTP_RiskType"] = [x[2] for x in rbtp]
df["RBTP_Changed"] = (df["Priority"] != "") & (df["Priority"] != df["RBTP_Priority"])

# Move Priority + RBTP_Priority to the end (last two columns)
df = move_cols_to_end(df, ["Priority", "RBTP_Priority"])

# Preview
st.subheader("Önizleme")
st.dataframe(df.head(50), use_container_width=True)

# -------------------------------
# Dağılım + Değişim Analizi
# -------------------------------
st.subheader("Dağılım ve Değişim Analizi")

prev_counts = df["Priority"].fillna("").replace("", "(boş)").value_counts()
next_counts = df["RBTP_Priority"].fillna("").replace("", "(boş)").value_counts()

compare = pd.DataFrame({
    "Önceki Adet": prev_counts,
    "Sonraki Adet": next_counts
}).fillna(0).astype(int)

compare["Değişim (Adet)"] = compare["Sonraki Adet"] - compare["Önceki Adet"]
compare["Değişim (%)"] = (
    compare["Değişim (Adet)"] /
    compare["Önceki Adet"].replace(0, pd.NA)
) * 100
compare["Değişim (%)"] = compare["Değişim (%)"].round(1)

order = ["Gating", "High", "Medium", "Low", "(boş)"]
compare = compare.reindex([x for x in order if x in compare.index])

c1, c2, c3 = st.columns([2, 1, 1])

with c1:
    st.write("Önceki vs Sonraki (adet + fark + oran)")
    st.dataframe(compare, use_container_width=True)

changed = int(df["RBTP_Changed"].sum())
total = len(df)
changed_rate = round(changed / total * 100, 1) if total else 0.0

with c2:
    st.metric("Değişen Senaryo", changed, f"{changed_rate}%")

with c3:
    st.metric("Aynı Kalan", total - changed, f"{round(100 - changed_rate, 1)}%")

# Extra KPI: Gating'e yükselen/düşen
priority_norm = df["Priority"].replace("", "(boş)")
rbtp_norm = df["RBTP_Priority"].replace("", "(boş)")

up_to_gating = int(((priority_norm != "Gating") & (rbtp_norm == "Gating")).sum())
down_from_gating = int(((priority_norm == "Gating") & (rbtp_norm != "Gating")).sum())
net_gating = up_to_gating - down_from_gating

k1, k2, k3 = st.columns(3)
k1.metric("Gating'e yükselen", up_to_gating)
k2.metric("Gating'den düşen", down_from_gating)
k3.metric("Net Gating değişimi", net_gating)

# Transition Matrix
st.write("Priority → RBTP_Priority geçiş matrisi")
transition = pd.crosstab(
    priority_norm,
    rbtp_norm
).reindex(index=order, columns=order, fill_value=0)

st.dataframe(transition, use_container_width=True)

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

st.caption("Not: Çıktı her zaman ';' ile yazılır (TR Excel uyumlu).")
