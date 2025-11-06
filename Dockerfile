# 🐧 Use Ubuntu base image
FROM ubuntu:22.04

# 🧰 Install Python 3.11 and required tools
RUN apt-get update && \
    apt-get install -y software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip && \
    rm -rf /var/lib/apt/lists/*

# 🏗️ Set working directory
WORKDIR /app

# 📦 Copy project files
COPY . .

# 🔧 Setup virtual environment and install dependencies
RUN python3.11 -m venv venv && \
    . venv/bin/activate && \
    pip install --upgrade pip && \
    pip install -r requirements.txt

# 🚀 Run the bot
CMD ["venv/bin/python", "main_bot.py"]
