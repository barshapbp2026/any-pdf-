# नेपाली कानुनी PDF सहायक

नेपाली कानुनी तथा प्रशासनिक PDF कागजातभित्र दफा, धारा, नियम, निर्देशन, अनुसूची र शीर्षक (Header) छिटो खोज्न बनाइएको Flask आधारित वेब एप।

> **विकासको अवस्था:** परियोजना विकास तथा परीक्षण चरणमा छ। यहाँ वर्णन गरिएका लक्ष्यगत सुविधाहरू सबै हालको कोडमा पूर्ण रूपमा जडान वा उत्पादन-स्तरमा परीक्षण भइसकेका छन् भन्ने अर्थ लाग्दैन। विशेषगरी बहु-फोल्डरका छवटा छुट्टाछुट्टै खोज बाकस, OCR, सटीक हाइलाइट र दुई प्रकारका Gemini chat को पूर्ण एकीकरण अझै परीक्षण गर्नुपर्ने विषय हुन्।

## मुख्य उद्देश्य

- धेरै नेपाली PDF (लक्ष्य: २०० वा सोभन्दा बढी) एउटै प्रणालीबाट खोज्ने।
- एउटा PDF मात्र वा प्रयोगकर्ताले छानेका फोल्डरका PDF मात्र खोज्ने।
- PDF को मुख्य शीर्षक/हेडरमा आधारित खोजलाई सामान्य body-text खोजबाट अलग राख्ने।
- नेपाली र अंग्रेजी अंक, पुराना नेपाली font encoding तथा सम्भव भएसम्म scan गरिएका PDF सम्हाल्ने।
- Gemini API key उपलब्ध हुँदा थप प्रश्नोत्तर सुविधा दिने; key नभए पनि स्थानीय खोज चलिरहने बनाउने।

## खोजका क्षेत्र

लक्ष्यगत पूर्ण स्थानीय खोज UI मा दुवै खोज मोडमा यी छवटा स्वतन्त्र खोज बाकस रहनेछन्:

1. दफा
2. नियम
3. धारा
4. निर्देशन
5. अनुसूची
6. शब्द (Header मात्र)

हरेक बाकसले आफ्नो सम्बन्धित खोज प्रक्रिया मात्र चलाउनुपर्छ। उदाहरणका लागि, अनुसूची खोज्दा दफा/नियम खोज प्रक्रिया अनावश्यक रूपमा चलाउनु हुँदैन।

### निश्चित PDF भित्र खोज

प्रयोगकर्ताले एउटा फोल्डर र त्यस भित्रको एउटा PDF छान्छ। खोज सोही PDF मा सीमित हुन्छ।

### छानिएका फोल्डरहरूमा खोज

प्रयोगकर्ताले एक वा धेरै फोल्डरमा tick लगाएर Load Selected गर्छ। खोज केवल सफलतापूर्वक लोड भएका छानिएका PDF मा सीमित हुनुपर्छ। नतिजामा PDF को नाम, वास्तविक शीर्षक/हेडर र पृष्ठ नम्बर देखिनुपर्छ। कुनै फोल्डर वा PDF लोड नभए स्पष्ट सूचना देखाउनुपर्छ।

## Header पहिचानका नियम

नेपाली कानुनी PDF मा शीर्षक विभिन्न शैलीमा हुन सक्छन्:

- नम्बर वा शीर्षक bold भएको,
- शीर्षक bold र underline दुवै भएको,
- bold मात्र भएको,
- underline मात्र भएको।

नम्बर नेपाली वा अंग्रेजी अंकमा हुन सक्छ; जस्तै ५. वा 5. र आवश्यक परे ५क. जस्ता प्रत्यय पनि सम्हाल्नुपर्छ। नम्बर र शीर्षक एउटै line मा नभई छुट्टाछुट्टै line मा हुन सक्ने भएकाले नजिकका line सावधानीपूर्वक जोडेर जाँच गर्नुपर्छ।

