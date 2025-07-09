import os
import argostranslate.package
import argostranslate.translate

os.environ["ARGOS_PACKAGES_DIR"] = "/root/argos_packages"
os.makedirs("/root/argos_packages", exist_ok=True)

model_path = "/root/en_tr.argosmodel"

try:
    package = argostranslate.package.Package(model_path)
    package.install()
    print("Model kuruldu.")
except Exception as e:
    print("Kurulum hatası:", str(e))

# Test çeviri
try:
    installed_languages = argostranslate.translate.get_installed_languages()
    from_lang = next((l for l in installed_languages if l.code == "en"), None)
    to_lang = next((l for l in installed_languages if l.code == "tr"), None)

    if from_lang and to_lang:
        translation = from_lang.get_translation(to_lang)
        text = "Hello, how are you?"
        translated = translation.translate(text)
        print("İngilizce:", text)
        print("Türkçe:", translated)
    else:
        print("Dil modeli yüklenemedi.")
except Exception as e:
    print("Çeviri hatası:", str(e))
