import subprocess
import argparse
import uuid
import os
from faster_whisper import WhisperModel
from transformers import MarianMTModel, MarianTokenizer

def indir_ses(url, dosya="temp.wav", sure=10):
    cmd = [
        "ffmpeg", "-y",
        "-i", url,
        "-t", str(sure),
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        dosya
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def transcribe_ingilizce(dosya):
    model = WhisperModel("medium", compute_type="float32")
    segments, _ = model.transcribe(dosya)
    return [seg.text for seg in segments]

def cevir_tr(cumleler):
    tokenizer = MarianTokenizer.from_pretrained("Helsinki-NLP/opus-mt-en-tr")
    model = MarianMTModel.from_pretrained("Helsinki-NLP/opus-mt-en-tr")
    
    ceviriler = []
    for cumle in cumleler:
        inputs = tokenizer(cumle, return_tensors="pt", padding=True)
        translated = model.generate(**inputs)
        tr = tokenizer.decode(translated[0], skip_special_tokens=True)
        ceviriler.append(tr)
    return ceviriler

def altyazi_yazdir(en_cumleler, tr_cumleler):
    print("\nYENİ ALTYAZI:\n")
    for i in range(len(en_cumleler)):
        print("EN:", en_cumleler[i])
        print("TR:", tr_cumleler[i])
        print("-" * 40)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Yayın URL’si (.m3u8)")
    args = parser.parse_args()

    while True:
        wav = f"temp_{uuid.uuid4().hex}.wav"
        indir_ses(args.url, wav, sure=10)
        en_cumleler = transcribe_ingilizce(wav)
        tr_cumleler = cevir_tr(en_cumleler)
        altyazi_yazdir(en_cumleler, tr_cumleler)
        os.remove(wav)

if __name__ == "__main__":
    main()
