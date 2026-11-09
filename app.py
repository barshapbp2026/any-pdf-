import os
import re
import difflib
import requests
import time
from flask import Flask, render_template, request, jsonify
import fitz
from dotenv import load_dotenv
# अपडेट गरिएको nepali_text बाट clean_extracted_text पनि इम्पोर्ट गरिएको छ
from nepali_text import (page_text_unicode, search_key, convert_preeti, is_legacy_font, _DEVA, _URL, clean_extracted_text)
from sabda import search_headers
from anusuchi import search_anusuchi

load_dotenv() 
app = Flask(__name__)

GITHUB_REPO = "barshapbp2026/any-pdf-"
GITHUB_BRANCH = "main"

# Cache repository PDF paths to avoid repeated GitHub tree requests.
_PDF_LIST_CACHE = []
_PDF_LIST_CACHE_AT = 0.0
_PDF_LIST_CACHE_TTL = 300
_FALLBACK_PDF_LIST = []

def _ocr_numbered_headers(page, page_num, target_pattern, any_heading_pattern, digits, expected_prefix):
    """OCR fallback for scanned/image-only PDF pages, with visual style hints."""
    try:
        import pytesseract
        from PIL import Image
        pix = page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False)
        image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        try:
            ocr = pytesseract.image_to_data(
                image, lang="nep+eng", config="--psm 6",
                output_type=pytesseract.Output.DICT
            )
        except Exception:
            ocr = pytesseract.image_to_data(
                image, lang="eng", config="--psm 6",
                output_type=pytesseract.Output.DICT
            )

        grouped = {}
        count = len(ocr.get("text", []))
        for idx in range(count):
            word = (ocr["text"][idx] or "").strip()
            if not word:
                continue
            key = (
                ocr.get("block_num", [0] * count)[idx],
                ocr.get("par_num", [0] * count)[idx],
                ocr.get("line_num", [0] * count)[idx]
            )
            grouped.setdefault(key, []).append({
                "text": word,
                "left": int(ocr["left"][idx]),
                "top": int(ocr["top"][idx]),
                "width": int(ocr["width"][idx]),
                "height": int(ocr["height"][idx]),
                "conf": float(ocr.get("conf", ["0"] * count)[idx] or 0)
            })

        lines = []
        for words in grouped.values():
            words.sort(key=lambda w: w["left"])
            text = " ".join(w["text"] for w in words)
            text = re.sub(r"(?<=[0-9०-९])\s+(?=\.)", "", text).strip()
            if not text:
                continue
            x0 = min(w["left"] for w in words)
            y0 = min(w["top"] for w in words)
            x1 = max(w["left"] + w["width"] for w in words)
            y1 = max(w["top"] + w["height"] for w in words)

            crop = image.convert("L").crop((x0, y0, x1, y1))
            pixels = list(crop.getdata())
            ink_ratio = (sum(1 for px in pixels if px < 100) / max(1, len(pixels)))
            is_bold = ink_ratio >= 0.22

            gray = image.convert("L")
            underline = False
            scan_y0 = min(gray.height, max(0, y1 - 1))
            scan_y1 = min(gray.height, y1 + max(5, int((y1 - y0) * 0.35)))
            scan = gray.crop((x0, scan_y0, x1, scan_y1))
            sw, sh = scan.size
            for yy in range(sh):
                run = 0
                longest = 0
                for xx in range(sw):
                    if scan.getpixel((xx, yy)) < 90:
                        run += 1
                        longest = max(longest, run)
                    else:
                        run = 0
                if longest >= max(10, int(sw * 0.55)):
                    underline = True
                    break
            lines.append({
                "text": text, "bold": is_bold, "underlined": underline,
                "box": (x0, y0, x1, y1)
            })

        candidates = []
        for i, line in enumerate(lines):
            match = target_pattern.match(line["text"])
            if not match:
                continue
            title_parts = []
            if match.group(2).strip():
                title_parts.append(match.group(2).strip())
            else:
                for following in lines[i + 1:]:
                    if any_heading_pattern.match(following["text"]):
                        break
                    title_parts.append(following["text"])
                    break
            title = re.sub(r"\s+", " ", " ".join(title_parts)).strip()
            if not title:
                continue
            header = f"{digits}. {title}"
            candidates.append({
                "page": page_num + 1,
                "section": f"{expected_prefix} {digits}",
                "snippet": header[:500],
                "header_text": header,
                "number_bold": False,
                "header_bold": False,
                "header_underlined": False,
                "bold": False,
                "underlined": False,
                "ocr": True,
                "header_length": len(title),
                "sequence_score": 0
            })
        return candidates
    except Exception as e:
        print(f"[OCR fallback] Page {page_num + 1}: {e}")
        return []

