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
    q_norm = _norm(query.strip())
    if not q_norm: return results
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        carried_section = None
        for page_num in range(len(doc)):
            page_text = doc[page_num].get_text("text")
            page_norm = _norm(page_text)
            section_positions = [(m.start(), m.group()) for m in SECTION_RE.finditer(page_norm)]
            idx = 0
            while True:
                pos = page_norm.find(q_norm, idx)
                if pos == -1: break
                section = carried_section
                for sp, sname in section_positions:
                    if sp <= pos: section = sname
                start = max(0, pos - 80)
                end = min(len(page_text), pos + len(query) + 140)
                snippet = page_text[start:end].replace("\n", " ").strip()
                results.append({"page": page_num + 1, "section": section, "snippet": snippet})
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
    
    final_queries = []
    if dhara: final_queries.append(f"धारा {dhara}")
    if dafa: final_queries.append(f"दफा {dafa}")
    if niyam: final_queries.append(f"नियम {niyam}")
    if nirdeshan: final_queries.append(f"निर्देशन {nirdeshan}")
    if anusuchi: final_queries.append(f"अनुसूची {anusuchi}")
    if general: final_queries.append(general)
    if queries: final_queries.extend(queries)
    
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
    
    # Key सफा गर्ने (यदि GEMINI_API_KEY=... भनेर हालिएको छ भने)
    if user_key.startswith("GEMINI_API_KEY="):
        user_key = user_key.split("=", 1)[1].strip()

    # Fallback Keys को सूची बनाउने
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
    context = "
".join(ctx_lines) if ctx_lines else "कुनै मिल्दो सामग्री भेटिएन।"
    
    prompt = (
        "तपाईं नेपाली कानुनी सहायक हुनुहुन्छ। तल दिइएको PDF सामग्रीको आधारमा **मात्र** प्रश्नको जवाफ दिनुहोस्। "
        "बाहिरी ज्ञान (Internet) वा आफ्नो जानकारी प्रयोग गर्न **पूर्ण रूपमा मनाही छ**। "
        "यदि जवाफ दिइएको PDF सामग्रीमा छैन भने, 'माफ गर्नुहोस्, यो जानकारी दिइएको PDF मा भेटिएन।' भन्नुहोस्।

"
        f"PDF सामग्री:
{context}

प्रश्न: {query_str}
"
    )
    
    # गुगलले २०२६ मा सिफारिस गरेका नयाँ र स्थिर मोडेलहरू
            models_to_try = [
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
            "gemini-flash-latest",
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash"
        ]
    
    last_error = ""
    # Key Fallback र Model Fallback दुवै लूपमा चलाउने
    for api_key in keys_to_try:
        for model in models_to_try:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
                if r.status_code == 200:
                    j = r.json()
                    if "candidates" in j and len(j["candidates"]) > 0:
                        return jsonify({"answer": j["candidates"][0]["content"]["parts"][0]["text"], "results": results, "mode": "gemini"})
                else:
                    try: 
                        err_data = r.json()
                        last_error = err_data.get("error", {}).get("message", f"HTTP {r.status_code}")
                    except: 
                        last_error = f"HTTP {r.status_code}"
            except Exception as e:
                last_error = str(e)
                
    # यदि सबै Key र Model फेल भए, स्थानीय खोज नतिजा देखाउने
    fallback_msg = f"⚠️ (Gemini API समस्या: {last_error}। स्थानीय खोज नतिजा तल छ।)

"
    return jsonify({"answer": fallback_msg + "PDF हरूमा भेटिएका मिल्दा स्थानहरू तल देखाइएका छन्।", "results": results, "mode": "fallback"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
