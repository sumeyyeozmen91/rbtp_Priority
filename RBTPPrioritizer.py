import streamlit as st
import pandas as pd
import io
import json

st.set_page_config(page_title="STP – Semantic Test Prioritization", layout="wide")

st.title("STP – Semantic Test Prioritization")
st.caption("CSV yükle → STP_Priority (Gating/High/Medium/Low) hesapla → Current vs STP karşılaştır → indir")

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
    Tek metne çevirir.
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

def move_cols_to_end(df, cols_in_order):
    cols = list(df.columns)
    for c in cols_in_order:
        if c in cols:
            cols.remove(c)
    cols.extend([c for c in cols_in_order if c in df.columns])
    return df[cols]

# -------------------------------
# STP (Semantic Test Prioritization) Engine
# (Test-case prioritization, NOT bug-based)
# -------------------------------
def _find_hits(text: str, groups: dict) -> list[str]:
    hits = []
    for name, pats in groups.items():
        for p in pats:
            if p in text:
                hits.append(name)
                break
    return hits

def stp_classify(summary: str, repo_path: str, steps: str, expected: str, status_core: bool=False):
    """
    Returns: (STP_Priority, STP_Reason, STP_RiskType, STP_MatchedSignals)
    """
    text = " | ".join([safe_text(summary), safe_text(repo_path), safe_text(steps), safe_text(expected)]).lower()

    # 1) Always Gating: privacy/security + data loss
    ALWAYS_GATING = {
        "Privacy/Security": [
            "privacy", "gizlilik", "unauthorized", "yetkisiz", "leak", "sız",
            "e2e", "encryption", "token", "authentication", "otp", "verification",
            "wrong person", "başkası görüyor", "everyone can see", "only contacts",
            "blocked", "engelle"
        ],
        "DataLoss/Backup": [
            "data loss", "lost", "kaybol", "silin", "deleted", "history",
            "backup", "restore", "icloud", "google drive"
        ]
    }
    hits = _find_hits(text, ALWAYS_GATING)
    if hits:
        if "Privacy/Security" in hits:
            return ("Gating", "Privacy/Security coverage is release-critical", "Privacy/Security", ", ".join(hits))
        return ("Gating", "Data loss / backup-restore coverage is release-critical", "DataLoss", ", ".join(hits))

    # 2) Core smoke: must-pass flows => Gating
    CORE_SMOKE = {
        "Login/OTP": ["login", "register", "otp", "sms", "verification", "doğrulama", "kayıt", "giriş"],
        "ChatSend/Receive": [
            "send message", "send text", "type message", "write message",
            "mesaj gönder", "mesaj yolla", "receive message", "mesaj al",
            "delivered", "read", "seen", "okundu"
        ],
        "CallConnect": [
            "start call", "make call", "incoming call", "outgoing call",
            "voice call", "video call", "answer call",
            "arama başlat", "sesli arama", "görüntülü arama", "çağrı", "aramayı yanıtla"
        ],
        "Notifications": ["push", "notification", "bildirim"]
    }
    smoke_hits = _find_hits(text, CORE_SMOKE)
    if smoke_hits:
        return ("Gating", "Core smoke (must-pass) scenario", "CoreSmoke", ", ".join(smoke_hits))

    # 3) High: important variations on core capability
    HIGH_VARIATIONS = {
        "NetworkVariation": ["offline", "airplane", "roaming", "wifi", "4g", "5g", "network", "internet", "uçak modu"],
        "Background/Lock": ["background", "foreground", "screen lock", "locked", "kilit", "ekran kilidi"],
        "MediaPayload": ["sticker", "gif", "emoji", "image", "photo", "video", "audio", "document", "attachment", "location", "lokasyon"],
        "MultiDevice/SIM": ["multi device", "dual sim"]
    }
    var_hits = _find_hits(text, HIGH_VARIATIONS)
    if var_hits:
        return ("High", "Important variation on core capability", "CoreVariation", ", ".join(var_hits))

    # 4) Medium: functional UI rules / interaction rules (edit mode, long press, visibility rules)
    UI_RULES = {
        "EditMode": ["edit mode", "editing", "edit message", "düzenle", "düzenleme", "edit"],
        "LongPress": ["long press", "press and hold", "uzun bas", "longpress"],
        "VisibilityRule": ["should not appear", "must not", "not displayed", "görünmemeli", "olmamalı", "disable", "disabled", "enabled"],
        "Navigation": ["back", "geri", "close", "kapat"]
    }
    ui_hits = _find_hits(text, UI_RULES)
    if ui_hits:
        return ("Medium", "Functional UI/interaction rule validation", "UIRule", ", ".join(ui_hits))

    # 5) Low: cosmetic only
    COSMETIC = {
        "Cosmetic/UI": ["alignment", "padding", "margin", "font", "typo", "spelling", "icon", "color", "theme", "animation", "ux", "ui", "çeviri", "metin"]
    }
    cos_hits = _find_hits(text, COSMETIC)
    if cos_hits:
        return ("Low", "Cosmetic / UI-only scenario", "Cosmetic", ", ".join(cos_hits))

    # optional: Status core knob
    if status_core and safe_text(repo_path).lower().startswith("/status"):
        return ("High", "Status treated as core area (fallback)", "StatusCore", "")

    # default
    return ("Medium", "Default functional coverage", "Default", "")

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
    st.info("Bir CSV yükleyince STP_Priority hesaplayıp indirilebilir dosya üreteceğim.")
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

