import os
import zipfile
import shutil
import asyncio
import logging
import tempfile
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Tuple, List, Dict, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from PIL import Image
import img2pdf
from pypdf import PdfWriter, PdfReader

# ========== TOKEN ==========
def get_token():
    if Path("token.txt").exists():
        return open("token.txt").read().strip()
    return os.environ.get("BOT_TOKEN")

BOT_TOKEN = get_token()
if not BOT_TOKEN:
    raise ValueError("Token topilmadi")

# ========== SOZLAMALAR ==========
BASE_DIR = Path(tempfile.gettempdir()) / "full_bot_final"
TEMP_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
MAX_FILE_SIZE = 50 * 1024 * 1024
AUTO_THRESHOLD = 3
API_TIMEOUT = 300
DEFAULT_QUALITY = 85
DEFAULT_LANG = "uz"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

session = AiohttpSession(timeout=API_TIMEOUT)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"), session=session)
dp = Dispatcher()

# ========== TILLAR ==========
TEXTS = {
    "uz": {
        "start": "📦 <b>To‘liq funksiyali bot</b>\n\n• PDF, DOCX, PPTX va rasmlarni qabul qilaman.\n• Rasmlar → bitta PDF\n• DOCX/PPTX ichidagi rasmlar siqilishi mumkin.\n• Natija – ZIP yoki bitta PDF.\n\n🔹 /mode compress|fast\n🔹 /format zip|pdf\n🔹 /auto – 3 ta faylda avtomatik ishlov\n🔹 /pack – qo‘lda ishlov\n🔹 /list, /clear, /stats\n🔹 /quality 1-100\n🔹 /remove &lt;n&gt;\n🔹 /sort name|date|size\n🔹 /password &lt;parol&gt;\n🔹 /lang uz|en|ru\n🔹 /convert docx pdf  (va boshqalar)\n\nHozirgi rejim: fast, format: zip, sifat: 85",
        "file_saved": "✅ {} saqlandi. Jami: {} ta fayl.",
        "no_files": "📭 Saqlangan fayl yo‘q.",
        "processing": "⏳ {} ta fayl qayta ishlanmoqda (rejim: {}, format: {})...",
        "error": "❌ Xatolik: {}",
        "cleared": "🧹 Barcha fayllar tozalandi.",
        "mode_changed": "✅ Rejim o‘zgartirildi: <b>{}</b>",
        "auto_on": "⚙️ Avtomatik rejim YOQILDI ({} ta faylda)",
        "auto_off": "⚙️ Avtomatik rejim O‘CHIRILDI",
        "format_changed": "✅ Chiqish formati <b>{}</b> ga o‘zgartirildi.",
        "quality_changed": "✅ Rasm siqish sifati <b>{}</b> ga o‘zgartirildi.",
        "password_set": "✅ PDF paroli o‘rnatildi.",
        "lang_changed": "✅ Til <b>{}</b> ga o‘zgartirildi.",
        "remove_usage": "❌ Ishlating: /remove &lt;raqam&gt; (1 dan {}) gacha",
        "removed": "✅ {} o‘chirildi. Qolgan: {} ta fayl.",
        "sort_usage": "❌ Ishlating: /sort name|date|size",
        "sorted": "✅ Fayllar {} bo‘yicha saralandi.",
        "list_header": "📁 Saqlangan fayllar ({} ta):",
        "list_total": "\n📦 Umumiy hajm: {}",
        "stats": "📊 Statistika:\n⏳ Yuklangan: {} fayl, {}\n📦 Qayta ishlangan: {} fayl, {}",
        "progress": "⏳ Qayta ishlash: {:.1f}%",
        "only_images_pdf": "⚠️ Bitta PDF formati faqat rasm va PDF fayllar qatnashganda ishlaydi. DOCX/PPTX bo‘lsa ZIP qilinadi.",
        "convert_usage": "❌ Ishlating: /convert &lt;from&gt; &lt;to&gt;\nMumkin: docx pdf, pptx pdf, pdf jpg, jpg pdf",
        "convert_start": "⏳ {} ta fayl konvertatsiya qilinmoqda ({} → {})...",
        "convert_success": "✅ {} → {} konvertatsiya bajarildi. {} ta yangi fayl qo‘shildi.",
        "libreoffice_missing": "❌ LibreOffice topilmadi! Iltimos uni o‘rnating.",
        "pdf2image_missing": "❌ PDF→JPG uchun pdf2image kerak."
    },
    "en": {
        "start": "📦 <b>Full-featured bot</b>\n\n• I accept PDF, DOCX, PPTX and images.\n• Images → single PDF\n• DOCX/PPTX can compress internal images.\n• Output – ZIP or single PDF.\n\n🔹 /mode compress|fast\n🔹 /format zip|pdf\n🔹 /auto – auto after 3 files\n🔹 /pack – manual\n🔹 /list, /clear, /stats\n🔹 /quality 1-100\n🔹 /remove &lt;n&gt;\n🔹 /sort name|date|size\n🔹 /password &lt;password&gt;\n🔹 /lang uz|en|ru\n🔹 /convert docx pdf\n\nCurrent mode: fast, format: zip, quality: 85",
        "file_saved": "✅ {} saved. Total: {} files.",
        "no_files": "📭 No files saved.",
        "processing": "⏳ Processing {} files (mode: {}, format: {})...",
        "error": "❌ Error: {}",
        "cleared": "🧹 All files cleared.",
        "mode_changed": "✅ Mode changed to: <b>{}</b>",
        "auto_on": "⚙️ Auto mode ENABLED (after {} files)",
        "auto_off": "⚙️ Auto mode DISABLED",
        "format_changed": "✅ Output format changed to <b>{}</b>.",
        "quality_changed": "✅ Image quality set to <b>{}</b>.",
        "password_set": "✅ PDF password set.",
        "lang_changed": "✅ Language changed to <b>{}</b>.",
        "remove_usage": "❌ Usage: /remove &lt;number&gt; (1 to {})",
        "removed": "✅ {} removed. Remaining: {} files.",
        "sort_usage": "❌ Usage: /sort name|date|size",
        "sorted": "✅ Files sorted by {}.",
        "list_header": "📁 Saved files ({}):",
        "list_total": "\n📦 Total size: {}",
        "stats": "📊 Statistics:\n⏳ Uploaded: {} files, {}\n📦 Processed: {} files, {}",
        "progress": "⏳ Progress: {:.1f}%",
        "only_images_pdf": "⚠️ Single PDF format works only with images and PDFs. DOCX/PPTX will be zipped.",
        "convert_usage": "❌ Usage: /convert &lt;from&gt; &lt;to&gt;\nPossible: docx pdf, pptx pdf, pdf jpg, jpg pdf",
        "convert_start": "⏳ Converting {} files ({} → {})...",
        "convert_success": "✅ {} → {} conversion done. {} new files added.",
        "libreoffice_missing": "❌ LibreOffice not found! Please install it.",
        "pdf2image_missing": "❌ PDF→JPG requires pdf2image."
    }
}

