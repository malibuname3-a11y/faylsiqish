import os
import zipfile
import shutil
import asyncio
import logging
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Tuple, List, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from PIL import Image

# ========== AVTOMATIK KONFIGURATSIYA ==========
def get_token() -> str:
    """Token ni avtomatik topish: ENV -> .env -> token.txt"""
    # 1. Environment variable
    token = os.environ.get("BOT_TOKEN")
    if token:
        return token
    # 2. .env fayl
    env_file = Path(".env")
    if env_file.exists():
        with open(env_file, "r") as f:
            for line in f:
                if line.startswith("BOT_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    # 3. token.txt
    token_file = Path("token.txt")
    if token_file.exists():
        with open(token_file, "r") as f:
            return f.read().strip()
    raise ValueError("❌ Token topilmadi! Iltimos, quyidagi usullardan birini ishlating:\n"
                     "1. export BOT_TOKEN='sizning_tokingiz'\n"
                     "2. .env fayl yarating: BOT_TOKEN=...\n"
                     "3. token.txt fayl yarating va ichiga token yozing")

def find_ghostscript() -> Optional[str]:
    """Ghostscript ni avtomatik topish: PATH -> standart papkalar"""
    # 1. PATH da qidirish
    for name in ["gswin64c", "gswin32c", "gs"]:
        gs = shutil.which(name)
        if gs:
            return gs
    # 2. Windows uchun maxsus joylar
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

# ========== GLOBAL O‘ZGARUVCHILAR ==========
BOT_TOKEN = get_token()
GHOSTSCRIPT = find_ghostscript()
TEMP_DIR = Path(os.environ.get("TEMP_DIR", tempfile.gettempdir())) / "file_compressor_bot"
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "compressed_files"))
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", 50 * 1024 * 1024))
DEFAULT_PDF_QUALITY = os.environ.get("PDF_QUALITY", "screen")
CLEANUP_HOURS = int(os.environ.get("CLEANUP_HOURS", 24))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Foydalanuvchi sozlamalari (PDF sifati)
user_quality = {}

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
    temp_extract = TEMP_DIR / f"extract_{datetime.now().timestamp()}"
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

def compress_docx_pptx(input_path: Path, output_path: Path, ftype: str) -> Tuple[bool, str]:
    try:
        shutil.copy2(input_path, output_path)
        img_count = compress_images_in_zip(output_path)
        orig = input_path.stat().st_size
        new = output_path.stat().st_size
        ratio = (1 - new/orig) * 100 if orig else 0
        return True, f"✅ {ftype} siqildi! {format_size(new)} ({ratio:.1f}% kichraydi)\n📸 {img_count} ta rasm optimallashtirildi."
    except Exception as e:
        return False, f"❌ Xatolik: {str(e)}"

def compress_pdf(input_path: Path, output_path: Path, quality: str = "screen") -> Tuple[bool, str]:
    if not GHOSTSCRIPT:
        return False, "❌ Ghostscript topilmadi! PDF siqish uchun uni o‘rnating:\n" \
                      "Windows: https://ghostscript.com/releases/gsdnld.html\n" \
                      "Ubuntu: sudo apt install ghostscript\n" \
                      "macOS: brew install ghostscript"
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
                return True, f"✅ PDF siqildi! {format_size(new)} ({ratio:.1f}% kichraydi)\n📊 Sifat: {q_names.get(quality, quality)}"
            else:
                output_path.unlink()
                shutil.copy2(input_path, output_path)
                return True, "⚠️ PDF siqilmadi (allaqachon optimallashtirilgan)"
        else:
            return False, f"❌ PDF xatosi: {r.stderr[:200] if r.stderr else 'Noma\'lum'}"
    except Exception as e:
        return False, f"❌ Xatolik: {str(e)}"

def create_zip(files: List[Path], zip_name: str) -> Path:
    zip_path = OUTPUT_DIR / zip_name
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            if f.exists():
                zf.write(f, f.name)
    return zip_path

def cleanup_old():
    now = datetime.now().timestamp()
    for d in [TEMP_DIR, OUTPUT_DIR]:
        if d.exists():
            for f in d.iterdir():
                if f.is_file() and now - f.stat().st_mtime > CLEANUP_HOURS * 3600:
                    try:
                        f.unlink()
                    except:
                        pass

# ========== BOT HANDLERLARI ==========
@dp.message(Command("start"))
async def start_cmd(m: Message):
    gs_status = "✅ mavjud" if GHOSTSCRIPT else "❌ topilmadi"
    await m.answer(f"""
📎 *File Compressor Bot*

Menga DOCX, PPTX yoki PDF fayl yuboring – siqib ZIPlab qaytaraman.

📌 *Qanday ishlaydi:*
1️⃣ Fayl yuboring
2️⃣ Siqish (PDF: Ghostscript, DOCX/PPTX: rasm siqish)
3️⃣ ZIP arxiv qilib qaytaraman

🔧 *Komandalar:*
/start – boshlash
/help – yordam
/stats – statistika
/quality [screen|ebook|printer|prepress] – PDF sifat

⚙️ Ghostscript: {gs_status}
""", parse_mode="Markdown")

