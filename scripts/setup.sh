#!/bin/bash
# Setup script for Linux/Wine

echo "🔧 Setting up SMC/ICT Trading Bot..."

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create necessary directories
touch logs/trades/.gitkeep
 touch logs/decisions/.gitkeep
touch logs/errors/.gitkeep
touch logs/performance/.gitkeep
touch data/historical/.gitkeep
touch data/cache/.gitkeep

# Copy example env
cp config/.env.example config/.env

echo "✅ Setup complete!"
echo "📝 Edit config/.env with your MT5 credentials"
