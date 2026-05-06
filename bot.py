import os
import zipfile
import shutil
import asyncio
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Tuple, List, Dict

from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from PIL import Image

# ========== TOKEN ==========
def get_token():
    if Path("token.txt").exists():
        return open("token.txt").read().strip()
    return os.environ.get("BOT_TOKEN")

BOT_TOKEN = get_token()
if not BOT_TOKEN:
    raise ValueError("Token topilmadi")

# ========== SOZLAMALAR ==========
BASE_DIR = Path(tempfile.gettempdir()) / "file_zip_bot"
TEMP_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
MAX_FILE_SIZE = 50 * 1024 * 1024   # 50 MB
AUTO_THRESHOLD = 3                 # 3 ta faylda avtomatik zip (agar /auto yoqilgan bo‘lsa)
API_TIMEOUT = 300

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

session = AiohttpSession(timeout=API_TIMEOUT)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"), session=session)
dp = Dispatcher()

# Foydalanuvchi seanslari: { user_id: {"files": [...], "mode": "fast"/"compress", "auto": bool} }
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

# ========== RASM / DOCX / PPTX SIQISH FUNKSIYALARI ==========
def compress_image_file(img_path: Path, quality: int = 85) -> bool:
    """Alohida rasm faylini siqish (o‘rniga yozadi)"""
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

def compress_docx_pptx(zip_path: Path, quality: int = 85) -> int:
    """DOCX/PPTX (ZIP) ichidagi rasmlarni siqadi"""
    temp_extract = BASE_DIR / f"extract_{datetime.now().timestamp()}"
    temp_extract.mkdir()
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

def compress_docx_pptx_file(src: Path, dst: Path, file_type: str) -> Tuple[bool, str, int]:
    try:
        shutil.copy2(src, dst)
        img_count = compress_docx_pptx(dst)
        orig = src.stat().st_size
        new = dst.stat().st_size
        reduction = (1 - new/orig) * 100 if orig else 0
        msg = f"✅ {file_type.upper()} siqildi! {format_size(new)} ({reduction:.1f}% kichraydi) – {img_count} rasm optimallashtirildi."
        return True, msg, new
    except Exception as e:
        return False, f"❌ Xatolik: {str(e)}", 0

def process_image_file(src: Path, dst: Path, name: str, mode: str) -> Tuple[bool, str, int]:
    """Rasmni siqish (compress) yoki nusxalash (fast)"""
    try:
        if mode == "compress":
            shutil.copy2(src, dst)
            if compress_image_file(dst, quality=85):
                size = dst.stat().st_size
                return True, f"🖼️ {name} siqildi → {format_size(size)}", size
            else:
                # agar siqilmagan bo‘lsa, asl nusxani ishlat
                dst.unlink()
                shutil.copy2(src, dst)
                size = dst.stat().st_size
                return True, f"🖼️ {name} (siqilmadi) → {format_size(size)}", size
        else:  # fast
            shutil.copy2(src, dst)
            size = dst.stat().st_size
            return True, f"🖼️ {name} → {format_size(size)}", size
    except Exception as e:
        return False, f"❌ {name}: {str(e)}", 0

def process_file_fast(src: Path, dst: Path, name: str, category: str) -> Tuple[bool, str, int]:
    try:
        shutil.copy2(src, dst)
        size = dst.stat().st_size
        return True, f"📄 {category}: {name} → {format_size(size)}", size
    except Exception as e:
        return False, f"❌ {name}: {str(e)}", 0

# ========== ZIP YARATISH ==========
def create_zip(files_data: List[Dict], zip_name: str) -> Path:
    zip_path = OUTPUT_DIR / zip_name
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in files_data:
            zf.write(item["path"], item["arcname"])
    return zip_path

def cleanup_user(user_id: int):
    if user_id in user_sessions:
        for f in user_sessions[user_id].get("files", []):
            try:
                Path(f["path"]).unlink()
            except:
                pass
        del user_sessions[user_id]

