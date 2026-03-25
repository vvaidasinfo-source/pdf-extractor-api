"""VIN Extractor API v3.0 - su pilnu laukų atpažinimu"""
import re, io, logging, platform, os
from typing import Optional, List, Dict, Any, Tuple
import pdfplumber, pytesseract
from pdf2image import convert_from_bytes
from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from dotenv import load_dotenv;

    load_dotenv()
except ImportError:
    pass
if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
app = FastAPI(title="VIN Extractor API", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TRUCK_WMI = {"YV2", "YV4", "YS2", "WMA", "WDB", "WDC", "XLR", "XLE", "ZCF", "VF6", "XTC", "1XP", "2NP", "1FU", "3AL",
             "1HT", "1M1", "WSM", "WS9", "WS1", "XMC", "X3F", "WJM", "YE2", "SFP", "SF9", "3H3", "1DW"}
TRANSLITERATION = {'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5, 'F': 6, 'G': 7, 'H': 8, 'J': 1, 'K': 2, 'L': 3, 'M': 4,
                   'N': 5, 'P': 7, 'R': 9, 'S': 2, 'T': 3, 'U': 4, 'V': 5, 'W': 6, 'X': 7, 'Y': 8, 'Z': 9}
WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def _transliterate(c):
    return int(c) if c.isdigit() else TRANSLITERATION.get(c.upper())


def _decode_year(c):
    t = {'A': 1980, 'B': 1981, 'C': 1982, 'D': 1983, 'E': 1984, 'F': 1985, 'G': 1986, 'H': 1987, 'J': 1988, 'K': 1989,
         'L': 1990, 'M': 1991, 'N': 1992, 'P': 1993, 'R': 1994, 'S': 1995, 'T': 1996, 'V': 1997, 'W': 1998, 'X': 1999,
         'Y': 2000, '1': 2001, '2': 2002, '3': 2003, '4': 2004, '5': 2005, '6': 2006, '7': 2007, '8': 2008, '9': 2009}
    return t.get(c)


def _decode_wmi(wmi):
    k = {"YV2": "Volvo Trucks", "YV4": "Volvo Trucks", "YS2": "Scania", "WMA": "MAN", "WDB": "Mercedes-Benz Trucks",
         "WDC": "Mercedes-Benz Trucks", "XLR": "DAF Trucks", "XLE": "DAF Trucks", "ZCF": "Iveco",
         "VF6": "Renault Trucks", "XTC": "KAMAZ", "1XP": "Kenworth", "2NP": "Peterbilt", "1FU": "Freightliner",
         "3AL": "Freightliner", "1HT": "International Trucks", "1M1": "Mack Trucks", "WSM": "Schmitz Cargobull",
         "WS9": "Schmitz Cargobull", "XMC": "Krone", "WJM": "Kogel", "YE2": "Wielton", "SFP": "Schwarzmuller"}
    return k.get(wmi, "Nezinomas ({})".format(wmi))


def normalize_vin(vin):
    r = list(vin)
    for i in list(range(0, 3)) + list(range(11, 17)):
        if r[i] == 'O': r[i] = '0'
    return ''.join(r)


def validate_vin(vin):
    vin = vin.strip().upper()
    errors, warnings = [], []
    if len(vin) != 17:
        errors.append("Neteisingas ilgis: {} (reikia 17)".format(len(vin)))
        return {"vin": vin, "valid": False, "errors": errors, "warnings": warnings, "decoded": {}}
    forbidden = [c for c in vin if c in "IOQ"]
    if forbidden: errors.append("Draudziami simboliai: {}".format(set(forbidden)))
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin): errors.append("Neleistini simboliai")
    if errors: return {"vin": vin, "valid": False, "errors": errors, "warnings": warnings, "decoded": {}}
    check = vin[8]
    total = sum(_transliterate(c) * WEIGHTS[i] for i, c in enumerate(vin) if _transliterate(c) is not None)
    rem = total % 11
    expected = "X" if rem == 10 else str(rem)
    if check != expected: warnings.append("Checksum neatitikimas: turetu buti '{}', yra '{}'".format(expected, check))
    wmi = vin[:3]
    decoded = {"wmi": wmi, "manufacturer": _decode_wmi(wmi), "vds": vin[3:9], "check_digit": vin[8],
               "model_year": _decode_year(vin[9]), "plant_code": vin[10], "serial_number": vin[11:],
               "is_truck": wmi in TRUCK_WMI}
    return {"vin": vin, "valid": True, "errors": [], "warnings": warnings, "decoded": decoded}