def get_text(user_id: int, key: str, *args) -> str:
    user = user_sessions.get(user_id)
    lang = DEFAULT_LANG
    if user and "lang" in user:
        lang = user["lang"]
    text = TEXTS.get(lang, TEXTS["uz"]).get(key, TEXTS["uz"][key])
    return text.format(*args) if args else text

# ========== USER DATA ==========
user_sessions: Dict[int, Dict] = {}

def ensure_dirs():
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def format_size(size: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} GB"

# ========== RASM SIQISH ==========
def compress_image_file(img_path: Path, quality: int) -> bool:
    try:
        with Image.open(img_path) as img:
            if img.mode in ('RGBA', 'LA', 'P'):
                rgb = Image.new('RGB', img.size, (255,255,255))
                if img.mode == 'RGBA':
                    rgb.paste(img, mask=img.split()[-1])
                else:
                    rgb.paste(img)
                img = rgb
            img.save(img_path, optimize=True, quality=quality)
            return True
    except Exception as e:
        logger.warning(f"Rasm siqish xatosi {img_path}: {e}")
        return False

def compress_docx_pptx(zip_path: Path, quality: int) -> int:
    temp_extract = BASE_DIR / f"extract_{datetime.now().timestamp()}"
    temp_extract.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(temp_extract)
        for img_path in temp_extract.rglob("*"):
            if img_path.suffix.lower() in ('.jpg', '.jpeg', '.png', '.bmp', '.gif'):
                if compress_image_file(img_path, quality):
                    count += 1
        new_zip = zip_path.with_name(f"compressed_{zip_path.name}")
        with zipfile.ZipFile(new_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in temp_extract.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(temp_extract))
        zip_path.unlink()
        new_zip.rename(zip_path)
    finally:
        shutil.rmtree(temp_extract, ignore_errors=True)
    return count

