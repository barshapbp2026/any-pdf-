import os
from flask import Flask, redirect, render_template_string, request, url_for
import fitz  # PyMuPDF

app = Flask(__name__)
UPLOAD_DIR = "/tmp/uploaded_pdfs"  # Vercel मा /tmp फोल्डर मात्र लेख्न मिल्छ
os.makedirs(UPLOAD_DIR, exist_ok=True)

HTML_TEMPLATE = """
<!doctype html>
<html lang="ne">
<head>
    <meta charset="utf-8">
    <title>नेपाली PDF दफा/नियम खोजी</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #f4f4f9; color: #333; }
        h1 { color: #2c3e50; }
        .box { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }
        input[type="text"], input[type="file"] { padding: 10px; width: 60%; margin-right: 10px; }
        button { padding: 10px 20px; background: #3498db; color: white; border: none; border-radius: 4px; cursor: pointer; }
        button:hover { background: #2980b9; }
        .result { background: #e8f8f5; padding: 15px; border-left: 5px solid #2ecc71; margin-top: 10px; }
    </style>
</head>
<body>
    <h1>🇳🇵 नेपाली PDF दफा, नियम तथा धारा खोजी प्रणाली</h1>
    
    <div class="box">
        <h3>📁 PDF अपलोड गर्नुहोस्</h3>
        <form method="POST" enctype="multipart/form-data">
            <input type="file" name="pdf_files" multiple accept=".pdf">
            <button type="submit">अपलोड गर्नुहोस्</button>
        </form>
        {% if message %}
            <p style="color: green;"><b>{{ message }}</b></p>
        {% endif %}
    </div>

    <div class="box">
        <h3>🔍 कागजातमा खोज्नुहोस्</h3>
        <form method="POST">
            <input type="text" name="query" placeholder="उदा: दफा १०, नियम ५" value="{{ query or '' }}">
            <button type="submit">खोज्नुहोस्</button>
        </form>
    </div>

    {% if results %}
        <div class="box">
            <h3>📌 खोज नतिजा ('{{ query }}'):</h3>
            {% for res in results %}
                <div class="result">
                    <p><b>फाइल नाम:</b> {{ res.filename }} | <b>पेज नम्बर:</b> {{ res.page }}</p>
                    <p><b>अंश:</b> {{ res.text }}</p>
                </div>
            {% endfor %}
        </div>
    {% elif searched %}
        <p style="color: red;">कुनै नतिजा फेला परेन।</p>
    {% endif %}
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def index():
  message = ""
  results = []
  query = ""

  if request.method == "POST":
    # यदि फाइल अपलोड गरिएको छ भने
    if "pdf_files" in request.files:
      files = request.files.getlist("pdf_files")
      for file in files:
        if file.filename:
          file.save(os.path.join(UPLOAD_DIR, file.filename))
      message = f"जम्मा {len(files)} वटा फाइल सफलतापूर्वक अपलोड भयो!"

    # यदि खोजी (Search) गरिएको छ भने
    if "query" in request.form:
      query = request.form.get("query")
      if query:
        all_files = os.listdir(UPLOAD_DIR)
        for filename in all_files:
          if filename.endswith(".pdf"):
            pdf_path = os.path.join(UPLOAD_DIR, filename)
            try:
              doc = fitz.open(pdf_path)
              for page_num in range(len(doc)):
                text = doc[page_num].get_text("text")
                if query in text:
                  results.append({
                      "filename": filename,
                      "page": page_num + 1,
                      "text": text[:300] + "...",
                  })
            except Exception as e:
              pass

  return render_template_string(
      HTML_TEMPLATE,
      message=message,
      results=results,
      query=query,
      searched=bool(query),
  )


if __name__ == "__main__":
  app.run(debug=True)