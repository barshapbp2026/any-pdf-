import os
import fitz  # PyMuPDF
import streamlit as st

UPLOAD_DIR = "uploaded_pdfs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

st.set_page_config(
    page_title="नेपाली PDF दफा/नियम खोजी सफ्टवेयर", layout="wide"
)

st.title("🇳🇵 नेपाली PDF दफा, नियम तथा धारा खोजी प्रणाली")
st.write(
    "तपाईंको PDF फाइलहरू अपलोड गर्नुहोस् र दफा, नियम वा कीवर्डको आधारमा खोज्नुहोस्।"
)

st.sidebar.header("📁 PDF अपलोड गर्नुहोस्")
uploaded_files = st.sidebar.file_uploader(
    "नेपाली PDF फाइलहरू छान्नुहोस्", type=["pdf"], accept_multiple_files=True
)

saved_files = []
if uploaded_files:
  for uploaded_file in uploaded_files:
    file_path = os.path.join(UPLOAD_DIR, uploaded_file.name)
    with open(file_path, "wb") as f:
      f.write(uploaded_file.getbuffer())
    saved_files.append(uploaded_file.name)
  st.sidebar.success(
    f"जम्मा {len(uploaded_files)} वटा फाइल सफलतापूर्वक अपलोड भयो!"
  )

st.header("🔍 कागजातमा खोज्नुहोस्")
search_query = st.text_input(
    "खोज्न चाहेको शब्द, दफा, नियम वा धारा टाइप गर्नुहोस् (उदा: दफा १०, नियम ५):"
)

if st.button("खोज्नुहोस्"):
  if not search_query.strip():
    st.warning("कृपया खोजी गर्नको लागि केही शब्द वा दफा नम्बर लेख्नुहोस्।")
  else:
    all_files = os.listdir(UPLOAD_DIR)
    if not all_files:
      st.error(
        "कृपया पहिले साइडबारबाट कम्तीमा एउटा PDF फाइल अपलोड गर्नुहोस्।"
      )
    else:
      results_found = False
      st.subheader(f"📌 '{search_query}' को खोजी नतिजा:")

      for filename in all_files:
        if filename.endswith(".pdf"):
          pdf_path = os.path.join(UPLOAD_DIR, filename)

          try:
            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
              page = doc[page_num]
              text = page.get_text("text")

              if search_query in text:
                results_found = True
                st.markdown(f"---")
                st.success(
                  f"📄 **फाइल नाम:** `{filename}` | **पेज नम्बर:** {page_num + 1}"
                )

                lines = text.split("\n")
                relevant_lines = [
                    line
                    for line in lines
                    if search_query in line or "दफा" in line or "नियम" in line
                ]

                if relevant_lines:
                  st.markdown("**सम्बन्धित अंशहरू:**")
                  for r_line in relevant_lines[:5]:
                    st.write(f"- {r_line}")

                with open(pdf_path, "rb") as pdf_file:
                  st.download_button(
                      label=f"📥 यो PDF डाउनलोड गर्नुहोस् ({filename})",
                      data=pdf_file,
                      file_name=filename,
                      mime="application/pdf",
                      key=f"download_{filename}_{page_num}",
                  )
          except Exception as e:
            st.error(f"फाइल {filename} पढ्दा त्रुटि आयो: {e}")

      if not results_found:
        st.info("तपाईंले खोज्नुभएको शब्द कुनै पनि PDF मा फेला परेन।")
