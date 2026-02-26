import streamlit as st
import pandas as pd
import io
import json
import re

st.set_page_config(page_title="RBTP Prioritizer", layout="wide")

st.title("RBTP – Risk-Based Test Prioritization (Semantic Heuristic)")
st.caption("CSV yükle → semantic heuristics ile RBTP_Priority hesapla → dağılım + geçiş → indir")

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

def move_cols_to_end(df, cols_in_order):
    cols = list(df.columns)
    for c in cols_in_order:
        if c in cols:
            cols.remove(c)
    cols.extend([c for c in cols_in_order if c in df.columns])
    return df[cols]

# -------------------------------
# Semantic-ish rule engine (test case prioritization)
# -------------------------------
def find_signals(text: str, patterns: dict) -> list[str]:
    hits = []
    for name, pats in patterns.items():
        for p in pats:
            if p in text:
                hits.append(name)
                break
    return hits

def classify_rbtp_semantic(summary: str, repo_path: str, steps: str, expected: str, status_core: bool=False):
    """
    Test scenario prioritization.
    Returns: (prio, reason, risktype, matched_signals:list[str])
    """
    text = " | ".join([safe_text(summary), safe_text(repo_path), safe_text(steps), safe_text(expected)]).lower()

    # --- signal dictionaries (substring-based) ---
    RISK_ALWAYS_GATING = {
        "Privacy/Security": [
            "privacy", "gizlilik", "unauthorized", "yetkisiz", "leak", "sız",
            "e2e", "encryption", "şifre", "token", "authentication", "otp",
            "wrong person", "başkası görüyor", "everyone can see", "only contacts",
            "blocked", "engelle"
        ],
        "DataLoss/Backup": [
            "data loss", "lost", "kaybol", "silin", "deleted", "history",
            "backup", "restore", "icloud", "google drive"
        ],
    }

    CORE_SMOKE = {
        "Login/OTP": ["login", "register", "otp", "sms", "verification", "doğrulama", "kayıt", "giriş"],
        "ChatTextSendReceive": [
            "send message", "send text", "type message", "write message",
            "mesaj gönder", "mesaj yolla", "mesaj al", "receive message", "delivered", "read"
        ],
        "CallConnect": [
            "start call", "make call", "call", "voice call", "video call", "incoming call", "outgoing call",
            "arama başlat", "sesli arama", "görüntülü arama", "çağrı"
        ],
        "PushNotification": ["push", "notification", "bildirim"]
    }

    VARIATIONS_HIGH = {
        "NetworkVariation": ["offline", "airplane", "roaming", "wifi", "4g", "5g", "network", "internet", "uçak modu"],
        "BackgroundLock": ["background", "foreground", "screen lock", "locked", "kilit", "ekran kilidi"],
        "MediaPayload": ["sticker", "emoji", "gif", "image", "photo", "video", "audio", "document", "attachment", "lokasyon", "location"]
    }

    UI_RULES_MEDIUM = {
        "EditModeRules": ["edit mode", "editing", "edit message", "düzenle", "düzenleme", "edit"],
        "LongPressMenu": ["long press", "press and hold", "uzun bas", "longpress"],
        "VisibilityRule": ["should not appear", "must not", "not displayed", "görünmemeli", "olmamalı", "disable", "disabled", "enabled"]
    }

    COSMETIC_LOW = {
        "Cosmetic/UI": ["alignment", "padding", "margin", "font", "typo", "spelling", "icon", "color", "theme", "animation", "ux", "ui", "çeviri", "metin"]
    }

    matched = []

    # 1) Always-gating risks
    risk_hits = find_signals(text, RISK_ALWAYS_GATING)
    if risk_hits:
        matched += risk_hits
        if "Privacy/Security" in risk_hits:
            return ("Gating", "Privacy/Security coverage is release-critical", "Privacy/Security", matched)
        return ("Gating", "Data loss / backup-restore coverage is release-critical", "DataLoss", matched)

    # 2) Core smoke => Gating
    smoke_hits = find_signals(text, CORE_SMOKE)
    if smoke_hits:
        matched += smoke_hits
        return ("Gating", "Core smoke (must-pass) scenario", "CoreSmoke", matched)

    # 3) Important variations on core features => High
    var_hits = find_signals(text, VARIATIONS_HIGH)
    if var_hits:
        matched += var_hits
        return ("High", "Important variation on core capability", "CoreVariation", matched)

    # 4) UI/interaction rule validation (functional but not release-blocker) => Medium
    ui_hits = find_signals(text, UI_RULES_MEDIUM)
    if ui_hits:
        matched += ui_hits
        return ("Medium", "Functional UI/interaction rule validation", "UIRule", matched)

    # 5) Pure cosmetic => Low
    cos_hits = find_signals(text, COSMETIC_LOW)
    if cos_hits:
        matched += cos_hits
        return ("Low", "Cosmetic / UI-only scenario", "Cosmetic", matched)

    # 6) Status core override (optional)
    if status_core and safe_text(repo_path).lower().startswith("/status"):
        return ("High", "Status treated as core area (fallback)", "StatusCore", matched)

    # default
    return ("Medium", "Default functional coverage", "Default", matched)

