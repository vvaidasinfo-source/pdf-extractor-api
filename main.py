"""
VIN Extractor API
-----------------
PDF nuskaitymas ir VIN kodų tikrinimas.
Palaikomi: tekstiniai PDF ir skenai (OCR).
"""

import re
import io
import logging
from pathlib import Path
from typing import Optional

import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="VIN Extractor API",
    description="Nuskaito PDF dokumentus ir tikrina VIN kodus",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# VIN logika
# ---------------------------------------------------------------------------

# Oficialūs WMI (World Manufacturer Identifier) prefiksai sunkvežimiams
TRUCK_WMI = {
    # Volvo
    "YV2", "YV4",
    # Scania
    "YS2",
    # MAN
    "WMA",
    # Mercedes-Benz (Daimler Trucks)
    "WDB", "WDC",
    # DAF
    "XLR", "XLE",
    # Iveco
    "ZCF",
    # Renault Trucks
    "VF6",
    # KAMAZ
    "XTC",
    # Kenworth / Peterbilt (PACCAR)
    "1XP", "2NP",
    # Freightliner
    "1FU", "3AL",
    # International (Navistar)
    "1HT",
    # Mack
    "1M1",
}

# Transliteracijos lentelė VIN checksum skaičiavimui
TRANSLITERATION = {
    'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5, 'F': 6, 'G': 7, 'H': 8,
    'J': 1, 'K': 2, 'L': 3, 'M': 4, 'N': 5, 'P': 7, 'R': 9,
    'S': 2, 'T': 3, 'U': 4, 'V': 5, 'W': 6, 'X': 7, 'Y': 8, 'Z': 9,
}

WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def _transliterate(char: str) -> Optional[int]:
    """Konvertuoja VIN simbolį į skaitinę reikšmę."""
    if char.isdigit():
        return int(char)
    return TRANSLITERATION.get(char.upper())


def validate_vin(vin: str) -> dict:
    """
    Pilnas VIN validavimas pagal ISO 3779 standartą.
    Grąžina dict su rezultatu ir detalėmis.
    """
    vin = vin.strip().upper()

    errors = []
    warnings = []

    # 1. Ilgis
    if len(vin) != 17:
        errors.append(f"Neteisingas ilgis: {len(vin)} simboliai (reikia 17)")
        return _result(vin, False, errors, warnings)

    # 2. Draudžiami simboliai (I, O, Q)
    forbidden = [c for c in vin if c in ("I", "O", "Q")]
    if forbidden:
        errors.append(f"Draudžiami simboliai: {', '.join(set(forbidden))}")

    # 3. Tik leistini simboliai
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
        errors.append("VIN turi neleistinų simbolių")

    if errors:
        return _result(vin, False, errors, warnings)

    # 4. Checksum (9-as simbolis) – galioja tik JAV registruotiems
    check_digit = vin[8]
    if check_digit in "0123456789X":
        total = 0
        for i, char in enumerate(vin):
            val = _transliterate(char)
            if val is None:
                errors.append(f"Nežinomas simbolis pozicijoje {i+1}: {char}")
                break
            total += val * WEIGHTS[i]
        else:
            remainder = total % 11
            expected = "X" if remainder == 10 else str(remainder)
            if check_digit != expected:
                # Europiniai VIN dažnai neatitinka JAV checksum – warning, ne error
                warnings.append(
                    f"Checksum neatitikimas (9 poz.): turėtų būti '{expected}', yra '{check_digit}'. "
                    "Normalus europiniams vilkikams."
                )

    # 5. Modelių metai (10-as simbolis)
    year_char = vin[9]
    year = _decode_year(year_char)
    if year is None:
        warnings.append(f"Nežinomas modelių metų kodas: '{year_char}'")

    # 6. WMI (gamintojas)
    wmi = vin[:3]
    manufacturer = _decode_wmi(wmi)
    is_truck = wmi in TRUCK_WMI

    # 7. Surinkimo gamykla (11-as simbolis)
    plant_char = vin[10]

    decoded = {
        "wmi": wmi,
        "manufacturer": manufacturer,
        "vds": vin[3:9],
        "check_digit": vin[8],
        "model_year": year,
        "plant_code": plant_char,
        "serial_number": vin[11:],
        "is_truck": is_truck,
    }

    is_valid = len(errors) == 0
    return _result(vin, is_valid, errors, warnings, decoded)


