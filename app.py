from flask import Flask, request
import requests
import os

TOKEN = os.getenv("BOT_TOKEN")
FLOWISE_URL = os.getenv("FLOWISE_URL")

app = Flask(__name__)

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = request.json

    if not data or "message" not in data:
        return "ok"

    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "")

    try:
        response = requests.post(
            FLOWISE_URL,
            json={"question": text},
            timeout=30
        )

        print("Flowise status:", response.status_code)
        print("Flowise response:", response.text)

        response.raise_for_status()

        answer = response.json().get("text", "Flowise не вернул ответ")

    except Exception as e:
        print("Ошибка Flowise:", e)
        answer = f"Ошибка Flowise: {e}"

    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": answer
        },
        timeout=30
    )

    return "ok", 200

@app.route("/", methods=["GET"])
def index():
    return "Bot is running!", 200

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
