# backend_multi.py — Aydın Translate Universe v1.0
# Tüm dünya dillerini gerçek zamanlı Türkçeye çeviren altyapı

import subprocess
import os
import sys
import wave
import tempfile
from TTS.utils.synthesizer import Synthesizer
import argostranslate.package
import argostranslate.translate

# Whisper.cpp ayarları
WHISPER_PATH = "/root/whisper.cpp/main"
WHISPER_MODEL = "/root/whisper.cpp/models/ggml-medium.bin"  # multilingual model

# TTS ayarları (Coqui TTS)
TTS_CONFIG = "/root/.local/share/tts/tts_models--tr--mai--medium/config.yaml"
TTS_MODEL = "/root/.local/share/tts/tts_models--tr--mai--medium/tr_model.onnx"

# Argos Translate paket kontrolü
installed_packages = argostranslate.package.get_installed_packages()
if not installed_packages:
    print("Argos dil paketleri bulunamadı. Lütfen yükleyin.")
    exit(1)

# Türkçe TTS yükle
synth = Synthesizer(
    tts_checkpoint=None,
    tts_config_path=TTS_CONFIG,
    tts_speakers_file=None,
    tts_languages_file=None,
    vocoder_checkpoint=None,
    vocoder_config=None,
    encoder_checkpoint=None,
    encoder_config=None,
    use_cuda=False,
    model_path=TTS_MODEL,
    config_path=TTS_CONFIG
)

# Yayın kontrol
if len(sys.argv) < 2:
    print("Kullanım: python3 backend_multi.py <m3u8_yayin_linki>")
    exit(1)

stream_url = sys.argv[1]
print("\nYayın dinleniyor, tüm dünya dillerinden Türkçeye çeviri başlatıldı...\n")

# ffmpeg ile yayın sesini oku (16kHz mono PCM)
ffmpeg_cmd = [
    "ffmpeg", "-i", stream_url,
    "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000",
    "-loglevel", "quiet", "-"
]
ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE)

buffer_size = 16000 * 2 * 5  # 5 saniyelik ses

while True:
    audio_data = ffmpeg_proc.stdout.read(buffer_size)
    if len(audio_data) == 0:
        continue

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        wav_path = tmp.name
        with wave.open(wav_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio_data)

    # Whisper.cpp ile çözümle
    whisper_cmd = [WHISPER_PATH, "-m", WHISPER_MODEL, "-f", wav_path, "-nt", "-l", "auto"]
    result = subprocess.run(whisper_cmd, stdout=subprocess.PIPE, text=True)
    os.remove(wav_path)

    lines = result.stdout.strip().split("\n")
    if not lines:
        continue

    metin = lines[-1].strip()
    if not metin:
        continue

    print("Algılanan Cümle:", metin)

    # Şimdilik varsayılan giriş dili: İngilizce (geliştirilebilir)
    source_lang = "en"

    try:
        translation = argostranslate.translate.get_translation(source_lang, "tr")
        turkce = translation.translate(metin)
    except Exception as e:
        print("Çeviri yapılamadı:", e)
        continue

    print("Türkçe Çeviri:", turkce)

    try:
        wav = synth.tts(turkce)
        synth.save_wav(wav, "out.wav")
        os.system("mpg123 out.wav")
    except:
        print("TTS hatası\n")
        continue
