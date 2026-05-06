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
OUTPUT_DIR = BASE_TEMP_DIR / "compressed"  # Siqilgan fayllar
MAX_FILE_SIZE = 50 * 1024 * 1024           # 50 MB
CLEANUP_HOURS = 24
API_TIMEOUT = 120
AUTO_PACK_THRESHOLD = 3                    # 3 ta faylda avtomatik siqish

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

session = AiohttpSession(timeout=API_TIMEOUT)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"), session=session)
dp = Dispatcher()

# Foydalanuvchi seanslari
user_sessions: Dict[int, Dict] = {}   # { user_id: {"files": [...], "auto_mode": bool} }
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
    """ZIP ichidagi barcha rasmlarni siqadi (DOCX/PPTX uchun)"""
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

def create_categorized_zip(files_data: List[Dict], zip_name: str) -> Path:
    zip_path = OUTPUT_DIR / zip_name
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fdata in files_data:
            cat = fdata["category"].upper()
            arcname = f"{cat}/{fdata['original_name']}"
            zf.write(fdata["compressed_path"], arcname)
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

# ========== AVTOMATIK QADOQLASH FUNKSIYASI ==========
async def auto_pack_if_needed(user_id: int, message: Message):
    """Agar auto_mode yoqilgan bo‘lsa va fayllar soni AUTO_PACK_THRESHOLD ga yetgan bo‘lsa, avtomatik o‘rab yuboradi."""
    if user_id not in user_sessions:
        return
    session_data = user_sessions[user_id]
    if not session_data.get("auto_mode", False):
        return
    files = session_data.get("files", [])
    if len(files) >= AUTO_PACK_THRESHOLD:
        await pack_files(user_id, message)