def compress_docx_pptx_file(src: Path, dst: Path, file_type: str, mode: str, quality: int) -> Tuple[bool, str, int]:
    try:
        shutil.copy2(src, dst)
        if mode == "compress":
            img_count = compress_docx_pptx(dst, quality)
            orig = src.stat().st_size
            new = dst.stat().st_size
            reduction = (1 - new/orig) * 100 if orig else 0
            msg = f"✅ {file_type.upper()} siqildi! {format_size(new)} ({reduction:.1f}% kichraydi) – {img_count} rasm optimallashtirildi."
            return True, msg, new
        else:
            size = dst.stat().st_size
            msg = f"📄 {file_type.upper()}: {src.name} → {format_size(size)} (o‘zgarishsiz)"
            return True, msg, size
    except Exception as e:
        return False, f"❌ Xatolik: {str(e)}", 0

def copy_pdf(src: Path, dst: Path, name: str) -> Tuple[bool, str, int]:
    try:
        shutil.copy2(src, dst)
        size = dst.stat().st_size
        return True, f"📄 PDF: {name} → {format_size(size)}", size
    except Exception as e:
        return False, f"❌ {name}: {str(e)}", 0

def images_to_pdf(image_paths: List[Path], output_pdf: Path, quality: int) -> bool:
    temp_imgs = []
    for idx, img_path in enumerate(image_paths):
        temp_img = output_pdf.parent / f"temp_{idx}_{img_path.name}"
        shutil.copy2(img_path, temp_img)
        if quality < 100:
            compress_image_file(temp_img, quality)
        temp_imgs.append(temp_img)
    try:
        with open(output_pdf, "wb") as f:
            f.write(img2pdf.convert([str(p) for p in temp_imgs]))
        return True
    except Exception as e:
        logger.error(f"Rasmlarni PDF ga birlashtirish xatosi: {e}")
        return False
    finally:
        for tmp in temp_imgs:
            tmp.unlink()

def create_zip(files_data: List[Dict], zip_name: str) -> Path:
    zip_path = OUTPUT_DIR / zip_name
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in files_data:
            zf.write(item["path"], item["arcname"])
    return zip_path

def create_single_pdf(files_data: List[Dict], output_pdf: Path, password: Optional[str] = None) -> bool:
    writer = PdfWriter()
    for item in files_data:
        if item["type"] == "pdf":
            reader = PdfReader(item["path"])
            for page in reader.pages:
                writer.add_page(page)
        elif item["type"] == "image":
            img_pdf = item["path"].parent / f"temp_img_{datetime.now().timestamp()}.pdf"
            if images_to_pdf([item["path"]], img_pdf, 85):
                reader = PdfReader(img_pdf)
                for page in reader.pages:
                    writer.add_page(page)
                img_pdf.unlink()
    if password:
        writer.encrypt(password)
    with open(output_pdf, "wb") as f:
        writer.write(f)
    return True

def cleanup_user(user_id: int):
    if user_id in user_sessions:
        for f in user_sessions[user_id].get("files", []):
            try:
                Path(f["path"]).unlink()
            except:
                pass
        del user_sessions[user_id]