def extract_header_lines(page):
    """Return visually/structurally identifiable heading lines, never body paragraphs."""
    data = page.get_text("dict")
    all_spans = [s for b in data.get("blocks", []) for ln in b.get("lines", []) for s in ln.get("spans", [])]
    page_legacy = any(is_legacy_font(s.get("font", "")) for s in all_spans)
    raw_lines, sizes = [], []
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            parts = []
            for span in spans:
                value_part = span.get("text", "")
                if is_legacy_font(span.get("font", "")) or (
                    page_legacy and not _DEVA.search(value_part) and not _URL.search(value_part)
                    and re.search(r"[a-zA-Z;'\]\[{}@#!]", value_part)
                ):
                    value_part = convert_preeti(value_part)
                parts.append(value_part)
            value = re.sub(r"\s+", " ", "".join(parts)).strip()
            if not value:
                continue
            size = max((float(s.get("size", 0) or 0) for s in spans), default=0)
            if size > 0:
                sizes.append(size)
            bold = any((int(s.get("flags", 0)) & 16) != 0 or "bold" in s.get("font", "").lower() for s in spans)
            clean_heading = value.lstrip("\uf0aa\uf076\uf077\uf0a7")
            value = clean_heading
            numbered = bool(re.match(
                r"^\s*(?:(?:धारा|दफा|नियम|अनुसूची|भाग|परिच्छेद)\s*[०-९0-9]+|"
                r"[०-९0-9]+\s*[क-ह]?\s*\.(?![०-९0-9])",
                clean_heading
            ))
            schedule_heading = bool(re.fullmatch(
                r"\s*(?:अनुसूची|अिुसूची)\s*[-–]?\s*[०-९0-9]+\s*",
                clean_heading
            ))
            numbered = numbered or schedule_heading
            raw_lines.append({"text": value, "size": size, "bold": bold, "numbered": numbered})
    if not raw_lines:
        return []
    body_size = sorted(sizes)[len(sizes) // 2] if sizes else 0
    output, seen = [], set()
    for i, item in enumerate(raw_lines):
        value = item["text"]
        is_large = body_size > 0 and item["size"] >= body_size * 1.16
        if len(value) > (320 if item["numbered"] else 180):
            continue
        if not (item["numbered"] or item["bold"] or is_large):
            continue
        number_only = re.match(r"^\s*[०-९0-9]+\s*[.)।:-]?\s*$", value)
        legal_label_only = re.match(
            r"^\s*(?:धारा|दफा|नियम|अनुसूची|भाग|परिच्छेद)\s*[०-९0-9]+\s*[.)।:-]?\s*$",
            value
        )
        if (number_only or legal_label_only) and i + 1 < len(raw_lines):
            nxt = raw_lines[i + 1]["text"]
            if len(nxt) <= 180:
                value = value + " " + nxt
        key = re.sub(r"\s+", " ", value).strip()
        if key not in seen:
            seen.add(key)
            output.append({"header_text": key, "bold": bool(item["bold"]), "underlined": False})
    return output


def _header_similarity(query_key, header_key):
    if not query_key or not header_key:
        return 0.0
    if query_key in header_key:
        return 1.0
    query_numbers = re.findall(r"[०-९0-9]+", query_key)
    header_numbers = re.findall(r"[०-९0-9]+", header_key)
    if query_numbers and not any(
        header_numbers[i:i + len(query_numbers)] == query_numbers
        for i in range(len(header_numbers) - len(query_numbers) + 1)
    ):
        return 0.0
    if len(query_key) < 3:
        return 0.0
    best = difflib.SequenceMatcher(None, query_key, header_key).ratio()
    for width in range(max(2, len(query_key) - 2), min(len(header_key), len(query_key) + 2) + 1):
        for start in range(0, len(header_key) - width + 1):
            best = max(best, difflib.SequenceMatcher(None, query_key, header_key[start:start + width]).ratio())
    return best

def search_in_pdf_bytes(pdf_bytes, query, folder_context="", max_results=30, allow_ocr=False):
    """Audit all matching numbered headings and return the strongest single header."""
    results = []
    numeric_match = re.fullmatch(r"\s*([0-9०-९]+)\s*\.?\s*", query or "")
    expected_prefix = "दफा"
    context_lower = folder_context.lower()
    if "संविधान" in context_lower:
        expected_prefix = "धारा"
    elif "निर्देशिका" in context_lower:
        expected_prefix = "निर्देशन"
    elif "नियम" in context_lower:
        expected_prefix = "नियम"

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if numeric_match:
            raw_digits = numeric_match.group(1)
            digit_map = str.maketrans("0123456789", "०१२३४५६७८९")
            digits = raw_digits.translate(digit_map)
            ascii_digits = digits.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
            number_options = {digits, ascii_digits}
            target_pattern = re.compile(
                r"^\s*(" + "|".join(re.escape(x) for x in number_options) +
                r")\s*\.(?![0-9०-९])\s*(.*)$"
            )
            any_heading_pattern = re.compile(r"^\s*[०-९0-9]+\s*\.(?![०-९0-9])")
            candidates = []
            document_heading_numbers = []

            for page_num in range(len(doc)):
                page = doc[page_num]
                page_dict = page.get_text("rawdict")
                drawings = page.get_drawings()
                underline_shapes = []
                for drawing in drawings:
                    for item in drawing.get("items", []):
                        if not item or item[0] not in ("l", "re"):
                            continue
                        if item[0] == "l":
                            p1, p2 = item[1], item[2]
                            x0, x1 = sorted((p1.x, p2.x))
                            y0, y1 = sorted((p1.y, p2.y))
                            if abs(p1.y - p2.y) <= 1.8:
                                underline_shapes.append((x0, x1, y0, y1, 0))
                        else:
                            rect = fitz.Rect(item[1])
                            if rect.height <= 3:
                                underline_shapes.append((rect.x0, rect.x1, rect.y0, rect.y1, rect.height))
                page_lines = []

                for block in page_dict.get("blocks", []):
                    for line in block.get("lines", []):
                        spans = line.get("spans", [])
                        raw_line_text = "".join(
                            ch.get("c", "") for sp in spans for ch in sp.get("chars", [])
                        )
                        trim_chars = len(raw_line_text) - len(
                            raw_line_text.lstrip("\uf0aa\uf076\uf077\uf0a7 \t\r\n")
                        )
                        line_text = raw_line_text[trim_chars:].strip()
                        line_text = re.sub(r"(?<=[0-9०-९])\s+(?=\.)", "", line_text)
                        if not line_text:
                            continue
                        line_bbox = fitz.Rect(line.get("bbox", (0, 0, 0, 0)))
                        char_styles = []
                        char_pos = 0
                        for sp in spans:
                            span_bold = (
                                (int(sp.get("flags", 0)) & 16) != 0
                                or "bold" in (sp.get("font", "").lower())
                            )
                            for ch in sp.get("chars", []):
                                ch_text = ch.get("c", "")
                                char_bbox = fitz.Rect(ch.get("bbox", (0, 0, 0, 0)))
                                char_underlined = any(
                                    char_bbox.y1 - 7 <= y0 <= char_bbox.y1 + 4
                                    and x1 >= char_bbox.x0 + 0.2
                                    and x0 <= char_bbox.x1 - 0.2
                                    for x0, x1, y0, y1, shape_height in underline_shapes
                                )
                                char_styles.append({
                                    "index": char_pos, "char": ch_text,
                                    "bold": span_bold, "underlined": char_underlined
                                })
                                char_pos += len(ch_text)

                        is_bold = any(item["bold"] for item in char_styles)
                        is_underlined = any(item["underlined"] for item in char_styles)
                        page_lines.append({
                            "text": line_text,
                            "spans": spans,
                            "char_styles": char_styles,
                            "trim_chars": trim_chars,
                            "bold": is_bold,
                            "underlined": is_underlined,
                            "font_size": max((float(sp.get("size", 0)) for sp in spans), default=0),
                            "x0": float(line_bbox.x0),
                            "y0": float(line_bbox.y0),
                            "y1": float(line_bbox.y1)
                        })

                for order_index, detected_line in enumerate(page_lines):
                    detected_match = any_heading_pattern.match(detected_line["text"])
                    if detected_match:
                        detected_digits = detected_match.group(0).strip().rstrip(".").strip()
                        detected_digits = detected_digits.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
                        try:
                            document_heading_numbers.append((int(detected_digits), page_num, order_index, detected_line["font_size"], detected_line["x0"]))
                        except ValueError:
                            pass

                for i, line in enumerate(page_lines):
                    match = target_pattern.match(line["text"])
                    if not match:
                        continue

                    title_parts = []
                    first_title = match.group(2).strip()
                    number_bold = False
                    header_bold = False
                    header_underlined = False

                    prefix_length = len(match.group(0)) - len(match.group(2))
                    title_start = int(line.get("trim_chars", 0)) + prefix_length
                    line_styles = line.get("char_styles", [])
                    number_bold = any(
                        st["bold"] and int(line.get("trim_chars", 0)) <= st["index"] < title_start
                        for st in line_styles
                    )

                    def leading_styled_text(styles, start_index=0, bold=False, underline=False):
                        chars = []
                        started = False
                        for st in styles:
                            if st["index"] < start_index:
                                continue
                            ch = st["char"]
                            if not started and ch.isspace():
                                continue
                            matches_style = (not bold or st["bold"]) and (not underline or st["underlined"])
                            if not matches_style:
                                break
                            started = True
                            chars.append(ch)
                        return "".join(chars).strip()

                    title_line = line
                    if not first_title:
                        same_baseline = [
                            item for item in page_lines[i + 1:]
                            if abs(float(item.get("y0", 9999)) - float(line.get("y0", 0))) <= 2.5
                            and float(item.get("x0", 0)) > float(line.get("x0", 0)) + 5
                            and item.get("text")
                        ]
                        if same_baseline:
                            title_line = min(same_baseline, key=lambda item: float(item.get("x0", 0)))
                            first_title = title_line["text"].strip()
                            title_start = int(title_line.get("trim_chars", 0))

                    title_styles = title_line.get("char_styles", [])
                    underlined_prefix = leading_styled_text(title_styles, title_start, underline=True)
                    bold_prefix = leading_styled_text(title_styles, title_start, bold=True)
                    both_prefix = leading_styled_text(title_styles, title_start, bold=True, underline=True)
                    header_underlined = bool(underlined_prefix)
                    header_bold = bool(bold_prefix)
                    header_bold_underlined = bool(both_prefix)

                    header_title = both_prefix or underlined_prefix or bold_prefix
                    if not header_title:
                        title_text = first_title
                        colon = re.search(r"[ः:]", title_text)
                        header_title = title_text[:colon.end()] if colon else title_text
                    header_title = re.sub(r"\s+", " ", header_title).strip()
                    if not header_title:
                        continue

                    header_text = f"{digits}. {header_title}"
                    candidates.append({
                        "page": page_num + 1,
                        "section": f"{expected_prefix} {digits}",
                        "snippet": header_text[:500],
                        "header_text": header_text,
                        "number_bold": bool(number_bold),
                        "header_bold": bool(header_bold),
                        "header_underlined": bool(header_underlined),
                        "header_bold_underlined": bool(header_bold_underlined),
                        "bold": bool(header_bold),
                        "underlined": bool(header_underlined),
                        "ocr": False,
                        "header_length": len(header_title),
                        "number_value": int(ascii_digits),
                        "heading_font_size": float(line.get("font_size", 0)),
                        "heading_x0": float(line.get("x0", 0)),
                        "heading_order": next((idx for idx, entry in enumerate(document_heading_numbers) if entry[1] == page_num and entry[2] == i), -1)
                    })

            for candidate in candidates:
                try:
                    target_num = int(ascii_digits)
                    pos = int(candidate.get("heading_order", -1))
                    candidate_size = float(candidate.get("heading_font_size", 0))
                    candidate_x0 = float(candidate.get("heading_x0", 0))
                    same_level = []
                    for entry_index, entry in enumerate(document_heading_numbers):
                        entry_size = float(entry[3] or 0)
                        entry_x0 = float(entry[4] or 0)
                        size_close = (
                            candidate_size <= 0 or entry_size <= 0
                            or abs(entry_size - candidate_size) <= max(1.0, candidate_size * 0.08)
                        )
                        indent_close = abs(entry_x0 - candidate_x0) <= 18
                        if size_close and indent_close:
                            same_level.append((entry_index, entry))
                    level_pos = next((j for j, item in enumerate(same_level) if item[0] == pos), -1)
                    if level_pos >= 0:
                        prev_num = same_level[level_pos - 1][1][0] if level_pos > 0 else None
                        next_num = same_level[level_pos + 1][1][0] if level_pos + 1 < len(same_level) else None
                        candidate["sequence_score"] = (
                            (1 if prev_num == target_num - 1 else 0) +
                            (1 if next_num == target_num + 1 else 0)
                        )
                    else:
                        candidate["sequence_score"] = 0
                    candidate.pop("heading_order", None)
                    candidate.pop("heading_font_size", None)
                    candidate.pop("heading_x0", None)
                except Exception:
                    candidate["sequence_score"] = 0
                    candidate.pop("heading_order", None)
                    candidate.pop("heading_font_size", None)
                    candidate.pop("heading_x0", None)

            if allow_ocr:
                for page_num in range(len(doc)):
                    candidates.extend(_ocr_numbered_headers(
                        doc[page_num], page_num, target_pattern,
                        any_heading_pattern, digits, expected_prefix
                    ))

            if candidates:
                for candidate in candidates:
                    candidate.pop("number_value", None)
                    results.append(candidate)
        else:
            clean_q = search_key(query)
            threshold = 0.78 if len(clean_q) >= 5 else 0.86
            for page_num in range(len(doc)):
                for header in extract_header_lines(doc[page_num]):
                    header_text = header["header_text"]
                    score = _header_similarity(clean_q, search_key(header_text))
                    if score >= threshold:
                        results.append({
                            "page": page_num + 1, "section": "हेडर",
                            "snippet": header_text[:300], "header_text": header_text[:500],
                            "bold": header["bold"], "underlined": header["underlined"],
                            "match_score": round(score, 3)
                        })
            results.sort(key=lambda item: item.get("match_score", 0), reverse=True)
            results = results[:max_results]
        doc.close()
    except Exception as e:
        print(f"[search_in_pdf_bytes] Error: {e}")
    return results

# ==========================================
# यहाँ अनुसूची सर्चको नयाँ फङ्सन राखिएको छ (एरर-फ्री)
# ==========================================
def search_anusuchi_tables(pdf_bytes, query, max_results=30):
    """Find matching अनुसूची headings and return pages containing their tables."""
    results = []
    number_match = re.search(r"([0-9०-९]{1,3})", query or "")
    if not number_match:
        return results
    
    raw_number = number_match.group(1)
    nep_number = raw_number.translate(str.maketrans("0123456789", "०१२३४५६७८९"))
    eng_number = raw_number.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    
    # search_key ले स्पेस, ड्यास र फन्टका गल्तीहरू हटाएर "अनुसूची५" मात्र बनाउँछ
    keys_to_find = [
        search_key(f"अनुसूची {nep_number}"),
        search_key(f"अनुसूची {eng_number}"),
        search_key(f"अनुसूची-{nep_number}"),
        search_key(f"अनुसूची-{eng_number}")
    ]

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_no in range(len(doc)):
            page = doc[page_no]
            text = page_text_unicode(page)
            normalized_page_text = search_key(text)
            
            # पहिलो २० लाइन होइन, पूरै पेजभर खोज्ने
            if not any(k in normalized_page_text for k in keys_to_find):
                continue
                
            table_found = False
            table_text = ""
            try:
                finder = page.find_tables()
                if finder.tables:
                    table_found = True
                    table_text = "\n".join(" | ".join(str(c or "") for c in row)
                                           for table in finder.tables for row in (table.extract() or []))
            except Exception:
                pass
                
            if not table_found:
                lines = [x.strip() for x in text.splitlines() if x.strip()]
                table_lines = [x for x in lines if len(re.findall(r"\s{2,}|\t|\|", x)) >= 1]
                if len(table_lines) >= 2:
                    table_found = True
                    table_text = "\n".join(table_lines)
                    
            results.append({
                "page": page_no + 1,
                "section": f"अनुसूची {nep_number}",
                "snippet": (f"अनुसूची {nep_number} — तालिका" if table_found else f"अनुसूची {nep_number}"),
                "highlight_text": table_text[:4000] if table_text else text[:4000]
            })
            
            if len(results) >= max_results:
                break
        doc.close()
    except Exception as e:
        print(f"[search_anusuchi_tables] Error: {e}")
    return results

def get_github_pdfs():
    global _PDF_LIST_CACHE, _PDF_LIST_CACHE_AT
    now = time.monotonic()
    if _PDF_LIST_CACHE and now - _PDF_LIST_CACHE_AT < _PDF_LIST_CACHE_TTL:
        return list(_PDF_LIST_CACHE)

    tree_url = (
        "https://api.github.com/repos/" + GITHUB_REPO
        + "/git/trees/" + GITHUB_BRANCH + "?recursive=1"
    )
    try:
        res = requests.get(
            tree_url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Any-PDF-Nepali-Search"
            },
            timeout=(3, 6)
        )
        res.raise_for_status()
        data = res.json()
        if data.get("truncated"):
            raise RuntimeError("GitHub returned a truncated repository tree")
        pdfs = sorted(
            item["path"].replace("pdfs/", "", 1)
            for item in data.get("tree", [])
            if item.get("type") == "blob"
            and item.get("path", "").startswith("pdfs/")
            and item.get("path", "").lower().endswith(".pdf")
        )
        if pdfs:
            _PDF_LIST_CACHE = pdfs
            _PDF_LIST_CACHE_AT = now
            return list(pdfs)
        raise RuntimeError("GitHub repository tree returned zero PDFs")
    except Exception as e:
        print(f"[PDF LIST] GitHub tree API failed: {e}")
        fallback = _PDF_LIST_CACHE or _FALLBACK_PDF_LIST
        if fallback:
            _PDF_LIST_CACHE = list(fallback)
            _PDF_LIST_CACHE_AT = now
        return list(fallback)

