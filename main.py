"""
VIN Extractor API v2.0
"""

import re
import io
import logging
import platform
from typing import Optional, List, Dict, Any, Tuple

import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="VIN Extractor API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TRUCK_WMI = {"YV2","YV4","YS2","WMA","WDB","WDC","XLR","XLE","ZCF","VF6","XTC","1XP","2NP","1FU","3AL","1HT","1M1"}
TRANSLITERATION = {'A':1,'B':2,'C':3,'D':4,'E':5,'F':6,'G':7,'H':8,'J':1,'K':2,'L':3,'M':4,'N':5,'P':7,'R':9,'S':2,'T':3,'U':4,'V':5,'W':6,'X':7,'Y':8,'Z':9}
WEIGHTS = [8,7,6,5,4,3,2,10,0,9,8,7,6,5,4,3,2]

def _transliterate(char):
    if char.isdigit(): return int(char)
    return TRANSLITERATION.get(char.upper())

def _decode_year(char):
    table = {'A':1980,'B':1981,'C':1982,'D':1983,'E':1984,'F':1985,'G':1986,'H':1987,'J':1988,'K':1989,'L':1990,'M':1991,'N':1992,'P':1993,'R':1994,'S':1995,'T':1996,'V':1997,'W':1998,'X':1999,'Y':2000,'1':2001,'2':2002,'3':2003,'4':2004,'5':2005,'6':2006,'7':2007,'8':2008,'9':2009}
    return table.get(char)

def _decode_wmi(wmi):
    known = {"YV2":"Volvo Trucks","YV4":"Volvo Trucks","YS2":"Scania","WMA":"MAN","WDB":"Mercedes-Benz Trucks","WDC":"Mercedes-Benz Trucks","XLR":"DAF Trucks","XLE":"DAF Trucks","ZCF":"Iveco","VF6":"Renault Trucks","XTC":"KAMAZ","1XP":"Kenworth","2NP":"Peterbilt","1FU":"Freightliner","3AL":"Freightliner","1HT":"International Trucks","1M1":"Mack Trucks"}
    return known.get(wmi, "Nezinomas gamintojas ({})".format(wmi[:1]))

def validate_vin(vin):
    vin = vin.strip().upper()
    errors, warnings = [], []
    if len(vin) != 17:
        errors.append("Neteisingas ilgis: {} (reikia 17)".format(len(vin)))
        return {"vin":vin,"valid":False,"errors":errors,"warnings":warnings,"decoded":{}}
    forbidden = [c for c in vin if c in "IOQ"]
    if forbidden: errors.append("Draudziami simboliai: {}".format(set(forbidden)))
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin): errors.append("Neleistini simboliai")
    if errors: return {"vin":vin,"valid":False,"errors":errors,"warnings":warnings,"decoded":{}}
    check = vin[8]
    total = sum(_transliterate(c)*WEIGHTS[i] for i,c in enumerate(vin) if _transliterate(c) is not None)
    rem = total % 11
    expected = "X" if rem == 10 else str(rem)
    if check != expected: warnings.append("Checksum neatitikimas: turetu buti '{}', yra '{}'".format(expected, check))
    wmi = vin[:3]
    decoded = {"wmi":wmi,"manufacturer":_decode_wmi(wmi),"vds":vin[3:9],"check_digit":vin[8],"model_year":_decode_year(vin[9]),"plant_code":vin[10],"serial_number":vin[11:],"is_truck":wmi in TRUCK_WMI}
    return {"vin":vin,"valid":True,"errors":[],"warnings":warnings,"decoded":decoded}

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
        seq = match.group()
        n = len(seq)
        if n == 17:
            candidates.add(seq)
        elif 18 <= n <= 20:
            for i in range(n):
                c = seq[:i] + seq[i+1:]
                if len(c) == 17:
                    candidates.add(c)
    return list(candidates)

def find_vins_in_text(text):
    text = fix_ocr_errors(text)
    upper = text.upper()
    candidates = set()
    for match in REGITRA_E_PATTERN.finditer(upper):
        candidates.add(match.group(1))
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
    except: return "eng"

def _preprocess_image(img):
    from PIL import ImageFilter, ImageEnhance
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = img.filter(ImageFilter.SHARPEN)
    return img.convert("L")

def extract_text_from_pdf(pdf_bytes):
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            parts = [page.extract_text() for page in pdf.pages if page.extract_text()]
            combined = "\n".join(parts)
            if len(combined.strip()) > 50:
                logger.info("pdfplumber")
                return combined, "pdfplumber"
    except Exception as e:
        logger.warning("pdfplumber klaida: {}".format(e))
    try:
        poppler_path = r"C:\poppler\Library\bin" if platform.system() == "Windows" else None
        images = convert_from_bytes(pdf_bytes, dpi=400, poppler_path=poppler_path)
        lang = _get_tesseract_lang()
        parts = []
        for i, img in enumerate(images):
            t = pytesseract.image_to_string(_preprocess_image(img), lang=lang, config=r"--oem 3 --psm 6")
            logger.info("Psl {} OCR: {!r}".format(i+1, t[:300]))
            parts.append(t)
        return "\n".join(parts), "tesseract_ocr"
    except Exception as e:
        raise HTTPException(status_code=500, detail="OCR klaida: {}".format(e))

class VinResult(BaseModel):
    vin: str; valid: bool; errors: List[str]; warnings: List[str]; decoded: Dict[str, Any]

class ExtractionResponse(BaseModel):
    filename: str; extraction_method: str; total_candidates: int; valid_vins: int; invalid_vins: int; results: List[VinResult]

class SingleVinResponse(BaseModel):
    vin: str; valid: bool; errors: List[str]; warnings: List[str]; decoded: Dict[str, Any]

@app.get("/")
def root():
    return {"service":"VIN Extractor API","version":"2.0.0"}

@app.get("/health")
def health():
    status = {"status":"ok","version":"2.0.0","libraries":{}}
    try: import pdfplumber as _; status["libraries"]["pdfplumber"] = "ok"
    except: status["libraries"]["pdfplumber"] = "NERA"; status["status"] = "degraded"
    try: pytesseract.get_tesseract_version(); status["libraries"]["tesseract"] = "ok"
    except: status["libraries"]["tesseract"] = "NERA"; status["status"] = "degraded"
    try: import pdf2image as _; status["libraries"]["pdf2image"] = "ok"
    except: status["libraries"]["pdf2image"] = "NERA"
    return status

@app.post("/extract", response_model=ExtractionResponse)
async def extract_vins(file: UploadFile = File(...), only_valid: bool = Query(False), only_trucks: bool = Query(False)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Reikalingas PDF failas")
    pdf_bytes = await file.read()
    if not pdf_bytes: raise HTTPException(status_code=400, detail="Tuscias failas")
    text, method = extract_text_from_pdf(pdf_bytes)
    candidates = find_vins_in_text(text)
    results = []
    for vin in candidates:
        r = validate_vin(vin)
        if only_valid and not r["valid"]: continue
        if only_trucks and not r.get("decoded",{}).get("is_truck"): continue
        results.append(VinResult(**r))
    results.sort(key=lambda x: (not x.valid, x.vin))
    valid_count = sum(1 for r in results if r.valid)
    return ExtractionResponse(filename=file.filename, extraction_method=method, total_candidates=len(candidates), valid_vins=valid_count, invalid_vins=len(results)-valid_count, results=results)

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
    return {"method":method,"char_count":len(text),"raw_text":text,"fixed_text":fix_ocr_errors(text),"vin_candidates_found":candidates}