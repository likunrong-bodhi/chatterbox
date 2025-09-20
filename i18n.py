import gettext
import os

DOMAIN = "messages"
LOCALE_DIR = os.path.join(os.path.dirname(__file__), "locales")
DEFAULT_LANG = "en"

def install(lang: str | None = None) -> gettext.NullTranslations:
    lang = lang or DEFAULT_LANG
    trans = gettext.translation(
        DOMAIN, localedir=LOCALE_DIR, languages=[lang], fallback=True
    )
    trans.install()
    return trans

def available_languages() -> list[str]:
    if not os.path.isdir(LOCALE_DIR):
        return [DEFAULT_LANG]
    langs = []
    for code in os.listdir(LOCALE_DIR):
        p = os.path.join(LOCALE_DIR, code, "LC_MESSAGES", f"{DOMAIN}.mo")
        if os.path.isfile(p):
            langs.append(code)
    # Always include zh_CN and zh_TW if present
    for special in ["zh_CN", "zh_TW"]:
        if special in os.listdir(LOCALE_DIR) and special not in langs:
            langs.append(special)
    if DEFAULT_LANG not in langs:
        langs.insert(0, DEFAULT_LANG)
    return sorted(set(langs))
