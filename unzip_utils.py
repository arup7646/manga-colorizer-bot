"""
unzip_utils.py
==============
Handles extraction of any archive format containing manga images.

Supported input formats:
  .zip / .cbz  → standard zip
  .rar / .cbr  → RAR archive
  .7z          → 7-zip archive
  .tar.gz      → tar gzip
  .pdf         → extract pages as images
  .jpg/.png    → single image (treated as one page)

Output: sorted list of image file paths ready for colorization
"""

import os
import zipfile
import shutil
import subprocess
import tempfile
from pathlib import Path

# Supported image extensions
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}


def extract_all(input_path, dest_dir):
    """
    Extract all images from any archive format.
    Returns sorted list of absolute image paths.
    """
    ext = Path(input_path).suffix.lower()

    if ext in (".zip", ".cbz"):
        return _extract_zip(input_path, dest_dir)

    elif ext in (".rar", ".cbr"):
        return _extract_rar(input_path, dest_dir)

    elif ext == ".7z":
        return _extract_7z(input_path, dest_dir)

    elif ext in (".tar", ".gz", ".tgz"):
        return _extract_tar(input_path, dest_dir)

    elif ext == ".pdf":
        return _extract_pdf(input_path, dest_dir)

    elif ext in IMAGE_EXTS:
        return _extract_single_image(input_path, dest_dir)

    else:
        # Try as zip first, then as image
        try:
            return _extract_zip(input_path, dest_dir)
        except zipfile.BadZipFile:
            pass
        # Try as image
        if _is_image(input_path):
            return _extract_single_image(input_path, dest_dir)
        raise ValueError(f"Unsupported format: {ext}")


def _collect_images(directory):
    """Recursively collect all images from directory, sorted by path."""
    images = []
    for root, dirs, files in os.walk(directory):
        # Skip hidden folders like __MACOSX
        dirs[:] = [d for d in dirs if not d.startswith("__") and not d.startswith(".")]
        for f in files:
            if Path(f).suffix.lower() in IMAGE_EXTS:
                images.append(os.path.join(root, f))
    return sorted(images)


def _extract_zip(input_path, dest_dir):
    """Extract ZIP or CBZ archive."""
    with zipfile.ZipFile(input_path, "r") as z:
        # Filter out hidden files and directories
        for member in z.namelist():
            if not any(part.startswith("__") or part.startswith(".")
                      for part in Path(member).parts):
                z.extract(member, dest_dir)

    images = _collect_images(dest_dir)
    if not images:
        raise ValueError("No images found inside the archive!")
    print(f"[unzip] Extracted {len(images)} images from ZIP")
    return images


def _extract_rar(input_path, dest_dir):
    """Extract RAR or CBR archive using unrar."""
    try:
        result = subprocess.run(
            ["unrar", "x", "-y", "-o+", input_path, dest_dir + "/"],
            capture_output=True, text=True
        )
        if result.returncode not in (0, 1):
            raise RuntimeError(f"unrar failed: {result.stderr}")
    except FileNotFoundError:
        # Try 7z as fallback
        try:
            subprocess.run(
                ["7z", "x", input_path, f"-o{dest_dir}", "-y"],
                capture_output=True, check=True
            )
        except FileNotFoundError:
            raise RuntimeError(
                "RAR support needs 'unrar' or '7z'.\n"
                "Install: apt-get install unrar\n"
                "Or convert your CBR to CBZ first."
            )

    images = _collect_images(dest_dir)
    if not images:
        raise ValueError("No images found inside the RAR archive!")
    print(f"[unzip] Extracted {len(images)} images from RAR")
    return images


def _extract_7z(input_path, dest_dir):
    """Extract 7-zip archive."""
    try:
        subprocess.run(
            ["7z", "x", input_path, f"-o{dest_dir}", "-y"],
            capture_output=True, check=True
        )
    except FileNotFoundError:
        raise RuntimeError("7z support needs p7zip: apt-get install p7zip-full")

    images = _collect_images(dest_dir)
    if not images:
        raise ValueError("No images found inside the 7z archive!")
    print(f"[unzip] Extracted {len(images)} images from 7z")
    return images


