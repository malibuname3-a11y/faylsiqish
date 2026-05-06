import os
import zipfile
import shutil
import asyncio
import logging
import tempfile
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Tuple, List, Dict

from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from PIL import Image
from pypdf import PdfWriter, PdfReader

# ========== TOKENNI AVTOMATIK TOPISH ==========
def get_token() -> str:
    token = os.environ.get("BOT_TOKEN")
    if token:
        return token
    env_file = Path(".env")
    if env_file.exists():
        with open(env_file, "r") as f:
            for line in f:
                if line.startswith("BOT_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    token_file = Path("token.txt")
    if token_file.exists():
        with open(token_file, "r") as f:
            return f.read().strip()
    raise ValueError("❌ Token topilmadi! token.txt yoki .env fayl yarating.")

# ========== SOZLAMALAR ==========
BOT_TOKEN = get_token()
BASE_TEMP_DIR = Path(tempfile.gettempdir()) / "file_compressor_bot"
TEMP_DIR = BASE_TEMP_DIR / "uploads"       # Yuklangan fayllar
OUTPUT_DIR = BASE_TEMP_DIR / "compressed"  # Qayta ishlangan fayllar
MAX_FILE_SIZE = 50 * 1024 * 1024           # 50 MB
CLEANUP_HOURS = 24
API_TIMEOUT = 120
AUTO_PACK_THRESHOLD = 3                    # 3 ta faylda avtomatik ishlov

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

session = AiohttpSession(timeout=API_TIMEOUT)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"), session=session)
dp = Dispatcher()

# Foydalanuvchi seanslari: { user_id: {"files": [...], "auto_mode": bool} }
user_sessions: Dict[int, Dict] = {}

# ========== YORDAMCHI FUNKSIYALAR ==========
def ensure_dirs():
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def format_size(size: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} GB"

def compress_images_in_zip(zip_path: Path, quality: int = 85) -> int:
    """ZIP ichidagi rasmlarni siqish (DOCX/PPTX uchun)"""
    temp_extract = BASE_TEMP_DIR / f"extract_{datetime.now().timestamp()}"
    temp_extract.mkdir()
    count = 0
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(temp_extract)
        for img_path in temp_extract.rglob("*"):
            if img_path.suffix.lower() in ('.jpg', '.jpeg', '.png', '.bmp', '.gif'):
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
                        count += 1
                except Exception as e:
                    logger.warning(f"Rasm siqish xatosi: {e}")
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

def compress_docx_pptx(input_path: Path, output_path: Path, file_type: str) -> Tuple[bool, str, int]:
    """DOCX yoki PPTX ni siqish"""
    try:
        shutil.copy2(input_path, output_path)
        img_count = compress_images_in_zip(output_path)
        orig = input_path.stat().st_size
        new = output_path.stat().st_size
        reduction = (1 - new/orig) * 100 if orig else 0
        msg = f"✅ {file_type.upper()} siqildi! {format_size(new)} ({reduction:.1f}% kichraydi)\n📸 {img_count} ta rasm optimallashtirildi."
        return True, msg, new
    except Exception as e:
        return False, f"❌ Xatolik: {str(e)}", 0

def merge_pdfs(pdf_paths: List[Path], output_path: Path) -> Tuple[bool, str, int]:
    """Bir nechta PDF fayllarni bitta PDF ga birlashtiradi"""
    if not pdf_paths:
        return False, "Hech qanday PDF fayl yo'q", 0
    if len(pdf_paths) == 1:
        # Faqat bitta PDF bo'lsa, nusxalash kifoya
        shutil.copy2(pdf_paths[0], output_path)
        size = output_path.stat().st_size
        return True, f"📄 Bitta PDF (siqilmagan)", size
    try:
        merger = PdfWriter()
        for path in pdf_paths:
            reader = PdfReader(path)
            for page in reader.pages:
                merger.add_page(page)
        merger.write(output_path)
        merger.close()
        size = output_path.stat().st_size
        return True, f"🔗 {len(pdf_paths)} ta PDF birlashtirildi → {format_size(size)}", size
    except Exception as e:
        return False, f"❌ PDF birlashtirish xatosi: {str(e)}", 0

def create_categorized_zip(files_data: List[Dict], zip_name: str) -> Path:
    """files_data: [{"path": Path, "category": str, "arcname": str}]"""
    zip_path = OUTPUT_DIR / zip_name
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in files_data:
            zf.write(item["path"], item["arcname"])
    return zip_path

def cleanup_user_session(user_id: int):
    if user_id in user_sessions:
        for f in user_sessions[user_id].get("files", []):
            try:
                Path(f["path"]).unlink()
            except:
                pass
        del user_sessions[user_id]

def cleanup_old_files():
    now = datetime.now().timestamp()
    for d in [BASE_TEMP_DIR, OUTPUT_DIR, TEMP_DIR]:
        if d.exists():
            for f in d.iterdir():
                if f.is_file() and now - f.stat().st_mtime > CLEANUP_HOURS * 3600:
                    try:
                        f.unlink()
                    except:
                        pass

# ========== ASOSIY ISHLOV BERISH FUNKSIYASI ==========
async def process_and_pack(user_id: int, message: Message):
    """Foydalanuvchining barcha fayllarini qayta ishlaydi (PDF birlashtirish, DOCX/PPTX siqish) va ZIP yuboradi"""
    session_data = user_sessions.get(user_id, {"files": []})
    files = session_data.get("files", [])
    if not files:
        await message.answer("📭 Hech qanday fayl saqlanmagan.")
        return

    status_msg = await message.answer(f"⏳ {len(files)} ta fayl qayta ishlanmoqda...")
    ensure_dirs()

    # Fayllarni turlari bo‘yicha ajratish
    pdf_files = []
    docx_files = []
    pptx_files = []
    for f in files:
        orig_path = Path(f["path"])
        ftype = f["type"]
        if ftype == "pdf":
            pdf_files.append(orig_path)
        elif ftype == "docx":
            docx_files.append((orig_path, f["name"]))
        elif ftype == "pptx":
            pptx_files.append((orig_path, f["name"]))

    compressed_items = []  # ZIP ichiga qo‘shiladigan elementlar
    results = []
    total_original = sum(f["size"] for f in files)
    total_processed = 0

    # 1. PDF larni birlashtirish
    if pdf_files:
        merged_pdf_path = OUTPUT_DIR / f"merged_pdfs_{datetime.now().timestamp()}.pdf"
        ok, msg, size = merge_pdfs(pdf_files, merged_pdf_path)
        if ok:
            compressed_items.append({
                "path": merged_pdf_path,
                "category": "PDF",
                "arcname": f"PDF/merged_documents.pdf"
            })
            total_processed += size
            results.append(f"📚 PDF: {msg} → {format_size(size)}")
        else:
            results.append(f"❌ PDF xatosi: {msg}")
    else:
        results.append("📭 PDF fayllar topilmadi.")

    # 2. DOCX larni siqish
    if docx_files:
        for doc_path, orig_name in docx_files:
            compressed_path = OUTPUT_DIR / f"compressed_{orig_name}"
            ok, msg, size = compress_docx_pptx(doc_path, compressed_path, "docx")
            if ok:
                compressed_items.append({
                    "path": compressed_path,
                    "category": "DOCX",
                    "arcname": f"DOCX/{orig_name}"
                })
                total_processed += size
                results.append(f"{msg}")
            else:
                results.append(f"❌ {orig_name}: {msg}")
    else:
        results.append("📭 DOCX fayllar topilmadi.")

    # 3. PPTX larni siqish
    if pptx_files:
        for ppt_path, orig_name in pptx_files:
            compressed_path = OUTPUT_DIR / f"compressed_{orig_name}"
            ok, msg, size = compress_docx_pptx(ppt_path, compressed_path, "pptx")
            if ok:
                compressed_items.append({
                    "path": compressed_path,
                    "category": "PPTX",
                    "arcname": f"PPTX/{orig_name}"
                })
                total_processed += size
                results.append(f"{msg}")
            else:
                results.append(f"❌ {orig_name}: {msg}")
    else:
        results.append("📭 PPTX fayllar topilmadi.")

    if not compressed_items:
        await status_msg.edit_text("❌ Hech qanday fayl qayta ishlanmadi.")
        return

    # Bitta ZIP arxiv yaratish
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"processed_files_{timestamp}.zip"
    zip_path = create_categorized_zip(compressed_items, zip_name)

    # Hisobot
    summary = "📊 <b>Hisobot:</b>\n" + "\n".join(results)
    summary += f"\n\n📦 Original hajm: {format_size(total_original)}"
    summary += f"\n💾 Qayta ishlangan hajm: {format_size(total_processed)}"
    saved = total_original - total_processed
    if total_original > 0:
        percent = (saved / total_original) * 100
        summary += f"\n📉 Tejalgan: {format_size(saved)} ({percent:.1f}%)"

    with open(zip_path, 'rb') as f:
        await message.answer_document(
            BufferedInputFile(f.read(), filename=zip_name),
            caption=summary
        )
    await status_msg.delete()

    # Tozalash
    cleanup_user_session(user_id)
    if zip_path.exists():
        zip_path.unlink()
    for item in compressed_items:
        try:
            item["path"].unlink()
        except:
            pass

async def auto_pack_if_needed(user_id: int, message: Message):
    if user_id not in user_sessions:
        return
    if not user_sessions[user_id].get("auto_mode", False):
        return
    if len(user_sessions[user_id].get("files", [])) >= AUTO_PACK_THRESHOLD:
        await process_and_pack(user_id, message)

# ========== BOT HANDLERLARI ==========
@dp.message(Command("start"))
async def start_cmd(message: Message):
    text = f"""
<b>📎 Universal File Processor Bot</b>

<b>Qanday ishlaydi:</b>
• PDF → siqilmaydi, <b>bir nechta PDF birlashtiriladi</b> (merge) → bitta PDF
• DOCX / PPTX → ichidagi rasmlar siqilib, hajmi kichrayadi
• Barcha qayta ishlangan fayllar <b>bitta ZIP</b> arxivda kategoriyalarga ajratiladi

<b>🔧 Buyruqlar:</b>
• Fayl yuboring (PDF, DOCX, PPTX)
• <b>/auto</b> – avtomatik rejim yoqilganda, {AUTO_PACK_THRESHOLD} ta fayldan so‘ng darhol ishlov beriladi
• <b>/pack</b> – saqlangan fayllarni qo‘lda ishlash
• <b>/list</b> – saqlangan fayllar ro‘yxati
• <b>/clear</b> – saqlangan fayllarni tozalash
• <b>/stats</b> – statistika


"""
    await message.answer(text)

@dp.message(Command("auto"))
async def auto_cmd(message: Message):
    user_id = message.from_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = {"files": [], "auto_mode": False}
    current = user_sessions[user_id]["auto_mode"]
    user_sessions[user_id]["auto_mode"] = not current
    status = "✅ YOQILDI" if not current else "❌ O‘CHIRILDI"
    await message.answer(f"⚙️ Avtomatik ishlov rejimi {status}.\nAgar yoqilgan bo‘lsa, {AUTO_PACK_THRESHOLD} ta fayl yuborilganda darhol bitta ZIP qilib yuboriladi.")

@dp.message(Command("list"))
async def list_cmd(message: Message):
    user_id = message.from_user.id
    session_data = user_sessions.get(user_id, {"files": []})
    files = session_data.get("files", [])
    if not files:
        await message.answer("📭 Hech qanday fayl saqlanmagan.")
        return
    total_size = sum(f["size"] for f in files)
    lines = [f"📁 <b>Saqlangan fayllar ({len(files)} ta):</b>"]
    for idx, f in enumerate(files, 1):
        lines.append(f"{idx}. {f['name']} ({format_size(f['size'])}) - {f['type'].upper()}")
    lines.append(f"\n📦 Umumiy hajm: {format_size(total_size)}")
    lines.append("\n🎯 /pack – qayta ishlash va ZIP olish")
    await message.answer("\n".join(lines))

@dp.message(Command("clear"))
async def clear_cmd(message: Message):
    user_id = message.from_user.id
    if user_id in user_sessions:
        cleanup_user_session(user_id)
        await message.answer("🧹 Barcha saqlangan fayllar tozalandi.")
    else:
        await message.answer("📭 Hech narsa yo‘q.")

@dp.message(Command("pack"))
async def pack_cmd(message: Message):
    user_id = message.from_user.id
    await process_and_pack(user_id, message)

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    ensure_dirs()
    cleanup_old_files()
    temp_cnt = sum(1 for _ in TEMP_DIR.iterdir() if _.is_file())
    out_cnt = sum(1 for _ in OUTPUT_DIR.iterdir() if _.is_file())
    temp_sz = sum(f.stat().st_size for f in TEMP_DIR.iterdir() if f.is_file())
    out_sz = sum(f.stat().st_size for f in OUTPUT_DIR.iterdir() if f.is_file())
    await message.answer(f"""
📊 <b>Statistika</b>
⏳ Yuklangan fayllar: {temp_cnt}
📦 Qayta ishlangan fayllar: {out_cnt}
💾 Vaqtinchalik hajm: {format_size(temp_sz)}
💿 Saqlangan hajm: {format_size(out_sz)}
    """)

@dp.message(lambda m: m.document)
async def handle_document(message: Message):
    doc = message.document
    file_name = doc.file_name
    file_size = doc.file_size
    ext = Path(file_name).suffix.lower()

    if ext not in ['.pdf', '.docx', '.pptx']:
        await message.answer(f"❌ {ext} format qo'llab-quvvatlanmaydi. Faqat PDF, DOCX, PPTX.")
        return
    if file_size > MAX_FILE_SIZE:
        await message.answer(f"❌ Fayl juda katta! Maksimal {format_size(MAX_FILE_SIZE)}.")
        return

    file_type = ext[1:]  # pdf, docx, pptx
    ensure_dirs()
    user_id = message.from_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = {"files": [], "auto_mode": False}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = f"{timestamp}_{user_id}_{file_name}"
    file_path = TEMP_DIR / safe_name

    try:
        file = await bot.get_file(doc.file_id)
        await bot.download_file(file.file_path, file_path)

        user_sessions[user_id]["files"].append({
            "path": str(file_path),
            "name": file_name,
            "type": file_type,
            "size": file_size,
            "timestamp": datetime.now()
        })
        total = len(user_sessions[user_id]["files"])
        auto_mode = user_sessions[user_id]["auto_mode"]
        msg = f"✅ <b>{file_name}</b> saqlandi.\n📎 Jami: {total} ta fayl.\n"
        if auto_mode:
            msg += f"⚙️ Avtomatik rejim yoqilgan. {AUTO_PACK_THRESHOLD} ta fayl to‘plansa avtomatik ishlanadi."
        else:
            msg += "🎯 /pack – qayta ishlash va ZIP olish"
        await message.answer(msg)
        await auto_pack_if_needed(user_id, message)
    except Exception as e:
        logger.exception("Yuklash xatosi")
        await message.answer(f"❌ Yuklashda xatolik: {str(e)[:100]}")
        if file_path.exists():
            file_path.unlink()

@dp.message()
async def unknown(message: Message):
    await message.answer("❓ Tushunarsiz. /help yordam beradi. Fayl yuboring (PDF, DOCX, PPTX)")

# ========== ISHGA TUSHIRISH ==========
async def on_shutdown():
    logger.info("Bot yopilmoqda...")
    await session.close()

async def shutdown(sig):
    logger.info(f"{sig} signal qabul qilindi. To‘xtatilmoqda...")
    await bot.session.close()
    await dp.stop_polling()
    sys.exit(0)

async def main():
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(sig)))

    print("="*50)
    print("🤮 Universal File Processor Bot (PDF merge + DOCX/PPTX compress)")
    print(f"✅ Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:] if len(BOT_TOKEN)>15 else ''}")
    ensure_dirs()
    cleanup_old_files()
    print(f"📁 Yuklangan fayllar: {TEMP_DIR.absolute()}")
    print(f"📁 Qayta ishlangan fayllar: {OUTPUT_DIR.absolute()}")
    print(f"⚙️ Avtomatik ishlov chegarasi: {AUTO_PACK_THRESHOLD} ta fayl")
    print("="*50)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await on_shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot to‘xtatildi.")
    except Exception as e:
        logger.exception(f"Kutilmagan xatolik: {e}")
