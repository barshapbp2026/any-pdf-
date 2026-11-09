"""Appendix-only locator. Search proceeds from the end of the PDF."""
import re
import fitz
from nepali_text import page_text_unicode, search_key

_DIGITS=str.maketrans("०१२३४५६७८९","0123456789")
def _number(text):
    m=re.search(r"(?:अनुसूची|अिुसूची)\s*[-–]?\s*([०-९0-9]+)",text)
    return int(m.group(1).translate(_DIGITS)) if m else None

def search_anusuchi(pdf_bytes, query, filename="", max_results=100):
    q=(query or "").strip()
    qn=re.sub(r"[^0-9०-९]","",q).translate(_DIGITS)
    doc=fitz.open(stream=pdf_bytes,filetype="pdf")
    out=[]
    try:
        for pno in range(len(doc)-1,-1,-1):
            page=doc[pno]
            for block in page.get_text("dict").get("blocks",[]):
                for line in block.get("lines",[]):
                    txt="".join(s.get("text","") for s in line.get("spans",[])).strip()
                    n=_number(txt)
                    if n is None: continue
                    if qn and str(n)!=qn: continue
                    out.append({"page":pno+1,"section":f"अनुसूची {n}","header_text":txt,
                                "snippet":txt,"filename":filename,"bold":any((int(s.get("flags",0))&16) or "bold" in s.get("font","").lower() for s in line.get("spans",[]))})
                    if len(out)>=max_results:return out
        return out
    finally:doc.close()
