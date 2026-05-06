import os
import zipfile
import shutil
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict

from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from PIL import Image

# ========== KONFIGURATSIYA ==========
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # BotFather dan olingan token

# Papkalar
TEMP_DIR = Path("temp_files")
COMPRESSED_DIR = Path("compressed_files")

# Fayl hajm chegaralari (baytlarda)
MAX_FILE_SIZE = 50 * 1024 * 1024  # Telegram bot uchun 50MB

# Logging sozlamalari
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot va dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========== YORDAMCHI FUNKSIYALAR ==========

def ensure_directories():
    """Kerakli papkalarni yaratish"""
    TEMP_DIR.mkdir(exist_ok=True)
    COMPRESSED_DIR.mkdir(exist_ok=True)


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


def compress_images_in_zip(zip_path: Path, quality: int = 85) -> int:
    """
    ZIP ichidagi barcha rasm fayllarini siqish
    Qaytaradi: nechta rasm siqilganligi
    """
    temp_extract = TEMP_DIR / f"extract_{datetime.now().timestamp()}"
    temp_extract.mkdir(exist_ok=True)
    
    compressed_count = 0
    
    try:
        # ZIP ni ochish
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_extract)
        
        # Barcha rasmlarni siqish
        image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif'}
        
        for root, dirs, files in os.walk(temp_extract):
            for file in files:
                file_path = Path(root) / file
                
                if file_path.suffix.lower() in image_extensions:
                    try:
                        # Rasmni ochish va siqish
                        with Image.open(file_path) as img:
                            if img.mode in ('RGBA', 'LA', 'P'):
                                # PNG transparent - RGB ga o'tkazish
                                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                                rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                                img = rgb_img
                            
                            # Rasmni saqlash
                            img.save(file_path, optimize=True, quality=quality)
                            compressed_count += 1
                    except Exception as e:
                        logger.warning(f"Rasm siqishda xatolik {file}: {e}")
        
        # Yangi ZIP yaratish
        new_zip_path = zip_path.parent / f"compressed_{zip_path.name}"
        with zipfile.ZipFile(new_zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
            for root, dirs, files in os.walk(temp_extract):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(temp_extract)
                    zip_ref.write(file_path, arcname)
        
        # Eski ZIP ni o'chirib, yangisini qo'yish
        zip_path.unlink()
        new_zip_path.rename(zip_path)
        
    finally:
        # Vaqtinchalik papkani tozalash
        shutil.rmtree(temp_extract, ignore_errors=True)
    
    return compressed_count


def compress_docx(input_path: Path, output_path: Path, quality: int = 85) -> Tuple[bool, str]:
    """
    DOCX faylini siqish (ZIP ichidagi rasmlarni siqish orqali)
    """
    try:
        # DOCX = ZIP format
        shutil.copy2(input_path, output_path)
        
        # ZIP ichidagi rasmlarni siqish
        compressed_images = compress_images_in_zip(output_path, quality)
        
        return True, f"✅ DOCX siqildi! {compressed_images} ta rasm optimallashtirildi."
    
    except Exception as e:
        logger.error(f"DOCX siqish xatosi: {e}")
        return False, f"❌ Xatolik: {str(e)}"


def compress_pptx(input_path: Path, output_path: Path, quality: int = 85) -> Tuple[bool, str]:
    """
    PPTX faylini siqish (ZIP ichidagi rasmlarni siqish orqali)
    """
    try:
        # PPTX = ZIP format
        shutil.copy2(input_path, output_path)
        
        # ZIP ichidagi rasmlarni siqish
        compressed_images = compress_images_in_zip(output_path, quality)
        
        return True, f"✅ PPTX siqildi! {compressed_images} ta rasm optimallashtirildi."
    
    except Exception as e:
        logger.error(f"PPTX siqish xatosi: {e}")
        return False, f"❌ Xatolik: {str(e)}"


def compress_pdf(input_path: Path, output_path: Path, quality: str = "screen") -> Tuple[bool, str]:
    """
    PDF faylini Ghostscript yordamida siqish
    quality: 'screen' (eng kichik), 'ebook', 'printer', 'prepress'
    """
    # Ghostscript mavjudligini tekshirish
    gs_paths = ["gs", "gswin64c", "gswin32c"]
    gs_cmd = None
    
    for path in gs_paths:
        if shutil.which(path):
            gs_cmd = path
            break
    
    if not gs_cmd:
        return False, "❌ Ghostscript topilmadi. PDF siqish uchun Ghostscript o'rnatishingiz kerak.\n\n" \
                      "Ubuntu/Debian: sudo apt install ghostscript\n" \
                      "Windows: https://ghostscript.com/releases/gsdnld.html\n" \
                      "Mac: brew install ghostscript"
    
    # PDF sifati sozlamalari
    quality_settings = {
        "screen": "/screen",   # 72 dpi, eng kichik
        "ebook": "/ebook",     # 150 dpi
        "printer": "/printer", # 300 dpi
        "prepress": "/prepress" # 300 dpi, yuqori sifat
    }
    
    gs_device = quality_settings.get(quality, "/screen")
    
    try:
        import subprocess
        
        cmd = [
            gs_cmd, "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS={gs_device}",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={output_path}",
            str(input_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode == 0 and output_path.exists():
            original_size = input_path.stat().st_size
            compressed_size = output_path.stat().st_size
            
            if compressed_size < original_size:
                reduction = (1 - compressed_size / original_size) * 100
                return True, f"✅ PDF siqildi! {format_size(compressed_size)} ({reduction:.1f}% kichraydi)"
            else:
                # Agar siqilmagan bo'lsa, originalni qaytarish yaxshiroq
                output_path.unlink()
                shutil.copy2(input_path, output_path)
                return True, "⚠️ PDF siqilmadi (fayl allaqachon optimallashtirilgan)"
        else:
            return False, f"❌ PDF siqishda xatolik: {result.stderr[:200]}"
            
    except subprocess.TimeoutExpired:
        return False, "❌ Vaqt tugadi! PDF juda katta bo'lishi mumkin."
    except Exception as e:
        logger.error(f"PDF siqish xatosi: {e}")
        return False, f"❌ Xatolik: {str(e)}"


def create_zip_archive(files: List[Path], zip_name: str) -> Path:
    """Siqilgan fayllarni ZIP arxivga joylashtirish"""
    zip_path = COMPRESSED_DIR / zip_name
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in files:
            if file.exists():
                zipf.write(file, file.name)
    
    return zip_path


def cleanup_old_files(max_age_hours: int = 24):
    """Eski vaqtinchalik fayllarni tozalash"""
    now = datetime.now().timestamp()
    
    for directory in [TEMP_DIR, COMPRESSED_DIR]:
        if not directory.exists():
            continue
            
        for file in directory.iterdir():
            if file.is_file():
                file_age = now - file.stat().st_mtime
                if file_age > max_age_hours * 3600:
                    try:
                        file.unlink()
                        logger.info(f"Tozalandi: {file.name}")
                    except Exception as e:
                        logger.warning(f"Tozalashda xatolik {file}: {e}")


# ========== TELEGRAM BOT HANDLERLARI ==========

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Start komandasi"""
    welcome_text = """
    📎 *File Compressor Bot*

    Menga DOCX, PPTX yoki PDF fayl yuboring, men uni siqib (compress) qilib,
    ZIPlab qaytarib beraman!

    *Qanday ishlaydi:*
    1. Menga fayl yuboring
    2. Men faylni siqaman
    3. Siqilgan faylni ZIP qilib qaytaraman

    *Natijalar:*
    - PDF: 50-80% gacha kichrayadi
    - DOCX/PPTX: Rasmlar siqiladi

    *Komandalar:*
    /start - Botni qayta ishga tushirish
    /help - Yordam
    /stats - Statistika

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
    • 📄 PDF - Ghostscript orqali siqiladi
    • 📝 DOCX - Ichidagi rasmlar siqiladi
    • 📊 PPTX - Ichidagi rasmlar siqiladi

    *PDF siqish darajalari:*
    - screen: eng kichik hajm (72 dpi)
    - ebook: o'rtacha (150 dpi)
    - printer: yuqori sifat (300 dpi)

    PDF uchun: /compress_pdf [screen|ebook|printer]

    *Cheklovlar:*
    • Maksimal fayl hajmi: 50MB
    • Bot faqat bitta faylni qabul qiladi
    
    /stats - Bot statistikasini ko'rish
    """
    await message.answer(help_text, parse_mode="Markdown")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Statistika komandasi"""
    # Vaqtinchalik fayllarni tozalash
    cleanup_old_files()
    
    # Papkalardagi fayllar soni
    temp_count = len(list(TEMP_DIR.glob("*"))) if TEMP_DIR.exists() else 0
    compressed_count = len(list(COMPRESSED_DIR.glob("*"))) if COMPRESSED_DIR.exists() else 0
    
    # Diskda band qilingan joy
    temp_size = sum(f.stat().st_size for f in TEMP_DIR.glob("*")) if TEMP_DIR.exists() else 0
    compressed_size = sum(f.stat().st_size for f in COMPRESSED_DIR.glob("*")) if COMPRESSED_DIR.exists() else 0
    
    stats_text = f"""
    📊 *Bot Statistikasi*
    
    ⏳ Kutilayotgan fayllar: {temp_count}
    📦 Siqilgan fayllar: {compressed_count}
    💾 Vaqtinchalik joy: {format_size(temp_size)}
    💿 Siqilgan fayllar hajmi: {format_size(compressed_size)}
    
    *Qo'llab-quvvatlanadigan formatlar:* PDF, DOCX, PPTX
    """
    await message.answer(stats_text, parse_mode="Markdown")


@dp.message(Command("compress_pdf"))
async def cmd_compress_pdf(message: Message):
    """PDF siqish darajasini o'zgartirish"""
    args = message.text.split()
    quality = args[1] if len(args) > 1 else "screen"
    
    if quality not in ["screen", "ebook", "printer", "prepress"]:
        await message.answer("❌ Noto'g'ri parametr! Qabul qilinadiganlar: screen, ebook, printer, prepress")
        return
    
    # Foydalanuvchi sozlamalarini saqlash (oddiy holat uchun dict)
    if not hasattr(cmd_compress_pdf, "user_settings"):
        cmd_compress_pdf.user_settings = {}
    
    cmd_compress_pdf.user_settings[message.from_user.id] = {"pdf_quality": quality}
    
    await message.answer(f"✅ PDF siqish darajasi '{quality}' ga o'rnatildi!")


@dp.message(lambda message: message.document)
async def handle_document(message: Message):
    """Fayl kelganda ishlov berish"""
    document = message.document
    file_name = document.file_name
    file_size = document.file_size
    
    # Fayl hajmini tekshirish
    if file_size > MAX_FILE_SIZE:
        await message.answer(f"❌ Fayl juda katta! Maksimal hajm: 50MB. Sizning faylingiz: {format_size(file_size)}")
        return
    
    # Fayl kengaytmasini tekshirish
    file_ext = Path(file_name).suffix.lower()
    
    if file_ext not in ['.pdf', '.docx', '.pptx']:
        await message.answer(f"❌ {file_ext} format qo'llab-quvvatlanmaydi.\n\n"
                              f"Qo'llab-quvvatlanadigan formatlar: .pdf, .docx, .pptx")
        return
    
    # Jarayon boshlanganligi haqida xabar
    status_msg = await message.answer(f"⏳ Fayl qabul qilindi. Siqish boshlanmoqda...\n"
                                       f"📁 {file_name} ({format_size(file_size)})")
    
    # Faylni yuklab olish
    file_id = document.file_id
    file = await bot.get_file(file_id)
    
    # Vaqtinchalik papkalarni yaratish
    ensure_directories()
    
    # Unikal fayl nomi yaratish
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    original_file = TEMP_DIR / f"original_{timestamp}_{file_name}"
    compressed_file = COMPRESSED_DIR / f"compressed_{timestamp}_{file_name}"
    
    try:
        # Faylni yuklab olish
        await bot.download_file(file.file_path, original_file)
        
        # Fayl turiga qarab siqish
        success = False
        result_message = ""
        
        if file_ext == '.pdf':
            # Foydalanuvchi sozlamalarini olish
            pdf_quality = "screen"
            if hasattr(cmd_compress_pdf, "user_settings"):
                user_settings = cmd_compress_pdf.user_settings.get(message.from_user.id, {})
                pdf_quality = user_settings.get("pdf_quality", "screen")
            
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
        
    except Exception as e:
        logger.error(f"Qayta ishlashda xatolik: {e}")
        await status_msg.edit_text(f"❌ Kutilmagan xatolik yuz berdi: {str(e)[:200]}")
    
    finally:
        # Tozalash
        try:
            if original_file.exists():
                original_file.unlink()
            if compressed_file.exists():
                compressed_file.unlink()
        except Exception as e:
            logger.warning(f"Tozalashda xatolik: {e}")


@dp.message()
async def handle_unknown(message: Message):
    """Tanilmagan xabarlar"""
    await message.answer("❓ Tushunarsiz buyruq. Yordam uchun /help yuboring.\n\n"
                         "Yoki menga to'g'ridan-to'g'ri DOCX, PPTX yoki PDF fayl yuboring!")


# ========== ASOSIY ISHGA TUSHIRISH ==========

async def main():
    """Botni ishga tushirish"""
    print("🤖 Bot ishga tushmoqda...")
    
    # Papkalarni tayyorlash
    ensure_directories()
    
    # Eski fayllarni tozalash (startda)
    cleanup_old_files()
    
    # Botni ishga tushirish
    await dp.start_polling(bot)
    
    print("✅ Bot ishga tushdi!")


if __name__ == "__main__":
    asyncio.run(main())