def _result(vin, valid, errors, warnings, decoded=None):
    return {
        "vin": vin,
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "decoded": decoded or {},
    }


def _decode_year(char: str) -> Optional[int]:
    """Dekodavimas pagal SAE J831 standartą."""
    table = {
        'A': 1980, 'B': 1981, 'C': 1982, 'D': 1983, 'E': 1984,
        'F': 1985, 'G': 1986, 'H': 1987, 'J': 1988, 'K': 1989,
        'L': 1990, 'M': 1991, 'N': 1992, 'P': 1993, 'R': 1994,
        'S': 1995, 'T': 1996, 'V': 1997, 'W': 1998, 'X': 1999,
        'Y': 2000, '1': 2001, '2': 2002, '3': 2003, '4': 2004,
        '5': 2005, '6': 2006, '7': 2007, '8': 2008, '9': 2009,
        'A2': 2010, 'B2': 2011, 'C2': 2012, 'D2': 2013, 'E2': 2014,
        'F2': 2015, 'G2': 2016, 'H2': 2017, 'J2': 2018, 'K2': 2019,
        'L2': 2020, 'M2': 2021, 'N2': 2022, 'P2': 2023, 'R2': 2024,
        'S2': 2025, 'T2': 2026,
    }
    # Pirma pabandyti su ciklo 2 žyma
    return table.get(char) or table.get(char)


def _decode_wmi(wmi: str) -> str:
    """Grąžina gamintoją pagal WMI."""
    known = {
        "YV2": "Volvo Trucks (Švedija)",
        "YV4": "Volvo Trucks (Švedija)",
        "YS2": "Scania (Švedija)",
        "WMA": "MAN (Vokietija)",
        "WDB": "Mercedes-Benz Trucks (Vokietija)",
        "WDC": "Mercedes-Benz Trucks (Vokietija)",
        "XLR": "DAF Trucks (Nyderlandai)",
        "XLE": "DAF Trucks (Nyderlandai)",
        "ZCF": "Iveco (Italija)",
        "VF6": "Renault Trucks (Prancūzija)",
        "XTC": "KAMAZ (Rusija)",
        "1XP": "Kenworth (JAV)",
        "2NP": "Peterbilt (Kanada)",
        "1FU": "Freightliner (JAV)",
        "3AL": "Freightliner (JAV)",
        "1HT": "International Trucks (JAV)",
        "1M1": "Mack Trucks (JAV)",
    }
    if wmi in known:
        return known[wmi]
    # Šalies identifikavimas pagal pirmą simbolį
    country_map = {
        'Y': "Skandinavija/Estija", 'W': "Vokietija", 'X': "Rusija/Nyderlandai",
        'Z': "Italija", 'V': "Prancūzija/Ispanija", '1': "JAV", '2': "Kanada",
        '3': "Meksika", 'J': "Japonija", 'K': "Korėja",
    }
    country = country_map.get(wmi[0], "Nežinoma šalis")
    return f"Nežinomas gamintojas ({country})"


# ---------------------------------------------------------------------------
# PDF nuskaitymas
# ---------------------------------------------------------------------------

VIN_PATTERN = re.compile(r'\b[A-HJ-NPR-Z0-9]{17}\b')


def extract_text_from_pdf(pdf_bytes: bytes) -> tuple[str, str]:
    """
    Bando ištraukti tekstą iš PDF.
    Grąžina (tekstas, metodas).
    """
    # 1. Bandymas – tekstinis PDF (greitas)
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text_parts = []
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
            combined = "\n".join(text_parts)
            if len(combined.strip()) > 50:
                logger.info("PDF tekstinis – naudojamas pdfplumber")
                return combined, "pdfplumber"
    except Exception as e:
        logger.warning(f"pdfplumber klaida: {e}")

    # 2. Bandymas – OCR (lėtas, skenams)
    logger.info("PDF skenuotas – naudojamas Tesseract OCR")
    try:
        images = convert_from_bytes(pdf_bytes, dpi=300)
        text_parts = []
        for img in images:
            # LT+EN kalbos, pagemode – automatinis
            t = pytesseract.image_to_string(img, lang="lit+eng")
            text_parts.append(t)
        return "\n".join(text_parts), "tesseract_ocr"
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OCR klaida: {e}")


