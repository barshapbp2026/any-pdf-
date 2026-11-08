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

def search_in_pdf_bytes(pdf_bytes, query, folder_context="", max_results=30):
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
                            "underlined": is_underlined
                        })

                for i, line in enumerate(page_lines):
                    match = target_pattern.match(line["text"])
                    if not match:
                        continue

                    title_parts = []
                    style_bold = line["bold"]
                    style_underlined = line["underlined"]
                    first_title = match.group(2).strip()
                    if first_title:
                        title_parts.append(first_title)
                    else:
                        # The number and its title are often extracted as separate lines.
                        for following in page_lines[i + 1:]:
                            if any_heading_pattern.match(following["text"]):
                                break
                            if following["text"]:
                                title_parts.append(following["text"])
                                style_bold = style_bold or following["bold"]
                                style_underlined = style_underlined or following["underlined"]
                                if len(" ".join(title_parts)) >= 500:
                                    break
                            if title_parts:
                                break

                    header_title = re.sub(r"\s+", " ", " ".join(title_parts)).strip()
                    if not header_title:
                        continue

                    header_text = f"{digits}. {header_title}"
                    candidates.append({
                        "page": page_num + 1,
                        "section": f"{expected_prefix} {digits}",
                        "snippet": header_text[:500],
                        "header_text": header_text,
                        "bold": bool(style_bold),
                        "underlined": bool(style_underlined),
                        "header_length": len(header_title)
                    })

            if candidates:
                # First prefer bold/underlined headings; if none exist, choose
                # the candidate with the longest actual header title.
                styled = [c for c in candidates if c["bold"] or c["underlined"]]
                pool = styled if styled else candidates
                chosen = max(
                    pool,
                    key=lambda c: (c["header_length"], c["bold"], c["underlined"])
                )
                chosen.pop("header_length", None)
                results.append(chosen)
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
                           else search_in_pdf_bytes(pdf_data, q, folder_context))
                if not matches:
                    continue

                numeric_query = re.fullmatch(r"\s*[0-9०-९]+\s*\.?\s*", q or "")
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

    # Final rule: styled (bold/underlined) headings win; among eligible
    # styled headings choose the longest title. If none are styled, use
    # the longest extracted heading title as the fallback.
    for q, candidates in audited.items():
        styled = [m for m in candidates if m.get("bold") or m.get("underlined")]
        pool = styled if styled else candidates
        if pool:
            chosen = max(pool, key=lambda m: (
                len(m.get("header_text") or m.get("snippet") or ""),
                bool(m.get("bold")),
                bool(m.get("underlined"))
            ))
            ordinary.append({
                "filename": chosen.pop("filename"),
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
    number_match = re.match(r"^\s*([0-9०-९]{1,3})\s*\.(?![0-9०-९])", heading)
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
                    line_match = re.match(r"^\s*([0-9०-९]{1,3})\s*\.(?![0-9०-९])", line_text)
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
