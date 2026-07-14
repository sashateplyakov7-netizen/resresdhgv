from flask import Flask, request
import requests
import os
TOKEN = os.getenv("BOT_TOKEN")
FLOWISE_URL = os.getenv("FLOWISE_URL")
app = Flask(__name__)
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = request.json
    if "message" not in data:
        return "ok"
    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "")
    response = requests.post(
        FLOWISE_URL,
        json={"question": text}
    )
    answer = response.json().get("text", "Ошибка Flowise")
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": answer
        }
    )
    return "ok"
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