# ========== ASOSIY ISHLOV ==========
async def process_files(user_id: int, message: Message):
    session = user_sessions.get(user_id, {"files": []})
    files = session.get("files", [])
    if not files:
        await message.answer(get_text(user_id, "no_files"))
        return

    mode = session.get("mode", "fast")
    fmt = session.get("format", "zip")
    quality = session.get("quality", DEFAULT_QUALITY)
    password = session.get("password", None)
    sort_by = session.get("sort_by", "date")
    
    if sort_by == "name":
        files.sort(key=lambda x: x["name"])
    elif sort_by == "size":
        files.sort(key=lambda x: x["size"])
    else:
        files.sort(key=lambda x: x.get("timestamp", 0))

    msg_text = get_text(user_id, "processing", len(files), mode, fmt)
    status = await message.answer(msg_text)
    ensure_dirs()

    pdf_list = []
    docx_list = []
    pptx_list = []
    image_list = []
    for f in files:
        src = Path(f["path"])
        name = f["name"]
        typ = f["type"]
        if typ == "pdf":
            pdf_list.append({"src": src, "name": name})
        elif typ == "docx":
            docx_list.append({"src": src, "name": name})
        elif typ == "pptx":
            pptx_list.append({"src": src, "name": name})
        elif typ == "image":
            image_list.append({"src": src, "name": name})

    total_original = sum(f["size"] for f in files)
    total_processed = 0
    results = []

    if fmt == "pdf" and not docx_list and not pptx_list:
        combined_pdf = OUTPUT_DIR / f"merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        all_items = []
        for p in pdf_list:
            all_items.append({"path": p["src"], "type": "pdf"})
        for img in image_list:
            all_items.append({"path": img["src"], "type": "image"})
        success = create_single_pdf(all_items, combined_pdf, password)
        if success:
            total_processed = combined_pdf.stat().st_size
            results.append(f"✅ Bitta PDF yaratildi: {format_size(total_processed)}")
            with open(combined_pdf, "rb") as f:
                await message.answer_document(
                    BufferedInputFile(f.read(), filename=combined_pdf.name),
                    caption=get_text(user_id, "list_total", format_size(total_original)) + f"\n💾 PDF hajmi: {format_size(total_processed)}"
                )
            await status.delete()
            cleanup_user(user_id)
            combined_pdf.unlink()
            return
        else:
            await status.edit_text(get_text(user_id, "error", "PDF yaratilmadi"))
            return

    processed_items = []
    total_steps = len(pdf_list) + len(docx_list) + len(pptx_list) + (1 if image_list else 0)
    step = 0

    if image_list:
        combined_pdf = OUTPUT_DIR / f"combined_images_{datetime.now().timestamp()}.pdf"
        img_paths = [img["src"] for img in image_list]
        if images_to_pdf(img_paths, combined_pdf, quality):
            size = combined_pdf.stat().st_size
            processed_items.append({"path": combined_pdf, "arcname": "IMAGES/combined_images.pdf"})
            total_processed += size
            results.append(f"🖼️ {len(image_list)} ta rasm → bitta PDF ({format_size(size)})")
        else:
            results.append(f"❌ {len(image_list)} ta rasmni birlashtirib bo‘lmadi")
        step += 1
        await status.edit_text(get_text(user_id, "progress", step/total_steps*100))

    for p in pdf_list:
        dst = OUTPUT_DIR / f"pdf_{datetime.now().timestamp()}_{p['name']}"
        ok, msg, sz = copy_pdf(p["src"], dst, p["name"])
        if ok:
            processed_items.append({"path": dst, "arcname": f"PDF/{p['name']}"})
            total_processed += sz
            results.append(msg)
        else:
            results.append(msg)
        step += 1
        await status.edit_text(get_text(user_id, "progress", step/total_steps*100))

    for d in docx_list:
        dst = OUTPUT_DIR / f"docx_{datetime.now().timestamp()}_{d['name']}"
        ok, msg, sz = compress_docx_pptx_file(d["src"], dst, "docx", mode, quality)
        if ok:
            processed_items.append({"path": dst, "arcname": f"DOCX/{d['name']}"})
            total_processed += sz
            results.append(msg)
        else:
            results.append(msg)
        step += 1
        await status.edit_text(get_text(user_id, "progress", step/total_steps*100))

    for p in pptx_list:
        dst = OUTPUT_DIR / f"pptx_{datetime.now().timestamp()}_{p['name']}"
        ok, msg, sz = compress_docx_pptx_file(p["src"], dst, "pptx", mode, quality)
        if ok:
            processed_items.append({"path": dst, "arcname": f"PPTX/{p['name']}"})
            total_processed += sz
            results.append(msg)
        else:
            results.append(msg)
        step += 1
        await status.edit_text(get_text(user_id, "progress", step/total_steps*100))

    if not processed_items:
        await status.edit_text(get_text(user_id, "error", "Hech narsa qayta ishlanmadi"))
        return

    zip_name = f"processed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    zip_path = create_zip(processed_items, zip_name)

    summary = "📊 Hisobot:\n" + "\n".join(results[:20])
    summary += f"\n\n📦 Asl hajmi: {format_size(total_original)}"
    summary += f"\n💾 Qayta ishlangan hajm: {format_size(total_processed)}"
    saved = total_original - total_processed
    if total_original > 0:
        summary += f"\n📉 Tejalgan: {format_size(saved)} ({(saved/total_original)*100:.1f}%)"

    with open(zip_path, 'rb') as f:
        await message.answer_document(BufferedInputFile(f.read(), filename=zip_name), caption=summary)
    await status.delete()

    cleanup_user(user_id)
    zip_path.unlink()
    for item in processed_items:
        try:
            item["path"].unlink()
        except:
            pass

