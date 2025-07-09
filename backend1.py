import os
import subprocess
import wave
import json
import argparse
from vosk import Model, KaldiRecognizer
from googletrans import Translator
from gtts import gTTS
import fasttext

# ------------------ Ayarlar ------------------ #
VOSK_MODEL_PATH = "vosk-model-small-tr-0.3"  # Senin mevcut klasörün
AUDIO_FILE = "temp.wav"
LANG_TARGET = "en"  # Çeviri dili: "en" İngilizce
FASTTEXT_MODEL_PATH = "cc.tr.300.bin"  # FastText Türkçe modeli
# -------------------------------------------- #

# Vosk modelini yükle
vosk_model = Model(VOSK_MODEL_PATH)
recognizer = KaldiRecognizer(vosk_model, 16000)

# FastText modelini yükle
ft_model = fasttext.load_model(FASTTEXT_MODEL_PATH)

# Google Translate başlat
translator = Translator()

# Yayın sesini indir
def download_audio_from_stream(url):
    print("Ses yayından indiriliyor...")
    command = [
        "ffmpeg", "-y", "-i", url,
        "-ar", "16000", "-ac", "1", "-f", "wav", AUDIO_FILE
    ]
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Ses indirildi:", AUDIO_FILE)

# Vosk ile ses → yazı
def transcribe_audio():
    wf = wave.open(AUDIO_FILE, "rb")
    results = []
    while True:
        data = wf.readframes(4000)
        if len(data) == 0:
            break
        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())["text"]
            results.append(result)
    final_result = json.loads(recognizer.FinalResult())["text"]
    results.append(final_result)
    return " ".join(results)

# FastText analizi
def fasttext_analysis(text):
    words = text.split()
    for word in words[:5]:
        print("Kelime:", word)
        neighbors = ft_model.get_nearest_neighbors(word)
        for score, similar in neighbors[:3]:
            print("   Benzer:", similar, "Skor:", score)

# Google Translate ile çeviri
def translate_text(text, target_lang):
    translated = translator.translate(text, dest=target_lang)
    return translated.text

# gTTS ile sesli okuma
def speak(text, lang):
    tts = gTTS(text=text, lang=lang)
    tts.save("tts.mp3")
    os.system("ffplay -nodisp -autoexit tts.mp3")

# ------------------ Ana Akış ------------------ #
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Yayın linki (.m3u8 uzantılı)")
    args = parser.parse_args()

    download_audio_from_stream(args.url)

    print("\n--- Altyazı (Türkçe metin) ---")
    original_text = transcribe_audio()
    print(original_text)

    print("\n--- FastText Benzerlik Analizi ---")
    fasttext_analysis(original_text)

    print("\n--- Çeviri (İngilizce) ---")
    translated_text = translate_text(original_text, LANG_TARGET)
    print(translated_text)

    print("\n--- Sesli Okuma Başlatılıyor ---")
    speak(translated_text, LANG_TARGET)
