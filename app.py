import os
import re
import requests
from flask import Flask, render_template, request, jsonify
import fitz
from supabase import create_client, Client

app = Flask(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
BUCKET_NAME = "pdfs"

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase Client initialized.")
    except Exception as e:
        print(f"❌ Failed to init Supabase: {e}")

DELETE_PASSWORD = os.environ.get("DELETE_PASSWORD", "admin123")

NEPALI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
def _norm(t):
    return t.lower().translate(NEPALI_DIGITS)

SECTION_RE = re.compile(r"((?:धारा|दफा|नियम|उपनियम)\s*[\d०-९]+)")

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

def collect_results(query, filename=None):
    if not supabase: return []
    try:
        if filename:
            res = supabase.table("documents").select("*").eq("name", filename).execute()
        else:
            res = supabase.table("documents").select("*").execute()
        files = res.data
    except Exception as e:
        print("DB fetch error:", e)
        return []
        
    out = []
    for f in files:
        try:
            pdf_data = supabase.storage.from_(BUCKET_NAME).download(f['name'])
            matches = search_in_pdf_bytes(pdf_data, query)
            if matches:
                out.append({"filename": f['name'], "matches": matches})
        except Exception as e:
            print(f"Error reading {f['name']}: {e}")
    return out

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/get_categories")
def get_categories():
    if not supabase: return jsonify([])
    try:
        res = supabase.table("categories").select("id, name, parent_category").execute()
        return jsonify(res.data)
    except Exception as e:
        return jsonify([])

@app.route("/add_category", methods=["POST"])
def add_category():
    if not supabase: return jsonify({"error": "DB not connected"}), 500
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    parent = (data.get("parent") or "").strip() or None
    if not name: return jsonify({"error": "नाम खाली छ"}), 400
    try:
        supabase.table("categories").insert({"name": name, "parent_category": parent}).execute()
        return jsonify({"message": f"'{name}' सफलतापूर्वक थपियो"})
    except Exception as e:
        return jsonify({"error": f"त्रुटि: {str(e)}"}), 400

@app.route("/list_pdfs")
def list_pdfs():
    if not supabase: return jsonify([])
    try:
        category = request.args.get("category", "")
        query = supabase.table("documents").select("name, category")
        if category: query = query.eq("category", category)
        res = query.execute()
        return jsonify(res.data)
    except Exception as e:
        return jsonify([])

import uuid  # फाइलको नाम बनाउन यो लाइब्रेरी चाहिन्छ

@app.route("/upload", methods=["POST"])
def upload():
    if not supabase: return jsonify({"error": "DB not connected"}), 500
    files = request.files.getlist("pdf_files")
    category = request.form.get("category", "अन्य")
    saved = []
    errors = []
    
    for f in files:
        if f and f.filename and f.filename.lower().endswith(".pdf"):
            original_name = os.path.basename(f.filename)  # नेपाली नाम (Display गर्न)
            
            # Supabase को लागि सुरक्षित नाम (UUID प्रयोग गर्ने)
            # यसले गर्दा नेपाली नामको समस्या आउँदैन
            safe_key = f"{uuid.uuid4().hex}.pdf"  
            
            try:
                # १. स्टोरेजमा सुरक्षित नाम (safe_key) ले अपलोड गर्ने
                supabase.storage.from_(BUCKET_NAME).upload(
                    path=safe_key, 
                    file=f.read(), 
                    file_options={"content-type": "application/pdf", "upsert": "true"}
                )
                
                # २. डेटाबेसमा भने असली नेपाली नाम (original_name) राख्ने
                existing = supabase.table("documents").select("id").eq("name", original_name).execute()
                if not existing.data:
                    supabase.table("documents").insert({
                        "name": original_name, 
                        "category": category,
                        "storage_key": safe_key
                    }).execute()
                saved.append(original_name)
                
            except Exception as e:
                print(f"Upload error: {e}")
                errors.append(str(e))
                
    return jsonify({"saved": saved, "errors": errors})

@app.route("/delete_pdf", methods=["POST"])
def delete_pdf():
    if not supabase: return jsonify({"error": "DB not connected"}), 500
    data = request.get_json() or {}
    if data.get("password", "") != DELETE_PASSWORD:
        return jsonify({"error": "गलत पासवर्ड!"}), 403
        
    filename = data.get("filename", "")  # यो नेपाली नाम हो
    
    try:
        # डेटाबेसबाट storage_key पत्ता लगाउने
        res = supabase.table("documents").select("storage_key").eq("name", filename).execute()
        if res.data:
            storage_key = res.data[0]['storage_key']
            # स्टोरेजबाट हटाउने
            supabase.storage.from_(BUCKET_NAME).remove([storage_key])
            
        # डेटाबेसबाट हटाउने
        supabase.table("documents").delete().eq("name", filename).execute()
        return jsonify({"message": "PDF सफलतापूर्वक मेटाइयो"})
    except Exception as e:
        return jsonify({"error": f"मेटाउन सकिएन: {e}"}), 500

@app.route("/pdf/<path:filename>")
def serve_pdf(filename):
    if not supabase: return "DB not connected", 500
    try:
        # डेटाबेसबाट storage_key पत्ता लगाउने
        res = supabase.table("documents").select("storage_key").eq("name", filename).execute()
        if not res.data:
            return "File not found in DB", 404
            
        storage_key = res.data[0]['storage_key']
        pdf_data = supabase.storage.from_(BUCKET_NAME).download(storage_key)
        return pdf_data, 200, {'Content-Type': 'application/pdf'}
    except Exception as e:
        print(f"serve_pdf error: {e}")
        return "File not found", 404

@app.route("/search", methods=["POST"])
def search():
    data = request.get_json() or {}
    query = (data.get("query") or "").strip()
    mode = data.get("mode", "all")
    filename = data.get("filename") if mode == "specific" else None
    if not query: return jsonify({"error": "खाली छ"}), 400
    results = collect_results(query, filename=filename)
    return jsonify({"results": results, "query": query})

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json() or {}
    query = (data.get("query") or "").strip()
    api_key = (data.get("api_key") or "").strip()
    filename = (data.get("filename") or "").strip() or None
    if not query: return jsonify({"error": "प्रश्न खाली छ"}), 400
    results = collect_results(query, filename=filename)
    
    if api_key:
        ctx_lines = []
        for g in results[:5]:
            for m in g["matches"][:6]:
                sec = m["section"] or "सन्दर्भ अज्ञात"
                ctx_lines.append(f"[PDF: {g['filename']} | पृष्ठ {m['page']} | {sec}] {m['snippet']}")
        context = "\n".join(ctx_lines) if ctx_lines else "कुनै मिल्दो सामग्री भेटिएन।"
        prompt = f"तपाईं नेपाली कानुनी सहायक हुनुहुन्छ। तल दिइएको PDF सामग्रीको आधारमा प्रश्नको जवाफ दिनुहोस्।\n\nPDF सामग्री:\n{context}\n\nप्रश्न: {query}\n"
        
        models_to_try = ["gemini-1.5-flash", "gemini-1.5-flash-8b", "gemini-1.5-pro", "gemini-2.0-flash"]
        last_error = ""
        for model in models_to_try:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
                if r.status_code == 200:
                    j = r.json()
                    if "candidates" in j and len(j["candidates"]) > 0:
                        return jsonify({"answer": j["candidates"][0]["content"]["parts"][0]["text"], "results": results, "mode": "gemini"})
                else:
                    try: last_error = r.json().get("error", {}).get("message", f"HTTP {r.status_code}")
                    except: last_error = f"HTTP {r.status_code}"
            except Exception as e:
                last_error = str(e)
                
        fallback_msg = f"⚠️ (Gemini API समस्या: {last_error}। स्थानीय खोज नतिजा तल छ।)\n\n"
        return jsonify({"answer": fallback_msg + "PDF हरूमा भेटिएका मिल्दा स्थानहरू तल देखाइएका छन्।", "results": results, "mode": "fallback"})
        
    return jsonify({"answer": "PDF हरूमा भेटिएका मिल्दा स्थानहरू तल देखाइएका छन्।", "results": results, "mode": "traditional"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
