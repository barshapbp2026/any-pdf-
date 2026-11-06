import os
import re
import requests
from flask import Flask, render_template, request, jsonify
import fitz
from dotenv import load_dotenv

load_dotenv() 

app = Flask(__name__)

GITHUB_REPO = "barshapbp2026/any-pdf-"
GITHUB_BRANCH = "main"

NEPALI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
def _norm(t):
    return t.lower().translate(NEPALI_DIGITS)

SECTION_RE = re.compile(r"((?:धारा|दफा|नियम|उपनियम|निर्देशन|अनुसूची)\s*[\d०-९]+)")

def search_in_pdf_bytes(pdf_bytes, query, max_results=30):
    results = []
    # खोज्ने शब्दबाट सबै स्पेस हटाउने
    q_norm = _norm(query.strip()).replace(" ", "")
    if not q_norm: return results
    
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        carried_section = None
        for page_num in range(len(doc)):
            page_text = doc[page_num].get_text("text")
            
            # PDF को टेक्स्टबाट पनि सबै स्पेस र नयाँ लाइन हटाउने
            page_norm_clean = _norm(page_text).replace(" ", "").replace("\n", "").replace("\r", "")
            
            # पेजमा भएका धारा/दफा पत्ता लगाउने
            section_positions = [(m.start(), m.group()) for m in SECTION_RE.finditer(_norm(page_text))]
            
            idx = 0
            while True:
                pos = page_norm_clean.find(q_norm, idx)
                if pos == -1: break
                
                # स्निपेट (झलक) निकाल्नको लागि नजिकैको टेक्स्ट हेर्ने
                start = max(0, pos - 30)
                end = min(len(page_norm_clean), pos + len(q_norm) + 80)
                snippet = page_norm_clean[start:end]
                
                section = carried_section
                for sp, sname in section_positions:
                    if sp <= pos: section = sname
                        
                results.append({"page": page_num + 1, "section": section, "snippet": "..." + snippet + "..."})
                idx = pos + 1
                if len(results) >= max_results: break
                
            if section_positions: carried_section = section_positions[-1][1]
            if len(results) >= max_results: break
        doc.close()
    except Exception as e:
        print(f"[search_in_pdf] Error: {e}")
    return results

def get_github_pdfs():
    tree_url = f"https://api.github.com/repos/{GITHUB_REPO}/git/trees/{GITHUB_BRANCH}?recursive=1"
    try:
        res = requests.get(tree_url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("truncated"):
                return _fetch_all_pdfs_fallback()
            pdfs = [item["path"].replace("pdfs/", "", 1) for item in data.get("tree", []) 
                    if item["type"] == "blob" and item["path"].startswith("pdfs/") and item["path"].lower().endswith(".pdf")]
            return sorted(pdfs)
    except Exception as e:
        print(f"Tree API error: {e}")
    return _fetch_all_pdfs_fallback()

def _fetch_all_pdfs_fallback():
    pdfs = []
    def walk(path=""):
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}?ref={GITHUB_BRANCH}"
        try:
            res = requests.get(url, timeout=10)
            if res.status_code != 200: return
            for item in res.json():
                if item["type"] == "dir":
                    walk(item["path"])
                elif item["type"] == "file" and item["path"].startswith("pdfs/") and item["path"].lower().endswith(".pdf"):
                    pdfs.append(item["path"].replace("pdfs/", "", 1))
        except Exception: pass
    walk("pdfs")
    return sorted(pdfs)

def get_pdf_bytes_from_github(filepath):
    encoded_path = "/".join(requests.utils.quote(part) for part in f"pdfs/{filepath}".split("/"))
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{encoded_path}"
    try:
        res = requests.get(url, timeout=15)
        if res.status_code == 200: return res.content
    except Exception: pass
    return None