# ---------------------------------------------------------------------------
# Regitros laukų atpažinimas
# ---------------------------------------------------------------------------
FIELD_LABELS = {
    "A": "Registracijos numeris", "B": "Reg. data", "B1": "Reg. LT data", "B2": "Modelio metai",
    "D1": "Marke", "D2": "Tipas/variantas", "D3": "Modelis", "E": "VIN",
    "F1": "Max mase (kg)", "F2": "Leistina mase (kg)", "F3": "Junginio mase (kg)",
    "F4": "Puspriekab. mase (kg)", "F5": "Puspriekab. asiu mase (kg)",
    "G": "Tuscia mase (kg)", "H": "Galiojimo pabaiga", "I": "Dokumento data",
    "J": "Kategorija", "J1": "Kebulo kodas (nac)", "J2": "Kebulo kodas (ES)",
    "K": "Tipo patv. nr", "K1": "Nac. patv. nr",
    "P1": "Variklio turis (cm3)", "P2": "Galia (kW)", "P3": "Degalai", "P4": "Sukiai", "P5": "Variklio kodas",
    "Q": "Galios/mases santykis", "R": "Spalva", "S1": "Sedimu vietu sk", "S2": "Stovima vietu sk",
    "T": "Max greitis (km/h)", "V7": "CO2 (g/km)", "V9": "Tersalu lygis", "V10": "Hibridine",
    "C11": "Valdytojas", "C12": "Valdytojo vardas", "C13": "Valdytojo adresas", "C14": "Valdytojo kodas",
    "C21": "Savininkas", "C22": "Savininko vardas", "C23": "Savininko adresas", "C24": "Savininko kodas",
}


