import os
import zipfile
import shutil
import asyncio
import logging
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Tuple, List

from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from PIL import Image

# ========== KONFIGURATSIYA ==========
# Tokenni shu yerda o'zgartiring yoki environment variable orqali bering
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # <-- O'ZGARTIRING!

# Papkalar (avtomatik yaratiladi)
TEMP_DIR = Path("temp_files")
OUTPUT_DIR = Path("compressed_files")

# Maksimal fayl hajmi (50MB)
MAX_FILE_SIZE = 50 * 1024 * 1024

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========== GHOSTSCRIPT PATHNI TOPISH ==========
def find_ghostscript() -> str:
    """
    Ghostscript ijro etiladigan faylini topadi.
    Avval PATH dan qidiradi, keyin Windows standart joylaridan.
    Agar topilmasa, None qaytaradi.
    """
    # Mumkin bo'lgan nomlar
    possible_names = ["gswin64c", "gswin32c", "gs"]
    
    # 1. PATH dan qidirish
    for name in possible_names:
        gs_path = shutil.which(name)
        if gs_path:
            return gs_path
    
    # 2. Windows uchun qo'shimcha joylar
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
        
        search_dirs = [
            program_files,
            program_files_x86,
            "C:\\",  # ba'zi eski o'rnatuvchilar shu yerga qo'yadi
        ]
        
        # Har bir papka ichida gs* papkalarini qidir
        for base in search_dirs:
            gs_base = Path(base) / "gs"
            if gs_base.exists():
                for version_dir in gs_base.iterdir():
                    if version_dir.is_dir() and version_dir.name.startswith("gs"):
                        bin_dir = version_dir / "bin"
                        if bin_dir.exists():
                            for exe in possible_names:
                                exe_path = bin_dir / f"{exe}.exe"
                                if exe_path.exists():
                                    return str(exe_path)
    
    return None

GHOSTSCRIPT_PATH = find_ghostscript()
IF_GHOSTSCRIPT_MISSING = """
❌ Ghostscript topilmadi! PDF fayllarni siqish uchun uni o'rnating:

Windows:
1. Yuklab oling: https://ghostscript.com/releases/gsdnld.html
2. O'rnatishda "Add to system PATH" ni belgilang
3. Qayta ishga tushiring

Ubuntu/Debian: sudo apt install ghostscript
macOS: brew install ghostscript

O'rnatgandan keyin botni qayta ishga tushiring.
"""

# ========== YORDAMCHI FUNKSIYALAR ==========
def ensure_dirs():
    TEMP_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

def format_size(size: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} GB"

