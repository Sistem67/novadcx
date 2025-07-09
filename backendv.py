import argparse
import subprocess
import fasttext
import json
from vosk import Model, KaldiRecognizer
from transformers import MarianMTModel, MarianTokenizer
from TTS.api import TTS

# ---------------------- Argüman: Yayın URL ----------------------
parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True, help="Yayın ses linki (m3u8, mp3 vs.)")
args = parser.parse_args()
url = args.url

# ---------------------- Model Yolları ----------------------
vosk_en_path = "/root/vosk-model-small-en-us-0.15"
vosk_tr_path = "/root/vosk-model-small-tr-0.3"
fasttext_path = "/root/lid.176.bin"
marian_model = "Helsinki-NLP/opus-mt-en-tr"
coqui_model = "pavoque/turkish-female-glow-tts"

# ---------------------- Model Yükleme ----------------------
print(">> Modeller yükleniyor...")
fasttext_model = fasttext.load_model(fasttext_path)
vosk_en = Model(vosk_en_path)
tokenizer = MarianTokenizer.from_pretrained(marian_model)
translator = MarianMTModel.from_pretrained(marian_model)
tts = TTS(coqui_model)

# ---------------------- FFMPEG ile Ses Akışı ----------------------
def stream_audio(link):
    cmd = [
        "ffmpeg", "-i", link,
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ac", "1", "-ar", "16000", "-"
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

# ---------------------- Dil Algılama ----------------------
def detect_language(text):
    pred = fasttext_model.predict(text)[0][0]
    return pred.replace("__label__", "")

# ---------------------- İngilizce → Türkçe ----------------------
def translate_to_tr(text):
    inputs = tokenizer([text], return_tensors="pt", padding=True, truncation=True)
    output = translator.generate(**inputs)
    return tokenizer.decode(output[0], skip_special_tokens=True)

# ---------------------- Türkçe Sesli Okuma ----------------------
def speak(text):
    wav_path = "/root/out.wav"
    tts.tts_to_file(text=text, file_path=wav_path)
    subprocess.run(["ffplay", "-nodisp", "-autoexit", wav_path],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ---------------------- Ana İşlem ----------------------
def run_backend():
    stream = stream_audio(url)
    rec = KaldiRecognizer(vosk_en, 16000)
    buffer = b""

    print(f">> Yayın dinleniyor: {url}")
    try:
        while True:
            data = stream.stdout.read(4000)
            if not data:
                break

            buffer += data
            if len(buffer) < 8000:
                continue

            if rec.AcceptWaveform(buffer):
                result = json.loads(rec.Result())
                buffer = b""
                text = result.get("text", "").strip()

                if not text:
                    continue

                print(f"[Tespit]: {text}")
                lang = detect_language(text)
                print(f"[Dil]: {lang}")

                if lang == "en":
                    tr = translate_to_tr(text)
                    print(f"[Türkçe]: {tr}")
                    speak(tr)
                elif lang == "tr":
                    print(f"[Türkçe]: {text}")
                    speak(text)
                else:
                    print(f"[!] Desteklenmeyen dil: {lang}")

    except KeyboardInterrupt:
        print(">> Durduruldu (Ctrl+C).")
    except Exception as e:
        print(">> Hata:", e)

# ---------------------- Başlat ----------------------
if __name__ == "__main__":
    run_backend()