@dp.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer("""
🤖 *Yordam*

• Faylni yuboring (PDF, DOCX, PPTX)
• PDF sifatini /quality bilan o‘zgartiring:
  - screen (eng kichik)
  - ebook (o‘rtacha)
  - printer (yuqori)
  - prepress (eng yuqori)
Misol: `/quality screen`
""", parse_mode="Markdown")

@dp.message(Command("quality"))
async def quality_cmd(m: Message):
    args = m.text.split()
    if len(args) != 2 or args[1] not in ["screen","ebook","printer","prepress"]:
        await m.answer("❌ Noto‘g‘ri! Ishlating: /quality screen|ebook|printer|prepress")
        return
    user_quality[m.from_user.id] = args[1]
    await m.answer(f"✅ PDF sifati '{args[1]}' ga o‘rnatildi")

@dp.message(Command("stats"))
async def stats_cmd(m: Message):
    ensure_dirs()
    cleanup_old()
    temp_cnt = sum(1 for _ in TEMP_DIR.iterdir() if _.is_file())
    out_cnt = sum(1 for _ in OUTPUT_DIR.iterdir() if _.is_file())
    temp_sz = sum(f.stat().st_size for f in TEMP_DIR.iterdir() if f.is_file())
    out_sz = sum(f.stat().st_size for f in OUTPUT_DIR.iterdir() if f.is_file())
    await m.answer(f"""
📊 *Statistika*
⏳ Kutuvchi fayllar: {temp_cnt}
📦 Siqilgan fayllar: {out_cnt}
💾 Vaqtinchalik hajm: {format_size(temp_sz)}
💿 Siqilgan hajm: {format_size(out_sz)}
🖨️ Ghostscript: {"✅ mavjud" if GHOSTSCRIPT else "❌ topilmadi"}
""", parse_mode="Markdown")

@dp.message(lambda m: m.document)
async def handle_doc(m: Message):
    doc = m.document
    fname = doc.file_name
    fsize = doc.file_size
    ext = Path(fname).suffix.lower()
    if fsize > MAX_FILE_SIZE:
        await m.answer(f"❌ Fayl juda katta! Maksimal {format_size(MAX_FILE_SIZE)}")
        return
    if ext not in ['.pdf','.docx','.pptx']:
        await m.answer(f"❌ {ext} format qo‘llab-quvvatlanmaydi. Faqat PDF, DOCX, PPTX.")
        return
    
    status = await m.answer(f"⏳ Ishlov berilmoqda...\n📁 {fname} ({format_size(fsize)})")
    ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    original = TEMP_DIR / f"orig_{ts}_{fname}"
    compressed = OUTPUT_DIR / f"comp_{ts}_{fname}"
    
    try:
        file = await bot.get_file(doc.file_id)
        await bot.download_file(file.file_path, original)
        
        if ext == '.pdf':
            qual = user_quality.get(m.from_user.id, DEFAULT_PDF_QUALITY)
            ok, msg = compress_pdf(original, compressed, qual)
        elif ext == '.docx':
            ok, msg = compress_docx_pptx(original, compressed, "DOCX")
        else:  # .pptx
            ok, msg = compress_docx_pptx(original, compressed, "PPTX")
        
        if not ok:
            await status.edit_text(msg)
            return
        
        zip_path = create_zip([compressed], f"compressed_{ts}_{Path(fname).stem}.zip")
        with open(zip_path, 'rb') as f:
            await m.answer_document(
                BufferedInputFile(f.read(), filename=zip_path.name),
                caption=f"{msg}\n\n📦 ZIP hajmi: {format_size(compressed.stat().st_size)}\n📎 Asl hajm: {format_size(fsize)}\n💾 Tejalgan: {format_size(fsize - compressed.stat().st_size)}"
            )
        await status.delete()
    except Exception as e:
        logger.exception("Xatolik")
        await status.edit_text(f"❌ Xatolik: {str(e)[:200]}")
    finally:
        for p in [original, compressed]:
            if p.exists(): p.unlink()
        if 'zip_path' in locals() and zip_path.exists(): zip_path.unlink()

@dp.message()
async def unknown(m: Message):
    await m.answer("❓ Tushunarsiz. /help yordam beradi yoki PDF/DOCX/PPTX fayl yuboring.")

# ========== ISHGA TUSHIRISH ==========
async def main():
    print("="*50)
    print("🤖 File Compressor Bot ishga tushmoqda...")
    print(f"✅ Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:] if len(BOT_TOKEN)>15 else ''}")
    if GHOSTSCRIPT:
        print(f"✅ Ghostscript topildi: {GHOSTSCRIPT}")
    else:
        print("❌ Ghostscript topilmadi! PDF siqish ishlamaydi.")
        print("   O‘rnatish uchun qo‘llanma: https://ghostscript.com/releases/gsdnld.html")
    ensure_dirs()
    cleanup_old()
    print(f"📁 Vaqtinchalik: {TEMP_DIR.absolute()}")
    print(f"📁 Chiqish: {OUTPUT_DIR.absolute()}")
    print("="*50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
