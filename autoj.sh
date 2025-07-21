#!/bin/bash

# Otomatik Radyo Sunucusu Kurulumu

echo "Güncellemeler yapılıyor..."
sudo apt update && sudo apt install -y nodejs npm curl git

echo "Cheerio ve Axios kuruluyor..."
sudo npm install -g cheerio axios

echo "Çalışma klasörü oluşturuluyor..."
mkdir -p ~/auto-dj
cd ~/auto-dj

echo "Node.js sunucu dosyası oluşturuluyor..."

cat <<EOF > server.js
const http = require('http');
const WebSocket = require('ws');
const fs = require('fs');
const path = require('path');
const axios = require('axios');
const cheerio = require('cheerio');

const mediaPath = '/mnt/usb';
const port = 8000;
const externalURL = 'https://siteadresi.com/index/of/mp3/'; // 🔁 BURAYI DEĞİŞTİR

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
  const mp3s = [];

  // 1. USB mp3
  function walk(dir) {
    if (!fs.existsSync(dir)) return;
    fs.readdirSync(dir).forEach(file => {
      const full = path.join(dir, file);
      if (fs.statSync(full).isDirectory()) {
        walk(full);
      } else if (full.toLowerCase().endsWith('.mp3')) {
        mp3s.push(full.replace(mediaPath + '/', ''));
      }
    });
  }
  walk(mediaPath);

  // 2. Online mp3
  try {
    const { data } = await axios.get(externalURL);
    const \$ = cheerio.load(data);
    $('a').each((_, el) => {
      const href = \$(el).attr('href');
      if (href && href.endsWith('.mp3')) {
        mp3s.push(externalURL + href);
      }
    });
  } catch (e) {
    console.log('Online mp3 listesi alınamadı:', e.message);
  }

  ws.send(JSON.stringify(mp3s));
});

server.listen(port, '0.0.0.0', () => {
  console.log(\`Auto-DJ server çalışıyor → http://localhost:\${port}\`);
});
EOF

echo "HTML arayüzü oluşturuluyor..."

cat <<EOF > index.html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Auto DJ</title>
</head>
<body>
  <h2>Çalan Parçalar</h2>
  <ul id="list"></ul>
  <audio id="player" controls autoplay></audio>

  <script>
    const ws = new WebSocket("ws://" + location.hostname + ":8000");
    const player = document.getElementById("player");
    const list = document.getElementById("list");
    let tracks = [], current = 0;

    ws.onmessage = event => {
      tracks = JSON.parse(event.data);
      tracks.forEach((t, i) => {
        const li = document.createElement("li");
        li.textContent = t;
        list.appendChild(li);
      });
      play();
    };

    function play() {
      if (current >= tracks.length) current = 0;
      const src = tracks[current];
      player.src = src.startsWith("http") ? src : "/mnt/usb/" + src;
      player.play();
    }

    player.onended = () => {
      current++;
      play();
    };
  </script>
</body>
</html>
EOF

echo "Auto DJ başlatılıyor..."
node server.js