def find_vins_in_text(text: str) -> list[str]:
    """Randa visus 17-simbolių kandidatus."""
    # Normalizuojame tarpus ir brūkšnelius
    cleaned = re.sub(r"[\s\-_]+", "", text)
    # Bet kadangi VIN gali būti su tarpais (pvz., "YS2 R4X2 0A XXXXXX")
    # ieškome ir originalame tekste
    candidates = set()
    candidates.update(VIN_PATTERN.findall(text.upper()))
    candidates.update(VIN_PATTERN.findall(cleaned.upper()))
    return list(candidates)


# ---------------------------------------------------------------------------
# Pydantic modeliai
# ---------------------------------------------------------------------------

class VinResult(BaseModel):
    vin: str
    valid: bool
    errors: list[str]
    warnings: list[str]
    decoded: dict


class ExtractionResponse(BaseModel):
    filename: str
    extraction_method: str
    total_candidates: int
    valid_vins: int
    invalid_vins: int
    results: list[VinResult]


class SingleVinResponse(BaseModel):
    vin: str
    valid: bool
    errors: list[str]
    warnings: list[str]
    decoded: dict


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", summary="API info")
def root():
    return {
        "service": "VIN Extractor API",
        "version": "1.0.0",
        "endpoints": {
            "POST /extract": "Nuskaityti PDF ir rasti VIN kodus",
            "GET /validate/{vin}": "Patikrinti vieną VIN kodą",
            "GET /health": "Sistemos būsena",
        },
    }


@app.get("/health", summary="Sistemos būsena")
def health():
    """Tikrina ar OCR ir PDF bibliotekos veikia."""
    status = {"status": "ok", "libraries": {}}

    try:
        import pdfplumber  # noqa
        status["libraries"]["pdfplumber"] = "✓"
    except ImportError:
        status["libraries"]["pdfplumber"] = "✗ NĖRA"
        status["status"] = "degraded"

    try:
        pytesseract.get_tesseract_version()
        status["libraries"]["tesseract"] = "✓"
    except Exception:
        status["libraries"]["tesseract"] = "✗ NĖRA (OCR neveiks)"
        status["status"] = "degraded"

    try:
        import pdf2image  # noqa
        status["libraries"]["pdf2image"] = "✓"
    except ImportError:
        status["libraries"]["pdf2image"] = "✗ NĖRA"

    return status


@app.post("/extract", response_model=ExtractionResponse, summary="Nuskaityti PDF")
async def extract_vins(
    file: UploadFile = File(..., description="PDF dokumentas"),
    only_valid: bool = Query(False, description="Grąžinti tik validžius VIN"),
    only_trucks: bool = Query(False, description="Filtruoti tik sunkvežimių VIN"),
):
    """
    Nuskaito PDF dokumentą ir grąžina visus rastus VIN kodus su validacijos rezultatais.

    - **file**: PDF failas (tekstinis arba skenuo­tas)
    - **only_valid**: jei `true` – grąžinami tik teisingi VIN
    - **only_trucks**: jei `true` – grąžinami tik sunkvežimių VIN
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Reikalingas PDF failas")

    pdf_bytes = await file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=400, detail="Tuščias failas")

    logger.info(f"Apdorojamas: {file.filename} ({len(pdf_bytes):,} baitų)")

    # Nuskaitymas
    text, method = extract_text_from_pdf(pdf_bytes)

    # VIN paieška
    candidates = find_vins_in_text(text)
    logger.info(f"Rasta kandidatų: {len(candidates)}")

    # Validacija
    results = []
    for vin in candidates:
        r = validate_vin(vin)

        if only_valid and not r["valid"]:
            continue
        if only_trucks and r.get("decoded", {}).get("is_truck") is False:
            continue

        results.append(VinResult(**r))

    # Rūšiavimas: pirma validūs
    results.sort(key=lambda x: (not x.valid, x.vin))

    valid_count = sum(1 for r in results if r.valid)

    return ExtractionResponse(
        filename=file.filename,
        extraction_method=method,
        total_candidates=len(candidates),
        valid_vins=valid_count,
        invalid_vins=len(results) - valid_count,
        results=results,
    )


@app.get("/validate/{vin}", response_model=SingleVinResponse, summary="Patikrinti VIN")
def validate_single(vin: str):
    """
    Patikrina vieną VIN kodą be PDF įkėlimo.
    Naudinga greitam testavimui.
    """
    result = validate_vin(vin)
    return SingleVinResponse(**result)
