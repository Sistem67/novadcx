import subprocess
import os
import sys
import time
import wave
import tempfile
from llama_cpp import Llama
from TTS.utils.synthesizer import Synthesizer
import argostranslate.package
import argostranslate.translate

# MODEL YOLLARI
WHISPER_CPP_PATH = "/root/whisper.cpp/main"
WHISPER_MODEL = "/root/whisper.cpp/models/ggml-small.bin"
LLM_MODEL = "/root/llm_models/phi-2.gguf"
TTS_CONFIG = "/root/.local/share/tts/tts_models--tr--mai--medium/config.yaml"
TTS_MODEL = "/root/.local/share/tts/tts_models--tr--mai--medium/tr_model.onnx"

# LLM başlat
llm = Llama(model_path=LLM_MODEL, n_ctx=512)

def sadeleştir(metin):
    prompt = f"Aşağıdaki cümleyi sadeleştir:\n\"{metin}\"\nCevap:"
    try:
        yanit = llm(prompt, max_tokens=50, stop=["\n"])
        return yanit["choices"][0]["text"].strip()
    except:
        return metin

# Argos çeviri kontrolü
installed_packages = argostranslate.package.get_installed_packages()
if not installed_packages:
    print("Argos dil paketi kurulu değil.")
    exit(1)
translate = argostranslate.translate.get_translation("en", "tr")

# TTS yükle
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

# Yayın adresi kontrol
if len(sys.argv) < 2:
    print("Kullanım: python3 backend.py <m3u8_yayin_linki>")
    exit(1)

stream_url = sys.argv[1]
print("\nSistem başlatıldı, yayın dinleniyor...\n")

# ffmpeg ile yayından ses al (16kHz mono PCM)
ffmpeg_cmd = [
    "ffmpeg", "-i", stream_url,
    "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000",
    "-loglevel", "quiet", "-"
]
ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE)

buffer_size = 16000 * 2 * 5  # 5 saniyelik ses

while True:
    audio = ffmpeg_proc.stdout.read(buffer_size)
    if len(audio) == 0:
        continue

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        wav_path = tmp.name
        with wave.open(wav_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio)

    whisper_cmd = [WHISPER_CPP_PATH, "-m", WHISPER_MODEL, "-f", wav_path, "-nt"]
    whisper_out = subprocess.run(whisper_cmd, stdout=subprocess.PIPE, text=True)
    os.remove(wav_path)

    lines = whisper_out.stdout.strip().split("\n")
    if not lines:
        continue
    metin = lines[-1].strip()
    if not metin:
        continue

    sade = sadeleştir(metin)
    tr = translate.translate(sade)

    print("Orijinal :", metin)
    print("Sade     :", sade)
    print("Türkçe   :", tr)
    print()

    try:
        wav = synth.tts(tr)
        synth.save_wav(wav, "out.wav")
        os.system("mpg123 out.wav")
    except:
        print("TTS hatası, cümle atlandı.\n")
