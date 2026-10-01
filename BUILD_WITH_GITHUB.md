# Build MB Code Scanner tanpa install Python di PC kamu

Cara termudah untuk menghasilkan paket Windows `.exe` adalah memakai GitHub Actions. GitHub menyediakan Windows build machine untuk menjalankan Python + PyInstaller + Tesseract, lalu hasilnya dikirim kembali sebagai file ZIP.

## Yang dibutuhkan
- Akun GitHub gratis
- Project ini

## Langkah
1. Buat repository baru di GitHub, misalnya `mb-code-scanner`.
2. Upload **semua isi folder project ini** ke repository tersebut. Pastikan folder `.github/workflows/build-windows.yml` ikut ter-upload.
3. Buka tab **Actions** di repository.
4. Pilih workflow **Build MB Code Scanner (Windows EXE)**.
5. Klik **Run workflow** → **Run workflow**.
6. Tunggu sampai status selesai/berwarna hijau.
7. Buka run tersebut. Di bagian **Artifacts**, download `MB-Code-Scanner-Windows`.
8. Extract ZIP tersebut di Windows. Jalankan `MB Code Scanner.exe`.

Komputer yang menjalankan hasil akhir tidak perlu Python. Tesseract juga sudah dibundel ke dalam paket aplikasi.
