from flask import Flask, request, jsonify, send_from_directory
from PIL import Image, ImageDraw, ImageFont
import base64, io, qrcode, os, gspread, re, string, csv, json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from threading import Lock

app = Flask(__name__, static_folder="templates")

OUTPUT_DIR = "OUTPUT"
os.makedirs(OUTPUT_DIR, exist_ok=True)

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
drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

SPREADSHEET_ID = "16LPq3yLMR1B7LO5sWEfD8E14pydyj5dF8W0KJXEs1MU"
sheet = gc.open_by_key(SPREADSHEET_ID)
design_sheet = sheet.worksheet("AVAILABLE_DESIGNS")

PARENT_FOLDER_ID = "1bWI7H_zyXHgn4u0mW_ZzlZ-i_0dB31fF"

HEADERS = [
    "PDF NAME","DESIGN NAME","MRP","LINK","AVAILABILITY",
    "DES-FORMAT","DESNO","CAT(ENG)","CAT (BANGLA)","ID"
]
COLUMN_INDEX = {h:i+1 for i,h in enumerate(HEADERS)}

CATEGORY_MASTER = {
    "HALF SHIRT":("হাফ শার্ট","B"),
    "FULL SHIRT":("ফুল শার্ট","A"),
    "TSHIRT":("টি-শার্ট","E"),
    "FORMAL TROUSER":("ফরমাল ট্রাউজার","D"),
    "COTTON TROUSER":("কটন ট্রাউজার","C"),
    "JEANS":("জিন্স","F"),
    "BLAZER":("ব্লেজার","G"),
}

lock = Lock()

# ---------------- UTILITIES ----------------

def normalize_design(d):
    d = d.strip().upper()
    if not d.startswith("DES-"):
        d = f"DES-{d}"
    return d

def extract_desno(design):
    match = re.search(r'(\d+)', design)
    return match.group(1) if match else ""

def extract_format(design):
    core = design.replace("DES-","")
    digits = extract_desno(design)
    return core.replace(digits,"")

def get_next_id():
    col = design_sheet.col_values(COLUMN_INDEX["ID"])[1:]
    letters = [c for c in col if c.strip() in string.ascii_uppercase]
    if not letters:
        return "A"
    last = sorted(letters)[-1]
    idx = string.ascii_uppercase.index(last)
    return string.ascii_uppercase[idx+1] if idx < 25 else last

def upload_to_drive(path, filename):
    meta = {"name":filename,"parents":[PARENT_FOLDER_ID]}
    media = MediaFileUpload(path,mimetype="image/jpeg")
    file = drive_service.files().create(
        body=meta,media_body=media,fields="id"
    ).execute()
    file_id = file.get("id")
    drive_service.permissions().create(
        fileId=file_id,
        body={"role":"reader","type":"anyone"}
    ).execute()
    link = f"https://drive.google.com/file/d/{file_id}/view"
    return file_id, link

def update_availability(value, status):
    value = value.strip().upper()
    data = design_sheet.get_all_values()
    for idx, row in enumerate(data[1:], start=2):
        if value == row[COLUMN_INDEX["DESNO"]-1] or value == row[COLUMN_INDEX["DESIGN NAME"]-1].upper():
            design_sheet.update(f"E{idx}", status)
            return True
    return False

# ---------------- ROUTES ----------------

@app.route("/")
def home():
    return send_from_directory("templates","index.html")

@app.route("/categories")
def categories():
    col = design_sheet.col_values(COLUMN_INDEX["CAT(ENG)"])[1:]
    unique = set([c.strip() for c in col if c.strip()])
    unique.update(CATEGORY_MASTER.keys())
    return jsonify({"categories":sorted(unique)})

@app.route("/render",methods=["POST"])
def render():
    data = request.json
    entries = data.get("entries", [])

    rows_to_append = []

    with lock:
        for item in entries:
            image_data = item.get("image")
            design_raw = item.get("design")
            mrp = item.get("mrp")
            cat_eng = item.get("category_eng","").upper().strip()

            design = normalize_design(design_raw)
            desno = extract_desno(design)
            des_format = extract_format(design)

            if cat_eng not in CATEGORY_MASTER:
                new_id = get_next_id()
                CATEGORY_MASTER[cat_eng] = (cat_eng, new_id)

            cat_bangla, cat_id = CATEGORY_MASTER[cat_eng]

            header, encoded = image_data.split(",",1)
            img = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGB")

            W,H = 1600,2000
            canvas = Image.new("RGB",(W,H),"white")
            draw = ImageDraw.Draw(canvas)
            draw.rectangle([5,5,W-5,H-5],outline="black",width=2)

            img.thumbnail((W-80,H-360))
            canvas.paste(img,((W-img.width)//2,30))

            font_big = ImageFont.truetype("DejaVuSans-Bold.ttf",56)
            font_small = ImageFont.truetype("DejaVuSans.ttf",44)

            text_y = H-260
            draw.text((60,text_y),design,fill="black",font=font_big)
            draw.text((60,text_y+75),f"MRP: {mrp}",fill="black",font=font_small)

            qr = qrcode.make(design)
            qr = qr.resize((280,280))
            canvas.paste(qr,(W-330,H-330))

            path = os.path.join(OUTPUT_DIR,f"{design}.jpg")
            canvas.save(path,"JPEG")

            file_id, drive_link = upload_to_drive(path,f"{design}.jpg")

            rows_to_append.append([
                file_id, design, mrp, drive_link, "YES",
                des_format, desno, cat_eng, cat_bangla, cat_id
            ])

        if rows_to_append:
            design_sheet.append_rows(rows_to_append, value_input_option="RAW")

    return jsonify({"ok":True})

@app.route("/remove",methods=["POST"])
def remove():
    return jsonify({"ok":update_availability(request.json.get("design",""),"NO")})

@app.route("/restock",methods=["POST"])
def restock():
    return jsonify({"ok":update_availability(request.json.get("design",""),"YES")})

@app.route("/report/available")
def report():
    data = design_sheet.get_all_values()
    rows = data[1:]
    result = []
    for r in rows:
        result.append({
            "CAT (BANGLA)":r[COLUMN_INDEX["CAT (BANGLA)"]-1],
            "CAT(ENG)":r[COLUMN_INDEX["CAT(ENG)"]-1],
            "DESIGN NAME":r[COLUMN_INDEX["DESIGN NAME"]-1],
            "MRP":r[COLUMN_INDEX["MRP"]-1],
        })
    return jsonify({"data":result})

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0",port=port)
