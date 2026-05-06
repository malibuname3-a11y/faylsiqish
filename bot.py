import os
import zipfile
import shutil
import asyncio
import logging
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Union

from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from PIL import Image

# ========== KONFIGURATSIYA - AVTOMATIK TOPISH ==========

def get_bot_token():
    """Bot tokenini avtomatik topish (environment variable yoki fayldan)"""
    # 1. Environment variable dan olish
    token = os.environ.get("BOT_TOKEN")
    if token:
        return token
    
    # 2. .env fayldan o'qish
    env_file = Path(".env")
    if env_file.exists():
        with open(env_file, 'r') as f:
            for line in f:
                if line.startswith("BOT_TOKEN="):
                    return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    
    # 3. token.txt fayldan o'qish
    token_file = Path("token.txt")
    if token_file.exists():
        with open(token_file, 'r') as f:
            return f.read().strip()
    
    # 4. Hech narsa topilmasa
    raise ValueError("❌ Bot token topilmadi! Iltimos, quyidagi usullardan birini ishlating:\n"
                     "1. Environment variable: export BOT_TOKEN='sizning_tokingiz'\n"
                     "2. .env fayl yarating: BOT_TOKEN='sizning_tokingiz'\n"
                     "3. token.txt fayl yarating va ichiga tokeningizni yozing")

