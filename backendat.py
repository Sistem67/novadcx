import sys
import subprocess
import json
import argparse
import fasttext
import argostranslate.translate
import argostranslate.package
import vosk

# --- FASTTEXT MODEL YOLU ---
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"  # FastText dil algılama modeli

# --- VOSK MODEL YOLU ---
VOSK_MODEL_PATH = "/root/vosk-model-small-en-us-0.15"  # Vosk modeli

def load_fasttext_model():
    try:
        model = fasttext.load_model(FASTTEXT_MODEL_PATH)
        return model
    except Exception as e:
        print(f"[ERROR] FastText modeli yüklenemedi: {e}")
        sys.exit(1)

def detect_language(model, text):
    labels, _ = model.predict(text)
    if labels:
        lang = labels[0].replace("__label__", "")
        return lang
    return None

def load_argos_translation(from_lang_code, to_lang_code="tr"):
    installed_languages = argostranslate.translate.get_installed_languages()
    from_lang = next((l for l in installed_languages if l.code == from_lang_code), None)
    to_lang = next((l for l in installed_languages if l.code == to_lang_code), None)
    if from_lang is None or to_lang is None:
        print(f"[ERROR] Argos Translate modeli yüklü değil: {from_lang_code} → {to_lang_code}")
        sys.exit(1)
    return from_lang.get_translation(to_lang)

def main():
    parser = argparse.ArgumentParser(description="Canlı yayından ses al, altyazı + çeviri yap.")
    parser.add_argument("--url", type=str, required=True, help="M3U8 canlı yayın linki")
    args = parser.parse_args()

    fasttext_model = load_fasttext_model()
    translation = None  # Sonradan ayarlanacak

    # ffmpeg komutu: m3u8 → 16kHz mono PCM raw ses
    ffmpeg_cmd = [
        "ffmpeg",
        "-i", args.url,
        "-loglevel", "quiet",
        "-ar", "16000",
        "-ac", "1",
        "-f", "s16le",
        "-"
    ]

    ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE)

    model = vosk.Model(VOSK_MODEL_PATH)
    rec = vosk.KaldiRecognizer(model, 16000)

    print("[INFO] Yayından ses alınıyor, altyazı ve çeviri başlıyor... Ctrl+C ile durdur.")

    try:
        while True:
            data = ffmpeg_process.stdout.read(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                result_json = rec.Result()
                result = json.loads(result_json)
                text = result.get("text", "").strip()
                if text:
                    print(f"Orijinal: {text}")
                    lang = detect_language(fasttext_model, text)
                    if lang:
                        # Yalnızca çeviri modelini dilden sonra yükle
                        if translation is None or translation.from_lang.code != lang:
                            translation = load_argos_translation(lang)
                        translated = translation.translate(text)
                        print(f"Çeviri ({lang} → tr): {translated}")
    except KeyboardInterrupt:
        print("\n[INFO] Program sonlandırıldı.")
    finally:
        ffmpeg_process.kill()


if __name__ == "__main__":
    main()
