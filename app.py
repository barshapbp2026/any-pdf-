import os
import re
import requests
import pytesseract
from PIL import Image
from flask import Flask, render_template, request, jsonify
import fitz
from dotenv import load_dotenv

load_dotenv() 
app = Flask(__name__)

GITHUB_REPO = "barshapbp2026/any-pdf-"
GITHUB_BRANCH = "main"

def search_in_pdf_bytes(pdf_bytes, query, folder_context="", max_results=30):
    results = []
    q_digits = re.sub(r'[^\d०-९]', '', query.strip())
    
    expected_prefix = "दफा"
    context_lower = folder_context.lower()
    if "संविधान" in context_lower: expected_prefix = "धारा"
    elif "निर्देशिका" in context_lower: expected_prefix = "निर्देशन"
    elif "नियम" in context_lower: expected_prefix = "नियम"

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        if q_digits:
            q_nep = q_digits.translate(str.maketrans("0123456789", "०१२३४५६७८९"))
            q_ascii = q_digits.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
            search_needles = [f"{q_nep}.", f"{q_ascii}.", f"{q_nep}", f"{q_ascii}"]
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_text = page.get_text("text")
                
                found_on_page = False
                for needle in search_needles:
                    # page.search_for प्रयोग गर्ने (Chrome को Ctrl+F जस्तै)
                    # flags ले स्पेस र लाइन ब्रेकको समस्या हटाउन मद्दत गर्छ
                    rects = page.search_for(needle, flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_DEHYPHENATE)
                    if rects:
                        for line in page_text.split('\n'):
                            if needle in line:
                                results.append({
                                    "page": page_num + 1,
                                    "section": f"{expected_prefix} {q_nep}",
                                    "snippet": line.strip()[:200]
                                })
                                found_on_page = True
                                break
                    if found_on_page: break
                
                # यदि search_for ले भेटेन भने (टेक्स्ट बिग्रेको हुन सक्छ) -> OCR प्रयोग गर्ने
                if not found_on_page:
                    try:
                        pix = page.get_pixmap(dpi=300)
                        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                        ocr_text = pytesseract.image_to_string(img, lang='nep')
                        for line in ocr_text.split('\n'):
                            if q_nep in line or q_ascii in line:
                                results.append({
                                    "page": page_num + 1,
                                    "section": f"{expected_prefix} {q_nep} (OCR)",
                                    "snippet": line.strip()[:200]
                                })
                                break
                    except Exception as e:
                        print(f"OCR Error: {e}")
                        
                if len(results) >= max_results: break
                
        else:
            clean_q = query.strip()
            for page_num in range(len(doc)):
                page = doc[page_num]
                # search_for प्रयोग गर्ने
                rects = page.search_for(clean_q, flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_DEHYPHENATE)
                if rects:
                    page_text = page.get_text("text")
                    for line in page_text.split('\n'):
                        if clean_q in line:
                            results.append({
                                "page": page_num + 1,
                                "section": "सन्दर्भ",
                                "snippet": line.strip()[:200]
                            })
                            break
                if len(results) >= max_results: break
                
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

def collect_results(queries, mode="all", target="", targets=[]):
    all_files = get_github_pdfs()
    
    if mode == "specific" and target:
        files_to_search = [target]
    elif mode in ["folder", "folders"] and targets:
        files_to_search = [f for f in all_files if any(f.startswith(t + "/") or f == t for t in targets)]
    else:
        files_to_search = all_files

    out = []
    for f in files_to_search:
        try:
            pdf_data = get_pdf_bytes_from_github(f)
            folder_context = f.split('/')[0] if '/' in f else ""
            
            if pdf_data:
                for q in queries:
                    matches = search_in_pdf_bytes(pdf_data, q, folder_context) 
                    if matches:
                        if not any(x['filename'] == f and x['matches'] == matches for x in out):
                            out.append({"filename": f, "matches": matches, "query": q})
        except Exception as e:
            print(f"Error processing {f}: {e}")
    return out

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

@app.route("/debug/<path:filepath>")
def debug_pdf(filepath):
    pdf_data = get_pdf_bytes_from_github(filepath)
    if not pdf_data:
        return "PDF not found", 404
    
    doc = fitz.open(stream=pdf_data, filetype="pdf")
    output = [f"Total pages: {len(doc)}", f"File: {filepath}", "======================================"]
    
    for page_num in range(len(doc)):
        page_text = doc[page_num].get_text("text")
        for line in page_text.split('\n'):
            if '३४' in line or '34' in line:
                output.append(f"Page {page_num+1}: {repr(line[:150])}")
    
    doc.close()
    return "<pre style='font-family:monospace; padding:20px; background:#f4f4f4;'>" + "\n".join(output) + "</pre>"

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
    queries = data.get("queries", [])
    
    final_queries = []
    for q in [dhara, dafa, niyam, nirdeshan, anusuchi, general]:
        if q: final_queries.append(q)
    if queries:
        final_queries.extend(queries)
        
    final_queries = list(set(final_queries))
    if not final_queries:
        return jsonify({"error": "खोज शब्द खाली छ।"}), 400
        
    results = collect_results(final_queries, mode, target, targets)
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
    
    ctx_lines = []
    for g in results[:5]:
        for m in g["matches"][:6]:
            sec = m["section"] or "सन्दर्भ"
            ctx_lines.append(f"[PDF: {g['filename']} | पृष्ठ {m['page']} | {sec}] {m['snippet']}")
    context_str = "\n".join(ctx_lines) if ctx_lines else "कुनै मिल्दो सामग्री भेटिएन।"
    
    prompt = (
        "तपाईं नेपाली कानुनी सहायक हुनुहुन्छ। तल दिइएको PDF सामग्रीको आधारमा मात्र प्रश्नको जवाफ दिनुहोस्। "
        "बाहिरी ज्ञान (Internet) वा आफ्नो जानकारी प्रयोग गर्न पूर्ण रूपमा मनाही छ। "
        "यदि जवाफ दिइएको PDF सामग्रीमा छैन भने, 'माफ गर्नुहोस्, यो जानकारी दिइएको PDF मा भेटिएन।' भन्नुहोस्।\n\n"
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
                    r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
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
