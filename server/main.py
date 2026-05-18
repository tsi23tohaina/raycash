import os
import numpy as np
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO
import tensorflow as tf
from PIL import Image
import io
import time
import requests

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*") 

MODEL_PATH = "models/model.tflite"
LABEL_PATH = "models/labels.txt"
UPLOAD_FOLDER = "uploads"

# IP exacte de ton ESP32 d'après ta capture Hotspot
ESP32_IP = "http://192.168.137.174" 

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- CHARGEMENT DE L'IA ---
labels = ["Aluminium", "Plastique", "Verre", "Papier", "Carton", "Inconnu"]
try:
    interpreter = tf.lite.Interpreter(model_path=MODEL_PATH)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()
    input_shape = input_details[0]['shape']
    print("✅ Serveur Central ReyCash en ligne sur http://192.168.137.1:5000")
except Exception as e:
    print(f"❌ Erreur IA : {e}")

# ROUTE ROUTINE : Écoute les actions du matériel
@app.route('/esp_signal', methods=['POST'])
def esp_signal():
    data = request.json
    action = data.get("action") 
    print(f"📢 Signal reçu de l'ESP32 : {action}")
    
    if action == "BUTTON_CLICK":
        # 1. Le PC ordonne à l'ESP32 de faire retentir le buzzer immédiatement !
        try:
            requests.post(f"{ESP32_IP}/action", data="BEEP_START", timeout=1)
        except Exception as e:
            print(f"Erreur envoi BEEP à l'ESP32 : {e}")

        # 2. Le PC ordonne à l'APK Flutter de lancer la capture photo
        socketio.emit('command_from_esp', {'action': 'START'})
        return jsonify({"status": "buzzer_and_camera_triggered"}), 200

    return jsonify({"status": "ignored"}), 200

# ROUTE PREDICT : Reçoit l'image de Flutter, classifie et pilote le matériel
@app.route('/predict', methods=['POST'])
def predict():
    if 'image' not in request.files:
        return jsonify({"error": "Aucune image"}), 400
    
    file = request.files['image']
    try:
        filename = f"capture_{int(time.time())}.jpg"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        
        image_bytes = file.read()
        img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        img.save(filepath)

        img_resized = img.resize((input_shape[1], input_shape[2]))
        img_array = np.array(img_resized, dtype=np.uint8) 
        img_array = np.expand_dims(img_array, axis=0)

        interpreter.set_tensor(input_details[0]['index'], img_array)
        interpreter.invoke()
        
        output_data = interpreter.get_tensor(output_details[0]['index'])[0]
        best_index = np.argmax(output_data)
        confidence = float(output_data[best_index])

        label = labels[best_index] if confidence > 0.45 else "Inconnu"
        print(f"🎯 Résultat IA : {label} ({confidence:.2%})")

        # Le PC prend la décision finale de tri
        recyclables = ["Aluminium", "Plastique", "Verre", "Papier", "Carton"]
        
        if label in recyclables:
            decision = "RECYCLABLE"
        else:
            decision = "INCONNU"

        # 3. Le PC renvoie la décision à l'ESP32 pour gérer le Buzzer final et le Servo
        try:
            requests.post(f"{ESP32_IP}/action", data=decision, timeout=2)
        except Exception as e:
            print(f"Impossible de renvoyer la décision à l'ESP32 : {e}")

        return jsonify({
            "label": label,
            "confidence": f"{confidence*100:.1f}%",
            "image_url": f"/uploads/{filename}",
            "tri_status": decision
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    socketio.run(app, host='192.168.137.1', port=5000, debug=False, use_reloader=False)