def parse_regitra_fields(text: str) -> Dict[str, Any]:
    """Ištraukia visus Regitros dokumento laukus TIK is duomenu puslapio."""
    fields = {}

    # Naudoti tik pirmą puslapį - sustoti ties Pastabos skyriumi
    stop_markers = ["Pastabos", "PASTABOS", "valstybinis registracijos numeris",
                    "pirmosios registracijos data", "gamybine marke"]
    data_text = text
    for marker in stop_markers:
        idx = text.find(marker)
        if idx > 200:  # Ignoruoti jei per anksti (gali buti antraste)
            data_text = text[:idx]
            break

    def extract(pattern, key):
        """Ištraukia reikšmę pagal šabloną. Tikrina kad reikšmė nėra aprašymas."""
        if key in fields:
            return
        m = re.search(pattern, data_text, re.IGNORECASE | re.MULTILINE)
        if not m:
            return
        val = m.group(1).strip().rstrip('-').strip()
        # Praleisti jei tuščia arba brūkšneliai
        if not val or val in ('--', '-', ''):
            return
        # Praleisti jei reikšmė per ilga ir atrodo kaip aprašymas (>60 simbolių su žodžiais)
        if len(val) > 60 and ' ' in val and not any(c.isdigit() for c in val[:10]):
            return
        fields[key] = {"label": FIELD_LABELS.get(key, key), "value": val}

    extract(r'(?<![A-Z])A\s{1,10}([A-Z]{2,4}\d{3,6})\b', 'A')
    extract(r'\bB\s{1,10}(\d{4}-\d{2}-\d{2})', 'B')
    extract(r'\bB\.?1\s{1,10}(\d{4}-\d{2}-\d{2})', 'B1')
    extract(r'\bB\.?2\s{1,10}([\d\-/]+)', 'B2')
    extract(r'\bD\.?1\s{1,10}([A-Z][A-Z\s]{2,20}?)(?:\n|\s{3,})', 'D1')
    extract(r'\bD\.?2\s{1,10}([^\n]{3,60})', 'D2')
    extract(r'\bD\.?3\s{1,10}([^\n]{2,40})', 'D3')
    extract(r'\bE\s{1,15}([A-HJ-NPR-Z0-9]{17})\b', 'E')
    extract(r'\bF\.?1\s{1,10}(\d{3,6})', 'F1')
    extract(r'\bF\.?2\s{1,10}(\d{3,6})', 'F2')
    extract(r'\bF\.?3\s{1,10}(\d{3,6})', 'F3')
    extract(r'\bF\.?4\s{1,10}(\d{3,6})', 'F4')
    extract(r'\bF\.?5\s{1,10}(\d{3,6})', 'F5')
    extract(r'\bG\s{1,10}(\d{3,6})', 'G')
    extract(r'\bH\s{1,10}(\d{4}-\d{2}-\d{2})', 'H')
    extract(r'\bI\s{1,10}(\d{4}-\d{2}-\d{2})', 'I')
    extract(r'\bJ\s{1,5}([A-Z]\d)\b', 'J')
    extract(r'\bJ\.?1\s{1,10}([^\n]{1,20})', 'J1')
    extract(r'\bJ\.?2\s{1,10}([A-Z]{2,6})\b', 'J2')
    extract(r'\bK\s{1,5}([^\n]{5,50})', 'K')
    extract(r'\bK\.?1\s{1,5}([^\n]{3,50})', 'K1')
    extract(r'\bP\.?1\s{1,10}(\d{3,6})', 'P1')
    extract(r'\bP\.?2\s{1,10}(\d{2,4})', 'P2')
    extract(r'\bP\.?3\s{1,10}([A-Za-z]{3,15})', 'P3')
    extract(r'\bP\.?4\s{1,10}(\d{3,6})', 'P4')
    extract(r'\bP\.?5\s{1,10}([^\n]{2,30})', 'P5')
    extract(r'\bR\s{1,10}([A-Z]{3,12})\b', 'R')
    extract(r'\bS\.?1\s{1,10}(\d{1,3})', 'S1')
    extract(r'\bS\.?2\s{1,10}(\d{1,3})', 'S2')
    extract(r'\bT\s{1,10}(\d{2,3})\b', 'T')
    extract(r'\bV\.?7\s{1,10}([^\n]{3,30})', 'V7')
    extract(r'\bV\.?9\s{1,10}([^\n]{5,60})', 'V9')
    extract(r'\bV\.?10\s{0,5}\)?([^\n]{1,10})', 'V10')
    extract(r'\bC\.?1\.?1\s{1,5}([^\n]{3,80})', 'C11')
    extract(r'\bC\.?1\.?2\s{1,5}([^\n]{2,40})', 'C12')
    extract(r'\bC\.?1\.?3\s{1,5}([^\n]{3,60})', 'C13')
    extract(r'[(\[]C\.?1\.?4[)\]]\s{0,5}(\d{7,12})', 'C14')
    extract(r'\bC\.?2\.?1\s{1,5}([^\n]{3,80})', 'C21')
    extract(r'\bC\.?2\.?2\s{1,5}([^\n]{2,40})', 'C22')
    extract(r'\bC\.?2\.?3\s{1,5}([^\n]{3,60})', 'C23')
    extract(r'[(\[]C\.?2\.?4[)\]]\s{0,5}(\d{7,12})', 'C24')

    return fields


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------
VIN_PATTERN = re.compile(r'[A-HJ-NPR-Z0-9]{17}')
REGITRA_E_PATTERN = re.compile(r'(?:^|\s)E\s{1,15}([A-HJ-NPR-Z0-9]{17})(?:\s|$)', re.MULTILINE)


