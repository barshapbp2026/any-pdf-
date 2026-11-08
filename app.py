import os
import re
import requests
from flask import Flask, render_template, request, jsonify
import fitz
from dotenv import load_dotenv
from nepali_text import page_text_unicode, search_key

load_dotenv() 
app = Flask(__name__)

GITHUB_REPO = "barshapbp2026/any-pdf-"
GITHUB_BRANCH = "main"

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
            # If Nepali traineddata is unavailable, still attempt English digits/text.
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
            # Tesseract may output Devanagari digits or ASCII digits.
            text = re.sub(r"(?<=[0-9०-९])\s+(?=\.)", "", text).strip()
            if not text:
                continue
            x0 = min(w["left"] for w in words)
            y0 = min(w["top"] for w in words)
            x1 = max(w["left"] + w["width"] for w in words)
            y1 = max(w["top"] + w["height"] for w in words)

            # Estimate boldness from ink density inside the OCR text crop.
            crop = image.convert("L").crop((x0, y0, x1, y1))
            pixels = list(crop.getdata())
            ink_ratio = (sum(1 for px in pixels if px < 100) / max(1, len(pixels)))
            is_bold = ink_ratio >= 0.22

            # Detect a continuous horizontal underline immediately below text.
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
            style_bold = line["bold"]
            style_underlined = line["underlined"]
            if match.group(2).strip():
                title_parts.append(match.group(2).strip())
            else:
                for following in lines[i + 1:]:
                    if any_heading_pattern.match(following["text"]):
                        break
                    title_parts.append(following["text"])
                    style_bold = style_bold or following["bold"]
                    style_underlined = style_underlined or following["underlined"]
                    break
            title = re.sub(r"\s+", " ", " ".join(title_parts)).strip()
            if not title:
                continue
            header = f"{digits}. {title}"
            # OCR is only the final longest-header fallback; its visual
            # estimates must not decide bold or underline ranking.
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
                page_dict = page.get_text("dict")
                drawings = page.get_drawings()
                page_lines = []

                for block in page_dict.get("blocks", []):
                    for line in block.get("lines", []):
                        spans = line.get("spans", [])
                        line_text = "".join(sp.get("text", "") for sp in spans).strip()
                        line_text = re.sub(r"(?<=[0-9०-९])\s+(?=\.)", "", line_text)
                        if not line_text:
                            continue
                        line_bbox = fitz.Rect(line.get("bbox", (0, 0, 0, 0)))
                        is_bold = any(
                            (int(sp.get("flags", 0)) & 16) != 0
                            or "bold" in (sp.get("font", "").lower())
                            for sp in spans
                        )
                        is_underlined = False
                        # Detect drawn underline strokes directly below the text line.
                        for drawing in drawings:
                            for item in drawing.get("items", []):
                                if not item or item[0] not in ("l", "re"):
                                    continue
                                if item[0] == "l":
                                    p1, p2 = item[1], item[2]
                                    x0, x1 = sorted((p1.x, p2.x))
                                    y0, y1 = sorted((p1.y, p2.y))
                                    horizontal = abs(p1.y - p2.y) <= 1.8
                                    near_baseline = line_bbox.y1 - 1 <= y0 <= line_bbox.y1 + 4
                                    overlaps = x1 >= line_bbox.x0 and x0 <= line_bbox.x1
                                    if horizontal and near_baseline and overlaps and (x1 - x0) >= min(8, line_bbox.width * 0.25):
                                        is_underlined = True
                                        break
                                else:
                                    rect = fitz.Rect(item[1])
                                    near_baseline = line_bbox.y1 - 1 <= rect.y0 <= line_bbox.y1 + 4
                                    overlaps = rect.x1 >= line_bbox.x0 and rect.x0 <= line_bbox.x1
                                    if near_baseline and overlaps and rect.width >= min(8, line_bbox.width * 0.25) and rect.height <= 3:
                                        is_underlined = True
                                        break
                            if is_underlined:
                                break

                        page_lines.append({
                            "text": line_text,
                            "spans": spans,
                            "bold": is_bold,
                            "underlined": is_underlined,
                            "font_size": max((float(sp.get("size", 0)) for sp in spans), default=0),
                            "x0": float(line_bbox.x0)
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

                    # Treat the number and title as independent text spans.
                    prefix_length = len(match.group(0)) - len(match.group(2))
                    char_pos = 0
                    for span in line.get("spans", []):
                        span_text = span.get("text", "")
                        span_end = char_pos + len(span_text)
                        span_bold = (
                            (int(span.get("flags", 0)) & 16) != 0
                            or "bold" in span.get("font", "").lower()
                        )
                        if char_pos < prefix_length and span_end > 0 and span_bold:
                            number_bold = True
                        if span_end > prefix_length and span_bold:
                            header_bold = True
                        char_pos = span_end

                    if first_title:
                        title_parts.append(first_title)
                        header_underlined = bool(line["underlined"])
                    else:
                        # Number-only line: get style only from the title line.
                        for following in page_lines[i + 1:]:
                            if any_heading_pattern.match(following["text"]):
                                break
                            if following["text"]:
                                title_parts.append(following["text"])
                                header_bold = bool(following["bold"])
                                header_underlined = bool(following["underlined"])
                                if len(" ".join(title_parts)) >= 500:
                                    break
                            if title_parts:
                                break

                    header_title = re.sub(r"\\s+", " ", " ".join(title_parts)).strip()
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
                        "bold": bool(header_bold),
                        "underlined": bool(header_underlined),
                        "ocr": False,
                        "header_length": len(header_title),
                        "number_value": int(ascii_digits),
                        "heading_font_size": float(line.get("font_size", 0)),
                        "heading_x0": float(line.get("x0", 0)),
                        "heading_order": next((idx for idx, entry in enumerate(document_heading_numbers) if entry[1] == page_num and entry[2] == i), -1)
                    })

            # Score N-1 -> N -> N+1 only among headings at the
            # same visual level (similar font size and left indentation).
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
            for page_num in range(len(doc)):
                for line in page_text_unicode(doc[page_num]).splitlines():
                    if clean_q and clean_q in search_key(line):
                        results.append({"page": page_num + 1, "section": "सन्दर्भ", "snippet": line.strip()[:200]})
                        break
                if len(results) >= max_results:
                    break
        doc.close()
    except Exception as e:
        print(f"[search_in_pdf_bytes] Error: {e}")
    return results

