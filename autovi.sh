#!/bin/bash

echo "Güncelleniyor ve gerekli paketler kuruluyor..."
sudo apt update && sudo apt install -y nodejs npm curl git

echo "Node.js global paketleri kuruluyor..."
sudo npm install -g cheerio axios ws

echo "Çalışma dizini oluşturuluyor..."
mkdir -p ~/auto-video
cd ~/auto-video

echo "Server dosyası oluşturuluyor..."

cat > server.js << 'EOF'
const http = require('http');
const WebSocket = require('ws');
const fs = require('fs');
const path = require('path');
const axios = require('axios');
const cheerio = require('cheerio');

const mediaPath = '/mnt/usb';   // USB mount noktası
const port = 8500;
const externalURL = 'https://siteadresi.com/index/of/videos/'; // BURAYI kendi video dizin URL'nle değiştir

const server = http.createServer((req, res) => {
  if (req.url === '/') {
    fs.createReadStream('index.html').pipe(res);
  } else {
    const filePath = path.join(__dirname, req.url);
    fs.access(filePath, fs.constants.R_OK, err => {
      if (err) {
        res.statusCode = 404;
        res.end('Dosya bulunamadı');
      } else {
        fs.createReadStream(filePath).pipe(res);
      }
    });
  }
});

const wss = new WebSocket.Server({ server });

wss.on('connection', async (ws) => {
  const videos = [];

  // USB içindeki video dosyalarını bul (.mp4 ve .m3u8)
  function walk(dir) {
    if (!fs.existsSync(dir)) return;
    fs.readdirSync(dir).forEach(file => {
      const full = path.join(dir, file);
      if (fs.statSync(full).isDirectory()) {
        walk(full);
      } else if (full.toLowerCase().endsWith('.mp4') || full.toLowerCase().endsWith('.m3u8')) {
        videos.push(full.replace(mediaPath + '/', ''));
      }
    });
  }
  walk(mediaPath);

  // Online dizinden video linklerini çek (.mp4 ve .m3u8)
  try {
    const { data } = await axios.get(externalURL);
    const $ = cheerio.load(data);
    $('a').each((_, el) => {
      const href = $(el).attr('href');
      if (href && (href.toLowerCase().endsWith('.mp4') || href.toLowerCase().endsWith('.m3u8'))) {
        videos.push(externalURL + href);
      }
    });
  } catch (e) {
    console.log('Online video listesi alınamadı:', e.message);
  }

  ws.send(JSON.stringify(videos));
});

server.listen(port, '0.0.0.0', () => {
  console.log(`Auto-Video server port ${port} üzerinde çalışıyor.`);
});
EOF

echo "HTML video player oluşturuluyor..."

cat > index.html << 'EOF'
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Auto Video Player</title>
  <style>
    body { background: #111; color: #eee; font-family: Arial, sans-serif; margin: 0; padding: 0; }
    #list { max-height: 200px; overflow-y: auto; padding: 10px; background: #222; margin: 0; }
    #list li { padding: 5px; cursor: pointer; }
    #list li:hover, #list li.active { background: #555; }
    video { width: 100%; height: auto; background: black; display: block; margin: 0; }
  </style>
</head>
<body>
  <video id="player" controls autoplay></video>
  <ul id="list"></ul>

  <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
  <script>
    const ws = new WebSocket("ws://" + location.hostname + ":8500");
    const player = document.getElementById("player");
    const list = document.getElementById("list");
    let videos = [], current = 0;
    let hls;

    ws.onmessage = event => {
      videos = JSON.parse(event.data);
      list.innerHTML = "";
      videos.forEach((video, i) => {
        const li = document.createElement("li");
        li.textContent = video;
        li.onclick = () => { current = i; play(); };
        list.appendChild(li);
      });
      play();
    };

    function play() {
      if (videos.length === 0) return;
      if (current >= videos.length) current = 0;

      // Aktif liste elemanını vurgula
      [...list.children].forEach((li, i) => {
        li.classList.toggle('active', i === current);
      });

      const src = videos[current];
      if (hls) {
        hls.destroy();
        hls = null;
      }

      if (src.endsWith('.m3u8')) {
        if (Hls.isSupported()) {
          hls = new Hls();
          hls.loadSource(src);
          hls.attachMedia(player);
          hls.on(Hls.Events.MANIFEST_PARSED, () => {
            player.play();
          });
        } else if (player.canPlayType('application/vnd.apple.mpegurl')) {
          player.src = src;
          player.play();
        } else {
          alert('Tarayıcınız HLS (m3u8) formatını desteklemiyor.');
        }
      } else {
        player.src = src.startsWith("http") ? src : "/mnt/usb/" + src;
        player.play();
      }
    }

    player.onended = () => {
      current++;
      play();
    };
  </script>
</body>
</html>
EOF

echo "Node.js video server başlatılıyor..."
node server.js
