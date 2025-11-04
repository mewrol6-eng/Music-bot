FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg build-essential libsndfile1 git --no-install-recommends \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

CMD ["bash", "start.sh"]
