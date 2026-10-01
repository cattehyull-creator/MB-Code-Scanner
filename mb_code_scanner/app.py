import os
import re
import sys
import csv
import threading
import queue
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = tk.Tk


APP_NAME = "MB Code Scanner"
VALID_PRODUCTS = {"ACP", "GBO", "CRP", "GBM", "GBT", "GBA"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

# More tolerant than the old exact regex. OCR often inserts spaces or
# changes hyphens, so extraction below works from OCR tokens too.
CODE_RE = re.compile(
    r"\bMB\s*[-–—]?\s*(ACP|GBO|CRP|GBM|GBT|GBA)\s*[-–—]?\s*([A-Z]{3})\s*[-–—]?\s*(\d{3})\b",
    re.IGNORECASE,
)


def resource_path(*parts):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def find_tesseract():
    candidates = [
        resource_path("tesseract", "tesseract.exe"),
        resource_path("tesseract", "bin", "tesseract.exe"),
        Path(os.environ.get("TESSERACT_PATH", "")),
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    for p in candidates:
        try:
            if p and p.is_file():
                return str(p)
        except Exception:
            pass
    return None


def normalize_ocr(text: str) -> str:
    text = text.upper()

    for dash in "‐‑‒–—―−﹘﹣－":
        text = text.replace(dash, "-")

    # Common OCR confusions.
    text = text.replace("M8", "MB")

    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*-\s*", "-", text)
    return text


def _similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


def _correct_product(token):
    token = re.sub(r"[^A-Z0-9]", "", token.upper())
    if token in VALID_PRODUCTS:
        return token

    # Small OCR errors such as G8T -> GBT.
    candidates = sorted(
        VALID_PRODUCTS,
        key=lambda x: _similar(token, x),
        reverse=True,
    )
    if candidates and _similar(token, candidates[0]) >= 0.67:
        return candidates[0]
    return None


def _correct_letters(token):
    token = re.sub(r"[^A-Z0-9]", "", token.upper())
    # Only accept 3-character groups. A few common digit/letter OCR swaps
    # are corrected because the middle group is known to be letters.
    if len(token) != 3:
        return None
    table = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B"})
    token = token.translate(table)
    if re.fullmatch(r"[A-Z]{3}", token):
        return token
    return None


def _correct_digits(token):
    token = re.sub(r"[^A-Z0-9]", "", token.upper())
    if len(token) != 3:
        return None
    table = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"})
    token = token.translate(table)
    if re.fullmatch(r"\d{3}", token):
        return token
    return None


def extract_codes(text: str):
    """
    Extract valid MB codes even when OCR outputs:
      MB - GBT - ACE - 711
      MB GBT ACE 711
      M8-G8T-ACE-7I1
    """
    normalized = normalize_ocr(text)
    found = []

    # First: normal flexible regex.
    for product, letters, digits in CODE_RE.findall(normalized):
        found.append(f"MB-{product.upper()}-{letters.upper()}-{digits}")

    # Second: token-based recovery. This is important when Tesseract loses
    # the hyphens or confuses a few characters.
    tokens = re.findall(r"[A-Z0-9]+", normalized)

    for i in range(len(tokens) - 3):
        first, second, third, fourth = tokens[i:i + 4]

        first = first.upper()
        if first not in {"MB", "M8"} and _similar(first, "MB") < 0.75:
            continue

        product = _correct_product(second)
        letters = _correct_letters(third)
        digits = _correct_digits(fourth)

        if product and letters and digits:
            found.append(f"MB-{product}-{letters}-{digits}")

    # Also handle cases where OCR combines MB/product into one token.
    for i in range(len(tokens) - 2):
        first, second, third = tokens[i:i + 3]
        first = first.upper()

        m = re.match(r"^(?:MB|M8)(ACP|GBO|CRP|GBM|GBT|GBA)$", first)
        if m:
            product = m.group(1)
            letters = _correct_letters(second)
            digits = _correct_digits(third)
            if letters and digits:
                found.append(f"MB-{product}-{letters}-{digits}")

    # De-duplicate while preserving order.
    result = []
    seen = set()
    for code in found:
        if code not in seen:
            seen.add(code)
            result.append(code)
    return result


def rotate_bound(image, angle):
    h, w = image.shape[:2]
    center = (w / 2, h / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos = abs(matrix[0, 0])
    sin = abs(matrix[0, 1])
    nw = int((h * sin) + (w * cos))
    nh = int((h * cos) + (w * sin))
    matrix[0, 2] += (nw / 2) - center[0]
    matrix[1, 2] += (nh / 2) - center[1]
    return cv2.warpAffine(
        image,
        matrix,
        (nw, nh),
        borderValue=255,
        flags=cv2.INTER_CUBIC,
    )


def deskew(gray):
    thresh = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 50:
        return gray

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # Do not trust the automatic angle on a very busy photo.
    if abs(angle) > 12:
        return gray

    return rotate_bound(gray, angle)


def make_variants(image):
    """
    Create several OCR-friendly versions:
    - original grayscale/upscaled
    - CLAHE contrast
    - Otsu
    - adaptive threshold
    - sharpened
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = deskew(gray)

    max_dim = max(gray.shape)
    scale = 2.5 if max_dim < 1800 else (2.0 if max_dim < 3000 else 1.5)

    up = cv2.resize(
        gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
    )

    # Improve local contrast for labels photographed in dim light.
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    contrast = clahe.apply(up)

    # Mild denoise while preserving letter edges.
    denoise = cv2.GaussianBlur(contrast, (3, 3), 0)

    otsu = cv2.threshold(
        denoise, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )[1]

    adaptive = cv2.adaptiveThreshold(
        denoise,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        9,
    )

    # Unsharp mask.
    sharp = cv2.addWeighted(up, 1.6, cv2.GaussianBlur(up, (0, 0), 1.2), -0.6, 0)

    return [up, contrast, sharp, otsu, adaptive]


def _ocr(variant, psm):
    config = (
        f"--oem 3 --psm {psm} "
        "-c preserve_interword_spaces=1 "
        "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789- "
    )
    return pytesseract.image_to_string(variant, config=config)


def process_image(path, tesseract_cmd):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError("File gambar tidak dapat dibaca")

    texts = []
    all_codes = []

    variants = make_variants(image)

    # Try several page segmentation modes because the label can be a small
    # isolated text area or be surrounded by cardboard/background text.
    for variant in variants:
        for psm in (11, 6, 12, 7):
            text = _ocr(variant, psm)
            texts.append(text)

            codes = extract_codes(text)
            for code in codes:
                if code not in all_codes:
                    all_codes.append(code)

            # A valid code is enough to stop expensive extra OCR passes.
            if all_codes:
                return all_codes, "\n".join(texts)

    return all_codes, "\n".join(texts)


class App(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1100x720")
        self.minsize(900, 600)
        self.configure(bg="#f5f7fb")
        self.files = []
        self.results = []
        self.queue = queue.Queue()
        self.running = False
        self._build_style()
        self._build_ui()
        self.after(100, self._poll_queue)

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Sub.TLabel", font=("Segoe UI", 10))
        style.configure(
            "Primary.TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(16, 9),
        )
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8))
        style.configure("Treeview", rowheight=30, font=("Segoe UI", 10))
        style.configure(
            "Treeview.Heading",
            font=("Segoe UI", 10, "bold"),
        )

    def _build_ui(self):
        root = ttk.Frame(self, padding=22)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text="OCR offline untuk membaca kode MB dari banyak foto",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 16))

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(0, 12))

        ttk.Button(
            actions,
            text="+ Tambah Foto",
            style="Primary.TButton",
            command=self.add_files,
        ).pack(side="left")

        ttk.Button(
            actions,
            text="Hapus Semua",
            command=self.clear_files,
        ).pack(side="left", padx=8)

        ttk.Button(
            actions,
            text="Export CSV",
            command=self.export_csv,
        ).pack(side="right")

        self.scan_btn = ttk.Button(
            actions,
            text="Scan Semua",
            style="Primary.TButton",
            command=self.start_scan,
        )
        self.scan_btn.pack(side="right", padx=8)

        drop = tk.Label(
            root,
            text="Drag & drop JPG / JPEG / PNG di sini",
            font=("Segoe UI", 12),
            bg="#ffffff",
            fg="#5b6575",
            relief="groove",
            bd=1,
            height=3,
        )
        drop.pack(fill="x", pady=(0, 12))
        self.drop = drop

        if DND_FILES is not None:
            drop.drop_target_register(DND_FILES)
            drop.dnd_bind("<<Drop>>", self._on_drop)

        middle = ttk.Panedwindow(root, orient="horizontal")
        middle.pack(fill="both", expand=True)

        left = ttk.Frame(middle, padding=(0, 0, 10, 0))
        right = ttk.Frame(middle, padding=(10, 0, 0, 0))
        middle.add(left, weight=1)
        middle.add(right, weight=2)

        ttk.Label(
            left,
            text="Foto",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 6))

        lf = ttk.Frame(left)
        lf.pack(fill="both", expand=True)

        self.file_list = tk.Listbox(
            lf,
            selectmode=tk.EXTENDED,
            font=("Segoe UI", 10),
            activestyle="none",
        )
        self.file_list.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(
            lf,
            orient="vertical",
            command=self.file_list.yview,
        )
        sb.pack(side="right", fill="y")
        self.file_list.configure(yscrollcommand=sb.set)

        ttk.Label(
            right,
            text="Hasil kode valid",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 6))

        cols = ("file", "codes", "status")
        self.tree = ttk.Treeview(right, columns=cols, show="headings")
        self.tree.heading("file", text="Foto")
        self.tree.heading("codes", text="Kode valid")
        self.tree.heading("status", text="Status")
        self.tree.column("file", width=260)
        self.tree.column("codes", width=300)
        self.tree.column("status", width=150)
        self.tree.pack(fill="both", expand=True)

        bottom = ttk.Frame(root)
        bottom.pack(fill="x", pady=(12, 0))

        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x")

        self.status_var = tk.StringVar(value="Siap. Tambahkan foto.")
        ttk.Label(
            bottom,
            textvariable=self.status_var,
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(6, 0))

    def _on_drop(self, event):
        try:
            paths = self.tk.splitlist(event.data)
            self.add_paths(paths)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Gagal membaca drop: {e}")

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Pilih foto",
            filetypes=[
                ("Image files", "*.jpg *.jpeg *.png"),
                ("All files", "*.*"),
            ],
        )
        self.add_paths(paths)

    def add_paths(self, paths):
        added = 0
        existing = {str(Path(x).resolve()).lower() for x in self.files}

        for p in paths:
            path = Path(p)
            if path.suffix.lower() in IMAGE_EXTS and path.exists():
                key = str(path.resolve()).lower()
                if key not in existing:
                    self.files.append(str(path))
                    existing.add(key)
                    self.file_list.insert("end", path.name)
                    added += 1

        self.status_var.set(
            f"{len(self.files)} foto dalam antrean. "
            f"{added} foto baru ditambahkan."
        )

    def clear_files(self):
        if self.running:
            return

        self.files.clear()
        self.results.clear()
        self.file_list.delete(0, "end")

        for item in self.tree.get_children():
            self.tree.delete(item)

        self.progress["value"] = 0
        self.status_var.set("Daftar foto dikosongkan.")

    def start_scan(self):
        if self.running:
            return

        if not self.files:
            messagebox.showinfo(
                APP_NAME,
                "Tambahkan minimal satu foto terlebih dahulu.",
            )
            return

        tesseract = find_tesseract()

        if not tesseract:
            messagebox.showerror(
                APP_NAME,
                "Tesseract OCR tidak ditemukan.\n\n"
                "Untuk versi EXE, folder 'tesseract' harus berada "
                "di samping aplikasi dan berisi tesseract.exe + "
                "data tessdata.",
            )
            return

        self.running = True
        self.scan_btn.configure(state="disabled")
        self.results = []

        for item in self.tree.get_children():
            self.tree.delete(item)

        self.progress["maximum"] = len(self.files)
        self.progress["value"] = 0

        threading.Thread(
            target=self._scan_worker,
            args=(tesseract,),
            daemon=True,
        ).start()

    def _scan_worker(self, tesseract):
        pytesseract.pytesseract.tesseract_cmd = tesseract

        total = len(self.files)
        workers = min(4, max(1, (os.cpu_count() or 2)))

        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_map = {
                ex.submit(process_image, p, tesseract): p
                for p in self.files
            }

            done = 0

            for future in as_completed(future_map):
                path = future_map[future]

                try:
                    codes, raw = future.result()
                    error = ""
                except Exception as e:
                    codes, raw, error = [], "", str(e)

                self.queue.put(
                    ("result", path, codes, error)
                )

                done += 1
                self.queue.put(("progress", done, total))

        self.queue.put(("done",))

    def _poll_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()

                if msg[0] == "result":
                    _, path, codes, error = msg
                    status = (
                        "ERROR"
                        if error
                        else ("Ditemukan" if codes else "Tidak ada kode")
                    )

                    self.results.append(
                        {
                            "file": path,
                            "codes": codes,
                            "status": status,
                            "error": error,
                        }
                    )

                    self.tree.insert(
                        "",
                        "end",
                        values=(
                            Path(path).name,
                            ", ".join(codes) if codes else "—",
                            status,
                        ),
                    )

                elif msg[0] == "progress":
                    _, done, total = msg
                    self.progress["value"] = done
                    self.status_var.set(
                        f"Memproses {done}/{total} foto..."
                    )

                elif msg[0] == "done":
                    self.running = False
                    self.scan_btn.configure(state="normal")

                    count = sum(
                        len(r["codes"]) for r in self.results
                    )

                    self.status_var.set(
                        f"Selesai. {count} kode valid ditemukan "
                        f"dari {len(self.files)} foto."
                    )

        except queue.Empty:
            pass

        self.after(100, self._poll_queue)

    def export_csv(self):
        if not self.results:
            messagebox.showinfo(
                APP_NAME,
                "Belum ada hasil scan untuk diekspor.",
            )
            return

        out = filedialog.asksaveasfilename(
            title="Simpan hasil",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="mb_code_results.csv",
        )

        if not out:
            return

        with open(
            out,
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as f:
            writer = csv.writer(f)
            writer.writerow(
                ["Foto", "Kode Valid", "Status"]
            )

            for r in self.results:
                if r["codes"]:
                    for code in r["codes"]:
                        writer.writerow(
                            [
                                Path(r["file"]).name,
                                code,
                                r["status"],
                            ]
                        )
                else:
                    writer.writerow(
                        [
                            Path(r["file"]).name,
                            "",
                            r["status"],
                        ]
                    )

        messagebox.showinfo(
            APP_NAME,
            f"Hasil disimpan ke:\n{out}",
        )


if __name__ == "__main__":
    app = App()
    app.mainloop()