def get_pdf_bytes_from_github(filepath):
    encoded_path = "/".join(requests.utils.quote(part) for part in ("pdfs/" + filepath).split("/"))
    url = "https://raw.githubusercontent.com/" + GITHUB_REPO + "/" + GITHUB_BRANCH + "/" + encoded_path
    try:
        res = requests.get(url, timeout=15)
        if res.status_code == 200: 
            return res.content
    except Exception: 
        pass
    return None

def collect_results(queries, mode="all", target="", targets=[], anusuchi_query=""):
    all_files = get_github_pdfs()

    if mode == "specific" and target:
        files_to_search = [target]
    elif mode in ["folder", "folders"] and targets:
        files_to_search = [f for f in all_files if any(f.startswith(t + "/") or f == t for t in targets)]
    else:
        files_to_search = all_files

    audited = {}
    ordinary = []
    for f in files_to_search:
        try:
            pdf_data = get_pdf_bytes_from_github(f)
            folder_context = f.split('/')[0] if '/' in f else ""
            if not pdf_data:
                continue

            for q in queries:
                matches = (search_anusuchi_tables(pdf_data, anusuchi_query)
                           if anusuchi_query and q == anusuchi_query
                           else search_in_pdf_bytes(pdf_data, q, folder_context, allow_ocr=False))
                if not matches:
                    continue

                is_anusuchi_search = bool(anusuchi_query and q == anusuchi_query)
                numeric_query = (
                    not is_anusuchi_search
                    and re.fullmatch(r"\s*[0-9०-९]+\s*\.?\s*", q or "")
                )
                if numeric_query:
                    for match in matches:
                        match = dict(match)
                        match["filename"] = f
                        match["query"] = q
                        audited.setdefault(q, []).append(match)
                else:
                    ordinary.append({"filename": f, "matches": matches, "query": q})
        except Exception as e:
            print(f"Error processing {f}: {e}")

    def unique_match(items, predicate):
        hits = [item for item in items if predicate(item)]
        return hits[0] if len(hits) == 1 else None

    for q, candidates in audited.items():
        if not candidates:
            continue

        chosen = unique_match(candidates, lambda m: bool(m.get("number_bold")))
        if chosen is None:
            chosen = unique_match(candidates, lambda m: bool(m.get("header_bold_underlined")))
        if chosen is None:
            chosen = unique_match(candidates, lambda m: bool(m.get("header_bold", m.get("bold"))))
        if chosen is None:
            chosen = unique_match(candidates, lambda m: bool(m.get("header_underlined", m.get("underlined"))))

        if chosen is None:
            best_flow = max((int(m.get("sequence_score", 0)) for m in candidates), default=0)
            if best_flow > 0:
                chosen = unique_match(candidates, lambda m: int(m.get("sequence_score", 0)) == best_flow)

        if chosen is None:
            ocr_candidates = []
            for f in files_to_search:
                folder_context = f.split('/')[0] if '/' in f else ""
                try:
                    pdf_data = get_pdf_bytes_from_github(f)
                    if not pdf_data:
                        continue
                    numeric_match = re.fullmatch(r"\\s*([0-9०-९]+)\\s*\\.?\\s*", q or "")
                    if not numeric_match:
                        continue
                    raw_digits = numeric_match.group(1)
                    digit_map = str.maketrans("0123456789", "०१२३४५६७८९")
                    digits = raw_digits.translate(digit_map)
                    ascii_digits = digits.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
                    number_options = {digits, ascii_digits}
                    target_pattern = re.compile(
                        r"^\\s*(" + "|".join(re.escape(x) for x in number_options) +
                        r")\\s*\\.(?![0-9०-९])\\s*(.*)$"
                    )
                    any_heading_pattern = re.compile(r"^\\s*[०-९0-9]+\\s*\\.(?![०-९0-9])")
                    doc = fitz.open(stream=pdf_data, filetype="pdf")
                    expected_prefix = "दफा"
                    context_lower = folder_context.lower()
                    if "संविधान" in context_lower:
                        expected_prefix = "धारा"
                    elif "निर्देशिका" in context_lower:
                        expected_prefix = "निर्देशन"
                    elif "नियम" in context_lower:
                        expected_prefix = "नियम"
                    for page_num in range(len(doc)):
                        for item in _ocr_numbered_headers(
                            doc[page_num], page_num, target_pattern,
                            any_heading_pattern, digits, expected_prefix
                        ):
                            item["filename"] = f
                            item["query"] = q
                            ocr_candidates.append(item)
                    doc.close()
                except Exception as e:
                    print(f"[OCR final fallback] {f}: {e}")

            if ocr_candidates:
                chosen = max(ocr_candidates, key=lambda m: int(m.get("header_length", 0)))
            else:
                chosen = max(candidates, key=lambda m: int(m.get("header_length", 0)))

        chosen = dict(chosen)
        chosen_filename = chosen.pop("filename")
        chosen.pop("query", None)
        ordinary.append({
            "filename": chosen_filename,
            "matches": [chosen],
            "query": q
        })

    return ordinary

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/list_pdfs")
def list_pdfs():
    return jsonify(get_github_pdfs())

