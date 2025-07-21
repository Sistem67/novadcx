#!/bin/bash

set -e

echo "=== MP3 Auto-DJ Otomatik Kurulum Başlıyor ==="

# 1. Node.js kurulumu
echo "[1/11] Node.js kuruluyor..."
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
  apt-get install -y nodejs
else
  echo "Node.js zaten kurulu."
fi

# 2. Auto-DJ dizini oluştur
echo "[2/11] Auto-DJ dizini hazırlanıyor..."
mkdir -p /opt/auto-dj
cd /opt/auto-dj

# 3. package.json oluştur
echo "[3/11] package.json oluşturuluyor..."
cat > package.json <<EOF
{
  "name": "auto-dj",
  "version": "1.0.0",
  "description": "USB + Online Auto-DJ WebSocket Server",
  "main": "server.js",
  "dependencies": {
    "ws": "^8.13.0"
  }
}
EOF

# 4. server.js dosyası (online mp3 listeli)
echo "[4/11] server.js oluşturuluyor..."
cat > server.js <<'EOF'
const http = require('http');
const WebSocket = require('ws');
const fs = require('fs');
const path = require('path');

const mediaPath = '/mnt/usb';
const port = 8000;

// İnternetten dinlenecek mp3 URL listesi (buraya kendi linklerini ekle)
const onlineMP3s = [
  "https://www.example.com/music/song1.mp3",
  "https://www.example.com/music/song2.mp3"
];

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

wss.on('connection', ws => {
  const mp3s = [];

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

  // Online mp3 URL'lerini listeye ekle
  mp3s.push(...onlineMP3s);

  ws.send(JSON.stringify(mp3s));
});

server.listen(port, '0.0.0.0', () => {
  console.log(`Auto-DJ server port ${port} üzerinde çalışıyor.`);
});
EOF

# 5. index.html oluştur
echo "[5/11] index.html oluşturuluyor..."
cat > index.html <<'EOF'
<!DOCTYPE html>
<html>
<head><title>Auto-DJ</title></head>
<body>
  <h2>Auto-DJ WebSocket Player</h2>
  <audio id="player" controls autoplay></audio>
  <script>
    const socket = new WebSocket('ws://' + location.host);
    const player = document.getElementById('player');
    let playlist = [];
    let current = 0;

    socket.onmessage = function(event) {
      playlist = JSON.parse(event.data);
      playNext();
    };

    function playNext() {
      if (playlist.length === 0) return;
      if (current >= playlist.length) current = 0;
      player.src = playlist[current];
      player.play();
      current++;
    }

    player.addEventListener('ended', playNext);
  </script>
</body>
</html>
EOF

# 6. Bağımlılıkları yükle
echo "[6/11] Node.js bağımlılıkları yükleniyor..."
npm install

# 7. /mnt/usb dizini oluştur
echo "[7/11] /mnt/usb dizini hazırlanıyor..."
mkdir -p /mnt/usb

# 8. usb-autodj.sh scripti oluşturuluyor
echo "[8/11] usb-autodj.sh scripti oluşturuluyor..."
cat > /usr/local/bin/usb-autodj.sh <<'EOF'
#!/bin/bash
DEVICE="/dev/$1"
MOUNT_POINT="/mnt/usb"

if mountpoint -q "$MOUNT_POINT"; then
  umount "$MOUNT_POINT"
fi

sleep 1
mount "$DEVICE" "$MOUNT_POINT"

systemctl restart auto-dj.service
EOF
chmod +x /usr/local/bin/usb-autodj.sh

# 9. usb-autodj-remove.sh scripti oluşturuluyor
echo "[9/11] usb-autodj-remove.sh scripti oluşturuluyor..."
cat > /usr/local/bin/usb-autodj-remove.sh <<'EOF'
#!/bin/bash
MOUNT_POINT="/mnt/usb"

if mountpoint -q "$MOUNT_POINT"; then
  umount "$MOUNT_POINT"
fi

systemctl restart auto-dj.service
EOF
chmod +x /usr/local/bin/usb-autodj-remove.sh

# 10. udev kuralı oluşturuluyor
echo "[10/11] udev kuralı oluşturuluyor..."
cat > /etc/udev/rules.d/99-usb-autodj.rules <<'EOF'
ACTION=="add", KERNEL=="sd?1", RUN+="/usr/local/bin/usb-autodj.sh %k"
ACTION=="remove", KERNEL=="sd?1", RUN+="/usr/local/bin/usb-autodj-remove.sh"
EOF

# 11. systemd servis dosyası oluşturuluyor
echo "[11/11] systemd servisi oluşturuluyor..."
cat > /etc/systemd/system/auto-dj.service <<EOF
[Unit]
Description=Auto-DJ Node.js Server
After=network.target

[Service]
ExecStart=/usr/bin/node /opt/auto-dj/server.js
Restart=always
User=nobody
WorkingDirectory=/opt/auto-dj

[Install]
WantedBy=multi-user.target
EOF

# Servisi etkinleştir ve başlat
systemctl daemon-reload
systemctl enable auto-dj.service
systemctl start auto-dj.service

echo "Kurulum tamamlandı! Auto-DJ server http://<SUNUCU_IP>:8000 adresinde çalışıyor."
echo "USB belleğinizi takınız, otomatik algılanıp yayına başlayacaktır."
echo "Online mp3 URL listesi server.js içinden ayarlanabilir."
