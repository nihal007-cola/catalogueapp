from flask import Flask, request, jsonify, send_from_directory
from PIL import Image, ImageDraw, ImageFont
import base64
import io
import qrcode
import os
import gspread
import csv
import tempfile

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


app = Flask(__name__, static_folder="templates")

OUTPUT_DIR = "OUTPUT"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---------------- GOOGLE CONFIG (UNCHANGED) ----------------

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials(
    None,
    refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
    token_uri="https://oauth2.googleapis.com/token",
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    scopes=SCOPES
)

creds.refresh(Request())

gc = gspread.authorize(creds)

drive_service = build(
    "drive",
    "v3",
    credentials=creds,
    cache_discovery=False
)

SPREADSHEET_ID = "16LPq3yLMR1B7LO5sWEfD8E14pydyj5dF8W0KJXEs1MU"
sheet = gc.open_by_key(SPREADSHEET_ID)

design_sheet = sheet.worksheet("AVAILABLE_DESIGNS")
pwd_sheet = sheet.worksheet("PASSWORD")

PARENT_FOLDER_ID = "1bWI7H_zyXHgn4u0mW_ZzlZ-i_0dB31fF"

# ---------------- SHEET STRUCTURE ----------------

HEADERS = [
    "PDF NAME",
    "DESIGN NAME",
    "MRP",
    "LINK",
    "AVAILABILITY",
    "DES-FORMAT",
    "DESNO",
    "CAT(ENG)",
    "CAT (BANGLA)",
    "ID"
]

COLUMN_INDEX = {name: idx + 1 for idx, name in enumerate(HEADERS)}


# ---------------- DRIVE HELPERS ----------------

def upload_to_drive(filepath, filename, folder_id=None):

    parent = folder_id if folder_id else PARENT_FOLDER_ID

    file_metadata = {
        "name": filename,
        "parents": [parent]
    }

    media = MediaFileUpload(filepath, mimetype="image/jpeg")

    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id"
    ).execute()

    file_id = file.get("id")

    drive_service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"}
    ).execute()

    return f"https://drive.google.com/file/d/{file_id}/view"


# ---------------- UTILITIES ----------------

def normalize_design(design):
    design = design.strip().upper()
    if not design.startswith("DES-"):
        design = f"DES-{design}"
    return design


def update_availability(design_name, value):
    col = design_sheet.col_values(COLUMN_INDEX["DESIGN NAME"])
    for idx, val in enumerate(col):
        if val == design_name:
            row_number = idx + 1
            design_sheet.update_cell(row_number, COLUMN_INDEX["AVAILABILITY"], value)
            return True
    return False


# ---------------- ROUTES ----------------

@app.route("/")
def home():
    return send_from_directory("templates", "index.html")


@app.route("/checkPassword", methods=["POST"])
def check_password():
    data = request.json
    user_pwd = data.get("password", "").strip()
    real_pwd = pwd_sheet.acell("A1").value.strip()
    return jsonify({"ok": user_pwd == real_pwd})


# ---------------- SINGLE DESIGN RENDER ----------------