@app.route("/pdf/<path:filepath>")
def serve_pdf(filepath):
    pdf_data = get_pdf_bytes_from_github(filepath)
    if pdf_data: 
        return pdf_data, 200, {'Content-Type': 'application/pdf'}
    return "PDF File not found", 404

@app.route("/pdf-highlight/<path:filepath>", methods=["GET", "OPTIONS"])
def serve_pdf_with_header_highlight(filepath):
    if request.method == "OPTIONS":
        response = app.make_response(("", 204))
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Range"
        return response

    pdf_data = get_pdf_bytes_from_github(filepath)
    if not pdf_data:
        return "PDF File not found", 404

    page_number = request.args.get("page", default=1, type=int)
    heading = (request.args.get("snippet") or "").splitlines()[0].strip()
    number_match = re.match(r"^\s*([0-9०-९]+)\s*\.(?![0-9०-९])", heading)
    try:
        doc = fitz.open(stream=pdf_data, filetype="pdf")
        if page_number < 1 or page_number > len(doc):
            doc.close()
            return "Invalid PDF page", 400

        page = doc[page_number - 1]
        highlighted = False

        if number_match:
            digit_map = str.maketrans("0123456789", "०१२३४५६७८९")
            wanted = number_match.group(1).translate(digit_map)
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                for line in block.get("lines", []):
                    line_text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                    line_text = line_text.lstrip("\uf0aa\uf076\uf077\uf0a7").strip()
                    line_text = re.sub(r"(?<=[0-9०-९])\s+(?=\.)", "", line_text)
                    line_match = re.match(r"^\s*([0-9०-९]+)\s*\.(?![0-9०-९])", line_text)
                    if not line_match:
                        continue
                    line_number = line_match.group(1).translate(digit_map)
                    if line_number != wanted:
                        continue

                    rect = fitz.Rect(line.get("bbox", (0, 0, 0, 0)))
                    if not rect.is_empty:
                        annot = page.add_highlight_annot(rect)
                        annot.set_colors(stroke=(0.55, 0.0, 0.0))
                        annot.set_opacity(0.65)
                        annot.update()
                        highlighted = True
                    break
                if highlighted:
                    break

        output = doc.tobytes()
        doc.close()
        response = app.make_response((output, 200))
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = 'inline; filename="highlighted.pdf"'
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Expose-Headers"] = "Accept-Ranges, Content-Length, Content-Range"
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Cache-Control"] = "no-store"
        return response
    except Exception as e:
        print(f"[serve_pdf_with_header_highlight] Error: {e}")
        return "Could not prepare highlighted PDF", 500

