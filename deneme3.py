import asyncio
import subprocess
import json
import os
import websockets
from vosk import Model, KaldiRecognizer, SpkModel
import fasttext
from googletrans import Translator
from edge_tts import Communicate

# === Ayarlar ===
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000
VOSK_MODEL_PATH = "/root/vosk-model-spk-0.4"  # Güncellenmiş küçük model
FASTTEXT_MODEL_PATH = "/root/lid.176.bin"
WS_PORT = 8000
TTS_VOICE = "tr-TR-EmelNeural"  # Kadın sesi

# === Model Yükleme ===
assert os.path.exists(VOSK_MODEL_PATH), "Vosk modeli eksik!"
assert os.path.exists(FASTTEXT_MODEL_PATH), "FastText modeli eksik!"

model = Model(VOSK_MODEL_PATH)
spk_model = SpkModel(os.path.join(VOSK_MODEL_PATH, "spk"))  # Konuşmacı tanıma modeli
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

# === TTS ile Türkçe sesli okuma ===
async def speak_turkish(text):
    try:
        tts = Communicate(text=text, voice=TTS_VOICE)
        await tts.save("output.mp3")
        os.system("mpg123 -q output.mp3")
        os.remove("output.mp3")  # Geçici dosyayı sil
    except Exception as e:
        print(f"TTS Hatası: {e}")

# === Konuşmacı vektörünü işle ===
def process_speaker_vector(spk):
    # Basit bir konuşmacı kimliği oluştur (daha gelişmiş bir sistem için kullanılabilir)
    return f"Konuşmacı-{hash(tuple(spk)) % 1000:03d}"

# === Ana akış: ses tanıma, çeviri, gönderim ===
async def recognize_and_translate(url):
    audio_stream = stream_audio(url)
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)
    recognizer.SetSpkModel(spk_model)  # Konuşmacı tanımayı etkinleştir
    buffer = ""
    last_speaker = ""

    while True:
        data = audio_stream.read(CHUNK_SIZE)
        if len(data) == 0:
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get('text', '').strip()
            spk = result.get('spk', None)  # Konuşmacı vektörü

            if text:
                current_speaker = process_speaker_vector(spk) if spk else "Bilinmeyen"
                
                # Konuşmacı değiştiyse buffer'ı temizle
                if current_speaker != last_speaker and buffer:
                    buffer = ""
                    print(f"\n[Yeni Konuşmacı]: {current_speaker}")
                
                last_speaker = current_speaker
                buffer += " " + text

                if len(buffer) >= 30:  # Daha küçük buffer boyutu
                    try:
                        # Dil tespiti
                        lang = ft_model.predict(buffer, k=1)[0][0].replace("__label__", "")
                        # Türkçeye çeviri
                        translated = translator.translate(buffer, src=lang, dest='tr').text

                        print(f"\n[{current_speaker} - {lang}]: {buffer}")
                        print(f"[Çeviri]: {translated}")

                        # WebSocket ile gönder
                        await send_subtitle(f"[{current_speaker}]: {translated}")

                        # Sesli okuma (Sadece ana konuşmacı için)
                        if "Konuşmacı-000" in current_speaker:  # Örnek filtreleme
                            await speak_turkish(translated)

                        buffer = ""
                    except Exception as e:
                        print(f"Çeviri hatası: {e}")
                        buffer = ""

# === Ana fonksiyon ===
async def main(url):
    print(f"WebSocket altyazı servisi başlatılıyor (port {WS_PORT})...")
    print(f"VOSK SPK Modeli yüklendi: Konuşmacı tanıma aktif")
    server = websockets.serve(subtitle_server, "0.0.0.0", WS_PORT)
    await server
    await recognize_and_translate(url)

# === Başlatıcı ===
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Vosk-SPK Çeviri Sistemi")
    parser.add_argument('--url', required=True, help='M3U8 yayın linki')
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Durduruldu.")
