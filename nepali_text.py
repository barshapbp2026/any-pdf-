"""
नेपाली PDF टेक्स्ट सुधार:
1) Preeti / Fontasy Himali / Himalb जस्ता पुराना ASCII फन्टको टेक्स्टलाई युनिकोडमा बदल्ने
2) खोजका लागि टेक्स्ट normalize गर्ने (स्पेस, मात्रा, अंक आदि)
"""
import re
import unicodedata

LEGACY_FONT_HINTS = ("preeti", "himali", "himalb", "fontasy", "kantipur", "sagarmatha", "pcs nepali", "aakriti")

# Preeti-मा आधारित म्यापिङ (Fontasy Himali पनि यही लेआउट प्रयोग गर्छ)
_MULTI = [
    ("qm", "क्र"), ("km", "फ"), ("Dm", "म्"), ("q", "त्र"), ("„", "द्ध"), ("‹", "ङ्घ"), ("§", "ट्ट"), ("¶", "ठ्ठ"),
    ("Q", "त्त"), ("Ø", "्र"), ("|", "्र"), ("«", "्र"), ("¥", "र्‍"), ("°", "ङ्ग"),
    ("´", "झ"), ("¿", "रू"), ("Í", "ङ्क"), ("Î", "ङ्ख"), ("å", "द्व"), ("ç", "ॐ"),
    ("¢", "द्घ"), ("£", "घ्"), ("¤", "झ्"), ("¡", "ज्ञ्"), ("…", "ऽ"), ("Ë", "ङ्ग"),
    ("Ü", "%"), ("‘", "ँ"), ("“", "ँ"), ("ˆ", "फ्"), ("Æ", "\u201d"), ("Ý", "ट्ठ"),
    ("Ö", "="), ("Ì", "न्न"), ("Ù", ";"), ("ß", "द्म"), ("Å", "हृ"), ("Ú", "\u2019"),
    ("Á", "?"), ("·", "ठ"),
]
_SINGLE = {
    "a": "ब", "b": "द", "c": "अ", "d": "म", "e": "भ", "f": "ा", "g": "न", "h": "ज",
    "i": "ष्", "j": "व", "k": "प", "l": "ि", "m": "ः", "n": "ल", "o": "य", "p": "उ",
    "r": "च", "s": "क", "t": "त", "u": "ग", "v": "ख", "w": "ध", "x": "ह", "y": "थ",
    "z": "श", "A": "ब्", "B": "द्य", "C": "ऋ", "D": "म्", "E": "भ्", "F": "ँ", "G": "न्",
    "H": "ज्", "I": "क्ष्", "J": "व्", "K": "प्", "L": "ी", "M": "ः", "N": "ल्", "O": "इ",
    "P": "ए", "R": "च्", "S": "क्", "T": "त्", "U": "ग्", "V": "ख्", "W": "ध्", "X": "ह्",
    "Y": "थ्", "Z": "श्", "`": "ञ", "~": "ञ्", "1": "ज्ञ", "2": "द्द", "3": "घ", "4": "द्ध",
    "5": "छ", "6": "ट", "7": "ठ", "8": "ड", "9": "ढ", "0": "ण", "!": "१", "@": "२",
    "#": "३", "$": "४", "%": "५", "^": "६", "&": "७", "*": "८", "(": "९", ")": "०",
    "-": "(", "_": ")", "+": "ं", "[": "ृ", "{": "र्", "]": "े", "}": "ै", "\\": "्",
    "'": "ु", '"': "ू", ";": "स", ":": "स्", ",": ",", ".": "।", "/": "र", "<": "?",
    ">": "श्र", "?": "रु", "=": ".", "÷": "/", "…": "‘", "Ÿ": "ट्ठ", "ª": "ङ", "®": "+",
    "Ý": "ट्ठ", "Ò": "\u201c", "æ": "\u201c", "Þ": "ङ्ख", "‰": "झ्", "Ê": "ज्ञ्",
}
_MATRA = "ािीुूृेैोौंःँ"


def convert_preeti(text: str) -> str:
    if not text:
        return text
    for a, b in _MULTI:
        text = text.replace(a, b)
    out = "".join(_SINGLE.get(ch, ch) for ch in text)
    # ि (इकार) Preeti मा अक्षरभन्दा अगाडि लेखिन्छ → पछाडि सार्ने (संयुक्ताक्षर सहित)
    out = re.sub(r"ि((?:.्)*[क-हक्षत्रज्ञ](?:़)?)", r"\1ि", out)
    # 'f]' = ो, 'f}' = ौ
    out = out.replace("ाे", "ो").replace("ाै", "ौ")
    # रेफ ({ = र्) अक्षर पछाडि आउँछ → अगाडि सार्ने
    out = re.sub(r"((?:.्)*.[" + _MATRA + r"]*)र्", r"र्\1", out)
    out = out.replace("अा", "आ").replace("आे", "ओ").replace("आै", "औ").replace("एे", "ऐ")
    out = out.replace("्ा", "")
    return out


_DEVA = re.compile(r"[\u0900-\u097f]")
_URL = re.compile(r"www\.|https?:|\.gov|\.np|@\w+\.")


def is_legacy_font(font_name: str) -> bool:
    n = (font_name or "").lower()
    return any(h in n for h in LEGACY_FONT_HINTS)


def page_text_unicode(page) -> str:
    """PyMuPDF पेजबाट टेक्स्ट निकाल्ने, पुराना फन्टको भागलाई युनिकोडमा बदलेर।"""
    lines = []
    d = page.get_text("dict")
    spans = [sp for b in d.get("blocks", []) for l in b.get("lines", []) for sp in l.get("spans", [])]
    # पेजमा पुरानो फन्ट छ भने, Times New Roman नाममा आएको टेक्स्ट पनि Preeti नै हुन सक्छ
    page_legacy = any(is_legacy_font(sp.get("font", "")) for sp in spans)
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            parts = []
            for span in line.get("spans", []):
                t = span.get("text", "")
                if is_legacy_font(span.get("font", "")) or (
                    page_legacy and not _DEVA.search(t) and not _URL.search(t)
                    and re.search(r"[a-zA-Z;'\]\[{}@#!]", t)):
                    t = convert_preeti(t)
                parts.append(t)
            lines.append("".join(parts))
    return "\n".join(lines)


_NEP_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")
# खोजका लागि हटाउने: स्पेस, ZWJ/ZWNJ, हलन्त, सबै मात्रा, चन्द्रबिन्दु/शिरबिन्दु, नुक्ता
# Remove whitespace, zero-width controls and punctuation, but KEEP vowel signs.
_STRIP = re.compile(r"[\s\u200b-\u200d\u0964\u0965,;:!?।॥()\[\]{}\"'“”‘’–—-]+")
_FOLD = str.maketrans({
    "श": "स", "ष": "स", "व": "ब", "ङ": "न", "ञ": "न", "ण": "न",
    "ई": "इ", "ी": "ि", "ऊ": "उ", "ू": "ु",
    "ऐ": "ए", "ै": "े", "औ": "ओ", "ौ": "ो", "आ": "अ",
    "ँ": "ं", "़": None
})


def search_key(text: str) -> str:
    """Nepali search normalization: consonant variants, vowel length and Unicode variants."""
    t = unicodedata.normalize("NFC", text or "").lower().translate(_NEP_DIGITS)
    t = _STRIP.sub("", t)
    return t.translate(_FOLD)