def collect_results(queries, filepaths=None, folders=None):
    all_files = get_github_pdfs()
    if filepaths:
        files_to_search = filepaths
    elif folders:
        files_to_search = [f for f in all_files if any(f.startswith(folder + "/") or f == folder for folder in folders)]
    else:
        files_to_search = all_files

    out = []
    for f in files_to_search:
        try:
            pdf_data = get_pdf_bytes_from_github(f)
            if pdf_data:
                for q in queries:
                    matches = search_in_pdf_bytes(pdf_data, q)
                    if matches:
                        if not any(x['filename'] == f and x['matches'] == matches for x in out):
                            out.append({"filename": f, "matches": matches, "query": q})
        except Exception: pass
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
    return "PDF File not found on GitHub", 404

@app.route("/check_api_key", methods=["POST"])
def check_api_key():
    data = request.get_json() or {}
    raw_api_key = (data.get("api_key") or "").strip()
    if raw_api_key.startswith("GEMINI_API_KEY="):
        raw_api_key = raw_api_key.split("=", 1)[1].strip()
        
    if not raw_api_key:
        return jsonify({"valid": False, "error": "API Key खाली छ"}), 400
        
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={raw_api_key}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return jsonify({"valid": True})
        else:
            try:
                exact_error = r.json().get("error", {}).get("message", "API Key अमान्य छ")
            except:
                exact_error = f"HTTP {r.status_code} एरर"
            return jsonify({"valid": False, "error": exact_error}), 400
    except Exception as e:
        return jsonify({"valid": False, "error": f"नेटवर्क एरर: {str(e)}"}), 400

@app.route("/search", methods=["POST"])
def search():
    data = request.get_json() or {}
    mode = data.get("mode", "all")
    target = data.get("target", "")
    targets = data.get("targets", [])
    queries = data.get("queries", [])
    
    dhara = (data.get("dhara") or "").strip()
    dafa = (data.get("dafa") or "").strip()
    niyam = (data.get("niyam") or "").strip()
    nirdeshan = (data.get("nirdeshan") or "").strip()
    anusuchi = (data.get("anusuchi") or "").strip()
    general = (data.get("general") or "").strip()
    
    specific_nums = []
    if dhara: specific_nums.append(dhara)
    if dafa: specific_nums.append(dafa)
    if niyam: specific_nums.append(niyam)
    if nirdeshan: specific_nums.append(nirdeshan)
    if anusuchi: specific_nums.append(anusuchi)

    final_queries = []
    for num in specific_nums:
        # व्याकरण मिलाएर सबै विकल्पहरू खोज्ने (प्रयोगकर्ताले जुनसुकै बक्समा लेखे पनि)
        final_queries.extend([
            f"धारा {num}", f"धारा{num}",
            f"दफा {num}", f"दफा{num}",
            f"नियम {num}", f"नियम{num}",
            f"निर्देशन {num}", f"निर्देशन{num}",
            f"अनुसूची {num}", f"अनुसूची{num}"
        ])

    if general:
        final_queries.append(general)
    if queries:
        final_queries.extend(queries)
        
    # डुप्लिकेट हटाउने र खाली भएको छान्ने
    final_queries = list(set([q for q in final_queries if q.strip()]))
    
    if not final_queries:
        return jsonify({"error": "खोज शब्द खाली छ।"}), 400
        
    filepath = target if mode == "specific" else None
    folders = targets if mode == "folder" else None
    results = collect_results(final_queries, filepaths=filepath, folders=folders)
    return jsonify({"results": results})

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json() or {}
    queries = data.get("queries", [])
    user_key = (data.get("api_key") or "").strip()
    
    if user_key.startswith("GEMINI_API_KEY="):
        user_key = user_key.split("=", 1)[1].strip()

    keys_to_try = []
    if user_key:
        keys_to_try.append(user_key)
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
    
    filepath = target if mode == "specific" else None
    folders = targets if mode == "folder" else None
    results = collect_results(queries, filepaths=filepath, folders=folders)
    
    ctx_lines = []
    for g in results[:5]:
        for m in g["matches"][:6]:
            sec = m["section"] or "सन्दर्भ अज्ञात"
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
            list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
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
