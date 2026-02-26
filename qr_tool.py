from flask import Flask, request, jsonify, send_from_directory
from PIL import Image, ImageDraw, ImageFont
import base64
import io
import qrcode
import os
import gspread

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__, static_folder="templates")

OUTPUT_DIR = "OUTPUT"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---------------- GOOGLE CONFIG ----------------

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_file(
    "service_account.json",
    scopes=SCOPES
)

gc = gspread.authorize(creds)
drive_service = build("drive", "v3", credentials=creds)

SPREADSHEET_ID = "16LPq3yLMR1B7LO5sWEfD8E14pydyj5dF8W0KJXEs1MU"
sheet = gc.open_by_key(SPREADSHEET_ID)

design_sheet = sheet.worksheet("AVAILABLE_DESIGNS")
pwd_sheet = sheet.worksheet("PASSWORD")

# 👉 YOUR PROVIDED FOLDER
PARENT_FOLDER_ID = "1dBZrNjVtfMz2jay-CI4cHA8rCZarF3OO"


# ---------------- DRIVE HELPERS ----------------

def get_or_create_linux_upload_folder():

    query = f"'{PARENT_FOLDER_ID}' in parents and name='Linux upload' and trashed=false"
    results = drive_service.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True
    ).execute()

    files = results.get("files", [])

    if files:
        return files[0]["id"]

    folder_metadata = {
        "name": "Linux upload",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [PARENT_FOLDER_ID]
    }

    folder = drive_service.files().create(
        body=folder_metadata,
        fields="id",
        supportsAllDrives=True
    ).execute()

    return folder.get("id")


def upload_to_drive(filepath, filename):

    folder_id = get_or_create_linux_upload_folder()

    file_metadata = {
        "name": filename,
        "parents": [folder_id]
    }

    media = MediaFileUpload(filepath, mimetype="image/jpeg")

    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id",
        supportsAllDrives=True
    ).execute()

    file_id = file.get("id")

    drive_service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
        supportsAllDrives=True
    ).execute()

    return f"https://drive.google.com/file/d/{file_id}/view"


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


@app.route("/render", methods=["POST"])
def render():

    data = request.json

    image_data = data.get("image")
    design = data.get("design")
    mrp = data.get("mrp")

    if not image_data or not design or not mrp:
        return jsonify({"error": "Missing fields"})

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

    draw.text((80, 1550), f"DES-{design}", fill="black", font=font_big)
    draw.text((80, 1620), f"MRP: {mrp}", fill="black", font=font_small)

    qr = qrcode.make(design)
    qr = qr.resize((250, 250))
    canvas.paste(qr, (W-330, H-330))

    output_path = os.path.join(OUTPUT_DIR, f"{design}.jpg")
    canvas.save(output_path, "JPEG")

    try:
        drive_link = upload_to_drive(output_path, f"{design}.jpg")
    except Exception as e:
        return jsonify({"error": f"Drive upload failed: {str(e)}"})

    try:
        design_sheet.append_row([
            "",
            f"DES-{design}",
            mrp,
            drive_link,
            "YES"
        ])
    except Exception as e:
        return jsonify({"error": f"Sheet write failed: {str(e)}"})

    return jsonify({"ok": True})


@app.route("/remove", methods=["POST"])
def remove_design():

    data = request.json
    design = data.get("design", "").strip()

    if not design:
        return jsonify({"error": "Missing design"})

    records = design_sheet.get_all_records()

    for idx, row in enumerate(records, start=2):
        if row.get("DESIGN NAME") == design or row.get("DESIGN NAME") == f"DES-{design}":
            design_sheet.update(f"E{idx}", "NO")
            return jsonify({"ok": True})

    return jsonify({"error": "Design not found"})


@app.route("/restock", methods=["POST"])
def restock_design():

    data = request.json
    design = data.get("design", "").strip()

    if not design:
        return jsonify({"error": "Missing design"})

    records = design_sheet.get_all_records()

    for idx, row in enumerate(records, start=2):
        if row.get("DESIGN NAME") == design or row.get("DESIGN NAME") == f"DES-{design}":
            design_sheet.update(f"E{idx}", "YES")
            return jsonify({"ok": True})

    return jsonify({"error": "Design not found"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)