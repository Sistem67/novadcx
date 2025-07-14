import asyncio
import subprocess
import json
import os
import websockets
from vosk import Model, KaldiRecognizer
import fasttext
from googletrans import Translator
from edge_tts import Communicate

# === Ayarlar ===
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
VOSK_MODEL_PATH = "/root/vosk-model-small-en-us-0.15"
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"
WS_PORT = 8000
TTS_VOICE = "tr-TR-EmelNeural"  # Kadın sesi için

# === Model Yükleme ===
assert os.path.exists(VOSK_MODEL_PATH), "Vosk modeli eksik!"
assert os.path.exists(FASTTEXT_MODEL_PATH), "FastText modeli eksik!"

model = Model(VOSK_MODEL_PATH)
ft_model = fasttext.load_model(FASTTEXT_MODEL_PATH)
translator = Translator()
clients = set()

# === WebSocket altyazı sunucusu ===
async def subtitle_server(websocket, path):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

async def send_subtitle(text):
    if clients:
        await asyncio.wait([client.send(text) for client in clients])

# === Yayın sesini ffmpeg ile oku ===
def stream_audio(url):
    return subprocess.Popen([
        'ffmpeg', '-i', url,
        '-loglevel', 'quiet',
        '-ar', str(SAMPLE_RATE),
        '-ac', '1', '-f', 's16le', '-'
    ], stdout=subprocess.PIPE).stdout

# === TTS ile Türkçe sesli okuma (Kadın sesi) ===
async def speak_turkish(text):
    try:
        tts = Communicate(text=text, voice=TTS_VOICE)
        await tts.save("output.mp3")
        os.system("mpg123 -q output.mp3")
    except Exception as e:
        print(f"TTS Hatası: {e}")

# === Ana akış: ses tanıma, çeviri, gönderim ===
async def recognize_and_translate(url):
    audio_stream = stream_audio(url)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)
    buffer = ""

    while True:
        data = audio_stream.read(CHUNK_SIZE)
        if len(data) == 0:
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get('text', '').strip()

            if text:
                buffer += " " + text

                if len(buffer) >= 40:
                    try:
                        # Dil tespiti
                        lang = ft_model.predict(buffer, k=1)[0][0].replace("__label__", "")
                        # Türkçeye çeviri
                        translated = translator.translate(buffer, src=lang, dest='tr').text

                        print(f"\n[Orijinal - {lang}]: {buffer}")
                        print(f"[Türkçe]: {translated}")

                        # WebSocket ile gönder
                        await send_subtitle(translated)

                        # Sesli okuma (Kadın sesi)
                        await speak_turkish(translated)

                        buffer = ""
                    except Exception as e:
                        print(f"Çeviri hatası: {e}")
                        buffer = ""

# === Ana fonksiyon ===
async def main(url):
    print(f"WebSocket altyazı servisi başlatılıyor (port {WS_PORT})...")
    server = websockets.serve(subtitle_server, "0.0.0.0", WS_PORT)
    await server
    await recognize_and_translate(url)

# === Başlatıcı ===
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Vosk-Multi Çeviri Sistemi")
    parser.add_argument('--url', required=True, help='M3U8 yayın linki')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Durduruldu.")
