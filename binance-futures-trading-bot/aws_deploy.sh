#!/bin/bash

# AWS EC2 Deployment Script for Trading Bot
# Run this script on your EC2 instance after cloning the repository

echo "=========================================="
echo "Trading Bot AWS Deployment Script"
echo "=========================================="

# Update system
echo "Updating system packages..."
sudo apt-get update -y
sudo apt-get upgrade -y

# Install Python 3.10 or higher
echo "Installing Python..."
sudo apt-get install -y python3 python3-pip python3-venv

# Install git
echo "Installing git..."
sudo apt-get install -y git

# Create virtual environment
echo "Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install requirements
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Install screen for persistent sessions
echo "Installing screen..."
sudo apt-get install -y screen

# Create systemd service file
echo "Creating systemd service..."
sudo cat > /etc/systemd/system/trading-bot.service << EOF
[Unit]
Description=Binance Trading Bot with Web Monitor
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trading-bot/binance-futures-trading-bot
Environment="PATH=/home/ubuntu/trading-bot/venv/bin"
ExecStart=/home/ubuntu/trading-bot/venv/bin/python /home/ubuntu/trading-bot/binance-futures-trading-bot/start_with_monitor.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
sudo systemctl daemon-reload

# Enable and start the service
echo "Starting trading bot service..."
sudo systemctl enable trading-bot
sudo systemctl start trading-bot

# Open port 5000 for web interface
echo "Configuring firewall..."
sudo ufw allow 5000
sudo ufw allow 22
sudo ufw --force enable

# Display status
echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "Bot Status:"
sudo systemctl status trading-bot --no-pager

echo ""
echo "Access the web interface at:"
echo "http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):5000"
echo ""
echo "Useful commands:"
echo "  View logs: sudo journalctl -u trading-bot -f"
echo "  Stop bot: sudo systemctl stop trading-bot"
echo "  Start bot: sudo systemctl start trading-bot"
echo "  Restart bot: sudo systemctl restart trading-bot"
echo "=========================================="