async def auto_process(user_id: int, message: Message):
    if user_id not in user_sessions:
        return
    if not user_sessions[user_id].get("auto", False):
        return
    if len(user_sessions[user_id].get("files", [])) >= AUTO_THRESHOLD:
        await process_files(user_id, message)

# ========== KONVERTATSIYA (LIBREOFFICE) ==========
def find_libreoffice() -> Optional[str]:
    possible = ["soffice", "libreoffice"]
    for name in possible:
        path = shutil.which(name)
        if path:
            return path
    # Windows
    windows_paths = [
        "C:\\Program Files\\LibreOffice\\program\\soffice.exe",
        "C:\\Program Files (x86)\\LibreOffice\\program\\soffice.exe"
    ]
    for p in windows_paths:
        if Path(p).exists():
            return p
    return None

def convert_with_libreoffice(src: Path, dst: Path) -> bool:
    lo = find_libreoffice()
    if not lo:
        return False
    cmd = [lo, "--headless", "--convert-to", dst.suffix[1:], "--outdir", str(dst.parent), str(src)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
        converted = dst.parent / f"{src.stem}.{dst.suffix[1:]}"
        if converted.exists():
            shutil.move(str(converted), str(dst))
            return True
        return False
    except Exception as e:
        logger.error(f"LibreOffice xatosi: {e}")
        return False

def convert_pdf_to_images(pdf_path: Path, output_folder: Path, base_name: str) -> List[Path]:
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(pdf_path, dpi=150)
        out_paths = []
        for i, img in enumerate(images):
            out_path = output_folder / f"{base_name}_page{i+1}.jpg"
            img.save(out_path, "JPEG", quality=85)
            out_paths.append(out_path)
        return out_paths
    except ImportError:
        raise Exception("pdf2image o‘rnatilmagan")
    except Exception as e:
        raise e

async def convert_files(user_id: int, from_fmt: str, to_fmt: str) -> Tuple[bool, str, int]:
    session_data = user_sessions.get(user_id, {"files": []})
    files = session_data.get("files", [])
    matching = [f for f in files if Path(f["name"]).suffix.lower() == f".{from_fmt}"]
    if not matching:
        return False, get_text(user_id, "no_files"), 0
    ensure_dirs()
    new_files = []
    errors = []
    converted_count = 0
    from_fmt = from_fmt.lower()
    to_fmt = to_fmt.lower()
    if from_fmt in ("docx","pptx") and to_fmt == "pdf":
        for f in matching:
            src = Path(f["path"])
            dst = OUTPUT_DIR / f"converted_{datetime.now().timestamp()}_{src.stem}.pdf"
            if convert_with_libreoffice(src, dst):
                new_files.append({
                    "path": str(dst),
                    "name": dst.name,
                    "type": "pdf",
                    "size": dst.stat().st_size,
                    "timestamp": datetime.now().timestamp()
                })
                converted_count += 1
            else:
                errors.append(f["name"])
    elif from_fmt == "pdf" and to_fmt == "jpg":
        try:
            for f in matching:
                src = Path(f["path"])
                jpgs = convert_pdf_to_images(src, OUTPUT_DIR, src.stem)
                for jpg in jpgs:
                    new_files.append({
                        "path": str(jpg),
                        "name": jpg.name,
                        "type": "image",
                        "size": jpg.stat().st_size,
                        "timestamp": datetime.now().timestamp()
                    })
                    converted_count += 1
        except Exception as e:
            return False, str(e), 0
    elif from_fmt == "jpg" and to_fmt == "pdf":
        for f in matching:
            src = Path(f["path"])
            dst = OUTPUT_DIR / f"converted_{datetime.now().timestamp()}_{src.stem}.pdf"
            if images_to_pdf([src], dst, 85):
                new_files.append({
                    "path": str(dst),
                    "name": dst.name,
                    "type": "pdf",
                    "size": dst.stat().st_size,
                    "timestamp": datetime.now().timestamp()
                })
                converted_count += 1
            else:
                errors.append(f["name"])
    else:
        return False, get_text(user_id, "convert_usage"), 0
    if new_files:
        user_sessions[user_id]["files"].extend(new_files)
        msg = get_text(user_id, "convert_success", from_fmt.upper(), to_fmt.upper(), converted_count)
        if errors:
            msg += f"\n⚠️ Xatoliklar: {', '.join(errors)}"
        return True, msg, converted_count
    else:
        return False, get_text(user_id, "error", "Hech qanday fayl konvertatsiya qilinmadi"), 0

# ========== FAYL / RASM YUKLASH ==========
async def save_file(user_id: int, file_id: str, file_name: str, file_size: int, file_type: str) -> bool:
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "files": [],
            "mode": "fast",
            "auto": False,
            "format": "zip",
            "quality": DEFAULT_QUALITY,
            "password": None,
            "lang": DEFAULT_LANG,
            "sort_by": "date"
        }
    ensure_dirs()
    safe_name = f"{datetime.now().timestamp()}_{user_id}_{file_name}"
    file_path = TEMP_DIR / safe_name
    try:
        file = await bot.get_file(file_id)
        await bot.download_file(file.file_path, file_path)
        user_sessions[user_id]["files"].append({
            "path": str(file_path),
            "name": file_name,
            "type": file_type,
            "size": file_size,
            "timestamp": datetime.now().timestamp()
        })
        return True
    except Exception as e:
        logger.exception(f"Yuklash xatosi: {e}")
        if file_path.exists():
            file_path.unlink()
        return False

