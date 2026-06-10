import os
import json
import base64
import requests
import io
from datetime import datetime
from flask import Flask, request, jsonify

# Library untuk integrasi Google Sheets & Google Drive
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

app = Flask(__name__)

# ==================== KONFIGURASI KREDENSIAL (DENGAN STRIP OTOMATIS) ====================
# Membersihkan tanda kutip ganda/tunggal dan spasi yang tidak sengaja ter-paste di Vercel
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip("'\" ")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip("'\" ")
GOOGLE_SHEET_KEY = os.environ.get("GOOGLE_SHEET_KEY", "").strip("'\" ")
GOOGLE_SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME", "Sheet1").strip("'\" ")

# ID Folder Google Drive tempat menyimpan struk
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip("'\" ")

# Mengambil kredensial Google Service Account
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

def get_google_credentials():
    """Mengembalikan objek kredensial Google yang sah untuk Sheets dan Drive."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        return Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        return Credentials.from_service_account_file("credentials.json", scopes=scopes)

def get_gspread_client():
    """Melakukan autentikasi ke Google Sheets API."""
    creds = get_google_credentials()
    return gspread.authorize(creds)

# ==================== GOOGLE DRIVE SERVICE ====================
def upload_to_google_drive(image_bytes, mime_type="image/jpeg"):
    """Mengunggah foto struk ke Google Drive dan mengubah nama sesuai format."""
    creds = get_google_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    
    # Cari dan hitung jumlah struk belanja yang sudah ada untuk menentukan nomor urut selanjutnya
    query = "name contains 'struk_belanja_'"
    if GOOGLE_DRIVE_FOLDER_ID:
        query += f" and '{GOOGLE_DRIVE_FOLDER_ID}' in parents"
    query += " and trashed = false"
    
    try:
        results = drive_service.files().list(q=query, fields="files(id, name)").execute()
        existing_files = results.get('files', [])
        nomor_urut = len(existing_files) + 1
    except Exception as e:
        print(f"Gagal mendeteksi nomor urut dari Drive, gunakan default 1. Error: {str(e)}")
        nomor_urut = 1
        
    # Format nama file: struk_belanja_nomorUrut_ddmmyyyy
    today_str = datetime.now().strftime("%d%m%Y")
    ext = ".jpg" if "png" not in mime_type else ".png"
    filename = f"struk_belanja_{nomor_urut}_{today_str}{ext}"
    
    file_metadata = {'name': filename}
    if GOOGLE_DRIVE_FOLDER_ID:
        file_metadata['parents'] = [GOOGLE_DRIVE_FOLDER_ID]
        
    media = MediaIoBaseUpload(io.BytesIO(image_bytes), mimetype=mime_type, resumable=True)
    
    # Buat file di Google Drive
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, webViewLink'
    ).execute()
    
    return filename, file.get('webViewLink')

# ==================== KELAS UTAMA GEMINI AI INTEGRATION ====================
class GeminiService:
    @staticmethod
    def _execute_post_request(payload):
        """Menembak API Gemini dengan URL stabil v1beta menggunakan model gemini-1.5-flash."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code == 200:
                return response.json()
            else:
                error_msg = response.text
                try:
                    err_json = response.json()
                    error_msg = err_json.get("error", {}).get("message", response.text)
                except:
                    pass
                raise Exception(f"Google API Error {response.status_code}: {error_msg}")
        except Exception as e:
            raise Exception(f"Koneksi ke Gemini API gagal: {str(e)}")

    @classmethod
    def analyze_message_intent(cls, user_text):
        """Menganalisis pesan pengguna menggunakan format prompt yang kompatibel dengan semua API key."""
        today_str = datetime.now().strftime("%Y-%m-%d %A")
        
        prompt = (
            "Kamu adalah asisten keuangan pribadi cerdas. Tugasmu adalah menganalisis pesan pengguna dan mengklasifikasikan intensinya.\n"
            f"Hari ini adalah hari {today_str}.\n\n"
            "Klasifikasikan pesan ke salah satu dari 3 intensi ini:\n"
            "1. 'ADD_EXPENSE': Keinginan mencatat pengeluaran baru.\n"
            "2. 'RECAP_REQUEST': Permintaan laporan/ringkasan/rekap pengeluaran untuk jangka waktu tertentu.\n"
            "3. 'GENERAL': Sapaan, perintah start, perintah test, atau percakapan umum.\n\n"
            "Kembalikan data harus dalam format JSON valid dengan struktur persis seperti di bawah ini:\n"
            "{\n"
            "  \"intent\": \"ADD_EXPENSE\" atau \"RECAP_REQUEST\" atau \"GENERAL\",\n"
            "  \"expense_data\": {\n"
            "    \"biaya\": 50000 (nominal angka saja/integer tanpa simbol),\n"
            "    \"uraian\": \"nama pengeluaran singkat\",\n"
            "    \"tanggal\": \"YYYY-MM-DD\" (tanggal transaksi, gunakan tanggal hari ini jika tidak disebutkan),\n"
            "    \"kategori\": \"Makanan\" atau \"Transportasi\" atau \"Kebutuhan Rumah\" atau \"Kesehatan\" atau \"Hiburan\" atau \"Listrik & Air\" atau \"Gadget\" atau \"Lainnya\"\n"
            "  },\n"
            "  \"recap_params\": {\n"
            "    \"start_date\": \"YYYY-MM-DD\" (tanggal mulai periode laporan),\n"
            "    \"end_date\": \"YYYY-MM-DD\" (tanggal akhir periode laporan),\n"
            "    \"period_description\": \"deskripsi periode ramah\"\n"
            "  }\n"
            "}\n\n"
            f"Pesan pengguna: '{user_text}'"
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        
        result = cls._execute_post_request(payload)
        raw_text = result['candidates'][0]['content']['parts'][0]['text']
        return json.loads(raw_text.strip())

    @classmethod
    def analyze_image(cls, image_bytes, mime_type="image/jpeg"):
        """Menganalisis gambar struk menggunakan Gemini Vision dengan prompt terstruktur."""
        today_str = datetime.now().strftime("%Y-%m-%d %A")
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        prompt = (
            "Kamu adalah asisten keuangan pribadi yang sangat pintar.\n"
            "Tugasmu adalah menganalisis gambar struk belanja ini, lakukan OCR, dan kembalikan data dalam format JSON terstruktur.\n"
            f"Hari ini adalah tanggal {today_str}.\n"
            "Kembalikan data harus dalam format JSON valid dengan struktur persis seperti ini:\n"
            "{\n"
            "  \"biaya\": 150000 (total pengeluaran, angka saja/integer tanpa titik/koma/simbol),\n"
            "  \"uraian\": \"belanja bulanan/beli barang\",\n"
            "  \"tanggal\": \"YYYY-MM-DD\" (tanggal struk, gunakan hari ini jika tidak tertera),\n"
            "  \"kategori\": \"Makanan\" atau \"Transportasi\" atau \"Kebutuhan Rumah\" atau \"Kesehatan\" atau \"Hiburan\" atau \"Listrik & Air\" atau \"Gadget\" atau \"Lainnya\"\n"
            "}"
        )
        
        payload = {
            "contents": [{
                "parts": [
                    {"inlineData": {"mimeType": mime_type, "data": base64_image}},
                    {"text": prompt}
                ]
            }],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        
        result = cls._execute_post_request(payload)
        raw_text = result['candidates'][0]['content']['parts'][0]['text']
        return json.loads(raw_text.strip())

    @classmethod
    def generate_recap_report(cls, expenses, period_desc):
        """Menggunakan AI untuk menghasilkan draf laporan keuangan yang menarik berdasarkan data filter."""
        expenses_json = json.dumps(expenses, indent=2)
        
        prompt = (
            "Kamu adalah seorang perencana keuangan keluarga (Financial Advisor) profesional, hangat, ramah, dan interaktif.\n"
            "Tugasmu adalah mengubah data mentah pengeluaran pengguna menjadi laporan keuangan periodik yang sangat menarik, rapi, dan mudah dipahami.\n\n"
            "Gunakan format Markdown Telegram yang didukung:\n"
            "- Gunakan huruf tebal (*teks*) untuk penekanan angka, judul, atau kategori.\n"
            "- Gunakan daftar berpoin dengan emoji menarik yang representatif.\n\n"
            "Struktur laporan yang wajib kamu hasilkan:\n"
            "1. 📊 *Judul Laporan & Periode Rekap*\n"
            "2. 💵 *Ringkasan Total Pengeluaran*\n"
            "3. 🗂️ *Rincian Pengeluaran per Kategori* diurutkan dari yang terbesar, hitung persentase kontribusinya.\n"
            "4. 💡 *Insight Penting* (pola belanja)\n"
            "5. 🌱 *Tips Finansial Pendek*\n\n"
            f"Berikut adalah data pengeluaran mentah saya:\n{expenses_json}\n\n"
            f"Tolong buatkan ringkasan laporan keuangan yang menarik untuk periode: {period_desc}."
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        
        result = cls._execute_post_request(payload)
        return result['candidates'][0]['content']['parts'][0]['text']

# ==================== HELPERS UNTUK TELEGRAM ====================
def send_telegram_message(chat_id, text):
    """Mengirim pesan teks balasan ke Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

def download_telegram_file(file_id):
    """Mengunduh file gambar dari Telegram."""
    get_file_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
    response = requests.get(get_file_url).json()
    
    if response.get("ok"):
        file_path = response["result"]["file_path"]
        download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        file_response = requests.get(download_url)
        if file_response.status_code == 200:
            return file_response.content
    return None

def validate_environment_variables():
    """Memeriksa apakah semua variabel lingkungan penting sudah diisi dan tidak kosong."""
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    if not GOOGLE_SHEET_KEY:
        missing.append("GOOGLE_SHEET_KEY")
    if not GOOGLE_SERVICE_ACCOUNT_JSON and not os.path.exists("credentials.json"):
        missing.append("GOOGLE_SERVICE_ACCOUNT_JSON (atau file credentials.json)")
    return missing

# ==================== GOOGLE SHEETS & FILTERING HANDLER ====================
def save_to_google_sheet(data, file_url=None):
    """Menyimpan data hasil ekstraksi beserta link Drive (opsional) ke Google Spreadsheet."""
    client = get_gspread_client()
    sheet = client.open_by_key(GOOGLE_SHEET_KEY).worksheet(GOOGLE_SHEET_NAME)
    
    row = [
        data.get("tanggal"),
        data.get("uraian"),
        data.get("kategori"),
        data.get("biaya"),
        file_url if file_url else "-"
    ]
    sheet.append_row(row)

def get_expenses_from_sheet():
    """Mengambil seluruh data dari Google Sheet."""
    client = get_gspread_client()
    sheet = client.open_by_key(GOOGLE_SHEET_KEY).worksheet(GOOGLE_SHEET_NAME)
    return sheet.get_all_values()

def parse_date(date_str):
    """Mengurai format tanggal secara fleksibel guna mencegah error format."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None

def filter_expenses_by_date(all_rows, start_date_str, end_date_str):
    """Memfilter baris data dari spreadsheet berdasarkan rentang tanggal."""
    if len(all_rows) <= 1:
        return []
        
    header = [h.strip().lower() for h in all_rows[0]]
    
    try:
        tgl_idx = header.index("tanggal")
        uraian_idx = header.index("uraian")
        kat_idx = header.index("kategori")
        biaya_idx = header.index("biaya")
    except ValueError:
        tgl_idx, uraian_idx, kat_idx, biaya_idx = 0, 1, 2, 3
        
    link_idx = header.index("link struk belanja") if "link struk belanja" in header else None
    
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    
    filtered_list = []
    for row in all_rows[1:]:
        if len(row) <= max(tgl_idx, uraian_idx, kat_idx, biaya_idx):
            continue
            
        row_date_str = row[tgl_idx].strip()
        row_date = parse_date(row_date_str)
        
        if row_date and (start_date <= row_date <= end_date):
            biaya_clean = row[biaya_idx].replace(".", "").replace(",", "").replace("Rp", "").replace(" ", "").strip()
            try:
                biaya_int = int(biaya_clean)
            except ValueError:
                biaya_int = 0
                
            link_val = row[link_idx] if (link_idx is not None and len(row) > link_idx) else "-"
            
            filtered_list.append({
                "tanggal": row_date.strftime("%Y-%m-%d"),
                "uraian": row[uraian_idx],
                "kategori": row[kat_idx],
                "biaya": biaya_int,
                "link": link_val
            })
            
    return filtered_list

# ==================== WEBHOOK ROUTE ====================
@app.route("/", methods=["GET"])
def index():
    return "Bot Otomasi Telegram ke Google Sheets & Drive dengan AI Rekap aktif!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    """Menerima webhook dari Telegram."""
    update = request.get_json()
    
    if not update or "message" not in update:
        return jsonify({"status": "ignored"}), 200
        
    message = update["message"]
    chat_id = message["chat"]["id"]
    
    # LANGKAH DIAGNOSTIK UTAMA: Validasi Variabel Lingkungan di Vercel
    missing_vars = validate_environment_variables()
    if missing_vars:
        error_msg = (
            "⚠️ *Konfigurasi Vercel Tidak Lengkap!*\n\n"
            "Aplikasi Flask mendeteksi bahwa variabel lingkungan penting berikut belum diisi atau kosong di dashboard Vercel Anda:\n"
            f"{', '.join([f'`{v}`' for v in missing_vars])}\n\n"
            "👉 *Cara Memperbaiki*:\n"
            "1. Masuk ke **Vercel Dashboard**.\n"
            "2. Buka menu **Settings > Environment Variables**.\n"
            "3. Pastikan nama variabel di atas ditulis dengan benar dan diisi nilainya tanpa tanda kutip.\n"
            "4. **PENTING**: Lakukan **Redeploy** (Deploy ulang) pada tab **Deployments** agar perubahan environment variable tersebut aktif!"
        )
        send_telegram_message(chat_id, error_msg)
        return jsonify({"status": "missing_config"}), 200
    
    # 1. Kasus Inputan Gambar (Photo/Struk)
    if "photo" in message:
        send_telegram_message(chat_id, "🔍 *Sedang memproses struk belanja Anda...*")
        try:
            file_id = message["photo"][-1]["file_id"]
            image_bytes = download_telegram_file(file_id)
            
            if not image_bytes:
                send_telegram_message(chat_id, "❌ Gagal mengunduh gambar dari Telegram.")
                return jsonify({"status": "failed_download"}), 200
                
            # Unggah ke Google Drive
            send_telegram_message(chat_id, "📤 *Mengunggah foto struk ke Google Drive...*")
            filename_drive, file_url_drive = upload_to_google_drive(image_bytes, mime_type="image/jpeg")
            
            # Ekstraksi dengan AI
            send_telegram_message(chat_id, "🧠 *AI sedang menganalisis isi struk belanja...*")
            extracted_data = GeminiService.analyze_image(image_bytes)
            
            # Simpan ke Sheets
            save_to_google_sheet(extracted_data, file_url_drive)
            
            report = (
                "✅ *STRUK BERHASIL DIPROSES!*\n\n"
                f"📅 *Tanggal:* {extracted_data.get('tanggal')}\n"
                f"📝 *Uraian:* {extracted_data.get('uraian')}\n"
                f"🏷️ *Kategori:* {extracted_data.get('kategori')}\n"
                f"💵 *Biaya:* Rp {extracted_data.get('biaya'):,}\n\n"
                f"📂 *Google Drive:* [{filename_drive}]({file_url_drive})\n"
                "_Data dan tautan struk telah disimpan otomatis di Google Sheets & Google Drive Anda._"
            )
            send_telegram_message(chat_id, report)
            
        except Exception as e:
            error_msg = f"❌ Terjadi kesalahan saat memproses gambar:\n\n{str(e)}"
            send_telegram_message(chat_id, error_msg)
            
    # 2. Kasus Inputan Teks Bebas
    elif "text" in message:
        user_text = message["text"]
        
        # JALUR DIAGNOSTIK SPESIFIK: Menguji langsung semua API Key di bot Anda
        if user_text.strip() == "/test":
            diagnostic_results = []
            
            # A. Verifikasi Environment Variables
            missing = validate_environment_variables()
            if missing:
                diagnostic_results.append(f"❌ *Environment Var*: Kurang variabel {', '.join(missing)}")
            else:
                diagnostic_results.append("✅ *Environment Var*: Terkonfigurasi lengkap.")
                
            # B. Verifikasi Langsung Gemini API dengan Google
            try:
                test_payload = {
                    "contents": [{"parts": [{"text": "Katakan 'Halo Bot Otomasi Pengeluaran' dalam satu baris saja."}]}]
                }
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
                res = requests.post(url, json=test_payload, headers={"Content-Type": "application/json"}, timeout=10)
                if res.status_code == 200:
                    diagnostic_results.append("✅ *Gemini API*: Berhasil terhubung!")
                else:
                    diagnostic_results.append(f"❌ *Gemini API*: Gagal terhubung (Status {res.status_code})\nDetail Error: `{res.text}`")
            except Exception as ex:
                diagnostic_results.append(f"❌ *Gemini API*: Error Koneksi\nDetail: `{str(ex)}`")
                
            # C. Verifikasi Google Sheets
            try:
                client = get_gspread_client()
                client.open_by_key(GOOGLE_SHEET_KEY).worksheet(GOOGLE_SHEET_NAME)
                diagnostic_results.append("✅ *Google Sheets*: Koneksi berhasil!")
            except Exception as ex:
                diagnostic_results.append(f"❌ *Google Sheets*: Gagal terhubung\nDetail: `{str(ex)}`")
                
            report = "🛠️ *DIAGNOSIS SISTEM BOT OTOMASI*\n\n" + "\n".join(diagnostic_results)
            send_telegram_message(chat_id, report)
            return jsonify({"status": "test_complete"}), 200
            
        if user_text.strip() == "/start":
            welcome_text = (
                "👋 *Halo! Saya Bot Keuangan Pintar.*\n\n"
                "Saya bisa membantu Anda:\n"
                "1. ✍️ *Mencatat Pengeluaran (Teks)*: `Sate kambing 65.000 kemarin siang`\n"
                "2. 📸 *Mencatat Pengeluaran (Gambar)*: Kirim foto struk belanja Anda.\n"
                "3. 📊 *Laporan Rekapitulasi (AI)*: Cukup ketik `rekap minggu ini`, `laporan pengeluaran bulan lalu`.\n"
                "4. 🛠️ *Uji Sistem*: Ketik `/test` untuk memeriksa kesehatan semua API Key Anda.\n\n"
                "Data tersimpan terpusat di Google Sheets & Google Drive Anda!"
            )
            send_telegram_message(chat_id, welcome_text)
            return jsonify({"status": "welcome"}), 200
            
        try:
            analysis = GeminiService.analyze_message_intent(user_text)
            intent = analysis.get("intent")
            
            # A. Intensi: Meminta Rekapitulasi Pengeluaran
            if intent == "RECAP_REQUEST":
                recap_params = analysis.get("recap_params", {})
                start_date = recap_params.get("start_date")
                end_date = recap_params.get("end_date")
                period_desc = recap_params.get("period_description", "Periode Kustom")
                
                if not start_date or not end_date:
                    send_telegram_message(chat_id, "⚠️ Periode laporan kurang jelas. Silakan sebutkan dengan format rentang waktu yang spesifik.")
                    return jsonify({"status": "invalid_recap_params"}), 200
                    
                send_telegram_message(chat_id, f"📊 *Mengumpulkan data transaksi untuk {period_desc}...*")
                
                all_rows = get_expenses_from_sheet()
                matching_expenses = filter_expenses_by_date(all_rows, start_date, end_date)
                
                if not matching_expenses:
                    send_telegram_message(chat_id, f"⚠️ *Tidak ada pengeluaran yang tercatat* untuk periode *{period_desc}* ({start_date} s.d {end_date}).")
                    return jsonify({"status": "no_data_found"}), 200
                
                send_telegram_message(chat_id, "🧠 *AI sedang menyusun laporan analisis keuangan Anda...*")
                report_markdown = GeminiService.generate_recap_report(matching_expenses, period_desc)
                
                send_telegram_message(chat_id, report_markdown)
                
            # B. Intensi: Menambah Pengeluaran Baru
            elif intent == "ADD_EXPENSE":
                send_telegram_message(chat_id, "🧠 *Memproses pencatatan pengeluaran baru Anda...*")
                expense_data = analysis.get("expense_data", {})
                
                save_to_google_sheet(expense_data)
                
                report = (
                    "✅ *PENGELUARAN TERCATAT!*\n\n"
                    f"📅 *Tanggal:* {expense_data.get('tanggal')}\n"
                    f"📝 *Uraian:* {expense_data.get('uraian')}\n"
                    f"🏷️ *Kategori:* {expense_data.get('kategori')}\n"
                    f"💵 *Biaya:* Rp {expense_data.get('biaya'):,}\n\n"
                    "_Data di atas berhasil disimpan otomatis ke Google Sheets Anda._"
                )
                send_telegram_message(chat_id, report)
                
            # C. Intensi: Percakapan Umum/Lainnya
            else:
                send_telegram_message(chat_id, "👋 Maaf, saya tidak begitu memahami instruksi Anda. Silakan ketik perintah pencatatan pengeluaran atau permintaan rekapitulasi pengeluaran Anda.")
                
        except Exception as e:
            error_msg = f"❌ Terjadi kesalahan saat memproses permintaan:\n\n{str(e)}"
            send_telegram_message(chat_id, error_msg)
            
    return jsonify({"status": "success"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)