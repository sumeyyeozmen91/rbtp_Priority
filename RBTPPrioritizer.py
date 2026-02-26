import streamlit as st
import pandas as pd
import json
import io

st.set_page_config(page_title="STP_ANALYZE", layout="wide")

st.title("STP_ANALYZE – Semantic Test Prioritization")

PRIO_ORDER = ["Gating", "High", "Medium", "Low"]

# ---------------------------------------------------
# Helpers
# ---------------------------------------------------

def safe(x):
    return "" if pd.isna(x) else str(x)

def pick_column(df, candidates):

    cols = {c.lower(): c for c in df.columns}

    for c in candidates:

        if c.lower() in cols:
            return cols[c.lower()]

    return None


def parse_steps(x):

    t = safe(x)

    if not t.startswith("["):
        return t

    try:

        data = json.loads(t)

        out = []

        for s in data:

            f = s.get("fields", {})

            out.append(f.get("Action",""))

            out.append(f.get("Expected Result",""))

        return " ".join(out)

    except:

        return t


# ---------------------------------------------------
# STP Engine
# ---------------------------------------------------

def stp(summary, repo, steps, expected):

    text = f"{summary} {repo} {steps} {expected}".lower()

    # ----------------
    # GATING
    # ----------------

    if any(k in text for k in [

        "send message",
        "receive message",
        "mesaj gönder",
        "mesaj al",
        "delivered",
        "read",
        "okundu"

    ]):

        return "Gating"


    if any(k in text for k in [

        "privacy",
        "encryption",
        "otp",
        "verification"

    ]):

        return "Gating"


    # ----------------
    # HIGH
    # ----------------

    if any(k in text for k in [

        "sticker",
        "emoji",
        "image",
        "video",
        "attachment",
        "forward",
        "reply",
        "delete"

    ]):

        return "High"


    # ----------------
    # LOW
    # ----------------

    if any(k in text for k in [

        "alignment",
        "font",
        "color",
        "icon",
        "ui"

    ]):

        return "Low"


    # ----------------
    # DEFAULT
    # ----------------

    return "Medium"



# ---------------------------------------------------
# Upload
# ---------------------------------------------------

file = st.file_uploader("CSV yükle")

if file:

    df = pd.read_csv(file, sep=";", dtype=str).fillna("")

    summary_col = pick_column(df, ["summary"])
    repo_col = pick_column(df, ["repository path"])
    steps_col = pick_column(df, ["manual test steps"])
    expected_col = pick_column(df, ["expected"])

    summaries = df[summary_col]
    repo = df[repo_col] if repo_col else ""
    steps = df[steps_col].apply(parse_steps) if steps_col else ""
    expected = df[expected_col] if expected_col else ""

    df["STP_Priority"] = [

        stp(

            summaries[i],
            repo[i] if repo_col else "",
            steps[i] if steps_col else "",
            expected[i] if expected_col else ""

        )

        for i in range(len(df))

    ]


    # --------------------------------
    # SUMMARY TABLE
    # --------------------------------

    counts = df["STP_Priority"].value_counts().reindex(PRIO_ORDER, fill_value=0)

    perc = (counts / len(df) * 100).round(1)

    summary = pd.DataFrame({

        "Count": counts,
        "Percent": perc

    })

    st.subheader("Summary")

    st.dataframe(summary)


    # --------------------------------
    # DOWNLOAD
    # --------------------------------

    csv = io.StringIO()

    df.to_csv(csv, sep=";", index=False)

    st.download_button(

        "Download STP Output",

        csv.getvalue(),

        "STP_Output.csv"

    )
