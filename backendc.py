import argparse
import asyncio
import json
import os
import subprocess

import fasttext
import torch
from fastapi import FastAPI, WebSocket
from transformers import MarianMTModel, MarianTokenizer
from vosk import Model, KaldiRecognizer
from piper import PiperVoice  # Düzeltilmiş import
from piper.utils import wav_to_pcm16  # Düzeltilmiş utils import

# Ayarlar
VOSK_MODEL_PATH = "vosk-model-small-tr-0.15"
FASTTEXT_MODEL_PATH = "lid.176.bin"
WS_HOST = "0.0.0.0"
WS_PORT = 8000
TARGET_LANG = "tr"  # Çeviri hedef dili: Türkçe

# Model yolları
PIPER_MODEL_DIR = os.path.expanduser("~/xdcx/models")
PIPER_MODEL_PATH = os.path.join(PIPER_MODEL_DIR, "tr_TR-fahrettin-medium.onnx")
PIPER_CONFIG_PATH = os.path.join(PIPER_MODEL_DIR, "tr_TR-fahrettin-medium.onnx.json")

print("Modeller yükleniyor...")

# FastText dil modeli yükle
ft_model = fasttext.load_model(FASTTEXT_MODEL_PATH)

# MarianMT çeviri modeli (İngilizce → Türkçe)
model_name = "Helsinki-NLP/opus-mt-en-tr"
tokenizer = MarianTokenizer.from_pretrained(model_name)
translation_model = MarianMTModel.from_pretrained(model_name)

# Vosk ses tanıma modeli
vosk_model = Model(VOSK_MODEL_PATH)
recognizer = KaldiRecognizer(vosk_model, 16000)

# Piper TTS modeli (CPU)
try:
    voice = PiperVoice.load(
        model_path=PIPER_MODEL_PATH,
        config_path=PIPER_CONFIG_PATH,
        use_cuda=False
    )
    print("Piper TTS modeli başarıyla yüklendi.")
except Exception as e:
    print(f"Piper TTS modeli yüklenemedi: {e}")
    raise

# FastAPI uygulaması ve WebSocket bağlantıları
app = FastAPI()
connected_clients = set()

async def broadcast(message):
    if connected_clients:
        await asyncio.wait([client.send_text(message) for client in connected_clients])

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    print(f"Yeni istemci bağlandı: {websocket.client}")
    try:
        while True:
            await websocket.receive_text()
    except Exception as e:
        print(f"İstemci hatası: {e}")
    finally:
        connected_clients.remove(websocket)
        print(f"İstemci ayrıldı: {websocket.client}")

async def run_ffmpeg(url, queue):
    command = [
        "ffmpeg",
        "-i", url,
        "-loglevel", "quiet",
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        "-f", "wav",
        "pipe:1"
    ]
    process = await asyncio.create_subprocess_exec(*command, stdout=subprocess.PIPE)
    print("ffmpeg başlatıldı...")
    try:
        while True:
            data = await process.stdout.read(4000)
            if not data:
                break
            await queue.put(data)
    except Exception as e:
        print(f"ffmpeg hatası: {e}")
    finally:
        process.kill()

async def speech_recognizer(queue):
    print("Ses tanıyıcı başlatıldı...")
    while True:
        data = await queue.get()
        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get("text", "").strip()
            if text:
                print(f"Tanındı: {text}")
                lang = ft_model.predict(text)[0][0].replace("__label__", "")
                print(f"Dil algılandı: {lang}")

                # Eğer hedef dil değilse çeviri yap
                if lang != TARGET_LANG and len(text.split()) > 1:
                    translated = translate_text(text)
                else:
                    translated = text

                print(f"Çeviri: {translated}")

                # Ses sentezi
                audio = synthesize_speech(translated)

                # WebSocket ile altyazı gönder
                msg = json.dumps({
                    "type": "subtitle",
                    "text": translated,
                })
                await broadcast(msg)

        queue.task_done()

def translate_text(text):
    batch = tokenizer(text, return_tensors="pt", padding=True)
    translated = translation_model.generate(**batch)
    tgt_text = tokenizer.decode(translated[0], skip_special_tokens=True)
    return tgt_text

def synthesize_speech(text):
    try:
        wav = voice.synthesize(text)
        pcm16 = wav_to_pcm16(wav)  # Düzeltilmiş utils kullanımı
        return pcm16
    except Exception as e:
        print(f"Ses sentezi hatası: {e}")
        return None

async def main(url):
    queue = asyncio.Queue()
    ffmpeg_task = asyncio.create_task(run_ffmpeg(url, queue))
    recog_task = asyncio.create_task(speech_recognizer(queue))

    import uvicorn
    config = uvicorn.Config(app, host=WS_HOST, port=WS_PORT, log_level="info")
    server = uvicorn.Server(config)

    await asyncio.gather(ffmpeg_task, recog_task, server.serve())

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Canlı altyazı, çeviri ve sesli çeviri backend")
    parser.add_argument("--url", type=str, required=True, help="M3U8 canlı yayın linki")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url))
    except KeyboardInterrupt:
        print("Sunucu kapatıldı.")
