def classify_rbtp(summary: str, repo_path: str, steps: str, expected: str, mode_status_core: bool=False):
    """
    TEST CASE prioritization engine (NOT bug-based).
    Returns: (RBTP_Priority, RBTP_ChangeReason, RBTP_RiskType)
    """
    text = " | ".join([safe_text(summary), safe_text(repo_path), safe_text(steps), safe_text(expected)]).lower()

    # --- 1) Risk flags that always push up ---
    privacy_security = [
        "privacy", "gizlilik", "unauthorized", "yetkisiz", "leak", "sız",
        "blocked", "engelle", "visibility", "wrong person", "başkası görüyor",
        "e2e", "encryption", "şifre", "token", "authentication", "otp"
    ]
    data_loss = [
        "data loss", "lost", "kaybol", "silin", "deleted", "history", "backup", "restore"
    ]

    if any(p in text for p in privacy_security):
        return ("Gating", "Security/Privacy coverage is release-critical", "Privacy/Security")
    if any(p in text for p in data_loss):
        return ("Gating", "Data loss / backup-restore coverage is release-critical", "DataLoss")

    # --- 2) Feature tier (core-ness) ---
    tier0_core = [
        "login", "register", "otp", "verification",
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
        "animation", "ui", "ux", "icon", "font", "typo", "alignment", "padding", "color"
    ]

    def get_tier():
        if any(k in text for k in tier0_core): return 0
        if any(k in text for k in tier1_major): return 1
        if any(k in text for k in tier2_nice): return 2
        return 1  # unknown -> Medium bandına yakın

    tier = get_tier()

    # --- 3) Scenario type: Smoke vs Variation vs Cosmetic ---
    smoke_actions = [
        # must-pass flows
        "send message", "send", "receive", "delivered", "read",
        "start call", "make call", "call", "answer call",
        "login", "otp", "verification",
        "open app", "open", "launch"
    ]

    variation_flags = [
        # important variations that still matter a lot
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

    # optional: status core knob
    if mode_status_core and safe_text(repo_path).lower().startswith("/status"):
        # status smoke goes up
        if is_smoke:
            return ("Gating", "Status is CORE + smoke flow", "CoreSmoke")
        # variations high, cosmetic low/medium
        if is_variation and not is_cosmetic:
            return ("High", "Status core + important variation", "CoreVariation")
        if is_cosmetic:
            return ("Low", "Status cosmetic", "Cosmetic")
        return ("Medium", "Status default", "Default")

    # --- 4) Map to priority (test-based) ---
    # Tier-0 smoke => Gating
    if tier == 0 and is_smoke:
        return ("Gating", "Tier-0 core smoke test (must-pass)", "CoreSmoke")

    # Tier-0 variations => High
    if tier == 0 and is_variation and not is_cosmetic:
        return ("High", "Tier-0 core + important variation", "CoreVariation")

    # Tier-1 smoke => High (major feature smoke)
    if tier == 1 and is_smoke:
        return ("High", "Tier-1 major feature smoke", "MajorSmoke")

    # Cosmetic => Low (unless it is also core smoke, which already returned)
    if is_cosmetic and tier == 2:
        return ("Low", "Cosmetic / UX / UI test", "Cosmetic")

    # Tier-2 non-cosmetic (rare) => Medium
    if tier == 2:
        return ("Medium", "Nice-to-have area, not smoke", "Enhancement")

    # Default => Medium
    return ("Medium", "Default coverage", "Default")