# ========== BUYRUQLAR ==========
@dp.message(Command("start"))
async def start_cmd(msg: Message):
    uid = msg.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = {
            "files": [],
            "mode": "fast",
            "auto": False,
            "format": "zip",
            "quality": DEFAULT_QUALITY,
            "password": None,
            "lang": DEFAULT_LANG,
            "sort_by": "date"
        }
    await msg.answer(get_text(uid, "start"))

@dp.message(Command("mode"))
async def mode_cmd(msg: Message):
    args = msg.text.split()
    if len(args) != 2 or args[1] not in ["compress", "fast"]:
        await msg.answer("❌ Ishlating: /mode compress yoki /mode fast")
        return
    uid = msg.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = {
            "files": [],
            "mode": "fast",
            "auto": False,
            "format": "zip",
            "quality": DEFAULT_QUALITY,
            "password": None,
            "lang": DEFAULT_LANG,
            "sort_by": "date"
        }
    user_sessions[uid]["mode"] = args[1]
    await msg.answer(get_text(uid, "mode_changed", args[1]))

@dp.message(Command("auto"))
async def auto_cmd(msg: Message):
    uid = msg.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = {
            "files": [],
            "mode": "fast",
            "auto": False,
            "format": "zip",
            "quality": DEFAULT_QUALITY,
            "password": None,
            "lang": DEFAULT_LANG,
            "sort_by": "date"
        }
    current = user_sessions[uid]["auto"]
    user_sessions[uid]["auto"] = not current
    if not current:
        await msg.answer(get_text(uid, "auto_on", AUTO_THRESHOLD))
    else:
        await msg.answer(get_text(uid, "auto_off"))

@dp.message(Command("format"))
async def format_cmd(msg: Message):
    args = msg.text.split()
    if len(args) != 2 or args[1] not in ["zip", "pdf"]:
        await msg.answer("❌ Ishlating: /format zip yoki /format pdf")
        return
    uid = msg.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = {
            "files": [],
            "mode": "fast",
            "auto": False,
            "format": "zip",
            "quality": DEFAULT_QUALITY,
            "password": None,
            "lang": DEFAULT_LANG,
            "sort_by": "date"
        }
    user_sessions[uid]["format"] = args[1]
    await msg.answer(get_text(uid, "format_changed", args[1]))