def fix_ocr_errors(text):
    text = re.sub(r'\|[\|\[]', 'E ', text)
    text = re.sub(r'\[[\|\[]', 'E ', text)
    return text


def extract_vin_candidates_fuzzy(text):
    candidates = set()
    upper = re.sub(r'\s', '', text.upper())
    for match in re.finditer(r'[A-HJ-NPR-Z0-9]{15,20}', upper):
        seq = match.group();
        n = len(seq)
        if n == 17:
            candidates.add(seq)
        elif 18 <= n <= 20:
            for i in range(n):
                c = seq[:i] + seq[i + 1:]
                if len(c) == 17: candidates.add(c)
    return list(candidates)


def find_vins_in_text(text):
    text = fix_ocr_errors(text)
    upper = text.upper()
    candidates = set()
    for m in REGITRA_E_PATTERN.finditer(upper): candidates.add(m.group(1))
    candidates.update(VIN_PATTERN.findall(upper))
    cleaned = re.sub(r'[\s\-_]+', '', upper)
    candidates.update(VIN_PATTERN.findall(cleaned))
    candidates.update(extract_vin_candidates_fuzzy(cleaned))
    logger.info("VIN kandidatai: {}".format(candidates))
    return list(candidates)


def _get_tesseract_lang():
    try:
        langs = pytesseract.get_languages()
        if "lit" in langs and "eng" in langs: return "lit+eng"
        if "eng" in langs: return "eng"
        return langs[0] if langs else "eng"
    except:
        return "eng"


def _preprocess_image(img):
    from PIL import ImageFilter, ImageEnhance
    img = img.convert("RGB");
    r, g, b = img.split()
    img = ImageEnhance.Contrast(r).enhance(3.0)
    img = img.convert("L")
    img = img.point(lambda x: 0 if x < 140 else 255, "1").convert("L")
    return img.filter(ImageFilter.SHARPEN)