Header पहिचान गर्दा सामान्य body paragraph लाई शीर्षक ठान्नु हुँदैन। PDF मा font/style metadata नभए bold/underline भरपर्दो रूपमा पहिचान गर्न नसकिन सक्छ। यस्तो अवस्थामा अनिश्चिततालाई स्पष्ट देखाउनुपर्छ।

## अनुसूची खोज — anusuchi.py

अनुसूची खोज मुख्य दफा/धारा/नियमको क्रमपछि आउने अनुसूची शीर्षक पत्ता लगाउन समर्पित मोड्युल हो।

- PDF को अन्तिम पृष्ठतिरबाट उल्टो दिशामा खोजेर अनुसूचीका सम्भावित शीर्षक पहिचान गर्ने।
- नेपाली तथा अंग्रेजी अंकलाई एउटै ढाँचामा मिलाएर तुलना गर्ने।
- कुनै PDF मा अनुसूची नभए त्रुटि नदिई “अनुसूची भेटिएन” देखाउने।
- सही क्रम: मुख्य दफा/धारा/नियम पहिले; त्यसपछि अनुसूची–१, अनुसूची–२ हुँदै अगाडि।
- वास्तविक शीर्षक, PDF नाम र पृष्ठ नम्बर देखाउने।

## Header भित्र शब्द खोज — sabda.py

यो मोड्युलको उद्देश्य PDF को सम्पूर्ण text वा body paragraph मा होइन, मुख्य भागका शीर्षकहरूमा मात्र शब्द खोज्नु हो।

- Bold, underline वा bold+underline भएको header line मात्र जाँच्ने।
- अनुसूची खण्डलाई यस खोजबाट अलग राख्ने।
- धेरै शब्द भएको query मा पहिले सबै शब्द एउटै header मा छन् कि छैनन् जाँच्ने।
- संयुक्त query मा नतिजा नभए प्रत्येक शब्दलाई छुट्टाछुट्टै जाँच्ने।
- केही नभेटिए स्पष्ट सन्देश देखाउने: “यो शब्द कुनै Header मा भेटिएन।”

## OCR तथा पुराना नेपाली Font

PDF text layer उपलब्ध भए PyMuPDF बाट text/style निकालिन्छ। Scan गरिएको वा image-only PDF का लागि OCR fallback आवश्यक पर्छ। हालको कोडमा OCR helper र pytesseract/Pillow dependency समावेश छन्; deployment वातावरणमा Tesseract र Nepali language data (nep) पनि स्थापना तथा परीक्षण हुनुपर्छ।

पुराना Preeti/Himali-जस्ता font का लागि nepali_text.py मा conversion र text normalization helper छन्। सबै legacy font encoding एउटै हुँदैनन्, त्यसैले वास्तविक PDF नमुनाबाट परीक्षण र mapping सुधार आवश्यक छ।

## Gemini AI सुविधा — वैकल्पिक

Gemini API key नदिँदा पनि स्थानीय PDF खोज उपलब्ध रहनुपर्छ। Key उपलब्ध गराउँदा लक्षित प्रणालीमा यी दुई छुट्टाछुट्टै chat मोड हुनेछन्:

1. **PDF भित्र खोज:** छानिएका/लोड गरिएका PDF बाट सम्बन्धित कागजात, पृष्ठ र अंश पहिचान गरेर प्रश्नको उत्तर दिने।
2. **PDF बाहिर/इन्टरनेटसहित खोज:** PDF का प्रमाणसँगै बाह्य वेब स्रोत आवश्यक भए तिनलाई पनि प्रयोग गर्ने।

धेरै PDF लाई प्रत्येक prompt मा पूरा पठाउनुको सट्टा पहिले सान्दर्भिक PDF/page/chunk छानेर मात्र विश्लेषण गर्नुपर्छ। उत्तरमा PDF नाम, पृष्ठ नम्बर र सम्बन्धित दफा/शीर्षक उल्लेख गर्नुपर्छ। बाह्य इन्टरनेट खोज उपलब्ध नभए त्यसो भएको स्पष्ट बताउनुपर्छ।

