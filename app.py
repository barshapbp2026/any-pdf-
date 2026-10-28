import os
from flask import Flask, render_template, request
import fitz  # PyMuPDF

app = Flask(__name__)
UPLOAD_DIR = "/tmp/uploaded_pdfs"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.route("/", methods=["GET", "POST"])
def index():
  message = ""
  results = []
  query = ""
  selected_category = request.form.get("category", "1")

  if request.method == "POST":
    # फाइल अपलोड
    if "pdf_files" in request.files:
      files = request.files.getlist("pdf_files")
      for file in files:
        if file.filename:
          file.save(os.path.join(UPLOAD_DIR, file.filename))
      message = f"जम्मा {len(files)} वटा फाइल सफलतापूर्वक अपलोड भयो!"

    # खोजी (Search)
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

  return render_template(
      "index.html",
      message=message,
      results=results,
      query=query,
      searched=bool(query),
  )


if __name__ == "__main__":
  app.run(debug=True)