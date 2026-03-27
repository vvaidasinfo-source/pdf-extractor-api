"""VIN Extractor API v3.0 - su pilnu laukų atpažinimu"""
import re, io, logging, platform, os
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
import pdfplumber, pytesseract
from pdf2image import convert_from_bytes
from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass
if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
app = FastAPI(title="VIN Extractor API", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TRUCK_WMI = {"YV2","YV4","YS2","WMA","WDB","WDC","XLR","XLE","ZCF","VF6","XTC","1XP","2NP","1FU","3AL","1HT","1M1","WSM","WS9","WS1","XMC","X3F","WJM","YE2","SFP","SF9","3H3","1DW"}
TRANSLITERATION = {'A':1,'B':2,'C':3,'D':4,'E':5,'F':6,'G':7,'H':8,'J':1,'K':2,'L':3,'M':4,'N':5,'P':7,'R':9,'S':2,'T':3,'U':4,'V':5,'W':6,'X':7,'Y':8,'Z':9}
WEIGHTS = [8,7,6,5,4,3,2,10,0,9,8,7,6,5,4,3,2]

def _transliterate(c):
    return int(c) if c.isdigit() else TRANSLITERATION.get(c.upper())

def _decode_year(c):
    t = {'A':1980,'B':1981,'C':1982,'D':1983,'E':1984,'F':1985,'G':1986,'H':1987,'J':1988,'K':1989,'L':1990,'M':1991,'N':1992,'P':1993,'R':1994,'S':1995,'T':1996,'V':1997,'W':1998,'X':1999,'Y':2000,'1':2001,'2':2002,'3':2003,'4':2004,'5':2005,'6':2006,'7':2007,'8':2008,'9':2009}
    return t.get(c)

def _decode_wmi(wmi):
    k = {"YV2":"Volvo Trucks","YV4":"Volvo Trucks","YS2":"Scania","WMA":"MAN","WDB":"Mercedes-Benz Trucks","WDC":"Mercedes-Benz Trucks","XLR":"DAF Trucks","XLE":"DAF Trucks","ZCF":"Iveco","VF6":"Renault Trucks","XTC":"KAMAZ","1XP":"Kenworth","2NP":"Peterbilt","1FU":"Freightliner","3AL":"Freightliner","1HT":"International Trucks","1M1":"Mack Trucks","WSM":"Schmitz Cargobull","WS9":"Schmitz Cargobull","XMC":"Krone","WJM":"Kogel","YE2":"Wielton","SFP":"Schwarzmuller"}
    return k.get(wmi, "Nezinomas ({})".format(wmi))

def normalize_vin(vin):
    r = list(vin)
    for i in list(range(0,3)) + list(range(11,17)):
        if r[i] == 'O': r[i] = '0'
    return ''.join(r)

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

# ---------------------------------------------------------------------------
# Universalus laukų atpažinimas (LT + DE)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Universalus dokumento laukų atpažinimas (LT + DE)
# ---------------------------------------------------------------------------
FIELD_LABELS = {
    "A":"Registracijos numeris","B":"Reg. data","B1":"Reg. LT data","B2":"Modelio metai",
    "D1":"Markė","D2":"Tipas/variantas","D3":"Modelis","E":"VIN",
    "F1":"Max masė (kg)","F2":"Leistina masė (kg)","F3":"Junginio masė (kg)",
    "F4":"Puspriekab. masė (kg)","F5":"Puspriekab. ašių masė (kg)",
    "G":"Tuščia masė (kg)","H":"Galiojimo pabaiga","I":"Dokumento data",
    "J":"Kategorija","J1":"Kėbulo kodas (nac)","J2":"Kėbulo kodas (ES)",
    "K":"Tipo patv. nr","K1":"Nac. patv. nr",
    "P1":"Variklio tūris (cm3)","P2":"Galia (kW)","P3":"Degalai","P4":"Sūkiai","P5":"Variklio kodas",
    "Q":"Galios/masės santykis","R":"Spalva","S1":"Sėdimų vietų sk","S2":"Stovimų vietų sk",
    "T":"Max greitis (km/h)","V7":"CO2 (g/km)","V9":"Teršalų lygis","V10":"Hibridinė",
    "C11":"Valdytojas","C12":"Valdytojo vardas","C13":"Valdytojo adresas","C14":"Valdytojo kodas",
    "C21":"Savininkas","C22":"Savininko vardas","C23":"Savininko adresas","C24":"Savininko kodas",
}

# Vokiški etiketių žodžiai - naudojami aptikimui ir praleidimui
_DE_SKIP = (
    r"(?:Amtliches\s+Kennzeichen|Datum\s+der\s+Erstzulassung|Marke|Typ|Variante|Version|"
    r"Handelsbezeichnung|Fahrzeug.Identifizierungsnummer|Fahrzeugklasse|Farbe|"
    r"Hubraum[^{]{0,30}|Nennleistung[^{]{0,30}|Kraftstoff|Hersteller[^\n]{0,30}|"
    r"Name\s+oder\s+Firmenname|Vorname|Anschrift|Bezeichnung[^\n]{0,30})\s*"
)

def detect_document_country(text: str) -> str:
    """Nustato dokumento šalį: 'de' arba 'lt'."""
    u = text.upper()
    de_hits = sum(1 for kw in [
        "ZULASSUNGSBESCHEINIGUNG", "BUNDESREPUBLIK", "FAHRZEUG",
        "KRAFTSTOFF", "KENNZEICHEN", "HUBRAUM", "NENNLEISTUNG",
    ] if kw in u)
    lt_hits = sum(1 for kw in [
        "REGISTRACIJOS LIUDIJIMAS", "REGITRA", "LIETUVOS RESPUBLIKA",
        "EUROPOS SAJUNGA", "EUROPOS SĄJUNGA", "DYZELINAS", "DEGALAI",
    ] if kw in u)
    return "de" if de_hits > lt_hits else "lt"


def _norm_date(val: str) -> str:
    """DD.MM.YYYY → YYYY-MM-DD, kitus palieka."""
    m = re.match(r'^(\d{2})\.(\d{2})\.(\d{4})$', val.strip())
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return val


def _make_extractor(fields, data_text):
    """Grąžina extract() funkciją su bendra logika."""
    def extract(pattern, key, transform=None):
        if key in fields:
            return
        m = re.search(pattern, data_text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if not m:
            return
        val = m.group(1).strip().strip('-').strip()
        if not val or re.fullmatch(r'[-–—/\s]+', val):
            return
        if len(val) > 80 and ' ' in val and not any(c.isdigit() for c in val[:10]):
            return
        if transform:
            val = transform(val)
        fields[key] = {"label": FIELD_LABELS.get(key, key), "value": val}
    return extract


def _parse_lt_fields(data_text: str) -> Dict[str, Any]:
    """
    Lietuviški Regitros dokumentai.
    Veikia su nauju skaitmeniniu formatu, senu nuskaitytu ir nufotografuotu.
    Toleruoja OCR klaidas: taškas→kablelis, dvigubi tarpai, sulieti simboliai.
    """
    fields: Dict[str, Any] = {}
    x = _make_extractor(fields, data_text)

    # ── A: Registracijos numeris ─────────────────────────────────────────────
    # Naujas formatas: "A   NRE513"  Senas: "A   HF340"  Foto: OCR gali duoti "A  NSE437"
    x(r'(?:^|\s)A\s{1,12}([A-Z]{1,4}\d{3,6})\b', 'A')

    # ── B: datos ─────────────────────────────────────────────────────────────
    # Naujame formate eilutė: "A  NRE513   I  2026-01-15   H  --"
    # Todėl datos ieškome pagal etiketę, bet ne eilutės pradžioje
    x(r'(?:^|\s)B\s{1,10}(\d{4}-\d{2}-\d{2})', 'B')
    x(r'(?:^|\s)B[,.]?\.?1\s{1,10}(\d{4}-\d{2}-\d{2})', 'B1')
    x(r'(?:^|\s)B[,.]?\.?2\s{1,10}([\d\-/]+)', 'B2')

    # ── I: dokumento data (dažnai eilutėje su B.1) ───────────────────────────
    x(r'(?:^|\s)I\s{1,10}(\d{4}-\d{2}-\d{2})', 'I')
    x(r'(?:^|\s)H\s{1,10}(\d{4}-\d{2}-\d{2})', 'H')

    # ── D.1: Markė ────────────────────────────────────────────────────────────
    # Naujas: "D.1  MERCEDES-BENZ\n"  Senas/OCR: "D,1 VOLVO" arba "D 1  VOLVO"
    x(r'D[,. ]?\.?1\s{1,12}([A-ZÄÖÜ][A-ZÄÖÜ0-9/\s\-]{1,35}?)(?:\n|\s{3,}|D[,. ]?\.?2)', 'D1')

    # ── D.2, D.3 ─────────────────────────────────────────────────────────────
    x(r'D[,. ]?\.?2\s{1,10}([^\n]{3,80})', 'D2')
    x(r'D[,. ]?\.?3\s{1,10}([^\n]{2,50})', 'D3')

    # ── E: VIN ───────────────────────────────────────────────────────────────
    x(r'(?:^|\s)E\s{0,15}([A-HJ-NPR-Z0-9]{17})\b', 'E')

    # ── F masės ──────────────────────────────────────────────────────────────
    # Naujame formate "F.1  18000   F.2  --   F.3  --" viena eilutė
    x(r'F[,.]?\.?1\s{1,10}(\d{3,6})', 'F1')
    x(r'F[,.]?\.?2\s{1,10}(\d{3,6})', 'F2')
    x(r'F[,.]?\.?3\s{1,10}(\d{3,6})', 'F3')
    x(r'[(\[]?F[,.]?\.?4[)\]]?\s{1,10}(\d{3,6})', 'F4')
    x(r'[(\[]?F[,.]?\.?5[)\]]?\s{1,10}(\d{3,6})', 'F5')

    # ── G: tuščia masė ───────────────────────────────────────────────────────
    x(r'(?:^|\s)G\s{1,10}(\d{3,6})', 'G')

    # ── J: kategorija ─────────────────────────────────────────────────────────
    x(r'(?:^|\s)J\s{1,6}([A-Z]\d)\b', 'J')
    x(r'J[,.]?\.?1\s{1,10}([^\n]{1,25})', 'J1')
    x(r'J[,.]?\.?2\s{1,10}([A-Z]{1,6})\b', 'J2')

    # ── K: tipo patvirtinimas ─────────────────────────────────────────────────
    x(r'(?:^|\s)K\s{1,6}([^\n]{5,60})', 'K')
    x(r'K[,.]?\.?1\s{1,6}([^\n]{3,60})', 'K1')

    # ── P: variklis ───────────────────────────────────────────────────────────
    x(r'P[,.]?\.?1\s{1,10}(\d{3,6})', 'P1')
    x(r'P[,.]?\.?2\s{1,10}(\d{2,5})', 'P2')
    x(r'P[,.]?\.?3\s{1,10}([A-Za-zžščųėįūąŽŠČŲĖĮŪĄ]{3,20})', 'P3')
    x(r'P[,.]?\.?4\s{1,10}(\d{3,6})', 'P4')
    x(r'P[,.]?\.?5\s{1,10}([^\n]{2,35})', 'P5')

    # ── R: spalva ─────────────────────────────────────────────────────────────
    x(r'(?:^|\s)R\s{1,10}([A-ZÄÖÜ][A-ZÄÖÜA-Z]{2,15})\b', 'R')

    # ── S, T, Q ───────────────────────────────────────────────────────────────
    x(r'S[,.]?\.?1\s{1,10}(\d{1,3})', 'S1')
    x(r'S[,.]?\.?2\s{1,10}(\d{1,3})', 'S2')
    x(r'(?:^|\s)T\s{1,10}(\d{2,3})\b', 'T')
    x(r'(?:^|\s)Q\s{1,10}([\d.,]+)\b', 'Q')

    # ── V: emisija ────────────────────────────────────────────────────────────
    x(r'V[,.]?\.?7\s{1,10}([^\n]{3,40})', 'V7')
    x(r'V[,.]?\.?9\s{1,10}([^\n]{5,80})', 'V9')
    x(r'V[,.]?\.?10\s{0,6}\)?([^\n]{1,15})', 'V10')

    # ── C: valdytojas / savininkas ────────────────────────────────────────────
    # Senas formatas: "(C.1.4) 141776948"  Naujas: žiūrima be skliaustų
    x(r'C[,.]?\.?1[,.]?\.?1\s{1,8}([^\n]{3,80})', 'C11')
    x(r'C[,.]?\.?1[,.]?\.?2\s{1,8}([^\n]{2,50})', 'C12')
    x(r'C[,.]?\.?1[,.]?\.?3\s{1,8}([^\n]{3,80})', 'C13')
    x(r'[(\[]C[,.]?\.?1[,.]?\.?4[)\]]\s{0,8}(\d{7,12})', 'C14')
    x(r'C[,.]?\.?2[,.]?\.?1\s{1,8}([^\n]{3,80})', 'C21')
    x(r'C[,.]?\.?2[,.]?\.?2\s{1,8}([^\n]{2,50})', 'C22')
    x(r'C[,.]?\.?2[,.]?\.?3\s{1,8}([^\n]{3,80})', 'C23')
    x(r'[(\[]C[,.]?\.?2[,.]?\.?4[)\]]\s{0,8}(\d{7,12})', 'C24')

    return fields


def _parse_de_fields(data_text: str) -> Dict[str, Any]:
    """
    Vokiški Zulassungsbescheinigung Teil I ir Teil II.
    Nenaudoja IGNORECASE ten kur lauko kodas gali sutapti su žodžio vidumi.
    """
    fields: Dict[str, Any] = {}
    FLAGS_S  = re.MULTILINE
    FLAGS_IC = re.MULTILINE | re.IGNORECASE

    def _set(key, val):
        if key in fields or not val:
            return
        val = val.strip().strip("-–").strip()
        if not val or re.fullmatch(r'[-–—/\s]+', val):
            return
        if len(val) > 80 and " " in val and not any(c.isdigit() for c in val[:10]):
            return
        fields[key] = {"label": FIELD_LABELS.get(key, key), "value": val}

    def xs(pattern, key, transform=None, flags=FLAGS_S):
        if key in fields:
            return
        m = re.search(pattern, data_text, flags)
        if m:
            val = m.group(1).strip()
            if transform:
                val = transform(val)
            _set(key, val)

    def norm_date(v):
        m = re.match(r'(\d{2})\.(\d{2})\.(\d{4})', v.strip())
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else v

    # A: Kennzeichen
    xs(r'Amtliches\s+Kennzeichen\s+([A-ZÄÖÜ]{1,3}\s+[A-Z0-9]{1,8})\b', 'A')
    if 'A' not in fields:
        xs(r'(?:^|\n)A\s+Amtliches[^\n]{0,30}\s([A-ZÄÖÜ]{1,3}\s+[A-Z0-9]{1,8})\b', 'A')
    if 'A' not in fields:
        xs(r'(?:^|\n)([A-ZÄÖÜ]{1,3}\s+[A-Z]{1,2}\d{2,5})\s*(?:\n|\s{5,})', 'A')

    # B: Erstzulassung
    xs(r'Erstzulassung\s+(\d{2}\.\d{2}\.\d{4})', 'B', norm_date)
    if 'B' not in fields:
        xs(r'(?:^|\n)B\s+[A-Z][^\n]{0,40}\s(\d{2}\.\d{2}\.\d{4})', 'B', norm_date)

    # D.1 Marke
    xs(r'D\.?1\s+Marke\s+([A-ZÄÖÜ][A-ZÄÖÜ0-9/\s\-]{1,40}?)(?:\n|D\.?2|\s{4,})', 'D1')
    if 'D1' not in fields:
        xs(r'D\.?1\s+([A-ZÄÖÜ][A-ZÄÖÜ0-9/\s\-]{1,40}?)(?:\n|D\.?2|\s{4,})', 'D1')

    # D.2, D.3
    xs(r'D\.?2\s+(?:Typ\s+)?([^\n]{2,60})', 'D2', flags=FLAGS_IC)
    xs(r'D\.?3\s+(?:Handelsbezeichnung\s+)?([^\n]{1,50})', 'D3', flags=FLAGS_IC)

    # E: VIN
    xs(r'Fahrzeug.Identifizierungsnummer\s+([A-HJ-NPR-Z0-9]{17})\b', 'E', flags=FLAGS_IC)
    if 'E' not in fields:
        xs(r'(?:^|\n)E\s+[A-Z][^\n]{0,50}\s([A-HJ-NPR-Z0-9]{17})\b', 'E')
    if 'E' not in fields:
        xs(r'\bE\s{1,15}([A-HJ-NPR-Z0-9]{17})\b', 'E')

    # J: Fahrzeugklasse
    xs(r'J\s+Fahrzeugklasse\s+([A-Z]\d)\b', 'J')
    if 'J' not in fields:
        xs(r'(?:^|\n)J\s+([A-Z]\d)\b', 'J')
    if 'J' not in fields:
        xs(r'\(4\)\s+([A-Z]\d)\b', 'J')

    # F masės
    xs(r'F\.?1\s+[A-Za-z][^\n]{0,40}\s(\d{4,6})\b', 'F1', flags=FLAGS_IC)
    if 'F1' not in fields:
        xs(r'(?:^|\n)F\.?1\s+(\d{4,6})\b', 'F1')
    xs(r'F\.?2\s+[A-Za-z][^\n]{0,40}\s(\d{4,6})\b', 'F2', flags=FLAGS_IC)
    if 'F2' not in fields:
        xs(r'(?:^|\n)F\.?2\s+(\d{4,6})\b', 'F2')

    # G: Leermasse
    xs(r'(?:^|\n)G\s+(\d{3,6})\b', 'G')

    # K: Typ-Genehmigungsnummer
    xs(r'(?:^|\n)K\s+[A-Za-z][^\n]{0,50}\s(e\d\*[^\s]{5,40})', 'K', flags=FLAGS_IC)

    # P: variklis
    xs(r'P\.?1\s+Hubraum[^\n]{0,25}\s(\d{4,6})\b', 'P1', flags=FLAGS_IC)
    if 'P1' not in fields:
        xs(r'(?:^|\n)P\.?1\s+(\d{4,6})\b', 'P1')
    xs(r'P\.?2\s+Nennleistung[^\n]{0,35}\s([\d/]+)\b', 'P2', flags=FLAGS_IC)
    if 'P2' not in fields:
        xs(r'(?:^|\n)P\.?2\s+([\d/]+)\b', 'P2')
    xs(r'P\.?3\s+Kraftstoff\s+([A-Za-z]{3,15})\b', 'P3', flags=FLAGS_IC)
    if 'P3' not in fields:
        xs(r'(?:^|\n)P\.?3\s+([A-Za-z]{3,15})\b', 'P3')
    if 'P3' not in fields:
        xs(r'\b(Diesel|Benzin|Elektro|Hybrid)\b', 'P3', flags=FLAGS_IC)

    # R: Farbe (tik eilutės pradžioje!)
    xs(r'(?:^|\n)R\s+Farbe\s+([A-ZÄÖÜ][A-ZÄÖÜ]{2,15})\b', 'R')
    if 'R' not in fields:
        xs(r'(?:^|\n)R\s+([A-ZÄÖÜ][A-ZÄÖÜ]{2,15})\b', 'R')

    # S, T
    xs(r'(?:^|\n)S\.?1\s+[A-Za-z][^\n]{0,30}\s(\d{1,3})\b', 'S1', flags=FLAGS_IC)
    if 'S1' not in fields:
        xs(r'(?:^|\n)S\.?1\s+(\d{1,3})\b', 'S1')
    xs(r'(?:^|\n)T\s+[A-Za-z][^\n]{0,30}\s(\d{2,3})\b', 'T', flags=FLAGS_IC)
    if 'T' not in fields:
        xs(r'(?:^|\n)T\s+(\d{2,3})\b', 'T')

    # C: valdytojas / savininkas
    # Teil II: C.3.1 = pavadinimas (C.1.1 atitikmuo), C.6.1 = savininkas (C.2.1 atitikmuo)
    # Etiketė atskirta dviem tarpais nuo reikšmės; reikšmė gali turėti kabutes
    _clean = lambda v: v.replace('"', '').strip()
    xs(r'C\.?3\.?1\s+\S[^\n]{3,50}?\s{2,}(.{3,100}?)(?:\n|$)', 'C11', _clean, FLAGS_IC)
    xs(r'C\.?6\.?1\s+\S[^\n]{3,50}?\s{2,}(.{3,100}?)(?:\n|$)', 'C21', _clean, FLAGS_IC)
    xs(r'C\.?3\.?3\s+\S[^\n]{0,40}?\s{2,}([^\n]{3,100})', 'C13', _clean, FLAGS_IC)
    if 'C13' not in fields:
        xs(r'C\.?3\.?3\s+(?:Anschrift\s+)?([^\n]{3,100})', 'C13', _clean, FLAGS_IC)
    xs(r'C\.?6\.?3\s+(?:Anschrift\s+)?([^\n]{3,100})', 'C23', _clean, FLAGS_IC)

    return fields


def parse_regitra_fields(text: str) -> Dict[str, Any]:
    """
    Universalus įėjimo taškas.
    Automatiškai nustato dokumento šalį (LT / DE) ir
    naudoja atitinkamą parserį.
    """
    country = detect_document_country(text)
    logger.info("Dokumento šalis: {}".format(country))

    # Apkarpyti 'Pastabos' / 'Definition der Felder' / 'Zur Beachtung' skyrių
    # (antrasis puslapis su laukų aprašymais - ne duomenys)
    stop_markers_lt = ["Pastabos", "PASTABOS", "valstybinis registracijos numeris",
                       "pirmosios registracijos data"]
    stop_markers_de = ["Definition der Felder", "Zur Beachtung", "Hinweis zu Feld"]

    stop_markers = stop_markers_de if country == "de" else stop_markers_lt
    data_text = text
    for marker in stop_markers:
        idx = text.find(marker)
        if idx > 300:
            data_text = text[:idx]
            break

    if country == "de":
        fields = _parse_de_fields(data_text)
    else:
        fields = _parse_lt_fields(data_text)

    logger.info("Atpažinti laukai: {}".format(list(fields.keys())))
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
        seq = match.group(); n = len(seq)
        if n == 17: candidates.add(seq)
        elif 18 <= n <= 20:
            for i in range(n):
                c = seq[:i] + seq[i+1:]
                if len(c) == 17: candidates.add(c)
    return list(candidates)

KNOWN_PREFIXES_2 = {
    "WD","WM","WA","WB","WF","WV","WS","WJ",
    "YV","YS","YE","YW",
    "XL","XM","XS","XC","XE","XK",
    "VF","VN","VS","VX",
    "ZC","ZA","ZF","ZD",
    "TM","TR","TY",
    "SB","SA","SC","SF",
    "JN","JT","JA","JF","JH","JK",
    "KM","KL","KN","KP",
    "LB","LA","LF","LS","LV",
    "1F","1G","1H","1J","1L","1M",
    "2T","2G","2C","2F","2H",
}

def is_likely_real_vin(vin):
    digit_count = sum(1 for c in vin if c.isdigit())
    if digit_count < 4:
        return False
    # Zinomas WMI
    if vin[:3] in TRUCK_WMI:
        return True
    # Zinomas salies prefiksas
    if vin[:2] in KNOWN_PREFIXES_2:
        return True
    # Checksum + bent 4 raides
    alpha_count = sum(1 for c in vin if c.isalpha())
    if alpha_count >= 4:
        total = sum(_transliterate(c)*WEIGHTS[i] for i,c in enumerate(vin) if _transliterate(c) is not None)
        rem = total % 11
        expected = "X" if rem == 10 else str(rem)
        if vin[8] == expected and vin[:2] not in {"EV","EX","EW","EU","ET","ES","ER","EP","EN","EM","EL","EK","EJ","EH","EG","EF","ED","EC","EB","EA"}:
            return True
    return False

def find_vins_in_text(text):
    """Randa VIN kodus - tik tikrus, ne teksto fragmentus."""
    text = fix_ocr_errors(text)
    upper = text.upper()
    candidates = set()

    # 1. E laukas - tiksliausias
    for m in REGITRA_E_PATTERN.finditer(upper):
        candidates.add(m.group(1))

    # 2. Tiksli paieska
    candidates.update(VIN_PATTERN.findall(upper))
    cleaned = re.sub(r"[\s\-_]+", "", upper)
    candidates.update(VIN_PATTERN.findall(cleaned))

    # 3. Filtruoti - palikti tik tikrus VIN
    real_vins = {v for v in candidates if is_likely_real_vin(v)}
    logger.info("VIN kandidatai: {} -> tikri: {}".format(len(candidates), len(real_vins)))
    return list(real_vins)

def _get_tesseract_lang():
    try:
        langs = pytesseract.get_languages()
        selected = [l for l in ("lit", "deu", "fra", "pol", "lav", "est", "eng") if l in langs]
        if selected:
            return "+".join(selected)
        return langs[0] if langs else "eng"
    except: return "eng"

def _preprocess_image(img):
    from PIL import ImageFilter, ImageEnhance
    img = img.convert("RGB"); r, g, b = img.split()
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
    payload = json.dumps({"requests": [{"inputConfig": {"content": b64, "mimeType": "application/pdf"}, "features": [{"type": "DOCUMENT_TEXT_DETECTION"}], "imageContext": {"languageHints": ["lt","en","de","fr","pl","lv","et"]}}]}).encode()
    req = Request("https://vision.googleapis.com/v1/files:annotate?key=" + api_key, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    pages = data["responses"][0].get("responses", [])
    return "\n".join(p["fullTextAnnotation"]["text"] for p in pages if "fullTextAnnotation" in p)

AVAILABLE_ENGINES = ["auto", "pdfplumber", "google_vision", "tesseract"]

class EngineEnum(str, Enum):
    auto         = "auto"
    pdfplumber   = "pdfplumber"
    google_vision = "google_vision"
    tesseract    = "tesseract"

def _run_pdfplumber(pdf_bytes):
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        parts = [p.extract_text() for p in pdf.pages if p.extract_text()]
        combined = "\n".join(parts)
        if len(combined.strip()) > 50:
            return combined, "pdfplumber"
    raise ValueError("pdfplumber nerado teksto")

def _run_google_vision(pdf_bytes):
    if not os.environ.get("GOOGLE_API_KEY"):
        raise ValueError("GOOGLE_API_KEY nenurodytas aplinkoje")
    text = extract_text_with_google(pdf_bytes)
    if len(text.strip()) > 50:
        return text, "google_vision"
    raise ValueError("Google Vision negrąžino teksto")

def _run_tesseract(pdf_bytes):
    poppler_path = r"C:\poppler\Library\bin" if platform.system() == "Windows" else None
    images = convert_from_bytes(pdf_bytes, dpi=400, poppler_path=poppler_path)
    lang = _get_tesseract_lang()
    parts = []
    for i, img in enumerate(images):
        t = pytesseract.image_to_string(_preprocess_image(img), lang=lang, config=r"--oem 3 --psm 6")
        logger.info("Psl {} OCR: {!r}".format(i+1, t[:300]))
        parts.append(t)
    result = "\n".join(parts)
    if not result.strip():
        raise ValueError("Tesseract negrąžino teksto")
    return result, "tesseract_ocr"

def extract_text_from_pdf(pdf_bytes, engine: str = "auto"):
    """
    Ištraukia tekstą iš PDF.
    engine: "auto" | "pdfplumber" | "google_vision" | "tesseract"
    """
    engine = engine.lower().strip()

    # --- Rankinis pasirinkimas ---
    if engine == "pdfplumber":
        try:
            return _run_pdfplumber(pdf_bytes)
        except Exception as e:
            raise HTTPException(status_code=422, detail="pdfplumber klaida: {}".format(e))

    if engine == "google_vision":
        try:
            return _run_google_vision(pdf_bytes)
        except Exception as e:
            raise HTTPException(status_code=422, detail="Google Vision klaida: {}".format(e))

    if engine == "tesseract":
        try:
            return _run_tesseract(pdf_bytes)
        except Exception as e:
            raise HTTPException(status_code=500, detail="Tesseract klaida: {}".format(e))

    if engine != "auto":
        raise HTTPException(status_code=400, detail="Nežinomas variklis '{}'. Galimi: {}".format(engine, AVAILABLE_ENGINES))

    # --- Auto režimas: pdfplumber → Google Vision → Tesseract ---
    try:
        return _run_pdfplumber(pdf_bytes)
    except Exception as e:
        logger.warning("pdfplumber klaida: {}".format(e))

    if os.environ.get("GOOGLE_API_KEY"):
        try:
            return _run_google_vision(pdf_bytes)
        except Exception as e:
            logger.warning("Google OCR klaida: {}".format(e))

    try:
        return _run_tesseract(pdf_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail="OCR klaida: {}".format(e))

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class VinResult(BaseModel):
    vin: str; valid: bool; errors: List[str]; warnings: List[str]; decoded: Dict[str, Any]

class ExtractionResponse(BaseModel):
    filename: str; extraction_method: str; total_candidates: int
    valid_vins: int; invalid_vins: int
    results: List[VinResult]
    parsed_fields: Dict[str, Any] = {}

class SingleVinResponse(BaseModel):
    vin: str; valid: bool; errors: List[str]; warnings: List[str]; decoded: Dict[str, Any]

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/engines")
def list_engines():
    """Grąžina galimus OCR variklius ir jų būseną."""
    google_ready = bool(os.environ.get("GOOGLE_API_KEY"))
    try:
        tesseract_ready = bool(pytesseract.get_tesseract_version())
    except Exception:
        tesseract_ready = False
    return {
        "engines": [
            {"id": "auto",          "name": "Auto (rekomenduojama)", "available": True,           "description": "Bando pdfplumber → Google Vision → Tesseract"},
            {"id": "pdfplumber",    "name": "pdfplumber",            "available": True,           "description": "Greitas tekstinis PDF ištraukimas, be OCR"},
            {"id": "google_vision", "name": "Google Vision OCR",     "available": google_ready,   "description": "Debesų OCR – tikslus, reikia GOOGLE_API_KEY"},
            {"id": "tesseract",     "name": "Tesseract OCR",         "available": tesseract_ready,"description": "Vietinis OCR – veikia be interneto"},
        ],
        "default": "auto"
    }

@app.get("/debug/env")
def debug_env():
    key = os.environ.get("GOOGLE_API_KEY", "")
    return {
        "GOOGLE_API_KEY_set": bool(key),
        "GOOGLE_API_KEY_length": len(key),
        "GOOGLE_API_KEY_preview": key[:8] + "..." if key else "NERA",
        "all_env_keys": [k for k in os.environ.keys() if "GOOGLE" in k or "API" in k]
    }

@app.get("/")
def root():
    return {"service":"VIN Extractor API","version":"3.0.0","google_ocr": bool(os.environ.get("GOOGLE_API_KEY"))}

@app.get("/health")
def health():
    status = {"status":"ok","version":"3.0.0","google_ocr": bool(os.environ.get("GOOGLE_API_KEY")),"libraries":{}}
    try: import pdfplumber as _; status["libraries"]["pdfplumber"] = "ok"
    except: status["libraries"]["pdfplumber"] = "NERA"; status["status"] = "degraded"
    try: pytesseract.get_tesseract_version(); status["libraries"]["tesseract"] = "ok"
    except: status["libraries"]["tesseract"] = "NERA"; status["status"] = "degraded"
    try: import pdf2image as _; status["libraries"]["pdf2image"] = "ok"
    except: status["libraries"]["pdf2image"] = "NERA"
    return status

@app.post("/extract", response_model=ExtractionResponse)
async def extract_vins(
    file: UploadFile = File(...),
    only_valid: bool = Query(False),
    only_trucks: bool = Query(False),
    engine: EngineEnum = Query(EngineEnum.google_vision, description="OCR variklis"),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Reikalingas PDF failas")
    pdf_bytes = await file.read()
    if not pdf_bytes: raise HTTPException(status_code=400, detail="Tuscias failas")
    text, method = extract_text_from_pdf(pdf_bytes, engine=engine)
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
        if only_trucks and not r.get("decoded",{}).get("is_truck"): continue
        results.append(VinResult(**r))
    results.sort(key=lambda x: (not x.valid, x.vin))
    valid_count = sum(1 for r in results if r.valid)
    return ExtractionResponse(filename=file.filename, extraction_method=method, total_candidates=len(candidates), valid_vins=valid_count, invalid_vins=len(results)-valid_count, results=results, parsed_fields=parsed_fields)

@app.get("/validate/{vin}", response_model=SingleVinResponse)
def validate_single(vin: str):
    return SingleVinResponse(**validate_vin(vin))

@app.post("/debug/ocr")
async def debug_ocr(
    file: UploadFile = File(...),
    engine: EngineEnum = Query(EngineEnum.google_vision, description="OCR variklis"),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Reikalingas PDF failas")
    pdf_bytes = await file.read()
    text, method = extract_text_from_pdf(pdf_bytes, engine=engine)
    candidates = find_vins_in_text(text)
    return {"method":method,"char_count":len(text),"raw_text":text,"fixed_text":fix_ocr_errors(text),"vin_candidates_found":candidates,"parsed_fields":parse_regitra_fields(text)}