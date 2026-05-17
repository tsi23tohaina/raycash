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

# --- CONFIGURATION ---
MODEL_PATH = "models/model.tflite"
LABEL_PATH = "models/labels.txt"
UPLOAD_FOLDER = "uploads"

# ⚠️ REMPLACEZ PAR L'IP QUE L'ESP32 AFFICHE DANS LE MONITEUR SÉRIE ARDUINO
ESP32_IP = "http://10.162.138.X" 

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- CHARGEMENT DE L'IA ---
labels = []
try:
    if os.path.exists(LABEL_PATH):
        with open(LABEL_PATH, 'r') as f:
            labels = [line.strip() for line in f.readlines()]
    else:
        labels = ["Aluminium", "Plastique", "Verre", "Papier", "Carton", "Inconnu"]

    interpreter = tf.lite.Interpreter(model_path=MODEL_PATH)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()
    input_shape = input_details[0]['shape']
    print(f"✅ Serveur ReyCash en ligne sur http://10.162.138.163:5000")
except Exception as e:
    print(f"❌ ERREUR INITIALISATION IA : {e}")

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# ROUTE : Signal reçu depuis l'ESP32 (Ultrason <= 10cm)
@app.route('/esp_signal', methods=['POST'])
def esp_signal():
    data = request.json
    action = data.get("action") 
    print(f"📢 Signal matériel reçu de l'ESP32 : {action}")
    
    if action == "START":
        # Envoie l'ordre à Flutter de prendre la photo immédiatement
        socketio.emit('command_from_esp', {'action': 'START'})
        return jsonify({"status": "capture_triggered"}), 200
    return jsonify({"status": "ignored"}), 200

# ROUTE : Réception de l'image, prédiction et commande du Servo
@app.route('/predict', methods=['POST'])
def predict():
    if 'image' not in request.files:
        return jsonify({"error": "Aucune image reçue"}), 400
    
    file = request.files['image']
    try:
        timestamp = int(time.time())
        filename = f"capture_{timestamp}.jpg"
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
        if output_data.dtype == np.uint8:
            output_data = output_data / 255.0
            
        best_index = np.argmax(output_data)
        confidence = float(output_data[best_index])

        if confidence > 0.45 and best_index < len(labels):
            label = labels[best_index]
        else:
            label = "Inconnu"

        print(f"🎯 Résultat : {label} ({confidence:.2%})")

        # Logique de tri automatique
        recyclables = ["Aluminium", "Plastique", "Verre", "Papier", "Carton"]
        decision_tri = "INCONNU"
        
        if label in recyclables:
            decision_tri = "RECYCLABLE"
            try:
                requests.post(f"{ESP32_IP}/servo", data="RECYCLABLE", timeout=2)
            except Exception as e:
                print(f"⚠️ Impossible de joindre l'ESP32 pour pivoter : {e}")
        else:
            try:
                requests.post(f"{ESP32_IP}/servo", data="INCONNU", timeout=2)
            except Exception as e:
                print(f"⚠️ Impossible de joindre l'ESP32 : {e}")

        return jsonify({
            "label": label,
            "confidence": f"{confidence*100:.1f}%",
            "image_url": f"/uploads/{filename}",
            "tri_status": decision_tri
        })

    except Exception as e:
        print(f"🔥 Erreur traitement image : {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Écoute sur l'adresse IP fixe de votre carte réseau Wi-Fi
    socketio.run(app, host='10.162.138.163', port=5000, debug=False, use_reloader=False)