def _extract_tar(input_path, dest_dir):
    """Extract tar/tar.gz archive."""
    import tarfile
    with tarfile.open(input_path) as t:
        t.extractall(dest_dir)

    images = _collect_images(dest_dir)
    if not images:
        raise ValueError("No images found inside the tar archive!")
    print(f"[unzip] Extracted {len(images)} images from tar")
    return images


def _extract_pdf(input_path, dest_dir):
    """Extract PDF pages as PNG images."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(input_path)
        images = []
        for i, page in enumerate(doc):
            pix  = page.get_pixmap(dpi=150)
            path = os.path.join(dest_dir, f"{i+1:04d}.png")
            pix.save(path)
            images.append(path)
        print(f"[unzip] Extracted {len(images)} pages from PDF")
        return images
    except ImportError:
        raise RuntimeError(
            "PDF support needs PyMuPDF: pip install PyMuPDF"
        )


def _extract_single_image(input_path, dest_dir):
    """Copy single image to dest_dir."""
    dest = os.path.join(dest_dir, os.path.basename(input_path))
    shutil.copy2(input_path, dest)
    print(f"[unzip] Single image: {os.path.basename(input_path)}")
    return [dest]


def _is_image(path):
    """Check if file is an image by reading magic bytes."""
    try:
        with open(path, "rb") as f:
            header = f.read(12)
        # JPEG
        if header[:2] == b"\xff\xd8":
            return True
        # PNG
        if header[:8] == b"\x89PNG\r\n\x1a\n":
            return True
        # WebP
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return True
    except Exception:
        pass
    return False


def repack_images(colored_dir, original_path, output_path):
    """
    Repack colorized images into same format as original.
    Returns final output path (may differ if CBR→CBZ conversion).
    """
    ext = Path(original_path).suffix.lower()

    if ext in (".zip", ".cbz", ".cbr", ".rar"):
        # CBR/RAR → repack as CBZ (can't create RAR without license)
        out_ext  = ".cbz" if ext in (".cbr", ".rar") else ext
        out_path = str(Path(output_path).with_suffix(out_ext))
        _pack_zip(colored_dir, out_path)
        return out_path

    elif ext == ".pdf":
        _pack_pdf(colored_dir, output_path)
        return output_path

    elif ext in IMAGE_EXTS:
        # Single image — just return first colored page
        pages = sorted(os.listdir(colored_dir))
        if pages:
            shutil.copy2(os.path.join(colored_dir, pages[0]), output_path)
        return output_path

    elif ext == ".7z":
        # Repack as zip since creating 7z needs extra tools
        out_path = str(Path(output_path).with_suffix(".zip"))
        _pack_zip(colored_dir, out_path)
        return out_path

    else:
        _pack_zip(colored_dir, output_path)
        return output_path


def _pack_zip(colored_dir, output_path):
    """Pack colored images into ZIP."""
    images = sorted(
        f for f in os.listdir(colored_dir)
        if Path(f).suffix.lower() in IMAGE_EXTS
    )
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in images:
            z.write(os.path.join(colored_dir, f), arcname=f)
    print(f"[repack] Packed {len(images)} images → {os.path.basename(output_path)}")


def _pack_pdf(colored_dir, output_path):
    """Pack colored images into PDF."""
    images = sorted(
        os.path.join(colored_dir, f)
        for f in os.listdir(colored_dir)
        if Path(f).suffix.lower() in IMAGE_EXTS
    )
    try:
        import img2pdf
        with open(output_path, "wb") as f:
            f.write(img2pdf.convert(images))
    except ImportError:
        from PIL import Image
        imgs = [Image.open(p).convert("RGB") for p in images]
        if imgs:
            imgs[0].save(output_path, "PDF", save_all=True, append_images=imgs[1:])
    print(f"[repack] Packed {len(images)} pages → PDF")


def output_filename(original_name):
    """Generate output filename with _colored suffix, same extension (CBR→CBZ)."""
    p    = Path(original_name)
    ext  = p.suffix.lower()
    base = p.stem
    if ext in (".cbr", ".rar"):
        ext = ".cbz"
    return base + "_colored" + ext
