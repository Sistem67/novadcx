import os
import requests
import argostranslate.package
import argostranslate.translate

# Ayarlar
MODEL_URL = "https://www.argosopentech.com/datasets/en_tr.argosmodel"
PACKAGE_FILE = "/root/en_tr.argosmodel"
INSTALL_DIR = "/root/argos_packages"

# Ortam değişkeni: sistem dizinlerine yazmayı engeller
os.environ["ARGOS_PACKAGES_DIR"] = INSTALL_DIR
os.makedirs(INSTALL_DIR, exist_ok=True)

# Modeli indir
def download_model(url, path):
    if os.path.exists(path):
        print("[BILGI] Model zaten var:", path)
        return
    print("[INDIRME] Model indiriliyor...")
    r = requests.get(url)
    with open(path, "wb") as f:
        f.write(r.content)
    print("[OK] Indirme tamamlandi:", path)

# Modeli kur
def install_model(path):
    print("[KURULUM] Model kuruluyor:", path)
    package = argostranslate.package.Package(path)
    package.install()
    print("[OK] Model kuruldu.")

# Test çeviri
def test_translation():
    print("[TEST] Ornek ceviri yapiliyor...")
    installed_languages = argostranslate.translate.get_installed_languages()
    from_lang = next((l for l in installed_languages if l.code == "en"), None)
    to_lang = next((l for l in installed_languages if l.code == "tr"), None)
    if not from_lang or not to_lang:
        print("[HATA] EN -> TR ceviri modeli bulunamadi!")
        return
    translation = from_lang.get_translation(to_lang)
    text = "Hello, how are you?"
    translated = translation.translate(text)
    print("[EN]:", text)
    print("[TR]:", translated)

# Ana fonksiyon
def main():
    download_model(MODEL_URL, PACKAGE_FILE)
    install_model(PACKAGE_FILE)
    test_translation()

if __name__ == "__main__":
    main()