def compress_images_in_zip(zip_path: Path, quality: int = 85) -> int:
    """ZIP ichidagi barcha rasmlarni siqadi (DOCX va PPTX uchun)"""
    temp_extract = TEMP_DIR / f"extract_{datetime.now().timestamp()}"
    temp_extract.mkdir()
    count = 0
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(temp_extract)
        
        image_ext = ('.jpg', '.jpeg', '.png', '.bmp', '.gif')
        for img_path in temp_extract.rglob("*"):
            if img_path.suffix.lower() in image_ext:
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
                    logger.warning(f"Rasm siqishda xato {img_path}: {e}")
        
        # Yangi ZIP yaratish
        new_zip = zip_path.with_name(f"compressed_{zip_path.name}")
        with zipfile.ZipFile(new_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in temp_extract.rglob("*"):
                if file.is_file():
                    arcname = file.relative_to(temp_extract)
                    zf.write(file, arcname)
        
        zip_path.unlink()
        new_zip.rename(zip_path)
    finally:
        shutil.rmtree(temp_extract, ignore_errors=True)
    return count

def compress_docx_pptx(input_path: Path, output_path: Path, file_type: str) -> Tuple[bool, str]:
    try:
        shutil.copy2(input_path, output_path)
        img_count = compress_images_in_zip(output_path)
        orig = input_path.stat().st_size
        new = output_path.stat().st_size
        ratio = (1 - new/orig) * 100 if orig > 0 else 0
        return True, f"✅ {file_type} siqildi! {format_size(new)} ({ratio:.1f}% kichraydi)\n📸 {img_count} ta rasm optimallashtirildi."
    except Exception as e:
        logger.error(f"{file_type} siqish xatosi: {e}")
        return False, f"❌ Xatolik: {str(e)}"

def compress_pdf(input_path: Path, output_path: Path, quality: str = "screen") -> Tuple[bool, str]:
    if not GHOSTSCRIPT_PATH:
        return False, IF_GHOSTSCRIPT_MISSING
    
    quality_settings = {
        "screen": "/screen",
        "ebook": "/ebook",
        "printer": "/printer",
        "prepress": "/prepress"
    }
    gs_setting = quality_settings.get(quality, "/screen")
    
    cmd = [
        GHOSTSCRIPT_PATH,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS={gs_setting}",
        "-dNOPAUSE", "-dQUIET", "-dBATCH",
        "-dDetectDuplicateImages=true",
        "-dCompressFonts=true",
        "-sOutputFile=" + str(output_path),
        str(input_path)
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and output_path.exists():
            orig = input_path.stat().st_size
            new = output_path.stat().st_size
            if new < orig:
                ratio = (1 - new/orig) * 100
                quality_names = {"screen": "Ekran", "ebook": "Kitob", "printer": "Printer", "prepress": "Yuqori sifat"}
                return True, f"✅ PDF siqildi! {format_size(new)} ({ratio:.1f}% kichraydi)\n📊 Sifat: {quality_names.get(quality, quality)}"
            else:
                output_path.unlink()
                shutil.copy2(input_path, output_path)
                return True, "⚠️ PDF siqilmadi (allaqachon optimallashtirilgan)"
        else:
            error_msg = result.stderr[:200] if result.stderr else "Noma'lum xatolik"
            return False, f"❌ PDF siqishda xato: {error_msg}"
    except subprocess.TimeoutExpired:
        return False, "❌ Vaqt tugadi! PDF juda katta."
    except Exception as e:
        return False, f"❌ Xatolik: {str(e)}"

def create_zip(files: List[Path], zip_name: str) -> Path:
    zip_path = OUTPUT_DIR / zip_name
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            if f.exists():
                zf.write(f, f.name)
    return zip_path

# ========== BOT HANDLERLARI ==========
@dp.message(Command("start"))
async def start_cmd(message: Message):
    text = """
📎 *File Compressor Bot*

Menga DOCX, PPTX yoki PDF fayl yuboring, siqib ZIPlab qaytaraman.

📌 *Qanday ishlaydi:*
1️⃣ Faylni yuboring
2️⃣ Bot siqadi va ZIP qiladi
3️⃣ Sizga siqilgan faylni yuboradi

⚡ *Natijalar:* PDF 50-80%, DOCX/PPTX rasm siqish

🔧 *Komandalar:*
/start - boshlash
/help - yordam
/stats - statistika
/quality - PDF sifatini o'zgartirish
"""
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("help"))
async def help_cmd(message: Message):
    text = """
🤖 *Yordam*

• Faylni yuboring (PDF, DOCX, PPTX)
• PDF uchun sifatni /quality buyrug'i bilan o'zgartiring

*PDF sifat darajalari:*
- screen (eng kichik)
- ebook (o'rtacha)
- printer (yuqori)
- prepress (eng yuqori)

Misol: `/quality screen`
"""
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("quality"))
async def set_quality(message: Message):
    args = message.text.split()
    if len(args) != 2 or args[1] not in ["screen","ebook","printer","prepress"]:
        await message.answer("❌ Noto'g'ri! Ishla: /quality screen|ebook|printer|prepress")
        return
    quality = args[1]
    if not hasattr(set_quality, "user_settings"):
        set_quality.user_settings = {}
    set_quality.user_settings[message.from_user.id] = quality
    await message.answer(f"✅ PDF sifati '{quality}' ga o'rnatildi")

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    ensure_dirs()
    temp_files = sum(1 for _ in TEMP_DIR.iterdir() if _.is_file())
    out_files = sum(1 for _ in OUTPUT_DIR.iterdir() if _.is_file())
    total_temp = sum(f.stat().st_size for f in TEMP_DIR.iterdir() if f.is_file())
    total_out = sum(f.stat().st_size for f in OUTPUT_DIR.iterdir() if f.is_file())
    text = f"""
📊 *Statistika*
⏳ Kutuvchi fayllar: {temp_files}
📦 Siqilgan fayllar: {out_files}
💾 Vaqtinchalik hajm: {format_size(total_temp)}
💿 Siqilgan hajm: {format_size(total_out)}
🔧 GS holati: {"✅ mavjud" if GHOSTSCRIPT_PATH else "❌ topilmadi"}
"""
    await message.answer(text, parse_mode="Markdown")

