import asyncio
import subprocess
import json
import websockets
from vosk import Model, KaldiRecognizer
from googletrans import Translator
from langdetect import detect
import os
import pyttsx3  # Sesli okuma (TTS)

# --- Ayarlar ---
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
model_path = "/root/vosk-model-small-tr-0.3"  # Türkçe Vosk model yolu

# --- Hazırlıklar ---
if not os.path.exists(model_path):
    raise FileNotFoundError(f"Vosk model yolu bulunamadı: {model_path}")

model = Model(model_path)
translator = Translator()
clients = set()

# --- TTS Motoru ---
tts_engine = pyttsx3.init()

# Türkçe sesi seç (espeak veya sistem sesi)
for voice in tts_engine.getProperty('voices'):
    if 'tr' in voice.id or 'turkish' in voice.name.lower():
        tts_engine.setProperty('voice', voice.id)
        print(f"TTS dili Türkçe olarak ayarlandı: {voice.name}")
        break

tts_engine.setProperty('rate', 150)
tts_engine.setProperty('volume', 0.9)

# --- Websocket Sunucusu ---
async def subtitle_server(websocket, path):
    clients.add(websocket)
    print(f"[+] Websocket bağlandı: {websocket.remote_address}")
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)
        print(f"[-] Websocket ayrıldı: {websocket.remote_address}")

async def send_subtitle(text):
    if clients:
        await asyncio.wait([client.send(text) for client in clients])

# --- ffmpeg ile ses akışı ---
def stream_audio(url):
    return subprocess.Popen([
        'ffmpeg',
        '-i', url,
        '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE),
        '-ac', '1',
        '-f', 's16le',
        '-'
    ], stdout=subprocess.PIPE).stdout

# --- Ana işlev: Dinle → Çevir → Yolla + Sesli Oku ---
async def recognize_and_translate(url):
    audio_stream = stream_audio(url)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)
    loop = asyncio.get_event_loop()

    while True:
        data = audio_stream.read(CHUNK_SIZE)
        if len(data) == 0:
            print("[!] Ses verisi alınamıyor, yayın bitmiş olabilir.")
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            original_text = result.get('text', '').strip()

            if original_text:
                try:
                    detected_lang = detect(original_text)
                    print(f"[?] Tespit edilen dil: {detected_lang}")
                    
                    # Otomatik çeviri: TR değilse → TR, TR ise → EN
                    if detected_lang == 'tr':
                        translated = translator.translate(original_text, dest='en').text
                    else:
                        translated = translator.translate(original_text, dest='tr').text

                    print(f"[✔️] Çeviri: {translated}")
                    await send_subtitle(translated)

                    # Sesli okuma
                    loop.run_in_executor(None, tts_engine.say, translated)
                    loop.run_in_executor(None, tts_engine.runAndWait)

                except Exception as e:
                    print(f"[X] Hata: {e}")

# --- Giriş Noktası ---
async def main(url):
    server = websockets.serve(subtitle_server, "0.0.0.0", 8000)
    await server
    print("[🚀] Websocket 8000 portunda başlatıldı.")
    await recognize_and_translate(url)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Canlı yayından otomatik altyazı ve sesli okuma")
    parser.add_argument('--url', required=True, help='Canlı yayın M3U8 linki')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("\n[!] Program elle durduruldu.")
