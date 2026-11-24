"""Hold string literals with characters outside ASCII."""

GREETINGS: dict[str, str] = {
    "fr": "Bonjour, ça va ?",
    "de": "Grüße aus Köln",
    "el": "Καλημέρα",
    "ja": "こんにちは",
    "ar": "مرحبا",
}
SYMBOLS = "Ω ≤ ∞ € → ✓"
# Unicode 15.0 and 15.1 assigned these characters, so repr() writes them differently on
# Python versions whose Unicode tables are older.
NEW_CHARACTERS = "\U0001fa75 \U0001fae8 \U0002ebf0"
WRITTEN_OUT = "🩵 🫨"
COMBINING = "é and \N{LATIN SMALL LETTER E WITH ACUTE}"
RAW = b"caf\xc3\xa9 \xff"
naïve_total = len(SYMBOLS) + len(WRITTEN_OUT)
