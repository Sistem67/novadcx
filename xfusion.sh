#!/bin/bash

# FusionPBX Full Auto-Install Script for Ubuntu 20.04/22.04 LTS with Static IP
# Version: 1.6
# Author: Your Name
# Date: $(date +%Y-%m-%d)

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Static IP Configuration - BURAYI DÜZENLEYİN
STATIC_IP="192.168.1.3"  # Kendi sabit IP'nizi buraya girin
DOMAIN="$STATIC_IP"         # SSL olmadan IP üzerinden erişim

# Variables
FUSION_IP=$(hostname -I | awk '{print $1}')
DB_PASSWORD=$(openssl rand -hex 12)
FS_PASSWORD=$(openssl rand -hex 12)
ADMIN_PASSWORD=$(openssl rand -hex 8)

# Check if running as root
if [ "$(id -u)" != "0" ]; then
   echo -e "${RED}This script must be run as root${NC}" 1>&2
   exit 1
fi

# Check Ubuntu version
UBUNTU_VERSION=$(lsb_release -rs)
if [[ "$UBUNTU_VERSION" != "20.04" && "$UBUNTU_VERSION" != "22.04" ]]; then
    echo -e "${RED}This script is only for Ubuntu 20.04 or 22.04${NC}"
    exit 1
fi

# Welcome message
echo -e "${GREEN}"
echo "##########################################################"
echo "#          FusionPBX Full Automated Installation          #"
echo "#               Ubuntu $UBUNTU_VERSION LTS Server            #"
echo "#                Static IP: $STATIC_IP                #"
echo "##########################################################"
echo -e "${NC}"
sleep 3

# Update system
echo -e "${YELLOW}[1/10] Updating system packages...${NC}"
apt-get update -y
apt-get upgrade -y
apt-get install -y wget curl git

# Install dependencies
echo -e "${YELLOW}[2/10] Installing dependencies...${NC}"
apt-get install -y nginx
systemctl start nginx
systemctl enable nginx

# Install FusionPBX
echo -e "${YELLOW}[3/10] Installing FusionPBX...${NC}"
wget -O - https://raw.githubusercontent.com/fusionpbx/fusionpbx-install.sh/master/ubuntu.sh --no-check-certificate > /tmp/fusionpbx-install.sh
chmod +x /tmp/fusionpbx-install.sh

# Auto-answer the installation questions
echo -e "${YELLOW}[4/10] Running FusionPBX installer with auto-answers...${NC}"
export AUTO_ANSWER=true
export DB_PASSWORD=$DB_PASSWORD
export FS_PASSWORD=$FS_PASSWORD
/tmp/fusionpbx-install.sh

# Wait for services to start
echo -e "${YELLOW}[5/10] Waiting for services to start...${NC}"
sleep 10

# Set admin password
echo -e "${YELLOW}[6/10] Setting admin password...${NC}"
sudo -u postgres psql fusionpbx -c "UPDATE v_users SET password = md5('${ADMIN_PASSWORD}') WHERE username = 'admin';"
sudo -u postgres psql fusionpbx -c "UPDATE v_users SET salt = '' WHERE username = 'admin';"

# Configure firewall
echo -e "${YELLOW}[7/10] Configuring firewall...${NC}"
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 5060/tcp
ufw allow 5060/udp
ufw allow 5080/tcp
ufw allow 5080/udp
ufw allow 16384:32768/udp
echo "y" | ufw enable

# Configure Nginx for IP access
echo -e "${YELLOW}[8/10] Configuring Nginx for IP access...${NC}"
cat > /etc/nginx/sites-available/fusionpbx << EOL
server {
    listen 80;
    server_name $STATIC_IP;
    
    root /var/www/fusionpbx;
    index index.php index.html index.htm;
    
    location / {
        try_files \$uri \$uri/ /index.php?\$query_string;
    }
    
    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/var/run/php/php-fpm.sock;
    }
}
EOL

ln -s /etc/nginx/sites-available/fusionpbx /etc/nginx/sites-enabled/fusionpbx
rm -f /etc/nginx/sites-enabled/default
systemctl restart nginx

# Finalize installation
echo -e "${YELLOW}[9/10] Finalizing installation...${NC}"
systemctl restart freeswitch
systemctl restart php-fpm

# Update FusionPBX IP settings
echo -e "${YELLOW}[10/10] Updating FusionPBX IP settings...${NC}"
sudo -u postgres psql fusionpbx -c "UPDATE v_default_settings SET default_setting_value = '$STATIC_IP' WHERE default_setting_subcategory = 'domain';"
sudo -u postgres psql fusionpbx -c "UPDATE v_domains SET domain_name = '$STATIC_IP';"

# Installation complete
echo -e "${GREEN}"
echo "##########################################################"
echo "#           FusionPBX Installation Complete!             #"
echo "##########################################################"
echo ""
echo "Access Information:"
echo -e "${NC}"
echo -e "FusionPBX Admin URL: ${GREEN}http://$STATIC_IP${NC}"
echo -e "Username: ${GREEN}admin${NC}"
echo -e "Password: ${GREEN}$ADMIN_PASSWORD${NC}"
echo ""
echo -e "Database Password: ${GREEN}$DB_PASSWORD${NC}"
echo -e "FreeSWITCH Password: ${GREEN}$FS_PASSWORD${NC}"
echo ""
echo -e "${YELLOW}Important Notes:${NC}"
echo "1. You're accessing via HTTP (not HTTPS) because we're using direct IP"
echo "2. For production use, consider:"
echo "   - Setting up a proper domain name"
echo "   - Configuring SSL certificates"
echo "3. Change the admin password after first login!"
echo -e "${GREEN}"
echo "##########################################################"
echo ""
echo -e "${NC}"