# ========== ASOSIY QADOQLASH FUNKSIYASI ==========
async def pack_files(user_id: int, message: Message):
    session_data = user_sessions.get(user_id, {"files": []})
    files = session_data.get("files", [])
    if not files:
        await message.answer("📭 Hech qanday fayl saqlanmagan.")
        return

    mode = session_data.get("mode", "fast")
    status = await message.answer(f"⏳ {len(files)} ta fayl ZIPlanmoqda (rejim: {mode})...")
    ensure_dirs()

    processed_items = []
    results = []
    total_original = sum(f["size"] for f in files)
    total_processed = 0

    for f in files:
        src = Path(f["path"])
        name = f["name"]
        category = f["type"].upper()   # PDF, DOCX, PPTX, IMAGE
        out = OUTPUT_DIR / f"{mode}_{datetime.now().timestamp()}_{name}"

        if category == "IMAGE":
            ok, msg, size = process_image_file(src, out, name, mode)
        elif category in ("DOCX", "PPTX") and mode == "compress":
            ok, msg, size = compress_docx_pptx_file(src, out, category)
        else:
            # PDF yoki fast mode yoki compress rejimida bo‘lmagan DOCX/PPTX
            ok, msg, size = process_file_fast(src, out, name, category)

        if ok:
            processed_items.append({"path": out, "arcname": f"{category}/{name}"})
            total_processed += size
            results.append(msg)
        else:
            results.append(f"❌ {name}: {msg}")

    if not processed_items:
        await status.edit_text("❌ Hech qanday fayl qayta ishlanmadi.")
        return

    zip_name = f"files_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    zip_path = create_zip(processed_items, zip_name)

    summary = "📊 Hisobot:\n" + "\n".join(results[:20])
    summary += f"\n\n📦 Asl hajmi: {format_size(total_original)}"
    summary += f"\n💾 Qayta ishlangan: {format_size(total_processed)}"
    saved = total_original - total_processed
    if total_original > 0:
        summary += f"\n📉 Tejalgan: {format_size(saved)} ({(saved/total_original)*100:.1f}%)"

    with open(zip_path, 'rb') as f:
        await message.answer_document(BufferedInputFile(f.read(), filename=zip_name), caption=summary)
    await status.delete()

    # Tozalash
    cleanup_user(user_id)
    zip_path.unlink()
    for item in processed_items:
        try:
            item["path"].unlink()
        except:
            pass

async def auto_pack(user_id: int, message: Message):
    if user_id not in user_sessions:
        return
    if not user_sessions[user_id].get("auto", False):
        return
    if len(user_sessions[user_id].get("files", [])) >= AUTO_THRESHOLD:
        await pack_files(user_id, message)

# ========== FAYL/ RASM YUKLASH ==========
async def save_file(user_id: int, file_id: str, file_name: str, file_size: int, file_type: str):
    if user_id not in user_sessions:
        user_sessions[user_id] = {"files": [], "mode": "fast", "auto": False}
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
            "timestamp": datetime.now()
        })
        return True
    except Exception as e:
        logger.exception("Yuklash xatosi")
        if file_path.exists():
            file_path.unlink()
        return False

# ========== BUYRUQLAR ==========
@dp.message(Command("start"))
async def start_cmd(msg: Message):
    await msg.answer(
        "📦 **Universal ZIP Bot**\n\n"
        "🖼️ Rasm (photo yoki document), PDF, DOCX, PPTX yuboring – ular saqlanadi.\n"
        "🔹 `/pack` – barcha saqlangan fayllarni bitta ZIP arxivga solib yuboradi.\n"
        "🔹 `/mode compress` – DOCX/PPTX va rasmlarni siqadi (hajm kamayadi, biroz sekin).\n"
        "🔹 `/mode fast` – hech qanday siqish yo‘q, tez zip.\n"
        "🔹 `/auto` – 3 ta fayldan keyin avtomatik ZIP yuborish (yoqish/o‘chirish).\n"
        "🔹 `/list`, `/clear` – fayllar ro‘yxati va tozalash.\n\n"
        "Hozirgi rejim: **fast**\n"
        "Rasmlar soni cheklanmagan, xohlagan vaqtda `/pack` bilan bitta fayl qilib oling."
    )