**सुरक्षा:** वास्तविक API key source code, GitHub commit वा log मा नराख्नुहोस्। Environment secret वा सुरक्षित user-session व्यवस्था प्रयोग गर्नुहोस्। API key प्रयोगकर्ताले आफ्नो इच्छाले मात्र दिनुपर्छ।

## प्रविधि

- Python र Flask
- PyMuPDF (fitz) — PDF text, page र style extraction
- pytesseract र Pillow — OCR fallback
- Requests — GitHub/API अनुरोध
- python-dotenv — local environment configuration
- HTML, CSS, JavaScript — web UI
- Gemini API — वैकल्पिक AI प्रश्नोत्तर

## मुख्य फाइलहरू

| फाइल | जिम्मेवारी |
|---|---|
| app.py | Flask application, PDF listing/search र HTTP endpoints |
| nepali_text.py | नेपाली text normalization, legacy-font conversion र search key |
| sabda.py | Header-only शब्द खोज |
| anusuchi.py | अनुसूची शीर्षक खोज |
| templates/index.html | Web interface |
| requirements.txt | Python dependencies |
| pdfs/ | Repository मा राखिएका PDF तथा folder संरचना |

## स्थानीय रूपमा चलाउने

Python 3.10 वा सोभन्दा माथिको संस्करण सिफारिस गरिन्छ।

    git clone https://github.com/barshapbp2026/any-pdf-.git
    cd any-pdf-
    python -m venv .venv

Linux/macOS:

    source .venv/bin/activate

Windows PowerShell:

    .venv\Scripts\Activate.ps1

Dependencies स्थापना:

    pip install -r requirements.txt

Flask app चलाउनुहोस्:

    python app.py

त्यसपछि browser मा app ले देखाएको local address खोल्नुहोस्। Production deployment मा Gunicorn जस्तो WSGI server प्रयोग गर्नुहोस्।

### OCR स्थापना

OCR चाहिने deployment मा Python package मात्र पर्याप्त हुँदैन। Operating system मा Tesseract OCR र उपलब्ध भए Nepali language data स्थापना गर्नुपर्छ। Debian/Ubuntu उदाहरण:

    sudo apt-get update
    sudo apt-get install -y tesseract-ocr tesseract-ocr-nep

Nepali trained data उपलब्ध नभए OCR fallback ले English model प्रयोग गर्न सक्छ, तर नेपाली पहिचानको गुणस्तर घट्न सक्छ।

## PDF राख्ने तरिका

PDF हरूलाई repository को pdfs/ directory भित्र विषय वा विभागअनुसार folder बनाएर राख्न सकिन्छ। उदाहरण:

    pdfs/
    ├── ऐन/
    │   └── उदाहरण-ऐन.pdf
    ├── नियमावली/
    │   └── उदाहरण-नियमावली.pdf
    └── निर्देशिका/
        └── उदाहरण-निर्देशिका.pdf

नयाँ PDF थपेपछि listing/cache refresh हुने व्यवस्था deployment मा सुनिश्चित गर्नुपर्छ। PDF ठूलो भए GitHub को file-size वा repository size सीमाका कारण Git LFS अथवा object storage आवश्यक पर्न सक्छ। गोप्य वा प्रतिबन्धित कागजात सार्वजनिक repository मा नराख्नुहोस्।

## नतिजा र PDF खोल्ने व्यवहार

खोज नतिजाले कम्तीमा यी विवरण दिनुपर्छ:

- PDF को नाम
- भेटिएको दफा/धारा/नियम/निर्देशन/अनुसूची वा Header को वास्तविक शीर्षक
- PDF को page number
- OCR बाट आएको भए OCR भनेर चिनाउने सूचना

नतिजा खोल्दा सम्बन्धित PDF पृष्ठमा जानुपर्छ र सम्भव भएसम्म ठ्याक्कै मिलेको header line मात्र गाढा रातो (dark red) रंगले highlight हुनुपर्छ। पूरै पृष्ठ वा body text highlight गर्नु हुँदैन।

## परीक्षण सूची

