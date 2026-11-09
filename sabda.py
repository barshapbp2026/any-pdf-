"""Strict heading-only word search for Nepali PDFs.

Only lines whose title text is visually bold and/or underlined are considered.
Appendix pages are excluded by detecting the first schedule heading and stopping
there; the caller supplies an already selected PDF byte stream.
"""
import re
import fitz
from nepali_text import page_text_unicode, search_key

def _line_styles(page):
    data = page.get_text("dict")
    drawings = page.get_drawings()
    underlines = []
    for drawing in drawings:
        for item in drawing.get("items", []):
            if item and item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) <= 2:
                    underlines.append((min(p1.x,p2.x), max(p1.x,p2.x), p1.y))
            elif item and item[0] == "re":
                r = fitz.Rect(item[1])
                if r.height <= 3:
                    underlines.append((r.x0, r.x1, r.y0))
    out=[]
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans=line.get("spans", [])
            txt="".join(s.get("text","") for s in spans).strip()
            if not txt: continue
            bold=any((int(s.get("flags",0)) & 16) or "bold" in s.get("font","").lower() for s in spans)
            rect=fitz.Rect(line.get("bbox",(0,0,0,0)))
            underlined=any(x1 >= rect.x0 and x0 <= rect.x1 and rect.y1-7 <= y <= rect.y1+4 for x0,x1,y in underlines)
            out.append({"text":txt,"bold":bool(bold),"underlined":bool(underlined),"rect":rect})
    return out

def search_headers(pdf_bytes, query, filename="", max_results=100):
    terms=[t for t in re.split(r"\s+", (query or "").strip()) if t]
    if not terms: return []
    doc=fitz.open(stream=pdf_bytes,filetype="pdf")
    found=[]; schedule_started=False
    try:
        for pno,page in enumerate(doc):
            for line in _line_styles(page):
                txt=line["text"]
                if re.match(r"^\s*(?:अनुसूची|अिुसूची)\s*[-–]?\s*[०-९0-9]+",txt):
                    schedule_started=True
                if schedule_started: continue
                if not (line["bold"] or line["underlined"]): continue
                key=search_key(txt)
                # All terms first; if no exact all-term match, individual terms are tried below.
                all_key=all(search_key(t) in key for t in terms)
                if all_key:
                    found.append({"page":pno+1,"section":"Header","header_text":txt,"snippet":txt,
                                  "bold":line["bold"],"underlined":line["underlined"],"filename":filename})
                    if len(found)>=max_results: return found
        if not found:
            for term in terms:
                tk=search_key(term)
                if not tk: continue
                for pno,page in enumerate(doc):
                    for line in _line_styles(page):
                        if re.match(r"^\s*(?:अनुसूची|अिुसूची)\s*[-–]?\s*[०-९0-9]+",line["text"]): continue
                        if not (line["bold"] or line["underlined"]): continue
                        if tk in search_key(line["text"]):
                            found.append({"page":pno+1,"section":"Header","header_text":line["text"],"snippet":line["text"],
                                          "bold":line["bold"],"underlined":line["underlined"],"filename":filename,"matched_term":term})
                            if len(found)>=max_results: return found
        return found
    finally: doc.close()
