import os
import zipfile
import shutil
import asyncio
import logging
import subprocess
import tempfile
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Tuple, List, Dict, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from PIL import Image

# ========== AVTOMATIK KONFIGURATSIYA ==========
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
    raise ValueError("❌ Token topilmadi! token.txt yoki .env fayl yarating yoki BOT_TOKEN muhit o'zgaruvchisini o'rnating.")

def find_ghostscript() -> Optional[str]:
    for name in ["gswin64c", "gswin32c", "gs"]:
        gs = shutil.which(name)
        if gs:
            return gs
    if os.name == "nt":
        for base in [os.environ.get("ProgramFiles", "C:\\Program Files"),
                     os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")]:
            gs_dir = Path(base) / "gs"
            if gs_dir.exists():
                for ver_dir in gs_dir.iterdir():
                    if ver_dir.is_dir() and ver_dir.name.startswith("gs"):
                        bin_dir = ver_dir / "bin"
                        if bin_dir.exists():
                            for exe in ["gswin64c.exe", "gswin32c.exe"]:
                                exe_path = bin_dir / exe
                                if exe_path.exists():
                                    return str(exe_path)
    return None

# ========== SOZLAMALAR ==========
BOT_TOKEN = get_token()
GHOSTSCRIPT = find_ghostscript()
BASE_TEMP_DIR = Path(tempfile.gettempdir()) / "file_compressor_bot"
TEMP_DIR = BASE_TEMP_DIR / "uploads"
OUTPUT_DIR = BASE_TEMP_DIR / "compressed"
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", 50 * 1024 * 1024))
DEFAULT_PDF_QUALITY = os.environ.get("PDF_QUALITY", "screen")
CLEANUP_HOURS = int(os.environ.get("CLEANUP_HOURS", 24))
API_TIMEOUT = int(os.environ.get("API_TIMEOUT", 120))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Session va bot
session = AiohttpSession(timeout=API_TIMEOUT)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"), session=session)
dp = Dispatcher()

# Foydalanuvchi ma'lumotlari
user_sessions: Dict[int, Dict] = {}
user_pdf_quality: Dict[int, str] = {}

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

def compress_docx_pptx(input_path: Path, output_path: Path, ftype: str) -> Tuple[bool, str, int]:
    try:
        shutil.copy2(input_path, output_path)
        img_count = compress_images_in_zip(output_path)
        orig = input_path.stat().st_size
        new = output_path.stat().st_size
        ratio = (1 - new/orig) * 100 if orig else 0
        return True, f"✅ {ftype} siqildi! {format_size(new)} ({ratio:.1f}% kichraydi)\n📸 {img_count} ta rasm optimallashtirildi.", new
    except Exception as e:
        return False, f"❌ Xatolik: {str(e)}", 0

def compress_pdf(input_path: Path, output_path: Path, quality: str = "screen") -> Tuple[bool, str, int]:
    if not GHOSTSCRIPT:
        return False, "❌ Ghostscript topilmadi! PDF siqish uchun uni o'rnating.", 0
    q_map = {"screen":"/screen","ebook":"/ebook","printer":"/printer","prepress":"/prepress"}
    gs_setting = q_map.get(quality, "/screen")
    cmd = [GHOSTSCRIPT, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
           f"-dPDFSETTINGS={gs_setting}", "-dNOPAUSE", "-dQUIET", "-dBATCH",
           "-dDetectDuplicateImages=true", "-dCompressFonts=true",
           f"-sOutputFile={output_path}", str(input_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and output_path.exists():
            orig = input_path.stat().st_size
            new = output_path.stat().st_size
            if new < orig:
                ratio = (1 - new/orig) * 100
                q_names = {"screen":"Ekran","ebook":"Kitob","printer":"Printer","prepress":"Yuqori"}
                return True, f"✅ PDF siqildi! {format_size(new)} ({ratio:.1f}% kichraydi)\n📊 Sifat: {q_names.get(quality, quality)}", new
            else:
                output_path.unlink()
                shutil.copy2(input_path, output_path)
                return True, "⚠️ PDF siqilmadi (allaqachon optimallashtirilgan)", orig
        else:
            return False, f"❌ PDF xatosi: {r.stderr[:200] if r.stderr else 'Noma'lum'}", 0
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
                f["path"].unlink()
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

# ========== BOT HANDLERLARI ==========
@dp.message(Command("start"))
async def start_cmd(message: Message):
    gs_status = "✅ mavjud" if GHOSTSCRIPT else "❌ topilmadi"
    text = f"""
<b>📎 Ko'p faylli Compressor Bot</b>

Men bir nechta DOCX, PPTX, PDF fayllarni qabul qilaman, ularni siqib, <b>bitta ZIP</b> arxivda kategoriyalarga ajratib beraman.

<b>📌 Qanday ishlaydi:</b>
1️⃣ Fayllarni birma-bir yuboring (har bir fayl alohida)
2️⃣ <b>/list</b> – yuklangan fayllar ro'yxati
3️⃣ <b>/pack</b> – barcha siqilgan fayllarni ZIP qilib olish
4️⃣ <b>/clear</b> – saqlangan fayllarni tozalash

<b>🔧 Boshqa komandalar:</b>
/quality [screen|ebook|printer|prepress] – PDF sifat
/stats – statistika
/help – yordam

⚙️ Ghostscript: {gs_status}
"""
    await message.answer(text)

@dp.message(Command("help"))
async def help_cmd(message: Message):
    text = """
<b>🤖 Yordam</b>

• Fayl yuboring (PDF, DOCX, PPTX) – ular vaqtincha saqlanadi
• <b>/pack</b> – barcha saqlangan fayllarni siqib, bitta ZIP arxiv qilib yuboradi
• <b>/list</b> – saqlangan fayllar ro'yxati va umumiy hajm
• <b>/clear</b> – saqlangan fayllarni o'chirib tashlaydi

<b>PDF sifat darajalari:</b>
screen (eng kichik) | ebook | printer | prepress (eng sifatli)

Misol: <code>/quality screen</code>
"""
    await message.answer(text)

@dp.message(Command("quality"))
async def quality_cmd(message: Message):
    args = message.text.split()
    if len(args) != 2 or args[1] not in ["screen","ebook","printer","prepress"]:
        await message.answer("❌ Noto'g'ri! Ishlating: /quality screen|ebook|printer|prepress")
        return
    user_pdf_quality[message.from_user.id] = args[1]
    await message.answer(f"✅ PDF sifati '{args[1]}' ga o'rnatildi")

@dp.message(Command("list"))
async def list_cmd(message: Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id, {"files": []})
    files = session.get("files", [])
    if not files:
        await message.answer("📭 Hech qanday fayl saqlanmagan. Avval fayl yuboring.")
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
    session = user_sessions.get(user_id, {"files": []})
    files = session.get("files", [])
    if not files:
        await message.answer("📭 Hech qanday fayl saqlanmagan. Avval fayl yuboring.")
        return

    status = await message.answer(f"⏳ {len(files)} ta fayl siqilmoqda... Iltimos kuting.")
    ensure_dirs()
    quality = user_pdf_quality.get(user_id, DEFAULT_PDF_QUALITY)

    compressed_data = []
    results = []
    total_original = 0
    total_compressed = 0

    for fdata in files:
        original_path = fdata["path"]
        file_type = fdata["type"]
        original_name = fdata["name"]
        total_original += fdata["size"]

        compressed_name = f"compressed_{original_name}"
        compressed_path = OUTPUT_DIR / compressed_name

        if file_type == "pdf":
            ok, msg, sz = compress_pdf(original_path, compressed_path, quality)
        elif file_type in ["docx", "pptx"]:
            ok, msg, sz = compress_docx_pptx(original_path, compressed_path, file_type.upper())
        else:
            ok, msg, sz = False, f"❌ {file_type} qo'llab-quvvatlanmaydi", 0

        if ok:
            compressed_data.append({
                "original_path": original_path,
                "compressed_path": compressed_path,
                "category": file_type.upper(),
                "original_name": original_name
            })
            total_compressed += sz
            results.append(f"✅ {original_name} -> {format_size(sz)}")
        else:
            results.append(f"❌ {original_name}: {msg}")

    if not compressed_data:
        await status.edit_text("❌ Hech qanday fayl siqilmadi.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"compressed_{timestamp}_{user_id}.zip"
    zip_path = create_categorized_zip(compressed_data, zip_name)

    summary = f"📊 <b>Hisobot:</b>\n" + "\n".join(results[:10])
    if len(results) > 10:
        summary += f"\n... va {len(results)-10} ta fayl"
    summary += f"\n\n📦 Original hajm: {format_size(total_original)}"
    summary += f"\n💾 Siqilgan hajm: {format_size(total_compressed)}"
    summary += f"\n📉 Tejalgan: {format_size(total_original - total_compressed)} ({(1-total_compressed/total_original)*100:.1f}%)"

    with open(zip_path, 'rb') as f:
        await message.answer_document(
            BufferedInputFile(f.read(), filename=zip_name),
            caption=summary
        )
    await status.delete()

    cleanup_user_session(user_id)
    if zip_path.exists():
        zip_path.unlink()
    for item in compressed_data:
        try:
            item["compressed_path"].unlink()
        except:
            pass

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
⏳ Kutuvchi fayllar: {temp_cnt}
📦 Siqilgan fayllar: {out_cnt}
💾 Vaqtinchalik hajm: {format_size(temp_sz)}
💿 Siqilgan hajm: {format_size(out_sz)}
🖨️ Ghostscript: {"✅ mavjud" if GHOSTSCRIPT else "❌ topilmadi"}
""")

@dp.message(lambda m: m.document)
async def handle_document(message: Message):
    doc = message.document
    file_name = doc.file_name
    file_size = doc.file_size
    ext = Path(file_name).suffix.lower()

    if file_size > MAX_FILE_SIZE:
        await message.answer(f"❌ Fayl juda katta! Maksimal {format_size(MAX_FILE_SIZE)}")
        return

    if ext not in ['.pdf', '.docx', '.pptx']:
        await message.answer(f"❌ {ext} format qo'llab-quvvatlanmaydi. Faqat PDF, DOCX, PPTX.")
        return

    file_type = ext[1:]  # pdf, docx, pptx

    ensure_dirs()
    user_id = message.from_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = {"files": []}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = f"{timestamp}_{user_id}_{file_name}"
    file_path = TEMP_DIR / safe_name

    try:
        file = await bot.get_file(doc.file_id)
        await bot.download_file(file.file_path, file_path)

        user_sessions[user_id]["files"].append({
            "path": file_path,
            "name": file_name,
            "type": file_type,
            "size": file_size,
            "timestamp": datetime.now()
        })

        total_files = len(user_sessions[user_id]["files"])
        await message.answer(f"✅ <b>{file_name}</b> saqlandi.\n📎 {total_files} ta fayl to'plangan.\n🎯 /pack – siqish va ZIP olish")
    except Exception as e:
        logger.exception("Yuklash xatosi")
        await message.answer(f"❌ Yuklashda xatolik: {str(e)[:100]}")
        if file_path.exists():
            file_path.unlink()

@dp.message()
async def unknown(message: Message):
    await message.answer("❓ Tushunarsiz. /help yordam beradi yoki PDF/DOCX/PPTX fayl yuboring.")

# ========== ASOSIY FUNKSIYA (TO‘G‘RI yopilish bilan) ==========
async def on_shutdown():
    logger.info("Bot yopilmoqda...")
    await session.close()
    logger.info("Session yopildi.")

async def main():
    # Signal handler (Ctrl+C)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    print("="*50)
    print("🤖 Ko'p faylli Compressor Bot ishga tushmoqda...")
    print(f"✅ Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:] if len(BOT_TOKEN)>15 else ''}")
    if GHOSTSCRIPT:
        print(f"✅ Ghostscript topildi: {GHOSTSCRIPT}")
    else:
        print("❌ Ghostscript topilmadi! PDF siqish ishlamaydi.")
    ensure_dirs()
    cleanup_old_files()
    print(f"📁 Yuklangan fayllar: {TEMP_DIR.absolute()}")
    print(f"📁 Siqilgan fayllar: {OUTPUT_DIR.absolute()}")
    print("="*50)

    try:
        # Pollingni boshlash
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await on_shutdown()

async def shutdown():
    logger.info("Shutdown signal qabul qilindi.")
    await bot.session.close()
    await dp.stop_polling()
    sys.exit(0)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot foydalanuvchi tomonidan to'xtatildi.")
    except Exception as e:
        logger.exception(f"Kutilmagan xatolik: {e}")
