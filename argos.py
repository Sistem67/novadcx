import argostranslate.package
import argostranslate.translate
import requests

def download_argos_model(url, filename):
    print(f"Model indiriliyor: {filename}")
    r = requests.get(url)
    with open(filename, "wb") as f:
        f.write(r.content)
    print("İndirme tamamlandı.")

def install_argos_model(package_path):
    print(f"Model kuruluyor: {package_path}")
    package = argostranslate.package.Package(package_path)
    package.install()
    print("Kurulum tamamlandı.")

def main():
    # Örnek: İngilizce → Türkçe model dosyası URL'si
    model_url = "https://argosopentech.com/models/en_tr.argosmodel"
    model_file = "en_tr.argosmodel"

    download_argos_model(model_url, model_file)
    install_argos_model(model_file)

    # Yüklü dilleri ve çeviri çiftlerini göster
    installed_languages = argostranslate.translate.get_installed_languages()
    for lang in installed_languages:
        print(f"Yüklü dil: {lang.name} ({lang.code})")

if __name__ == "__main__":
    main()
