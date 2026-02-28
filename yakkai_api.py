from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import anthropic

load_dotenv()
app = Flask(__name__)

# Load Anthropic client
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY not set in .env")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
MODEL = os.getenv("YAKKAI_MODEL", "claude-haiku-4-5-20251001")

@app.route('/api/yakkai/chat', methods=['POST'])
def chat():
    data = request.json
    message = data.get('message')
    if not message:
        return jsonify({'error': 'No message provided'}), 400

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": message}]
        )
        reply = response.content[0].text
        return jsonify({'reply': reply})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)