नयाँ सुविधा merge/deploy गर्नु अघि कम्तीमा यी परीक्षण गर्नुपर्छ:

- [ ] एउटा PDF मात्र छानेर खोज्दा अरू PDF बाट नतिजा नआएको।
- [ ] धेरै folder tick गरी Load Selected गर्दा छानिएका PDF मात्र लोड भएको।
- [ ] दफा, नियम, धारा, निर्देशन, अनुसूची र शब्द खोज एकअर्काबाट स्वतन्त्र चलेको।
- [ ] ५, 5, ५क जस्ता नम्बर र dot भएका heading परीक्षण भएको।
- [ ] Bold, underline, bold+underline तथा अलग-अलग line मा भएको शीर्षक परीक्षण भएको।
- [ ] एउटै header धेरै PDF मा भए सबै नतिजा देखिएको।
- [ ] अनुसूची नभएको PDF मा error नआएको।
- [ ] अनुसूची खोज अन्तिम पृष्ठबाट सुरु भई सही शीर्षक/पृष्ठमा पुगेको।
- [ ] शब्द खोजले body paragraph र अनुसूचीमा भएका शब्दलाई नसमेटेको।
- [ ] OCR text-based PDF र scan/image-only PDF दुवैमा परीक्षण भएको।
- [ ] PDF viewer ले सही पृष्ठ खोलेको र केवल मिलेको header line highlight गरेको।
- [ ] Gemini key नभए पनि स्थानीय खोज चलेको।
- [ ] गलत/अवैध Gemini key मा स्पष्ट error आएको र सही key मा test सफल भएको।
- [ ] नयाँ PDF थपेपछि listing र खोजमा देखिएको।
- [ ] मोबाइल र desktop दुवैमा UI प्रयोग गर्न मिलेको।

## हालको सीमितता र विकास प्राथमिकता

यो branch मा sabda.py र anusuchi.py का प्रारम्भिक implementation तथा app.py मा तिनका import/cache defaults थपिएका छन्। यी मोड्युलहरू अझै विभिन्न वास्तविक PDF नमुनामा परीक्षण, Flask route/API integration, UI wiring, OCR verification र highlight accuracy जाँचको विषय हुन्। त्यसैले अहिले नै सबै २००+ PDF मा पूर्ण शुद्धता वा सबै उल्लेखित UI सुविधा तयार भएको दाबी गरिएको छैन।

अर्को विकास प्राथमिकता:

1. छवटै local search box लाई Single PDF र Multiple Folders दुवैमा पूर्ण रूपमा जोड्ने।
2. Folder selection तथा Load Selected को server-side scope कडाइका साथ लागू गर्ने।
3. Header detection मा bold/underline र line grouping सुधार्ने; false positive घटाउने।
4. अनुसूची र Header-only शब्द खोजलाई वास्तविक PDF नमुनाबाट परीक्षण गर्ने।
5. OCR fallback, page coordinates र dark-red header-only highlight प्रमाणित गर्ने।
6. Gemini key validation, selected-PDF context retrieval र दुई chat मोड सुरक्षित रूपमा जोड्ने।
7. ठूला PDF संग्रहका लागि caching/indexing र incremental refresh थप्ने।

## योगदान र सुधार

Bug report गर्दा सम्भव भए PDF को नाम, पृष्ठ नम्बर, खोजेको शब्द/नम्बर, अपेक्षित header र वास्तवमा आएको नतिजा उल्लेख गर्नुहोस्। पुरानो font वा scan समस्या देखाउन सम्बन्धित पृष्ठको नमुना उपयोगी हुन्छ। संवेदनशील कागजात सार्वजनिक issue मा upload नगर्नुहोस्।

## License

यस repository मा छुट्टै LICENSE फाइलले अनुमति नदिएसम्म code र PDF सामग्रीको पुनःप्रयोगका अधिकार स्वतः खुला भएको मान्नु हुँदैन। PDF सामग्रीको copyright/प्रयोग अनुमति सम्बन्धित अधिकारधनीको नियमअनुसार हुन्छ।
