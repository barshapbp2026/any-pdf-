import os
import re
import requests
from flask import Flask, render_template, request, jsonify, send_from_directory
import fitz  # PyMuPDF

app = Flask(__name__)
UPLOAD_DIR = "/tmp/uploaded_pdfs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

DELETE_PASSWORD = os.environ.get("DELETE_PASSWORD", "admin123")
GEMINI_MODEL = "gemini-2.0-flash"

NEPALI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
def _norm(t):
    return t.lower().translate(NEPALI_DIGITS)

SECTION_RE = re.compile(r"((?:धारा|दफा|नियम|उपनियम)\s*[\d०-९]+)")

def search_in_pdf(pdf_path, query, max_results=30):
    results = []
    q_norm = _norm(query.strip())
    if not q_norm:
        return results
    try:
        doc = fitz.open(pdf_path)
        carried_section = None
        for page_num in range(len(doc)):
            page_text = doc[page_num].get_text("text")
            page_norm = _norm(page_text)

            section_positions = [(m.start(), m.group()) for m in SECTION_RE.finditer(page_norm)]

            idx = 0
            while True:
                pos = page_norm.find(q_norm, idx)
                if pos == -1:
                    break
                section = carried_section
                for sp, sname in section_positions:
                    if sp <= pos:
                        section = sname
                start = max(0, pos - 80)
                end = min(len(page_text), pos + len(query) + 140)
                snippet = page_text[start:end].replace("\n", " ").strip()
                results.append({"page": page_num + 1, "section": section, "snippet": snippet})
                idx = pos + 1
                if len(results) >= max_results:
                    break
            if section_positions:
                carried_section = section_positions[-1][1]
            if len(results) >= max_results:
                break
        doc.close()
    except Exception as e:
        print(f"[search_in_pdf] {pdf_path}: {e}")
    return results

def collect_results(query, filename=None):
    if filename:
        files = [filename] if os.path.exists(os.path.join(UPLOAD_DIR, filename)) else []
    else:
        files = [f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf")]
    out = []
    for f in files:
        path = os.path.join(UPLOAD_DIR, f)
        matches = search_in_pdf(path, query)
        if matches:
            out.append({"filename": f, "matches": matches})
    return out

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/list_pdfs")
def list_pdfs():
    files = sorted(f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".pdf"))
    return jsonify(files)

@app.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("pdf_files")
    saved = []
    for f in files:
        if f and f.filename and f.filename.lower().endswith(".pdf"):
            safe = os.path.basename(f.filename)
            f.save(os.path.join(UPLOAD_DIR, safe))
            saved.append(safe)
    return jsonify({"saved": saved})

@app.route("/delete", methods=["POST"])
def delete():
    data = request.get_json() or {}
    if data.get("password", "") != DELETE_PASSWORD:
        return jsonify({"error": "गलत पासवर्ड। PDF मेटाउन सकिएन।"}), 403
    filename = os.path.basename(data.get("filename", ""))
    path = os.path.join(UPLOAD_DIR, filename)
    if not filename or not os.path.exists(path):
        return jsonify({"error": "फाइल भेटिएन।"}), 404
    os.remove(path)
    return jsonify({"message": f"{filename} सफलतापूर्वक हटाइयो।"})

@app.route("/pdf/<path:filename>")
def serve_pdf(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.route("/search", methods=["POST"])
def search():
    data = request.get_json() or {}
    query = (data.get("query") or "").strip()
    mode = data.get("mode", "all")
    filename = data.get("filename") if mode == "specific" else None
    if not query:
        return jsonify({"error": "खोज शब्द खाली छ।"}), 400
    results = collect_results(query, filename=filename)
    return jsonify({"results": results, "query": query})

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json() or {}
    query = (data.get("query") or "").strip()
    api_key = (data.get("api_key") or "").strip()
    filename = (data.get("filename") or "").strip() or None
    if not query:
        return jsonify({"error": "प्रश्न खाली छ।"}), 400

    results = collect_results(query, filename=filename)

    if api_key:
        ctx_lines = []
        for g in results[:5]:
            for m in g["matches"][:6]:
                sec = m["section"] or "सन्दर्भ अज्ञात"
                ctx_lines.append(f"[PDF: {g['filename']} | पृष्ठ {m['page']} | {sec}] {m['snippet']}")
        context = "
".join(ctx_lines) if ctx_lines else "कुनै मिल्दो सामग्री भेटिएन।"

        prompt = (
            "तपाईं नेपाली कानुनी सहायक हुनुहुन्छ। तल दिइएको PDF सामग्रीको आधारमा "
            "प्रयोगकर्ताको प्रश्नको जवाफ दिनुहोस्। जवाफमा स्पष्ट रूपमा उल्लेख गर्नुहोस् "
            "— कुन धारा/दफा/नियम/उपनियममा छ र कुन PDF को कुन पृष्ठमा छ।

"
            f"PDF सामग्री:
{context}

प्रश्न: {query}
"
        )
        
        # Fallback Models
        models_to_try = ["gemini-3.8-flash", "gemini-1.5-flash"]
        
        for model in models_to_try:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=45)
                j = r.json()
                if r.status_code == 200:
                    answer = j["candidates"][0]["content"]["parts"][0]["text"]
                    return jsonify({"answer": answer, "results": results, "mode": "gemini"})
            except Exception as e:
                print(f"[Gemini Error {model}]: {e}")
                continue  # यदि यो मोडेल फेल भयो भने अर्को मोडेल प्रयास गर्ने

        # यदि सबै मोडेल फेल भए (वा कोटा सकियो भने) स्थानीय खोजमा Fallback गर्ने
        fallback_msg = "⚠️ (Gemini API मा समस्या आयो वा कोटा सकियो। तल PDF भित्रको स्थानीय खोज नतिजा देखाइएको छ।)

"
        if not results:
            return jsonify({"answer": fallback_msg + f"'{query}' का लागि कुनै PDF मा सामग्री भेटिएन।", "results": [], "mode": "fallback"})
        return jsonify({"answer": fallback_msg + "PDF हरूमा भेटिएका मिल्दा स्थानहरू तल देखाइएका छन्।", "results": results, "mode": "fallback"})

    # यदि प्रयोगकर्ताले API Key नै हालेको छैन भने (Traditional Search)
    if not results:
        return jsonify({
            "answer": f"'{query}' का लागि कुनै PDF मा सामग्री भेटिएन। (Gemini API Key हालेर थप विस्तृत जवाफ पाउन सकिन्छ।)",
            "results": [],
            "mode": "traditional",
        })
    return jsonify({
        "answer": "PDF हरूमा भेटिएका मिल्दा स्थानहरू तल देखाइएका छन्। विस्तृत व्याख्याको लागि Gemini API Key हाल्नुहोस्।",
        "results": results,
        "mode": "traditional",
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