@app.route("/check_api_key", methods=["POST"])
def check_api_key():
    data = request.get_json() or {}
    raw_api_key = (data.get("api_key") or "").strip()
    if raw_api_key.startswith("GEMINI_API_KEY="):
        raw_api_key = raw_api_key.split("=", 1)[1].strip()
    if not raw_api_key:
        return jsonify({"valid": False, "error": "API Key खाली छ"}), 400
    try:
        url = "https://generativelanguage.googleapis.com/v1beta/models?key=" + raw_api_key
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return jsonify({"valid": True})
        else:
            exact_error = r.json().get("error", {}).get("message", "API Key अमान्य छ")
            return jsonify({"valid": False, "error": exact_error}), 400
    except Exception as e:
        return jsonify({"valid": False, "error": "नेटवर्क एरर: " + str(e)}), 400

@app.route("/search", methods=["POST"])
def search():
    data = request.get_json() or {}
    mode = data.get("mode", "all")
    target = data.get("target", "")
    targets = data.get("targets", [])
    
    dhara = (data.get("dhara") or "").strip()
    dafa = (data.get("dafa") or "").strip()
    niyam = (data.get("niyam") or "").strip()
    nirdeshan = (data.get("nirdeshan") or "").strip()
    anusuchi = (data.get("anusuchi") or "").strip()
    general = (data.get("general") or "").strip()
    anusuchi_query = anusuchi
    queries = data.get("queries", [])
    
    final_queries = []
    for q in [dhara, dafa, niyam, nirdeshan, anusuchi, general]:
        if q: final_queries.append(q)
    if queries:
        final_queries.extend(queries)
        
    final_queries = list(set(final_queries))
    if not final_queries:
        return jsonify({"error": "खोज शब्द खाली छ।"}), 400
        
    results = collect_results(final_queries, mode, target, targets, anusuchi_query)
    return jsonify({"results": results})

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json() or {}
    queries = data.get("queries", [])
    user_key = (data.get("api_key") or "").strip()
    
    if user_key.startswith("GEMINI_API_KEY="):
        user_key = user_key.split("=", 1)[1].strip()

    keys_to_try = []
    if user_key: keys_to_try.append(user_key)
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key and env_key not in keys_to_try:
        keys_to_try.append(env_key)

    if not keys_to_try:
        return jsonify({"error": "कृपया एउटा API Key हाल्नुहोस् वा Netrun मा GEMINI_API_KEY राख्नुहोस्।"}), 400

    if not queries or not any(q.strip() for q in queries):
        return jsonify({"error": "प्रश्न खाली छ।"}), 400
        
    mode = data.get("mode", "all")
    target = data.get("target", "")
    targets = data.get("targets", [])
    query_str = " AND ".join(queries)
    
    results = collect_results(queries, mode, target, targets)
    
    all_files = get_github_pdfs()
    if mode == "specific" and target:
        chat_files = [target]
    elif mode in ["folder", "folders"] and targets:
        chat_files = [f for f in all_files if any(f.startswith(t + "/") or f == t for t in targets)]
    else:
        chat_files = all_files

    query_keys = [search_key(q) for q in queries if search_key(q)]
    documents = []
    for filename in chat_files:
        pdf_data = get_pdf_bytes_from_github(filename)
        if not pdf_data:
            continue
        try:
            doc = fitz.open(stream=pdf_data, filetype="pdf")
        except Exception:
            continue
        page_texts = [page_text_unicode(page).strip() for page in doc]
        full_key = search_key("\n".join(page_texts))
        relevance = sum(1 for qk in query_keys if qk in full_key)
        documents.append((relevance, filename, page_texts))
        doc.close()

    documents.sort(key=lambda item: item[0], reverse=True)
    ctx_parts = []
    total_chars = 0
    MAX_CHARS = 60000
    for relevance, filename, page_texts in documents[:4]:
        for page_number, txt in enumerate(page_texts, start=1):
            if total_chars >= MAX_CHARS:
                break
            if not txt:
                continue
            remaining = MAX_CHARS - total_chars
            txt = txt[:remaining]
            total_chars += len(txt)
            ctx_parts.append(
                f"===== [PDF: {filename} | पृष्ठ {page_number}] =====\n{txt}"
            )
        if total_chars >= MAX_CHARS:
            break
    context_str = "\n\n".join(ctx_parts) if ctx_parts else "चयन गरिएको PDF मा पढ्न मिल्ने टेक्स्ट भेटिएन।"

    prompt = (
        "तपाईं नेपाली कानुनी सहायक हुनुहुन्छ। तल दिइएको PDF सामग्रीको आधारमा मात्र जवाफ दिनुहोस्; "
        "बाहिरी ज्ञान प्रयोग नगर्नुहोस्।\n"
        "जवाफ यस ढाँचामा दिनुहोस्:\n"
        "1. **कानुन र दफा**: कुन ऐन/नियमावलीको कुन दफा/नियम/धारा हो, र PDF को पृष्ठ नम्बर।\n"
        "2. **मूल व्यवस्था**: त्यो दफाले के भन्छ — मुख्य वाक्यहरू उद्धरण (\"...\") सहित, उपदफा (१), (२) ... अनुसार।\n"
        "3. **सजिलो भाषामा अर्थ**: आम मानिसले बुझ्ने सरल नेपालीमा व्याख्या, सम्भव भए एउटा सानो उदाहरण।\n"
        "4. **ध्यान दिनुपर्ने कुरा**: अपवाद, सर्त, म्याद, वा सम्बन्धित अरू दफा उल्लेख छ भने।\n"
        "PDF टेक्स्टमा केही अक्षर बिग्रिएका हुन सक्छन् (जस्तै 'उपिफा' = 'उपदफा'); सही शब्द बुझेर लेख्नुहोस्।\n"
        "जवाफ PDF मा छैन भने 'माफ गर्नुहोस्, यो जानकारी दिइएको PDF मा भेटिएन।' भन्नुहोस्।\n\n"
        f"PDF सामग्री:\n{context_str}\n\nप्रश्न: {query_str}\n"
    )
    
    last_error = ""
    for api_key in keys_to_try:
        try:
            list_url = "https://generativelanguage.googleapis.com/v1beta/models?key=" + api_key
            list_res = requests.get(list_url, timeout=10)
            if list_res.status_code != 200:
                last_error = list_res.json().get("error", {}).get("message", "Key अमान्य")
                continue
                
            models_data = list_res.json().get("models", [])
            available_models = [m["name"].replace("models/", "") for m in models_data if "generateContent" in m.get("supportedGenerationMethods", [])]
            available_models.sort(key=lambda x: (0 if 'flash' in x else 1, x))
            
            for model in available_models:
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                    r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=90)
                    if r.status_code == 200:
                        j = r.json()
                        if "candidates" in j and len(j["candidates"]) > 0:
                            return jsonify({"answer": j["candidates"][0]["content"]["parts"][0]["text"], "results": results, "mode": "gemini"})
                except Exception:
                    continue
        except Exception as e:
            last_error = str(e)
                
    fallback_msg = f"⚠️ (Gemini API समस्या: {last_error}। स्थानीय खोज नतिजा तल छ।)\n\n"
    return jsonify({"answer": fallback_msg + "PDF हरूमा भेटिएका मिल्दा स्थानहरू तल देखाइएका छन्।", "results": results, "mode": "fallback"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