def get_ghostscript_path():
    # Standart qidiruv
    gs = shutil.which("gswin64c") or shutil.which("gswin32c") or shutil.which("gs")
    if gs:
        return gs
    
    # Qo‘lda qo‘shish (o‘z papkangizni yozing!)
    manual_path = r"C:\Program Files\gs\gs10.07.0\bin\gswin64c.exe"
    if os.path.exists(manual_path):
        return manual_path
    
    return None
    
    # 2. Windows standart joylari
    if os.name == 'nt':  # Windows
        possible_paths = [
            r"C:\Program Files\gs\gs*\bin\gswin64c.exe",
            r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe",
            r"C:\Ghostscript\bin\gswin64c.exe",
        ]
        
        import glob
        for pattern in possible_paths:
            matches = glob.glob(pattern)
            if matches:
                return matches[0]
    
    # 3. Linux/Mac standart joylari
    elif os.name == 'posix':
        possible_paths = [
            "/usr/bin/gs",
            "/usr/local/bin/gs",
            "/opt/local/bin/gs",
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                return path
    
    return None

def get_temp_dir():
    """Vaqtinchalik papka joyini aniqlash"""
    # 1. Environment variable
    temp_dir = os.environ.get("BOT_TEMP_DIR")
    if temp_dir:
        return Path(temp_dir)
    
    # 2. Sistemaning vaqtinchalik papkasi
    system_temp = tempfile.gettempdir()
    bot_temp = Path(system_temp) / "file_compressor_bot"
    
    return bot_temp

def get_output_dir():
    """Siqilgan fayllar papkasini aniqlash"""
    # 1. Environment variable
    output_dir = os.environ.get("BOT_OUTPUT_DIR")
    if output_dir:
        return Path(output_dir)
    
    # 2. Joriy papkadagi output papkasi
    current_dir = Path.cwd() / "compressed_files"
    return current_dir

# ========== AVTOMATIK TOPILGAN SOZLAMALAR ==========

try:
    BOT_TOKEN = get_bot_token()
    GHOSTSCRIPT_PATH = get_ghostscript_path()
    TEMP_DIR = get_temp_dir()
    OUTPUT_DIR = get_output_dir()
    
    # Fayl hajm chegaralari (baytlarda)
    MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", 50 * 1024 * 1024))  # default 50MB
    
    # PDF sifat sozlamalari
    PDF_QUALITY = os.environ.get("PDF_QUALITY", "screen")  # screen, ebook, printer, prepress
    
    # Tozalash vaqti (soat)
    CLEANUP_HOURS = int(os.environ.get("CLEANUP_HOURS", 24))
    
    # Logging darajasi
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    
except ValueError as e:
    print(e)
    exit(1)

# Logging sozlamalari
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot va dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========== YORDAMCHI FUNKSIYALAR ==========

def get_system_info() -> dict:
    """Tizim haqida ma'lumot yig'ish"""
    info = {
        "os": os.name,
        "platform": "Unknown",
        "python_version": "Unknown",
        "ghostscript": GHOSTSCRIPT_PATH,
        "temp_dir": str(TEMP_DIR),
        "output_dir": str(OUTPUT_DIR),
    }
    
    try:
        import platform
        info["platform"] = platform.platform()
        info["python_version"] = platform.python_version()
    except:
        pass
    
    return info

def ensure_directories():
    """Kerakli papkalarni yaratish"""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Papkalar mavjudligini tekshirish va log qilish
    logger.info(f"Vaqtinchalik papka: {TEMP_DIR}")
    logger.info(f"Chiqish papkasi: {OUTPUT_DIR}")
    
    if GHOSTSCRIPT_PATH:
        logger.info(f"Ghostscript topildi: {GHOSTSCRIPT_PATH}")
    else:
        logger.warning("Ghostscript topilmadi! PDF siqish ishlamaydi.")

def get_file_size_mb(file_path: Path) -> float:
    """Fayl hajmini MB da qaytaradi"""
    return file_path.stat().st_size / (1024 * 1024)

def format_size(size_bytes: int) -> str:
    """Hajmni chiroyli formatda ko'rsatish"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} GB"

def find_image_files(directory: Path) -> List[Path]:
    """Papkadagi barcha rasm fayllarini topish"""
    image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tiff'}
    images = []
    
    for ext in image_extensions:
        images.extend(directory.rglob(f"*{ext}"))
        images.extend(directory.rglob(f"*{ext.upper()}"))
    
    return images

def compress_images_in_zip(zip_path: Path, quality: int = 85) -> int:
    """ZIP ichidagi barcha rasm fayllarini siqish"""
    temp_extract = TEMP_DIR / f"extract_{datetime.now().timestamp()}"
    temp_extract.mkdir(exist_ok=True)
    
    compressed_count = 0
    
    try:
        # ZIP ni ochish
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_extract)
        
        # Barcha rasmlarni topish va siqish
        images = find_image_files(temp_extract)
        
        for img_path in images:
            try:
                with Image.open(img_path) as img:
                    # PNG transparent bo'lsa, RGB ga o'tkazish
                    if img.mode in ('RGBA', 'LA', 'P'):
                        rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'RGBA':
                            rgb_img.paste(img, mask=img.split()[-1])
                        else:
                            rgb_img.paste(img)
                        img = rgb_img
                    
                    # Rasmni saqlash
                    img.save(img_path, optimize=True, quality=quality)
                    compressed_count += 1
                    
            except Exception as e:
                logger.warning(f"Rasm siqishda xatolik {img_path}: {e}")
        
        # Yangi ZIP yaratish
        new_zip_path = zip_path.parent / f"compressed_{zip_path.name}"
        with zipfile.ZipFile(new_zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
            for root, dirs, files in os.walk(temp_extract):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(temp_extract)
                    zip_ref.write(file_path, arcname)
        
        # Eski ZIP ni o'chirib, yangisini qo'yish
        if zip_path.exists():
            zip_path.unlink()
        new_zip_path.rename(zip_path)
        
    finally:
        # Vaqtinchalik papkani tozalash
        shutil.rmtree(temp_extract, ignore_errors=True)
    
    return compressed_count

def compress_docx(input_path: Path, output_path: Path, quality: int = 85) -> Tuple[bool, str]:
    """DOCX faylini siqish"""
    try:
        shutil.copy2(input_path, output_path)
        compressed_images = compress_images_in_zip(output_path, quality)
        
        original_size = input_path.stat().st_size
        compressed_size = output_path.stat().st_size
        reduction = (1 - compressed_size / original_size) * 100
        
        return True, f"✅ DOCX siqildi! {format_size(compressed_size)} ({reduction:.1f}% kichraydi)\n📸 {compressed_images} ta rasm optimallashtirildi."
    
    except Exception as e:
        logger.error(f"DOCX siqish xatosi: {e}")
        return False, f"❌ Xatolik: {str(e)}"

def compress_pptx(input_path: Path, output_path: Path, quality: int = 85) -> Tuple[bool, str]:
    """PPTX faylini siqish"""
    try:
        shutil.copy2(input_path, output_path)
        compressed_images = compress_images_in_zip(output_path, quality)
        
        original_size = input_path.stat().st_size
        compressed_size = output_path.stat().st_size
        reduction = (1 - compressed_size / original_size) * 100
        
        return True, f"✅ PPTX siqildi! {format_size(compressed_size)} ({reduction:.1f}% kichraydi)\n📸 {compressed_images} ta rasm optimallashtirildi."
    
    except Exception as e:
        logger.error(f"PPTX siqish xatosi: {e}")
        return False, f"❌ Xatolik: {str(e)}"

def compress_pdf(input_path: Path, output_path: Path, quality: str = None) -> Tuple[bool, str]:
    """PDF faylini Ghostscript yordamida siqish"""
    
    if quality is None:
        quality = PDF_QUALITY
    
    # Ghostscript mavjudligini tekshirish
    if not GHOSTSCRIPT_PATH:
        return False, "❌ Ghostscript topilmadi! PDF siqish uchun Ghostscript o'rnating:\n\n" \
                      "Ubuntu/Debian: sudo apt install ghostscript\n" \
                      "Windows: https://ghostscript.com/releases/gsdnld.html\n" \
                      "Mac: brew install ghostscript"
    
    # PDF sifati sozlamalari
    quality_settings = {
        "screen": "/screen",   # 72 dpi
        "ebook": "/ebook",     # 150 dpi
        "printer": "/printer", # 300 dpi
        "prepress": "/prepress" # 300 dpi yuqori
    }
    
    gs_device = quality_settings.get(quality, "/screen")
    
    try:
        cmd = [
            GHOSTSCRIPT_PATH, "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS={gs_device}",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            "-dDetectDuplicateImages=true",
            "-dCompressFonts=true",
            "-dSubsetFonts=true",
            f"-sOutputFile={output_path}",
            str(input_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode == 0 and output_path.exists():
            original_size = input_path.stat().st_size
            compressed_size = output_path.stat().st_size
            
            if compressed_size < original_size:
                reduction = (1 - compressed_size / original_size) * 100
                quality_names = {"screen": "Ekran", "ebook": "Kitob", "printer": "Printer", "prepress": "Yuqori sifat"}
                return True, f"✅ PDF siqildi! {format_size(compressed_size)} ({reduction:.1f}% kichraydi)\n📊 Sifat: {quality_names.get(quality, quality)}"
            else:
                # Agar siqilmagan bo'lsa, originalni qaytarish
                if output_path.exists():
                    output_path.unlink()
                shutil.copy2(input_path, output_path)
                return True, "⚠️ PDF siqilmadi (fayl allaqachon optimallashtirilgan)"
        else:
            return False, f"❌ PDF siqishda xatolik: {result.stderr[:200] if result.stderr else 'Noma\'lum xatolik'}"
            
    except subprocess.TimeoutExpired:
        return False, "❌ Vaqt tugadi! PDF juda katta bo'lishi mumkin."
    except Exception as e:
        logger.error(f"PDF siqish xatosi: {e}")
        return False, f"❌ Xatolik: {str(e)}"

def create_zip_archive(files: List[Path], zip_name: str) -> Path:
    """Siqilgan fayllarni ZIP arxivga joylashtirish"""
    zip_path = OUTPUT_DIR / zip_name
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in files:
            if file.exists():
                zipf.write(file, file.name)
    
    return zip_path

def cleanup_old_files():
    """Eski vaqtinchalik fayllarni tozalash"""
    now = datetime.now().timestamp()
    deleted_count = 0
    
    for directory in [TEMP_DIR, OUTPUT_DIR]:
        if not directory.exists():
            continue
            
        for file in directory.iterdir():
            if file.is_file():
                file_age = now - file.stat().st_mtime
                if file_age > CLEANUP_HOURS * 3600:
                    try:
                        file.unlink()
                        deleted_count += 1
                    except Exception as e:
                        logger.warning(f"Tozalashda xatolik {file}: {e}")
    
    if deleted_count > 0:
        logger.info(f"{deleted_count} ta eski fayl tozalandi")

# ========== TELEGRAM BOT HANDLERLARI ==========

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Start komandasi"""
    welcome_text = """
📎 *File Compressor Bot*

Menga DOCX, PPTX yoki PDF fayl yuboring, men uni siqib (compress) qilib,
ZIPlab qaytarib beraman!

*Qanday ishlaydi:*
1️⃣ Menga fayl yuboring
2️⃣ Men faylni siqaman
3️⃣ Siqilgan faylni ZIP qilib qaytaraman

*Natijalar:*
• PDF: 50-80% gacha kichrayadi
• DOCX/PPTX: Rasmlar siqiladi

*Komandalar:*
/start - Botni qayta ishga tushirish
/help - Yordam
/stats - Statistika
/system - Tizim ma'lumoti

*Eslatma:* Fayl hajmi 50MB dan oshmasligi kerak!
"""
    await message.answer(welcome_text, parse_mode="Markdown")

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Help komandasi"""
    help_text = """
🤖 *Yordam*

*Qo'llanma:*
• Menga istalgan DOCX, PPTX yoki PDF faylni yuboring
• Avtomatik ravishda siqib, ZIP qilib yuboraman

*Qo'llab-quvvatlanadigan formatlar:*
📄 PDF - Ghostscript orqali siqiladi
📝 DOCX - Ichidagi rasmlar siqiladi
📊 PPTX - Ichidagi rasmlar siqiladi

*PDF siqish darajalari:*
• screen - eng kichik hajm (72 dpi)
• ebook - o'rtacha (150 dpi)  
• printer - yuqori sifat (300 dpi)
• prepress - eng yuqori sifat

PDF uchun: `/compress_pdf [screen|ebook|printer|prepress]`

*Cheklovlar:*
• Maksimal fayl hajmi: 50MB
• Bot faqat bitta faylni qabul qiladi

/stats - Bot statistikasini ko'rish
/system - Tizim ma'lumoti
"""
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("system"))
async def cmd_system(message: Message):
    """Tizim ma'lumoti"""
    info = get_system_info()
    
    gs_status = "✅ O'rnatilgan" if info["ghostscript"] else "❌ O'rnatilmagan"
    
    system_text = f"""
🖥️ *Tizim Ma'lumoti*

• Operatsion tizim: `{info['os']}`
• Platforma: `{info['platform'][:50]}`
• Python versiya: `{info['python_version']}`

📦 *Ghostscript:*
• Holat: {gs_status}
• Joylashuv: `{info['ghostscript'] or 'Topilmadi'}`

📁 *Papkalar:*
• Vaqtinchalik: `{info['temp_dir']}`
• Chiqish: `{info['output_dir']}`

⚙️ *Sozlamalar:*
• Maksimal hajm: {format_size(MAX_FILE_SIZE)}
• PDF sifati: `{PDF_QUALITY}`
• Tozalash vaqti: `{CLEANUP_HOURS} soat`
"""
    await message.answer(system_text, parse_mode="Markdown")

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Statistika komandasi"""
    # Vaqtinchalik fayllarni tozalash
    cleanup_old_files()
    
    # Papkalardagi fayllar soni
    temp_files = list(TEMP_DIR.glob("*")) if TEMP_DIR.exists() else []
    output_files = list(OUTPUT_DIR.glob("*")) if OUTPUT_DIR.exists() else []
    
    temp_count = len([f for f in temp_files if f.is_file()])
    output_count = len([f for f in output_files if f.is_file()])
    
    # Diskda band qilingan joy
    temp_size = sum(f.stat().st_size for f in temp_files if f.is_file())
    output_size = sum(f.stat().st_size for f in output_files if f.is_file())
    
    stats_text = f"""
📊 *Bot Statistikasi*

⏳ Kutilayotgan fayllar: `{temp_count}`
📦 Siqilgan fayllar: `{output_count}`
💾 Vaqtinchalik joy: `{format_size(temp_size)}`
💿 Siqilgan fayllar hajmi: `{format_size(output_size)}`

📁 Papkalar:
• Temp: `{TEMP_DIR}`
• Output: `{OUTPUT_DIR}`

🔧 Qo'llab-quvvatlanadigan formatlar: PDF, DOCX, PPTX
"""
    await message.answer(stats_text, parse_mode="Markdown")

@dp.message(Command("compress_pdf"))
async def cmd_compress_pdf(message: Message):
    """PDF siqish darajasini o'zgartirish"""
    args = message.text.split()
    quality = args[1] if len(args) > 1 else None
    
    if quality and quality not in ["screen", "ebook", "printer", "prepress"]:
        await message.answer("❌ Noto'g'ri parametr! Qabul qilinadiganlar: screen, ebook, printer, prepress")
        return
    
    # Foydalanuvchi sozlamalarini saqlash
    if not hasattr(cmd_compress_pdf, "user_settings"):
        cmd_compress_pdf.user_settings = {}
    
    if quality:
        cmd_compress_pdf.user_settings[message.from_user.id] = {"pdf_quality": quality}
        await message.answer(f"✅ PDF siqish darajasi '{quality}' ga o'rnatildi!")
    else:
        current = cmd_compress_pdf.user_settings.get(message.from_user.id, {}).get("pdf_quality", PDF_QUALITY)
        await message.answer(f"📊 Hozirgi PDF siqish darajasi: `{current}`\n\nO'zgartirish uchun: `/compress_pdf [screen|ebook|printer|prepress]`", parse_mode="Markdown")

@dp.message(lambda message: message.document)
async def handle_document(message: Message):
    """Fayl kelganda ishlov berish"""
    document = message.document
    file_name = document.file_name
    file_size = document.file_size
    
    # Fayl hajmini tekshirish
    if file_size > MAX_FILE_SIZE:
        await message.answer(f"❌ Fayl juda katta! Maksimal hajm: {format_size(MAX_FILE_SIZE)}. Sizning faylingiz: {format_size(file_size)}")
        return
    
    # Fayl kengaytmasini tekshirish
    file_ext = Path(file_name).suffix.lower()
    
    if file_ext not in ['.pdf', '.docx', '.pptx']:
        await message.answer(f"❌ `{file_ext}` format qo'llab-quvvatlanmaydi.\n\nQo'llab-quvvatlanadigan formatlar: .pdf, .docx, .pptx", parse_mode="Markdown")
        return
    
    # Jarayon boshlanganligi haqida xabar
    status_msg = await message.answer(f"⏳ Fayl qabul qilindi. Siqish boshlanmoqda...\n📁 `{file_name}` ({format_size(file_size)})", parse_mode="Markdown")
    
    # Faylni yuklab olish
    file_id = document.file_id
    file = await bot.get_file(file_id)
    
    # Vaqtinchalik papkalarni yaratish
    ensure_directories()
    
    # Unikal fayl nomi yaratish
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    original_file = TEMP_DIR / f"original_{timestamp}_{file_name}"
    compressed_file = OUTPUT_DIR / f"compressed_{timestamp}_{file_name}"
    
    try:
        # Faylni yuklab olish
        await bot.download_file(file.file_path, original_file)
        
        # Fayl turiga qarab siqish
        success = False
        result_message = ""
        
        if file_ext == '.pdf':
            # Foydalanuvchi sozlamalarini olish
            pdf_quality = None
            if hasattr(cmd_compress_pdf, "user_settings"):
                pdf_quality = cmd_compress_pdf.user_settings.get(message.from_user.id, {}).get("pdf_quality")
            
            success, result_message = compress_pdf(original_file, compressed_file, pdf_quality)
        
        elif file_ext == '.docx':
            success, result_message = compress_docx(original_file, compressed_file)
        
        elif file_ext == '.pptx':
            success, result_message = compress_pptx(original_file, compressed_file)
        
        if not success:
            await status_msg.edit_text(result_message)
            return
        
        # Siqilgan fayl hajmini tekshirish
        compressed_size = compressed_file.stat().st_size
        
        # ZIP arxiv yaratish
        zip_name = f"compressed_{timestamp}_{Path(file_name).stem}.zip"
        zip_path = create_zip_archive([compressed_file], zip_name)
        
        # Natijani foydalanuvchiga yuborish
        with open(zip_path, 'rb') as zip_file:
            await message.answer_document(
                BufferedInputFile(zip_file.read(), filename=zip_name),
                caption=f"{result_message}\n\n"
                        f"📦 ZIP hajmi: {format_size(compressed_size)}\n"
                        f"📎 Asl hajm: {format_size(file_size)}\n"
                        f"💾 Tejalgan: {format_size(file_size - compressed_size)}"
            )
        
        await status_msg.delete()
        
        # Tozalash
        if original_file.exists():
            original_file.unlink()
        if compressed_file.exists():
            compressed_file.unlink()
        if zip_path.exists():
            zip_path.unlink()
        
    except Exception as e:
        logger.error(f"Qayta ishlashda xatolik: {e}")
        await status_msg.edit_text(f"❌ Kutilmagan xatolik yuz berdi: {str(e)[:200]}")
        
        # Tozalash
        try:
            if original_file.exists():
                original_file.unlink()
            if compressed_file.exists():
                compressed_file.unlink()
        except:
            pass

@dp.message()
async def handle_unknown(message: Message):
    """Tanilmagan xabarlar"""
    await message.answer("❓ Tushunarsiz buyruq. Yordam uchun /help yuboring.\n\nYoki menga to'g'ridan-to'g'ri DOCX, PPTX yoki PDF fayl yuboring!")

# ========== ASOSIY ISHGA TUSHIRISH ==========

async def main():
    """Botni ishga tushirish"""
    print("=" * 50)
    print("🤖 File Compressor Bot ishga tushmoqda...")
    print("=" * 50)
    
    # Papkalarni tayyorlash
    ensure_directories()
    
    # Eski fayllarni tozalash
    cleanup_old_files()
    
    # Tizim ma'lumotini chiqarish
    info = get_system_info()
    print(f"✅ Bot token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:] if len(BOT_TOKEN) > 15 else ''}")
    print(f"✅ Ghostscript: {info['ghostscript'] or 'TOPILMADI'}")
    print(f"✅ Vaqtinchalik papka: {TEMP_DIR}")
    print(f"✅ Chiqish papkasi: {OUTPUT_DIR}")
    print(f"✅ Maksimal fayl hajmi: {format_size(MAX_FILE_SIZE)}")
    print("=" * 50)
    print("🎯 Bot ishga tushdi! Telegramda @ ni tekshiring...")
    print("=" * 50)
    
    # Botni ishga tushirish
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