if steps_col != "(yok)":
    steps_text = df[steps_col].map(parse_steps_maybe)
else:
    steps_text = pd.Series([""] * len(df))

if expected_col != "(yok)":
    expected_text = df[expected_col].map(safe_text)
else:
    expected_text = pd.Series([""] * len(df))

# Current priority normalization into one output column: Priority
if priority_col != "(yok)":
    df["Priority"] = df[priority_col].map(normalize_priority)
    if priority_col != "Priority" and priority_col in df.columns:
        df.drop(columns=[priority_col], inplace=True)
else:
    df["Priority"] = ""

# Compute STP
out_rows = []
for i in range(len(df)):
    pr, reason, rtype, matched = stp_classify(
        summaries.iat[i],
        repo_paths.iat[i],
        steps_text.iat[i],
        expected_text.iat[i],
        status_core=status_core
    )
    out_rows.append((pr, reason, rtype, matched))

df["STP_Priority"] = [r[0] for r in out_rows]
df["STP_Reason"] = [r[1] for r in out_rows]
df["STP_RiskType"] = [r[2] for r in out_rows]
df["STP_MatchedSignals"] = [r[3] for r in out_rows]

df["STP_Changed"] = (df["Priority"] != "") & (df["Priority"] != df["STP_Priority"])

# Put Priority + STP_Priority at the end
df = move_cols_to_end(df, ["Priority", "STP_Priority"])

# Preview
st.subheader("Önizleme")
st.dataframe(df.head(50), use_container_width=True)

# -------------------------------
# Distribution + change
# -------------------------------
st.subheader("Dağılım ve Değişim Analizi")

prev_counts = df["Priority"].fillna("").replace("", "(boş)").value_counts()
next_counts = df["STP_Priority"].fillna("").replace("", "(boş)").value_counts()

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

changed = int(df["STP_Changed"].sum())
total = len(df)
changed_rate = round(changed / total * 100, 1) if total else 0.0
with c2:
    st.metric("Değişen Senaryo", changed, f"{changed_rate}%")
with c3:
    st.metric("Aynı Kalan", total - changed, f"{round(100 - changed_rate, 1)}%")

st.write("Priority → STP_Priority geçiş matrisi")
transition = pd.crosstab(
    df["Priority"].replace("", "(boş)"),
    df["STP_Priority"].replace("", "(boş)")
).reindex(index=order, columns=order, fill_value=0)
st.dataframe(transition, use_container_width=True)

# Download
st.subheader("İndir")
out = io.StringIO()
df.to_csv(out, sep=";", index=False)
st.download_button(
    label="STP çıktısını indir (CSV ;)",
    data=out.getvalue().encode("utf-8"),
    file_name="STP_Output.csv",
    mime="text/csv"
)

st.caption("Not: Çıktı her zaman ';' ile yazılır (TR Excel uyumlu).")