@dp.message(Command("quality"))
async def quality_cmd(msg: Message):
    args = msg.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await msg.answer("❌ Ishlating: /quality <1-100>")
        return
    q = int(args[1])
    if q < 1 or q > 100:
        await msg.answer("❌ Sifat 1 dan 100 gacha")
        return
    uid = msg.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = {
            "files": [],
            "mode": "fast",
            "auto": False,
            "format": "zip",
            "quality": DEFAULT_QUALITY,
            "password": None,
            "lang": DEFAULT_LANG,
            "sort_by": "date"
        }
    user_sessions[uid]["quality"] = q
    await msg.answer(get_text(uid, "quality_changed", q))

@dp.message(Command("password"))
async def password_cmd(msg: Message):
    args = msg.text.split()
    if len(args) != 2:
        await msg.answer("❌ Ishlating: /password <parol>")
        return
    uid = msg.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = {
            "files": [],
            "mode": "fast",
            "auto": False,
            "format": "zip",
            "quality": DEFAULT_QUALITY,
            "password": None,
            "lang": DEFAULT_LANG,
            "sort_by": "date"
        }
    user_sessions[uid]["password"] = args[1]
    await msg.answer(get_text(uid, "password_set"))

@dp.message(Command("lang"))
async def lang_cmd(msg: Message):
    args = msg.text.split()
    if len(args) != 2 or args[1] not in ["uz", "en", "ru"]:
        await msg.answer("❌ Ishlating: /lang uz|en|ru")
        return
    uid = msg.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = {
            "files": [],
            "mode": "fast",
            "auto": False,
            "format": "zip",
            "quality": DEFAULT_QUALITY,
            "password": None,
            "lang": DEFAULT_LANG,
            "sort_by": "date"
        }
    user_sessions[uid]["lang"] = args[1]
    await msg.answer(get_text(uid, "lang_changed", args[1]))

@dp.message(Command("remove"))
async def remove_cmd(msg: Message):
    args = msg.text.split()
    uid = msg.from_user.id
    if uid not in user_sessions or not user_sessions[uid].get("files"):
        await msg.answer(get_text(uid, "no_files"))
        return
    if len(args) != 2 or not args[1].isdigit():
        await msg.answer(get_text(uid, "remove_usage", len(user_sessions[uid]["files"])))
        return
    idx = int(args[1]) - 1
    files = user_sessions[uid]["files"]
    if idx < 0 or idx >= len(files):
        await msg.answer(get_text(uid, "remove_usage", len(files)))
        return
    removed = files.pop(idx)
    try:
        Path(removed["path"]).unlink()
    except:
        pass
    await msg.answer(get_text(uid, "removed", removed["name"], len(files)))

@dp.message(Command("sort"))
async def sort_cmd(msg: Message):
    args = msg.text.split()
    if len(args) != 2 or args[1] not in ["name", "date", "size"]:
        await msg.answer(get_text(msg.from_user.id, "sort_usage"))
        return
    uid = msg.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = {
            "files": [],
            "mode": "fast",
            "auto": False,
            "format": "zip",
            "quality": DEFAULT_QUALITY,
            "password": None,
            "lang": DEFAULT_LANG,
            "sort_by": "date"
        }
    user_sessions[uid]["sort_by"] = args[1]
    await msg.answer(get_text(uid, "sorted", args[1]))

@dp.message(Command("convert"))
async def convert_cmd(msg: Message):
    args = msg.text.split()
    if len(args) != 3:
        await msg.answer(get_text(msg.from_user.id, "convert_usage"))
        return
    from_fmt = args[1].lower()
    to_fmt = args[2].lower()
    allowed = {("docx","pdf"), ("pptx","pdf"), ("pdf","jpg"), ("jpg","pdf")}
    if (from_fmt, to_fmt) not in allowed:
        await msg.answer(get_text(msg.from_user.id, "convert_usage"))
        return
    uid = msg.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = {
            "files": [],
            "mode": "fast",
            "auto": False,
            "format": "zip",
            "quality": DEFAULT_QUALITY,
            "password": None,
            "lang": DEFAULT_LANG,
            "sort_by": "date"
        }
    # LibreOffice mavjudligini tekshirish (docx/pptx -> pdf)
    if from_fmt in ("docx","pptx") and to_fmt == "pdf":
        if not find_libreoffice():
            await msg.answer(get_text(uid, "libreoffice_missing"))
            return
    await msg.answer(get_text(uid, "convert_start", len(user_sessions[uid]["files"]), from_fmt.upper(), to_fmt.upper()))
    success, res_msg, count = await convert_files(uid, from_fmt, to_fmt)
    await msg.answer(res_msg)

