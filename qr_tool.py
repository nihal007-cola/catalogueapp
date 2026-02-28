from flask import Flask, request, jsonify, send_from_directory
from PIL import Image, ImageDraw, ImageFont
import base64, io, qrcode, os, gspread, re, string
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

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

def next_sequential_id():
    col = design_sheet.col_values(COLUMN_INDEX["ID"])[1:]
    existing = [c for c in col if c.strip()]
    if not existing:
        return "A"
    last = sorted(existing)[-1]
    if last not in string.ascii_uppercase:
        return "A"
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
    link = f"https://drive.google.com/file/d/{file_id}/view?usp=drivesdk"
    return file_id, link

def update_availability(value, status):
    value = value.strip().upper()

    desno_col = design_sheet.col_values(COLUMN_INDEX["DESNO"])
    design_col = design_sheet.col_values(COLUMN_INDEX["DESIGN NAME"])

    for i in range(1, len(desno_col)):
        sheet_desno = desno_col[i].strip()
        sheet_design = design_col[i].strip().upper()

        if value == sheet_desno:
            design_sheet.update_cell(i+1, COLUMN_INDEX["AVAILABILITY"], status)
            return True

        if value == sheet_design:
            design_sheet.update_cell(i+1, COLUMN_INDEX["AVAILABILITY"], status)
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

    image_data = data.get("image")
    design_raw = data.get("design")
    mrp = data.get("mrp")
    cat_eng = data.get("category_eng","").upper().strip()

    if not image_data or not design_raw or not mrp:
        return jsonify({"ok":False,"error":"Missing fields"})

    design = normalize_design(design_raw)
    desno = extract_desno(design)
    des_format = extract_format(design)

    if not desno:
        return jsonify({"ok":False,"error":"Invalid Design Format"})

    if cat_eng not in CATEGORY_MASTER:
        new_id = next_sequential_id()
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

    row = [
        file_id,
        design,
        mrp,
        drive_link,
        "YES",
        des_format,
        desno,
        cat_eng,
        cat_bangla,
        cat_id
    ]

    design_sheet.append_row(row)
    return jsonify({"ok":True})

@app.route("/remove",methods=["POST"])
def remove():
    val = request.json.get("design","")
    success = update_availability(val,"NO")
    return jsonify({"ok":success})

@app.route("/restock",methods=["POST"])
def restock():
    val = request.json.get("design","")
    success = update_availability(val,"YES")
    return jsonify({"ok":success})

@app.route("/deduplicate",methods=["POST"])
def deduplicate():
    col = design_sheet.col_values(COLUMN_INDEX["DESIGN NAME"])[1:]
    seen = set()
    delete = []

    for idx in range(len(col)-1,-1,-1):
        r = idx+2
        if col[idx] in seen:
            delete.append(r)
        else:
            seen.add(col[idx])

    delete.sort(reverse=True)
    for r in delete:
        design_sheet.delete_rows(r)

    return jsonify({"deleted_rows":len(delete)})

@app.route("/report/available")
def report():
    show_all = request.args.get("all","false")=="true"
    data = design_sheet.get_all_values()
    headers = data[0]
    rows = data[1:]
    output = []

    for r in rows:
        if show_all or r[COLUMN_INDEX["AVAILABILITY"]-1]=="YES":
            output.append(dict(zip(headers,r)))

    return jsonify({"data":output})

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0",port=port)