# -------------------------------
# UI
# -------------------------------
with st.sidebar:
    st.header("Ayarlar")
    sep = st.selectbox("CSV ayıracı", options=[";", ","], index=0)
    status_core = st.checkbox("Status CORE kabul et", value=False)
    st.markdown("---")
    st.subheader("Kolon eşleştirme")
    st.caption("Dosyan farklı kolon isimleri kullanıyorsa buradan seç.")

uploaded = st.file_uploader("CSV yükle", type=["csv"])

if not uploaded:
    st.info("Bir CSV yükleyince RBTP_Priority hesaplayıp indirilebilir dosya üreteceğim.")
    st.stop()

try:
    df = pd.read_csv(uploaded, sep=sep, dtype=str, keep_default_na=False)
except Exception as e:
    st.error(f"CSV okunamadı: {e}")
    st.stop()

st.success(f"Yüklendi: {len(df):,} satır, {len(df.columns)} kolon")

default_summary = pick_column(df, ["Summary", "summary"])
default_repo = pick_column(df, ["Custom field (Test Repository Path)", "Test Repository Path", "RepositoryPath", "Repository Path"])
default_steps = pick_column(df, ["Custom field (Manual Test Steps)", "Manual Test Steps", "Actions", "Action"])
default_expected = pick_column(df, ["Custom field (Scenario Expected Result)", "Scenario Expected Result", "Expected", "Expected Result"])
default_priority = pick_column(df, ["Priority", "CurrentPriority"])

summary_col = st.sidebar.selectbox("Summary kolonu", options=df.columns, index=(df.columns.get_loc(default_summary) if default_summary else 0))
repo_col = st.sidebar.selectbox("RepositoryPath kolonu", options=df.columns, index=(df.columns.get_loc(default_repo) if default_repo else 0))
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

# Priority output handling (single column named Priority; drop original selected column if different)
if priority_col != "(yok)":
    df["Priority"] = df[priority_col].map(normalize_priority)
    if priority_col != "Priority" and priority_col in df.columns:
        df.drop(columns=[priority_col], inplace=True)
else:
    df["Priority"] = ""

# Compute RBTP (semantic heuristics)
rows = []
for i in range(len(df)):
    pr, reason, rtype, matched = classify_rbtp_semantic(
        summaries.iat[i],
        repo_paths.iat[i],
        steps_text.iat[i],
        expected_text.iat[i],
        status_core=status_core
    )
    rows.append((pr, reason, rtype, ", ".join(matched)))

df["RBTP_Priority"] = [r[0] for r in rows]
df["RBTP_ChangeReason"] = [r[1] for r in rows]
df["RBTP_RiskType"] = [r[2] for r in rows]
df["RBTP_MatchedSignals"] = [r[3] for r in rows]
df["RBTP_Changed"] = (df["Priority"] != "") & (df["Priority"] != df["RBTP_Priority"])

# Move Priority + RBTP_Priority to the end (last two columns)
df = move_cols_to_end(df, ["Priority", "RBTP_Priority"])

# Preview
st.subheader("Önizleme")
st.dataframe(df.head(50), use_container_width=True)

# -------------------------------
# Distribution + change
# -------------------------------
st.subheader("Dağılım ve Değişim Analizi")

prev_counts = df["Priority"].fillna("").replace("", "(boş)").value_counts()
next_counts = df["RBTP_Priority"].fillna("").replace("", "(boş)").value_counts()

compare = pd.DataFrame({
    "Önceki Adet": prev_counts,
    "Sonraki Adet": next_counts
}).fillna(0).astype(int)

compare["Değişim (Adet)"] = compare["Sonraki Adet"] - compare["Önceki Adet"]
compare["Değişim (%)"] = (compare["Değişim (Adet)"] / compare["Önceki Adet"].replace(0, pd.NA)) * 100
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

st.write("Priority → RBTP_Priority geçiş matrisi")
transition = pd.crosstab(
    df["Priority"].replace("", "(boş)"),
    df["RBTP_Priority"].replace("", "(boş)")
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
