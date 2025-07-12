from transformers import MarianMTModel, MarianTokenizer
from huggingface_hub import login
import os

# 1. AYARLAR (BU KISIMLARI KENDİ BİLGİLERİNİZLE DOLDURUN)
MODEL_ADI = "Helsinki-NLP/opus-mt-en-tr"  # İndirmek istediğiniz model
KAYIT_DIZINI = "indirilen_model"         # Modelin kaydedileceği klasör
HUGGINGFACE_TOKEN = "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"  # Sizin tokeniniz

# 2. HUGGING FACE GİRİŞİ
try:
    login(token=HUGGINGFACE_TOKEN)
    print("Hugging Face hesabına giriş yapıldı")
except Exception as e:
    print(f"Giriş hatası: {e}")
    exit()

# 3. MODEL İNDİRME
try:
    print(f"{MODEL_ADI} modeli indiriliyor...")
    
    # Tokenizer ve modeli indir
    tokenizer = MarianTokenizer.from_pretrained(MODEL_ADI)
    model = MarianMTModel.from_pretrained(MODEL_ADI)
    
    # Yerel diske kaydet
    os.makedirs(KAYIT_DIZINI, exist_ok=True)
    model.save_pretrained(KAYIT_DIZINI)
    tokenizer.save_pretrained(KAYIT_DIZINI)
    
    print(f"Model başarıyla indirildi: {KAYIT_DIZINI}")
    
    # TEST ÇEVİRİSİ
    orijinal_metin = "Hello, how are you?"
    girdi = tokenizer(orijinal_metin, return_tensors="pt")
    çeviri = model.generate(**girdi)
    çıktı = tokenizer.decode(çeviri[0], skip_special_tokens=True)
    
    print(f"Çeviri testi: '{orijinal_metin}' -> '{çıktı}'")

except Exception as e:
    print(f"Model indirme hatası: {e}")
