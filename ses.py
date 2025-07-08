import time
from gtts import gTTS
import os

def sesli_okuma(metin):
    try:
        tts = gTTS(text=metin, lang='tr')
        tts.save("output.mp3")
        os.system("mpg123 output.mp3")
    except Exception as e:
        print("Sesli okuma hatası:", e)

def izleyici():
    son_metin = ""
    while True:
        try:
            with open("translated.txt", "r", encoding="utf-8") as f:
                metin = f.read().strip()
                if metin and metin != son_metin:
                    sesli_okuma(metin)
                    son_metin = metin
        except FileNotFoundError:
            pass
        time.sleep(1)

if __name__ == "__main__":
    izleyici()