@dp.message(Command("pack"))
async def pack_cmd(msg: Message):
    await process_files(msg.from_user.id, msg)

@dp.message(Command("list"))
async def list_cmd(msg: Message):
    uid = msg.from_user.id
    files = user_sessions.get(uid, {}).get("files", [])
    if not files:
        await msg.answer(get_text(uid, "no_files"))
        return
    total = sum(f["size"] for f in files)
    lines = [get_text(uid, "list_header", len(files))]
    for idx, f in enumerate(files, 1):
        lines.append(f"{idx}. {f['name']} ({format_size(f['size'])})")
    lines.append(get_text(uid, "list_total", format_size(total)))
    await msg.answer("\n".join(lines))

@dp.message(Command("clear"))
async def clear_cmd(msg: Message):
    cleanup_user(msg.from_user.id)
    await msg.answer(get_text(msg.from_user.id, "cleared"))

@dp.message(Command("stats"))
async def stats_cmd(msg: Message):
    ensure_dirs()
    temp_cnt = sum(1 for _ in TEMP_DIR.iterdir() if _.is_file())
    out_cnt = sum(1 for _ in OUTPUT_DIR.iterdir() if _.is_file())
    temp_sz = sum(f.stat().st_size for f in TEMP_DIR.iterdir() if f.is_file())
    out_sz = sum(f.stat().st_size for f in OUTPUT_DIR.iterdir() if f.is_file())
    uid = msg.from_user.id
    await msg.answer(get_text(uid, "stats", temp_cnt, format_size(temp_sz), out_cnt, format_size(out_sz)))

@dp.message(lambda m: m.document)
async def handle_document(msg: Message):
    doc = msg.document
    if doc.file_size > MAX_FILE_SIZE:
        await msg.answer(f"❌ Fayl juda katta ({format_size(doc.file_size)}). Maksimal {format_size(MAX_FILE_SIZE)}")
        return
    ext = Path(doc.file_name).suffix.lower()
    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'):
        file_type = "image"
        name = doc.file_name
    elif ext == '.pdf':
        file_type = "pdf"
        name = doc.file_name
    elif ext == '.docx':
        file_type = "docx"
        name = doc.file_name
    elif ext == '.pptx':
        file_type = "pptx"
        name = doc.file_name
    else:
        await msg.answer(f"❌ {ext} qo‘llab-quvvatlanmaydi. Faqat PDF, DOCX, PPTX va rasm fayllari.")
        return
    success = await save_file(msg.from_user.id, doc.file_id, name, doc.file_size, file_type)
    if success:
        total = len(user_sessions[msg.from_user.id]["files"])
        await msg.answer(get_text(msg.from_user.id, "file_saved", name, total))
        await auto_process(msg.from_user.id, msg)

@dp.message(lambda m: m.photo)
async def handle_photo(msg: Message):
    photo = msg.photo[-1]
    if photo.file_size > MAX_FILE_SIZE:
        await msg.answer(f"❌ Rasm juda katta ({format_size(photo.file_size)}). Maksimal {format_size(MAX_FILE_SIZE)}")
        return
    name = f"photo_{photo.file_unique_id}.jpg"
    success = await save_file(msg.from_user.id, photo.file_id, name, photo.file_size, "image")
    if success:
        total = len(user_sessions[msg.from_user.id]["files"])
        await msg.answer(get_text(msg.from_user.id, "file_saved", name, total))
        await auto_process(msg.from_user.id, msg)

@dp.message()
async def unknown(msg: Message):
    await msg.answer("❓ Tushunarsiz. /start bilan boshlang yoki fayl/rasm yuboring.")

async def main():
    print("🚀 Bot ishga tushdi (pptx siqish tuzatilgan).")
    ensure_dirs()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