async def pack_files(user_id: int, message: Message):
    """Bir foydalanuvchining barcha fayllarini siqib, ZIP qilib yuboradi va seansni tozalaydi."""
    session_data = user_sessions.get(user_id, {"files": []})
    files = session_data.get("files", [])
    if not files:
        await message.answer("📭 Hech qanday fayl saqlanmagan.")
        return

    status_msg = await message.answer(f"⏳ {len(files)} ta fayl siqilmoqda... Iltimos kuting.")
    ensure_dirs()

    compressed_data = []
    results = []
    total_original = 0
    total_compressed = 0

    for fdata in files:
        original_path = Path(fdata["path"])
        file_type = fdata["type"]
        original_name = fdata["name"]
        total_original += fdata["size"]

        compressed_path = OUTPUT_DIR / f"compressed_{original_name}"
        success, msg, comp_size = compress_docx_pptx(original_path, compressed_path, file_type)
        if success:
            compressed_data.append({
                "compressed_path": compressed_path,
                "category": file_type.upper(),
                "original_name": original_name
            })
            total_compressed += comp_size
            results.append(f"✅ {original_name} -> {format_size(comp_size)}")
        else:
            results.append(f"❌ {original_name}: {msg}")

    if not compressed_data:
        await status_msg.edit_text("❌ Hech qanday fayl siqilmadi.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"compressed_files_{timestamp}.zip"
    zip_path = create_categorized_zip(compressed_data, zip_name)

    summary_lines = results[:10]
    if len(results) > 10:
        summary_lines.append(f"... va {len(results)-10} ta fayl")
    summary = "📊 <b>Hisobot:</b>\n" + "\n".join(summary_lines)
    summary += f"\n\n📦 Original hajm: {format_size(total_original)}"
    summary += f"\n💾 Siqilgan hajm: {format_size(total_compressed)}"
    saved = total_original - total_compressed
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
    for item in compressed_data:
        try:
            item["compressed_path"].unlink()
        except:
            pass

# ========== BOT HANDLERLARI ==========
@dp.message(Command("start"))
async def start_cmd(message: Message):
    text = """
<b>📎 DOCX/PPTX Compressor Bot</b>

Faqat <b>DOCX va PPTX</b> fayllarni siqadi (ichidagi rasmlarni optimallashtiradi).

<b>🔧 Qanday ishlaydi:</b>
• Fayl yuboring – ular vaqtincha saqlanadi.
• <b>/list</b> – saqlangan fayllar ro‘yxati.
• <b>/pack</b> – barcha fayllarni siqib, bitta ZIP qilib yuboradi.
• <b>/auto</b> – avtomatik rejimni yoqish/o‘chirish.
  <i>Agar avtomatik rejim yoqilgan bo‘lsa, {AUTO_PACK_THRESHOLD} yoki undan ko‘p fayl yuborilganda avtomatik ZIPlanadi.</i>
• <b>/clear</b> – saqlangan fayllarni tozalash.

<b>❗ Eslatma:</b> PDF fayllar qo‘llab-quvvatlanmaydi.
"""
    await message.answer(text.format(AUTO_PACK_THRESHOLD=AUTO_PACK_THRESHOLD))

@dp.message(Command("help"))
async def help_cmd(message: Message):
    text = f"""
<b>🤖 Yordam</b>

• <b>Fayl yuboring</b> – faqat DOCX va PPTX.
• <b>/list</b> – yuborgan fayllaringiz.
• <b>/pack</b> – barcha fayllarni siqib, ZIP arxiv qilib yuboradi.
• <b>/auto</b> – avtomatik siqish rejimi.
  Yoqilgan bo‘lsa, {AUTO_PACK_THRESHOLD} ta fayl yuborilganda darhol ZIPlab yuboriladi.
• <b>/clear</b> – barcha saqlangan fayllarni o‘chiradi.
• <b>/stats</b> – bot statistikasi.

Hech qanday qo‘shimcha dastur (Ghostscript) kerak emas.
"""
    await message.answer(text)

@dp.message(Command("auto"))
async def auto_cmd(message: Message):
    user_id = message.from_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = {"files": [], "auto_mode": False}
    current = user_sessions[user_id].get("auto_mode", False)
    user_sessions[user_id]["auto_mode"] = not current
    status = "✅ YOQILDI" if not current else "❌ O‘CHIRILDI"
    await message.answer(f"Avtomatik siqish rejimi {status}.\nAgar yoqilgan bo‘lsa, {AUTO_PACK_THRESHOLD} ta fayl yuborilganda avtomatik ZIPlanadi.")

@dp.message(Command("list"))
async def list_cmd(message: Message):
    user_id = message.from_user.id
    session_data = user_sessions.get(user_id, {"files": []})
    files = session_data.get("files", [])
    if not files:
        await message.answer("📭 Hech qanday fayl saqlanmagan. Avval DOCX yoki PPTX fayl yuboring.")
        return
    total_size = sum(f["size"] for f in files)
    lines = [f"📁 <b>Saqlangan fayllar ({len(files)} ta):</b>"]
    for idx, f in enumerate(files, 1):
        lines.append(f"{idx}. {f['name']} ({format_size(f['size'])}) - {f['type'].upper()}")
    lines.append(f"\n📦 Umumiy hajm: {format_size(total_size)}")
    lines.append("\n🎯 /pack – siqish va ZIP olish")
    await message.answer("\n".join(lines))

@dp.message(Command("clear"))
async def clear_cmd(message: Message):
    user_id = message.from_user.id
    if user_id in user_sessions:
        cleanup_user_session(user_id)
        await message.answer("🧹 Barcha saqlangan fayllar tozalandi.")
    else:
        await message.answer("📭 Hech qanday fayl saqlanmagan.")

@dp.message(Command("pack"))
async def pack_cmd(message: Message):
    user_id = message.from_user.id
    await pack_files(user_id, message)

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
📦 Siqilgan fayllar: {out_cnt}
💾 Vaqtinchalik hajm: {format_size(temp_sz)}
💿 Siqilgan hajm: {format_size(out_sz)}
    """)

@dp.message(lambda m: m.document)
async def handle_document(message: Message):
    doc = message.document
    file_name = doc.file_name
    file_size = doc.file_size
    ext = Path(file_name).suffix.lower()

    if ext not in ['.docx', '.pptx']:
        await message.answer(f"❌ {ext} format qo'llab-quvvatlanmaydi. Faqat DOCX va PPTX fayllarni yuboring.")
        return

    if file_size > MAX_FILE_SIZE:
        await message.answer(f"❌ Fayl juda katta! Maksimal {format_size(MAX_FILE_SIZE)}.")
        return

    file_type = ext[1:]
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
        auto_mode = user_sessions[user_id].get("auto_mode", False)
        msg = f"✅ <b>{file_name}</b> saqlandi.\n📎 Jami: {total} ta fayl.\n"
        if auto_mode:
            msg += f"⚙️ Avtomatik rejim yoqilgan. {AUTO_PACK_THRESHOLD} ta fayl to‘plansa avtomatik ZIPlanadi."
        else:
            msg += "🎯 /pack – siqish va ZIP olish"
        await message.answer(msg)
        # Avtomatik siqish tekshiruvi
        await auto_pack_if_needed(user_id, message)
    except Exception as e:
        logger.exception("Yuklash xatosi")
        await message.answer(f"❌ Yuklashda xatolik: {str(e)[:100]}")
        if file_path.exists():
            file_path.unlink()

@dp.message()
async def unknown(message: Message):
    await message.answer("❓ Tushunarsiz. /help yordam beradi. Faqat DOCX yoki PPTX fayl yuboring.")

# ========== ISHGA TUSHIRISH ==========
async def on_shutdown():
    logger.info("Bot yopilmoqda...")
    await session.close()

async def shutdown(sig):
    logger.info(f"{sig} signal qabul qilindi. Bot to‘xtatilmoqda...")
    await bot.session.close()
    await dp.stop_polling()
    sys.exit(0)

async def main():
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(sig)))

    print("="*50)
    print("🤖 DOCX/PPTX Compressor Bot (avtomatik 3+ faylni bitta ZIP qiladi)")
    print(f"✅ Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:] if len(BOT_TOKEN)>15 else ''}")
    ensure_dirs()
    cleanup_old_files()
    print(f"📁 Yuklangan fayllar: {TEMP_DIR.absolute()}")
    print(f"📁 Siqilgan fayllar: {OUTPUT_DIR.absolute()}")
    print(f"⚙️ Avtomatik siqish chegarasi: {AUTO_PACK_THRESHOLD} ta fayl")
    print("="*50)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await on_shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot foydalanuvchi tomonidan to'xtatildi.")
    except Exception as e:
        logger.exception(f"Kutilmagan xatolik: {e}")
