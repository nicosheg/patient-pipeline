import os, json, gspread, requests
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request, jsonify
from datetime import datetime, timezone

app = Flask(__name__)

# ---- CONFIG ----
AI_API_KEY = os.environ['AI_API_KEY']
AI_API_URL = 'https://api.groq.com/openai/v1/chat/completions'
AI_MODEL = 'llama-3.3-70b-versatile'
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = '7035558775'
GOOGLE_SHEET_NAME = "Today's Patient Enquiries"
CREDS_PATH = '/etc/secrets/google_creds.json'

# ---- GOOGLE SHEETS ----
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_PATH, scope)
client = gspread.authorize(creds)
sheet = client.open(GOOGLE_SHEET_NAME).sheet1

# ---- ENSURE HEADERS ----
headers = ['Date & Time', 'Patient Name', 'Phone', 'Issue', 'Urgency', 'Contact Time', 'Insurance', 'Status']
existing = sheet.get_all_values()
if not existing or existing[0] != headers:
    sheet.insert_row(headers, 1)

# ---- AI EXTRACTION ----
def extract_patient_info(raw_text):
    prompt = f"""Extract the following fields from this patient enquiry. Return ONLY a valid JSON object with these exact keys:
name, issue, urgency, insurance, phone, contact_time.

Rules:
- name: The name of the person who needs dental treatment. Look for "my name is...", "I'm...", "this is...", or any person's name mentioned.
- issue: Brief description of the dental problem.
- urgency: HIGH, MEDIUM, or LOW. Dental pain, broken teeth, swelling, emergencies are HIGH. Routine checkups are LOW.
- insurance: Insurance provider name (e.g., Delta Dental, Aetna, Guardian), or "None" if not mentioned.
- phone: Any phone number mentioned.
- contact_time: When they want to be called (e.g., "after 5pm", "before noon", "tomorrow morning").
- If you cannot find a field, set it to "Not provided".

Enquiry:
{raw_text}

JSON:"""
    api_headers = {'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'}
    data = {
        'model': AI_MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.0,
        'response_format': {'type': 'json_object'}
    }
    resp = requests.post(AI_API_URL, headers=api_headers, json=data, timeout=15)
    resp.raise_for_status()
    return json.loads(resp.json()['choices'][0]['message']['content'])

# ---- TELEGRAM ----
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    r = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()
    if not r.json().get('ok'):
        raise Exception(r.json().get('description', 'Telegram error'))

# ---- WEBHOOK ----
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    raw_message = ''

    if 'message' in data:
        raw_message = data['message']
    elif 'data' in data and 'fields' in data['data']:
        for f in data['data']['fields']:
            if f.get('key') == 'message' or f.get('type') == 'TEXTAREA':
                raw_message = f.get('value', '')
                break
    else:
        raw_message = str(data)

    if not raw_message:
        return jsonify({'error': 'No message content'}), 400

    info = extract_patient_info(raw_message)
    if not info:
        return jsonify({'error': 'Extraction failed'}), 500

    now = datetime.now(timezone.utc).strftime('%b %d, %Y %I:%M %p')

    row = [
        now,
        info.get('name', ''),
        info.get('phone', ''),
        info.get('issue', ''),
        info.get('urgency', ''),
        info.get('contact_time', ''),
        info.get('insurance', ''),
        'New'
    ]
    sheet.append_row(row)

    urgency_emoji = {'HIGH': '🔥', 'MEDIUM': '⚠️', 'LOW': 'ℹ️'}
    emoji = urgency_emoji.get(info.get('urgency', ''), '')
    msg = f"""🚨 *NEW PATIENT REQUEST*

👤 *{info.get('name', 'N/A')}*
🦷 *{info.get('issue', 'N/A')}*
{emoji} *{info.get('urgency', 'N/A')} URGENCY*
📞 *{info.get('phone', 'N/A')}*
🕒 *{info.get('contact_time', 'N/A')}*

──────────────
✓ Added to Patient Tracker"""
    send_telegram(msg)
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