@dp.message(lambda m: m.document)
async def handle_file(message: Message):
    doc = message.document
    file_name = doc.file_name
    file_size = doc.file_size
    if file_size > MAX_FILE_SIZE:
        await message.answer(f"❌ Fayl juda katta! Maksimal {format_size(MAX_FILE_SIZE)}")
        return
    
    ext = Path(file_name).suffix.lower()
    if ext not in ['.pdf', '.docx', '.pptx']:
        await message.answer(f"❌ {ext} format qo'llab-quvvatlanmaydi. Faqat PDF, DOCX, PPTX.")
        return
    
    status = await message.answer(f"⏳ Yuklab olinmoqda va siqilmoqda...\n📁 {file_name} ({format_size(file_size)})")
    
    # Papkalar
    ensure_dirs()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    original = TEMP_DIR / f"orig_{timestamp}_{file_name}"
    compressed = OUTPUT_DIR / f"comp_{timestamp}_{file_name}"
    
    try:
        file = await bot.get_file(doc.file_id)
        await bot.download_file(file.file_path, original)
        
        # Siqish
        if ext == '.pdf':
            quality = getattr(set_quality, "user_settings", {}).get(message.from_user.id, "screen")
            success, msg = compress_pdf(original, compressed, quality)
        elif ext == '.docx':
            success, msg = compress_docx_pptx(original, compressed, "DOCX")
        else:  # .pptx
            success, msg = compress_docx_pptx(original, compressed, "PPTX")
        
        if not success:
            await status.edit_text(msg)
            return
        
        # ZIP yaratish
        zip_name = f"compressed_{timestamp}_{Path(file_name).stem}.zip"
        zip_path = create_zip([compressed], zip_name)
        
        # Yuborish
        with open(zip_path, "rb") as f:
            await message.answer_document(
                BufferedInputFile(f.read(), filename=zip_name),
                caption=f"{msg}\n\n📦 ZIP hajmi: {format_size(compressed.stat().st_size)}\n📎 Asl hajm: {format_size(file_size)}\n💾 Tejalgan: {format_size(file_size - compressed.stat().st_size)}"
            )
        await status.delete()
    
    except Exception as e:
        logger.exception("Ishlov berishda xato")
        await status.edit_text(f"❌ Xatolik: {str(e)[:200]}")
    finally:
        # Tozalash
        for p in [original, compressed]:
            if p and p.exists():
                p.unlink()
        # Zip faylni tozalash (agar mavjud bo'lsa)
        if 'zip_path' in locals() and zip_path.exists():
            zip_path.unlink()

@dp.message()
async def unknown(message: Message):
    await message.answer("❓ Tushunarsiz. /help yordam beradi yoki fayl yuboring (PDF, DOCX, PPTX)")

# ========== ISHGA TUSHIRISH ==========
async def main():
    print("="*50)
    print("🤖 File Compressor Bot ishga tushmoqda...")
    ensure_dirs()
    if GHOSTSCRIPT_PATH:
        print(f"✅ Ghostscript topildi: {GHOSTSCRIPT_PATH}")
    else:
        print("❌ Ghostscript topilmadi! PDF siqish ishlamaydi.")
        print(IF_GHOSTSCRIPT_MISSING)
    print(f"📁 Vaqtinchalik papka: {TEMP_DIR.absolute()}")
    print(f"📁 Chiqish papkasi: {OUTPUT_DIR.absolute()}")
    print("="*50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
