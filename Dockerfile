# Use Python 3.11
FROM python:3.11

WORKDIR /app

# copy source
COPY . /app

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run bot
CMD ["python", "bot.py"]
