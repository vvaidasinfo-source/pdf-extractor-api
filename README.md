# VIN Extractor API

Python FastAPI mikroservisas, kuris nuskaito PDF dokumentus ir tikrina VIN kodus.

## Funkcijos

- **Tekstiniai PDF** – greitas nuskaitymas su `pdfplumber`
- **Skenai / nuotraukos** – automatinis OCR su Tesseract
- **VIN validacija** – ISO 3779 standartas (ilgis, simboliai, checksum)
- **Gamintojai** – atpažįsta Volvo, Scania, MAN, Mercedes, DAF, Iveco ir kt.
- **Filtrai** – tik validūs, tik sunkvežimiai

---

## Paleidimas

### Variantas 1: Docker (rekomenduojama)

```bash
docker-compose up --build
```

API bus pasiekiamas: http://localhost:8000

### Variantas 2: Lokaliai

```bash
# Įdiegti Tesseract ir Poppler
# Ubuntu/Debian:
sudo apt install tesseract-ocr tesseract-ocr-lit poppler-utils

# macOS:
brew install tesseract poppler

# Python priklausomybės
pip install -r requirements.txt

# Paleisti serverį
uvicorn main:app --reload --port 8000
```

---

## API naudojimas

### 1. PDF nuskaitymas

```bash
curl -X POST "http://localhost:8000/extract" \
  -F "file=@vilkikas.pdf"
```

#### Atsakymo pavyzdys:

```json
{
  "filename": "vilkikas.pdf",
  "extraction_method": "pdfplumber",
  "total_candidates": 3,
  "valid_vins": 2,
  "invalid_vins": 1,
  "results": [
    {
      "vin": "YS2R4X20XA123456",
      "valid": true,
      "errors": [],
      "warnings": [],
      "decoded": {
        "wmi": "YS2",
        "manufacturer": "Scania (Švedija)",
        "vds": "4X20XA",
        "check_digit": "0",
        "model_year": 2010,
        "plant_code": "A",
        "serial_number": "123456",
        "is_truck": true
      }
    }
  ]
}
```

### 2. Tik validūs VIN

```bash
curl -X POST "http://localhost:8000/extract?only_valid=true" \
  -F "file=@dokumentas.pdf"
```

### 3. Tik sunkvežimių VIN

```bash
curl -X POST "http://localhost:8000/extract?only_trucks=true" \
  -F "file=@dokumentas.pdf"
```

### 4. Vieno VIN tikrinimas

```bash
curl "http://localhost:8000/validate/YS2R4X20XA123456"
```

### 5. Sistemos būsena

```bash
curl "http://localhost:8000/health"
```

---

## Python kliento pavyzdys

```python
import httpx

# Nuskaityti PDF
with open("vilkikas.pdf", "rb") as f:
    response = httpx.post(
        "http://localhost:8000/extract",
        files={"file": ("vilkikas.pdf", f, "application/pdf")},
        params={"only_valid": True, "only_trucks": True}
    )

data = response.json()
print(f"Rasta validžių VIN: {data['valid_vins']}")
for r in data["results"]:
    print(f"  {r['vin']} — {r['decoded']['manufacturer']}")
```

---

## Paketo naudojimas (batch – daug PDF)

```python
import httpx
from pathlib import Path

client = httpx.Client(base_url="http://localhost:8000", timeout=60)

for pdf_path in Path("./pdfs").glob("*.pdf"):
    with open(pdf_path, "rb") as f:
        resp = client.post(
            "/extract",
            files={"file": (pdf_path.name, f, "application/pdf")},
            params={"only_valid": True}
        )
    data = resp.json()
    print(f"{pdf_path.name}: {data['valid_vins']} validžių VIN")
```

---

## Atpažįstami gamintojai (WMI)

| WMI | Gamintojas |
|-----|-----------|
| YS2 | Scania (Švedija) |
| YV2 | Volvo Trucks (Švedija) |
| WMA | MAN (Vokietija) |
| WDB | Mercedes-Benz Trucks |
| XLR | DAF (Nyderlandai) |
| ZCF | Iveco (Italija) |
| VF6 | Renault Trucks |
| WMA | MAN (Vokietija) |

---

## Architektūra

```
PDF įvestis
    │
    ├─ Tekstinis PDF → pdfplumber (greitas ~0.1s/psl.)
    │
    └─ Skenuo­tas PDF → Tesseract OCR (lėtesnis ~2-5s/psl.)
              │
              ▼
        Regex VIN paieška
              │
              ▼
        ISO 3779 Validacija
        • Ilgis (17 simbolių)
        • Draudžiami simboliai (I, O, Q)
        • Checksum (9 poz.)
        • Modelių metai
        • WMI dekodavimas
              │
              ▼
        JSON atsakymas
```