def get_github_pdfs():
    tree_url = "https://api.github.com/repos/" + GITHUB_REPO + "/git/trees/" + GITHUB_BRANCH + "?recursive=1"
    try:
        res = requests.get(tree_url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            pdfs = [item["path"].replace("pdfs/", "", 1) for item in data.get("tree", []) 
                    if item["type"] == "blob" and item["path"].startswith("pdfs/") and item["path"].lower().endswith(".pdf")]
            return sorted(pdfs)
    except Exception as e:
        print(f"Tree API error: {e}")
    return []

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

def search_anusuchi_tables(pdf_bytes, query, max_results=30):
    """Find matching अनुसूची headings and return pages containing their tables."""
    results = []
    number_match = re.search(r"([0-9०-९]{1,3})", query or "")
    if not number_match:
        return results
    number = number_match.group(1).translate(str.maketrans("0123456789", "०१२३४५६७८९"))
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        heading_re = re.compile(r"अनुसूची\s*(?:[-–:]?\s*)?" + re.escape(number) + r"(?![०-९0-9])")
        for page_no in range(len(doc)):
            page = doc[page_no]
            text = page_text_unicode(page)
            if not heading_re.search(text):
                continue
            table_found = False
            table_text = ""
            # Prefer PyMuPDF's table detector when available.
            try:
                finder = page.find_tables()
                if finder.tables:
                    table_found = True
                    table_text = "\n".join(" | ".join(str(c or "") for c in row)
                                           for table in finder.tables for row in (table.extract() or []))
            except Exception:
                pass
            # Fallback: a schedule page often has aligned columns / repeated cell separators.
            if not table_found:
                lines = [x.strip() for x in text.splitlines() if x.strip()]
                table_lines = [x for x in lines if len(re.findall(r"\s{2,}|\t|\|", x)) >= 1]
                if len(table_lines) >= 2:
                    table_found = True
                    table_text = "\n".join(table_lines)
            if table_found:
                results.append({
                    "page": page_no + 1,
                    "section": "अनुसूची " + number,
                    "snippet": ("अनुसूची " + number + " — तालिका"),
                    "highlight_text": table_text[:4000]
                })
                if len(results) >= max_results:
                    break
        doc.close()
    except Exception as e:
        print(f"[search_anusuchi_tables] Error: {e}")
    return results

def collect_results(queries, mode="all", target="", targets=[], anusuchi_query=""):
    all_files = get_github_pdfs()

    if mode == "specific" and target:
        files_to_search = [target]
    elif mode in ["folder", "folders"] and targets:
        files_to_search = [f for f in all_files if any(f.startswith(t + "/") or f == t for t in targets)]
    else:
        files_to_search = all_files

    # Keep an audit pool per query so a numeric heading is selected across
    # the complete requested scope, not independently from every PDF.
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

                # Strict numbered-heading protocol applies only to the
                # Dhara, Dafa, Niyam and Nirdeshan boxes. Anusuchi is a
                # separate table lookup and must bypass this ranking.
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

    # Strict staged selection: advance whenever a stage has zero or
    # multiple matches. A stage wins only when exactly one candidate matches.
    def unique_match(items, predicate):
        hits = [item for item in items if predicate(item)]
        return hits[0] if len(hits) == 1 else None

    for q, candidates in audited.items():
        if not candidates:
            continue

        chosen = unique_match(candidates, lambda m: bool(m.get("number_bold")))
        if chosen is None:
            chosen = unique_match(
                candidates,
                lambda m: bool(m.get("header_bold", m.get("bold")))
                and bool(m.get("header_underlined", m.get("underlined")))
            )
        if chosen is None:
            chosen = unique_match(
                candidates,
                lambda m: bool(m.get("header_bold", m.get("bold")))
            )
        if chosen is None:
            chosen = unique_match(
                candidates,
                lambda m: bool(m.get("header_underlined", m.get("underlined")))
            )

        # Flow is considered only after all four style stages. Prefer a
        # uniquely strongest same-level sequence; ties continue to OCR.
        if chosen is None:
            best_flow = max((int(m.get("sequence_score", 0)) for m in candidates), default=0)
            if best_flow > 0:
                chosen = unique_match(
                    candidates,
                    lambda m: int(m.get("sequence_score", 0)) == best_flow
                )

        # OCR is strictly the last tie-break. It extracts only the title
        # attached to each matching number, never paragraph/body text.
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
                # If OCR cannot read any candidate, retain the longest native
                # header title; never measure the body text below a heading.
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
    """Serve a PDF with only the matching numbered main heading highlighted dark red."""
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
    # Identify the main heading number immediately followed by a dot (e.g. १२.)
    # Accept a little spacing between the section number and its dot.
    number_match = re.match(r"^\s*([0-9०-९]+)\s*\.(?![0-9०-९])", heading)
    try:
        doc = fitz.open(stream=pdf_data, filetype="pdf")
        if page_number < 1 or page_number > len(doc):
            doc.close()
            return "Invalid PDF page", 400

        page = doc[page_number - 1]
        highlighted = False

        # Only numbered main headers are highlighted; body-text searches
        # deliberately receive no highlight.
        if number_match:
            digit_map = str.maketrans("0123456789", "०१२३४५६७८९")
            wanted = number_match.group(1).translate(digit_map)
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                for line in block.get("lines", []):
                    line_text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                    # Some PDFs place the dot in a separate span with a space.
                    line_text = re.sub(r"(?<=[0-9०-९])\s+(?=\.)", "", line_text)
                    line_match = re.match(r"^\s*([0-9०-९]+)\s*\.(?![0-9०-९])", line_text)
                    if not line_match:
                        continue
                    line_number = line_match.group(1).translate(digit_map)
                    if line_number != wanted:
                        continue

                    # Highlight the header line only, never paragraph/body text.
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
        # Required because the hosted PDF.js viewer is on a different origin.
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
    
    # भेटिएका पेजहरूको पूरा टेक्स्ट (र अर्को पेज पनि, किनकि दफा पेज पार गर्न सक्छ) Gemini लाई पठाउने
    ctx_parts = []
    total_chars = 0
    MAX_CHARS = 60000
    for g in results[:4]:
        pdf_data = get_pdf_bytes_from_github(g["filename"])
        if not pdf_data:
            continue
        try:
            doc = fitz.open(stream=pdf_data, filetype="pdf")
        except Exception:
            continue
        seen = set()
        for m in g["matches"][:3]:
            for p in (m["page"], m["page"] + 1):
                if p in seen or p > len(doc) or total_chars > MAX_CHARS:
                    continue
                seen.add(p)
                txt = page_text_unicode(doc[p - 1]).strip()
                total_chars += len(txt)
                ctx_parts.append(f"===== [PDF: {g['filename']} | पृष्ठ {p} | {m['section']}] =====\n{txt}")
        doc.close()
    context_str = "\n\n".join(ctx_parts) if ctx_parts else "कुनै मिल्दो सामग्री भेटिएन।"

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