def extract_text_with_google(pdf_bytes):
    import base64, json
    from urllib.request import Request, urlopen
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key: raise ValueError("GOOGLE_API_KEY nenurodytas")
    b64 = base64.b64encode(pdf_bytes).decode()
    payload = json.dumps({"requests": [{"inputConfig": {"content": b64, "mimeType": "application/pdf"},
                                        "features": [{"type": "DOCUMENT_TEXT_DETECTION"}], "imageContext": {
            "languageHints": ["lt", "en", "de", "fr", "pl", "lv", "et"]}}]}).encode()
    req = Request("https://vision.googleapis.com/v1/files:annotate?key=" + api_key, data=payload,
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    pages = data["responses"][0].get("responses", [])
    return "\n".join(p["fullTextAnnotation"]["text"] for p in pages if "fullTextAnnotation" in p)


def extract_text_from_pdf(pdf_bytes):
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            parts = [p.extract_text() for p in pdf.pages if p.extract_text()]
            combined = "\n".join(parts)
            if len(combined.strip()) > 50:
                logger.info("pdfplumber");
                return combined, "pdfplumber"
    except Exception as e:
        logger.warning("pdfplumber klaida: {}".format(e))
    if os.environ.get("GOOGLE_API_KEY"):
        try:
            text = extract_text_with_google(pdf_bytes)
            if len(text.strip()) > 50:
                logger.info("Google Vision OCR");
                return text, "google_vision"
        except Exception as e:
            logger.warning("Google OCR klaida: {}".format(e))
    try:
        poppler_path = r"C:\poppler\Library\bin" if platform.system() == "Windows" else None
        images = convert_from_bytes(pdf_bytes, dpi=400, poppler_path=poppler_path)
        lang = _get_tesseract_lang()
        parts = []
        for i, img in enumerate(images):
            t = pytesseract.image_to_string(_preprocess_image(img), lang=lang, config=r"--oem 3 --psm 6")
            logger.info("Psl {} OCR: {!r}".format(i + 1, t[:300]));
            parts.append(t)
        return "\n".join(parts), "tesseract_ocr"
    except Exception as e:
        raise HTTPException(status_code=500, detail="OCR klaida: {}".format(e))


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class VinResult(BaseModel):
    vin: str;
    valid: bool;
    errors: List[str];
    warnings: List[str];
    decoded: Dict[str, Any]


class ExtractionResponse(BaseModel):
    filename: str;
    extraction_method: str;
    total_candidates: int
    valid_vins: int;
    invalid_vins: int
    results: List[VinResult]
    parsed_fields: Dict[str, Any] = {}


class SingleVinResponse(BaseModel):
    vin: str;
    valid: bool;
    errors: List[str];
    warnings: List[str];
    decoded: Dict[str, Any]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
def root():
    return {"service": "VIN Extractor API", "version": "3.0.0", "google_ocr": bool(os.environ.get("GOOGLE_API_KEY"))}


@app.get("/health")
def health():
    status = {"status": "ok", "version": "3.0.0", "google_ocr": bool(os.environ.get("GOOGLE_API_KEY")), "libraries": {}}
    try:
        import pdfplumber as _; status["libraries"]["pdfplumber"] = "ok"
    except:
        status["libraries"]["pdfplumber"] = "NERA"; status["status"] = "degraded"
    try:
        pytesseract.get_tesseract_version(); status["libraries"]["tesseract"] = "ok"
    except:
        status["libraries"]["tesseract"] = "NERA"; status["status"] = "degraded"
    try:
        import pdf2image as _; status["libraries"]["pdf2image"] = "ok"
    except:
        status["libraries"]["pdf2image"] = "NERA"
    return status


@app.post("/extract", response_model=ExtractionResponse)
async def extract_vins(file: UploadFile = File(...), only_valid: bool = Query(False), only_trucks: bool = Query(False)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Reikalingas PDF failas")
    pdf_bytes = await file.read()
    if not pdf_bytes: raise HTTPException(status_code=400, detail="Tuscias failas")
    text, method = extract_text_from_pdf(pdf_bytes)
    parsed_fields = parse_regitra_fields(text)

    # Pirmenybe E laukui is parsed_fields - tiksliausias saltinis
    vin_from_e = parsed_fields.get("E", {}).get("value")
    if vin_from_e:
        candidates = [normalize_vin(vin_from_e)]
        logger.info("VIN is lauko E: {}".format(candidates))
    else:
        candidates = find_vins_in_text(text)
        logger.info("VIN is fuzzy paieskas: {}".format(candidates))

    results = []
    seen = set()
    for vin in candidates:
        vin = normalize_vin(vin)
        if vin in seen: continue
        seen.add(vin)
        r = validate_vin(vin)
        if only_valid and not r["valid"]: continue
        if only_trucks and not r.get("decoded", {}).get("is_truck"): continue
        results.append(VinResult(**r))
    results.sort(key=lambda x: (not x.valid, x.vin))
    valid_count = sum(1 for r in results if r.valid)
    return ExtractionResponse(filename=file.filename, extraction_method=method, total_candidates=len(candidates),
                              valid_vins=valid_count, invalid_vins=len(results) - valid_count, results=results,
                              parsed_fields=parsed_fields)


@app.get("/validate/{vin}", response_model=SingleVinResponse)
def validate_single(vin: str):
    return SingleVinResponse(**validate_vin(vin))


@app.post("/debug/ocr")
async def debug_ocr(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Reikalingas PDF failas")
    pdf_bytes = await file.read()
    text, method = extract_text_from_pdf(pdf_bytes)
    candidates = find_vins_in_text(text)
    return {"method": method, "char_count": len(text), "raw_text": text, "fixed_text": fix_ocr_errors(text),
            "vin_candidates_found": candidates, "parsed_fields": parse_regitra_fields(text)}