@dp.message(Command("mode"))
async def mode_cmd(msg: Message):
    args = msg.text.split()
    if len(args) != 2 or args[1] not in ["compress", "fast"]:
        await msg.answer("❌ Ishlating: /mode compress yoki /mode fast")
        return
    uid = msg.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = {"files": [], "mode": "fast", "auto": False}
    user_sessions[uid]["mode"] = args[1]
    await msg.answer(f"✅ Rejim o‘zgartirildi: **{args[1]}**")

@dp.message(Command("auto"))
async def auto_cmd(msg: Message):
    uid = msg.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = {"files": [], "mode": "fast", "auto": False}
    current = user_sessions[uid]["auto"]
    user_sessions[uid]["auto"] = not current
    status = "YOQILDI" if not current else "O‘CHIRILDI"
    await msg.answer(f"⚙️ Avtomatik ZIP rejimi {status}. (3 ta faylda avtomatik)")

@dp.message(Command("pack"))
async def pack_cmd(msg: Message):
    await pack_files(msg.from_user.id, msg)

@dp.message(Command("list"))
async def list_cmd(msg: Message):
    uid = msg.from_user.id
    files = user_sessions.get(uid, {}).get("files", [])
    if not files:
        await msg.answer("📭 Saqlangan fayl yo‘q.")
        return
    total = sum(f["size"] for f in files)
    text = f"📁 Saqlangan fayllar ({len(files)}):\n" + "\n".join(f"• {f['name']} ({format_size(f['size'])})" for f in files)
    text += f"\n\n📦 Umumiy hajm: {format_size(total)}"
    await msg.answer(text)

@dp.message(Command("clear"))
async def clear_cmd(msg: Message):
    cleanup_user(msg.from_user.id)
    await msg.answer("🧹 Barcha fayllar tozalandi.")

# ========== HAMMA TURDAGI FAYLLARNI QABUL QILISH ==========
@dp.message(lambda m: m.document)
async def handle_document(msg: Message):
    doc = msg.document
    if doc.file_size > MAX_FILE_SIZE:
        await msg.answer(f"❌ Fayl juda katta ({format_size(doc.file_size)}). Maksimal {format_size(MAX_FILE_SIZE)}")
        return
    ext = Path(doc.file_name).suffix.lower()
    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'):
        file_type = "IMAGE"
        name = doc.file_name
    elif ext in ('.pdf', '.docx', '.pptx'):
        file_type = ext[1:]   # pdf, docx, pptx
        name = doc.file_name
    else:
        await msg.answer(f"❌ {ext} qo‘llab-quvvatlanmaydi. Faqat PDF, DOCX, PPTX va rasm fayllari.")
        return
    success = await save_file(msg.from_user.id, doc.file_id, name, doc.file_size, file_type)
    if success:
        total = len(user_sessions[msg.from_user.id]["files"])
        await msg.answer(f"✅ {name} saqlandi. Jami: {total} ta fayl.")
        await auto_pack(msg.from_user.id, msg)
    else:
        await msg.answer("❌ Yuklashda xatolik.")

@dp.message(lambda m: m.photo)
async def handle_photo(msg: Message):
    photo = msg.photo[-1]   # eng katta rasm
    if photo.file_size > MAX_FILE_SIZE:
        await msg.answer(f"❌ Rasm juda katta ({format_size(photo.file_size)}). Maksimal {format_size(MAX_FILE_SIZE)}")
        return
    name = f"photo_{photo.file_unique_id}.jpg"
    success = await save_file(msg.from_user.id, photo.file_id, name, photo.file_size, "IMAGE")
    if success:
        total = len(user_sessions[msg.from_user.id]["files"])
        await msg.answer(f"✅ Rasm saqlandi. Jami: {total} ta fayl.")
        await auto_pack(msg.from_user.id, msg)
    else:
        await msg.answer("❌ Rasm yuklashda xatolik.")

@dp.message()
async def unknown(msg: Message):
    await msg.answer("❓ Tushunarsiz. Fayl (PDF, DOCX, PPTX) yoki rasm yuboring.")

# ========== ISHGA TUSHIRISH ==========
async def main():
    print("🚀 Bot ishga tushdi (rasmlar, PDF, DOCX, PPTX qabul qilinadi).")
    ensure_dirs()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
