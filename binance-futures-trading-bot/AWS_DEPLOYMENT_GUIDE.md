# AWS EC2 Deployment Guide for Binance Trading Bot

## Quick Start Guide

Follow these steps to deploy the trading bot on AWS EC2 Free Tier.

---

## Step 1: Launch EC2 Instance

1. **Log in to AWS Console**
   - Go to https://console.aws.amazon.com/
   - Navigate to EC2 service

2. **Launch Instance**
   - Click "Launch Instance"
   - Choose settings:
     - **Name**: `trading-bot`
     - **AMI**: Ubuntu Server 22.04 LTS (Free tier eligible)
     - **Instance Type**: t2.micro (Free tier)
     - **Key Pair**: Create new or use existing (download .pem file)
     - **Network Settings**:
       - Allow SSH traffic from anywhere
       - Allow HTTP traffic
       - Allow HTTPS traffic
     - **Storage**: 20 GB gp3 (Free tier includes 30 GB)

3. **Launch the instance** and wait for it to start

---

## Step 2: Connect to Your Instance

### Option A: Using SSH (Recommended)
```bash
# Make key file secure (Linux/Mac)
chmod 400 your-key.pem

# Connect to instance
ssh -i your-key.pem ubuntu@your-instance-public-ip
```

### Option B: Using EC2 Instance Connect
- Select your instance in AWS Console
- Click "Connect" → "EC2 Instance Connect"

---

## Step 3: Deploy the Trading Bot

Once connected to your instance, run these commands:

```bash
# 1. Clone your repository
git clone https://github.com/[your-username]/trading-bot.git
cd trading-bot

# 2. Navigate to bot directory
cd binance-futures-trading-bot

# 3. Make deployment script executable
chmod +x aws_deploy.sh

# 4. Run deployment script
./aws_deploy.sh
```

The script will automatically:
- Install Python and dependencies
- Set up virtual environment
- Install all required packages
- Create a system service for the bot
- Start the bot and web interface
- Configure firewall

---

## Step 4: Access the Web Interface

1. **Find your public IP**:
   - Check AWS EC2 console for your instance's public IP
   - Or run: `curl http://169.254.169.254/latest/meta-data/public-ipv4`

2. **Open in browser**:
   ```
   http://your-instance-public-ip:5000
   ```

3. **Security Group Configuration** (if web interface doesn't load):
   - Go to EC2 → Security Groups
   - Find your instance's security group
   - Edit inbound rules
   - Add rule:
     - Type: Custom TCP
     - Port: 5000
     - Source: 0.0.0.0/0 (or your IP for better security)

---

## Step 5: Monitor and Manage

### View Logs
```bash
# Real-time logs
sudo journalctl -u trading-bot -f

# Last 100 lines
sudo journalctl -u trading-bot -n 100

# Trading logs
tail -f ~/trading-bot/binance-futures-trading-bot/trading-bot/trading_log.txt
```

### Service Management
```bash
# Check status
sudo systemctl status trading-bot

# Stop bot
sudo systemctl stop trading-bot

# Start bot
sudo systemctl start trading-bot

# Restart bot
sudo systemctl restart trading-bot

# Disable auto-start
sudo systemctl disable trading-bot
```

### Update Code
```bash
cd ~/trading-bot
git pull origin main
sudo systemctl restart trading-bot
```

---

## Alternative: Quick Deploy with Screen

If you prefer manual control without systemd:

```bash
# Install screen
sudo apt-get install screen -y

# Create new screen session
screen -S trading-bot

# Navigate to bot directory
cd ~/trading-bot/binance-futures-trading-bot

# Activate virtual environment
source venv/bin/activate

# Start the bot with monitor
python start_with_monitor.py

# Detach from screen: Press Ctrl+A, then D

# Reattach to screen
screen -r trading-bot
```

---

## Important Security Notes

1. **API Keys**:
   - Never commit API keys to GitHub
   - Use environment variables or secure files
   - The bot is configured for TESTNET by default

2. **Firewall**:
   - Only open necessary ports (22 for SSH, 5000 for web interface)
   - Consider using AWS Security Groups for IP whitelisting

3. **SSL/HTTPS** (Optional but recommended for production):
   - Set up nginx as reverse proxy
   - Use Let's Encrypt for free SSL certificate
   - Update security group to allow port 443

---

## Cost Considerations

**AWS Free Tier includes**:
- 750 hours/month of t2.micro instance (12 months)
- 30 GB storage
- 15 GB bandwidth

**After Free Tier**:
- t2.micro: ~$8-10/month
- Storage: ~$2/month
- Bandwidth: Varies by usage

---

## Troubleshooting

### Bot won't start
```bash
# Check Python version (needs 3.8+)
python3 --version

# Check logs
sudo journalctl -u trading-bot -n 50

# Reinstall dependencies
cd ~/trading-bot/binance-futures-trading-bot
source venv/bin/activate
pip install -r requirements.txt
```

### Web interface not accessible
```bash
# Check if service is running
sudo systemctl status trading-bot

# Check if port 5000 is listening
sudo netstat -tlnp | grep 5000

# Check firewall
sudo ufw status

# Check AWS Security Group for port 5000
```

### Permission errors
```bash
# Fix ownership
sudo chown -R ubuntu:ubuntu ~/trading-bot

# Fix permissions
chmod -R 755 ~/trading-bot
```

---

## Presentation Demo Steps

For your presentation, follow these steps:

1. **Show AWS Console**
   - Display running EC2 instance
   - Show it's using free tier

2. **Open Web Interface**
   - Navigate to `http://your-ec2-ip:5000`
   - Show real-time monitoring dashboard

3. **Demonstrate Features**
   - Show TESTNET balance
   - Display active trades (if any)
   - Show win/loss statistics
   - Display live logs

4. **Show Terminal (Optional)**
   - SSH into instance
   - Show bot running with `sudo systemctl status trading-bot`
   - Display logs with `sudo journalctl -u trading-bot -f`

5. **Highlight Key Points**
   - Automated trading on Binance Testnet
   - Real-time monitoring interface
   - Cloud deployment for 24/7 operation
   - Scalable architecture

---

## Support

If you encounter issues during deployment:
1. Check the logs first
2. Verify all dependencies are installed
3. Ensure API keys are correctly configured
4. Check network/firewall settings

Good luck with your presentation!