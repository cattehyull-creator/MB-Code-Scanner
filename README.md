# MB Code Scanner

Aplikasi desktop Windows untuk OCR banyak foto secara offline dan hanya mengambil kode valid dengan format:

`MB-(ACP|GBO|CRP|GBM|GBT|GBA)-AAA-999`

Fitur:
- JPG/JPEG/PNG
- Banyak foto sekaligus
- Drag & drop
- OCR lokal/offline menggunakan Tesseract
- Normalisasi huruf kecil dan variasi dash
- Toleransi spasi di sekitar `-`
- Preprocessing dan deskew ringan
- Validasi kode ketat
- Export CSV

## Cara termudah membuat EXE
Lihat `BUILD_WITH_GITHUB.md`. Workflow GitHub Actions membangun paket Windows di runner Windows dan membundel Tesseract, sehingga pengguna akhir tidak perlu Python.

## Build lokal
Jika suatu saat memakai komputer Windows yang sudah memiliki Python + pip + Tesseract, gunakan `build_windows.bat`.