@app.route("/render", methods=["POST"])
def render():

    data = request.json

    image_data = data.get("image")
    design_raw = data.get("design")
    mrp = data.get("mrp")
    cat_eng = data.get("category_eng", "")
    cat_bangla = data.get("category_bangla", "")
    des_format = data.get("des_format", "")
    desno = data.get("desno", "")
    id_value = data.get("id", "")
    folder_id = data.get("folder_id", None)

    if not image_data or not design_raw or not mrp:
        return jsonify({"error": "Missing fields"})

    design = normalize_design(design_raw)

    try:
        header, encoded = image_data.split(",", 1)
        image_bytes = base64.b64decode(encoded)
    except:
        return jsonify({"error": "Invalid image"})

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    W, H = 1600, 2000
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)

    draw.rectangle([10, 10, W-10, H-10], outline="black", width=3)

    img.thumbnail((1400, 1400))
    canvas.paste(img, ((W - img.width)//2, 80))

    try:
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 48)
        font_small = ImageFont.truetype("DejaVuSans.ttf", 40)
    except:
        font_big = None
        font_small = None

    draw.text((80, 1550), design, fill="black", font=font_big)
    draw.text((80, 1620), f"MRP: {mrp}", fill="black", font=font_small)

    qr = qrcode.make(design)
    qr = qr.resize((250, 250))
    canvas.paste(qr, (W-330, H-330))

    output_path = os.path.join(OUTPUT_DIR, f"{design}.jpg")
    canvas.save(output_path, "JPEG")

    try:
        drive_link = upload_to_drive(output_path, f"{design}.jpg", folder_id)
    except Exception as e:
        return jsonify({"error": f"Drive upload failed: {str(e)}"})

    row = [
        "",
        design,
        mrp,
        drive_link,
        "YES",
        des_format,
        desno,
        cat_eng,
        cat_bangla,
        id_value
    ]

    design_sheet.append_row(row)

    return jsonify({"ok": True})


# ---------------- REMOVE ----------------

@app.route("/remove", methods=["POST"])
def remove_design():

    design_raw = request.json.get("design", "")
    design = normalize_design(design_raw)

    success = update_availability(design, "NO")

    if not success:
        return jsonify({"error": "Design not found"})

    return jsonify({"ok": True})


# ---------------- RESTOCK ----------------

@app.route("/restock", methods=["POST"])
def restock_design():

    design_raw = request.json.get("design", "")
    design = normalize_design(design_raw)

    success = update_availability(design, "YES")

    if not success:
        return jsonify({"error": "Design not found"})

    return jsonify({"ok": True})


# ---------------- DEDUPLICATE (KEEP LATEST) ----------------

@app.route("/deduplicate", methods=["POST"])
def deduplicate():

    design_col = design_sheet.col_values(COLUMN_INDEX["DESIGN NAME"])

    if len(design_col) <= 1:
        return jsonify({"ok": True, "deleted_rows": 0})

    design_col = design_col[1:]

    last_occurrence = {}
    rows_to_delete = []

    for idx, design in enumerate(design_col, start=2):
        if design:
            last_occurrence[design] = idx

    for idx, design in enumerate(design_col, start=2):
        if design:
            if last_occurrence.get(design) != idx:
                rows_to_delete.append(idx)

    rows_to_delete.sort(reverse=True)

    for row in rows_to_delete:
        design_sheet.delete_rows(row)

    return jsonify({
        "ok": True,
        "deleted_rows": len(rows_to_delete)
    })


# ---------------- AVAILABLE REPORT ----------------

@app.route("/report/available", methods=["GET"])
def available_report():

    all_data = design_sheet.get_all_values()

    if len(all_data) <= 1:
        return jsonify({"data": []})

    headers = all_data[0]
    rows = all_data[1:]

    available_rows = []

    for row in rows:
        if len(row) >= COLUMN_INDEX["AVAILABILITY"]:
            if row[COLUMN_INDEX["AVAILABILITY"] - 1] == "YES":
                available_rows.append(dict(zip(headers, row)))

    return jsonify({"data": available_rows})


# ---------------- EXCEL IMPORT ----------------

@app.route("/importExcel", methods=["POST"])
def import_excel():

    file = request.files.get("file")

    if not file:
        return jsonify({"error": "No file uploaded"})

    temp = tempfile.NamedTemporaryFile(delete=False)
    file.save(temp.name)

    rows_added = 0

    with open(temp.name, newline='', encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)

        required = ["DESIGN NAME", "MRP"]

        if not all(col in reader.fieldnames for col in required):
            return jsonify({"error": "Invalid headers"})

        for row in reader:
            design = normalize_design(row["DESIGN NAME"])
            mrp = row["MRP"]

            design_sheet.append_row([
                "",
                design,
                mrp,
                "",
                "YES",
                "",
                "",
                "",
                "",
                ""
            ])

            rows_added += 1

    return jsonify({"ok": True, "rows_added": rows_added})


# ---------------- MAIN ----------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
